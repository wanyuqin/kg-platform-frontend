import { Alert, Button, Card, Descriptions, Input, Select, Space, Steps, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import {
  FeishuResolveResult,
  KNOWLEDGE_TYPES,
  TYPE_COLOR,
  ValidationFinding,
  createFeishuSourceDoc,
  domainSelectOption,
  DomainItem,
  resolveFeishuDoc,
  api,
} from '../api/client'

// 飞书绑定向导（设计 7.3）：URL 预览 → 权限预检 → 建档 → 跳转详情
export default function FeishuBind() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [feishuUrl, setFeishuUrl] = useState('')
  const [preview, setPreview] = useState<FeishuResolveResult | null>(null)
  const [domain, setDomain] = useState<string | undefined>(searchParams.get('domain') ?? undefined)
  const [type, setType] = useState<string>('faq')
  const [name, setName] = useState('')
  const [step, setStep] = useState(0)
  const [resolving, setResolving] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [permissionDenied, setPermissionDenied] = useState<FeishuResolveResult | null>(null)
  const [validationErrors, setValidationErrors] = useState<ValidationFinding[]>([])

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => {
      const list = resp.data.items as DomainItem[]
      setDomains(list)
      const requested = searchParams.get('domain') ?? undefined
      setDomain(list.some((d) => d.code === requested) ? requested : list[0]?.code)
    })
  }, [searchParams])

  const resolve = async () => {
    if (!feishuUrl.trim()) {
      message.warning('请输入飞书文档 URL')
      return
    }
    setResolving(true)
    setPermissionDenied(null)
    setValidationErrors([])
    try {
      const data = await resolveFeishuDoc(feishuUrl.trim())
      setPreview(data)
      setName(data.title || '')
      setStep(data.permission_check.ok ? 1 : 0)
      if (!data.permission_check.ok) {
        setPermissionDenied(data)
      }
    } finally {
      setResolving(false)
    }
  }

  const submit = async () => {
    if (!domain || !name.trim()) {
      message.warning('请填写名称并选择知识域')
      return
    }
    setSubmitting(true)
    setPermissionDenied(null)
    setValidationErrors([])
    try {
      const resp = await createFeishuSourceDoc({
        domain,
        type,
        name: name.trim(),
        feishu_url: preview?.feishu_url || feishuUrl.trim(),
      })
      if (resp.status === 201) {
        message.success('飞书文档已注册，正在同步')
        navigate(`/source-docs/${resp.data.id}`)
        return
      }
    } catch (err: unknown) {
      const ax = err as { response?: { status?: number; data?: Record<string, unknown> } }
      const status = ax.response?.status
      const data = ax.response?.data
      if (status === 403 && data?.permission_check) {
        setPermissionDenied(data as unknown as FeishuResolveResult)
        setStep(0)
        return
      }
      if (status === 422) {
        setValidationErrors((data?.errors as ValidationFinding[]) ?? [])
        if (data?.id) {
          message.warning('内容校验未通过，已建档可稍后修复')
          navigate(`/source-docs/${data.id}`)
        }
        return
      }
    } finally {
      setSubmitting(false)
    }
  }

  const perm = permissionDenied?.permission_check ?? preview?.permission_check
  const isKbError = perm?.error_code === 'feishu_app_not_in_kb'

  return (
    <Card title="注册飞书文档">
      <Steps
        current={step}
        style={{ marginBottom: 24, maxWidth: 560 }}
        items={[{ title: '解析 URL' }, { title: '确认信息' }, { title: '完成' }]}
      />

      {step === 0 && (
        <Space direction="vertical" style={{ width: '100%', maxWidth: 640 }} size="middle">
          <Input.TextArea
            rows={2}
            placeholder="粘贴飞书文档链接（docx / wiki）"
            value={feishuUrl}
            onChange={(e) => setFeishuUrl(e.target.value)}
          />
          <Button type="primary" loading={resolving} onClick={() => void resolve()}>
            解析并预检权限
          </Button>
          {preview && preview.permission_check.ok && (
            <Alert type="success" showIcon message={`已识别：${preview.title}`} />
          )}
          {perm && !perm.ok && (
            <Alert
              type={isKbError ? 'error' : 'warning'}
              showIcon
              message={perm.error_message || '权限不足'}
              description={
                <Space direction="vertical">
                  {perm.action_guide && <Typography.Text>{perm.action_guide}</Typography.Text>}
                  <Typography.Text type="secondary">
                    请按指引完成授权后，重新解析 URL 再提交。
                  </Typography.Text>
                </Space>
              }
            />
          )}
        </Space>
      )}

      {step >= 1 && preview?.permission_check.ok && (
        <Space direction="vertical" style={{ width: '100%', maxWidth: 640 }} size="middle">
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="飞书标题">{preview.title}</Descriptions.Item>
            <Descriptions.Item label="文档类型">{preview.feishu_doc_type}</Descriptions.Item>
            <Descriptions.Item label="链接">
              <a href={preview.feishu_url} target="_blank" rel="noreferrer">
                {preview.feishu_url}
              </a>
            </Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Select
              style={{ width: 280 }}
              value={domain}
              onChange={setDomain}
              options={domains.map(domainSelectOption)}
            />
            <Select
              style={{ width: 160 }}
              value={type}
              onChange={setType}
              options={KNOWLEDGE_TYPES.map((t) => ({ value: t.value, label: t.label }))}
            />
            <Tag color={TYPE_COLOR[type]}>{type}</Tag>
          </Space>
          <Input
            placeholder="知识文件名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          {validationErrors.length > 0 && (
            <Alert
              type="error"
              message="阶段一校验失败"
              description={
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {validationErrors.map((e, i) => (
                    <li key={i}>{e.message}</li>
                  ))}
                </ul>
              }
            />
          )}
          <Space>
            <Button onClick={() => setStep(0)}>上一步</Button>
            <Button type="primary" loading={submitting} onClick={() => void submit()}>
              确认注册
            </Button>
          </Space>
        </Space>
      )}
    </Card>
  )
}
