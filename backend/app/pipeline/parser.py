"""Markdown 解析与切分（技术设计文档 8.1）。

- 一级标题 `#` 为条目边界，一文件多条；无一级标题时整文件一条
- 条目内二级标题 `##` 为模板段名（trim + 全半角冒号/空格归一后与附录 A 精确匹配）
- 一级标题文本默认作为 title；FAQ 以"标准问法"段覆盖（覆盖由导入流程按类型执行）
- 未知段名 trim 归一后原样保留，由 validators 报 blocking
"""

import re

from app.pipeline.content_hash import SECTION_ORDER

_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TRAILING_COLON = re.compile(r"[:：]\s*$")
_ANY_SPACE = re.compile(r"[\s　]+")


def _clean_name(raw: str) -> str:
    """段名基础归一：全角空格转半角、trim、去尾部全/半角冒号。"""
    name = raw.replace("　", " ").strip()
    name = _TRAILING_COLON.sub("", name).strip()
    return name


# 附录 A 规范段名映射：去除全部空白后的形态 -> 规范名（如"适用版本/套餐"→"适用版本 / 套餐"）
_CANONICAL: dict[str, str] = {
    _ANY_SPACE.sub("", _clean_name(name)): name
    for names in SECTION_ORDER.values()
    for name in names
}


def _normalize_section_name(raw: str) -> str:
    cleaned = _clean_name(raw)
    return _CANONICAL.get(_ANY_SPACE.sub("", cleaned), cleaned)


def split_entries(markdown: str) -> list[str]:
    """按一级标题切分条目；首个一级标题前的非空内容单独成一条；代码块内的 # 不切分。"""
    chunks: list[list[str]] = [[]]
    in_fence = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = _HEADING.match(line)
        if not in_fence and m and len(m.group(1)) == 1:
            chunks.append([])
        chunks[-1].append(line)
    return ["\n".join(c) for c in chunks if "".join(c).strip()]


def parse_sections(entry: str) -> tuple[str | None, dict[str, str]]:
    """返回 (一级标题文本, {规范化段名: 内容})。

    一级标题与首个二级标题之间的游离文本忽略；三级及以下标题属于段内容；
    重复段名的内容合并（两个换行连接），不丢失。
    """
    title: str | None = None
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        if current is None:
            return
        content = "\n".join(buf).strip()
        if current in sections:
            sections[current] = (
                f"{sections[current]}\n\n{content}" if content else sections[current]
            )
        else:
            sections[current] = content

    for line in entry.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        level = len(m.group(1)) if m else 0
        if level == 1 and title is None:
            title = m.group(2).strip()
        elif level == 2:
            flush()
            current = _normalize_section_name(m.group(2))
            buf = []
        elif current is not None:
            buf.append(line)
    flush()
    return title, sections
