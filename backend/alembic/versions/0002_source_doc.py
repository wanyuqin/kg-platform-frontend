"""source_doc 表 + import_batch/import_item/knowledge 加列 + 存量回填（spec §3、§7）。

回填顺序：已确认批次各生成一个文件（source='upload'，同名加序号去重）→
回挂批次与条目（doc_seq 取 item.seq）→ 其余条目归入每 (domain, type)
「手工录入-<type>」文件（source='manual'，doc_seq 按 created_at 排序；
名字带 type 避免撞 (domain_code, name) 唯一约束）。

存量回填完毕后，knowledge.source_doc_id / doc_seq 收紧为 NOT NULL——
发布链路（PublishInput，Task 3）已带文件归属，回填保证了收紧前无遗漏行
（kg_test 每次重建、dev 库尚未迁移，就地改 0002 安全）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_source_doc"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_doc",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("domain_code", sa.String(32), sa.ForeignKey("domain.code"), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("domain_code", "name"),
        sa.CheckConstraint("source IN ('manual','upload','feishu')"),
        sa.CheckConstraint("status IN ('active','archived')"),
    )
    op.add_column("import_batch", sa.Column("source_doc_id", sa.BigInteger, sa.ForeignKey("source_doc.id")))
    op.add_column("import_batch", sa.Column("origin", sa.String(16), nullable=False, server_default="upload"))
    op.add_column("import_item", sa.Column("align_action", sa.String(16), nullable=False, server_default="new"))
    op.add_column("import_item", sa.Column("match_kid", sa.String(64)))
    op.add_column("knowledge", sa.Column("source_doc_id", sa.BigInteger, sa.ForeignKey("source_doc.id")))
    op.add_column("knowledge", sa.Column("doc_seq", sa.Integer))

    # ---- 存量回填 ----
    # 1) 已确认批次 → 文件（同名批次按创建序加“(n)”后缀去重）
    op.execute(
        """
        WITH named AS (
            SELECT id, domain_code, type, file_name, created_by,
                   ROW_NUMBER() OVER (PARTITION BY domain_code, file_name ORDER BY id) AS rn
            FROM import_batch WHERE status = 'confirmed'
        ), ins AS (
            INSERT INTO source_doc (name, domain_code, type, source, created_by)
            SELECT CASE WHEN rn = 1 THEN file_name ELSE file_name || '(' || rn || ')' END,
                   domain_code, type, 'upload', created_by
            FROM named
            RETURNING id, name, domain_code
        )
        UPDATE import_batch b SET source_doc_id = ins.id
        FROM named JOIN ins
          ON ins.domain_code = named.domain_code
         AND ins.name = CASE WHEN named.rn = 1 THEN named.file_name
                             ELSE named.file_name || '(' || named.rn || ')' END
        WHERE b.id = named.id
        """
    )
    # 2) 批次条目回挂（doc_seq = item.seq）
    op.execute(
        """
        UPDATE knowledge k SET source_doc_id = b.source_doc_id, doc_seq = i.seq
        FROM import_item i JOIN import_batch b ON i.batch_id = b.id
        WHERE i.result_kid = k.kid AND b.source_doc_id IS NOT NULL
        """
    )
    # 3) 其余条目 → 每 (domain, type)「手工录入-<type>」文件
    #    名字带 type 区分并按 (domain, type) 聚合各建一行：否则同 domain 多 type、
    #    或同 type 多 owner 的游离条目都会产生多行同名，撞 (domain_code, name) 唯一约束；
    #    created_by 取代表值（min）即可，「手工录入」文件本就无单一归属人，聚合语义无损
    op.execute(
        """
        INSERT INTO source_doc (name, domain_code, type, source, created_by)
        SELECT '手工录入-' || k.type, k.domain_code, k.type, 'manual', min(k.owner_user_id)
        FROM knowledge k WHERE k.source_doc_id IS NULL
        GROUP BY k.domain_code, k.type
        """
    )
    op.execute(
        """
        WITH seqed AS (
            SELECT k.kid, d.id AS doc_id,
                   ROW_NUMBER() OVER (PARTITION BY d.id ORDER BY k.created_at) AS rn
            FROM knowledge k
            JOIN source_doc d ON d.domain_code = k.domain_code AND d.type = k.type
                             AND d.name = '手工录入-' || k.type AND d.source = 'manual'
            WHERE k.source_doc_id IS NULL
        )
        UPDATE knowledge k SET source_doc_id = seqed.doc_id, doc_seq = seqed.rn
        FROM seqed WHERE k.kid = seqed.kid
        """
    )

    # 存量回填完毕，收紧非空约束（Task 3：发布链路已带文件归属）
    op.alter_column("knowledge", "source_doc_id", nullable=False)
    op.alter_column("knowledge", "doc_seq", nullable=False)


def downgrade() -> None:
    op.drop_column("knowledge", "doc_seq")
    op.drop_column("knowledge", "source_doc_id")
    op.drop_column("import_item", "match_kid")
    op.drop_column("import_item", "align_action")
    op.drop_column("import_batch", "origin")
    op.drop_column("import_batch", "source_doc_id")
    op.drop_table("source_doc")
