"""source_doc 表与三表新列的约束行为（spec §3）。

knowledge.source_doc_id / doc_seq 的非空约束已随 Task 3（发布链路带文件
归属）收紧，见 TestKnowledgeDocColumns。
"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.storage.pg.models import Domain, Knowledge, SourceDoc


@pytest.fixture
async def domain(db_session):
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    await db_session.commit()


def make_doc(**overrides) -> SourceDoc:
    kw = dict(
        name="免单FAQ", domain_code="free-order", type="faq",
        source="upload", created_by="ou_dev",
    )
    kw.update(overrides)
    return SourceDoc(**kw)


class TestSourceDoc:
    async def test_insert_defaults(self, db_session, domain):
        doc = make_doc()
        db_session.add(doc)
        await db_session.commit()
        assert doc.id is not None
        assert doc.status == "active"

    async def test_name_unique_per_domain(self, db_session, domain):
        db_session.add(make_doc())
        await db_session.commit()
        db_session.add(make_doc())
        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_source_check_constraint(self, db_session, domain):
        db_session.add(make_doc(source="paste"))  # 非法枚举
        with pytest.raises(IntegrityError):
            await db_session.commit()


class TestKnowledgeDocColumns:
    async def test_knowledge_requires_source_doc(self, db_session, domain):
        """knowledge.source_doc_id/doc_seq 非空约束生效。"""
        db_session.add(
            Knowledge(
                kid="faq-fo-9001", title="t", domain_code="free-order", type="faq",
                source_type="manual", source_ref="form:x", owner_user_id="ou_dev",
                status="published", effective_date=date(2026, 7, 1),
                expire_date=date(2027, 7, 1), content_hash="0" * 64,
                # 故意不给 source_doc_id / doc_seq
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()
