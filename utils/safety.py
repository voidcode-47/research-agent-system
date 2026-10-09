"""安全防护：防死循环、Token 预算控制、幻觉检测、破坏性命令拦截。"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# 破坏性命令/操作特征（运行时硬拦截，与 config.prompts.SAFETY_CONSTRAINTS 提示配合）。
# 仅对声明了 dangerous=True 的工具生效（见 BaseTool.dangerous 与
# ReActAgent._execute_tool），避免误伤搜索/抓取类工具的合法查询。
DESTRUCTIVE_PATTERNS: list = [
    re.compile(r"\brm\b", re.IGNORECASE),                          # rm
    re.compile(r"\brmdir\b", re.IGNORECASE),                       # rmdir
    # Windows: rd /s /q、rd /q /s（选项顺序任意）
    re.compile(r"\brd\b(?=[^\n]*\s/s\b)", re.IGNORECASE),
    re.compile(r"\b(del|erase)\b(?=[\s/])", re.IGNORECASE),        # del ...、del/f
    re.compile(r"git\s+push\b.*(?:-f\b|--force\b)", re.IGNORECASE),  # git push -f/--force
    re.compile(r"\bmkfs\.\w+", re.IGNORECASE),                     # mkfs.ext4 ...
    re.compile(r"\bformat\s+[A-Za-z]:", re.IGNORECASE),            # Windows: format C:
    re.compile(r"格式化"),                                          # 中文"格式化"
    re.compile(r"\bdd\b\s+if=", re.IGNORECASE),                    # dd if=...
    re.compile(r"\bshred\b", re.IGNORECASE),                       # shred
    re.compile(r"shutil\.rmtree", re.IGNORECASE),                  # Python shutil.rmtree
    re.compile(r"os\.r?emove", re.IGNORECASE),                     # os.remove
    re.compile(r"os\.unlink", re.IGNORECASE),                      # os.unlink
]

_CJK_RUN_RE = re.compile(r"[一-鿿]{2,}")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _extract_keywords(text: str) -> list[str]:
    """提取用于覆盖度比对的关键词。

    中文没有空格分隔，按空白切词会把整句当成一个"词"，导致任何中文文本
    都被判为无依据。这里对中文用 2-gram，对拉丁文按词切分。
    """
    keywords = [w.lower() for w in _LATIN_WORD_RE.findall(text) if len(w) > 3]
    for run in _CJK_RUN_RE.findall(text):
        keywords.extend(run[i:i + 2] for i in range(len(run) - 1))
    return keywords


def _collect_strings(obj) -> list:
    """递归收集参数中的所有字符串值（处理嵌套 dict/list/tuple/set）。"""
    parts: list = []
    if isinstance(obj, str):
        parts.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            parts.extend(_collect_strings(v))
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            parts.extend(_collect_strings(v))
    else:
        parts.append(str(obj))
    return parts


@dataclass
class SafetyGuard:
    """集中处理防死循环、Token 预算、幻觉校验。"""

    max_iterations: int = 10
    max_token_budget: int = 8000
    _action_history: list[str] = field(default_factory=list)
    _token_used: int = 0
    _iteration: int = 0

    def reset(self) -> None:
        """每轮对话重置。"""
        self._action_history.clear()
        self._token_used = 0
        self._iteration = 0

    def tick_iteration(self) -> int:
        self._iteration += 1
        return self._iteration

    @property
    def iteration(self) -> int:
        return self._iteration

    @property
    def over_iterations(self) -> bool:
        return self._iteration >= self.max_iterations

    @property
    def is_critical(self) -> bool:
        """是否进入危险区间（用于强制收敛）。

        默认 10 轮时为第 7 轮起；max_iterations 较小时按比例放宽，
        避免"第 1 轮就要求直接作答"导致完全不调用工具。
        """
        return self._iteration >= max(self.max_iterations // 2, self.max_iterations - 3)

    def add_tokens(self, tokens: int) -> None:
        self._token_used += tokens

    def check_budget(self, token_count: int) -> bool:
        """检查上下文 token 预算是否超限（纯查询，不污染累计用量统计）。

        Args:
            token_count: 当前上下文 token 总量。必须传"原始记忆"的计数——
            get_messages() 已按窗口截断，用它计数永远达不到预算阈值。
        """
        return token_count >= self.max_token_budget

    def _action_signature(self, name: str, arguments: dict) -> str:
        """生成 Action 签名（name + 排序后参数哈希）。"""
        sorted_args = str(sorted((arguments or {}).items()))
        raw = f"{name}:{sorted_args}"
        return hashlib.md5(raw.encode()).hexdigest()

    def is_looping(self, name: str, arguments: dict) -> bool:
        """检测死循环：最近 3 步内同一 Action 重复 3 次。

        阈值取 3（而非 2）：模型在工具返回空结果后原样重试一次属于正常行为，
        直接终止会让整轮研究前功尽弃。
        """
        sig = self._action_signature(name, arguments or {})
        self._action_history.append(sig)
        if len(self._action_history) > 3:
            self._action_history.pop(0)
        repeat = self._action_history.count(sig)
        if repeat >= 3:
            logger.warning(f"检测到循环行为: {name}({arguments}) 重复 {repeat} 次")
            return True
        return False

    def check_destructive(self, tool_name: str, arguments: dict) -> Optional[str]:
        """检查工具调用参数是否含破坏性命令/操作。

        与 config.prompts.SAFETY_CONSTRAINTS 配合：提示词约束模型行为，
        本方法在运行时做硬拦截兜底。仅对 dangerous=True 的工具生效
        （见 ReActAgent._execute_tool 的调用处）。

        Args:
            tool_name: 工具名（仅用于日志）。
            arguments: 工具调用参数。

        Returns:
            命中时返回说明字符串；未命中返回 None。
        """
        text = " ".join(_collect_strings(arguments or {}))
        for pat in DESTRUCTIVE_PATTERNS:
            m = pat.search(text)
            if m:
                return f"检测到破坏性操作 '{m.group(0)}'（规则: {pat.pattern}）"
        return None

    def detect_hallucination(
        self,
        claim: str,
        sources: list[str],
    ) -> float:
        """简单幻觉检测：检查 claim 关键词是否在 sources 中出现。

        返回置信度 0-1，1=完全有依据，0=纯编造。
        深度校验需要注入 LLM，此方法做快速粗筛。
        """
        if not sources:
            return 0.3
        combined = " ".join(sources).lower()
        keywords = _extract_keywords(claim)
        if not keywords:
            return 0.5
        hits = sum(1 for kw in keywords if kw in combined)
        return hits / len(keywords)
