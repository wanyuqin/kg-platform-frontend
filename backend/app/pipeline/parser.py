"""Markdown 解析与切分（技术设计文档 8.1）。

- 一级标题 `#` 为条目边界，一文件多条；无一级标题时整文件一条
- 条目内二级标题 `##` 为模板段名（trim + 全半角冒号/空格归一后与附录 A 精确匹配）
- 一级标题文本默认作为 title；FAQ 以"标准问法"段覆盖

TODO(P1)：实现 split_entries / parse_sections，与 validators 串联。
"""


def split_entries(markdown: str) -> list[str]:
    raise NotImplementedError


def parse_sections(entry: str) -> tuple[str | None, dict[str, str]]:
    """返回 (一级标题文本, {段名: 内容})。"""
    raise NotImplementedError
