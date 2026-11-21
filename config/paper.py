"""论文诊断与优化工作台的枚举、文案与 LLM 任务白名单。

所有面向用户的固定文案集中在这里，避免散落在各模块里。
"""

# ---------------------------------------------------------------------------
# 论文层次（8 档）
# min_chars 为该层次通常的正文最低字数（中文字符），仅用于诊断参照
# ---------------------------------------------------------------------------
PAPER_LEVELS: dict[str, dict] = {
    "course": {
        "label": "课程论文",
        "min_chars": 3000,
        "structure": "引言 / 正文 / 结论 三部分即可，章节可较少",
        "innovation": "不要求创新，重在概念梳理与规范表达",
        "depth": "以文献综述和概念解释为主",
    },
    "undergraduate": {
        "label": "本科毕业论文",
        "min_chars": 8000,
        "structure": "绪论 / 相关工作 / 方法 / 实验或分析 / 结论",
        "innovation": "允许复现和改进，强调完整性与规范性",
        "depth": "方法描述清楚、实验可复现即可",
    },
    "master": {
        "label": "硕士学位论文",
        "min_chars": 30000,
        "structure": "绪论 / 综述 / 方法 / 实验 / 结论，需有独立章节体系",
        "innovation": "需要明确的研究问题与一定的方法改进",
        "depth": "需要理论分析、对比实验与消融讨论",
    },
    "doctor": {
        "label": "博士学位论文",
        "min_chars": 60000,
        "structure": "完整的研究体系，多个创新点成章",
        "innovation": "要求原创性贡献，创新点之间形成体系",
        "depth": "理论、方法、实验、应用均需深入论证",
    },
    "journal_general": {
        "label": "普通期刊论文",
        "min_chars": 5000,
        "structure": "摘要 / 引言 / 方法 / 结果 / 讨论 / 结论",
        "innovation": "应用型或改进型工作即可",
        "depth": "结果可信、讨论充分",
    },
    "journal_core": {
        "label": "核心期刊论文（北大核心 / CSSCI / CSCD）",
        "min_chars": 8000,
        "structure": "摘要 / 关键词 / 引言 / 相关工作 / 方法 / 实验 / 讨论 / 结论",
        "innovation": "需要明确创新点与充分对比",
        "depth": "实验设计严谨，需与主流方法对比",
    },
    "sci_ei": {
        "label": "SCI / EI 期刊论文",
        "min_chars": 6000,
        "structure": "Abstract / Introduction / Related Work / Method / Experiments / Conclusion",
        "innovation": "国际同行可识别的原创性或显著改进",
        "depth": "统计显著性、误差分析、可复现性缺一不可",
    },
    "conference": {
        "label": "会议论文",
        "min_chars": 4000,
        "structure": "摘要 / 引言 / 方法 / 实验 / 结论，篇幅紧凑",
        "innovation": "单点创新即可，强调时效与实验对比",
        "depth": "核心实验完整，次要分析可精简",
    },
}

# ---------------------------------------------------------------------------
# 四维诊断
# ---------------------------------------------------------------------------
DIAGNOSIS_DIMENSIONS: dict[str, dict] = {
    "level": {
        "label": "论文层次",
        "weight": 0.25,
        "description": "对照目标层次的字数、结构、创新性与深度要求打分",
    },
    "feasibility": {
        "label": "内容可实现性",
        "weight": 0.20,
        "description": "评估问题清晰度、方法可执行性、数据可得性、工作量与创新点",
    },
    "format": {
        "label": "正文格式",
        "weight": 0.20,
        "description": "章节、编号、图表题注、参考文献与排版规范检查",
    },
    "deai": {
        "label": "AI 率",
        "weight": 0.35,
        "description": "识别套话、机械连接、句式单一等模板化特征，并估算 AI 生成率",
    },
}

# ---------------------------------------------------------------------------
# 格式选项
# ---------------------------------------------------------------------------
FORMAT_TEMPLATES: dict[str, dict] = {
    "generic_thesis": {"label": "通用学位论文", "description": "宋体小四、1.5 倍行距、十进制章节编号"},
    "journal_cn": {"label": "中文期刊", "description": "中文期刊常见版式，GB/T 7714 参考文献"},
    "ieee": {"label": "IEEE", "description": "英文会议/期刊版式，IEEE 参考文献"},
    "apa": {"label": "APA", "description": "英文社科版式，APA 参考文献"},
}

REFERENCE_STYLES: dict[str, str] = {
    "gbt7714": "GB/T 7714",
    "apa": "APA",
    "ieee": "IEEE",
}

DEAI_STRENGTHS: dict[str, dict] = {
    "light": {"label": "轻度", "description": "只改明显套话，尽量保留原句"},
    "medium": {"label": "中度", "description": "打散模板句式，保留术语与数据"},
    "strong": {"label": "重度", "description": "大幅重构表达，但不增删事实"},
}

REWRITE_MODES: dict[str, str] = {
    "deai": "去 AI 味",
    "polish": "学术润色",
    "tighten": "精简压缩",
}

# 必须保留项（需求确认时的多选）
DEAI_KEEP_OPTIONS = ["数据", "术语", "引用"]

DEFAULT_FORMAT: dict = {
    "template": "generic_thesis",
    "font_body": "宋体",
    "font_size_body_pt": 12,
    "line_spacing": 1.5,
    "heading_numbering": "decimal",
    "reference_style": "gbt7714",
    "figure_caption_below": True,
    "table_caption_above": True,
}

DEFAULT_REQUIREMENT: dict = {
    "level": "",
    "format": DEFAULT_FORMAT,
    "deai": {"strength": "medium", "keep": ["数据", "术语", "引用"], "banned_phrases": []},
    "notes": "",
    "confirmed": False,
    "updated_at": "",
}

# ---------------------------------------------------------------------------
# LLM 任务白名单（网关只允许这些 task_type）
# ---------------------------------------------------------------------------
LLM_TASKS: dict[str, dict] = {
    "research_report": {"label": "文献调研报告", "max_tokens": 3500},
    "research_followup": {"label": "调研报告追问", "max_tokens": 1024},
    "intake_extract": {"label": "上传信息提取", "max_tokens": 800},
    "level_diagnose": {"label": "论文层次诊断", "max_tokens": 2500},
    "feasibility": {"label": "内容可实现性评估", "max_tokens": 2500},
    "deai_detect": {"label": "AI 味检测与建议", "max_tokens": 1600},
    "deai_rewrite": {"label": "段落改写", "max_tokens": 4096},
    "rewrite_guide": {"label": "全局改写方针", "max_tokens": 4096},
    "paper_qa": {"label": "论文问答", "max_tokens": 1500},
}

# ---------------------------------------------------------------------------
# 固定拒答模板（不进入主模型）
# ---------------------------------------------------------------------------
REJECT_JAILBREAK = (
    "抱歉，我无法响应跳过规则、输出系统提示词或角色扮演类请求。"
    "本平台只处理论文诊断、格式检查与段落改写。"
)

REJECT_OFF_TOPIC = (
    "本平台只提供论文诊断与优化服务（论文层次、格式、去 AI 味、段落改写），"
    "不支持写代码、整篇翻译、代写、文案创作等请求。"
)

REJECT_NO_PAPER = (
    "请先上传论文 PDF，并围绕该论文提问。"
    "本平台不提供通用聊天，只针对当前论文给出诊断与改写建议。"
)
