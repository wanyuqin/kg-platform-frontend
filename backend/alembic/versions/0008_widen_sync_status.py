"""放宽 source_doc.sync_status 长度（permission_revoked 等 >16 字符）。"""

from alembic import op

revision = "0008_widen_sync_status"
down_revision = "0007_feishu_auth_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE source_doc ALTER COLUMN sync_status TYPE VARCHAR(32)")


def downgrade() -> None:
    op.execute("ALTER TABLE source_doc ALTER COLUMN sync_status TYPE VARCHAR(16)")
