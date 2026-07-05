import axios from 'axios'
import { message } from 'antd'

// 后端错误 envelope：{ error: { code, message, request_id } }（技术设计文档 6.1）
export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string }
}

export const api = axios.create({ timeout: 15_000, withCredentials: true })

api.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const body = err.response?.data as ApiErrorBody | undefined
    if (err.response?.status === 401) {
      const onLoginPage = window.location.pathname === '/login'
      const isMeProbe = String(err.config?.url ?? '').includes('/api/auth/me')
      if (!onLoginPage && !isMeProbe) {
        message.error('未登录或会话过期，请先登录')
        const returnUrl = window.location.pathname + window.location.search
        window.location.assign(`/login?returnUrl=${encodeURIComponent(returnUrl)}`)
      }
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

export const TYPE_LABEL: Record<string, string> = Object.fromEntries(
  KNOWLEDGE_TYPES.map((t) => [t.value, t.label]),
)

export const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  pending_review: '待审核',
  published: '已发布',
  expired: '已过期',
  archived: '已下架',
}

export const INDEX_STATE_LABEL: Record<string, string> = {
  none: '未索引',
  indexing: '索引中',
  ready: '可检索',
  failed: '索引失败（重试中）',
}

export const INDEX_STATE_TAG_COLOR: Record<string, string> = {
  none: 'default',
  indexing: 'processing',
  ready: 'green',
  failed: 'red',
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

export interface SourceDocItem {
  id: number
  name: string
  domain: string
  type: string
  source: 'manual' | 'upload' | 'feishu'
  status: 'active' | 'archived'
  entry_total: number
  entry_published: number
  index_ready: number
  index_indexing: number
  index_failed: number
  updated_at: string
  sync_status?: FeishuSyncStatus | null
  feishu_url?: string | null
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
  source: 'manual' | 'upload' | 'feishu' | null
  source_title: string | null
  effective_date: string
  expire_date: string
  updated_at: string
  hits_30d?: number
  source_doc: { id: number; name: string | null; source: 'manual' | 'upload' | 'feishu' | null; title: string | null }
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
  created_by: string
  created_by_name: string
  revoked_at: string | null
  calls_30d: number
  last_used_at: string | null
}

export interface UserDomainRole {
  code: string
  name: string
  role: string
}

export interface ConsoleUserItem {
  user_id: string
  name: string
  is_platform_admin: boolean
  created_at: string
  domains: UserDomainRole[]
  active_key_count: number
}

export interface ConsoleUserDetail extends ConsoleUserItem {
  keys: DomainKeyItem[]
}

export interface DomainMemberItem {
  user_id: string
  name: string
  role: string
}

export interface KnowledgeDetailOut extends KnowledgeItem {
  content: string
  fields: Record<string, string>
  versions: { version: number; created_by: string; created_at: string; content_hash: string }[]
  hits_30d: number
}

/** 拉取指定类型的标准 Markdown 模板（GET /api/templates/{type}.md） */
export async function fetchTemplate(type: string): Promise<string> {
  const resp = await api.get(`/api/templates/${type}.md`, { responseType: 'text' })
  return resp.data as string
}

/** 下载标准模板为本地 .md 文件 */
export async function downloadTemplate(type: string): Promise<void> {
  const content = await fetchTemplate(type)
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${type}-template.md`
  link.click()
  URL.revokeObjectURL(url)
}

/** 过期日期剩余天数；负数为已过期天数 */
export function daysUntil(dateStr: string): number {
  return Math.ceil((new Date(dateStr).getTime() - Date.now()) / 86_400_000)
}

export interface ValidationFinding {
  rule: string
  level: 'blocking' | 'warning'
  message: string
  meta?: Record<string, unknown>
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

/** 知识域下拉选项：中文名称 + 技术标识 */
export function domainSelectOption(d: Pick<DomainItem, 'code' | 'name'>) {
  return { value: d.code, label: `${d.name}（${d.code}）` }
}

/** 按 code 解析知识域展示名；找不到时回退 code */
export function domainDisplayLabel(
  domains: Pick<DomainItem, 'code' | 'name'>[],
  code: string,
): string {
  const hit = domains.find((d) => d.code === code)
  return hit ? `${hit.name}（${hit.code}）` : code
}

export interface ImportItemOut {
  id: number
  seq: number
  title: string | null
  is_valid: boolean
  validation: ValidationFinding[]
  result_kid: string | null
  fields: Record<string, string>
  align_action: string
  match_kid: string | null
  is_form: boolean
}

export interface ImportBatchStats {
  total: number
  valid: number
  duplicate_in_batch: number
  requires_review: boolean
}

export interface ImportBatchOut {
  id: number
  domain: string
  type: string
  file_name: string
  status: string
  items: ImportItemOut[]
  stats: ImportBatchStats
  template_url: string
  source_doc_id: number | null
}

export interface ImportConfirmSummary {
  succeeded: number
  pending_review: number
  failed_duplicate: number
  failed_blocking: number
  failed_other: number
}

export interface ImportConfirmResult {
  item_id: number
  kid: string | null
  error: string | null
  status?: 'pending_review' | 'published'
}

export interface ImportConfirmOut {
  id: number
  status: string
  source_doc_id: number | null
  requires_review: boolean
  summary: ImportConfirmSummary
  results: ImportConfirmResult[]
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

// ---- 飞书同步（P2，对接 feishu_sync API） ----

export type FeishuSyncStatus =
  | 'pending'
  | 'syncing'
  | 'success'
  | 'failed'
  | 'awaiting_auth'
  | 'permission_revoked'
  | 'auth_timeout'
  | 'archived'

export const SYNC_STATUS_LABEL: Record<FeishuSyncStatus, string> = {
  pending: '待同步',
  syncing: '同步中',
  success: '已同步',
  failed: '同步失败',
  awaiting_auth: '待授权',
  permission_revoked: '权限已撤销',
  auth_timeout: '授权超时',
  archived: '已归档',
}

export const SYNC_STATUS_COLOR: Record<FeishuSyncStatus, string> = {
  pending: 'default',
  syncing: 'processing',
  success: 'green',
  failed: 'red',
  awaiting_auth: 'gold',
  permission_revoked: 'red',
  auth_timeout: 'orange',
  archived: 'default',
}

/** 轮询中的非终态 */
export const FEISHU_SYNC_POLLING: FeishuSyncStatus[] = ['pending', 'syncing']

export interface FeishuPermissionCheck {
  ok: boolean
  error_code?: string
  error_message?: string
  action_guide?: string
}

export interface FeishuResolveResult {
  resolved: boolean
  feishu_doc_type: string
  feishu_doc_token: string
  feishu_url: string
  title: string
  permission_check: FeishuPermissionCheck
}

export interface FeishuPhase1Result {
  source_doc_id: number
  import_batch_id: number
  parsed_items: number
  blocking_count: number
  skipped_blocks: number
  ok: boolean
}

export interface FeishuCreateResult {
  id: number
  name: string
  source: string
  phase1?: FeishuPhase1Result
  sync_status?: FeishuSyncStatus
  permission_check?: FeishuPermissionCheck
  errors?: ValidationFinding[]
}

export interface FeishuSyncDuplicateInfo {
  kid: string
  title?: string | null
  status?: string | null
  source_doc_id?: number | null
  source_doc_name?: string | null
}

export interface FeishuSyncFailureItem {
  seq: number
  reason: string
  reason_label?: string
  title?: string | null
  align_action?: string | null
  duplicate?: FeishuSyncDuplicateInfo
  validation?: ValidationFinding[]
  kid?: string | null
  detail?: string | null
}

export interface FeishuSyncErrorDetail {
  code: string
  message: string
  breakdown?: Record<string, number>
  failures?: FeishuSyncFailureItem[]
}

export interface FeishuSyncStatusOut {
  sync_status: FeishuSyncStatus | null
  technical_status: string | null
  last_sync_at: string | null
  last_sync_error: string | null
  last_sync_error_detail: FeishuSyncErrorDetail | null
  feishu_url: string | null
  feishu_doc_token: string | null
  feishu_doc_type: string | null
  feishu_title: string | null
  content_hash: string | null
  source_doc_status: string | null
  archived_at: string | null
  awaiting_auth_since: string | null
  sync_interval_sec: number | null
}

export interface FeishuSyncHistoryItem {
  import_batch_id: number
  status: string
  created_by: string
  created_at: string
}

export interface FeishuSyncHistory {
  source_doc_id: number
  items: FeishuSyncHistoryItem[]
  current: FeishuSyncStatusOut
}

export async function resolveFeishuDoc(feishuUrl: string): Promise<FeishuResolveResult> {
  const resp = await api.post<FeishuResolveResult>('/api/source-docs/resolve', { feishu_url: feishuUrl })
  return resp.data
}

export async function createFeishuSourceDoc(body: {
  domain: string
  type: string
  name: string
  feishu_url: string
}) {
  return api.post<FeishuCreateResult>('/api/source-docs', body)
}

export async function fetchFeishuSyncStatus(docId: number): Promise<FeishuSyncStatusOut> {
  const resp = await api.get<FeishuSyncStatusOut>(`/api/source-docs/${docId}/sync-status`)
  return resp.data
}

export async function fetchFeishuSyncHistory(docId: number): Promise<FeishuSyncHistory> {
  const resp = await api.get<FeishuSyncHistory>(`/api/source-docs/${docId}/sync-history`)
  return resp.data
}

export async function triggerFeishuSync(docId: number) {
  return api.post(`/api/source-docs/${docId}/sync`)
}

// ---- 审核待办（P2） ----

export type ReviewTaskType = 'risk' | 'manual_fill' | 'conflict'
export type ReviewTaskStatus = 'pending' | 'approved' | 'rejected' | 'expired'

export const REVIEW_TASK_TYPE_LABEL: Record<ReviewTaskType, string> = {
  risk: '风险审核',
  manual_fill: '人工补齐',
  conflict: '冲突处理',
}

export const REVIEW_TASK_STATUS_LABEL: Record<ReviewTaskStatus, string> = {
  pending: '待处理',
  approved: '已通过',
  rejected: '已驳回',
  expired: '已过期',
}

export interface ReviewTaskKnowledgeSummary {
  title: string
  status: string
  type: string
  risk_note: string | null
}

export interface ReviewTaskItem {
  id: number
  kid: string
  domain: string
  task_type: ReviewTaskType
  status: ReviewTaskStatus
  risk_note: string | null
  submitter_id: string
  submitter_name: string | null
  reviewer_id: string | null
  reject_reason: string | null
  created_at: string
  knowledge: ReviewTaskKnowledgeSummary | null
}

export interface ReviewTaskListOut {
  items: ReviewTaskItem[]
  total: number
  page: number
  page_size: number
}

export async function fetchReviewTasks(params: {
  domain?: string
  task_type?: ReviewTaskType
  status?: ReviewTaskStatus
  page?: number
  page_size?: number
}): Promise<ReviewTaskListOut> {
  const resp = await api.get<ReviewTaskListOut>('/api/review-tasks', { params })
  return resp.data
}

export async function approveReviewTask(id: number) {
  return api.post(`/api/review-tasks/${id}/approve`)
}

export async function rejectReviewTask(id: number, reason: string) {
  return api.post(`/api/review-tasks/${id}/reject`, { reason })
}

export async function patchKnowledgeMeta(
  kid: string,
  body: { tags?: string[]; owner?: string; expire_date?: string },
) {
  return api.patch(`/api/knowledge/${kid}/meta`, body)
}
