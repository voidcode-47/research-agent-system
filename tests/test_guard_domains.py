"""Academic subject words do not turn a paper operation into a life service."""
import pytest

from utils.guard import gate


@pytest.mark.parametrize("question", [
    "请分析这篇股票收益预测论文的实验方法。",
    "这篇天气预测论文的训练数据划分是否存在时间泄漏？",
    "请检查金融市场股票收益率研究中的统计检验与论文结论是否一致。",
    "请指出气象学论文的天气预测实验中缺少哪些对照组。",
    "这个股票预测方法的交叉验证设计是否合理？",
    "请润色论文中关于极端天气风险的这一段。",
    "请分析金融风险论文的样本选择偏差。",
    "请检查气象学论文的降水量数据是否支持结论。",
])
def test_domain_specific_paper_operations_are_allowed(question):
    result = gate(question, has_paper=True)
    assert result["allowed"] is True
    assert result["reason"] == "ok"


@pytest.mark.parametrize("question", [
    "推荐几只明天会涨的股票。",
    "帮我看看今天的股票。",
    "帮我查一下明天北京的天气。",
    "请告诉我周末的天气预报。",
    "帮我写一个股票交易程序。",
    "请写代码抓取天气预报。",
    "为了这篇论文，请推荐一只值得买入的股票。",
    "为了写气象学论文，帮我查一下明天北京的天气。",
])
def test_unrelated_services_remain_rejected_with_paper_context(question):
    result = gate(question, has_paper=True)
    assert result["allowed"] is False
    assert result["reason"] == "off_topic"
