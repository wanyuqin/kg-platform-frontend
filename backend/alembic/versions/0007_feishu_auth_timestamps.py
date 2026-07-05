"""0007：授权轮询时间戳（feishu-sync §4.5）。"""

from alembic import op

revision = "0007_feishu_auth_timestamps"
down_revision = "0006_feishu_source_doc_fields"
branch_labels = None
depends_on = None

DDL = """
ALTER TABLE source_doc ADD COLUMN awaiting_auth_since TIMESTAMPTZ;
ALTER TABLE sync_state ADD COLUMN last_auth_check_at TIMESTAMPTZ;
"""

DOWNGRADE = """
ALTER TABLE sync_state DROP COLUMN IF EXISTS last_auth_check_at;
ALTER TABLE source_doc DROP COLUMN IF EXISTS awaiting_auth_since;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(DOWNGRADE)
