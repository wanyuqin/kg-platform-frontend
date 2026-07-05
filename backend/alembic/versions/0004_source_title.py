"""source_doc / import_batch 增加 source_title（原文/所属文章标题）。

- manual/upload：导入时 frontmatter title 或 doc_name；存量回填为 name。
- feishu：P2 飞书注册/同步时写入飞书文档标题。
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_source_title"
down_revision = "0003_source_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_batch", sa.Column("source_title", sa.String(256)))
    op.add_column("source_doc", sa.Column("source_title", sa.String(256)))
    op.execute("UPDATE source_doc SET source_title = name WHERE source_title IS NULL")


def downgrade() -> None:
    op.drop_column("source_doc", "source_title")
    op.drop_column("import_batch", "source_title")
