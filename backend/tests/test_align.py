"""重拆对齐纯函数（spec §5）：标题精确匹配，四类动作。"""

from app.pipeline.align import ExistingEntry, align
from app.pipeline.content_hash import content_hash

FAQ_A = "# 如何退款？\n\n## 标准问法\n如何退款？\n\n## 相似问法\n- 退款流程\n- 退钱\n\n## 标准答案\n订单页申请。\n\n## 适用条件\n7 天内"
FAQ_A_CHANGED = FAQ_A.replace("订单页申请。", "订单详情页点击申请退款。")
FAQ_B = "# 发货时间？\n\n## 标准问法\n发货时间？\n\n## 相似问法\n- 几天发货\n- 何时发货\n\n## 标准答案\n当天发货。\n\n## 适用条件\n现货"


def hash_of(entry_md: str) -> str:
    from app.pipeline import parser

    _, fields = parser.parse_sections(entry_md)
    return content_hash("faq", fields)


def exists(kid: str, title: str, md: str, is_form: bool = False) -> ExistingEntry:
    return ExistingEntry(kid=kid, title=title, content_hash=hash_of(md), is_form=is_form)


class TestAlign:
    def test_four_actions(self):
        existing = [
            exists("faq-fo-0001", "如何退款？", FAQ_A),      # 将变更
            exists("faq-fo-0002", "被删掉的", FAQ_B),         # 将消失
        ]
        items = align("faq", f"{FAQ_A_CHANGED}\n\n{FAQ_B}", existing)
        by_action = {i.align_action: i for i in items}
        assert by_action["changed"].match_kid == "faq-fo-0001"
        assert by_action["new"].title == "发货时间？"
        assert by_action["disappeared"].match_kid == "faq-fo-0002"
        assert by_action["disappeared"].content == ""
        assert by_action["disappeared"].seq == 3  # 排在解析条目之后

    def test_unchanged(self):
        existing = [exists("faq-fo-0001", "如何退款？", FAQ_A)]
        items = align("faq", FAQ_A, existing)
        assert items[0].align_action == "unchanged"
        assert items[0].match_kid == "faq-fo-0001"

    def test_faq_title_uses_standard_question(self):
        """FAQ 匹配用「标准问法」段作标题（与 confirm 覆盖规则一致）。"""
        md = FAQ_A.replace("# 如何退款？", "# 随便写的一级标题")
        existing = [exists("faq-fo-0001", "如何退款？", FAQ_A)]
        assert align("faq", md, existing)[0].align_action == "unchanged"

    def test_disappeared_form_entry_flagged(self):
        existing = [exists("faq-fo-0003", "表单加的", FAQ_B, is_form=True)]
        items = align("faq", FAQ_A, existing)
        gone = [i for i in items if i.align_action == "disappeared"][0]
        assert gone.is_form is True
