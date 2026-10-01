"""总结员 Agent：负责生成最终研究报告。

无工具纯生成，综合前两个 Agent 的成果。
"""
from llm.base import BaseLLM
from config.prompts import SUMMARIZER_SYSTEM_PROMPT, SUMMARIZER_SYSTEM_PROMPT_DETAILED
from utils.logger import get_logger
from utils.report_refs import normalize_report_references

logger = get_logger(__name__)


class SummarizerAgent:
    """总结员 Agent，生成最终报告。"""

    def __init__(self, llm: BaseLLM, detailed: bool = False):
        self.llm = llm
        self.detailed = detailed

    def node_fn(self):
        """LangGraph 节点函数。"""
        def _node(state: dict) -> dict:
            query = state.get("query", "")
            research_data = state.get("research_data", [])
            analysis = state.get("analysis", "")
            conflicts = state.get("conflicts", [])
            sources = state.get("sources", [])

            logger.info("[总结员] 开始生成报告")

            # 拼接资料
            research_text = "\n\n".join(
                d for d in research_data if not d.startswith("研究结论:")
            )
            conclusion_text = "\n".join(
                d for d in research_data if d.startswith("研究结论:")
            )

            # 引用池：研究员调用过的工具与参数（含 URL/DOI/query），供报告标注 [n]
            ref_lines = []
            for s in sources[:40]:
                tool = s.get("tool", "")
                args = s.get("args", {})
                if not tool:
                    continue
                if isinstance(args, dict):
                    arg_str = " | ".join(f"{k}={v}" for k, v in args.items() if v)
                else:
                    arg_str = str(args)
                ref_lines.append(f"- [{tool}] {arg_str}")
            references_text = "\n".join(ref_lines) if ref_lines else "（无工具调用记录）"

            prompt = f"""请综合以下信息，生成结构化的研究报告。

研究主题: {query}

研究资料:
{research_text}

分析结论:
{analysis}

发现的矛盾:
{conflicts if conflicts else '无'}

研究员使用的检索工具记录（引用标注参考，正文中用 [1][2] 编号对应）:
{references_text}

请按以下格式输出 Markdown 报告:
## 摘要
（一句话总结）

## 详细分析
（分点论述；每个关键论断后标注来源编号 [1][2]）

## 关键发现
（最重要的 3-5 点，同样标注来源编号）

## 参考文献
（按正文引用顺序列出编号对应的来源，格式：编号. 标题/查询 - 链接或 DOI）

要求：
- 只标注确实出现在研究资料中的来源，不编造引用
- 学术文献类来源尽量给出 DOI 或链接"""

            try:
                system_prompt = (
                    SUMMARIZER_SYSTEM_PROMPT_DETAILED
                    if self.detailed
                    else SUMMARIZER_SYSTEM_PROMPT
                )
                response = self.llm.chat(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.5,
                    max_tokens=8192,  # 本地模型默认输出短，报告必须给足生成空间
                )

                summary = response.content

                # 引用规范化：无论模型编号多乱，保证正文 [n] 与参考文献一一对应
                if summary:
                    normalized = normalize_report_references(summary)
                    if normalized != summary:
                        logger.info("[总结员] 报告引用编号已规范化（正文与参考文献对齐）")
                    summary = normalized
            except Exception as e:
                logger.error(f"报告生成失败: {e}")
                summary = f"报告生成失败: {e}\n\n分析结论:\n{analysis}"

            logger.info("[总结员] 报告生成完成")
            return {
                "summary": summary,
                "status": "summary_done",
                "current_agent": "quality_check",
            }

        return _node
