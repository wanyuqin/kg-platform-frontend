# P2 飞书接入 Code Review · 细化版

> 配套文档：[feishu-sync.md](./modules/feishu-sync.md)
>
> Review 时间：2026-07-05
> 范围：P2 飞书同步全量实现（9 个核心文件 + 3 个 alembic 迁移 + 9 个测试，约 1700 行新代码）
> 风格：每个 issue 给"在哪 → 现在是什么 → 改成什么 → 怎么测"四段式

---

## 测试与 lint 状态

| 项 | 结果 |
|---|---|
| `pytest tests/test_feishu_*.py` | **94 passed** |
| `pytest tests/test_dev_login.py::test_disabled_by_default_404` | 1 failed（master 已有，与本次改动无关） |
| `ruff check` (feishu + scheduler + mq + tests) | **All checks passed**（自动修复 4 个 unused import） |
| 全测试 (359 total) | 358 passed + 1 dev_login（master 既有） |

---

## 上一轮 Issue 追踪

| 上一轮 | 状态 |
|---|---|
| 🔴 B1 disappeared 异常吞掉 | ❌ **仍未修复**（sync.py:416-425 仍是 `except (InvalidTransition, Exception)`） |
| 🟡 I1 连接池 | ❌ 未改 |
| 🟡 I2 429 重试 | ❌ 未改 |
| 🟡 I3 sync_status 枚举对齐 | ✅ **完美修复**：`mirror_business_status()` + `_TECH_TO_BIZ` 映射 |
| 🟡 I4 加密签名 | ❌ 未改 |
| 🟡 I5 risk_matrix | ✅ **完美修复**：完整 7 维度评分 + ReviewTask + 飞书卡片 |
| 🟢 N1 synced_reference skipped 矛盾 | ❌ 未改 |

---

## 🔴 Blocking（必须合并前修）

### B1 · `disappeared` 分支吞所有异常

**位置**：`backend/app/feishu/sync.py:416-425`

**上下文代码**：

```python
416:         if aligned_item.align_action == "disappeared":
417:             row = await session.get(Knowledge, aligned_item.match_kid)
418:             if row and row.status != Status.ARCHIVED:
419:                 try:
420:                     row.status = transition(Status(row.status), Event.ARCHIVE)
421:                     await viking.delete(build_uri(row.domain_code, row.type, row.kid))
422:                     published += 1            # ← 命名误导：实际是 archived
423:                 except (InvalidTransition, Exception) as exc:  # ← 吞所有异常
424:                     failed += 1
425:             continue
```

**问题**：

- `except (InvalidTransition, Exception)` 等价于 `except Exception`——日志丢失，PG 已 archive 但 OpenViking 残留 → **孤儿文件**
- `published += 1` 名不副实（应是 `archived`），跟后续 `published` 计数混在一起，前端展示错乱

**修复方案**（~12 行）：

```python
if aligned_item.align_action == "disappeared":
    row = await session.get(Knowledge, aligned_item.match_kid)
    if not row or row.status == Status.ARCHIVED:
        continue
    try:
        row.status = transition(Status(row.status), Event.ARCHIVE)
    except InvalidTransition as exc:
        logger.warning(
            "feishu disappeared invalid transition kid=%s status=%s err=%s",
            row.kid, row.status, exc,
        )
        failed += 1
        continue
    try:
        await viking.delete(build_uri(row.domain_code, row.type, row.kid))
    except Exception:
        logger.exception("viking.delete failed for disappeared kid=%s", row.kid)
        # 回滚 PG archive 状态 + 落 review_task（让运维人工兜底）
        row.status = Status(row.status)  # 状态机不支持 revert，先用原状态查
        # ↑ 这里建议补一个 reverse_archive Event
        failed += 1
        continue
    archived += 1
    continue
```

需要 `Status` 加一个 `ACTIVE` 或 `Event` 加一个 `REACTIVATE`（看状态机设计）。

**配套测试**（`tests/test_feishu_sync.py` 新增）：

```python
async def test_phase2_disappeared_viking_delete_failure_rolls_back(monkeypatch):
    # mock viking.delete 抛异常
    # assert: row.status 没变 ARCHIVED + failed=1
```

---

### B2 · `trigger_feishu_sync` 阻塞 phase2

**位置**：`backend/app/console/feishu_sync.py:312-373`（手动同步端点）

**上下文代码**：

```python
312: @router.post("/source-docs/{doc_id}/sync")
313: async def trigger_feishu_sync(
...
329:     try:
330:         result = await sync_feishu_doc(
331:             session,
332:             doc_id,
...
338:             run_phase2=True,    # ← 同步阻塞等 phase2
339:         )
...
363:     await session.commit()
364:     return {
365:         "phase1": _phase1_out(result.phase1),
366:         "phase2": {            # ← phase2 跑完才返回
367:             "published": result.phase2.published if result.phase2 else 0,
...
```

对比 `create_feishu_source_doc`（line 296-297）：

```python
296:     await session.commit()
297:     enqueue_phase2(background_tasks, doc.id, phase1, user.user_id, viking=viking)
```

**问题**：

- bind 走 BackgroundTask，manual 走同步阻塞——**行为不一致**
- 中/高风险触发飞书卡片 + review_task 可能 60s+，触发 HTTP 网关超时
- 用户点"立即同步"按钮实际要等 phase2 全跑完才能看到结果

**修复方案**（重构 trigger_feishu_sync）：

```python
@router.post("/source-docs/{doc_id}/sync")
async def trigger_feishu_sync(
    doc_id: int,
    background_tasks: BackgroundTasks,  # ← 加这个
    user: ConsoleUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    client: FeishuClient = Depends(get_feishu_client),
    oss: OssClient = Depends(get_oss_client),
    viking: VikingClient = Depends(get_viking),
):
    ...
    try:
        phase1 = await sync_feishu_doc_phase1(  # ← 只跑 phase1
            session, doc_id, client=client, oss=oss,
            triggered_by="manual", actor_user_id=user.user_id,
        )
    except FeishuPermissionError as exc:
        await session.commit()
        return JSONResponse(status_code=403, content={...})

    if not phase1.ok:
        await session.commit()
        return JSONResponse(status_code=422, content={...})

    await session.commit()
    enqueue_phase2(background_tasks, doc_id, phase1, user.user_id, viking=viking)

    return {
        "phase1": _phase1_out(phase1),
        "sync_status": "syncing",
        "next": "phase2 running",   # ← 行为跟 bind 一致
    }
```

**配套测试**（`tests/test_console_feishu.py` 新增）：

```python
async def test_manual_sync_runs_phase2_in_background():
    # mock phase2 抛异常
    # assert: HTTP 200 立即返回 + 后台 phase2 失败不会影响响应
```

---

## 🟡 Important（P2 验收前修）

### I-A · MQ 消费幂等检查只挡正在 sync 的消息

**位置**：`backend/app/storage/mq/consumer.py:130-150`

**上下文代码**：

```python
130: async def _should_skip_message(session: AsyncSession, message: FeishuEventMessage) -> bool:
131:     """同 doc 正在 syncing 时跳过重放消息（§11.2 幂等）。"""
...
148:     return sync.sync_status == "syncing" or (
149:         doc is not None and doc.sync_status == "syncing"
150:     )
```

**问题**：

- 只能挡 `sync_status == "syncing"` 的窗口期
- 场景：phase1 写完 import_batch → phase2 还没开始的 `sync_status="idle"` 间隙 → MQ 重放 → **再走一次 phase1+phase2 重复入库**
- 已经有 `has_sync_receipt`（sync.py:114）了，但没用上

**修复方案**（~15 行）：

```python
async def _should_skip_message(session: AsyncSession, message: FeishuEventMessage) -> bool:
    sync = (
        await session.execute(
            select(SyncState).where(SyncState.source_doc_id == message.source_doc_id)
        )
    ).scalar_one_or_none()
    if sync is None:
        logger.warning("feishu mq skip unknown doc=%s", message.source_doc_id)
        return True
    if sync.feishu_doc_token != message.feishu_doc_token:
        logger.warning(
            "feishu mq token mismatch doc=%s msg=%s state=%s",
            message.source_doc_id, message.feishu_doc_token, sync.feishu_doc_token,
        )

    # 1. 正在 sync：跳过（保持原行为）
    if sync.sync_status == "syncing":
        return True

    # 2. 新增：sync_feishu_doc 内部已经写 receipt 了，但消息可能在 phase1/phase2 之间到达
    #    —— 在 handle_message 入口预查 content_hash 是否已处理
    #    但 content_hash 来自响应体不在 MQ 里，所以这里只能查"当前 sync_state.content_hash"
    if sync.content_hash and sync.sync_status == "idle" and sync.last_error is None:
        logger.info("feishu mq skip already-synced doc=%s", message.source_doc_id)
        return True  # 已经成功同步过

    return False
```

更彻底的方案：让 MQ message 携带 `content_hash` 字段（飞书事件 head 里 revision_id 或自己 sha256 文件），用 `has_sync_receipt(source_doc_id, content_hash)` 查。但这要改 message schema，先做上面这个保守版。

---

### I-B · `archive_cleanup` 的 `viking.delete` 失败无重试

**位置**：`backend/app/scheduler/feishu_archive_cleanup.py:91-95`

**上下文代码**：

```python
91:     for uri in viking_deletes:
92:         try:
93:             await viking.delete(uri)
94:         except Exception:
95:             logger.exception("viking delete failed during archive purge uri=%s", uri)
```

**问题**：

- 同 B1 模式（吞异常）
- PG 已删 `Knowledge` 表，但 OpenViking 残留 → **永久孤儿文件**（运维只能手动登 OpenViking 删）
- 失败的 URI 只 log，没有持久化，下一轮 scheduler 不会重试

**修复方案**（两步）：

**Step 1**：建一张孤儿文件表（`alembic/versions/0007_*.py`）：

```python
class VikingCleanupFailed(Base):
    __tablename__ = "viking_cleanup_failed"
    id: Mapped[int] = mapped_column(primary_key=True)
    uri: Mapped[str] = mapped_column(String(512), unique=True)
    last_error: Mapped[str]
    retry_count: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[datetime]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

**Step 2**：改 cleanup（`feishu_archive_cleanup.py:91-95`）：

```python
for uri in viking_deletes:
    try:
        await viking.delete(uri)
    except Exception as exc:
        logger.exception("viking delete failed uri=%s", uri)
        # 落库，下次跑重试
        existing = await session.execute(
            select(VikingCleanupFailed).where(VikingCleanupFailed.uri == uri)
        )
        if existing.scalar_one_or_none() is None:
            session.add(VikingCleanupFailed(
                uri=uri,
                last_error=str(exc),
                retry_count=0,
                next_retry_at=datetime.now(UTC) + timedelta(hours=1),
            ))
```

再写一个 scheduler job：`viking_cleanup_failed_retry.py`，每 6h 跑一次，捞 `next_retry_at < now AND retry_count < 5`。

---

### I-C · MQ 重试用 `asyncio.sleep + create_task` 不可靠

**位置**：`backend/app/storage/mq/consumer.py:114-127`

**上下文代码**：

```python
114:     async def _schedule_retry(self, message: FeishuEventMessage) -> Literal["retry", "dlq"]:
115:         if message.retry_count >= MAX_RETRIES:
116:             await self._producer.publish_dlq(message)
117:             return "dlq"
118:         delay = RETRY_DELAYS_SEC[min(message.retry_count, len(RETRY_DELAYS_SEC) - 1)]
119:         retry_msg = message.with_retry()
120: 
121:         async def _republish() -> None:
122:             if delay > 0 and self._settings.feishu_mq_backend != "memory":
123:                 await asyncio.sleep(delay)
124:             await self._producer.publish(retry_msg)
125: 
126:         asyncio.create_task(_republish())    # ← fire-and-forget
127:         return "retry"
```

**问题**：

- `asyncio.create_task` 创建后台任务，**进程 kill 后任务消失**——sleep 期间被 SIGTERM → 消息**永远丢失**
- 返回 "retry" 但 MQ 里根本没消息，consumer 端不知道
- 短延迟（60s）出问题的概率小，长延迟（900s）极易踩到

**修复方案**（短期 + 长期）：

**短期**（立即可上）：

```python
async def _schedule_retry(self, message: FeishuEventMessage) -> Literal["retry", "dlq"]:
    if message.retry_count >= MAX_RETRIES:
        await self._producer.publish_dlq(message)
        return "dlq"
    delay = RETRY_DELAYS_SEC[min(message.retry_count, len(RETRY_DELAYS_SEC) - 1)]
    if self._settings.feishu_mq_backend == "memory" or delay == 0:
        await self._producer.publish(message.with_retry())
        return "retry"
    # 生产环境保守走 DLQ，避免 create_task 丢消息
    logger.warning(
        "feishu mq retry would sleep %ds; routing to DLQ instead doc=%s",
        delay, message.source_doc_id,
    )
    await self._producer.publish_dlq(message)
    return "dlq"
```

**长期**（P3 + RocketMQ 延迟消息）：

```python
# producer.publish 支持延迟投递
await self._producer.publish_delayed(message.with_retry(), delay_ms=delay * 1000)
```

RocketMQ 4.x 用 `set_start_deliver_time`，5.x 用 `Message.delay_time_level`。

---

### I-D · `archive_source_doc` 事务半完成

**位置**：`backend/app/feishu/sync.py:584-601`

**上下文代码**：

```python
584: async def archive_source_doc(session: AsyncSession, source_doc_id: int) -> None:
585:     """飞书文档删除事件：归档 source_doc（§8.3 / D6）。"""
586:     doc = await session.get(SourceDoc, source_doc_id)
587:     if doc is None:
588:         return
589:     now = datetime.now(UTC)
590:     doc.status = "archived"
591:     doc.archived_at = now
592:     doc.sync_status = "archived"
593:     sync = (
594:         await session.execute(select(SyncState).where(SyncState.source_doc_id == source_doc_id))
595:     ).scalar_one_or_none()
596:     if sync:
597:         sync.sync_status = "error"              # ← 一边 archived
598:         sync.last_error = "feishu_doc_deleted"
599:         doc.last_sync_error = sync.last_error   # ← 双写 N2
600:     await session.flush()   # ← 没 commit，靠调用方
601:     logger.info("feishu source_doc archived id=%s", source_doc_id)
```

**问题**：

- 自身只 flush，依赖 `event_dispatch.py:77` 调用方 commit
- 调用链 `dispatch_feishu_event` → `console/feishu_sync.py:450` commit 一切看着没问题
- 但如果 line 596 之后到 line 600 之间抛错 → `doc.status="archived"` 但 `sync.sync_status` 没改 → **半完成**

**修复方案**（~10 行）：

```python
async def archive_source_doc(session: AsyncSession, source_doc_id: int) -> None:
    doc = await session.get(SourceDoc, source_doc_id)
    if doc is None:
        return
    try:
        now = datetime.now(UTC)
        doc.status = "archived"
        doc.archived_at = now
        doc.sync_status = "archived"
        sync = (
            await session.execute(
                select(SyncState).where(SyncState.source_doc_id == source_doc_id)
            )
        ).scalar_one_or_none()
        if sync:
            sync.sync_status = "archived"   # ← 跟 doc 对齐（解决 N5）
            sync.last_error = None
            sync.last_sync_at = now
        await session.flush()
        logger.info("feishu source_doc archived id=%s", source_doc_id)
    except Exception:
        await session.rollback()
        logger.exception("archive_source_doc failed id=%s", source_doc_id)
        raise
```

---

### I-E · `dispatch_feishu_event` delete 分支没回滚保护

**位置**：`backend/app/feishu/event_dispatch.py:71-78`

**上下文代码**：

```python
71:     if event_type == DELETE_EVENT:
72:         row = await find_sync_by_file_token(session, file_token)
73:         if row is None:
74:             logger.info("feishu delete ignored unknown token=%s", file_token)
75:             return "ignored"
76:         doc, _sync = row
77:         await archive_source_doc(session, doc.id)   # ← 抛错时没 rollback
78:         return "archived"
```

**问题**：

- 如果 `archive_source_doc` 内部抛错（I-D 修了之后会 re-raise），调用方 `console/feishu_sync.py:450` `await session.commit()` 会失败，但 archive_source_doc 已经改了 ORM 对象状态——下次 commit 行为不可预期

**修复方案**（`event_dispatch.py:71-78`）：

```python
if event_type == DELETE_EVENT:
    row = await find_sync_by_file_token(session, file_token)
    if row is None:
        logger.info("feishu delete ignored unknown token=%s", file_token)
        return "ignored"
    doc, _sync = row
    try:
        await archive_source_doc(session, doc.id)
        return "archived"
    except Exception:
        await session.rollback()
        logger.exception("archive_source_doc failed for token=%s", file_token)
        return "error"   # ← 新增 return 标签
```

`console/feishu_sync.py:449-450`：

```python
action = await dispatch_feishu_event(session, ...)
if action == "error":
    await session.rollback()
else:
    await session.commit()
```

---

## 🟢 Nit（可选）

### N1 · `_render_synced_reference` 已渲染却 append skipped

**位置**：`backend/app/feishu/docx_to_markdown.py:165-167`

```python
165:     if btype == 50:
166:         ctx.skipped_blocks.append(block_id)         # ← skipped 是"完全没渲染"
167:         return "> [引用同步块暂不支持，请本地化内容]\n"  # ← 已经渲染了
```

**修**（去掉 line 166）：

```python
165:     if btype == 50:
166:         return "> [引用同步块暂不支持，请本地化内容]\n"
```

`skipped_blocks` 语义保持"完全没渲染"，风险矩阵的 `skipped_blocks / total_blocks` 维度更准确。

---

### N2 · `doc.last_sync_error` / `sync.last_error` 双写

**位置**：`backend/app/feishu/sync.py:599`（archive_source_doc 内）

`mirror_business_status`（sync.py:66-70）已经在镜像 `sync.last_error → doc.last_sync_error`，这里 line 599 又显式双写一次。

**修**：删 line 599，让 mirror 统一管：

```python
if sync:
    sync.sync_status = "error"
    sync.last_error = "feishu_doc_deleted"
    mirror_business_status(doc, sync)   # ← 加这一行
```

注意：现在 archive_source_doc 没调 `mirror_business_status`，所以 line 599 是补丁——更好的方案是把 archive 状态语义统一（见 N5）。

---

### N3 · `CreateFeishuDocBody.type` 校验重复

**位置**：`backend/app/console/feishu_sync.py:63` + `:200-201`

```python
63:     type: str = Field(pattern="^(faq|sop|policy|product|case|term)$")   # 硬编码
...
200:     if body.type not in KNOWLEDGE_TYPES:
201:         raise errors.invalid_argument(f"unknown type: {body.type}")    # 二次校验
```

**修**（用 Literal 让 Pydantic 兜底）：

```python
from typing import Literal
from app.domain.kid import KNOWLEDGE_TYPES

class CreateFeishuDocBody(BaseModel):
    domain: str
    type: Literal["faq", "sop", "policy", "product", "case", "term"]
    name: str
    feishu_url: str
```

或者更优雅：

```python
_KNOWLEDGE_TYPE_VALUES = tuple(KNOWLEDGE_TYPES)
type: Literal[_KNOWLEDGE_TYPE_VALUES]  # type: ignore[valid-type]
```

删 line 200-201 的二次校验。

---

### N4 · `_TECH_TO_BIZ` 映射 `quarantine` 是 dead code

**位置**：`backend/app/feishu/sync.py:55`

```python
50: _TECH_TO_BIZ = {
51:     "registered": "pending",
52:     "syncing": "syncing",
53:     "idle": "success",
54:     "error": "failed",
55:     "quarantine": "failed",    # ← 代码里没有任何地方写 "quarantine"
56: }
```

**修**：删 line 55，或者如果 ADR 里规划了 P3 quarantine 状态，留 TODO 注释：

```python
# TODO(P3): quarantine 用于敏感词命中隔离，落地后取消注释
```

---

### N5 · archive 状态语义割裂

**位置**：`backend/app/feishu/sync.py:592-599`

```python
592:     doc.sync_status = "archived"             # 业务枚举：archived
...
597:         sync.sync_status = "error"           # 技术枚举：error
598:         sync.last_error = "feishu_doc_deleted"
```

前端 `_sync_status_out`（`console/feishu_sync.py:90-110`）展示的时候：

- `sync_status = "archived"`（来自 doc）
- `technical_status = "error"`（来自 sync）

**展示混乱**，前端不知道信哪个。

**修**：跟 I-D 一起改，archive 状态两边对齐：

```python
if sync:
    sync.sync_status = "archived"   # 跟 doc 一致
    sync.last_error = None
    sync.last_sync_at = now
```

`_TECH_TO_BIZ` 也得加：

```python
_TECH_TO_BIZ = {
    "registered": "pending",
    "syncing": "syncing",
    "idle": "success",
    "error": "failed",
    "archived": "archived",   # ← 新增
    # "quarantine": "failed",   # ← N4 删掉
}
```

---

## 🔧 上一轮未修问题（标注当前行号）

| Issue | 位置（精确） | 当前状态 |
|---|---|---|
| I1 连接池 | `backend/app/feishu/client.py:57-60` + `:115-118` | 每次请求 new `AsyncClient` |
| I2 429 重试 | `backend/app/feishu/client.py:68-76` + `:124-132` | 只重试 5xx |
| I4 加密签名 | `backend/app/feishu/event.py:43-49` | encrypt_key 传了但 body 没先 AES 解密 |

**I4 修复建议**（新增 ~25 行）：

```python
# event.py 新增
def _try_decrypt(raw_body: bytes, encrypt_key: str) -> bytes | None:
    """如果开了加密通道，body 是 base64(AES-256-CBC(json))；失败返回 None。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        decoded = json.loads(raw_body)
        if "encrypt" not in decoded:
            return None
        ciphertext = base64.b64decode(decoded["encrypt"])
        # 飞书约定：前 16 字节 = IV
        iv = ciphertext[:16]
        ct = ciphertext[16:]
        cipher = Cipher(algorithms.AES(encrypt_key.encode()), modes.CBC(iv))
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(ct) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(plaintext) + unpadder.finalize()
        return plaintext
    except Exception:
        return None

def verify_signature(*, timestamp, nonce, body, signature, encrypt_key=None):
    key = encrypt_key if encrypt_key is not None else get_settings().lark_encrypt_key
    if not key or not signature:
        return
    # 先尝试解密 body
    decrypted = _try_decrypt(body, key)
    if decrypted is not None:
        body = decrypted
    content = f"{timestamp}{nonce}{key}".encode() + body
    expected = hashlib.sha256(content).hexdigest()
    if expected != signature:
        raise FeishuError("事件签名校验失败")
```

并加 `cryptography` 到 `pyproject.toml`（先看有没有，没有就加）。

---

## 💡 Suggestion（替代方案）

- **重试改用 RocketMQ 延迟消息**：`msg.set_start_deliver_time(int((time.time() + delay) * 1000))`
- **MQ 消费幂等检查升级**：用 `has_sync_receipt(source_doc_id, content_hash)` 替代 sync_status 检查
- **archive_source_doc 改为同步阻塞** + 加 BackgroundTask（跟 bind 一致）
- **`doc.last_sync_error` / `sync.last_error` 单一来源**：让 mirror_business_status 负责镜像，不要双写
- **统一 sync_state 枚举**：把 sync_state 的技术枚举（registered/syncing/idle/error/quarantine）和 source_doc 的业务枚举（pending/syncing/success/failed/awaiting_auth/...）在 ORM 层用一个 Enum 强类型化
- **feishu_poll_tick 应该用 advisory lock 防止并发**：现在看代码没有跨进程保护，多个 scheduler 实例会重复触发同步

---

## 🎉 Praise（做得好的地方）

- **架构跟文档严格对齐**：`feishu-sync.md` §4-§7 的契约（DocResolver / Block 渲染 / Media 转存 / 两阶段同步）全部落实，包括 D9.4 双轨感知的 `permission_check.ok` 字段也写出来了
- **风险矩阵完整落地**（I5 修复）：7 维度评分 + `score_risk()` + `publish_mode_for_risk()` + `risk_note_for_score()` + `ReviewTask` 落库 + `send_review_card()` 推飞书卡片——**P2 验收"低风险自动生效 / 中高风险审核"完整跑通**
- **D4 字段归属设计优秀**：`mirror_business_status()` + `_TECH_TO_BIZ` 映射表，把 sync_state 的技术枚举（registered/idle/...）自动镜像到 source_doc 的业务枚举（pending/success/...）—— **I3 完美修复**
- **授权状态机清晰**（D9.4 实现）：`AUTH_WAIT_STATUSES` frozenset + `should_auth_poll()` + `mark_auth_timeout()` + `is_auth_timed_out()` 四函数分层，**双轨感知（按钮 + 60s 轮询 24h 超时）落地**
- **alembic 0006 数据回填**：迁移脚本里直接 `UPDATE source_doc sd SET ... FROM sync_state ss WHERE ss.source_doc_id = sd.id`—— **线上数据平滑迁移**
- **MQ 抽象**：`MqBackend` Protocol + `MemoryMqBackend`（测试用）+ `RocketMqBackend`（生产用），单测不依赖真 RocketMQ
- **archive_cleanup 完整**（D6）：删 ReviewTask + KnowledgeVersion + status 转移 + viking.delete + 物理删条目 + 保留 source_doc
- **event_dispatch 干净**：4 种事件类型分派（SYNC_TRIGGER_EVENTS / DELETE_EVENT / 其他 ignore）+ 找不到 source_doc 静默 return
- **MQ 消息协议**：FeishuEventMessage dataclass + `to_bytes/from_bytes/with_retry` 三方法，序列化用 json 简单可靠
- **控制台 API 完整**（§13.2）：5 个 endpoint（resolve / create / sync / sync-status / sync-history）+ event 回调，含 422/403/201 各 HTTP 状态码映射
- **测试质量高**：每个模块都有单测，用 `httpx.MockTransport` 完全脱离真实飞书；`test_feishu_sync.py` 用 `RecordingViking` 验证 PG 写入
- **错误映射表设计清晰**：`exceptions.py` 集中管理 5 种飞书错误码 → 平台错误码 + `action_guide` 文案，跟文档 §4.4 表完全一致
- **限流实现简洁**：28 行 `AsyncTokenBucket` 完整覆盖令牌桶语义，单测覆盖正常/空桶/并发三种场景
- **异常分层合理**：`FeishuError` → `FeishuPermissionError` + `FeishuSyncError`（业务码）分层清楚
- **OssClient 用 `asyncio.to_thread`** 隔离 boto3 同步调用，正确

---

## 📋 修改优先级 & 工作量

| 优先级 | Issue | 文件 | 行数 | 工作量 |
|---|---|---|---|---|
| 🔴 现在 | B1 disappeared | sync.py:416-425 | ~15 | 30min + 1 测试 |
| 🔴 现在 | B2 manual 阻塞 | feishu_sync.py:312-373 | ~30 | 45min + 1 测试 |
| 🟡 本周 | I-A 幂等 | consumer.py:130-150 | ~10 | 20min + 1 测试 |
| 🟡 本周 | I-B cleanup 重试 | feishu_archive_cleanup.py + 新表 + 新 job | ~80 | 2h（含迁移） |
| 🟡 本周 | I-C MQ sleep 保守降级 | consumer.py:114-127 | ~15 | 15min |
| 🟡 本周 | I-D archive 事务 | sync.py:584-601 | ~15 | 30min |
| 🟡 本周 | I-E dispatch 回滚 | event_dispatch.py:71-78 | ~10 | 15min |
| 🟢 顺手 | N1/N2/N3/N4/N5 | 各 1-5 行 | ~10 | 15min |
| 🟡 P3 | I1/I2/I4 | client.py + event.py | ~60 | 2h（含测试） |

---

## 📊 Decision

**🔄 Request Changes** —

**必改（合并前）**：

- 🔴 B1 + B2（30 行 + 30 行，1h 干完）

**P2 验收前**：

- 🟡 I-A / I-B / I-C / I-D / I-E（建议一鼓作气改完，工作量约 4-5h）

**顺手**：

- 🟢 N1-N5（5 个一行修改，15min）

**P3 攒一起**：

- I1 / I2 / I4（client + event，2h）
