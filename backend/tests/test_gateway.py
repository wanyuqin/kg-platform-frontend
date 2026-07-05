"""Gateway 两接口的全链路集成测试（技术设计文档 6.2 / 6.3）。

真 PG（kg_test）+ 真 Redis + ASGI 全链路；OpenViking 用 FakeViking 依赖覆盖。
数据经真实 publish() 灌入，read/search 走完整校验链。
"""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.audit import writer
from app.gateway.auth import hash_key, new_key_material
from app.main import create_app
from app.pipeline.publish import PublishInput, publish
from app.storage.pg.models import ApiKey, Domain, Knowledge, SourceDoc
from app.storage.pg.session import get_session
from app.storage.viking.client import VikingError, get_viking
from tests.conftest import RecordingViking

TODAY = date(2026, 7, 4)


class FakeViking:
    """替代 Gateway 依赖的检索桩：返回既定命中，记录调用参数。"""

    def __init__(self):
        self.results: list[dict] = []
        self.calls: list[dict] = []
        self.error: Exception | None = None

    async def search(self, query: str, dir_prefixes: list[str], limit: int) -> list[dict]:
        self.calls.append({"query": query, "prefixes": dir_prefixes, "limit": limit})
        if self.error:
            raise self.error
        return self.results


def hit(kid: str, domain: str = "free-order", type_: str = "faq", score: float = 0.9) -> dict:
    return {
        "path": f"viking://resources/{domain}/{type_}/{kid}.md",
        "score": score,
        "summary": f"{kid} 的 L0 摘要",
    }


def make_input(title: str, answer: str, type_: str = "faq", **overrides) -> PublishInput:
    sections = {
        "标准问法": title,
        "相似问法": "- 问法甲\n- 问法乙",
        "标准答案": answer,
        "适用条件": "无限制",
    }
    if type_ == "policy":
        sections = {
            "一句话摘要": answer,
            "适用范围": "全部订单",
            "规则条款": answer,
            "例外条款": "无",
            "生效 / 失效时间": "2026-01-01 起长期有效",
        }
    fields = dict(
        domain_code="free-order",
        type_=type_,
        title=title,
        sections=sections,
        tags=["发票"],
        owner_user_id="ou_owner",
        source_type="manual",
        source_ref="form:test",
        source_url="https://example.com/src",
        effective_date=date(2026, 7, 1),
        expire_date=None,
        actor_user_id="ou_actor",
    )
    fields.update(overrides)
    return PublishInput(**fields)


@pytest.fixture
async def seeded(db_session):
    """两个域 + 三条知识：正常 faq、已过期 faq、他域 policy。"""
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    db_session.add(Domain(code="other", short_code="ot", name="他域", created_by="t"))
    await db_session.commit()
    doc_fo = SourceDoc(name="免单FAQ文件", domain_code="free-order", type="faq",
                       source="manual", created_by="t")
    doc_other = SourceDoc(name="他域政策文件", domain_code="other", type="policy",
                          source="manual", created_by="t")
    db_session.add_all([doc_fo, doc_other])
    await db_session.flush()

    rv = RecordingViking()
    ok = await publish(
        db_session,
        rv.client,
        make_input("企业版发票如何申请？", "后台申请。", source_doc_id=doc_fo.id, doc_seq=1),
    )
    expired = await publish(
        db_session,
        rv.client,
        make_input("旧活动规则？", "已结束。", source_doc_id=doc_fo.id, doc_seq=2),
    )
    row = (
        await db_session.execute(select(Knowledge).where(Knowledge.kid == expired.kid))
    ).scalar_one()
    row.expire_date = TODAY - timedelta(days=1)
    foreign = await publish(
        db_session,
        rv.client,
        make_input(
            "他域政策", "他域内容。", type_="policy", domain_code="other",
            source_doc_id=doc_other.id, doc_seq=1,
        ),
    )
    await db_session.commit()
    return {"ok": ok.kid, "expired": expired.kid, "foreign": foreign.kid, "doc_fo_id": doc_fo.id}


@pytest.fixture
async def api_key(db_session):
    key_id, plaintext = new_key_material()
    db_session.add(
        ApiKey(
            key_id=key_id,
            key_hash=hash_key(plaintext),
            agent_name="gw-test",
            domain_whitelist=["free-order"],
            qps_limit=1000,
            created_by="test",
        )
    )
    await db_session.commit()
    return key_id, plaintext


@pytest.fixture
def fake_viking():
    return FakeViking()


@pytest.fixture
async def client(db_session, fake_viking):
    app = create_app()

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_viking] = lambda: fake_viking
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def auth(api_key) -> dict:
    return {"Authorization": f"Bearer {api_key[1]}"}


def drain_audit() -> list[dict]:
    records = []
    while not writer._queue.empty():
        records.append(writer._queue.get_nowait())
    return records


@pytest.fixture
async def multi_domain_key(db_session):
    key_id, plaintext = new_key_material()
    db_session.add(
        ApiKey(
            key_id=key_id,
            key_hash=hash_key(plaintext),
            agent_name="multi-domain-agent",
            domain_whitelist=["free-order", "other"],
            qps_limit=1000,
            created_by="test",
        )
    )
    await db_session.commit()
    return key_id, plaintext


class TestRead:
    async def test_200_returns_snapshot_and_meta(self, client, seeded, api_key):
        resp = await client.get(f"/v1/knowledge/{seeded['ok']}", headers=auth(api_key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["kid"] == seeded["ok"]
        assert body["title"] == "企业版发票如何申请？"
        assert "## 标准答案" in body["content"]  # 来自 knowledge_version 快照（ADR-0018）
        assert body["source_url"] == "https://example.com/src"
        assert body["source"] == "manual"
        assert body["source_title"] == "免单FAQ文件"
        assert body["source_doc"] == {
            "id": seeded["doc_fo_id"],
            "name": "免单FAQ文件",
            "source": "manual",
            "title": "免单FAQ文件",
        }
        assert body["domain"] == "free-order" and body["type"] == "faq"
        assert body["version"] == 1

    async def test_source_url_falls_back_to_source_doc(self, client, seeded, api_key, db_session):
        """条目 source_url 为空时，回退到所属知识文件的 source_url。"""
        from app.storage.pg.models import Knowledge, SourceDoc

        row = await db_session.get(Knowledge, seeded["ok"])
        doc = await db_session.get(SourceDoc, row.source_doc_id)
        row.source_url = None
        doc.source_url = "https://example.com/file-level"
        await db_session.commit()

        resp = await client.get(f"/v1/knowledge/{seeded['ok']}", headers=auth(api_key))
        assert resp.status_code == 200
        assert resp.json()["source_url"] == "https://example.com/file-level"

    async def test_source_title_from_feishu_doc(self, client, seeded, api_key, db_session):
        """飞书来源返回同步的文档标题（P2 写入 source_title）。"""
        from app.storage.pg.models import Knowledge, SourceDoc

        row = await db_session.get(Knowledge, seeded["ok"])
        doc = await db_session.get(SourceDoc, row.source_doc_id)
        doc.source = "feishu"
        doc.name = "doccnXYZ"
        doc.source_title = "免单活动 FAQ 手册"
        doc.source_url = "https://feishu.cn/docx/doccnXYZ"
        await db_session.commit()

        resp = await client.get(f"/v1/knowledge/{seeded['ok']}", headers=auth(api_key))
        body = resp.json()
        assert body["source"] == "feishu"
        assert body["source_title"] == "免单活动 FAQ 手册"
        assert body["source_doc"]["title"] == "免单活动 FAQ 手册"

    async def test_404_unknown_kid(self, client, seeded, api_key):
        resp = await client.get("/v1/knowledge/faq-fo-9999", headers=auth(api_key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    async def test_404_foreign_domain(self, client, seeded, api_key):
        # 越权统一 404 不暴露存在性（ADR-0013）
        resp = await client.get(f"/v1/knowledge/{seeded['foreign']}", headers=auth(api_key))
        assert resp.status_code == 404

    async def test_multi_domain_key_reads_foreign(self, client, seeded, multi_domain_key):
        resp = await client.get(
            f"/v1/knowledge/{seeded['foreign']}", headers=auth(multi_domain_key)
        )
        assert resp.status_code == 200
        assert resp.json()["domain"] == "other"

    async def test_single_domain_key_still_blocks_foreign(self, client, seeded, api_key):
        resp = await client.get(f"/v1/knowledge/{seeded['foreign']}", headers=auth(api_key))
        assert resp.status_code == 404

    async def test_404_expired(self, client, seeded, api_key):
        # 过期语义 P1 查询时兜底（ADR-0020）
        resp = await client.get(f"/v1/knowledge/{seeded['expired']}", headers=auth(api_key))
        assert resp.status_code == 404

    async def test_401_without_bearer(self, client, seeded):
        resp = await client.get(f"/v1/knowledge/{seeded['ok']}")
        assert resp.status_code == 401

    async def test_401_unknown_key(self, client, seeded):
        # 参数合法后进入鉴权：不存在的 key 统一 401（技术 十）
        resp = await client.get(
            f"/v1/knowledge/{seeded['ok']}",
            headers={"Authorization": "Bearer kp_zzzzzzzz_not-a-real-secret"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"

    async def test_bad_params_return_400_even_with_bad_key(self, client, seeded):
        # 契约：body 校验先于鉴权，坏参数 + 坏 key 仍是 400（技术 6.1）
        resp = await client.post(
            "/v1/search",
            json={"query": ""},
            headers={"Authorization": "Bearer kp_zzzzzzzz_not-a-real-secret"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_argument"

    async def test_writes_read_audit(self, client, seeded, api_key):
        drain_audit()
        await client.get(f"/v1/knowledge/{seeded['ok']}", headers=auth(api_key))
        records = drain_audit()
        assert len(records) == 1
        r = records[0]
        assert r["action"] == "read"
        assert r["kid"] == seeded["ok"]
        assert r["version"] == 1
        assert r["key_id"] == api_key[0]


class TestSearch:
    async def test_maps_hits_with_pg_fields(self, client, seeded, api_key, fake_viking):
        fake_viking.results = [hit(seeded["ok"], score=0.9)]
        resp = await client.post("/v1/search", json={"query": "发票"}, headers=auth(api_key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["excluded_expired"] == 0
        assert len(body["results"]) == 1
        r = body["results"][0]
        assert r["kid"] == seeded["ok"]
        assert r["title"] == "企业版发票如何申请？"  # title 以 PG 为准（技术 6.2 第 4 步）
        assert r["summary"] == f"{seeded['ok']} 的 L0 摘要"
        assert r["uri"].endswith(f"{seeded['ok']}.md")
        assert r["domain"] == "free-order" and r["type"] == "faq"

    async def test_search_prefixes_cover_whitelist_and_common(
        self, client, seeded, api_key, fake_viking
    ):
        await client.post("/v1/search", json={"query": "发票"}, headers=auth(api_key))
        prefixes = fake_viking.calls[0]["prefixes"]
        assert "viking://resources/free-order" in prefixes
        assert "viking://resources/common" in prefixes  # 自动并入 common

    async def test_multi_domain_search_prefixes(self, client, seeded, multi_domain_key, fake_viking):
        await client.post("/v1/search", json={"query": "政策"}, headers=auth(multi_domain_key))
        prefixes = fake_viking.calls[0]["prefixes"]
        assert "viking://resources/free-order" in prefixes
        assert "viking://resources/other" in prefixes
        assert "viking://resources/common" in prefixes

    async def test_multi_domain_search_returns_foreign_hit(
        self, client, seeded, multi_domain_key, fake_viking
    ):
        fake_viking.results = [hit(seeded["foreign"], domain="other", type_="policy")]
        resp = await client.post(
            "/v1/search", json={"query": "政策"}, headers=auth(multi_domain_key)
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1
        assert resp.json()["results"][0]["domain"] == "other"

    async def test_candidate_limit_is_topk_x3(self, client, seeded, api_key, fake_viking):
        await client.post("/v1/search", json={"query": "发票"}, headers=auth(api_key))
        assert fake_viking.calls[0]["limit"] == 15  # 默认 top_k 5 × 3 重排余量

    async def test_top_k_param_wins(self, client, seeded, api_key, fake_viking):
        fake_viking.results = [hit(seeded["ok"], score=s / 10) for s in range(9, 1, -1)]
        resp = await client.post(
            "/v1/search", json={"query": "发票", "top_k": 2}, headers=auth(api_key)
        )
        assert fake_viking.calls[0]["limit"] == 6
        assert len(resp.json()["results"]) <= 2

    async def test_type_level_topk_config(self, client, seeded, api_key, fake_viking, db_session):
        # 类型级配置：仅请求 type 恰为单一类型时启用，取白名单 domain 的最大值（ADR-0011）
        d = (
            await db_session.execute(select(Domain).where(Domain.code == "free-order"))
        ).scalar_one()
        d.type_topk = {"faq": 8}
        await db_session.commit()
        await client.post(
            "/v1/search", json={"query": "发票", "type": ["faq"]}, headers=auth(api_key)
        )
        assert fake_viking.calls[0]["limit"] == 24  # 8 × 3

    async def test_excludes_expired_and_counts(self, client, seeded, api_key, fake_viking):
        fake_viking.results = [hit(seeded["ok"], score=0.9), hit(seeded["expired"], score=0.8)]
        resp = await client.post("/v1/search", json={"query": "规则"}, headers=auth(api_key))
        body = resp.json()
        assert [r["kid"] for r in body["results"]] == [seeded["ok"]]
        assert body["excluded_expired"] == 1

    async def test_filters_foreign_domain_hits(self, client, seeded, api_key, fake_viking):
        # 防御性过滤：即使 viking 返回了白名单外命中，PG 回查层也要拦（PG 是唯一事实来源）
        fake_viking.results = [hit(seeded["foreign"], domain="other", type_="policy")]
        resp = await client.post("/v1/search", json={"query": "政策"}, headers=auth(api_key))
        assert resp.json()["results"] == []

    async def test_filters_by_tag(self, client, seeded, api_key, fake_viking):
        fake_viking.results = [hit(seeded["ok"])]
        resp = await client.post(
            "/v1/search", json={"query": "发票", "tag": ["不存在的标签"]}, headers=auth(api_key)
        )
        assert resp.json()["results"] == []
        resp = await client.post(
            "/v1/search", json={"query": "发票", "tag": ["发票", "别的"]}, headers=auth(api_key)
        )
        assert len(resp.json()["results"]) == 1  # 多值 OR

    async def test_empty_results_still_200(self, client, seeded, api_key, fake_viking):
        resp = await client.post("/v1/search", json={"query": "无命中"}, headers=auth(api_key))
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    async def test_503_on_viking_error(self, client, seeded, api_key, fake_viking):
        fake_viking.error = VikingError("down")
        resp = await client.post("/v1/search", json={"query": "发票"}, headers=auth(api_key))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "upstream_unavailable"

    async def test_429_when_over_qps(self, client, seeded, db_session):
        key_id, plaintext = new_key_material()
        db_session.add(
            ApiKey(
                key_id=key_id,
                key_hash=hash_key(plaintext),
                agent_name="throttled",
                domain_whitelist=["free-order"],
                qps_limit=0,
                created_by="test",
            )
        )
        await db_session.commit()
        resp = await client.post(
            "/v1/search", json={"query": "发票"}, headers={"Authorization": f"Bearer {plaintext}"}
        )
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "1"

    async def test_writes_search_audit(self, client, seeded, api_key, fake_viking):
        fake_viking.results = [hit(seeded["ok"], score=0.9), hit(seeded["expired"], score=0.8)]
        drain_audit()
        await client.post(
            "/v1/search", json={"query": "发票", "type": ["faq"]}, headers=auth(api_key)
        )
        records = drain_audit()
        assert len(records) == 1
        r = records[0]
        assert r["action"] == "search"
        assert r["query"] == "发票"
        assert r["filter_type"] == ["faq"]
        assert r["hits"] == [{"kid": seeded["ok"], "version": 1, "score": 0.9}]
        assert r["excluded_expired"] == 1
