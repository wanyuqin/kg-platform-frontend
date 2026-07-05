"""导入批内去重（import dedup 阶段一，spec §3）。

纯内存、无 DB；与 publish 共用 content_hash 计算。
"""

from dataclasses import asdict, dataclass
from typing import Literal

from app.pipeline.content_hash import content_hash

Level = Literal["blocking"]


@dataclass
class DedupInput:
    seq: int
    item_id: int | None
    fields: dict[str, str]
    align_action: str = "new"
    skip: bool = False  # unchanged/disappeared 或模板/敏感已 blocking


@dataclass
class DedupFinding:
    rule: str
    level: Level
    message: str
    meta: dict


_SKIP_ALIGN = frozenset({"unchanged", "disappeared"})


def batch_dedup_findings(
    type_: str,
    items: list[DedupInput],
) -> dict[int, list[DedupFinding]]:
    """阶段一：当前文件/批次内去重，O(n)，无 DB。返回 key 为 item_id。"""
    seen: dict[str, tuple[int, int]] = {}  # hash -> (seq, item_id)
    out: dict[int, list[DedupFinding]] = {}

    for item in sorted(items, key=lambda x: x.seq):
        if item.skip or item.align_action in _SKIP_ALIGN or item.item_id is None:
            continue
        hash_ = content_hash(type_, item.fields)
        if hash_ in seen:
            dup_seq, dup_id = seen[hash_]
            out[item.item_id] = [
                DedupFinding(
                    rule="duplicate_in_batch",
                    level="blocking",
                    message=f"与本文件第 {dup_seq} 条内容重复",
                    meta={
                        "scope": "batch",
                        "duplicate_seq": dup_seq,
                        "duplicate_item_id": dup_id,
                        "content_hash": hash_,
                    },
                )
            ]
        else:
            seen[hash_] = (item.seq, item.item_id)
            out.setdefault(item.item_id, [])

    return out


def merge_dedup_into_validation(
    validation: list[dict],
    findings: list[DedupFinding],
) -> tuple[list[dict], bool]:
    """合并 findings，返回 (新 validation, 是否仍 valid)。"""
    merged = list(validation) + [asdict(f) for f in findings]
    ok = not any(f["level"] == "blocking" for f in merged)
    return merged, ok
