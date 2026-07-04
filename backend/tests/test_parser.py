"""Markdown 解析与切分（技术设计文档 8.1；边界用例按 13.1 点名）。"""

from app.pipeline.parser import parse_sections, split_entries

FAQ_ONE = """# 企业版发票如何申请？

## 标准问法
企业版发票如何申请？

## 标准答案
登录管理后台申请。
"""

FAQ_TWO = """# 发票抬头填错了怎么办？

## 标准问法
发票抬头填错了怎么办？

## 标准答案
作废重开。
"""


class TestSplitEntries:
    def test_single_entry(self):
        entries = split_entries(FAQ_ONE)
        assert len(entries) == 1
        assert "企业版发票如何申请" in entries[0]

    def test_multiple_entries_split_at_h1(self):
        entries = split_entries(FAQ_ONE + "\n" + FAQ_TWO)
        assert len(entries) == 2
        assert "登录管理后台申请" in entries[0]
        assert "作废重开" in entries[1]
        assert "作废重开" not in entries[0]

    def test_no_h1_whole_file_is_one_entry(self):
        md = "## 标准问法\n怎么开发票？\n\n## 标准答案\n后台申请。\n"
        entries = split_entries(md)
        assert len(entries) == 1
        assert entries[0].strip() == md.strip()

    def test_preamble_before_first_h1_is_own_entry(self):
        md = "这是一段没有标题的引言。\n\n" + FAQ_ONE
        entries = split_entries(md)
        assert len(entries) == 2
        assert "引言" in entries[0]
        assert "标准答案" in entries[1]

    def test_h1_inside_fenced_code_block_does_not_split(self):
        md = FAQ_ONE + "\n```bash\n# 这是注释不是标题\necho hi\n```\n"
        entries = split_entries(md)
        assert len(entries) == 1
        assert "这是注释不是标题" in entries[0]

    def test_blank_input_yields_no_entries(self):
        assert split_entries("") == []
        assert split_entries("   \n\n  ") == []


class TestParseSections:
    def test_title_and_sections(self):
        title, sections = parse_sections(FAQ_ONE)
        assert title == "企业版发票如何申请？"
        assert sections["标准问法"] == "企业版发票如何申请？"
        assert sections["标准答案"] == "登录管理后台申请。"

    def test_no_h1_title_is_none(self):
        md = "## 标准问法\n怎么开发票？\n"
        title, sections = parse_sections(md)
        assert title is None
        assert sections["标准问法"] == "怎么开发票？"

    def test_section_name_normalized_trim_and_colon(self):
        # 13.1 边界：段名全半角混排——全角冒号、首尾空格、全角空格
        md = "# 标题\n\n##  标准问法：\n内容 A\n\n## 标准答案　:\n内容 B\n"
        _, sections = parse_sections(md)
        assert sections["标准问法"] == "内容 A"
        assert sections["标准答案"] == "内容 B"

    def test_section_name_normalized_to_canonical_spacing(self):
        # 附录 A 规范名"适用版本 / 套餐"，用户写成无空格斜杠也应归一到规范名
        md = "# 标题\n\n## 适用版本/套餐\n企业版\n\n## 生效/失效时间\n2026-01-01\n"
        _, sections = parse_sections(md)
        assert sections["适用版本 / 套餐"] == "企业版"
        assert sections["生效 / 失效时间"] == "2026-01-01"

    def test_unknown_section_name_kept_after_trim(self):
        # 未知段名保留（trim 归一后原样），由 validators 报 blocking
        md = "# 标题\n\n## 自由发挥段：\n内容\n"
        _, sections = parse_sections(md)
        assert sections == {"自由发挥段": "内容"}

    def test_duplicate_section_merged(self):
        # 13.1 边界：重复段名——内容合并不丢失
        md = "# 标题\n\n## 标准答案\n第一段。\n\n## 标准答案\n第二段。\n"
        _, sections = parse_sections(md)
        assert "第一段。" in sections["标准答案"]
        assert "第二段。" in sections["标准答案"]

    def test_content_between_h1_and_first_h2_ignored(self):
        md = "# 标题\n\n游离在段外的文字。\n\n## 标准问法\n内容\n"
        _, sections = parse_sections(md)
        assert list(sections) == ["标准问法"]

    def test_h3_belongs_to_section_content(self):
        md = "# 标题\n\n## 操作步骤\n1. 第一步\n\n### 细节\n补充说明\n"
        _, sections = parse_sections(md)
        assert "### 细节" in sections["操作步骤"]
        assert "补充说明" in sections["操作步骤"]

    def test_section_content_multiline_preserved(self):
        md = "# 标题\n\n## 相似问法\n- 问法一\n- 问法二\n"
        _, sections = parse_sections(md)
        assert sections["相似问法"] == "- 问法一\n- 问法二"
