"""kid 生成规则（技术设计文档 5.1）。

格式 {type_code}-{domain_short}-{seq}；common 域 short_code 为空串，
kid 退化为两段形式（如 term-0002）。seq 4 位零填充、超 9999 自然扩位。
"""

TYPE_CODE = {
    "faq": "faq",
    "sop": "sop",
    "policy": "pol",
    "product": "prd",
    "case": "case",
    "term": "term",
}

KNOWLEDGE_TYPES = tuple(TYPE_CODE)


def build_kid(type_: str, domain_short: str, seq: int) -> str:
    if type_ not in TYPE_CODE:
        raise ValueError(f"unknown knowledge type: {type_}")
    if seq < 1:
        raise ValueError(f"seq must be positive: {seq}")
    parts = [TYPE_CODE[type_]]
    if domain_short:
        parts.append(domain_short)
    parts.append(f"{seq:04d}")
    return "-".join(parts)
