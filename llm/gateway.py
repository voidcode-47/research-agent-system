"""统一 LLM 网关：任务白名单 → 配额 → 封顶 max_tokens → JSON 校验 → 重试一次。

审计日志只记录 task_type、paper_id、tokens、成功/失败，绝不记录论文正文。
"""
import json
import re
import time

from config.paper import LLM_TASKS
from utils import auth, quota
from utils.logger import get_logger

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)

# OpenAICompatibleLLM 在 Key 无效 / 限流 / 本地未加载模型时把错误文本当内容返回
_PROVIDER_ERROR_PREFIXES = ("API 调用失败", "⚠️", "本地服务")


class _ModelCallFailed(RuntimeError):
    """模型调用本身失败（非 JSON 格式问题），不重试。"""


def _provider_error(content: str) -> bool:
    return (content or "").strip().startswith(_PROVIDER_ERROR_PREFIXES)


def _unbalanced(text: str) -> bool:
    """检测 JSON 是否被截断（括号/字符串未闭合）。"""
    depth = 0
    in_str = False
    escaped = False
    for ch in text or "":
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return in_str or depth > 0


def extract_json(text: str):
    """从模型输出中提取第一个合法 JSON 对象。

    容忍 ```json 围栏、前后废话；用括号配对（跳过字符串内括号）截取对象。
    """
    if not text:
        return None
    s = text.strip()
    m = _FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()

    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None


def _is_timeout_error(err: Exception) -> bool:
    """判断是否为请求超时（含 OpenAI/httpx 的超时异常）。"""
    if isinstance(err, TimeoutError):
        return True
    name = type(err).__name__.lower()
    return "timeout" in name or "timed out" in str(err).lower()


def _merge_continuation(text: str, extra: str) -> str:
    """合并截断续写，尽量去掉模型重复输出的最后一句。"""
    extra = (extra or "").lstrip()
    if not extra:
        return text
    if not text:
        return extra
    window = min(200, len(text), len(extra))
    for size in range(window, 2, -1):
        if text.endswith(extra[:size]):
            return text + extra[size:]
    separator = "" if text.endswith("\n") else "\n\n"
    return text + separator + extra


class LLMGateway:
    """所有论文相关 LLM 调用的唯一入口。"""

    def __init__(self, llm, quota_key: str = "local"):
        self.llm = llm
        self.quota_key = quota_key

    def run(
        self,
        task_type: str,
        messages: list[dict],
        paper_id: str = "",
        max_tokens: int | None = None,
        temperature: float = 0.3,
        expect_json: bool = True,
        validator=None,
        timeout: float | None = None,
        on_progress=None,
        max_continuations: int = 2,
        llm_options: dict | None = None,
    ):
        task = LLM_TASKS.get(task_type)
        if not task:
            raise ValueError(f"不允许的 LLM 任务类型：{task_type}")
        limit = min(int(max_tokens or task["max_tokens"]), int(task["max_tokens"]))

        # consume 在锁内检查并扣减，超限时不发起模型调用；管理员不占用配额
        unlimited = auth.is_admin()
        charge = quota.charge(self.quota_key, unlimited=unlimited)
        charged = not unlimited
        # 一次逻辑调用只扣一次；真正的模型输出（即便 JSON 不合格）计入额度，
        # 提供商报错、限流或连接失败等没有可用输出的情况在最终失败时退还。
        completion_received = False

        msgs = list(messages)
        last_err: Exception | None = None
        for attempt in range(2):
            started = time.monotonic()
            resp = None
            collected = ""
            continuations = 0
            try:
                while True:
                    options = {"timeout": timeout} if timeout is not None else {}
                    if llm_options:
                        options.update(llm_options)
                    resp = self.llm.chat(msgs, temperature=temperature,
                                         max_tokens=limit, **options)
                    content = getattr(resp, "content", "") or ""
                    if _provider_error(content):
                        msg = content.strip()[:200]
                        if re.search(r"401|403|authentication|api[\s_-]*key", msg, re.I):
                            msg += "。请在侧边栏「添加模型」并填写有效 API Key 后重试。"
                        raise _ModelCallFailed(msg)
                    completion_received = completion_received or bool(content.strip())
                    reason = str(getattr(resp, "finish_reason", "") or "")
                    truncated = bool(reason) and reason not in (
                        "stop", "end_turn", "stop_sequence", "STOP"
                    )
                    if truncated and not expect_json and content.strip():
                        collected = (_merge_continuation(collected, content)
                                     if collected else content)
                        if continuations < max_continuations:
                            continuations += 1
                            self._audit(task_type, paper_id, resp, started, False,
                                        "输出被截断，自动续写")
                            if on_progress is not None:
                                try:
                                    on_progress(continuations)
                                except Exception:
                                    logger.debug("续写进度回调失败", exc_info=True)
                            msgs = list(messages) + [
                                {"role": "assistant", "content": collected[-1200:]},
                                {
                                    "role": "user",
                                    "content": (
                                        "上一条输出因长度限制中断。请紧接中断处继续输出"
                                        "剩余内容；不要重复已写部分，不要重新开头，"
                                        "也不要输出任何解释。"
                                    ),
                                },
                            ]
                            continue
                        # 续写次数用尽：保留已生成内容，避免用户长时间等待后前功尽弃
                        if validator is not None and not validator(collected):
                            raise ValueError("模型输出未完整结束（达到续写上限）")
                        self._audit(task_type, paper_id, resp, started, True)
                        return collected
                    if truncated:
                        raise ValueError("模型输出未完整结束（" + reason[:80] + "）")

                    final_content = (_merge_continuation(collected, content)
                                     if collected else content)
                    data = extract_json(final_content) if expect_json else final_content
                    if expect_json and data is None:
                        raise ValueError("模型输出不是合法 JSON")
                    if validator is not None and not validator(data):
                        if _unbalanced(final_content):
                            raise ValueError("模型输出不完整（可能超出长度上限被截断）")
                        raise ValueError("模型输出未通过结构校验")
                    self._audit(task_type, paper_id, resp, started, True)
                    return data
            except _ModelCallFailed:
                if charged and not completion_received:
                    self._refund(charge)
                raise
            except Exception as e:
                last_err = e
                self._audit(task_type, paper_id, resp, started, False, str(e))
                # 超时重试只会让用户再等一轮且大概率仍超时，直接失败更快
                if attempt == 0 and not _is_timeout_error(e):
                    msgs = list(messages) + [{
                        "role": "user",
                        "content": (
                            "你上一次的输出不符合要求，原因：" + str(e)[:120]
                            + ("。请严格只输出一个合法 JSON 对象，字段按要求返回。"
                               if expect_json else "。请重新输出完整正文。")
                        ),
                    }]
        if charged and not completion_received:
            self._refund(charge)
        raise RuntimeError(f"模型任务 {task_type} 执行失败：{last_err}")

    def stream(self, task_type: str, messages: list[dict], *,
               temperature: float = 0.3, max_tokens: int | None = None,
               timeout: float | None = None, llm_options: dict | None = None):
        """流式逻辑调用只扣一次；无输出失败退款，已有输出的失败保留计费。"""
        task = LLM_TASKS.get(task_type)
        if not task:
            raise ValueError(f"不允许的 LLM 任务类型：{task_type}")
        limit = min(max_tokens or task["max_tokens"], task["max_tokens"])
        receipt = quota.charge(self.quota_key, unlimited=auth.is_admin())
        received = False
        length = 0
        stream = None
        try:
            options = {"timeout": timeout} if timeout is not None else {}
            if llm_options:
                options.update(llm_options)
            stream = self.llm.stream_chat(messages, temperature=temperature, max_tokens=limit, **options)
            for chunk in stream:
                if not chunk:
                    continue
                received = True
                length += len(chunk)
                if length > limit * 16:
                    raise RuntimeError("模型流式输出超过长度上限")
                yield chunk
            if not received:
                raise RuntimeError("模型未返回任何内容")
        finally:
            try:
                if stream is not None and hasattr(stream, "close"):
                    stream.close()
            finally:
                if receipt is not None and not received:
                    self._refund(receipt)

    def _refund(self, charge: quota.Charge | None) -> None:
        try:
            quota.refund(self.quota_key, charge=charge)
        except Exception as e:
            logger.warning(f"配额退还失败: {e}")

    def _audit(self, task_type: str, paper_id: str, resp, started: float,
               ok: bool, error: str = "") -> None:
        """审计：只记录任务、论文 ID、token 与结果，不记录正文。"""
        usage = getattr(resp, "usage", None) or {}
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        logger.info(
            "[paper-llm] task=%s paper=%s tokens=%s ok=%s elapsed=%.2fs%s",
            task_type, paper_id or "-", tokens if tokens is not None else "-",
            ok, time.monotonic() - started,
            f" error={error[:120]}" if error else "",
        )
