"""飞书审核卡片单元测试。"""

import json

import httpx

from app.feishu.card import build_review_card_content, send_review_card
from app.feishu.client import FeishuClient


class TestBuildReviewCard:
    def test_high_risk_header_template(self):
        raw = build_review_card_content(
            kid="faq-fo-0001",
            title="测试条目",
            domain_code="free-order",
            doc_title="飞书 FAQ",
            risk_level="high",
            risk_note="命中敏感信息",
            feishu_url="https://feishu.cn/docx/abc",
        )
        card = json.loads(raw)
        assert card["header"]["template"] == "red"
        assert "faq-fo-0001" in card["elements"][0]["text"]["content"]
        assert "查看飞书原文" in card["elements"][0]["text"]["content"]


class TestSendReviewCard:
    async def test_send_review_card_returns_message_id(self):
        sent: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "app_access_token" in str(request.url):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "msg_123"}})

        client = FeishuClient(transport=httpx.MockTransport(handler))
        msg_id = await send_review_card(
            client,
            receive_id="ou_reviewer",
            kid="faq-fo-0001",
            title="条目",
            domain_code="free-order",
            doc_title="FAQ",
            risk_level="mid",
            risk_note="内容 hash 变化",
        )
        assert msg_id == "msg_123"
        assert sent[0]["receive_id"] == "ou_reviewer"
        assert sent[0]["msg_type"] == "interactive"
