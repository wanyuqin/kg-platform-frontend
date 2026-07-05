"""批内去重单元测试（import dedup 阶段一，spec §8.1）。"""

from app.pipeline.dedup import DedupInput, batch_dedup_findings, merge_dedup_into_validation
from tests.test_validators import VALID


def _faq(**overrides) -> dict[str, str]:
    return {**VALID["faq"], **overrides}


def _inp(seq: int, item_id: int, fields: dict[str, str] | None = None, **kwargs) -> DedupInput:
    return DedupInput(seq=seq, item_id=item_id, fields=fields or _faq(), **kwargs)


class TestBatchDedupFindings:
    def test_two_identical_faq_smaller_seq_kept(self):
        items = [
            _inp(3, 101),
            _inp(50, 102, fields=_faq()),
        ]
        findings = batch_dedup_findings("faq", items)
        assert findings.get(101) == []
        dup = findings[102]
        assert len(dup) == 1
        assert dup[0].rule == "duplicate_in_batch"
        assert dup[0].level == "blocking"
        assert dup[0].message == "与本文件第 3 条内容重复"
        assert dup[0].meta["scope"] == "batch"
        assert dup[0].meta["duplicate_seq"] == 3
        assert dup[0].meta["duplicate_item_id"] == 101
        assert "content_hash" in dup[0].meta

    def test_three_identical_only_smallest_has_no_finding(self):
        items = [
            _inp(1, 201),
            _inp(2, 202, fields=_faq()),
            _inp(3, 203, fields=_faq()),
        ]
        findings = batch_dedup_findings("faq", items)
        assert findings.get(201) == []
        assert len(findings[202]) == 1
        assert findings[202][0].meta["duplicate_seq"] == 1
        assert len(findings[203]) == 1
        assert findings[203][0].meta["duplicate_seq"] == 1

    def test_unchanged_and_disappeared_skipped(self):
        items = [
            _inp(1, 301, align_action="unchanged"),
            _inp(2, 302, fields=_faq()),
            _inp(3, 303, fields=_faq(), align_action="disappeared"),
        ]
        findings = batch_dedup_findings("faq", items)
        assert 301 not in findings
        assert 303 not in findings
        assert findings.get(302) == []

    def test_already_blocked_item_skipped(self):
        items = [
            _inp(1, 401, skip=True),
            _inp(2, 402, fields=_faq()),
        ]
        findings = batch_dedup_findings("faq", items)
        assert findings.get(402) == []


class TestMergeDedupIntoValidation:
    def test_merge_marks_invalid_when_blocking(self):
        validation = [{"rule": "faq_similar_questions", "level": "warning", "message": "x"}]
        findings = batch_dedup_findings(
            "faq", [_inp(1, 501), _inp(2, 502, fields=_faq())]
        )[502]
        merged, ok = merge_dedup_into_validation(validation, findings)
        assert ok is False
        assert any(v["rule"] == "duplicate_in_batch" for v in merged)

    def test_merge_stays_valid_without_findings(self):
        validation: list[dict] = []
        merged, ok = merge_dedup_into_validation(validation, [])
        assert ok is True
        assert merged == []
