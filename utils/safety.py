"""安全防护：防死循环、Token 预算控制、幻觉检测、破坏性命令拦截。"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger
from utils.token_counter import count_messages_tokens

logger = get_logger(__name__)


# 破坏性命令/操作特征（运行时硬拦截，与 config.prompts.SAFETY_CONSTRAINTS 提示配合）。
# 仅对声明了 dangerous=True 的工具生效（见 BaseTool.dangerous 与
# ReActAgent._execute_tool），避免误伤搜索/抓取类工具的合法查询。
DESTRUCTIVE_PATTERNS: list = [
    re.compile(r"\brm\b", re.IGNORECASE),                          # rm
    re.compile(r"\brmdir\b", re.IGNORECASE),                       # rmdir
    re.compile(r"\brd\s+/s\b", re.IGNORECASE),                     # Windows: rd /s
    re.compile(r"\bdel\b\s+", re.IGNORECASE),                      # Windows: del ...
    re.compile(r"\berase\b\s+", re.IGNORECASE),                    # erase ...
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
        """是否进入危险区间（第 7 轮起强制收敛）。"""
        return self._iteration >= self.max_iterations - 3

    def add_tokens(self, tokens: int) -> None:
        self._token_used += tokens

    def check_budget(self, messages: list[dict]) -> bool:
        """检查上下文 token 预算是否超限（纯查询，不污染累计用量统计）。"""
        return count_messages_tokens(messages) >= self.max_token_budget

    def _action_signature(self, name: str, arguments: dict) -> str:
        """生成 Action 签名（name + 排序后参数哈希）。"""
        sorted_args = str(sorted((arguments or {}).items()))
        raw = f"{name}:{sorted_args}"
        return hashlib.md5(raw.encode()).hexdigest()

    def is_looping(self, name: str, arguments: dict) -> bool:
        """检测死循环：最近 3 步相同 Action 重复≥2 次。"""
        sig = self._action_signature(name, arguments or {})
        self._action_history.append(sig)
        if len(self._action_history) > 3:
            self._action_history.pop(0)
        repeat = self._action_history.count(sig)
        if repeat >= 2:
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
        keywords = [w for w in claim.split() if len(w) > 3]
        if not keywords:
            return 0.5
        hits = sum(1 for kw in keywords if kw.lower() in combined)
        return hits / len(keywords)
