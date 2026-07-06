"""飞书 OpenAPI 联调测试（真实网络，验证 Mock 无法覆盖的行为）。

运行方式（需 backend/.env 已配置 KG_LARK_APP_ID / KG_LARK_APP_SECRET，且应用已加入测试知识库）：

    cd backend && KG_FEISHU_LIVE=1 uv run pytest tests/test_feishu_live.py -v

默认 skip，避免 CI / 无凭证环境误调飞书。
"""

from __future__ import annotations

import os

import pytest

from app.config import get_settings
from app.feishu.client import FeishuClient
from app.feishu.doc_resolver import parse_feishu_url, resolve_doc, resolve_with_permission
from app.feishu.docx_to_markdown import blocks_to_markdown
from app.feishu.media import resolve_media_in_markdown
from app.feishu.oauth import get_app_access_token
from app.feishu.sync import sync_feishu_doc_phase1
from app.pipeline import parser
from app.storage.pg.models import Domain, SourceDoc, SyncState
from tests.test_feishu_sync import FakeOss

pytestmark = pytest.mark.skipif(
    os.environ.get("KG_FEISHU_LIVE") != "1",
    reason="set KG_FEISHU_LIVE=1 to run against real Feishu OpenAPI",
)

# 用户在飞书知识库创建的联调文档（wiki 节点 URL）
LIVE_WIKI_FAQ = "https://my.feishu.cn/wiki/U01vwDy2UiByLMkcLsicIQGinfh"
LIVE_WIKI_SOP = "https://my.feishu.cn/wiki/E4p7wR5JqixWSWkNyuzcjwWRnjd"

# wiki 节点 URL → 联调断言阈值；docx token 一律从 wiki resolve 动态获取，勿硬编码 obj_token
EXPECTED = {
    LIVE_WIKI_FAQ: {
        "wiki_node": "U01vwDy2UiByLMkcLsicIQGinfh",
        "title_substr": "FAQ",
        "doc_type": "faq",
        "min_blocks": 30,
        "min_entries": 3,
    },
    LIVE_WIKI_SOP: {
        "wiki_node": "E4p7wR5JqixWSWkNyuzcjwWRnjd",
        "title_substr": "SOP",
        "doc_type": "sop",
        "min_blocks": 50,
        "min_entries": 3,
    },
}


async def _wiki_obj_token(client: FeishuClient, wiki_url: str) -> str:
    parsed = parse_feishu_url(wiki_url)
    node = await client.get_wiki_node(parsed.document_token)
    node_data = node.get("node") or node
    obj_token = node_data.get("obj_token") or node_data.get("object_token")
    assert obj_token, f"wiki 节点缺少 obj_token: {wiki_url}"
    return obj_token


@pytest.fixture(scope="module")
def feishu_client() -> FeishuClient:
    settings = get_settings()
    if not settings.lark_app_id or not settings.lark_app_secret:
        pytest.skip("KG_LARK_APP_ID / KG_LARK_APP_SECRET 未配置")
    return FeishuClient()


class TestFeishuOAuthLive:
    async def test_app_access_token(self):
        token = await get_app_access_token()
        assert isinstance(token, str)
        assert len(token) >= 20


class TestFeishuWikiLive:
    @pytest.mark.parametrize("wiki_url", [LIVE_WIKI_FAQ, LIVE_WIKI_SOP])
    async def test_wiki_node_obj_type_is_string_docx(self, feishu_client: FeishuClient, wiki_url: str):
        """真实 API 返回 obj_type='docx'（字符串），非 mock 里的整型 22。"""
        parsed = parse_feishu_url(wiki_url)
        node = await feishu_client.get_wiki_node(parsed.document_token)
        node_data = node.get("node") or node
        assert node_data.get("obj_type") == "docx"
        assert node_data.get("obj_token")

    @pytest.mark.parametrize("wiki_url", [LIVE_WIKI_FAQ, LIVE_WIKI_SOP])
    async def test_resolve_with_permission(self, feishu_client: FeishuClient, wiki_url: str):
        expected = EXPECTED[wiki_url]
        wiki_obj_token = await _wiki_obj_token(feishu_client, wiki_url)
        resolved, perm = await resolve_with_permission(feishu_client, wiki_url)
        assert perm.ok is True, (perm.error_code, perm.error_message, perm.action_guide)
        assert resolved.obj_type == "docx"
        assert resolved.document_token == wiki_obj_token
        assert expected["title_substr"] in (resolved.title or "")

    @pytest.mark.parametrize("wiki_url", [LIVE_WIKI_FAQ, LIVE_WIKI_SOP])
    async def test_document_blocks_flat_under_root(self, feishu_client: FeishuClient, wiki_url: str):
        """真实 Block 树：绝大多数块 parent 指向 page，children 为空；与 mock 嵌套树不同。"""
        expected = EXPECTED[wiki_url]
        docx_token = await _wiki_obj_token(feishu_client, wiki_url)
        blocks = await feishu_client.get_document_blocks(docx_token)
        assert len(blocks) >= expected["min_blocks"]
        root = next(b for b in blocks if b.get("block_type") == 1)
        assert len(root.get("children") or []) >= expected["min_blocks"] - 1
        non_root_with_children = [
            b for b in blocks if b.get("block_type") != 1 and b.get("children")
        ]
        assert len(non_root_with_children) <= 2

    @pytest.mark.parametrize("wiki_url", [LIVE_WIKI_FAQ, LIVE_WIKI_SOP])
    async def test_markdown_render_real_blocks(self, feishu_client: FeishuClient, wiki_url: str):
        expected = EXPECTED[wiki_url]
        resolved = await resolve_doc(feishu_client, wiki_url)
        blocks = await feishu_client.get_document_blocks(resolved.document_token)
        rendered = blocks_to_markdown(blocks)
        assert len(rendered.markdown) > 200
        assert "auto." not in rendered.markdown
        assert "> [!NOTE]" in rendered.markdown  # block_type=19 callout
        assert rendered.block_map  # 至少一条 H1 映射

        md = await resolve_media_in_markdown(
            rendered.markdown,
            rendered.pending_media,
            client=feishu_client,
            oss=FakeOss(),
            feishu_doc_token=resolved.document_token,
        )
        entries = parser.split_entries(md)
        assert len(entries) >= expected["min_entries"]


class TestFeishuSyncPhase1Live:
    async def test_phase1_pipeline_against_faq_doc(self, db_session, feishu_client: FeishuClient):
        """端到端 phase1：真实拉取 → 渲染 → 解析；模板段名与平台不一致时会 blocking，但 API 链路应跑通。"""
        expected = EXPECTED[LIVE_WIKI_FAQ]
        wiki_obj_token = await _wiki_obj_token(feishu_client, LIVE_WIKI_FAQ)
        db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="live-test"))
        doc = SourceDoc(
            name="飞书 FAQ 联调",
            domain_code="free-order",
            type="faq",
            source="feishu",
            source_url=LIVE_WIKI_FAQ,
            feishu_url=LIVE_WIKI_FAQ,
            # 故意写入错误 token，验证 phase1 以 feishu_url resolve 为准
            feishu_doc_token="stale-or-unrelated-docx-token",
            feishu_doc_type="docx",
            sync_status="pending",
            created_by="live-test",
        )
        db_session.add(doc)
        await db_session.flush()
        sync = SyncState(
            source_doc_id=doc.id,
            domain_code="free-order",
            feishu_doc_token="stale-or-unrelated-docx-token",
            feishu_doc_type="docx",
            feishu_url=LIVE_WIKI_FAQ,
            registered_by="live-test",
        )
        db_session.add(sync)
        await db_session.commit()

        phase1 = await sync_feishu_doc_phase1(
            db_session,
            doc.id,
            client=feishu_client,
            oss=FakeOss(),
            triggered_by="manual",
            actor_user_id="live-test",
        )
        await db_session.commit()

        await db_session.refresh(doc)
        await db_session.refresh(sync)
        assert doc.feishu_doc_token == wiki_obj_token
        assert sync.feishu_doc_token == wiki_obj_token

        assert phase1.total_blocks >= expected["min_blocks"]
        assert phase1.parsed_items >= expected["min_entries"]
        assert phase1.markdown
        assert "auto." not in phase1.markdown
        # 测试文档段名（答案/适用范围）与平台模板（标准答案/适用条件）不一致，预期有 blocking
        assert phase1.blocking_count >= 1
