# AGENTS.md

本文件为 AI 编码代理（Claude Code 等）在 backend 目录工作时的指引。

知识管理平台后端：FastAPI 单体（Gateway `/v1/*` + 控制台 API `/api/*`）+ 独立 scheduler 进程。设计权威源是两份飞书文档（见仓库根 README），仓库内提炼版在 [doc/](doc/README.md)——改代码前先看对应模块文档；**已拍板决策（[doc/decisions/](doc/decisions/README.md)，ADR-0001~0021）不要推翻重提**，确需变更走 doc/README.md 的 ADR 流程。

## 常用命令

```bash
# 依赖中间件（在仓库根目录；PG :5433 + Redis :6379 + OpenViking :1933）
docker compose -f ../docker-compose.dev.yml up -d

uv sync                                        # 安装依赖（含 dev 组）
cp .env.example .env                           # 首次
uv run alembic upgrade head                    # 建表（P1 全部 DDL + common 域种子数据）
uv run uvicorn app.main:app --reload --port 8000   # api 进程
uv run python -m app.scheduler.main            # scheduler 进程（可选）

uv run pytest                                  # 全部测试
uv run pytest tests/test_kid.py                # 单文件
uv run pytest tests/test_state_machine.py -k "archived"  # 单用例
uv run ruff check . && uv run ruff format .    # lint / 格式化（line-length 100）
```

验证：`curl http://localhost:8000/healthz`（探活 PG + Redis，依赖故障报 degraded 不 5xx）；接口文档 http://localhost:8000/docs

## 架构要点

- **进程结构**：api 进程内 Gateway 与控制台**同进程、按路由划分**（`app/gateway/router.py` / `app/console/router.py`），共享 `app/domain/` 与 `app/storage/`——不要在两个 router 里各写一份业务逻辑。scheduler 是独立进程（`app/scheduler/main.py`），单实例、所有任务必须幂等。
- **模块 ↔ 文档**：gateway / console / pipeline / domain / storage / scheduler / audit 各模块的设计契约见 `doc/modules/<模块名>.md`，其中标注了对应的飞书章节与 ADR。
- **PostgreSQL 是唯一事实来源**（ADR-0002）：元数据与状态以 PG 为准，OpenViking 只存 published 正文；search 结果必须回查 PG 过滤。DDL 变更只走 Alembic 迁移脚本（当前基线 `alembic/versions/0001_init_p1_schema.py`），枚举用 VARCHAR + CHECK。
- **状态机唯一入口**：知识状态迁移一律经 `app/domain/state_machine.py` 的 `transition()`，非法迁移抛业务异常；不要在别处直接改 status。转移表已一次写全（P2/P3 行也在），实现只放开 P1 行。
- **错误响应**：统一 envelope `{error: {code, message, request_id}}`，一律用 `app/errors.py` 的便捷构造器（`invalid_argument` / `unauthorized` / `not_found` / `rate_limited` / `upstream_unavailable`）；越权与不存在统一 404（ADR-0013），不要区分。
- **配置**：`app/config.py` pydantic-settings，环境变量前缀 `KG_`，清单见 `.env.example`（与技术设计文档 12.1 对齐）；新配置项两处同步加。

## 约定

- 代码注释与文档一律**中文**；注释中"技术设计文档 x.x""设计 x.x"分别指两份飞书文档的章节，沿用此引用格式。
- 阶段标注 P1 / P2 / P3（含义见 [doc/roadmap.md](doc/roadmap.md)）：P2/P3 的预留（如 sync_state / review_task 表、pending_review 流转）只留结构与标注，不实现、不建表。
- P1 流水线全同步、零 LLM、无消息队列（ADR-0006 / ADR-0017），不要引入 Celery / RocketMQ / 异步任务框架。
- 测试：pytest（asyncio_mode=auto，testpaths=tests）。确定性逻辑（校验器、切分、状态机、kid 取号）每条规则配正反用例；新增校验规则必须同步补测试。
