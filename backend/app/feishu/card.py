"""飞书审核卡片（im/v1/messages，feishu-sync §10.2）。"""

from __future__ import annotations

import json
import logging
from typing import Literal

from app.feishu.client import FeishuClient

logger = logging.getLogger(__name__)

RiskLevel = Literal["low", "mid", "high"]


def build_review_card_content(
    *,
    kid: str,
    title: str,
    domain_code: str,
    doc_title: str,
    risk_level: RiskLevel,
    risk_note: str,
    feishu_url: str | None = None,
) -> str:
    """构造 interactive 卡片 JSON 字符串（msg_type=interactive）。"""
    level_label = {"low": "低", "mid": "中", "high": "高"}.get(risk_level, risk_level)
    lines = [
        f"**{title}**",
        f"- KID：`{kid}`",
        f"- 域：`{domain_code}`",
        f"- 来源文档：{doc_title}",
        f"- 风险等级：**{level_label}**",
        f"- 说明：{risk_note}",
    ]
    if feishu_url:
        lines.append(f"- [查看飞书原文]({feishu_url})")
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "知识平台 · 审核待办"},
            "template": "red" if risk_level == "high" else "orange",
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}],
    }
    return json.dumps(card, ensure_ascii=False)


async def send_review_card(
    client: FeishuClient,
    *,
    receive_id: str,
    kid: str,
    title: str,
    domain_code: str,
    doc_title: str,
    risk_level: RiskLevel,
    risk_note: str,
    feishu_url: str | None = None,
    receive_id_type: str = "open_id",
) -> str | None:
    """发送审核卡片；成功返回 message_id，失败记录日志并返回 None。"""
    content = build_review_card_content(
        kid=kid,
        title=title,
        domain_code=domain_code,
        doc_title=doc_title,
        risk_level=risk_level,
        risk_note=risk_note,
        feishu_url=feishu_url,
    )
    try:
        return await client.send_message(
            receive_id,
            msg_type="interactive",
            content=content,
            receive_id_type=receive_id_type,
        )
    except Exception:
        logger.exception("发送飞书审核卡片失败 kid=%s receive_id=%s", kid, receive_id)
        return None
