import axios from 'axios'
import { message } from 'antd'

// 后端错误 envelope：{ error: { code, message, request_id } }（技术设计文档 6.1）
export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string }
}

export const api = axios.create({ timeout: 15_000 })

api.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const body = err.response?.data as ApiErrorBody | undefined
    if (err.response?.status === 401) {
      message.error('未登录或会话过期，请先登录（本地开发：/api/auth/dev-login?user_id=dev&platform_admin=true）')
    } else if (body?.error) {
      message.error(`${body.error.message}（${body.error.code} / ${body.error.request_id}）`)
    } else {
      message.error('网络错误或服务不可用')
    }
    return Promise.reject(err)
  },
)

export const KNOWLEDGE_TYPES = [
  { value: 'faq', label: 'FAQ 问答' },
  { value: 'sop', label: '操作流程 SOP' },
  { value: 'policy', label: '政策 / 规则' },
  { value: 'product', label: '产品 / 功能说明' },
  { value: 'case', label: '案例 / 故障处理' },
  { value: 'term', label: '术语定义' },
] as const

export const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  pending_review: '待审核',
  published: '已发布',
  expired: '已过期',
  archived: '已下架',
}

export const INDEX_STATE_LABEL: Record<string, string> = {
  none: '—',
  indexing: '索引中',
  ready: '可检索',
  failed: '索引失败（重试中）',
}

// 六类模板段名（与后端附录 A / content_hash.SECTION_ORDER 一致）
export const TYPE_SECTIONS: Record<string, { name: string; required: boolean }[]> = {
  faq: [
    { name: '标准问法', required: true },
    { name: '相似问法', required: true },
    { name: '标准答案', required: true },
    { name: '适用条件', required: true },
    { name: '例外情况', required: false },
  ],
  sop: [
    { name: '目标与适用场景', required: true },
    { name: '前置条件', required: true },
    { name: '操作步骤', required: true },
    { name: '异常与分支处理', required: true },
    { name: '完成标志', required: true },
    { name: '回滚方式', required: false },
    { name: '注意事项', required: false },
  ],
  policy: [
    { name: '一句话摘要', required: true },
    { name: '适用范围', required: true },
    { name: '规则条款', required: true },
    { name: '例外条款', required: true },
    { name: '生效 / 失效时间', required: true },
    { name: '罚则与违规处理', required: false },
    { name: '制度依据来源', required: false },
  ],
  product: [
    { name: '功能定义', required: true },
    { name: '适用版本 / 套餐', required: true },
    { name: '能力边界', required: true },
    { name: '使用入口', required: false },
    { name: '限制与配额', required: false },
    { name: '常见误解澄清', required: false },
  ],
  case: [
    { name: '问题现象', required: true },
    { name: '触发条件与根因', required: true },
    { name: '排查步骤', required: true },
    { name: '解决方案', required: true },
    { name: '影响范围', required: false },
    { name: '预防措施', required: false },
  ],
  term: [
    { name: '术语名', required: true },
    { name: '定义', required: true },
    { name: '同义词 / 别名', required: true },
    { name: '使用示例', required: false },
    { name: '易混淆术语辨析', required: false },
  ],
}

// type 六色徽章（线稿⑤图例：faq绿 / policy橙 / sop蓝 / product紫 / case红 / term灰）
export const TYPE_COLOR: Record<string, string> = {
  faq: 'green',
  policy: 'orange',
  sop: 'blue',
  product: 'purple',
  case: 'red',
  term: 'default',
}

// ---- 接口返回类型（与后端响应字段一一对应） ----

export interface KnowledgeItem {
  kid: string
  title: string
  domain: string
  type: string
  tags: string[]
  status: string
  index_state: string
  version: number
  owner: string
  source_type: string
  source_ref: string
  source_url: string | null
  effective_date: string
  expire_date: string
  updated_at: string
  hits_30d?: number
}

export interface KnowledgeStats {
  total: number
  by_type: Record<string, number>
}

export interface DomainKeyItem {
  key_id: string
  agent_name: string
  qps_limit: number
  status: string
  domain_whitelist: string[]
  created_at: string
}

export interface KnowledgeDetailOut extends KnowledgeItem {
  content: string
  fields: Record<string, string>
  versions: { version: number; created_by: string; created_at: string; content_hash: string }[]
  hits_30d: number
}

/** 过期日期剩余天数；负数为已过期天数 */
export function daysUntil(dateStr: string): number {
  return Math.ceil((new Date(dateStr).getTime() - Date.now()) / 86_400_000)
}

export interface ValidationFinding {
  rule: string
  level: 'blocking' | 'warning'
  message: string
}

export interface SubmitResult {
  kid: string | null
  status: string
  validation: ValidationFinding[]
  version?: number
  index_state?: string
}

export interface DomainItem {
  code: string
  short_code: string
  name: string
  default_ttl_days: number
  type_topk: Record<string, number>
  created_at: string
  stats?: { total: number; by_type: Record<string, number>; agents: number }
}

export interface ImportItemOut {
  id: number
  seq: number
  title: string | null
  is_valid: boolean
  validation: ValidationFinding[]
  result_kid: string | null
  fields: Record<string, string>
}

export interface ImportBatchOut {
  id: number
  domain: string
  type: string
  file_name: string
  status: string
  items: ImportItemOut[]
  template_url: string
}

export interface AuditLogItem {
  ts: string
  key_id: string
  action: string
  query: string | null
  hits: { kid: string; version: number; score: number }[] | null
  excluded_expired: number | null
  kid: string | null
  version: number | null
  latency_ms: number
}
