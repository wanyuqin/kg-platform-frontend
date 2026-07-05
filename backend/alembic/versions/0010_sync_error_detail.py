"""sync_state / source_doc 同步错误明细 JSON。"""

from alembic import op

revision = "0010_sync_error_detail"
down_revision = "0009_viking_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS last_error_detail JSONB")
    op.execute("ALTER TABLE source_doc ADD COLUMN IF NOT EXISTS last_sync_error_detail JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE sync_state DROP COLUMN IF EXISTS last_error_detail")
    op.execute("ALTER TABLE source_doc DROP COLUMN IF EXISTS last_sync_error_detail")
