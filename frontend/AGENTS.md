# AGENTS.md

本文件为 AI 编码代理（Claude Code 等）在 frontend 目录工作时的指引。

知识管理平台控制台前端（kg-console）：React 18 + Ant Design 5 + Vite + TypeScript。页面范围与交互契约来自设计文档 7.2 的九页线框（P1 只做其中六页），后端接口契约见 [../backend/doc/modules/console.md](../backend/doc/modules/console.md) 与 [gateway.md](../backend/doc/modules/gateway.md)。

## 常用命令

```bash
npm install
npm run dev        # http://localhost:5173，/api 与 /v1 代理到 :8000（需先起后端）
npm run build      # tsc -b && vite build（类型检查即构建门禁）
npm run preview
```

暂无测试与 lint 配置；类型错误会挡 build，改完跑一次 `npm run build` 验证。

## 架构要点

- **路由集中在 `src/App.tsx`**：侧边栏菜单 + Routes 一处维护，一页一文件放 `src/pages/`。P1 页面：知识列表（主页）、知识详情、表单录入、拆分预览确认（ImportPreview）、domain 列表、审计查询。P2 页面：飞书绑定向导（FeishuBind）、飞书同步面板（FeishuSyncPanel）、审核待办（ReviewQueue）、打标（GovernanceTagging）。
- **请求必须走 `src/api/client.ts` 的 `api` 实例**：它统一处理后端错误 envelope（`{error: {code, message, request_id}}`）并弹 antd message，页面里不要再手写 axios 或重复错误提示。
- **共享常量复用 client.ts**：六类知识类型 `KNOWLEDGE_TYPES`、状态中文映射 `STATUS_LABEL` 已定义，新页面直接引用，不要再抄一份。
- 登录态：走 `src/auth/` 模块（Context + 路由守卫 + `/login` 页）。后端 `/api/auth/*` 种 HttpOnly `kg_session` cookie，请求无需手动带 token。
  - 本地开发默认 `dev` 模式（Login 页 dev-login 表单）；生产默认 `oauth`（飞书）。可通过 `VITE_AUTH_PROVIDER=dev|oauth|sso` 切换；公司 SSO 预留 `sso` + `VITE_SSO_LOGIN_URL`。
  - 会话探测：`GET /api/auth/me`；登出：`POST /api/auth/logout`。

## 约定

- UI 文案与代码注释一律**中文**；注释引用"设计 7.2""技术设计文档 七"等章节格式沿用。
- 知识类型、状态、错误码等枚举以后端为准（backend/doc 及 `app/errors.py`），前端只做展示映射，不要自造口径。
- 组件风格：直接用 antd 组件与内联布局，当前无全局样式体系；保持与现有页面一致，不引入额外 UI / 状态管理库。
