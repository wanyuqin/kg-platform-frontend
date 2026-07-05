"""飞书同步风险矩阵（feishu-sync §10）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.pipeline import sensitive
from app.pipeline.align import AlignedItem

RiskLevel = Literal["low", "mid", "high"]

_HIGH_RISK_WORDS = re.compile(r"删除|退款|资金|扣款|对客生效")


@dataclass
class RiskMatrixInput:
    """文档级评分输入（阶段二对齐完成后）。"""

    doc_type: str
    previous_content_hash: str | None
    new_content_hash: str
    aligned: list[AlignedItem]
    skipped_blocks: int = 0
    total_blocks: int = 0
    blocking_error_count: int = 0
    previous_entry_count: int = 0
    new_entry_count: int = 0


@dataclass
class RiskScore:
    level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    dimensions: dict[str, RiskLevel] = field(default_factory=dict)


def _max_level(levels: list[RiskLevel]) -> RiskLevel:
    if "high" in levels:
        return "high"
    if "mid" in levels:
        return "mid"
    return "low"


def _score_content_hash(previous: str | None, new: str) -> RiskLevel:
    if previous is None or previous == new:
        return "low"
    return "mid"


def _score_title_delta(aligned: list[AlignedItem]) -> RiskLevel:
    delta = sum(1 for a in aligned if a.align_action in ("new", "disappeared"))
    if delta <= 2:
        return "low"
    if delta <= 5:
        return "mid"
    return "high"


def _score_new_sensitive(aligned: list[AlignedItem]) -> RiskLevel:
    for item in aligned:
        if item.align_action not in ("new", "changed"):
            continue
        if sensitive.scan(item.content):
            return "high"
    return "low"


def _score_high_risk_words(doc_type: str, aligned: list[AlignedItem]) -> RiskLevel:
    if doc_type not in ("policy", "sop"):
        return "low"
    for item in aligned:
        if item.align_action not in ("new", "changed"):
            continue
        if _HIGH_RISK_WORDS.search(item.content):
            return "high"
    return "low"


def _score_skip_ratio(skipped: int, total: int) -> RiskLevel:
    if total <= 0:
        return "low"
    ratio = skipped / total
    if ratio < 0.05:
        return "low"
    if ratio <= 0.30:
        return "mid"
    return "high"


def _score_blocking_count(count: int) -> RiskLevel:
    if count <= 0:
        return "low"
    if count <= 2:
        return "mid"
    return "high"


def _score_scale_change(previous_count: int, new_count: int) -> RiskLevel:
    if previous_count <= 0:
        return "low"
    change = abs(new_count - previous_count) / previous_count
    if change < 0.20:
        return "low"
    if change <= 0.50:
        return "mid"
    return "high"


def score_risk(inp: RiskMatrixInput) -> RiskScore:
    """任一维度 high → high；否则取各维度最高等级（§10.1）。"""
    dimensions: dict[str, RiskLevel] = {
        "content_hash": _score_content_hash(inp.previous_content_hash, inp.new_content_hash),
        "title_delta": _score_title_delta(inp.aligned),
        "new_sensitive": _score_new_sensitive(inp.aligned),
        "high_risk_words": _score_high_risk_words(inp.doc_type, inp.aligned),
        "skip_ratio": _score_skip_ratio(inp.skipped_blocks, inp.total_blocks),
        "blocking_errors": _score_blocking_count(inp.blocking_error_count),
        "scale_change": _score_scale_change(inp.previous_entry_count, inp.new_entry_count),
    }
    level = _max_level(list(dimensions.values()))
    reasons = [f"{name}={lvl}" for name, lvl in dimensions.items() if lvl != "low"]
    return RiskScore(level=level, reasons=reasons, dimensions=dimensions)


def publish_mode_for_risk(level: RiskLevel) -> Literal["publish", "review"]:
    return "publish" if level == "low" else "review"


def risk_note_for_score(score: RiskScore) -> str:
    if score.level == "low":
        return "飞书同步低风险自动发布"
    prefix = "[高风险] " if score.level == "high" else ""
    detail = "；".join(score.reasons) if score.reasons else score.level
    return f"{prefix}飞书同步{score.level}风险：{detail}"
