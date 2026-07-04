import axios from 'axios'
import { message } from 'antd'

// 后端错误 envelope：{ error: { code, message, request_id } }（技术设计文档 6.1）
export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string }
}

export const api = axios.create({ timeout: 10_000 })

api.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const body = err.response?.data as ApiErrorBody | undefined
    if (body?.error) {
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
