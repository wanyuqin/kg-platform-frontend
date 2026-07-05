/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AUTH_PROVIDER?: 'dev' | 'oauth' | 'sso'
  readonly VITE_SSO_LOGIN_URL?: string
  readonly VITE_BACKEND_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
