"""P1 核心表 ORM 模型，与技术设计文档 3.2 的 DDL 对应。

DDL 的唯一事实来源是 alembic 迁移脚本（原样使用文档 SQL）；
本文件仅供查询使用，字段保持一致。
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CHAR, JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


_now = text("now()")


class Domain(Base):
    __tablename__ = "domain"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    short_code: Mapped[str] = mapped_column(String(8), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    default_ttl_days: Mapped[int] = mapped_column(Integer, server_default=text("365"))
    type_topk: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    reviewer_user_id: Mapped[str | None] = mapped_column(String(64))  # P2
    feishu_folder_token: Mapped[str | None] = mapped_column(String(128))  # P2
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)


class ConsoleUser(Base):
    __tablename__ = "console_user"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 飞书 open_id
    name: Mapped[str] = mapped_column(String(64))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)


class DomainMember(Base):
    __tablename__ = "domain_member"

    domain_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("domain.code"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("console_user.user_id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16))
    __table_args__ = (CheckConstraint("role IN ('admin','member')"),)


class Knowledge(Base):
    __tablename__ = "knowledge"

    kid: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    domain_code: Mapped[str] = mapped_column(String(32), ForeignKey("domain.code"))
    type: Mapped[str] = mapped_column(String(16))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    source_type: Mapped[str] = mapped_column(String(16))
    source_ref: Mapped[str] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    source_doc_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("source_doc.id"))
    doc_seq: Mapped[int] = mapped_column(Integer)
    owner_user_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(16))
    effective_date: Mapped[date] = mapped_column(Date)
    expire_date: Mapped[date] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(CHAR(64))
    index_state: Mapped[str] = mapped_column(String(16), server_default=text("'none'"))
    risk_note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kid: Mapped[str] = mapped_column(String(64), ForeignKey("knowledge.kid"))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(CHAR(64))
    meta: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    __table_args__ = (UniqueConstraint("kid", "version"),)


class KidSequence(Base):
    __tablename__ = "kid_sequence"

    domain_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), primary_key=True)
    next_seq: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class ApiKey(Base):
    __tablename__ = "api_key"

    key_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    key_hash: Mapped[str] = mapped_column(CHAR(64))
    agent_name: Mapped[str] = mapped_column(String(64))
    domain_whitelist: Mapped[list[str]] = mapped_column(ARRAY(Text))
    qps_limit: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class SourceDoc(Base):
    """知识文件（spec §3.1）：管理容器，条目仍是生命周期原子。"""

    __tablename__ = "source_doc"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    domain_code: Mapped[str] = mapped_column(String(32), ForeignKey("domain.code"))
    type: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    source_title: Mapped[str | None] = mapped_column(String(256))  # 原文标题；飞书=P2 文档名
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    feishu_doc_token: Mapped[str | None] = mapped_column(String(128))
    feishu_doc_type: Mapped[str | None] = mapped_column(String(16))
    feishu_url: Mapped[str | None] = mapped_column(String(1024))
    sync_status: Mapped[str] = mapped_column(String(32), server_default=text("'pending'"))
    last_sync_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    last_sync_error_detail: Mapped[dict | None] = mapped_column(JSONB)
    sync_interval_sec: Mapped[int | None] = mapped_column(Integer)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    awaiting_auth_since: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    __table_args__ = (
        UniqueConstraint("domain_code", "name"),
        CheckConstraint("source IN ('manual','upload','feishu')"),
        CheckConstraint("status IN ('active','archived')"),
        CheckConstraint("feishu_doc_type IS NULL OR feishu_doc_type IN ('docx','wiki','doc')"),
        CheckConstraint(
            "sync_status IN ('pending','syncing','success','failed',"
            "'awaiting_auth','permission_revoked','auth_timeout','archived')"
        ),
        CheckConstraint("sync_interval_sec IS NULL OR sync_interval_sec > 0"),
    )


class ImportBatch(Base):
    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain_code: Mapped[str] = mapped_column(String(32), ForeignKey("domain.code"))
    type: Mapped[str] = mapped_column(String(16))
    file_name: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'previewing'"))
    source_doc_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_doc.id"))
    origin: Mapped[str] = mapped_column(String(16), server_default=text("'upload'"))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    source_title: Mapped[str | None] = mapped_column(String(256))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)


class ImportItem(Base):
    __tablename__ = "import_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_batch.id"))
    seq: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    validation: Mapped[list] = mapped_column(JSONB)
    is_valid: Mapped[bool] = mapped_column(Boolean)
    result_kid: Mapped[str | None] = mapped_column(String(64))
    align_action: Mapped[str] = mapped_column(String(16), server_default=text("'new'"))
    match_kid: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("batch_id", "seq"),)


class AuditLog(Base):
    """按月 RANGE 分区（分区管理在迁移与 scheduler 中，ORM 仅作查询）。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    key_id: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(8))
    query: Mapped[str | None] = mapped_column(Text)
    filter_type: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    filter_tag: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    hits: Mapped[list | None] = mapped_column(JSONB)
    excluded_expired: Mapped[int | None] = mapped_column(Integer)
    kid: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    __table_args__ = {"postgresql_partition_by": "RANGE (ts)"}


class ReviewTask(Base):
    """P2 审核任务（技术 3.3）；pending_review 副作用，控制台审核待办队列。"""

    __tablename__ = "review_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kid: Mapped[str] = mapped_column(String(64), ForeignKey("knowledge.kid"))
    domain_code: Mapped[str] = mapped_column(String(32), ForeignKey("domain.code"))
    task_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    risk_note: Mapped[str | None] = mapped_column(String(256))
    submitter_id: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[str | None] = mapped_column(String(64))
    reject_reason: Mapped[str | None] = mapped_column(String(512))
    feishu_card_id: Mapped[str | None] = mapped_column(String(128))
    card_sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    card_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    __table_args__ = (
        CheckConstraint("task_type IN ('risk','manual_fill','conflict')"),
        CheckConstraint("status IN ('pending','approved','rejected','expired')"),
    )


class SyncState(Base):
    """P2 飞书文档同步状态（技术 3.3，ADR-0015）。"""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_doc_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("source_doc.id"), unique=True)
    domain_code: Mapped[str] = mapped_column(String(32), ForeignKey("domain.code"))
    feishu_doc_token: Mapped[str] = mapped_column(String(128))
    feishu_doc_type: Mapped[str] = mapped_column(String(16))
    feishu_title: Mapped[str | None] = mapped_column(String(256))
    feishu_url: Mapped[str | None] = mapped_column(String(1024))
    subscription_id: Mapped[str | None] = mapped_column(String(128))
    content_revision: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(CHAR(64))
    sync_status: Mapped[str] = mapped_column(String(16), server_default=text("'registered'"))
    last_sync_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_poll_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_auth_check_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(512))
    last_error_detail: Mapped[dict | None] = mapped_column(JSONB)
    last_block_ids: Mapped[list | None] = mapped_column(JSONB)
    last_sync_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    registered_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    __table_args__ = (
        CheckConstraint("feishu_doc_type IN ('docx','wiki','doc')"),
        CheckConstraint("sync_status IN ('registered','syncing','idle','error','quarantine','archived')"),
    )


class VikingCleanupFailed(Base):
    """OpenViking 删除失败待重试（feishu archive purge / disappeared）。"""

    __tablename__ = "viking_cleanup_failed"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uri: Mapped[str] = mapped_column(String(512), unique=True)
    last_error: Mapped[str] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_retry_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)


class FeishuSyncReceipt(Base):
    """飞书 MQ 消费幂等收据（feishu-sync §11.2）。"""

    __tablename__ = "feishu_sync_receipt"

    source_doc_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("source_doc.id"), primary_key=True)
    content_hash: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    triggered_by: Mapped[str] = mapped_column(String(16))
    processed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    __table_args__ = (
        CheckConstraint("triggered_by IN ('event','poll','manual','bind')"),
    )
