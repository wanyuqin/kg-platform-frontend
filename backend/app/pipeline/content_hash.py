"""content_hash 规范化与计算（技术设计文档 8.4）。

规范化：按模板段顺序重排 → 各段 trim → 连续空白折叠为单空格 →
段间以两个换行连接 → SHA-256 取 hex。
"""

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")

# 附录 A：各类型段名的重排顺序
SECTION_ORDER: dict[str, list[str]] = {
    "faq": ["标准问法", "相似问法", "标准答案", "适用条件", "例外情况"],
    "sop": [
        "目标与适用场景",
        "前置条件",
        "操作步骤",
        "异常与分支处理",
        "完成标志",
        "回滚方式",
        "注意事项",
    ],
    "policy": [
        "一句话摘要",
        "适用范围",
        "规则条款",
        "例外条款",
        "生效 / 失效时间",
        "罚则与违规处理",
        "制度依据来源",
    ],
    "product": ["功能定义", "适用版本 / 套餐", "能力边界", "使用入口", "限制与配额", "常见误解澄清"],
    "case": ["问题现象", "触发条件与根因", "排查步骤", "解决方案", "影响范围", "预防措施"],
    "term": ["术语名", "定义", "同义词 / 别名", "使用示例", "易混淆术语辨析"],
}


def normalize(type_: str, sections: dict[str, str]) -> str:
    order = SECTION_ORDER[type_]
    parts = []
    for name in order:
        value = sections.get(name)
        if value is None:
            continue
        folded = _WHITESPACE.sub(" ", value.strip())
        if folded:
            parts.append(f"{name}\n{folded}")
    return "\n\n".join(parts)


def content_hash(type_: str, sections: dict[str, str]) -> str:
    return hashlib.sha256(normalize(type_, sections).encode("utf-8")).hexdigest()
