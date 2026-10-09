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
            ref_list = state.get("ref_list", "")
            analysis = state.get("analysis", "")
            conflicts = state.get("conflicts", [])
            sources = state.get("sources", [])

            logger.info("[总结员] 开始生成报告")

            # 上游研究失败时不要写"报告"：空资料生成的报告没有来源，
            # 会被质检的长度阈值放行并当成有效结果返回。
            if state.get("research_failed") or not research_data:
                error = state.get("error") or "研究阶段未收集到任何资料"
                logger.error(f"[总结员] 跳过报告生成: {error}")
                return {
                    "summary": "",
                    "report_failed": True,
                    "error": error,
                    "status": "summary_failed",
                    "current_agent": "quality_check",
                }

            # 拼接资料
            research_text = "\n\n".join(
                d for d in research_data if not d.startswith("研究结论:")
            )
            conclusion_text = "\n".join(
                d for d in research_data if d.startswith("研究结论:")
            )

            # 引用池：优先用研究员整理好的统一编号文献清单（真实检索条目，
            # 编号唯一且已去重）；解析失败时降级为工具调用记录。
            if ref_list:
                references_text = ref_list
            else:
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

可引用文献清单（唯一引用池：正文引用只能标注清单里的编号，参考文献只能列清单条目）:
{references_text}

请按以下格式输出 Markdown 报告:
## 摘要
（一句话总结）

## 详细分析
（分点论述；每个关键论断后标注来源编号 [1][2]，编号必须来自上方文献清单）

## 关键发现
（最重要的 3-5 点，同样标注来源编号）

## 参考文献
（按正文引用顺序列出编号对应的清单条目，格式：编号. 标题 - 来源链接或 DOI）

要求：
- 正文引用的 [n] 必须能在上方文献清单中找到对应条目；清单里没有的来源一律不得引用
- 参考文献的每条必须与正文某个 [n] 对应，严禁凭空添加
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
                return {
                    "summary": "",
                    "report_failed": True,
                    "error": f"报告生成失败: {e}",
                    "status": "summary_failed",
                    "current_agent": "quality_check",
                }

            logger.info("[总结员] 报告生成完成")
            return {
                "summary": summary,
                "status": "summary_done",
                "current_agent": "quality_check",
            }

        return _node
