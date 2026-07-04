"""重拆对齐（spec §5，4.1.4 的 P1 零 LLM 落地）。

标题精确匹配（trim；FAQ 用「标准问法」段覆盖标题，与导入 confirm 规则一致）：
- 标题匹配 + hash 相同 → unchanged；hash 不同 → changed
- 新标题 → new；旧条目标题未出现 → disappeared（content 置空，seq 续在末尾）
改标题会被判为"消失+新增"，属 P1 已知边界，由预览页人工纠正。
"""

from dataclasses import dataclass

from app.pipeline import parser
from app.pipeline.content_hash import content_hash


@dataclass
class ExistingEntry:
    kid: str
    title: str
    content_hash: str
    is_form: bool


@dataclass
class AlignedItem:
    seq: int
    title: str | None
    content: str
    align_action: str
    match_kid: str | None
    is_form: bool = False


def _entry_title(type_: str, entry_md: str) -> tuple[str | None, dict[str, str]]:
    """提取条目标题和字段。对于 FAQ，用「标准问法」段覆盖标题。"""
    title, fields = parser.parse_sections(entry_md)
    if type_ == "faq" and fields.get("标准问法", "").strip():
        title = fields["标准问法"].strip()
    return (title.strip() if title else None), fields


def align(type_: str, markdown: str, existing: list[ExistingEntry]) -> list[AlignedItem]:
    """对齐新内容与现有条目。

    Args:
        type_: 内容类型（如 "faq"）
        markdown: 新的 markdown 文本
        existing: 现有条目列表

    Returns:
        对齐后的条目列表，按 seq 排序（新条目在前，消失条目在末尾）
    """
    by_title = {e.title.strip(): e for e in existing}
    matched: set[str] = set()
    items: list[AlignedItem] = []

    # 处理新条目
    for seq, entry_md in enumerate(parser.split_entries(markdown), start=1):
        title, fields = _entry_title(type_, entry_md)
        old = by_title.get(title) if title else None
        if old is None:
            items.append(AlignedItem(seq, title, entry_md, "new", None))
        else:
            matched.add(old.kid)
            action = "unchanged" if content_hash(type_, fields) == old.content_hash else "changed"
            items.append(AlignedItem(seq, title, entry_md, action, old.kid))

    # 处理消失的条目
    next_seq = len(items) + 1
    for e in existing:
        if e.kid not in matched:
            items.append(AlignedItem(next_seq, e.title, "", "disappeared", e.kid, is_form=e.is_form))
            next_seq += 1
    return items
