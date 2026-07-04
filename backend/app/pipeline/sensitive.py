"""敏感信息检测，正则为主、不调用 LLM（技术设计文档 8.3）。

P1 来源均为同步提交：命中即拒绝并返回片段位置。
"""

import re
from dataclasses import dataclass

_RULES: list[tuple[str, re.Pattern]] = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("云凭证 AK", re.compile(r"AKIA[0-9A-Z]{16}|AKLT[\w-]{16,}|LTAI[A-Za-z0-9]{12,}")),
    ("Secret 赋值", re.compile(r"(secret|password|api[_-]?key)\s*[:=]\s*\S{8,}", re.IGNORECASE)),
    (
        "内网 IP",
        re.compile(r"10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"),
    ),
]


@dataclass
class SensitiveHit:
    rule: str
    snippet: str
    position: int


def scan(content: str) -> list[SensitiveHit]:
    hits: list[SensitiveHit] = []
    for name, pattern in _RULES:
        for m in pattern.finditer(content):
            hits.append(SensitiveHit(rule=name, snippet=m.group(0), position=m.start()))
    return hits
