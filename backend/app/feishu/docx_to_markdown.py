"""飞书 docx Block 树 → Markdown（feishu-sync §5）。"""

from __future__ import annotations

from dataclasses import dataclass, field

# Block type 14 语言枚举 → Markdown fenced code lang（§5.3）
_CODE_LANG_MAP: dict[int, str] = {
    1: "text",
    23: "go",
    28: "json",
    31: "javascript",
    39: "markdown",
    50: "python",
    60: "shell",
    67: "yaml",
}

# block_type → 内容字段名（飞书 OpenAPI 约定）
_BLOCK_FIELD: dict[int, str] = {
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    17: "todo",
    18: "bitable",
    19: "callout",
    22: "divider",
    23: "file",
    24: "grid",
    25: "grid_column",
    26: "iframe",
    27: "image",
    28: "isv",
    29: "mindnote",
    30: "sheet",
    31: "table",
    32: "table_cell",
    33: "view",
    34: "quote_container",
    35: "task",
    49: "synced_source",
    50: "synced_reference",
    51: "sub_page_list",
}

_SKIP_WITH_WARN = frozenset({18, 23, 24, 25, 26, 29, 30, 35, 51, 999})
_SKIP_SILENT = frozenset({1, 28, 33})


@dataclass
class PendingMedia:
    block_id: str
    token: str
    filename: str | None = None


@dataclass
class MarkdownWithMap:
    markdown: str
    block_map: dict[int, str] = field(default_factory=dict)
    skipped_blocks: list[str] = field(default_factory=list)
    pending_media: list[PendingMedia] = field(default_factory=list)


@dataclass
class _RenderCtx:
    block_map: dict[int, str]
    skipped_blocks: list[str]
    pending_media: list[PendingMedia]
    h1_seq: int = 0


def blocks_to_markdown(blocks: list[dict]) -> MarkdownWithMap:
    """将 with_descendants 拉回的 Block 列表渲染为 Markdown。"""
    if not blocks:
        return MarkdownWithMap(markdown="")

    by_id = {b["block_id"]: b for b in blocks}
    root_id = _find_root_id(blocks)
    ctx = _RenderCtx(block_map={}, skipped_blocks=[], pending_media=[])

    parts: list[str] = []
    root = by_id[root_id]
    for child_id in root.get("children") or []:
        rendered = _render_block(child_id, by_id, ctx, depth=0, list_ctx=None)
        if rendered:
            parts.append(rendered)

    markdown = "\n\n".join(p.strip() for p in parts if p.strip())
    if markdown:
        markdown += "\n"
    return MarkdownWithMap(
        markdown=markdown,
        block_map=ctx.block_map,
        skipped_blocks=ctx.skipped_blocks,
        pending_media=ctx.pending_media,
    )


def _find_root_id(blocks: list[dict]) -> str:
    for b in blocks:
        if b.get("block_type") == 1:
            return b["block_id"]
    return blocks[0]["block_id"]


def _render_block(
    block_id: str,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    *,
    depth: int,
    list_ctx: dict | None,
) -> str:
    block = by_id.get(block_id)
    if not block:
        return ""

    btype = block.get("block_type", 0)

    if btype in _SKIP_SILENT:
        return _render_children(block, by_id, ctx, depth, list_ctx)

    if btype in _SKIP_WITH_WARN:
        ctx.skipped_blocks.append(block_id)
        return ""

    if btype == 2:
        return _render_text_block(block, by_id, ctx, depth, list_ctx)
    if 3 <= btype <= 11:
        return _render_heading(block, btype, by_id, ctx, depth, list_ctx)
    if btype == 12:
        return _render_list_item(block, by_id, ctx, depth, ordered=False)
    if btype == 13:
        return _render_list_item(block, by_id, ctx, depth, ordered=True)
    if btype == 14:
        return _render_code(block)
    if btype == 15:
        return _render_quote_block(block, by_id, ctx, depth)
    if btype == 17:
        return _render_todo(block, by_id, ctx, depth)
    if btype == 19:
        return _render_callout(block, by_id, ctx, depth)
    if btype == 22:
        return "---"
    if btype == 27:
        return _render_image(block, ctx)
    if btype == 31:
        return _render_table(block, by_id, ctx)
    if btype == 34:
        return _render_quote_container(block, by_id, ctx, depth)
    if btype == 49:
        return _render_synced_source(block)
    if btype == 50:
        return "> [引用同步块暂不支持，请本地化内容]\n"

    ctx.skipped_blocks.append(block_id)
    return ""


def _render_children(
    block: dict,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    depth: int,
    list_ctx: dict | None,
) -> str:
    parts = []
    for cid in block.get("children") or []:
        r = _render_block(cid, by_id, ctx, depth=depth, list_ctx=list_ctx)
        if r:
            parts.append(r)
    return "\n\n".join(parts)


def _render_text_block(
    block: dict,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    depth: int,
    list_ctx: dict | None,
) -> str:
    body = _elements_to_md(_block_body(block))
    child_parts = []
    for cid in block.get("children") or []:
        r = _render_block(cid, by_id, ctx, depth=depth, list_ctx=list_ctx)
        if r:
            child_parts.append(r)
    if child_parts:
        body = "\n\n".join(p for p in [body, *child_parts] if p)
    return body


def _render_heading(
    block: dict,
    btype: int,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    depth: int,
    list_ctx: dict | None,
) -> str:
    level = min(btype - 2, 6)  # 3→H1 … 11→H9，CommonMark 最多 6 级
    text = _elements_to_md(_block_body(block))
    prefix = "#" * level
    line = f"{prefix} {text}".rstrip()

    if level == 1:
        ctx.h1_seq += 1
        ctx.block_map[ctx.h1_seq] = block["block_id"]

    child_parts = []
    for cid in block.get("children") or []:
        r = _render_block(cid, by_id, ctx, depth=depth, list_ctx=list_ctx)
        if r:
            child_parts.append(r)
    if child_parts:
        return line + "\n\n" + "\n\n".join(child_parts)
    return line


def _render_list_item(
    block: dict,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    depth: int,
    *,
    ordered: bool,
) -> str:
    indent = "  " * depth
    body_field = _block_body(block)
    text = _elements_to_md(body_field)

    if ordered:
        seq = (body_field or {}).get("style", {}).get("sequence") or 1
        marker = f"{seq}. "
    else:
        marker = "- "

    lines = [f"{indent}{marker}{text}" if text else f"{indent}{marker}".rstrip()]

    for cid in block.get("children") or []:
        child = _render_block(cid, by_id, ctx, depth=depth + 1, list_ctx=None)
        if child:
            lines.append(child)
    return "\n".join(lines)


def _render_code(block: dict) -> str:
    body = _block_body(block) or {}
    lang_id = (body.get("style") or {}).get("language", 1)
    lang = _CODE_LANG_MAP.get(lang_id, "text")
    content = _elements_to_md(body, plain=True)
    return f"```{lang}\n{content}\n```"


def _render_quote_block(
    block: dict,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    depth: int,
) -> str:
    inner = _elements_to_md(_block_body(block))
    for cid in block.get("children") or []:
        r = _render_block(cid, by_id, ctx, depth=depth, list_ctx=None)
        if r:
            inner = "\n".join(p for p in [inner, r] if p)
    return _prefix_lines(inner, "> ")


def _render_callout(
    block: dict,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    depth: int,
) -> str:
    inner = _elements_to_md(_block_body(block))
    for cid in block.get("children") or []:
        r = _render_block(cid, by_id, ctx, depth=depth, list_ctx=None)
        if r:
            inner = "\n".join(p for p in [inner, r] if p)
    quoted = _prefix_lines(inner, "> ")
    return f"> [!NOTE]\n{quoted}" if quoted else "> [!NOTE]"


def _render_todo(
    block: dict,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    depth: int,
) -> str:
    body = _block_body(block) or {}
    done = (body.get("style") or {}).get("done", False)
    mark = "- [x] " if done else "- [ ] "
    text = _elements_to_md(body)
    line = f"{mark}{text}"
    for cid in block.get("children") or []:
        r = _render_block(cid, by_id, ctx, depth=depth + 1, list_ctx=None)
        if r:
            line += "\n" + r
    return line


def _render_image(block: dict, ctx: _RenderCtx) -> str:
    image = block.get("image") or {}
    token = image.get("token") or ""
    ctx.pending_media.append(
        PendingMedia(block_id=block["block_id"], token=token, filename=image.get("name"))
    )
    return f"![image](<IMAGE_PENDING:{block['block_id']}>)"



def _render_table(block: dict, by_id: dict[str, dict], ctx: _RenderCtx) -> str:
    table = block.get("table") or {}
    prop = table.get("property") or {}
    cols = prop.get("column_size") or 0
    rows = prop.get("row_size") or 0
    if cols <= 0 or rows <= 0:
        ctx.skipped_blocks.append(block["block_id"])
        return ""

    cells: list[str] = []
    for cid in block.get("children") or []:
        cell_block = by_id.get(cid)
        if not cell_block:
            cells.append("")
            continue
        cell_text = _render_table_cell(cell_block, by_id, ctx)
        cells.append(cell_text.replace("|", "\\|").replace("\n", " "))

    while len(cells) < rows * cols:
        cells.append("")

    header = "| " + " | ".join(["列"] * cols) + " |"
    sep = "| " + " | ".join(["---"] * cols) + " |"
    body_rows = []
    for r in range(rows):
        row_cells = cells[r * cols : (r + 1) * cols]
        body_rows.append("| " + " | ".join(row_cells) + " |")
    return "\n".join([header, sep, *body_rows])


def _render_table_cell(cell_block: dict, by_id: dict[str, dict], ctx: _RenderCtx) -> str:
    parts = []
    for cid in cell_block.get("children") or []:
        child = by_id.get(cid)
        if not child:
            continue
        if child.get("block_type") == 2:
            parts.append(_elements_to_md(_block_body(child)))
        else:
            parts.append(_render_block(cid, by_id, ctx, depth=0, list_ctx=None))
    return " ".join(p for p in parts if p)


def _render_quote_container(
    block: dict,
    by_id: dict[str, dict],
    ctx: _RenderCtx,
    depth: int,
) -> str:
    inner_parts = []
    for cid in block.get("children") or []:
        r = _render_block(cid, by_id, ctx, depth=depth, list_ctx=None)
        if r:
            inner_parts.append(r)
    return _prefix_lines("\n\n".join(inner_parts), "> ")


def _render_synced_source(block: dict) -> str:
    src = block.get("synced_source") or {}
    title = src.get("title") or "外部文档"
    url = src.get("url") or src.get("source_url") or "#"
    return f"> [同步自 {title}]({url})"


def _block_body(block: dict) -> dict | None:
    field = _BLOCK_FIELD.get(block.get("block_type", 0))
    if not field:
        return None
    return block.get(field)


def _elements_to_md(body: dict | None, *, plain: bool = False) -> str:
    if not body:
        return ""
    elements = body.get("elements") or []
    parts: list[str] = []
    for el in elements:
        if "text_run" in el:
            run = el["text_run"]
            content = run.get("content") or ""
            if plain:
                parts.append(content)
                continue
            style = run.get("text_element_style") or {}
            if style.get("inline_code"):
                content = f"`{content}`"
            if style.get("bold"):
                content = f"**{content}**"
            if style.get("italic"):
                content = f"*{content}*"
            if style.get("strikethrough"):
                content = f"~~{content}~~"
            if style.get("underline"):
                content = f"<u>{content}</u>"
            link = style.get("link") or {}
            if link.get("url"):
                content = f"[{content}]({link['url']})"
            parts.append(content)
        elif "mention_user" in el:
            parts.append(f"@{el['mention_user'].get('user_id', '用户')}")
        elif "mention_doc" in el:
            md = el["mention_doc"]
            title = md.get("title") or "文档"
            url = md.get("url") or "#"
            parts.append(f"[{title}]({url})")
        elif "equation" in el:
            parts.append(f"$${el['equation'].get('content', '')}$$")
        # file / inline_block：跳过（§5.4）
    return "".join(parts)


def _prefix_lines(text: str, prefix: str) -> str:
    if not text:
        return ""
    return "\n".join(prefix + line if line else prefix.rstrip() for line in text.splitlines())
