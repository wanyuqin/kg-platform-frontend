"""P1 核心表初始化，DDL 原样取自技术设计文档 3.2

Revision ID: 0001
Revises:
Create Date: 2026-07-04

"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = """
-- 领域（设计 5.1：权限隔离与治理配置单位）
CREATE TABLE domain (
  code                VARCHAR(32)  PRIMARY KEY,
  short_code          VARCHAR(8)   UNIQUE NOT NULL,
  name                VARCHAR(64)  NOT NULL,
  default_ttl_days    INT          NOT NULL DEFAULT 365,
  type_topk           JSONB        NOT NULL DEFAULT '{}',
  reviewer_user_id    VARCHAR(64),
  feishu_folder_token VARCHAR(128),
  created_by          VARCHAR(64)  NOT NULL,
  created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE console_user (
  user_id           VARCHAR(64) PRIMARY KEY,
  name              VARCHAR(64) NOT NULL,
  is_platform_admin BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE domain_member (
  domain_code VARCHAR(32) NOT NULL REFERENCES domain(code),
  user_id     VARCHAR(64) NOT NULL REFERENCES console_user(user_id),
  role        VARCHAR(16) NOT NULL CHECK (role IN ('admin','member')),
  PRIMARY KEY (domain_code, user_id)
);

CREATE TABLE knowledge (
  kid            VARCHAR(64)  PRIMARY KEY,
  title          VARCHAR(256) NOT NULL,
  domain_code    VARCHAR(32)  NOT NULL REFERENCES domain(code),
  type           VARCHAR(16)  NOT NULL CHECK (type IN ('faq','sop','policy','product','case','term')),
  tags           TEXT[]       NOT NULL DEFAULT '{}',
  source_type    VARCHAR(16)  NOT NULL CHECK (source_type IN ('manual','markdown','feishu_doc','feishu_wiki')),
  source_ref     VARCHAR(512) NOT NULL,
  source_url     VARCHAR(1024),
  owner_user_id  VARCHAR(64)  NOT NULL,
  version        INT          NOT NULL DEFAULT 1,
  status         VARCHAR(16)  NOT NULL CHECK (status IN ('draft','pending_review','published','expired','archived')),
  effective_date DATE         NOT NULL,
  expire_date    DATE         NOT NULL,
  content_hash   CHAR(64)     NOT NULL,
  index_state    VARCHAR(16)  NOT NULL DEFAULT 'none' CHECK (index_state IN ('none','indexing','ready','failed')),
  risk_note      VARCHAR(256),
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_knowledge_list   ON knowledge (domain_code, type, status, updated_at DESC);
CREATE INDEX idx_knowledge_expire ON knowledge (status, expire_date);
CREATE INDEX idx_knowledge_tags   ON knowledge USING GIN (tags);
CREATE UNIQUE INDEX uq_knowledge_hash ON knowledge (content_hash) WHERE status <> 'archived';

CREATE TABLE knowledge_version (
  id           BIGSERIAL    PRIMARY KEY,
  kid          VARCHAR(64)  NOT NULL REFERENCES knowledge(kid),
  version      INT          NOT NULL,
  title        VARCHAR(256) NOT NULL,
  content      TEXT         NOT NULL,
  content_hash CHAR(64)     NOT NULL,
  meta         JSONB        NOT NULL,
  created_by   VARCHAR(64)  NOT NULL,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (kid, version)
);

CREATE TABLE kid_sequence (
  domain_code VARCHAR(32) NOT NULL,
  type        VARCHAR(16) NOT NULL,
  next_seq    INT         NOT NULL DEFAULT 1,
  PRIMARY KEY (domain_code, type)
);

CREATE TABLE api_key (
  key_id           VARCHAR(16) PRIMARY KEY,
  key_hash         CHAR(64)    NOT NULL,
  agent_name       VARCHAR(64) NOT NULL,
  domain_whitelist TEXT[]      NOT NULL,
  qps_limit        INT         NOT NULL DEFAULT 10,
  status           VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  created_by       VARCHAR(64) NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at       TIMESTAMPTZ
);

CREATE TABLE import_batch (
  id          BIGSERIAL    PRIMARY KEY,
  domain_code VARCHAR(32)  NOT NULL REFERENCES domain(code),
  type        VARCHAR(16)  NOT NULL,
  file_name   VARCHAR(256) NOT NULL,
  status      VARCHAR(16)  NOT NULL DEFAULT 'previewing' CHECK (status IN ('previewing','confirmed','discarded')),
  created_by  VARCHAR(64)  NOT NULL,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE import_item (
  id         BIGSERIAL   PRIMARY KEY,
  batch_id   BIGINT      NOT NULL REFERENCES import_batch(id),
  seq        INT         NOT NULL,
  title      VARCHAR(256),
  content    TEXT        NOT NULL,
  validation JSONB       NOT NULL,
  is_valid   BOOLEAN     NOT NULL,
  result_kid VARCHAR(64),
  UNIQUE (batch_id, seq)
);

CREATE TABLE audit_log (
  id               BIGSERIAL,
  ts               TIMESTAMPTZ NOT NULL,
  key_id           VARCHAR(16) NOT NULL,
  action           VARCHAR(8)  NOT NULL CHECK (action IN ('search','read')),
  query            TEXT,
  filter_type      TEXT[],
  filter_tag      TEXT[],
  hits             JSONB,
  excluded_expired INT,
  kid              VARCHAR(64),
  version          INT,
  latency_ms       INT         NOT NULL,
  PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

-- 首两个月分区；此后由 scheduler 每月 25 日预建（技术设计文档 十一）
CREATE TABLE audit_log_2026_07 PARTITION OF audit_log
  FOR VALUES FROM ('2026-07-01+08') TO ('2026-08-01+08');
CREATE TABLE audit_log_2026_08 PARTITION OF audit_log
  FOR VALUES FROM ('2026-08-01+08') TO ('2026-09-01+08');

-- common 通用域（设计 5.1：所有 Agent 白名单默认包含；short_code 为空串，kid 退化两段式）
INSERT INTO domain (code, short_code, name, created_by)
VALUES ('common', '', '通用域', 'system');
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS audit_log CASCADE;
        DROP TABLE IF EXISTS import_item, import_batch, api_key, kid_sequence,
          knowledge_version, knowledge, domain_member, console_user, domain CASCADE;
        """
    )
