# 知识文件（source_doc）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地知识文件（source_doc）实体：条目归属文件、粘贴/在线编辑入口、重拆对齐更新、文件级生命周期操作与存量迁移（spec：`docs/superpowers/specs/2026-07-04-source-doc-design.md`）。

**Architecture:** 新增 `source_doc` 表作为管理容器；`import_batch` 挂 `source_doc_id` 成为"某文件的一次解析记录"；`knowledge` 挂 `source_doc_id + doc_seq`（非空）。对齐引擎是纯函数（`pipeline/align.py`），confirm 按 `align_action` 分派到既有发布/下架事务。全文 = 条目当前版本快照按 doc_seq 拼合，不另存正文。

**Tech Stack:** FastAPI + SQLAlchemy(async) + alembic + PostgreSQL；React 18 + antd 5 + vite；pytest + pytest-asyncio（PG 集成测试基建见 `backend/tests/conftest.py`）。

## Global Constraints

- 来源枚举英文：`manual` / `upload` / `feishu`（CHECK 约束，禁止其他值）。
- 条目状态机、kid、URI、Gateway 检索侧**零改动**；"下架" = `Status.ARCHIVED`（`Event.ARCHIVE`）。
- 所有 repo 内文档、代码注释一律中文。
- 测试基线 185 全绿，每个任务结束必须全量 `pytest` 通过再 commit。
- 后端工作目录：`kg-platform/backend`（pytest、alembic 均在此执行）；前端：`kg-platform/frontend`。
- 权限口径沿用：域成员可读，编辑=owner/域管理员/平台管理员；越权 404（`errors.not_found`）/403（`errors.forbidden`），冲突 409（`errors.conflict`）。
- 前端无单测（P1 口径），前端任务以 `npm run build` 通过 + 最后浏览器验收为准。

---

### Task 1: 数据模型与迁移（source_doc 表 + 三表加列 + 存量回填）

**Files:**
- Create: `backend/alembic/versions/0002_source_doc.py`
- Modify: `backend/app/storage/pg/models.py`（在 `ImportBatch` 之前插入 `SourceDoc`，并改 `ImportBatch`/`ImportItem`/`Knowledge`）
- Test: `backend/tests/test_source_doc_models.py`

**Interfaces:**
- Produces: ORM 类 `SourceDoc(id, name, domain_code, type, source, status, created_by, created_at, updated_at)`；`ImportBatch.source_doc_id: int | None`；`ImportItem.align_action: str`（默认 `'new'`）、`ImportItem.match_kid: str | None`；`Knowledge.source_doc_id: int`、`Knowledge.doc_seq: int`。后续所有任务依赖这些字段名。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_source_doc_models.py
"""source_doc 表与三表新列的约束行为（spec §3）。"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.storage.pg.models import Domain, Knowledge, SourceDoc


@pytest.fixture
async def domain(db_session):
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    await db_session.commit()


def make_doc(**overrides) -> SourceDoc:
    kw = dict(
        name="免单FAQ", domain_code="free-order", type="faq",
        source="upload", created_by="ou_dev",
    )
    kw.update(overrides)
    return SourceDoc(**kw)


class TestSourceDoc:
    async def test_insert_defaults(self, db_session, domain):
        doc = make_doc()
        db_session.add(doc)
        await db_session.commit()
        assert doc.id is not None
        assert doc.status == "active"

    async def test_name_unique_per_domain(self, db_session, domain):
        db_session.add(make_doc())
        await db_session.commit()
        db_session.add(make_doc())
        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_source_check_constraint(self, db_session, domain):
        db_session.add(make_doc(source="paste"))  # 非法枚举
        with pytest.raises(IntegrityError):
            await db_session.commit()


class TestKnowledgeDocColumns:
    async def test_knowledge_requires_source_doc(self, db_session, domain):
        """knowledge.source_doc_id/doc_seq 非空约束生效。"""
        db_session.add(
            Knowledge(
                kid="faq-fo-9001", title="t", domain_code="free-order", type="faq",
                source_type="manual", source_ref="form:x", owner_user_id="ou_dev",
                status="published", effective_date=date(2026, 7, 1),
                expire_date=date(2027, 7, 1), content_hash="0" * 64,
                # 故意不给 source_doc_id / doc_seq
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd kg-platform/backend && pytest tests/test_source_doc_models.py -v`
Expected: FAIL（`ImportError: cannot import name 'SourceDoc'`）

- [ ] **Step 3: 改 ORM（models.py）**

在 `class ImportBatch` 之前加：

```python
class SourceDoc(Base):
    """知识文件（spec §3.1）：管理容器，条目仍是生命周期原子。"""

    __tablename__ = "source_doc"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    domain_code: Mapped[str] = mapped_column(String(32), ForeignKey("domain.code"))
    type: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=_now)
    __table_args__ = (
        UniqueConstraint("domain_code", "name"),
        CheckConstraint("source IN ('manual','upload','feishu')"),
        CheckConstraint("status IN ('active','archived')"),
    )
```

`ImportBatch` 加一行（`created_by` 之前）：

```python
    source_doc_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_doc.id"))
```

`ImportItem` 加两行（`result_kid` 之后）：

```python
    align_action: Mapped[str] = mapped_column(String(16), server_default=text("'new'"))
    match_kid: Mapped[str | None] = mapped_column(String(64))
```

`Knowledge` 加两行（`source_url` 之后）：

```python
    source_doc_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("source_doc.id"))
    doc_seq: Mapped[int] = mapped_column(Integer)
```

- [ ] **Step 4: 写迁移（0002）**

```python
# backend/alembic/versions/0002_source_doc.py
"""source_doc 表 + import_batch/import_item/knowledge 加列 + 存量回填（spec §3、§7）。

回填顺序：已确认批次各生成一个文件（source='upload'，同名加序号去重）→
回挂批次与条目（doc_seq 取 item.seq）→ 其余条目归入每 domain「手工录入」文件
（source='manual'，doc_seq 按 created_at 排序）→ 加非空约束。
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_source_doc"
down_revision = "0001_init_p1_schema"
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
    # 3) 其余条目 → 每 domain「手工录入」文件
    op.execute(
        """
        INSERT INTO source_doc (name, domain_code, type, source, created_by)
        SELECT DISTINCT '手工录入', k.domain_code, k.type, 'manual', k.owner_user_id
        FROM knowledge k WHERE k.source_doc_id IS NULL
        """
    )
    op.execute(
        """
        WITH seqed AS (
            SELECT k.kid, d.id AS doc_id,
                   ROW_NUMBER() OVER (PARTITION BY d.id ORDER BY k.created_at) AS rn
            FROM knowledge k
            JOIN source_doc d ON d.domain_code = k.domain_code AND d.type = k.type
                             AND d.name = '手工录入' AND d.source = 'manual'
            WHERE k.source_doc_id IS NULL
        )
        UPDATE knowledge k SET source_doc_id = seqed.doc_id, doc_seq = seqed.rn
        FROM seqed WHERE k.kid = seqed.kid
        """
    )
    op.alter_column("knowledge", "source_doc_id", nullable=False)
    op.alter_column("knowledge", "doc_seq", nullable=False)


def downgrade() -> None:
    op.drop_column("knowledge", "doc_seq")
    op.drop_column("knowledge", "source_doc_id")
    op.drop_column("import_item", "match_kid")
    op.drop_column("import_item", "align_action")
    op.drop_column("import_batch", "source_doc_id")
    op.drop_table("source_doc")
```

注意：`手工录入`文件按 (domain, type) 各建一个（同一 domain 不同类型的游离条目各归各的类型文件），名字冲突概率可忽略，若真冲突迁移会失败暴露出来人工处理。

- [ ] **Step 5: 重建测试库并跑新测试**

Run: `cd kg-platform/backend && psql postgresql://kg:kg@localhost:5433/kg -c 'DROP DATABASE IF EXISTS kg_test' && pytest tests/test_source_doc_models.py -v`
Expected: PASS（conftest 会重建 kg_test 并迁到 head）

- [ ] **Step 6: 全量测试**

Run: `pytest -q`
Expected: 既有用例会因 `knowledge.source_doc_id` 非空约束大量失败——**这是预期的**，先看清失败面，下一步统一修。

- [ ] **Step 7: 修既有测试的种子数据**

既有测试凡直接 `session.add(Knowledge(...))` 或经 `publish()/save_draft()` 落 knowledge 行的，都需要文件归属。为最小化改动，在 `tests/conftest.py` 加公共 fixture：

```python
# conftest.py 追加
@pytest_asyncio.fixture
async def seed_doc(db_session):
    """给需要直接造 knowledge 行的用例一个默认知识文件。"""
    from app.storage.pg.models import SourceDoc

    doc = SourceDoc(
        name="测试文件", domain_code="free-order", type="faq",
        source="manual", created_by="t",
    )
    db_session.add(doc)
    await db_session.flush()
    return doc
```

逐个修复失败用例：直接 `Knowledge(...)` 构造处补 `source_doc_id=seed_doc.id, doc_seq=1`（同一用例多条则 doc_seq 递增）；经 `publish()` 的调用在 Task 4 改 `PublishInput` 后才会强制要求，本任务若 publish 相关用例未失败则不动。

Run: `pytest -q`
Expected: 全绿（数量 ≥ 188）

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0002_source_doc.py backend/app/storage/pg/models.py backend/tests/
git commit -m "feat: source_doc 表与三表加列，存量回填迁移"
```

---

### Task 2: 对齐引擎（pipeline/align.py，纯函数）

**Files:**
- Create: `backend/app/pipeline/align.py`
- Test: `backend/tests/test_align.py`

**Interfaces:**
- Consumes: `parser.split_entries / parse_sections`、`content_hash.content_hash`。
- Produces:

```python
@dataclass
class ExistingEntry:
    kid: str
    title: str
    content_hash: str
    is_form: bool  # source_ref 以 "form:" 开头

@dataclass
class AlignedItem:
    seq: int                 # 新文本内序号，1 起；disappeared 排在末尾续号
    title: str | None
    content: str             # 原始条目 markdown；disappeared 为 ""
    align_action: str        # "new" | "changed" | "unchanged" | "disappeared"
    match_kid: str | None
    is_form: bool            # 仅 disappeared 行有意义，其余 False

def align(type_: str, markdown: str, existing: list[ExistingEntry]) -> list[AlignedItem]
```

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_align.py
"""重拆对齐纯函数（spec §5）：标题精确匹配，四类动作。"""

from app.pipeline.align import ExistingEntry, align
from app.pipeline.content_hash import content_hash

FAQ_A = "# 如何退款？\n\n## 标准问法\n如何退款？\n\n## 相似问法\n- 退款流程\n- 退钱\n\n## 标准答案\n订单页申请。\n\n## 适用条件\n7 天内"
FAQ_A_CHANGED = FAQ_A.replace("订单页申请。", "订单详情页点击申请退款。")
FAQ_B = "# 发货时间？\n\n## 标准问法\n发货时间？\n\n## 相似问法\n- 几天发货\n- 何时发货\n\n## 标准答案\n当天发货。\n\n## 适用条件\n现货"


def hash_of(entry_md: str) -> str:
    from app.pipeline import parser

    _, fields = parser.parse_sections(entry_md)
    return content_hash("faq", fields)


def exists(kid: str, title: str, md: str, is_form: bool = False) -> ExistingEntry:
    return ExistingEntry(kid=kid, title=title, content_hash=hash_of(md), is_form=is_form)


class TestAlign:
    def test_four_actions(self):
        existing = [
            exists("faq-fo-0001", "如何退款？", FAQ_A),      # 将变更
            exists("faq-fo-0002", "被删掉的", FAQ_B),         # 将消失
        ]
        items = align("faq", f"{FAQ_A_CHANGED}\n\n{FAQ_B}", existing)
        by_action = {i.align_action: i for i in items}
        assert by_action["changed"].match_kid == "faq-fo-0001"
        assert by_action["new"].title == "发货时间？"
        assert by_action["disappeared"].match_kid == "faq-fo-0002"
        assert by_action["disappeared"].content == ""
        assert by_action["disappeared"].seq == 3  # 排在解析条目之后

    def test_unchanged(self):
        existing = [exists("faq-fo-0001", "如何退款？", FAQ_A)]
        items = align("faq", FAQ_A, existing)
        assert items[0].align_action == "unchanged"
        assert items[0].match_kid == "faq-fo-0001"

    def test_faq_title_uses_standard_question(self):
        """FAQ 匹配用「标准问法」段作标题（与 confirm 覆盖规则一致）。"""
        md = FAQ_A.replace("# 如何退款？", "# 随便写的一级标题")
        existing = [exists("faq-fo-0001", "如何退款？", FAQ_A)]
        assert align("faq", md, existing)[0].align_action == "unchanged"

    def test_disappeared_form_entry_flagged(self):
        existing = [exists("faq-fo-0003", "表单加的", FAQ_B, is_form=True)]
        items = align("faq", FAQ_A, existing)
        gone = [i for i in items if i.align_action == "disappeared"][0]
        assert gone.is_form is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_align.py -v`
Expected: FAIL（`ModuleNotFoundError: app.pipeline.align`）

- [ ] **Step 3: 实现**

```python
# backend/app/pipeline/align.py
"""重拆对齐（spec §5，4.1.4 的 P1 零 LLM 落地）。

标题精确匹配（trim；FAQ 用「标准问法」段覆盖标题，与导入 confirm 规则一致）：
- 标题匹配 + hash 相同 → unchanged；hash 不同 → changed
- 新标题 → new；旧条目标题未出现 → disappeared（content 置空，seq 续在末尾）
改标题会被判为“消失+新增”，属 P1 已知边界，由预览页人工纠正。
"""

from dataclasses import dataclass

from app.pipeline import parser
from app.pipeline.content_hash import content_hash


@dataclass
class ExistingEntry:
    kid: str
    title: str
    content_hash: str
    is_form: bool


@dataclass
class AlignedItem:
    seq: int
    title: str | None
    content: str
    align_action: str
    match_kid: str | None
    is_form: bool = False


def _entry_title(type_: str, entry_md: str) -> tuple[str | None, dict[str, str]]:
    title, fields = parser.parse_sections(entry_md)
    if type_ == "faq" and fields.get("标准问法", "").strip():
        title = fields["标准问法"].strip()
    return (title.strip() if title else None), fields


def align(type_: str, markdown: str, existing: list[ExistingEntry]) -> list[AlignedItem]:
    by_title = {e.title.strip(): e for e in existing}
    matched: set[str] = set()
    items: list[AlignedItem] = []

    for seq, entry_md in enumerate(parser.split_entries(markdown), start=1):
        title, fields = _entry_title(type_, entry_md)
        old = by_title.get(title) if title else None
        if old is None:
            items.append(AlignedItem(seq, title, entry_md, "new", None))
        else:
            matched.add(old.kid)
            action = "unchanged" if content_hash(type_, fields) == old.content_hash else "changed"
            items.append(AlignedItem(seq, title, entry_md, action, old.kid))

    next_seq = len(items) + 1
    for e in existing:
        if e.kid not in matched:
            items.append(AlignedItem(next_seq, e.title, "", "disappeared", e.kid, is_form=e.is_form))
            next_seq += 1
    return items
```

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `pytest tests/test_align.py -v && pytest -q`
Expected: PASS，全绿

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline/align.py backend/tests/test_align.py
git commit -m "feat: 重拆对齐引擎（标题匹配，四类动作）"
```

---

### Task 3: 发布链路带上文件归属（PublishInput 扩展 + 表单挂文件）

**Files:**
- Modify: `backend/app/pipeline/publish.py`（`PublishInput` 加字段；`save_draft`/`publish` 新建行时写入）
- Modify: `backend/app/console/knowledge.py`（`KnowledgeCreate` 加字段；`create_knowledge` 解析/新建文件；`_to_input` 传递）
- Test: `backend/tests/test_console_knowledge.py`（追加类）、既有 publish 测试修种子

**Interfaces:**
- Consumes: Task 1 的 `SourceDoc`。
- Produces: `PublishInput` 新增 `source_doc_id: int | None = None`、`doc_seq: int | None = None`（**新建行时必填**，更新已有 kid 时忽略）；`KnowledgeCreate` 新增 `source_doc_id: int | None`、`new_doc_name: str | None`；辅助函数 `resolve_source_doc(session, user, domain, type_, source_doc_id, new_doc_name) -> SourceDoc`（knowledge.py 内，Task 5/8 复用）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_console_knowledge.py` 追加：

```python
class TestSourceDocAttachment:
    async def test_create_with_new_doc(self, app_client, seeded):
        body = create_body(new_doc_name="客服FAQ")
        resp = await app_client.post(
            "/api/knowledge", json=body, cookies=await cookies_for("ou_member")
        )
        assert resp.status_code == 200
        kid = resp.json()["kid"]
        detail = await app_client.get(f"/api/knowledge/{kid}", cookies=await cookies_for("ou_member"))
        assert detail.json()["source_doc"]["name"] == "客服FAQ"

    async def test_create_with_existing_doc(self, app_client, seeded, db_session):
        from app.storage.pg.models import SourceDoc

        doc = SourceDoc(name="已有文件", domain_code="free-order", type="faq",
                        source="manual", created_by="ou_member")
        db_session.add(doc)
        await db_session.commit()
        body = create_body(source_doc_id=doc.id)
        resp = await app_client.post(
            "/api/knowledge", json=body, cookies=await cookies_for("ou_member")
        )
        assert resp.json()["kid"] is not None

    async def test_create_without_doc_rejected(self, app_client, seeded):
        resp = await app_client.post(
            "/api/knowledge", json=create_body(), cookies=await cookies_for("ou_member")
        )
        assert resp.status_code == 400  # 必须归属文件（spec §4.1）

    async def test_doc_type_mismatch_rejected(self, app_client, seeded, db_session):
        from app.storage.pg.models import SourceDoc

        doc = SourceDoc(name="SOP文件", domain_code="free-order", type="sop",
                        source="manual", created_by="ou_member")
        db_session.add(doc)
        await db_session.commit()
        resp = await app_client.post(
            "/api/knowledge", json=create_body(source_doc_id=doc.id),
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 400
```

同时把本文件既有用例的 `create_body()` 默认值加 `"new_doc_name": "默认测试文件"`（`create_body` 函数体内），避免全部报 400——`test_create_without_doc_rejected` 用 `create_body(new_doc_name=None)` 显式去掉。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_console_knowledge.py::TestSourceDocAttachment -v`
Expected: FAIL（400 未出现 / source_doc 字段缺失）

- [ ] **Step 3: 实现**

`publish.py`——`PublishInput` 末尾加：

```python
    source_doc_id: int | None = None  # 新建行必填；更新已有 kid 时忽略
    doc_seq: int | None = None
```

`save_draft` 与 `publish` 的 `Knowledge(...)` 构造各加：

```python
            source_doc_id=inp.source_doc_id,
            doc_seq=inp.doc_seq,
```

`console/knowledge.py`：

```python
from sqlalchemy import func, or_, select  # 已有
from app.storage.pg.models import SourceDoc  # import 区追加


async def resolve_source_doc(
    session: AsyncSession,
    user: ConsoleUser,
    domain: str,
    type_: str,
    source_doc_id: int | None,
    new_doc_name: str | None,
) -> SourceDoc:
    """表单/导入共用：取已有文件或新建 manual 文件；校验 domain/type/active。"""
    if source_doc_id is not None:
        doc = await session.get(SourceDoc, source_doc_id)
        if doc is None or doc.domain_code != domain:
            raise errors.not_found()
        if doc.type != type_ or doc.status != "active":
            raise errors.invalid_argument("知识文件类型不匹配或已归档")
        return doc
    if not (new_doc_name and new_doc_name.strip()):
        raise errors.invalid_argument("必须指定所属知识文件（source_doc_id 或 new_doc_name）")
    name = new_doc_name.strip()
    dup = await session.execute(
        select(SourceDoc.id).where(SourceDoc.domain_code == domain, SourceDoc.name == name)
    )
    if dup.scalar_one_or_none() is not None:
        raise errors.conflict(f"知识文件「{name}」已存在")
    doc = SourceDoc(name=name, domain_code=domain, type=type_, source="manual",
                    created_by=user.user_id)
    session.add(doc)
    await session.flush()  # 拿 id，不提交（与条目同事务）
    return doc


async def next_doc_seq(session: AsyncSession, doc_id: int) -> int:
    cur = await session.execute(
        select(func.coalesce(func.max(Knowledge.doc_seq), 0)).where(
            Knowledge.source_doc_id == doc_id
        )
    )
    return cur.scalar_one() + 1
```

`KnowledgeCreate` 加：

```python
    source_doc_id: int | None = None
    new_doc_name: str | None = None
```

`_to_input` 加参数 `doc: SourceDoc, doc_seq: int` 并在 `PublishInput(...)` 里传 `source_doc_id=doc.id, doc_seq=doc_seq`。`create_knowledge` 在 `run_pipeline` 之后、发布之前：

```python
    doc = await resolve_source_doc(
        session, user, body.domain, body.type, body.source_doc_id, body.new_doc_name
    )
    inp = _to_input(body, user, doc, await next_doc_seq(session, doc.id))
```

`update_knowledge`（PUT）不动文件归属：`_to_input` 的调用处查出 row 后传 `doc=await session.get(SourceDoc, row.source_doc_id)`、`doc_seq=row.doc_seq`（publish 更新路径本就忽略，但保持签名统一）。

`_knowledge_out` 出参补文件信息：改为 `def _knowledge_out(row: Knowledge, doc_name: str | None = None)` 加：

```python
        "source_doc": {"id": row.source_doc_id, "name": doc_name},
```

列表/详情端点取 doc_name：列表用一次 `select(SourceDoc.id, SourceDoc.name).where(SourceDoc.id.in_(...))` 建映射；详情单查。

- [ ] **Step 4: 跑测试 + 修连带失败**

Run: `pytest -q`
`test_publish.py` 等直接构造 `PublishInput` 的用例：补 `source_doc_id=seed_doc.id, doc_seq=1`（用 Task 1 的 `seed_doc` fixture）。`test_console_gaps.py` 若断言了 `_knowledge_out` 结构需补 `source_doc` 字段。
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline/publish.py backend/app/console/knowledge.py backend/tests/
git commit -m "feat: 发布链路带文件归属，表单创建必选知识文件"
```

---

### Task 4: 导入入口支持粘贴文本 + doc_name（POST /api/imports 扩展）

**Files:**
- Modify: `backend/app/console/knowledge.py`（`upload_import`）
- Test: `backend/tests/test_console_knowledge.py`（追加类；既有导入用例补 doc_name）

**Interfaces:**
- Produces: `POST /api/imports` 接受 multipart 字段：`domain`、`type`、`doc_name`（粘贴必填；上传缺省取文件名去 `.md` 后缀）、`file`（与 `text` 二选一）、`text`。同名文件预查 → 409。`batch.file_name` 统一承载 doc_name（spec §6 说明）。

- [ ] **Step 1: 写失败测试**

```python
FAQ_MD_OK = (
    "# 如何退款？\n\n## 标准问法\n如何退款？\n\n## 相似问法\n- 退款流程\n- 退钱\n\n"
    "## 标准答案\n订单页申请。\n\n## 适用条件\n7 天内\n"
)


class TestImportPasteText:
    async def test_paste_text_creates_batch(self, app_client, seeded):
        resp = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq", "doc_name": "粘贴FAQ", "text": FAQ_MD_OK},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_name"] == "粘贴FAQ"
        assert len(body["items"]) == 1

    async def test_paste_without_doc_name_rejected(self, app_client, seeded):
        resp = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq", "text": FAQ_MD_OK},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 400

    async def test_neither_file_nor_text_rejected(self, app_client, seeded):
        resp = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq", "doc_name": "x"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 400

    async def test_duplicate_doc_name_conflict(self, app_client, seeded, db_session):
        from app.storage.pg.models import SourceDoc

        db_session.add(SourceDoc(name="粘贴FAQ", domain_code="free-order", type="faq",
                                 source="manual", created_by="t"))
        await db_session.commit()
        resp = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq", "doc_name": "粘贴FAQ", "text": FAQ_MD_OK},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 409
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_console_knowledge.py::TestImportPasteText -v`
Expected: FAIL

- [ ] **Step 3: 实现（upload_import 改造）**

签名与正文改为：

```python
@router.post("/imports")
async def upload_import(
    domain: str = Form(),
    type: str = Form(),  # noqa: A002
    doc_name: str | None = Form(None),
    text: str | None = Form(None),
    file: UploadFile | None = File(None),
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await auth.require_domain_role(session, user, domain, {"admin", "member"})
    if type not in KNOWLEDGE_TYPES:
        raise errors.invalid_argument(f"unknown type: {type}")
    if (file is None) == (text is None):
        raise errors.invalid_argument("file 与 text 必须二选一")
    if file is not None:
        raw = await file.read()
        if len(raw) > get_settings().upload_max_mb * 1024 * 1024:
            raise errors.invalid_argument(f"文件超过 {get_settings().upload_max_mb}MB 上限")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise errors.invalid_argument("文件必须为 UTF-8 编码")
        name = (doc_name or "").strip() or (file.filename or "").removesuffix(".md")
    else:
        if len(text.encode()) > get_settings().upload_max_mb * 1024 * 1024:
            raise errors.invalid_argument(f"文本超过 {get_settings().upload_max_mb}MB 上限")
        content = text
        name = (doc_name or "").strip()
    if not name:
        raise errors.invalid_argument("必须提供 doc_name（知识文件名）")
    # 同名预查（spec §6：第一步即报错；confirm 唯一约束兜底并发）
    dup = await session.execute(
        select(SourceDoc.id).where(SourceDoc.domain_code == domain, SourceDoc.name == name)
    )
    if dup.scalar_one_or_none() is not None:
        raise errors.conflict(f"知识文件「{name}」已存在")
    # 以下与原实现一致，file_name 改用 name（承载 doc_name）
    batch = ImportBatch(domain_code=domain, type=type, file_name=name, created_by=user.user_id)
    ...  # 原 split_entries → run_pipeline → ImportItem 循环不变
```

既有解析循环里 `ImportItem(...)` 不用改（align_action 有 server_default 'new'）。原函数中 `raw`/`text` 变量名冲突处按上文重命名为 `content`。

- [ ] **Step 4: 修既有导入用例**

既有 `POST /api/imports` 测试的 multipart 里补 `data={"domain": ..., "type": ..., "doc_name": "..."}` 或依赖文件名缺省。跑全量。

Run: `pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/app/console/knowledge.py backend/tests/test_console_knowledge.py
git commit -m "feat: 导入支持粘贴文本，doc_name 同名预查"
```

---

### Task 5: confirm 建档回填（首次导入创建 source_doc）

**Files:**
- Modify: `backend/app/console/knowledge.py`（`confirm_import`）
- Test: `backend/tests/test_console_knowledge.py`（追加类）

**Interfaces:**
- Consumes: Task 3 的 `PublishInput.source_doc_id/doc_seq`。
- Produces: confirm 后 `batch.source_doc_id` 非空；条目 `source_doc_id` = 该文件、`doc_seq` = `item.seq`；响应加 `"source_doc_id"`。

- [ ] **Step 1: 写失败测试**

```python
class TestConfirmCreatesDoc:
    async def _make_batch(self, app_client) -> dict:
        resp = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq", "doc_name": "确认建档", "text": FAQ_MD_OK},
            cookies=await cookies_for("ou_member"),
        )
        return resp.json()

    async def test_confirm_creates_source_doc(self, app_client, seeded, db_session):
        from app.storage.pg.models import Knowledge, SourceDoc

        batch = await self._make_batch(app_client)
        resp = await app_client.post(
            f"/api/imports/{batch['id']}/confirm",
            json={"item_ids": [batch["items"][0]["id"]]},
            cookies=await cookies_for("ou_member"),
        )
        body = resp.json()
        assert body["source_doc_id"] is not None
        doc = await db_session.get(SourceDoc, body["source_doc_id"])
        assert doc.name == "确认建档" and doc.source == "manual"
        kid = body["results"][0]["kid"]
        row = await db_session.get(Knowledge, kid)
        assert row.source_doc_id == doc.id and row.doc_seq == 1

    async def test_upload_source_is_upload(self, app_client, seeded, db_session):
        """file 通道建的文件 source='upload'。"""
        from app.storage.pg.models import SourceDoc

        resp = await app_client.post(
            "/api/imports",
            data={"domain": "free-order", "type": "faq"},
            files={"file": ("faq.md", FAQ_MD_OK.encode(), "text/markdown")},
            cookies=await cookies_for("ou_member"),
        )
        batch = resp.json()
        resp = await app_client.post(
            f"/api/imports/{batch['id']}/confirm",
            json={"item_ids": [batch["items"][0]["id"]]},
            cookies=await cookies_for("ou_member"),
        )
        doc = await db_session.get(SourceDoc, resp.json()["source_doc_id"])
        assert doc.source == "upload"
```

来源判定需要知道批次走的哪个通道：`ImportBatch` 无此字段——在 Step 3 里用 `source_ref` 之外最简法：`upload_import` 时把来源暂存进 batch 的既有列没有位置，因此**给 ImportBatch 再加一列** `origin: Mapped[str]`（`'manual'|'upload'`，server_default `'upload'`），并入 Task 1 的迁移文件（本任务补 `op.add_column("import_batch", sa.Column("origin", sa.String(16), nullable=False, server_default="upload"))` 到 0002 迁移与 ORM，然后 DROP kg_test 重建）。

- [ ] **Step 2: 跑测试确认失败**

Run: `psql postgresql://kg:kg@localhost:5433/kg -c 'DROP DATABASE IF EXISTS kg_test' && pytest tests/test_console_knowledge.py::TestConfirmCreatesDoc -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`upload_import`：`ImportBatch(..., origin="manual" if text is not None else "upload")`。

`confirm_import`：在循环前建档（仅首次导入，更新批次 Task 7 已挂）：

```python
    doc: SourceDoc | None = None
    if batch.source_doc_id is None:
        doc = SourceDoc(
            name=batch.file_name, domain_code=batch.domain_code, type=batch.type,
            source=batch.origin, created_by=user.user_id,
        )
        session.add(doc)
        try:
            await session.flush()
        except IntegrityError:  # 并发同名兜底（spec §6）
            await session.rollback()
            raise errors.conflict(f"知识文件「{batch.file_name}」已存在")
        batch.source_doc_id = doc.id
    else:
        doc = await session.get(SourceDoc, batch.source_doc_id)
```

循环内 `PublishInput(...)` 追加 `source_doc_id=doc.id, doc_seq=item.seq`。响应加 `"source_doc_id": batch.source_doc_id`。文件顶部 import 补 `from sqlalchemy.exc import IntegrityError`。

- [ ] **Step 4: 跑测试 + 全量**

Run: `pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0002_source_doc.py backend/app/storage/pg/models.py backend/app/console/knowledge.py backend/tests/
git commit -m "feat: confirm 建档回填，条目归属知识文件"
```

---

### Task 6: source-docs 查询 API（列表 / 详情 / 拼合全文）

**Files:**
- Create: `backend/app/console/source_docs.py`
- Modify: `backend/app/console/router.py`（挂新 router；先 `grep -n include_router backend/app/console/router.py` 按既有写法追加）
- Test: `backend/tests/test_source_docs_api.py`

**Interfaces:**
- Produces:
  - `GET /api/source-docs?domain=&type=&status=&q=` → `{items: [{id,name,domain,type,source,status,entry_total,entry_published,updated_at}]}`
  - `GET /api/source-docs/{id}` → 上述字段 + `entries: [{kid,title,status,version,expire_date,doc_seq}]`（按 doc_seq）+ `batches: [{id,origin,created_by,created_at,stats:{new,changed,disappeared}}]`
  - `GET /api/source-docs/{id}/content` → `{name, markdown}`（非 draft/非 archived 条目当前版本快照按 doc_seq 以 `\n\n` 拼合）
  - 模块级辅助 `load_doc(session, user, doc_id, roles={"admin","member"}) -> SourceDoc`（越权/不存在 404；Task 7/8 复用）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_source_docs_api.py
"""知识文件查询接口（spec §4.2、§6）。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.console import auth as console_auth
from app.storage.pg.models import ConsoleUser, Domain, DomainMember
from app.storage.pg.session import get_session
from app.storage.viking.client import get_viking
from tests.conftest import RecordingViking
from tests.test_console_knowledge import FAQ_MD_OK, cookies_for

FAQ_MD_TWO = FAQ_MD_OK + (
    "\n# 发货时间？\n\n## 标准问法\n发货时间？\n\n## 相似问法\n- 几天发货\n- 何时发货\n\n"
    "## 标准答案\n当天发货。\n\n## 适用条件\n现货\n"
)


@pytest.fixture
async def app_client(db_session):
    from app.main import create_app

    app = create_app()

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_viking] = lambda: RecordingViking().client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def seeded(db_session):
    db_session.add(Domain(code="free-order", short_code="fo", name="免单域", created_by="t"))
    for uid in ("ou_member", "ou_out"):
        db_session.add(ConsoleUser(user_id=uid, name=uid))
    db_session.add(DomainMember(domain_code="free-order", user_id="ou_member", role="member"))
    await db_session.commit()


async def import_doc(app_client, name: str, md: str = FAQ_MD_TWO) -> int:
    resp = await app_client.post(
        "/api/imports",
        data={"domain": "free-order", "type": "faq", "doc_name": name, "text": md},
        cookies=await cookies_for("ou_member"),
    )
    batch = resp.json()
    resp = await app_client.post(
        f"/api/imports/{batch['id']}/confirm",
        json={"item_ids": [i["id"] for i in batch["items"]]},
        cookies=await cookies_for("ou_member"),
    )
    return resp.json()["source_doc_id"]


class TestSourceDocList:
    async def test_list_with_counts(self, app_client, seeded):
        await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            "/api/source-docs", params={"domain": "free-order"},
            cookies=await cookies_for("ou_member"),
        )
        item = resp.json()["items"][0]
        assert item["name"] == "文件甲"
        assert item["entry_total"] == 2 and item["entry_published"] == 2

    async def test_outsider_sees_nothing(self, app_client, seeded):
        await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            "/api/source-docs", params={"domain": "free-order"},
            cookies=await cookies_for("ou_out"),
        )
        assert resp.json()["items"] == []


class TestSourceDocDetail:
    async def test_detail_entries_ordered(self, app_client, seeded):
        doc_id = await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            f"/api/source-docs/{doc_id}", cookies=await cookies_for("ou_member")
        )
        entries = resp.json()["entries"]
        assert [e["doc_seq"] for e in entries] == [1, 2]
        assert resp.json()["batches"][0]["stats"]["new"] == 2

    async def test_content_concatenates(self, app_client, seeded):
        doc_id = await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            f"/api/source-docs/{doc_id}/content", cookies=await cookies_for("ou_member")
        )
        md = resp.json()["markdown"]
        assert md.index("# 如何退款？") < md.index("# 发货时间？")

    async def test_outsider_404(self, app_client, seeded):
        doc_id = await import_doc(app_client, "文件甲")
        resp = await app_client.get(
            f"/api/source-docs/{doc_id}", cookies=await cookies_for("ou_out")
        )
        assert resp.status_code == 404
```

注：`cookies_for`、`FAQ_MD_OK` 若在 test_console_knowledge.py 中非公开，直接在两文件间提到 `tests/helpers.py` 或本文件内复制定义——以跑通为准，不引入循环导入。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_source_docs_api.py -v`
Expected: FAIL（404 no route）

- [ ] **Step 3: 实现**

```python
# backend/app/console/source_docs.py
"""知识文件（source_doc）查询与操作面（spec §4、§6）。"""

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.console import auth
from app.console.router_deps import current_user
from app.domain.state_machine import Status
from app.storage.pg.models import (
    ConsoleUser,
    DomainMember,
    ImportBatch,
    ImportItem,
    Knowledge,
    KnowledgeVersion,
    SourceDoc,
)
from app.storage.pg.session import get_session

router = APIRouter()


def _doc_out(doc: SourceDoc, total: int = 0, published: int = 0) -> dict:
    return {
        "id": doc.id,
        "name": doc.name,
        "domain": doc.domain_code,
        "type": doc.type,
        "source": doc.source,
        "status": doc.status,
        "entry_total": total,
        "entry_published": published,
        "updated_at": doc.updated_at.isoformat(),
    }


async def load_doc(
    session: AsyncSession,
    user: ConsoleUser,
    doc_id: int,
    roles: set[str] = frozenset({"admin", "member"}),
) -> SourceDoc:
    doc = await session.get(SourceDoc, doc_id)
    if doc is None:
        raise errors.not_found()
    await auth.require_domain_role(session, user, doc.domain_code, roles)
    return doc


def _count_stmt():
    return (
        select(
            Knowledge.source_doc_id,
            func.count().label("total"),
            func.sum(case((Knowledge.status == Status.PUBLISHED, 1), else_=0)).label("published"),
        )
        .group_by(Knowledge.source_doc_id)
        .subquery()
    )


@router.get("/source-docs")
async def list_source_docs(
    domain: str | None = None,
    type: str | None = None,  # noqa: A002
    status: str | None = None,
    q: str | None = None,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    counts = _count_stmt()
    stmt = (
        select(SourceDoc, counts.c.total, counts.c.published)
        .outerjoin(counts, counts.c.source_doc_id == SourceDoc.id)
        .order_by(SourceDoc.updated_at.desc())
    )
    if not user.is_platform_admin:
        member_domains = select(DomainMember.domain_code).where(
            DomainMember.user_id == user.user_id
        )
        stmt = stmt.where(SourceDoc.domain_code.in_(member_domains))
    if domain:
        stmt = stmt.where(SourceDoc.domain_code == domain)
    if type:
        stmt = stmt.where(SourceDoc.type == type)
    if status:
        stmt = stmt.where(SourceDoc.status == status)
    if q:
        stmt = stmt.where(SourceDoc.name.ilike(f"%{q}%"))
    rows = (await session.execute(stmt)).all()
    return {"items": [_doc_out(d, t or 0, int(p or 0)) for d, t, p in rows]}


@router.get("/source-docs/{doc_id}")
async def source_doc_detail(
    doc_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    doc = await load_doc(session, user, doc_id)
    entries = (
        (
            await session.execute(
                select(Knowledge)
                .where(Knowledge.source_doc_id == doc_id)
                .order_by(Knowledge.doc_seq)
            )
        )
        .scalars()
        .all()
    )
    batches = (
        (
            await session.execute(
                select(ImportBatch)
                .where(ImportBatch.source_doc_id == doc_id)
                .order_by(ImportBatch.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    stats_rows = (
        await session.execute(
            select(ImportItem.batch_id, ImportItem.align_action, func.count())
            .where(ImportItem.batch_id.in_([b.id for b in batches] or [0]))
            .group_by(ImportItem.batch_id, ImportItem.align_action)
        )
    ).all()
    stats: dict[int, dict[str, int]] = {}
    for bid, action, n in stats_rows:
        stats.setdefault(bid, {})[action] = n
    published = sum(1 for e in entries if e.status == Status.PUBLISHED)
    return {
        **_doc_out(doc, len(entries), published),
        "entries": [
            {
                "kid": e.kid,
                "title": e.title,
                "status": e.status,
                "version": e.version,
                "expire_date": e.expire_date.isoformat(),
                "doc_seq": e.doc_seq,
            }
            for e in entries
        ],
        "batches": [
            {
                "id": b.id,
                "origin": b.origin,
                "created_by": b.created_by,
                "created_at": b.created_at.isoformat(),
                "stats": stats.get(b.id, {}),
            }
            for b in batches
        ],
    }


@router.get("/source-docs/{doc_id}/content")
async def source_doc_content(
    doc_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """全文 = 非 draft / 非 archived 条目当前版本快照按 doc_seq 拼合（spec §2、§4.2）。"""
    doc = await load_doc(session, user, doc_id)
    rows = (
        await session.execute(
            select(KnowledgeVersion.content)
            .join(
                Knowledge,
                (Knowledge.kid == KnowledgeVersion.kid)
                & (Knowledge.version == KnowledgeVersion.version),
            )
            .where(
                Knowledge.source_doc_id == doc_id,
                Knowledge.status.notin_([Status.DRAFT, Status.ARCHIVED]),
            )
            .order_by(Knowledge.doc_seq)
        )
    ).scalars()
    return {"name": doc.name, "markdown": "\n\n".join(c.rstrip() + "\n" for c in rows)}
```

`console/router.py` 按既有 `include_router` 写法追加 `source_docs.router`。

- [ ] **Step 4: 跑测试 + 全量**

Run: `pytest tests/test_source_docs_api.py -v && pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/app/console/source_docs.py backend/app/console/router.py backend/tests/test_source_docs_api.py
git commit -m "feat: source-docs 列表/详情/拼合全文接口"
```

---

### Task 7: 更新入口（POST /api/source-docs/{id}/update → 对齐批次）

**Files:**
- Modify: `backend/app/console/source_docs.py`
- Modify: `backend/app/console/knowledge.py`（`_batch_out` 的 items 加 `align_action/match_kid/is_form`）
- Test: `backend/tests/test_source_docs_api.py`（追加类）

**Interfaces:**
- Consumes: Task 2 `align()`、Task 6 `load_doc`。
- Produces: `POST /api/source-docs/{doc_id}/update`（multipart：`text` 或 `file`）→ 与 `_batch_out` 同构的批次预览，items 带 `align_action`、`match_kid`、`is_form`；归档文件 → 409。`is_form` 的判定：disappeared 且对应条目 `source_ref` 以 `form:` 开头。

- [ ] **Step 1: 写失败测试**

```python
class TestSourceDocUpdate:
    async def test_update_preview_four_actions(self, app_client, seeded, db_session):
        doc_id = await import_doc(app_client, "文件甲")  # 两条：如何退款？/ 发货时间？
        new_md = FAQ_MD_OK.replace("订单页申请。", "订单详情页申请。") + (
            "\n# 新问题？\n\n## 标准问法\n新问题？\n\n## 相似问法\n- 新1\n- 新2\n\n"
            "## 标准答案\n新答案。\n\n## 适用条件\n无\n"
        )  # 变更 1 条 + 新增 1 条；「发货时间？」消失
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/update",
            data={"text": new_md},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        actions = {i["align_action"] for i in resp.json()["items"]}
        assert actions == {"changed", "new", "disappeared"}

    async def test_update_archived_doc_conflict(self, app_client, seeded, db_session):
        from app.storage.pg.models import SourceDoc

        doc_id = await import_doc(app_client, "文件乙")
        (await db_session.get(SourceDoc, doc_id)).status = "archived"
        await db_session.commit()
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/update",
            data={"text": FAQ_MD_OK},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 409
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_source_docs_api.py::TestSourceDocUpdate -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`source_docs.py` 追加（import 区补 `File, Form, UploadFile`、`align`、`run_pipeline` 等）：

```python
from fastapi import File, Form, UploadFile

from app.config import get_settings
from app.console.knowledge import _batch_out, run_pipeline
from app.pipeline import parser
from app.pipeline.align import ExistingEntry, align


@router.post("/source-docs/{doc_id}/update")
async def update_source_doc(
    doc_id: int,
    text: str | None = Form(None),
    file: UploadFile | None = File(None),
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    doc = await load_doc(session, user, doc_id)
    if doc.status != "active":
        raise errors.conflict("知识文件已归档，不可更新")
    if (file is None) == (text is None):
        raise errors.invalid_argument("file 与 text 必须二选一")
    if file is not None:
        raw = await file.read()
        if len(raw) > get_settings().upload_max_mb * 1024 * 1024:
            raise errors.invalid_argument(f"文件超过 {get_settings().upload_max_mb}MB 上限")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise errors.invalid_argument("文件必须为 UTF-8 编码")
    else:
        content = text

    rows = (
        (
            await session.execute(
                select(Knowledge)
                .where(
                    Knowledge.source_doc_id == doc_id,
                    Knowledge.status.notin_([Status.DRAFT, Status.ARCHIVED]),
                )
                .order_by(Knowledge.doc_seq)
            )
        )
        .scalars()
        .all()
    )
    existing = [
        ExistingEntry(
            kid=r.kid, title=r.title, content_hash=r.content_hash,
            is_form=r.source_ref.startswith("form:"),
        )
        for r in rows
    ]
    aligned = align(doc.type, content, existing)

    batch = ImportBatch(
        domain_code=doc.domain_code, type=doc.type, file_name=doc.name,
        origin="manual" if text is not None else "upload",
        source_doc_id=doc.id, created_by=user.user_id,
    )
    session.add(batch)
    await session.flush()
    items = []
    for a in aligned:
        if a.align_action == "disappeared":
            validation, is_valid = [], True
        else:
            _, fields = parser.parse_sections(a.content)
            validation, is_valid = run_pipeline(doc.type, fields)
        items.append(
            ImportItem(
                batch_id=batch.id, seq=a.seq, title=a.title, content=a.content,
                validation=validation, is_valid=is_valid,
                align_action=a.align_action, match_kid=a.match_kid,
            )
        )
    session.add_all(items)
    await session.commit()
    return _batch_out(batch, items)
```

`knowledge.py` 的 `_batch_out` items 映射追加三个字段：

```python
                "align_action": i.align_action,
                "match_kid": i.match_kid,
                "is_form": bool(i.align_action == "disappeared" and i.match_kid and form_kids and i.match_kid in form_kids),
```

`is_form` 需要查库不适合放纯函数——改为在 `_batch_out` 加可选参数 `form_kids: set[str] | None = None`，`update_source_doc` 调用时传 `{r.kid for r in rows if r.source_ref.startswith("form:")}`，其余调用点不传（默认 None → False）。

注意跨模块 import：`source_docs.py` import `knowledge.py` 的 `_batch_out/run_pipeline` 是单向依赖，无循环（knowledge.py 不 import source_docs）。

- [ ] **Step 4: 跑测试 + 全量**

Run: `pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/app/console/source_docs.py backend/app/console/knowledge.py backend/tests/test_source_docs_api.py
git commit -m "feat: 知识文件更新入口，生成对齐批次预览"
```

---

### Task 8: confirm 处理更新批次（按 align_action 分派 + doc_seq 重写）

**Files:**
- Modify: `backend/app/console/knowledge.py`（`confirm_import`）
- Test: `backend/tests/test_source_docs_api.py`（追加类）

**Interfaces:**
- Consumes: Task 7 生成的批次（items 带 align_action/match_kid）。
- Produces: confirm 对更新批次的行为——`new`→新条目入库；`changed`→`publish(kid=match_kid)` 版本+1；`disappeared`→条目 ARCHIVE + viking.delete；`unchanged`→忽略；随后按规则重写 doc_seq。

- [ ] **Step 1: 写失败测试**

```python
class TestConfirmUpdateBatch:
    async def test_full_update_cycle(self, app_client, seeded, db_session):
        from app.storage.pg.models import Knowledge as K

        doc_id = await import_doc(app_client, "文件丙")
        new_md = FAQ_MD_OK.replace("订单页申请。", "订单详情页申请。") + (
            "\n# 新问题？\n\n## 标准问法\n新问题？\n\n## 相似问法\n- 新1\n- 新2\n\n"
            "## 标准答案\n新答案。\n\n## 适用条件\n无\n"
        )
        preview = (
            await app_client.post(
                f"/api/source-docs/{doc_id}/update",
                data={"text": new_md},
                cookies=await cookies_for("ou_member"),
            )
        ).json()
        selectable = [i["id"] for i in preview["items"] if i["align_action"] != "unchanged"]
        resp = await app_client.post(
            f"/api/imports/{preview['id']}/confirm",
            json={"item_ids": selectable},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200

        rows = (
            (await db_session.execute(
                __import__("sqlalchemy").select(K).where(K.source_doc_id == doc_id).order_by(K.doc_seq)
            )).scalars().all()
        )
        by_title = {r.title: r for r in rows}
        assert by_title["如何退款？"].version == 2          # changed → 版本+1
        assert by_title["新问题？"].status == "published"     # new → 入库
        assert by_title["发货时间？"].status == "archived"    # disappeared → 下架
        # doc_seq 重写：新文本序（如何退款？=1，新问题？=2）
        assert by_title["如何退款？"].doc_seq == 1
        assert by_title["新问题？"].doc_seq == 2

    async def test_disappeared_unselected_survives(self, app_client, seeded, db_session):
        from app.storage.pg.models import Knowledge as K

        doc_id = await import_doc(app_client, "文件丁")
        preview = (
            await app_client.post(
                f"/api/source-docs/{doc_id}/update",
                data={"text": FAQ_MD_OK},  # 只剩第一条 →「发货时间？」标 disappeared
                cookies=await cookies_for("ou_member"),
            )
        ).json()
        keep = [i["id"] for i in preview["items"] if i["align_action"] == "new"]  # 全 unchanged/disappeared → 空
        resp = await app_client.post(
            f"/api/imports/{preview['id']}/confirm",
            json={"item_ids": keep},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 200
        rows = (
            (await db_session.execute(
                __import__("sqlalchemy").select(K).where(K.source_doc_id == doc_id)
            )).scalars().all()
        )
        assert all(r.status == "published" for r in rows)  # 未勾选的 disappeared 不下架
```

（测试里 `__import__("sqlalchemy").select` 写法替换为文件顶部 `from sqlalchemy import select` 后直接用 `select`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_source_docs_api.py::TestConfirmUpdateBatch -v`
Expected: FAIL

- [ ] **Step 3: 实现（confirm_import 改造）**

循环体按 align_action 分派（保留原 new 逻辑为缺省分支）：

```python
    for item_id in body.item_ids:
        item = by_id.get(item_id)
        if item is None:
            results.append({"item_id": item_id, "kid": None, "error": "条目不存在"})
            continue
        if item.align_action == "unchanged":
            continue  # 幂等：无动作
        if item.align_action == "disappeared":
            row = await session.get(Knowledge, item.match_kid)
            row.status = transition(Status(row.status), Event.ARCHIVE)
            item.result_kid = row.kid
            await viking.delete(build_uri(row.domain_code, row.type, row.kid))
            results.append({"item_id": item_id, "kid": row.kid, "error": None})
            continue
        if not item.is_valid:
            results.append({"item_id": item_id, "kid": None, "error": "blocking 校验未通过"})
            continue
        title, fields = parser.parse_sections(item.content)
        if batch.type == "faq" and fields.get("标准问法"):
            title = fields["标准问法"]
        target_kid = item.match_kid if item.align_action == "changed" else None
        existing_row = await session.get(Knowledge, target_kid) if target_kid else None
        inp = PublishInput(
            domain_code=batch.domain_code,
            type_=batch.type,
            title=title or "未命名",
            sections=fields,
            tags=existing_row.tags if existing_row else [],
            owner_user_id=existing_row.owner_user_id if existing_row else user.user_id,
            source_type="markdown",
            source_ref=f"import:{batch.id}:{item.seq}",
            source_url=None,
            effective_date=date.today(),
            expire_date=None,
            actor_user_id=user.user_id,
            source_doc_id=doc.id,
            doc_seq=item.seq,
        )
        try:
            result = await publish(session, viking, inp, kid=target_kid)
        except DuplicateContent as exc:
            results.append({"item_id": item_id, "kid": None, "error": f"与 {exc.existing_kid} 内容重复"})
            continue
        item.result_kid = result.kid
        results.append({"item_id": item_id, "kid": result.kid, "error": None})
```

循环后 doc_seq 重写（仅更新批次，spec §5）：

```python
    if any(i.align_action != "new" for i in items):
        # 新文本内条目（含 unchanged/changed 的 match_kid 与 new 的 result_kid）按 seq 定序
        ordered: list[str] = []
        for i in sorted(items, key=lambda x: x.seq):
            if i.align_action in ("unchanged", "changed") and i.match_kid:
                ordered.append(i.match_kid)
            elif i.align_action == "new" and i.result_kid:
                ordered.append(i.result_kid)
        survivors = (
            (await session.execute(
                select(Knowledge).where(
                    Knowledge.source_doc_id == doc.id,
                    Knowledge.status != Status.ARCHIVED,
                ).order_by(Knowledge.doc_seq)
            )).scalars().all()
        )
        seq_map = {kid: n for n, kid in enumerate(ordered, start=1)}
        tail = len(ordered)
        for row in survivors:
            if row.kid in seq_map:
                row.doc_seq = seq_map[row.kid]
            else:  # 留在架上的旧条目（如表单条目）排末尾，保持原相对顺序
                tail += 1
                row.doc_seq = tail
        doc.updated_at = func.now()
```

import 区补 `from sqlalchemy import func`（已有）与 `SourceDoc`（Task 3 已加）。

- [ ] **Step 4: 跑测试 + 全量**

Run: `pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/app/console/knowledge.py backend/tests/test_source_docs_api.py
git commit -m "feat: confirm 按对齐动作分派，更新后重写 doc_seq"
```

---

### Task 9: 文件级操作（整体续期 / 整体下架 / 重命名）

**Files:**
- Modify: `backend/app/console/source_docs.py`
- Test: `backend/tests/test_source_docs_api.py`（追加类）

**Interfaces:**
- Produces:
  - `POST /api/source-docs/{id}/renew` body `{days: int | null}` → 非 archived/draft 条目 `expire_date = today + (days or domain.default_ttl_days)`，expired 条目走 `Event.RENEW` 回 published；返回 `{renewed: n}`
  - `POST /api/source-docs/{id}/offline` → published/expired 条目 ARCHIVE + viking.delete，doc 置 archived；返回 `{archived_entries: n}`
  - `PATCH /api/source-docs/{id}` body `{name}` → 重命名，同名 409

- [ ] **Step 1: 写失败测试**

```python
class TestSourceDocOps:
    async def test_renew_all(self, app_client, seeded):
        doc_id = await import_doc(app_client, "续期文件")
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/renew", json={"days": 30},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.json()["renewed"] == 2
        detail = (await app_client.get(
            f"/api/source-docs/{doc_id}", cookies=await cookies_for("ou_member")
        )).json()
        from datetime import date, timedelta

        want = (date.today() + timedelta(days=30)).isoformat()
        assert all(e["expire_date"] == want for e in detail["entries"])

    async def test_offline_archives_all(self, app_client, seeded):
        doc_id = await import_doc(app_client, "下架文件")
        resp = await app_client.post(
            f"/api/source-docs/{doc_id}/offline", cookies=await cookies_for("ou_member")
        )
        assert resp.json()["archived_entries"] == 2
        detail = (await app_client.get(
            f"/api/source-docs/{doc_id}", cookies=await cookies_for("ou_member")
        )).json()
        assert detail["status"] == "archived"
        assert all(e["status"] == "archived" for e in detail["entries"])

    async def test_rename_conflict(self, app_client, seeded):
        a = await import_doc(app_client, "甲名")
        await import_doc(app_client, "乙名")
        resp = await app_client.patch(
            f"/api/source-docs/{a}", json={"name": "乙名"},
            cookies=await cookies_for("ou_member"),
        )
        assert resp.status_code == 409
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_source_docs_api.py::TestSourceDocOps -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`source_docs.py` 追加（import 区补 `date, timedelta`、`Event, transition`、`Domain`、`BaseModel`、`get_viking, VikingClient, build_uri`）：

```python
from datetime import date, timedelta

from pydantic import BaseModel

from app.domain.state_machine import Event, transition
from app.storage.pg.models import Domain
from app.storage.viking.client import VikingClient, build_uri, get_viking


class RenewDocBody(BaseModel):
    days: int | None = None


@router.post("/source-docs/{doc_id}/renew")
async def renew_source_doc(
    doc_id: int,
    body: RenewDocBody,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    doc = await load_doc(session, user, doc_id)
    domain = await session.get(Domain, doc.domain_code)
    new_expire = date.today() + timedelta(days=body.days or domain.default_ttl_days)
    rows = (
        (await session.execute(
            select(Knowledge).where(
                Knowledge.source_doc_id == doc_id,
                Knowledge.status.notin_([Status.DRAFT, Status.ARCHIVED]),
            )
        )).scalars().all()
    )
    for row in rows:
        if row.status == Status.EXPIRED:
            row.status = transition(Status(row.status), Event.RENEW)
        row.expire_date = new_expire
    await session.commit()
    return {"renewed": len(rows), "expire_date": new_expire.isoformat()}


@router.post("/source-docs/{doc_id}/offline")
async def offline_source_doc(
    doc_id: int,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    viking: VikingClient = Depends(get_viking),
):
    doc = await load_doc(session, user, doc_id)
    rows = (
        (await session.execute(
            select(Knowledge).where(
                Knowledge.source_doc_id == doc_id,
                Knowledge.status.in_([Status.PUBLISHED, Status.EXPIRED]),
            )
        )).scalars().all()
    )
    for row in rows:
        row.status = transition(Status(row.status), Event.ARCHIVE)
    doc.status = "archived"
    await session.commit()
    for row in rows:
        await viking.delete(build_uri(row.domain_code, row.type, row.kid))  # 幂等
    return {"archived_entries": len(rows)}


class RenameBody(BaseModel):
    name: str


@router.patch("/source-docs/{doc_id}")
async def rename_source_doc(
    doc_id: int,
    body: RenameBody,
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    doc = await load_doc(session, user, doc_id)
    name = body.name.strip()
    if not name:
        raise errors.invalid_argument("名称不能为空")
    dup = await session.execute(
        select(SourceDoc.id).where(
            SourceDoc.domain_code == doc.domain_code,
            SourceDoc.name == name,
            SourceDoc.id != doc_id,
        )
    )
    if dup.scalar_one_or_none() is not None:
        raise errors.conflict(f"知识文件「{name}」已存在")
    doc.name = name
    await session.commit()
    return {"id": doc.id, "name": doc.name}
```

- [ ] **Step 4: 跑测试 + 全量**

Run: `pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/app/console/source_docs.py backend/tests/test_source_docs_api.py
git commit -m "feat: 文件级整体续期/下架/重命名"
```

---

### Task 10: 前端类型与 API 封装（client.ts）+ 路由/侧边栏

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: `SourceDocItem`、`SourceDocDetailOut`、`SourceDocEntry`、`ALIGN_LABEL/ALIGN_COLOR`；`ImportItemOut` 加 `align_action/match_kid/is_form`；`KnowledgeItem` 加 `source_doc: {id: number; name: string | null}`；路由 `/source-docs`、`/source-docs/:id` 与菜单项。Task 11–13 依赖这些名字。

- [ ] **Step 1: client.ts 追加**

```typescript
export interface SourceDocItem {
  id: number
  name: string
  domain: string
  type: string
  source: 'manual' | 'upload' | 'feishu'
  status: 'active' | 'archived'
  entry_total: number
  entry_published: number
  updated_at: string
}

export interface SourceDocEntry {
  kid: string
  title: string
  status: string
  version: number
  expire_date: string
  doc_seq: number
}

export interface SourceDocBatch {
  id: number
  origin: string
  created_by: string
  created_at: string
  stats: Record<string, number>
}

export interface SourceDocDetailOut extends SourceDocItem {
  entries: SourceDocEntry[]
  batches: SourceDocBatch[]
}

export const SOURCE_LABEL: Record<string, string> = {
  manual: '自建',
  upload: '上传',
  feishu: '飞书',
}

export const ALIGN_LABEL: Record<string, string> = {
  new: '新增',
  changed: '变更',
  unchanged: '未变',
  disappeared: '消失',
}

export const ALIGN_COLOR: Record<string, string> = {
  new: 'green',
  changed: 'blue',
  unchanged: 'default',
  disappeared: 'red',
}
```

`ImportItemOut` 加字段 `align_action: string`、`match_kid: string | null`、`is_form: boolean`；`ImportBatchOut` 加 `source_doc_id: number | null`；`KnowledgeItem` 加 `source_doc: { id: number; name: string | null }`。

- [ ] **Step 2: App.tsx 加菜单与路由**

`MENU` 在知识管理之后插入：

```tsx
  { key: '/source-docs', icon: <FileTextOutlined />, label: '知识文件' },
```

（import 区补 `FileTextOutlined`。）Routes 里 `/knowledge/:kid` **之前**加：

```tsx
            <Route path="/source-docs" element={<SourceDocList />} />
            <Route path="/source-docs/:id" element={<SourceDocDetail />} />
```

先建两个占位页避免编译失败（Task 12/13 实现）：`frontend/src/pages/SourceDocList.tsx` / `SourceDocDetail.tsx` 各导出 `export default function SourceDocList() { return null }`（对应命名）。

- [ ] **Step 3: 构建验证 + Commit**

Run: `cd kg-platform/frontend && npm run build`
Expected: 编译通过

```bash
git add frontend/src
git commit -m "feat: 前端知识文件类型/路由/侧边栏骨架"
```

---

### Task 11: ImportPreview 双 tab（粘贴/上传）+ doc_name + 更新模式（对齐徽标）

**Files:**
- Modify: `frontend/src/pages/ImportPreview.tsx`

**Interfaces:**
- Consumes: Task 10 类型；后端 Task 4/7 接口。
- Produces: 页面支持 `?docId=` 与 `?batchId=` 查询参数——`docId` 存在即"更新模式"（隐藏 domain/type/doc_name，提交到 `/api/source-docs/{docId}/update`）；`batchId` 存在则直接 `GET /api/imports/{batchId}` 载入预览（在线编辑跳转用）。

- [ ] **Step 1: 改造要点（完整实现）**

1. 顶部 hook：

```tsx
import { useNavigate, useSearchParams } from 'react-router-dom'
// 组件内：
const [params] = useSearchParams()
const docId = params.get('docId')
const batchId = params.get('batchId')
const [pasteText, setPasteText] = useState('')
const [docName, setDocName] = useState('')
```

2. `useEffect` 追加：`batchId` 存在时 `api.get(`/api/imports/${batchId}`)` → `setBatch` 并按默认勾选规则初始化 `selected`。

3. 默认勾选规则抽函数（首次导入与更新共用）：

```tsx
const defaultSelected = (items: ImportItemOut[]) =>
  items
    .filter((i) => i.is_valid && i.align_action !== 'unchanged')
    .filter((i) => !(i.align_action === 'disappeared' && i.is_form))
    .map((i) => i.id)
```

4. 提交函数：普通模式 `FormData` 带 `domain/type/doc_name` + `file` 或 `text`，POST `/api/imports`；更新模式只带 `file` 或 `text`，POST `/api/source-docs/${docId}/update`。

5. 第一步 UI：`Tabs` 两项——`粘贴文本`（`Input`（文件名，更新模式隐藏）+ `Input.TextArea rows={16}` + 提交按钮）/ `上传 .md`（原 `Upload.Dragger`，beforeUpload 里读 `docName` 一并提交）。

6. 条目行 `Tag`：有 `align_action` 时显示 `<Tag color={ALIGN_COLOR[item.align_action]}>{ALIGN_LABEL[item.align_action]}</Tag>`；`disappeared && is_form` 行附注 `<Typography.Text type="secondary">表单添加，未在新文本中</Typography.Text>`；`unchanged` 行 Checkbox `disabled`。

7. 底部汇总（更新模式）：

```tsx
const count = (a: string) =>
  batch.items.filter((i) => selected.includes(i.id) && i.align_action === a).length
// 文案：预计：新增 {count('new')} 条、更新 {count('changed')} 条、下架 {count('disappeared')} 条
```

8. 入库成功后步骤条推进：`confirm` 成功后 `setStep(2)`（引入 `const [step, setStep] = useState(0)`，`Steps current={step}`，载入批次时 `setStep(1)`），确认按钮成功后 `disabled`，并给"查看知识文件"按钮（更新/建档模式 `navigate(`/source-docs/${resp.data.source_doc_id}`)`）。这同时修掉本次实测发现的"步骤条停在第 2 步、按钮可重复点"问题。

- [ ] **Step 2: 构建 + Commit**

Run: `npm run build`
Expected: 编译通过

```bash
git add frontend/src/pages/ImportPreview.tsx
git commit -m "feat: 导入页双 tab 粘贴/上传，更新模式对齐徽标与汇总"
```

---

### Task 12: SourceDocList 页

**Files:**
- Modify(重写占位): `frontend/src/pages/SourceDocList.tsx`

**Interfaces:**
- Consumes: `GET /api/source-docs`、Task 10 类型。

- [ ] **Step 1: 实现**

```tsx
import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Input, Popconfirm, Select, Space, Table, Tag, message } from 'antd'
import { useNavigate } from 'react-router-dom'

import {
  api,
  DomainItem,
  KNOWLEDGE_TYPES,
  SOURCE_LABEL,
  SourceDocItem,
  TYPE_COLOR,
} from '../api/client'

// 知识文件列表（spec §4.2）：domain → 文件 → 条目 的中间层视图
export default function SourceDocList() {
  const navigate = useNavigate()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [domain, setDomain] = useState<string>()
  const [status, setStatus] = useState<string>()
  const [q, setQ] = useState('')
  const [items, setItems] = useState<SourceDocItem[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => {
      const list = resp.data.items.filter((d: DomainItem) => d.code !== 'common')
      setDomains(list)
      if (list.length) setDomain(list[0].code)
    })
  }, [])

  const load = useCallback(() => {
    if (!domain) return
    setLoading(true)
    api
      .get('/api/source-docs', { params: { domain, status, q: q || undefined } })
      .then((resp) => setItems(resp.data.items))
      .finally(() => setLoading(false))
  }, [domain, status, q])

  useEffect(load, [load])

  const offline = async (id: number) => {
    const resp = await api.post(`/api/source-docs/${id}/offline`)
    message.success(`已下架 ${resp.data.archived_entries} 条并归档文件`)
    load()
  }

  const renew = async (id: number) => {
    const resp = await api.post(`/api/source-docs/${id}/renew`, {})
    message.success(`已续期 ${resp.data.renewed} 条至 ${resp.data.expire_date}`)
    load()
  }

  return (
    <Card
      title={
        <Space>
          知识文件
          <Select
            style={{ width: 180 }}
            value={domain}
            onChange={setDomain}
            options={domains.map((d) => ({ value: d.code, label: d.code }))}
          />
        </Space>
      }
      extra={
        <Button type="primary" onClick={() => navigate('/knowledge/import')}>
          + 新建（粘贴 / 上传）
        </Button>
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'active', label: '在用' },
            { value: 'archived', label: '已归档' },
          ]}
        />
        <Input.Search placeholder="搜索名称" style={{ width: 220 }} onSearch={setQ} allowClear />
      </Space>
      <Table<SourceDocItem>
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={false}
        columns={[
          {
            title: '名称',
            dataIndex: 'name',
            render: (name, r) => <a onClick={() => navigate(`/source-docs/${r.id}`)}>{name}</a>,
          },
          {
            title: '类型',
            dataIndex: 'type',
            render: (t) => <Tag color={TYPE_COLOR[t]}>{t}</Tag>,
          },
          { title: '来源', dataIndex: 'source', render: (s) => SOURCE_LABEL[s] ?? s },
          {
            title: '条目数（在架/总）',
            render: (_, r) => `${r.entry_published}/${r.entry_total}`,
          },
          {
            title: '状态',
            dataIndex: 'status',
            render: (s) =>
              s === 'active' ? <Tag color="green">在用</Tag> : <Tag>已归档</Tag>,
          },
          { title: '最近更新', dataIndex: 'updated_at', render: (v) => v.slice(0, 19).replace('T', ' ') },
          {
            title: '操作',
            render: (_, r) => (
              <Space>
                <a onClick={() => navigate(`/source-docs/${r.id}`)}>查看</a>
                {r.status === 'active' && (
                  <>
                    <a onClick={() => navigate(`/knowledge/import?docId=${r.id}`)}>更新</a>
                    <a onClick={() => renew(r.id)}>整体续期</a>
                    <Popconfirm
                      title={`将下架该文件全部 ${r.entry_published} 条在架条目并归档，确认？`}
                      onConfirm={() => offline(r.id)}
                    >
                      <a style={{ color: '#cf1322' }}>整体下架</a>
                    </Popconfirm>
                  </>
                )}
              </Space>
            ),
          },
        ]}
      />
    </Card>
  )
}
```

- [ ] **Step 2: 构建 + Commit**

Run: `npm run build`

```bash
git add frontend/src/pages/SourceDocList.tsx
git commit -m "feat: 知识文件列表页"
```

---

### Task 13: SourceDocDetail 页（条目/全文/历史 + 在线编辑）

**Files:**
- Modify(重写占位): `frontend/src/pages/SourceDocDetail.tsx`

**Interfaces:**
- Consumes: `GET /api/source-docs/{id}`、`/content`、`POST /update`、`/renew`、`/offline`、`PATCH`；Task 11 的 `?docId=&batchId=` 约定。

- [ ] **Step 1: 实现**

```tsx
import { useCallback, useEffect, useState } from 'react'
import {
  Button, Card, Descriptions, Input, Popconfirm, Space, Table, Tabs, Tag, Typography, message,
} from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import {
  api, ALIGN_LABEL, SOURCE_LABEL, SourceDocDetailOut, STATUS_LABEL, TYPE_COLOR,
} from '../api/client'

// 知识文件详情（spec §4.2/§4.3）：条目视图 / 全文视图（可在线编辑）/ 变更历史
export default function SourceDocDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [doc, setDoc] = useState<SourceDocDetailOut | null>(null)
  const [markdown, setMarkdown] = useState('')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [newName, setNewName] = useState('')

  const load = useCallback(() => {
    api.get(`/api/source-docs/${id}`).then((r) => setDoc(r.data))
    api.get(`/api/source-docs/${id}/content`).then((r) => setMarkdown(r.data.markdown))
  }, [id])
  useEffect(load, [load])

  if (!doc) return null
  const active = doc.status === 'active'

  const submitEdit = async () => {
    const data = new FormData()
    data.append('text', draft)
    const resp = await api.post(`/api/source-docs/${id}/update`, data)
    navigate(`/knowledge/import?docId=${id}&batchId=${resp.data.id}`)
  }

  const rename = async () => {
    await api.patch(`/api/source-docs/${id}`, { name: newName })
    message.success('已重命名')
    setRenaming(false)
    load()
  }

  return (
    <Card
      title={
        <Space>
          {renaming ? (
            <Space.Compact>
              <Input defaultValue={doc.name} onChange={(e) => setNewName(e.target.value)} />
              <Button type="primary" onClick={rename}>保存</Button>
            </Space.Compact>
          ) : (
            <>
              {doc.name}
              {active && <a style={{ fontSize: 13 }} onClick={() => setRenaming(true)}>重命名</a>}
            </>
          )}
          <Tag color={TYPE_COLOR[doc.type]}>{doc.type}</Tag>
          {active ? <Tag color="green">在用</Tag> : <Tag>已归档</Tag>}
        </Space>
      }
      extra={
        active && (
          <Space>
            <Button onClick={() => navigate(`/knowledge/import?docId=${id}`)}>粘贴新版本</Button>
            <Button
              onClick={async () => {
                const r = await api.post(`/api/source-docs/${id}/renew`, {})
                message.success(`已续期 ${r.data.renewed} 条`)
                load()
              }}
            >
              整体续期
            </Button>
            <Popconfirm title="下架全部在架条目并归档文件？" onConfirm={async () => {
              await api.post(`/api/source-docs/${id}/offline`)
              load()
            }}>
              <Button danger>整体下架</Button>
            </Popconfirm>
          </Space>
        )
      }
    >
      <Descriptions size="small" column={4} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="domain">{doc.domain}</Descriptions.Item>
        <Descriptions.Item label="来源">{SOURCE_LABEL[doc.source]}</Descriptions.Item>
        <Descriptions.Item label="条目">{doc.entry_published}/{doc.entry_total}</Descriptions.Item>
        <Descriptions.Item label="最近更新">{doc.updated_at.slice(0, 19).replace('T', ' ')}</Descriptions.Item>
      </Descriptions>

      <Tabs
        items={[
          {
            key: 'entries',
            label: `条目（${doc.entries.length}）`,
            children: (
              <Table
                rowKey="kid"
                size="small"
                pagination={false}
                dataSource={doc.entries}
                columns={[
                  { title: '#', dataIndex: 'doc_seq', width: 50 },
                  {
                    title: '标题', dataIndex: 'title',
                    render: (t, r) => <a onClick={() => navigate(`/knowledge/${r.kid}`)}>{t}</a>,
                  },
                  { title: 'kid', dataIndex: 'kid' },
                  { title: '状态', dataIndex: 'status', render: (s) => STATUS_LABEL[s] ?? s },
                  { title: '版本', dataIndex: 'version', render: (v) => `v${v}` },
                  { title: '过期日期', dataIndex: 'expire_date' },
                ]}
              />
            ),
          },
          {
            key: 'content',
            label: '全文',
            children: editing ? (
              <>
                <Input.TextArea rows={24} value={draft} onChange={(e) => setDraft(e.target.value)} />
                <Space style={{ marginTop: 12 }}>
                  <Button type="primary" onClick={submitEdit}>提交（进入对齐预览）</Button>
                  <Button onClick={() => setEditing(false)}>取消</Button>
                </Space>
              </>
            ) : (
              <>
                {active && (
                  <Space style={{ marginBottom: 12 }}>
                    <Button onClick={() => { setDraft(markdown); setEditing(true) }}>编辑全文</Button>
                    <Button onClick={() => { navigator.clipboard.writeText(markdown); message.success('已复制') }}>
                      复制全文
                    </Button>
                  </Space>
                )}
                <Typography.Paragraph>
                  <pre style={{ whiteSpace: 'pre-wrap', background: '#fafafa', padding: 16 }}>{markdown}</pre>
                </Typography.Paragraph>
              </>
            ),
          },
          {
            key: 'history',
            label: `变更历史（${doc.batches.length}）`,
            children: (
              <Table
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={doc.batches}
                columns={[
                  { title: '时间', dataIndex: 'created_at', render: (v) => v.slice(0, 19).replace('T', ' ') },
                  { title: '操作人', dataIndex: 'created_by' },
                  { title: '方式', dataIndex: 'origin', render: (o) => (o === 'manual' ? '粘贴/编辑' : '上传') },
                  {
                    title: '变化',
                    dataIndex: 'stats',
                    render: (s: Record<string, number>) =>
                      Object.entries(s).map(([k, n]) => `${ALIGN_LABEL[k] ?? k} ${n}`).join('，') || '—',
                  },
                ]}
              />
            ),
          },
        ]}
      />
    </Card>
  )
}
```

- [ ] **Step 2: 构建 + Commit**

Run: `npm run build`

```bash
git add frontend/src/pages/SourceDocDetail.tsx
git commit -m "feat: 知识文件详情页（条目/全文/历史，在线编辑入口）"
```

---

### Task 14: KnowledgeForm 所属文件必选 + KnowledgeList/Detail 来源文件列

**Files:**
- Modify: `frontend/src/pages/KnowledgeForm.tsx`
- Modify: `frontend/src/pages/KnowledgeList.tsx`
- Modify: `frontend/src/pages/KnowledgeDetail.tsx`

- [ ] **Step 1: KnowledgeForm**

domain+type 已选后请求 `api.get('/api/source-docs', { params: { domain, type, status: 'active' } })` 填充"所属知识文件"选择器（`Form.Item name="source_doc_id" label="所属知识文件" rules={[{ required: !newDoc }]}`），旁边 `Checkbox`"新建文件"切换成 `Form.Item name="new_doc_name"` 输入框（required）。提交 body 带 `source_doc_id` 或 `new_doc_name`（互斥，二选一）。domain/type 变化时清空已选文件。

- [ ] **Step 2: KnowledgeList / KnowledgeDetail**

List 的 columns 在"负责人"前插入：

```tsx
  {
    title: '来源文件',
    render: (_, r) =>
      r.source_doc?.name ? (
        <a onClick={() => navigate(`/source-docs/${r.source_doc.id}`)}>{r.source_doc.name}</a>
      ) : ('—'),
  },
```

Detail 的溯源卡（现显示 source_type/source_ref 处）加一行"所属文件"，同样链接到 `/source-docs/{id}`。

- [ ] **Step 3: 构建 + Commit**

Run: `npm run build`

```bash
git add frontend/src/pages
git commit -m "feat: 表单必选所属文件，列表/详情展示来源文件"
```

---

### Task 15: dev 库迁移 + 端到端浏览器验收 + 文档同步

**Files:**
- Modify: `backend/doc/modules/console.md`、`backend/doc/modules/storage.md`（补 source_doc 模型与接口口径）
- Create: `backend/doc/decisions/ADR-0022-source-doc.md`（决策：文件=管理容器；统一源文档模型 manual/upload/feishu；所有条目必须属于文件；对齐消失默认下架、表单条目除外）

- [ ] **Step 1: dev 库迁移与存量验证**

```bash
cd kg-platform/backend && alembic upgrade head
psql postgresql://kg:kg@localhost:5433/kg -c "SELECT id,name,domain_code,type,source FROM source_doc; SELECT kid,source_doc_id,doc_seq FROM knowledge ORDER BY kid;"
```

Expected: 已确认批次各一个文件（faq-conform.md 等）；faq-fo-0001 归入 free-order 的「手工录入」文件；全部 knowledge 行 source_doc_id/doc_seq 非空。

- [ ] **Step 2: 重启后端 + 浏览器全流程验收**

后端重启加载新代码后，用浏览器（Chrome MCP）依次验证并截图确认：
1. 侧边栏「知识文件」→ 列表显示存量文件与条目计数；
2. 粘贴文本 tab：贴一份 2 条 FAQ + 文件名 → 拆分预览 → 入库 → 跳文件详情；
3. 详情页三 tab：条目有序、全文拼合正确、历史有首次导入记录；
4. 编辑全文：删 1 条改 1 条加 1 条 → 提交 → 对齐预览出现四类徽标、汇总正确 → 确认 → 条目状态/版本/doc_seq 符合预期（changed 版本+1、disappeared 归档）；
5. 表单创建：选已有文件 + 新建文件两条路各走一遍，知识列表"来源文件"列可点；
6. 整体续期/整体下架 + 归档后详情只读；
7. 步骤条走到"入库"，确认按钮不可重复提交。

- [ ] **Step 3: 文档同步 + 最终全量测试**

按 `backend/doc/README.md` 的规范补模块文档与 ADR-0022（中文，带溯源块）。

Run: `cd kg-platform/backend && pytest -q && cd ../frontend && npm run build`
Expected: 全绿 + 编译通过

- [ ] **Step 4: Commit**

```bash
git add backend/doc
git commit -m "docs: source_doc 模块文档与 ADR-0022"
```
