"""source_doc 表与三表新列的约束行为（spec §3）。

knowledge.source_doc_id / doc_seq 的非空约束测试推迟到 Task 3
（发布链路带文件归属后连同约束收紧一起补回）。
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.storage.pg.models import Domain, SourceDoc


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
