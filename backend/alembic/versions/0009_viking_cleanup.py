"""viking_cleanup_failed 表 + sync_state.archived 状态。"""

from alembic import op

revision = "0009_viking_cleanup"
down_revision = "0008_widen_sync_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sync_state DROP CONSTRAINT IF EXISTS sync_state_sync_status_check;
        ALTER TABLE sync_state ADD CONSTRAINT sync_state_sync_status_check
          CHECK (sync_status IN ('registered','syncing','idle','error','quarantine','archived'));
        """
    )
    op.execute(
        """
        CREATE TABLE viking_cleanup_failed (
          id BIGSERIAL PRIMARY KEY,
          uri VARCHAR(512) NOT NULL UNIQUE,
          last_error TEXT NOT NULL,
          retry_count INTEGER NOT NULL DEFAULT 0,
          next_retry_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS viking_cleanup_failed;")
    op.execute(
        """
        ALTER TABLE sync_state DROP CONSTRAINT IF EXISTS sync_state_sync_status_check;
        ALTER TABLE sync_state ADD CONSTRAINT sync_state_sync_status_check
          CHECK (sync_status IN ('registered','syncing','idle','error','quarantine'));
        """
    )
