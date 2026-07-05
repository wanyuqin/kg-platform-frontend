"""飞书事件回调校验单元测试（feishu-sync §8）。"""

import hashlib
import json

import pytest

from app.feishu.event import (
    extract_file_token,
    parse_event_payload,
    verify_event_token,
    verify_signature,
    verify_url_challenge,
)
from app.feishu.exceptions import FeishuError


class TestEventVerification:
    def test_url_challenge(self):
        body = {"type": "url_verification", "challenge": "ch-123"}
        assert verify_url_challenge(body) == "ch-123"

    def test_url_challenge_missing_raises(self):
        with pytest.raises(FeishuError):
            verify_url_challenge({"type": "url_verification"})

    def test_non_challenge_returns_none(self):
        assert verify_url_challenge({"type": "event_callback"}) is None

    def test_verify_signature_ok(self, monkeypatch):
        monkeypatch.setattr(
            "app.feishu.event.get_settings",
            lambda: type("S", (), {"lark_encrypt_key": "enc_key"})(),
        )
        body = b'{"hello":"world"}'
        ts, nonce = "1234567890", "nonce1"
        sig = hashlib.sha256(f"{ts}{nonce}enc_key".encode() + body).hexdigest()
        verify_signature(timestamp=ts, nonce=nonce, body=body, signature=sig)

    def test_verify_signature_mismatch_raises(self, monkeypatch):
        monkeypatch.setattr(
            "app.feishu.event.get_settings",
            lambda: type("S", (), {"lark_encrypt_key": "enc_key"})(),
        )
        with pytest.raises(FeishuError, match="签名校验失败"):
            verify_signature(
                timestamp="1",
                nonce="n",
                body=b"{}",
                signature="bad",
            )

    def test_verify_event_token_mismatch(self, monkeypatch):
        monkeypatch.setattr(
            "app.feishu.event.get_settings",
            lambda: type("S", (), {"lark_verification_token": "expected"})(),
        )
        with pytest.raises(FeishuError, match="Verification Token"):
            verify_event_token({"token": "wrong"})

    def test_parse_event_payload(self):
        raw = json.dumps({"schema": "2.0", "header": {"event_type": "drive.file.edited"}})
        event = parse_event_payload(raw.encode())
        assert event["schema"] == "2.0"

    def test_extract_file_token(self):
        event = {"event": {"file_token": "ftok123", "file_type": "docx"}}
        assert extract_file_token(event) == "ftok123"
