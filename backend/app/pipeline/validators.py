"""模板完整性校验（技术设计文档 8.2）。

校验器按类型硬编码为纯函数，与模板一起冻结，改动走发版；
每条规则配正反单测。

TODO(P1)：按 8.2 规则表实现九条规则。
"""

from dataclasses import dataclass
from typing import Literal

Level = Literal["blocking", "warning"]


@dataclass
class Finding:
    rule: str
    level: Level
    message: str


def validate(type_: str, sections: dict[str, str]) -> list[Finding]:
    raise NotImplementedError
