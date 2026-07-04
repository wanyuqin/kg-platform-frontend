"""发布事务集成测试（技术设计文档 8.4，跑在本地 PG kg_test 库上）。

OpenViking 用 MockTransport 模拟，断言写入调用与失败路径。
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.pipeline.publish import DuplicateContent, PublishInput, publish, save_draft
from app.storage.pg.models import Domain, Knowledge, KnowledgeVersion
from tests.conftest import RecordingViking

FAQ_SECTIONS = {
    "标准问法": "企业版发票如何申请？",
    "相似问法": "- 怎么开发票？\n- 发票在哪里申请？",
    "标准答案": "登录管理后台申请。",
    "适用条件": "企业版付费客户",
}


def make_input(**overrides) -> PublishInput:
    fields = dict(
        domain_code="free-order",
        type_="faq",
        title="企业版发票如何申请？",
        sections=dict(FAQ_SECTIONS),
        tags=["发票"],
        owner_user_id="ou_owner",
        source_type="manual",
        source_ref="form:test",
        source_url=None,
        effective_date=date(2026, 7, 1),
        expire_date=None,
        actor_user_id="ou_actor",
    )
    fields.update(overrides)
    return PublishInput(**fields)


@pytest.fixture
async def domain(db_session):
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="test"))
    await db_session.commit()
    return "free-order"


async def get_knowledge(db_session, kid: str) -> Knowledge:
    return (await db_session.execute(select(Knowledge).where(Knowledge.kid == kid))).scalar_one()


class TestFirstPublish:
    async def test_creates_kid_row_and_snapshot(self, db_session, domain, seed_doc):
        viking = RecordingViking()
        result = await publish(
            db_session, viking.client, make_input(source_doc_id=seed_doc.id, doc_seq=1)
        )

        assert result.kid == "faq-fo-0001"
        row = await get_knowledge(db_session, result.kid)
        assert row.status == "published"
        assert row.index_state == "indexing"
        assert row.version == 1
        assert row.tags == ["发票"]

        snap = (
            await db_session.execute(
                select(KnowledgeVersion).where(KnowledgeVersion.kid == result.kid)
            )
        ).scalar_one()
        assert snap.version == 1
        assert "## 标准答案" in snap.content
        assert snap.meta["domain"] == "free-order"

    async def test_seq_increments_per_domain_type(self, db_session, domain, seed_doc):
        viking = RecordingViking()
        r1 = await publish(
            db_session, viking.client, make_input(source_doc_id=seed_doc.id, doc_seq=1)
        )
        r2 = await publish(
            db_session,
            viking.client,
            make_input(
                title="免单资格如何判断？",
                sections={**FAQ_SECTIONS, "标准问法": "免单资格如何判断？"},
                source_doc_id=seed_doc.id,
                doc_seq=2,
            ),
        )
        assert (r1.kid, r2.kid) == ("faq-fo-0001", "faq-fo-0002")

    async def test_writes_viking_with_frontmatter(self, db_session, domain, seed_doc):
        viking = RecordingViking()
        result = await publish(
            db_session, viking.client, make_input(source_doc_id=seed_doc.id, doc_seq=1)
        )

        assert len(viking.writes) == 1
        w = viking.writes[0]
        assert w["uri"] == f"viking://resources/free-order/faq/{result.kid}.md"
        assert w["content"].startswith("---\n")  # Frontmatter（技术 九）
        assert f"kid: {result.kid}" in w["content"]
        assert "## 标准问法" in w["content"]

    async def test_expire_date_defaults_from_domain_ttl(self, db_session, domain, seed_doc):
        viking = RecordingViking()
        result = await publish(
            db_session,
            viking.client,
            make_input(expire_date=None, source_doc_id=seed_doc.id, doc_seq=1),
        )
        row = await get_knowledge(db_session, result.kid)
        assert row.expire_date == row.effective_date + timedelta(days=365)  # domain 默认 TTL


class TestDedup:
    async def test_duplicate_content_raises_with_existing_kid(self, db_session, domain, seed_doc):
        viking = RecordingViking()
        first = await publish(
            db_session, viking.client, make_input(source_doc_id=seed_doc.id, doc_seq=1)
        )
        with pytest.raises(DuplicateContent) as exc_info:
            await publish(
                db_session,
                viking.client,
                make_input(title="换个标题但正文相同", source_doc_id=seed_doc.id, doc_seq=2),
            )
        assert exc_info.value.existing_kid == first.kid
        # 事务已回滚：库里仍只有一条
        count = (await db_session.execute(select(func.count()).select_from(Knowledge))).scalar_one()
        assert count == 1


class TestRepublish:
    async def test_update_increments_version_same_kid_same_uri(self, db_session, domain, seed_doc):
        viking = RecordingViking()
        first = await publish(
            db_session, viking.client, make_input(source_doc_id=seed_doc.id, doc_seq=1)
        )
        updated = await publish(
            db_session,
            viking.client,
            make_input(sections={**FAQ_SECTIONS, "标准答案": "改为线上自助申请。"}),
            kid=first.kid,
        )
        assert updated.kid == first.kid
        assert updated.version == 2
        row = await get_knowledge(db_session, first.kid)
        assert row.version == 2 and row.status == "published"
        snaps = (
            (
                await db_session.execute(
                    select(KnowledgeVersion).where(KnowledgeVersion.kid == first.kid)
                )
            )
            .scalars()
            .all()
        )
        assert {s.version for s in snaps} == {1, 2}
        assert viking.writes[0]["uri"] == viking.writes[1]["uri"]  # URI 永不变（ADR-0019）


class TestVikingFailure:
    async def test_failure_sets_index_state_failed_keeps_published(self, db_session, domain, seed_doc):
        viking = RecordingViking(fail=True)
        result = await publish(
            db_session, viking.client, make_input(source_doc_id=seed_doc.id, doc_seq=1)
        )
        row = await get_knowledge(db_session, result.kid)
        # 8.4：写入失败不回退 published，index_state=failed 由 scheduler 重试收敛
        assert row.status == "published"
        assert row.index_state == "failed"


class TestDraftFlow:
    async def test_save_draft_assigns_kid_with_draft_slot_only(self, db_session, domain, seed_doc):
        kid = await save_draft(db_session, make_input(source_doc_id=seed_doc.id, doc_seq=1))
        row = await get_knowledge(db_session, kid)
        assert row.status == "draft"
        assert row.index_state == "none"
        # 版本历史（version>=1）只在发布时落（技术 四）；version=0 是草稿正文槽位
        snaps = (
            (await db_session.execute(select(KnowledgeVersion).where(KnowledgeVersion.kid == kid)))
            .scalars()
            .all()
        )
        assert [s.version for s in snaps] == [0]
        assert snaps[0].meta["fields"]["标准问法"]  # 表单回填数据

    async def test_publish_draft_transitions_to_published(self, db_session, domain, seed_doc):
        viking = RecordingViking()
        kid = await save_draft(db_session, make_input(source_doc_id=seed_doc.id, doc_seq=1))
        result = await publish(
            db_session,
            viking.client,
            make_input(source_doc_id=seed_doc.id, doc_seq=1),
            kid=kid,
        )
        assert result.kid == kid
        row = await get_knowledge(db_session, kid)
        assert row.status == "published"
        assert row.version == 1
        versions = (
            (
                await db_session.execute(
                    select(KnowledgeVersion.version).where(KnowledgeVersion.kid == kid)
                )
            )
            .scalars()
            .all()
        )
        assert versions == [1]  # 草稿槽位（version=0）发布时清除
