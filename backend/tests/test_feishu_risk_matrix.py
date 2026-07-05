"""风险矩阵单元测试（feishu-sync §10）。"""

from app.feishu.risk_matrix import (
    RiskMatrixInput,
    publish_mode_for_risk,
    risk_note_for_score,
    score_risk,
)
from app.pipeline.align import AlignedItem


def _item(action: str, content: str = "正文", title: str = "标题") -> AlignedItem:
    return AlignedItem(
        seq=1,
        title=title,
        content=content,
        align_action=action,
        match_kid="kid-1" if action != "new" else None,
    )


class TestScoreRisk:
    def test_first_sync_low_risk(self):
        score = score_risk(
            RiskMatrixInput(
                doc_type="faq",
                previous_content_hash=None,
                new_content_hash="abc",
                aligned=[_item("new")],
                previous_entry_count=0,
                new_entry_count=1,
            )
        )
        assert score.level == "low"
        assert publish_mode_for_risk(score.level) == "publish"

    def test_content_hash_change_mid(self):
        score = score_risk(
            RiskMatrixInput(
                doc_type="faq",
                previous_content_hash="old",
                new_content_hash="new",
                aligned=[_item("changed")],
                previous_entry_count=1,
                new_entry_count=1,
            )
        )
        assert score.dimensions["content_hash"] == "mid"

    def test_many_title_changes_high(self):
        aligned = [_item("new", title=f"t{i}") for i in range(6)]
        score = score_risk(
            RiskMatrixInput(
                doc_type="faq",
                previous_content_hash="h1",
                new_content_hash="h2",
                aligned=aligned,
                previous_entry_count=1,
                new_entry_count=7,
            )
        )
        assert score.dimensions["title_delta"] == "high"
        assert score.level == "high"

    def test_new_sensitive_high(self):
        score = score_risk(
            RiskMatrixInput(
                doc_type="faq",
                previous_content_hash=None,
                new_content_hash="x",
                aligned=[_item("new", content="联系 13800138000")],
                previous_entry_count=0,
                new_entry_count=1,
            )
        )
        assert score.dimensions["new_sensitive"] == "high"
        assert score.level == "high"

    def test_sop_high_risk_word_high(self):
        score = score_risk(
            RiskMatrixInput(
                doc_type="sop",
                previous_content_hash=None,
                new_content_hash="x",
                aligned=[_item("new", content="执行退款操作")],
                previous_entry_count=0,
                new_entry_count=1,
            )
        )
        assert score.dimensions["high_risk_words"] == "high"

    def test_skip_ratio_mid(self):
        score = score_risk(
            RiskMatrixInput(
                doc_type="faq",
                previous_content_hash=None,
                new_content_hash="x",
                aligned=[_item("new")],
                skipped_blocks=2,
                total_blocks=10,
                previous_entry_count=0,
                new_entry_count=1,
            )
        )
        assert score.dimensions["skip_ratio"] == "mid"

    def test_scale_change_mid(self):
        score = score_risk(
            RiskMatrixInput(
                doc_type="faq",
                previous_content_hash="a",
                new_content_hash="b",
                aligned=[_item("new"), _item("new", title="t2")],
                previous_entry_count=5,
                new_entry_count=6,
            )
        )
        assert score.dimensions["scale_change"] == "mid"

    def test_risk_note_high_prefix(self):
        score = score_risk(
            RiskMatrixInput(
                doc_type="faq",
                previous_content_hash=None,
                new_content_hash="x",
                aligned=[_item("new", content="13800138000")],
                previous_entry_count=0,
                new_entry_count=1,
            )
        )
        note = risk_note_for_score(score)
        assert note.startswith("[高风险]")
        assert publish_mode_for_risk(score.level) == "review"
