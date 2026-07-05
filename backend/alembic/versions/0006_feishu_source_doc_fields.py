"""飞书同步业务字段 + sync_state 技术字段 + MQ 幂等收据（feishu-sync §12.2 / §11.2）。

Revision ID: 0006_feishu_source_doc_fields
Revises: 0005_p2_review_sync
"""

from alembic import op

revision = "0006_feishu_source_doc_fields"
down_revision = "0005_p2_review_sync"
branch_labels = None
depends_on = None

DDL = """
-- source_doc：飞书元数据 + 业务同步状态（D4）
ALTER TABLE source_doc ADD COLUMN feishu_doc_token VARCHAR(128);
ALTER TABLE source_doc ADD COLUMN feishu_doc_type VARCHAR(16)
  CHECK (feishu_doc_type IS NULL OR feishu_doc_type IN ('docx','wiki','doc'));
ALTER TABLE source_doc ADD COLUMN feishu_url VARCHAR(1024);
ALTER TABLE source_doc ADD COLUMN sync_status VARCHAR(16) NOT NULL DEFAULT 'pending'
  CHECK (sync_status IN (
    'pending','syncing','success','failed',
    'awaiting_auth','permission_revoked','auth_timeout','archived'
  ));
ALTER TABLE source_doc ADD COLUMN last_sync_at TIMESTAMPTZ;
ALTER TABLE source_doc ADD COLUMN last_sync_error TEXT;
ALTER TABLE source_doc ADD COLUMN sync_interval_sec INTEGER
  CHECK (sync_interval_sec IS NULL OR sync_interval_sec > 0);
ALTER TABLE source_doc ADD COLUMN archived_at TIMESTAMPTZ;

CREATE INDEX idx_source_doc_feishu_poll
  ON source_doc (source, status, sync_status)
  WHERE source = 'feishu' AND status = 'active';
CREATE INDEX idx_source_doc_feishu_token
  ON source_doc (feishu_doc_token)
  WHERE feishu_doc_token IS NOT NULL;

-- 从 sync_state 回填已有飞书绑定
UPDATE source_doc sd
SET
  feishu_doc_token = ss.feishu_doc_token,
  feishu_doc_type  = ss.feishu_doc_type,
  feishu_url       = COALESCE(ss.feishu_url, sd.source_url),
  last_sync_at     = ss.last_sync_at,
  last_sync_error  = ss.last_error,
  sync_status = CASE ss.sync_status
    WHEN 'registered'  THEN 'pending'
    WHEN 'syncing'     THEN 'syncing'
    WHEN 'idle'        THEN 'success'
    WHEN 'error'       THEN 'failed'
    WHEN 'quarantine'  THEN 'failed'
    ELSE 'pending'
  END
FROM sync_state ss
WHERE ss.source_doc_id = sd.id;

UPDATE source_doc
SET archived_at = updated_at
WHERE status = 'archived' AND archived_at IS NULL;

-- sync_state：P3 预留技术字段
ALTER TABLE sync_state ADD COLUMN last_block_ids JSONB;
ALTER TABLE sync_state ADD COLUMN last_sync_started_at TIMESTAMPTZ;

-- MQ 消费幂等收据（§11.2）
CREATE TABLE feishu_sync_receipt (
  source_doc_id BIGINT NOT NULL REFERENCES source_doc(id),
  content_hash    CHAR(64) NOT NULL,
  triggered_by    VARCHAR(16) NOT NULL
                  CHECK (triggered_by IN ('event','poll','manual','bind')),
  processed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_doc_id, content_hash)
);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS feishu_sync_receipt;
ALTER TABLE sync_state DROP COLUMN IF EXISTS last_sync_started_at;
ALTER TABLE sync_state DROP COLUMN IF EXISTS last_block_ids;
DROP INDEX IF EXISTS idx_source_doc_feishu_token;
DROP INDEX IF EXISTS idx_source_doc_feishu_poll;
ALTER TABLE source_doc DROP COLUMN IF EXISTS archived_at;
ALTER TABLE source_doc DROP COLUMN IF EXISTS sync_interval_sec;
ALTER TABLE source_doc DROP COLUMN IF EXISTS last_sync_error;
ALTER TABLE source_doc DROP COLUMN IF EXISTS last_sync_at;
ALTER TABLE source_doc DROP COLUMN IF EXISTS sync_status;
ALTER TABLE source_doc DROP COLUMN IF EXISTS feishu_url;
ALTER TABLE source_doc DROP COLUMN IF EXISTS feishu_doc_type;
ALTER TABLE source_doc DROP COLUMN IF EXISTS feishu_doc_token;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(DOWNGRADE)
