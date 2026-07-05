"""飞书事件回调签名校验与解密（feishu-sync §8）。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import get_settings
from app.feishu.exceptions import FeishuError


def verify_url_challenge(body: dict[str, Any]) -> str | None:
    """URL 验证：返回 challenge 字符串；非验证事件返回 None。"""
    if body.get("type") == "url_verification":
        challenge = body.get("challenge")
        if not challenge:
            raise FeishuError("url_verification 缺少 challenge")
        return str(challenge)
    return None


def verify_event_token(body: dict[str, Any]) -> None:
    """校验 Verification Token（明文事件）。"""
    settings = get_settings()
    token = settings.lark_verification_token
    if not token:
        return
    event_token = body.get("token") or body.get("header", {}).get("token")
    if event_token and event_token != token:
        raise FeishuError("Verification Token 不匹配")


def verify_signature(
    *,
    timestamp: str,
    nonce: str,
    body: bytes,
    signature: str,
    encrypt_key: str | None = None,
) -> None:
    """校验 X-Lark-Signature（HMAC-SHA256 等价实现：sha256 拼接）。"""
    key = encrypt_key if encrypt_key is not None else get_settings().lark_encrypt_key
    if not key or not signature:
        return
    content = f"{timestamp}{nonce}{key}".encode() + body
    expected = hashlib.sha256(content).hexdigest()
    if expected != signature:
        raise FeishuError("事件签名校验失败")


def parse_event_payload(raw_body: bytes) -> dict[str, Any]:
    """解析 JSON 事件体。"""
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise FeishuError("事件 body 非合法 JSON") from exc


def extract_file_token(event: dict[str, Any]) -> str | None:
    """从 drive.file.* 事件提取 file_token。"""
    event_obj = event.get("event") or {}
    return event_obj.get("file_token") or event_obj.get("object_token")
