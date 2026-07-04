"""模板完整性校验九条规则的正反用例（技术设计文档 8.2）。"""

from app.pipeline.validators import validate

# 各类型完全合法的基准 sections（正例基线，反例在其上做最小破坏）
VALID = {
    "faq": {
        "标准问法": "企业版发票如何申请？",
        "相似问法": "- 怎么开发票？\n- 发票在哪里申请？",
        "标准答案": "登录管理后台申请。",
        "适用条件": "企业版付费客户",
        "例外情况": "代理商订单除外",
    },
    "sop": {
        "目标与适用场景": "免单审核标准操作。",
        "前置条件": "具备审核权限",
        "操作步骤": "1. 调出订单详情，预期看到配送时间线；\n2. 点击通过，预期状态变更。",
        "异常与分支处理": "无",
        "完成标志": "工单自动关闭。",
        "注意事项": "无",
    },
    "policy": {
        "一句话摘要": "平台原因免单，每月上限 3 次。",
        "适用范围": "全部即时配送订单",
        "规则条款": "1. 超时 40 分钟全额免单。",
        "例外条款": "无",
        "生效 / 失效时间": "2026-01-01 生效，长期有效。",
    },
    "product": {
        "功能定义": "批量开票功能。",
        "适用版本 / 套餐": "企业版",
        "能力边界": "支持：电子普票、专票。\n不支持：合并开票。",
    },
    "case": {
        "问题现象": "退款按钮置灰。",
        "触发条件与根因": "配送中状态退款入口按设计关闭。",
        "排查步骤": "1. 核对订单状态。",
        "解决方案": "引导用户等待配送结束。",
    },
    "term": {
        "术语名": "免单",
        "定义": "平台原因导致履约问题时的全额赔付动作。",
        "同义词 / 别名": "全额赔付",
    },
}


def rules_of(findings):
    return {f.rule for f in findings}


def blocking_of(findings):
    return {f.rule for f in findings if f.level == "blocking"}


class TestValidBaselines:
    def test_all_types_valid_no_findings(self):
        for type_, sections in VALID.items():
            assert validate(type_, dict(sections)) == [], f"{type_} 基准数据不应有 finding"

    def test_optional_section_absent_ok(self):
        s = dict(VALID["faq"])
        del s["例外情况"]
        assert validate("faq", s) == []


class TestGenericRules:
    def test_missing_required_section_blocking(self):
        s = dict(VALID["faq"])
        del s["适用条件"]
        findings = validate("faq", s)
        assert "missing_required_section" in blocking_of(findings)

    def test_empty_required_section_blocking(self):
        s = dict(VALID["faq"])
        s["标准答案"] = "   "
        assert "missing_required_section" in blocking_of(validate("faq", s))

    def test_unknown_section_blocking(self):
        s = dict(VALID["faq"])
        s["自由发挥段"] = "内容"
        assert "unknown_section" in blocking_of(validate("faq", s))

    def test_dangling_reference_blocking(self):
        s = dict(VALID["faq"])
        s["标准答案"] = "开票步骤详见附件。"
        assert "dangling_reference" in blocking_of(validate("faq", s))

    def test_dangling_reference_variants(self):
        for phrase in ["参见", "见原文", "如上所述", "上述文档"]:
            s = dict(VALID["faq"])
            s["标准答案"] = f"具体规则{phrase}即可。"
            assert "dangling_reference" in blocking_of(validate("faq", s)), phrase


class TestFaqRules:
    def test_similar_questions_less_than_two_warning(self):
        s = dict(VALID["faq"])
        s["相似问法"] = "- 怎么开发票？"
        findings = validate("faq", s)
        hits = [f for f in findings if f.rule == "faq_similar_questions"]
        assert hits and hits[0].level == "warning"

    def test_similar_questions_two_items_ok(self):
        assert validate("faq", dict(VALID["faq"])) == []


class TestSopRules:
    def test_steps_not_ordered_list_blocking(self):
        s = dict(VALID["sop"])
        s["操作步骤"] = "先调出订单，预期看到详情；然后点击通过，预期状态变更。"
        assert "sop_steps_ordered_list" in blocking_of(validate("sop", s))

    def test_step_without_expectation_blocking(self):
        s = dict(VALID["sop"])
        s["操作步骤"] = "1. 调出订单详情，预期看到时间线；\n2. 点击通过按钮。"
        assert "sop_step_expectation" in blocking_of(validate("sop", s))

    def test_high_risk_word_without_rollback_blocking(self):
        s = dict(VALID["sop"])
        s["操作步骤"] = "1. 发起退款操作，预期出现确认框。"
        assert "sop_high_risk_rollback" in blocking_of(validate("sop", s))

    def test_high_risk_word_with_rollback_ok(self):
        s = dict(VALID["sop"])
        s["操作步骤"] = "1. 发起退款操作，预期出现确认框。"
        s["回滚方式"] = "30 分钟内可撤销。"
        assert "sop_high_risk_rollback" not in rules_of(validate("sop", s))

    def test_high_risk_words_each_detected(self):
        for word in ["删除", "退款", "资金", "扣款", "对客生效"]:
            s = dict(VALID["sop"])
            s["操作步骤"] = f"1. 执行{word}动作，预期完成。"
            assert "sop_high_risk_rollback" in blocking_of(validate("sop", s)), word


class TestPolicyRules:
    def test_missing_effective_date_blocking(self):
        s = dict(VALID["policy"])
        del s["生效 / 失效时间"]
        assert "missing_required_section" in blocking_of(validate("policy", s))


class TestProductRules:
    def test_capability_missing_not_supported_blocking(self):
        s = dict(VALID["product"])
        s["能力边界"] = "支持：电子普票、专票。"
        assert "product_capability_boundary" in blocking_of(validate("product", s))

    def test_capability_missing_supported_blocking(self):
        s = dict(VALID["product"])
        s["能力边界"] = "不支持：合并开票。"
        assert "product_capability_boundary" in blocking_of(validate("product", s))

    def test_capability_both_present_ok(self):
        assert validate("product", dict(VALID["product"])) == []


class TestCaseRules:
    def test_unknown_root_cause_warning(self):
        s = dict(VALID["case"])
        s["触发条件与根因"] = "未知"
        findings = validate("case", s)
        hits = [f for f in findings if f.rule == "case_root_cause_unknown"]
        assert hits and hits[0].level == "warning"

    def test_known_root_cause_ok(self):
        assert "case_root_cause_unknown" not in rules_of(validate("case", dict(VALID["case"])))


class TestTermRules:
    def test_circular_definition_warning(self):
        s = dict(VALID["term"])
        s["定义"] = "免单是指对订单执行免单的动作。"
        findings = validate("term", s)
        hits = [f for f in findings if f.rule == "term_circular_definition"]
        assert hits and hits[0].level == "warning"

    def test_definition_without_term_name_ok(self):
        assert validate("term", dict(VALID["term"])) == []
