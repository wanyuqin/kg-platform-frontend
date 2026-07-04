# kg-platform-frontend

知识管理平台控制台（kg-console）：React 18 + Ant Design 5 + Vite + TypeScript。

配套后端仓库：[kg-platform-backend](https://github.com/wanyuqin/kg-platform-backend)

## 本地启动

需先启动后端（默认 `http://localhost:8000`）。

```bash
npm install
npm run dev        # http://localhost:5173
```

自定义后端地址：

```bash
VITE_BACKEND_TARGET=http://localhost:8001 npm run dev
```

## 构建

```bash
npm run build
```

本地无飞书凭证时，先在后端开启 dev-login，再访问：

`http://localhost:5173/api/auth/dev-login?user_id=dev&platform_admin=true`
