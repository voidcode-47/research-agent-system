"""论文工作台输入守卫：越狱 / 跑题 / 非论文请求的固定拒答。

只判断"用户指令"，不判断论文正文——论文里出现 Python、股票等词汇是正常的，
因此只有缺少论文信号时，编程/生活类特征才视为跑题。
"""
import re

from config.paper import REJECT_JAILBREAK, REJECT_NO_PAPER, REJECT_OFF_TOPIC

# 越狱 / 提示词窃取 / 角色扮演特征
_JAILBREAK_PATTERNS = [
    re.compile(r"忽略(?:以上|之前|上述).{0,8}(?:指令|提示|规则)"),
    re.compile(r"忽略(?:你的|所有|全部)?.{0,6}(?:设定|限制)"),
    re.compile(r"系统提示词|系统提示|输出你的设定|你的提示词"),
    re.compile(r"你现在是|从现在开始你是|扮演.{0,8}(?:角色|助手|ai|AI)"),
    re.compile(r"越狱|\bDAN\b|developer\s*mode", re.IGNORECASE),
    re.compile(r"忘记(?:设定|指令|提示|身份)"),
    re.compile(r"重复以上内容|复述以上"),
    re.compile(r"</system>|<\|im_start\|>"),
]

# 明确拒绝的跑题请求（即使带有论文词也拒绝：全文翻译、创作、生活服务）
_HARD_OFF_TOPIC = [
    re.compile(
        r"(?:整篇|全文|整本|以下全部).{0,6}(?:翻译|译成|译为)"
        r"|(?:翻译|译成|译为).{0,6}(?:整篇|全文|整本|以下全部)"
    ),
    re.compile(r"(写|代写|生成|创作).{0,8}(小说|作文|文案|邮件|简历|诗歌|故事|演讲稿|剧本)"),
    re.compile(r"(股票|彩票|天气|菜谱|食谱|星座|算命|运势)"),
    re.compile(r"(写|编|做|生成|开发|实现|教我).{0,12}(代码|程序|爬虫|脚本|网页|网站|小程序|外挂)"),
]

# 编程语言名：仅在缺少论文信号时判定跑题
_LANGUAGE_RE = re.compile(
    r"\b(python|java|javascript|typescript|sql|html|css|c\+\+|c#|php|golang|rust)\b",
    re.IGNORECASE,
)
_CODE_HINT_RE = re.compile(r"(代码|编程|程序|爬虫|脚本|算法实现)")

# 纯寒暄短句
_GREETING_RE = re.compile(
    r"^(你好|您好|hi|hello|嗨|在吗|谢谢|多谢|再见|拜拜|ok|好的|嗯|收到)[!！。.\s]*$",
    re.IGNORECASE,
)

# 论文相关信号词
_PAPER_SIGNALS = (
    "论文", "摘要", "关键词", "正文", "段落", "章节", "引言", "绪论", "结论",
    "参考文献", "致谢", "附录", "图表", "题注", "格式", "排版", "字号", "行距",
    "查重", "降重", "去ai", "ai味", "润色", "改写", "修改", "优化", "学术",
    "期刊", "投稿", "开题", "综述", "实验", "方法", "数据", "引用", "文献",
    "层次", "逻辑", "可执行", "字数", "盲审", "答辩", "毕业", "开题报告",
    "abstract", "paper", "thesis", "manuscript",
)


def is_jailbreak(text: str) -> bool:
    """是否包含越狱 / 提示词窃取 / 角色扮演指令。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(p.search(t) for p in _JAILBREAK_PATTERNS)


def has_paper_signal(text: str) -> bool:
    """用户指令里是否出现论文相关信号词。"""
    t = (text or "").lower()
    return any(k in t for k in _PAPER_SIGNALS)


def is_off_topic(text: str) -> bool:
    """是否为非论文请求（写代码、整篇翻译、创作、生活服务、纯寒暄）。"""
    t = (text or "").strip()
    if not t:
        return True
    if _GREETING_RE.match(t):
        return True
    if any(p.search(t) for p in _HARD_OFF_TOPIC):
        return True
    # 单独出现编程语言名时，若没有论文信号，视为写代码类请求；
    # 带"代码/算法实现"等词时同样处理（如"帮我用 java 实现这个算法"）
    if not has_paper_signal(t):
        if _LANGUAGE_RE.search(t):
            return True
        if _CODE_HINT_RE.search(t):
            return True
    return False


def gate(text: str, has_paper: bool) -> dict:
    """统一入口：返回 {allowed, reason, reply}。

    has_paper 由调用方给出：/api/paper/{id}/ask 绑定 paper_id 时恒为 True；
    /api/chat 在 PAPER_ONLY_MODE 下按 paper_id 或正文信号判断。
    """
    t = (text or "").strip()
    if not t:
        return {"allowed": False, "reason": "empty", "reply": REJECT_NO_PAPER}
    if is_jailbreak(t):
        return {"allowed": False, "reason": "jailbreak", "reply": REJECT_JAILBREAK}
    if is_off_topic(t):
        return {"allowed": False, "reason": "off_topic", "reply": REJECT_OFF_TOPIC}
    if not has_paper:
        return {"allowed": False, "reason": "no_paper", "reply": REJECT_NO_PAPER}
    return {"allowed": True, "reason": "ok", "reply": ""}
