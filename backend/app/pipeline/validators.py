"""模板完整性校验（技术设计文档 8.2）。

校验器按类型硬编码为纯函数，与模板一起冻结（ADR-0007），改动走发版；
每条规则配正反单测。未知段名规则来自 8.1（解析层保留段名、此处报错）。
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.pipeline.content_hash import SECTION_ORDER

Level = Literal["blocking", "warning"]


@dataclass
class Finding:
    rule: str
    level: Level
    message: str


# 附录 A：✅ 必填段（"可填占位值"的段也必须出现且非空，占位值如"无"合法）
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "faq": ["标准问法", "相似问法", "标准答案", "适用条件"],
    "sop": ["目标与适用场景", "前置条件", "操作步骤", "异常与分支处理", "完成标志"],
    "policy": ["一句话摘要", "适用范围", "规则条款", "例外条款", "生效 / 失效时间"],
    "product": ["功能定义", "适用版本 / 套餐", "能力边界"],
    "case": ["问题现象", "触发条件与根因", "排查步骤", "解决方案"],
    "term": ["术语名", "定义", "同义词 / 别名"],
}

_DANGLING_REF = re.compile(r"参见|见原文|如上所述|上述文档|详见附件")
_HIGH_RISK = re.compile(r"删除|退款|资金|扣款|对客生效")
_LIST_ITEM = re.compile(r"^\s*[-*]\s+\S", re.MULTILINE)
_ORDERED_STEP = re.compile(r"^\s*\d+[.、)]\s*", re.MULTILINE)


def _generic_findings(type_: str, sections: dict[str, str]) -> list[Finding]:
    findings = []
    for name in REQUIRED_SECTIONS[type_]:
        if not sections.get(name, "").strip():
            findings.append(
                Finding("missing_required_section", "blocking", f"必填段「{name}」缺失或为空")
            )
    known = set(SECTION_ORDER[type_])
    for name in sections:
        if name not in known:
            findings.append(
                Finding("unknown_section", "blocking", f"未知段名「{name}」，段名须与模板一致")
            )
    for name, content in sections.items():
        m = _DANGLING_REF.search(content)
        if m:
            findings.append(
                Finding(
                    "dangling_reference",
                    "blocking",
                    f"段「{name}」存在悬空指代「{m.group()}」，正文须自包含",
                )
            )
    return findings


def _faq_findings(sections: dict[str, str]) -> list[Finding]:
    similar = sections.get("相似问法", "")
    if similar.strip() and len(_LIST_ITEM.findall(similar)) < 2:
        return [Finding("faq_similar_questions", "warning", "相似问法不足 2 条，影响召回")]
    return []


def _sop_findings(sections: dict[str, str]) -> list[Finding]:
    findings = []
    steps = sections.get("操作步骤", "")
    if steps.strip():
        items = _ORDERED_STEP.split(steps)[1:]  # 首段为编号前内容，丢弃
        if not items:
            findings.append(
                Finding("sop_steps_ordered_list", "blocking", "操作步骤须为有序列表（1. 2. …）")
            )
        else:
            for i, item in enumerate(items, 1):
                if "预期" not in item:
                    findings.append(
                        Finding("sop_step_expectation", "blocking", f"步骤 {i} 缺少「预期」描述")
                    )
    body = "\n".join(sections.values())
    m = _HIGH_RISK.search(body)
    if m and not sections.get("回滚方式", "").strip():
        findings.append(
            Finding(
                "sop_high_risk_rollback",
                "blocking",
                f"命中高危动作词「{m.group()}」，回滚方式必填",
            )
        )
    return findings


def _product_findings(sections: dict[str, str]) -> list[Finding]:
    boundary = sections.get("能力边界", "")
    if boundary.strip():
        has_not = "不支持" in boundary
        has_yes = "支持" in boundary.replace("不支持", "")
        if not (has_not and has_yes):
            return [
                Finding(
                    "product_capability_boundary",
                    "blocking",
                    "能力边界须同时包含「支持」与「不支持」子项",
                )
            ]
    return []


def _case_findings(sections: dict[str, str]) -> list[Finding]:
    if sections.get("触发条件与根因", "").strip() == "未知":
        return [
            Finding(
                "case_root_cause_unknown",
                "warning",
                "触发条件与根因为「未知」（P1 记入 risk_note；P2 转中风险）",
            )
        ]
    return []


def _term_findings(sections: dict[str, str]) -> list[Finding]:
    name = sections.get("术语名", "").strip()
    if name and name in sections.get("定义", ""):
        return [
            Finding(
                "term_circular_definition", "warning", f"定义中出现术语名「{name}」自身（循环引用）"
            )
        ]
    return []


_TYPE_RULES = {
    "faq": _faq_findings,
    "sop": _sop_findings,
    "policy": lambda s: [],  # policy 特有规则（生效时间必填）由通用必填段规则覆盖
    "product": _product_findings,
    "case": _case_findings,
    "term": _term_findings,
}


def validate(type_: str, sections: dict[str, str]) -> list[Finding]:
    if type_ not in REQUIRED_SECTIONS:
        raise ValueError(f"未知知识类型: {type_}")
    return _generic_findings(type_, sections) + _TYPE_RULES[type_](sections)
