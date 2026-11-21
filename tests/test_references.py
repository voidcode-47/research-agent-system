"""参考文献一致性与 GB/T 7714 规范化：纯函数规则测试。"""
from paper import references


def _p(pid: str, ptype: str, text: str, page: int = 1) -> dict:
    return {"id": pid, "type": ptype, "text": text, "page": page}


def test_normalize_entry_adds_number_and_uppercases_type():
    normalized = references.normalize_entry("张三. 方法[j]. 学报, 2020, 12(3): 1–10", 3)
    assert normalized == "[3] 张三. 方法[J]. 学报, 2020, 12(3): 1-10."


def test_normalize_entry_flattens_full_width_brackets():
    normalized = references.normalize_entry("［1］ 李四. 研究[M]. 北京: 科学出版社, 2021")
    assert normalized == "[1] 李四. 研究[M]. 北京: 科学出版社, 2021."


def test_normalize_entry_keeps_existing_number():
    text = "[2] 王五. 综述[J]. 学报, 2019, 8(2): 3-9."
    assert references.normalize_entry(text) == text


def test_analyze_reports_uncited_and_dangling_citations():
    parsed = [
        _p("p1", "body", "已有研究[1]表明……"),
        _p("p2", "body", "另一处引用[3]。"),
        _p("r1", "reference", "[1] 张三. 方法[J]. 学报, 2020."),
        _p("r2", "reference", "[2] 李四. 研究[M]. 北京: 科学出版社, 2021."),
    ]
    result = references.analyze(parsed, {"reference_style": "gbt7714"})
    codes = [it["rule_id"] for it in result["issues"]]
    assert "REF-002" in codes
    assert "REF-003" in codes
    entries = {e["paragraph_id"]: e for e in result["entries"]}
    assert entries["r2"]["problems"][0]["code"] == "REF-002"
    assert entries["r1"]["cited"] is True


def test_analyze_flags_duplicate_numbers_and_entries():
    parsed = [
        _p("p1", "body", "引用[1]。"),
        _p("r1", "reference", "[1] 张三. 方法[J]. 学报, 2020."),
        _p("r2", "reference", "[1] 张三. 方法[J]. 学报, 2020."),
    ]
    codes = [it["rule_id"] for it in references.analyze(parsed)["issues"]]
    assert "REF-001" in codes
    assert "REF-006" in codes


def test_analyze_requires_url_and_date_for_online_reference():
    parsed = [_p("r1", "reference", "[1] 国家统计局. 数据[EB/OL]. 2023.")]
    messages = [it["suggestion"] for it in references.analyze(parsed)["issues"]]
    assert any("访问地址" in m for m in messages)
    assert any("引用日期" in m for m in messages)


def test_analyze_flags_citation_order_for_numeric_style():
    parsed = [
        _p("p1", "body", "先引用[2]，再引用[1]。"),
        _p("r1", "reference", "[1] 张三. 方法[J]. 学报, 2020."),
        _p("r2", "reference", "[2] 李四. 研究[M]. 北京: 科学出版社, 2021."),
    ]
    codes = [it["rule_id"] for it in references.analyze(parsed)["issues"]]
    assert "REF-004" in codes


def test_analyze_skips_uncited_for_apa_style():
    parsed = [
        _p("r1", "reference", "Smith J. A survey[J]. Journal of Software, 2020."),
    ]
    result = references.analyze(parsed, {"reference_style": "apa"})
    assert all(it["rule_id"] != "REF-002" for it in result["issues"])


def test_entries_expose_normalized_text_and_change_flag():
    parsed = [_p("r1", "reference", "李四. 研究[m]. 北京: 科学出版社, 2021")]
    entry = references.analyze(parsed)["entries"][0]
    assert entry["changed"] is True
    assert entry["normalized"] == "[1] 李四. 研究[M]. 北京: 科学出版社, 2021."
