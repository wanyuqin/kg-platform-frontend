"""docx_to_markdown 单元测试（feishu-sync §5、§16.1）。"""

from app.feishu.docx_to_markdown import blocks_to_markdown


def _page(*children: str) -> list[dict]:
    return [
        {"block_id": "page", "block_type": 1, "children": list(children)},
    ]


def _text(bid: str, content: str, *, parent: str | None = None) -> dict:
    block = {
        "block_id": bid,
        "block_type": 2,
        "text": {"elements": [{"text_run": {"content": content}}]},
        "children": [],
    }
    if parent:
        block["parent_id"] = parent
    return block


def _heading(bid: str, level: int, content: str) -> dict:
    field = f"heading{level}"
    return {
        "block_id": bid,
        "block_type": 2 + level,
        field: {"elements": [{"text_run": {"content": content}}]},
        "children": [],
    }


class TestBlocksToMarkdown:
    def test_text_paragraph(self):
        blocks = _page("t1") + [_text("t1", "你好世界")]
        result = blocks_to_markdown(blocks)
        assert result.markdown.strip() == "你好世界"
        assert result.block_map == {}
        assert result.skipped_blocks == []

    def test_heading_h1_records_block_map(self):
        blocks = _page("h1", "t1") + [
            _heading("h1", 1, "第一条"),
            _text("t1", "正文内容"),
        ]
        result = blocks_to_markdown(blocks)
        assert "# 第一条" in result.markdown
        assert "正文内容" in result.markdown
        assert result.block_map == {1: "h1"}

    def test_heading_levels_capped_at_h6(self):
        blocks = _page("h9") + [_heading("h9", 9, "九级标题")]
        result = blocks_to_markdown(blocks)
        assert result.markdown.startswith("###### 九级标题")

    def test_bold_italic_strikethrough(self):
        blocks = _page("t1") + [
            {
                "block_id": "t1",
                "block_type": 2,
                "text": {
                    "elements": [
                        {
                            "text_run": {
                                "content": "样式",
                                "text_element_style": {
                                    "bold": True,
                                    "italic": True,
                                    "strikethrough": True,
                                },
                            }
                        }
                    ]
                },
                "children": [],
            }
        ]
        result = blocks_to_markdown(blocks)
        assert "~~" in result.markdown and "**" in result.markdown and "*" in result.markdown

    def test_code_block(self):
        blocks = _page("c1") + [
            {
                "block_id": "c1",
                "block_type": 14,
                "code": {
                    "style": {"language": 50},
                    "elements": [{"text_run": {"content": "print('hi')"}}],
                },
                "children": [],
            }
        ]
        result = blocks_to_markdown(blocks)
        assert "```python" in result.markdown
        assert "print('hi')" in result.markdown

    def test_bullet_list(self):
        blocks = _page("b1", "b2") + [
            {
                "block_id": "b1",
                "block_type": 12,
                "bullet": {"elements": [{"text_run": {"content": "项一"}}]},
                "children": ["b1a"],
            },
            {
                "block_id": "b1a",
                "block_type": 12,
                "bullet": {"elements": [{"text_run": {"content": "子项"}}]},
                "children": [],
            },
            {
                "block_id": "b2",
                "block_type": 12,
                "bullet": {"elements": [{"text_run": {"content": "项二"}}]},
                "children": [],
            },
        ]
        result = blocks_to_markdown(blocks)
        assert "- 项一" in result.markdown
        assert "  - 子项" in result.markdown
        assert "- 项二" in result.markdown

    def test_todo_checked_and_unchecked(self):
        blocks = _page("td1", "td2") + [
            {
                "block_id": "td1",
                "block_type": 17,
                "todo": {
                    "style": {"done": False},
                    "elements": [{"text_run": {"content": "待办"}}],
                },
                "children": [],
            },
            {
                "block_id": "td2",
                "block_type": 17,
                "todo": {
                    "style": {"done": True},
                    "elements": [{"text_run": {"content": "完成"}}],
                },
                "children": [],
            },
        ]
        result = blocks_to_markdown(blocks)
        assert "- [ ] 待办" in result.markdown
        assert "- [x] 完成" in result.markdown

    def test_divider(self):
        blocks = _page("d1") + [{"block_id": "d1", "block_type": 22, "children": []}]
        result = blocks_to_markdown(blocks)
        assert "---" in result.markdown

    def test_image_pending_placeholder(self):
        blocks = _page("img1") + [
            {
                "block_id": "img1",
                "block_type": 27,
                "image": {"token": "media_tok_1"},
                "children": [],
            }
        ]
        result = blocks_to_markdown(blocks)
        assert "<IMAGE_PENDING:img1>" in result.markdown
        assert len(result.pending_media) == 1
        assert result.pending_media[0].token == "media_tok_1"

    def test_table_renders_markdown_table(self):
        blocks = _page("tbl") + [
            {
                "block_id": "tbl",
                "block_type": 31,
                "table": {"property": {"row_size": 2, "column_size": 2}},
                "children": ["c11", "c12", "c21", "c22"],
            },
            {
                "block_id": "c11",
                "block_type": 32,
                "table_cell": {},
                "children": ["t11"],
            },
            _text("t11", "A1"),
            {
                "block_id": "c12",
                "block_type": 32,
                "table_cell": {},
                "children": ["t12"],
            },
            _text("t12", "B1"),
            {
                "block_id": "c21",
                "block_type": 32,
                "table_cell": {},
                "children": ["t21"],
            },
            _text("t21", "A2"),
            {
                "block_id": "c22",
                "block_type": 32,
                "table_cell": {},
                "children": ["t22"],
            },
            _text("t22", "B2"),
        ]
        result = blocks_to_markdown(blocks)
        assert "| A1 | B1 |" in result.markdown
        assert "| A2 | B2 |" in result.markdown

    def test_unsupported_block_skipped_with_warn(self):
        blocks = _page("bt") + [
            {"block_id": "bt", "block_type": 18, "bitable": {}, "children": []},
        ]
        result = blocks_to_markdown(blocks)
        assert "bt" in result.skipped_blocks
        assert result.markdown.strip() == ""

    def test_synced_source_placeholder(self):
        blocks = _page("ss") + [
            {
                "block_id": "ss",
                "block_type": 49,
                "synced_source": {"title": "源文档", "url": "https://feishu.cn/docx/src"},
                "children": [],
            }
        ]
        result = blocks_to_markdown(blocks)
        assert "[同步自 源文档]" in result.markdown

    def test_quote_block(self):
        blocks = _page("q1") + [
            {
                "block_id": "q1",
                "block_type": 15,
                "quote": {"elements": [{"text_run": {"content": "引用文字"}}]},
                "children": [],
            }
        ]
        result = blocks_to_markdown(blocks)
        assert "> 引用文字" in result.markdown

    def test_callout_as_note(self):
        blocks = _page("ca1") + [
            {
                "block_id": "ca1",
                "block_type": 19,
                "callout": {"elements": [{"text_run": {"content": "提示"}}]},
                "children": [],
            }
        ]
        result = blocks_to_markdown(blocks)
        assert "> [!NOTE]" in result.markdown
        assert "提示" in result.markdown

    def test_multiple_h1_block_map_seq(self):
        blocks = _page("h1", "h2") + [
            _heading("h1", 1, "第一"),
            _heading("h2", 1, "第二"),
        ]
        result = blocks_to_markdown(blocks)
        assert result.block_map == {1: "h1", 2: "h2"}
