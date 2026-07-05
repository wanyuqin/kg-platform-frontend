"""P2 DDL：review_task 与 sync_state 表存在且约束正确。"""

from sqlalchemy import text


async def test_p2_tables_exist(db_session):
    for table in ("review_task", "sync_state"):
        row = (
            await db_session.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            )
        ).scalar_one_or_none()
        assert row == 1, f"missing table {table}"

    pending_idx = (
        await db_session.execute(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE indexname = 'uq_review_task_pending_kid'"
            )
        )
    ).scalar_one_or_none()
    assert pending_idx == 1
