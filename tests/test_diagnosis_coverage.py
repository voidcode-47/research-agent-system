"""Diagnosis sampling covers later sections within its context budget."""
import pytest

from paper.analysis import _paper_context


def _paragraph(pid, text, section, page):
    return {"id": pid, "type": "body", "text": text, "section": section, "page": page}


def _paper(language, *, oversized_first=False):
    if language == "zh":
        titles = ["第一章 绪论", "第二章 研究方法", "第三章 实验与结果", "第四章 结论"]
        background = "相关研究主要讨论该问题的背景、应用领域和已有工作的不足。"
        method = "METHOD_EVIDENCE 采用分层抽样，将样本随机划分为训练集和测试集。"
        experiment = "EXPERIMENT_EVIDENCE 与两个基线比较，并报告测试集上的准确率和置信区间。"
        conclusion = "CONCLUSION_EVIDENCE 结果仅支持当前数据集，仍需在其他人群中验证。"
    else:
        titles = ["1 Introduction", "2 Methods", "3 Experiments and Results", "4 Conclusion"]
        background = "Prior studies discuss the background, application areas and limits of earlier work. "
        method = "METHOD_EVIDENCE We use stratified sampling and randomly split training and test data."
        experiment = "EXPERIMENT_EVIDENCE We compare two baselines and report accuracy with confidence intervals."
        conclusion = "CONCLUSION_EVIDENCE The findings apply only to this dataset and need external validation."

    intro_count = 1 if oversized_first else 16
    repeats = 300 if oversized_first else 30
    introduction = [_paragraph(f"p_intro_{i}", background * repeats, titles[0], i + 1)
                    for i in range(intro_count)]
    later = [_paragraph("p_methods", method, titles[1], 20),
             _paragraph("p_experiments", experiment, titles[2], 30),
             _paragraph("p_conclusion", conclusion, titles[3], 40)]
    paragraphs = introduction + later
    sections = [{"id": f"s{i + 1}", "title": title, "level": 1,
                 "paragraph_ids": [p["id"] for p in paragraphs if p["section"] == title]}
                for i, title in enumerate(titles)]
    return {"title": "Diagnosis coverage study", "abstract": "A study with methods and experiments.",
            "keywords": ["sampling", "evaluation", "validation"],
            "sections": sections, "paragraphs": paragraphs}


@pytest.mark.parametrize("language", ["zh", "en"])
def test_diagnosis_context_covers_methods_experiments_and_conclusion(language):
    parsed = _paper(language)
    assert sum(len(p["text"]) for p in parsed["paragraphs"][:16]) > 6000

    context = _paper_context(parsed, max_chars=6000)

    assert len(context) <= 6000
    for marker, pid in [("METHOD_EVIDENCE", "p_methods"),
                        ("EXPERIMENT_EVIDENCE", "p_experiments"),
                        ("CONCLUSION_EVIDENCE", "p_conclusion")]:
        assert marker in context, f"missing later-section evidence: {marker}"
        assert pid in context, f"selected evidence must retain its paragraph ID: {pid}"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_oversized_opening_paragraph_does_not_hide_later_sections(language):
    parsed = _paper(language, oversized_first=True)
    assert len(parsed["paragraphs"][0]["text"]) > 6000

    context = _paper_context(parsed, max_chars=6000)

    assert len(context) <= 6000
    assert "METHOD_EVIDENCE" in context
    assert "p_methods" in context
    assert "EXPERIMENT_EVIDENCE" in context
    assert "CONCLUSION_EVIDENCE" in context


def test_short_paper_retains_all_body_evidence_and_source_ids():
    paragraphs = [_paragraph("p_question", "The research question concerns sampling.", "Introduction", 1),
                  _paragraph("p_method", "The method compares two random samples.", "Methods", 2),
                  _paragraph("p_result", "The result has limited generalizability.", "Results", 3)]
    parsed = {"title": "Short paper", "paragraphs": paragraphs, "sections": []}

    context = _paper_context(parsed, max_chars=6000)

    assert len(context) <= 6000
    for paragraph in paragraphs:
        assert paragraph["id"] in context
        assert paragraph["text"] in context
