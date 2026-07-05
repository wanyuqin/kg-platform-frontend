"""import_batch / source_doc 增加 source_url（文件级原文链接，条目发布时继承）。

存量不回填：已有条目保持 NULL，新导入带 frontmatter source_url 或更新后生效。
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_source_url"
down_revision = "0002_source_doc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_batch", sa.Column("source_url", sa.String(1024)))
    op.add_column("source_doc", sa.Column("source_url", sa.String(1024)))


def downgrade() -> None:
    op.drop_column("source_doc", "source_url")
    op.drop_column("import_batch", "source_url")
