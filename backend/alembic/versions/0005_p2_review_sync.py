"""P2 表：review_task（审核任务）+ sync_state（飞书同步状态），技术设计文档 3.3。

Revision ID: 0005
Revises: 0004_source_title
"""

from alembic import op

revision = "0005_p2_review_sync"
down_revision = "0004_source_title"
branch_labels = None
depends_on = None

DDL = """
-- 审核任务（设计 4.2 pending_review 副作用；控制台审核待办三 tab）
CREATE TABLE review_task (
  id              BIGSERIAL    PRIMARY KEY,
  kid             VARCHAR(64)  NOT NULL REFERENCES knowledge(kid),
  domain_code     VARCHAR(32)  NOT NULL REFERENCES domain(code),
  task_type       VARCHAR(16)  NOT NULL CHECK (task_type IN ('risk','manual_fill','conflict')),
  status          VARCHAR(16)  NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','approved','rejected','expired')),
  risk_note       VARCHAR(256),
  submitter_id    VARCHAR(64)  NOT NULL,
  reviewer_id     VARCHAR(64),
  reject_reason   VARCHAR(512),
  feishu_card_id  VARCHAR(128),
  card_sent_at    TIMESTAMPTZ,
  card_expires_at TIMESTAMPTZ,
  resolved_by     VARCHAR(64),
  resolved_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_review_task_queue
  ON review_task (domain_code, status, task_type, created_at DESC);
CREATE UNIQUE INDEX uq_review_task_pending_kid
  ON review_task (kid) WHERE status = 'pending';

-- 飞书单文档同步状态（ADR-0015：单文档注册 + 事件/轮询双通道）
CREATE TABLE sync_state (
  id                 BIGSERIAL    PRIMARY KEY,
  source_doc_id      BIGINT       NOT NULL UNIQUE REFERENCES source_doc(id),
  domain_code        VARCHAR(32)  NOT NULL REFERENCES domain(code),
  feishu_doc_token   VARCHAR(128) NOT NULL,
  feishu_doc_type    VARCHAR(16)  NOT NULL CHECK (feishu_doc_type IN ('docx','wiki','doc')),
  feishu_title       VARCHAR(256),
  feishu_url         VARCHAR(1024),
  subscription_id    VARCHAR(128),
  content_revision   VARCHAR(64),
  content_hash       CHAR(64),
  sync_status        VARCHAR(16)  NOT NULL DEFAULT 'registered'
                     CHECK (sync_status IN ('registered','syncing','idle','error','quarantine')),
  last_sync_at       TIMESTAMPTZ,
  last_event_at      TIMESTAMPTZ,
  last_poll_at       TIMESTAMPTZ,
  next_poll_at       TIMESTAMPTZ,
  last_error         VARCHAR(512),
  registered_by      VARCHAR(64)  NOT NULL,
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_sync_state_feishu_doc_token ON sync_state (feishu_doc_token);
CREATE INDEX idx_sync_state_poll
  ON sync_state (sync_status, next_poll_at)
  WHERE sync_status IN ('registered','idle','error');
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sync_state")
    op.execute("DROP TABLE IF EXISTS review_task")
