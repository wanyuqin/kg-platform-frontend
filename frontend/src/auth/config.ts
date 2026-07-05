/** 登录提供方：dev 本地后门 | oauth 飞书 | sso 公司 SSO（预留） */
export type AuthProvider = 'dev' | 'oauth' | 'sso'

function resolveProvider(): AuthProvider {
  const env = import.meta.env.VITE_AUTH_PROVIDER as AuthProvider | undefined
  if (env === 'dev' || env === 'oauth' || env === 'sso') return env
  return import.meta.env.DEV ? 'dev' : 'oauth'
}

/** OAuth / SSO 整页跳转 URL；dev 模式返回 null */
export function getLoginRedirectUrl(): string | null {
  const provider = resolveProvider()
  if (provider === 'sso') {
    const url = import.meta.env.VITE_SSO_LOGIN_URL
    if (!url) throw new Error('VITE_SSO_LOGIN_URL 未配置')
    return url
  }
  if (provider === 'oauth') return '/api/auth/login'
  return null
}

/** 是否展示开发环境登录表单 */
export function isDevLoginEnabled(): boolean {
  return resolveProvider() === 'dev'
}

/** 外部登录按钮文案 */
export function getExternalLoginLabel(): string {
  const provider = resolveProvider()
  if (provider === 'sso') return '公司 SSO 登录'
  return '飞书登录'
}

/** 构建 dev-login 跳转 URL（整页导航，后端 Set-Cookie） */
export function buildDevLoginUrl(params: {
  user_id: string
  name?: string
  platform_admin?: boolean
  returnUrl?: string
}): string {
  const q = new URLSearchParams()
  q.set('user_id', params.user_id)
  if (params.name) q.set('name', params.name)
  if (params.platform_admin) q.set('platform_admin', 'true')
  const base = `/api/auth/dev-login?${q.toString()}`
  // dev-login 后端固定 302 /；returnUrl 由前端登录页在回调后处理
  void params.returnUrl
  return base
}
