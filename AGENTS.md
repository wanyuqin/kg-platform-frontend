# AGENTS.md

本文件为 AI 编码代理（Claude Code 等）在本仓库工作时的指引。

知识管理平台控制台前端（kg-console）：React 18 + Ant Design 5 + Vite + TypeScript。页面范围与交互契约来自设计文档 7.2 的九页线框（P1 只做其中六页），后端接口契约见 [backend/doc/modules/console.md](https://github.com/wanyuqin/kg-platform-backend/blob/master/doc/modules/console.md) 与 [gateway.md](https://github.com/wanyuqin/kg-platform-backend/blob/master/doc/modules/gateway.md)。

## 常用命令

```bash
npm install
npm run dev        # http://localhost:5173，/api 与 /v1 代理到 :8000（需先起后端）
npm run build      # tsc -b && vite build（类型检查即构建门禁）
npm run preview
```

暂无测试与 lint 配置；类型错误会挡 build，改完跑一次 `npm run build` 验证。

## 架构要点

- **路由集中在 `src/App.tsx`**：侧边栏菜单 + Routes 一处维护，一页一文件放 `src/pages/`。当前 P1 页面：知识列表（主页）、知识详情、表单录入、拆分预览确认（ImportPreview）、domain 列表、审计查询。审核待办 / 打标 / 飞书同步管理是 P2，不要提前建页面。
- **请求必须走 `src/api/client.ts` 的 `api` 实例**：它统一处理后端错误 envelope（`{error: {code, message, request_id}}`）并弹 antd message，页面里不要再手写 axios 或重复错误提示。
- **共享常量复用 client.ts**：六类知识类型 `KNOWLEDGE_TYPES`、状态中文映射 `STATUS_LABEL` 已定义，新页面直接引用，不要再抄一份。
- 登录态：控制台走飞书 OAuth + session cookie（后端 `/api/auth/*`，尚未接入），请求无需手动带 token。

## 约定

- UI 文案与代码注释一律**中文**；注释引用"设计 7.2""技术设计文档 七"等章节格式沿用。
- 知识类型、状态、错误码等枚举以后端为准（backend/doc 及 `app/errors.py`），前端只做展示映射，不要自造口径。
- 组件风格：直接用 antd 组件与内联布局，当前无全局样式体系；保持与现有页面一致，不引入额外 UI / 状态管理库。
