import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CheckCircleFilled,
  CloseCircleFilled,
  MinusCircleOutlined,
  WarningFilled,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Divider,
  Form,
  Input,
  Row,
  Select,
  Space,
  Steps,
  Typography,
  message,
} from 'antd'
import dayjs from 'dayjs'
import { useNavigate, useSearchParams } from 'react-router-dom'

import {
  api,
  DomainItem,
  KNOWLEDGE_TYPES,
  SubmitResult,
  TYPE_SECTIONS,
  ValidationFinding,
} from '../api/client'

// ③ 表单录入页（线稿 7.2-③）：三步 wizard、模板动态字段（FAQ 相似问法为数组）、
// 右侧实时完整性校验面板（debounce 调 /api/knowledge/validate）
export default function KnowledgeForm() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const editKid = searchParams.get('edit')
  const [form] = Form.useForm()
  const [step, setStep] = useState(editKid ? 1 : 0)
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [domain, setDomain] = useState<string>()
  const [type, setType] = useState<string>()
  const [validation, setValidation] = useState<ValidationFinding[]>([])
  const [checking, setChecking] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [published, setPublished] = useState<{ kid: string; uri: string } | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => setDomains(resp.data.items))
  }, [])

  useEffect(() => {
    if (!editKid) return
    api.get(`/api/knowledge/${editKid}`).then((resp) => {
      const d = resp.data
      setDomain(d.domain)
      setType(d.type)
      form.setFieldsValue({
        title: d.title,
        tags: d.tags,
        effective_date: dayjs(d.effective_date),
        fields: d.fields,
        similar: d.type === 'faq' ? splitList(d.fields['相似问法'] ?? '') : undefined,
      })
    })
  }, [editKid, form])

  const collectFields = useCallback((): Record<string, string> => {
    const values = form.getFieldsValue()
    const fields: Record<string, string> = { ...(values.fields ?? {}) }
    if (type === 'faq') {
      const similar: string[] = (values.similar ?? []).filter(Boolean)
      fields['相似问法'] = similar.map((s: string) => `- ${s}`).join('\n')
    }
    Object.keys(fields).forEach((k) => {
      if (!fields[k]) delete fields[k]
    })
    return fields
  }, [form, type])

  // 实时完整性校验（线稿右侧面板）：字段变化 600ms 后调后端纯校验接口
  const revalidate = useCallback(() => {
    if (!type) return
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setChecking(true)
      try {
        const resp = await api.post('/api/knowledge/validate', {
          type,
          fields: collectFields(),
        })
        setValidation(resp.data.validation)
      } finally {
        setChecking(false)
      }
    }, 600)
  }, [type, collectFields])

  // 进入填写步骤即校验一次：空表单立即显示缺失项，避免"全绿"误导
  useEffect(() => {
    if (step === 1 && type) revalidate()
  }, [step, type, revalidate])

  const submit = async (saveMode: 'draft' | 'submit') => {
    const values = await form.validateFields()
    setSubmitting(true)
    try {
      const body = {
        domain,
        type,
        title: values.title,
        fields: collectFields(),
        tags: values.tags ?? [],
        effective_date: values.effective_date.format('YYYY-MM-DD'),
        save_mode: saveMode,
      }
      const resp = editKid
        ? await api.put<SubmitResult>(`/api/knowledge/${editKid}`, body)
        : await api.post<SubmitResult>('/api/knowledge', body)
      const result = resp.data
      setValidation(result.validation)
      if (result.status === 'rejected') {
        message.warning('校验未通过，已当场拒收（blocking 项见右侧）')
        return
      }
      if (result.status === 'draft') {
        message.success(`草稿已保存：${result.kid}`)
        navigate(`/knowledge/${result.kid}`)
        return
      }
      setPublished({
        kid: result.kid!,
        uri: `viking://resources/${domain}/${type}/${result.kid}.md`,
      })
      setStep(2)
    } finally {
      setSubmitting(false)
    }
  }

  const sections = type ? TYPE_SECTIONS[type] : []
  const blocking = validation.filter((v) => v.level === 'blocking')
  const okSections = sections.filter(
    (s) => s.required && !validation.some((v) => v.message.includes(`「${s.name}」`)),
  )

  return (
    <Card title={editKid ? `编辑重提：${editKid}` : '表单创建知识'}>
      <Steps
        current={step}
        style={{ maxWidth: 720, marginBottom: 32 }}
        items={[{ title: '选择 domain 与类型' }, { title: '填写内容' }, { title: '提交' }]}
      />

      {step === 0 && (
        <Form layout="vertical" style={{ maxWidth: 480 }}>
          <Form.Item label="domain（仅列出有权限的 domain）" required>
            <Select
              value={domain}
              onChange={setDomain}
              options={domains
                .filter((d) => d.code !== 'common')
                .map((d) => ({ value: d.code, label: `${d.code}（${d.name}）` }))}
              placeholder="选择知识域"
            />
          </Form.Item>
          <Form.Item label="知识类型" required>
            <Select
              value={type}
              onChange={setType}
              options={[...KNOWLEDGE_TYPES]}
              placeholder="选择后按该类型模板渲染字段"
            />
          </Form.Item>
          <Button type="primary" disabled={!domain || !type} onClick={() => setStep(1)}>
            下一步
          </Button>
        </Form>
      )}

      {step === 1 && (
        <Row gutter={24}>
          <Col span={14}>
            <Typography.Text type="secondary">
              domain：{domain} ｜ 类型：{KNOWLEDGE_TYPES.find((t) => t.value === type)?.label}
            </Typography.Text>
            <Divider style={{ margin: '12px 0' }} />
            <Form form={form} layout="vertical" onValuesChange={revalidate}>
              <Form.Item name="title" label="标题" rules={[{ required: true }]}>
                <Input placeholder="FAQ 类建议与标准问法一致" maxLength={256} />
              </Form.Item>
              {sections.map((s) => {
                if (type === 'faq' && s.name === '相似问法') {
                  return (
                    <Form.Item key={s.name} label="相似问法 *（≥2 条）" required>
                      <Form.List name="similar" initialValue={['', '']}>
                        {(fields, { add, remove }) => (
                          <>
                            {fields.map((f) => (
                              <Space key={f.key} style={{ display: 'flex', marginBottom: 8 }}>
                                <Form.Item name={f.name} noStyle>
                                  <Input style={{ width: 360 }} placeholder="一种相似的问法" />
                                </Form.Item>
                                <MinusCircleOutlined onClick={() => remove(f.name)} />
                              </Space>
                            ))}
                            <Button type="dashed" onClick={() => add('')}>
                              + 添加一条
                            </Button>
                          </>
                        )}
                      </Form.List>
                    </Form.Item>
                  )
                }
                const placeholder = s.required ? undefined : '选填'
                return (
                  <Form.Item
                    key={s.name}
                    name={['fields', s.name]}
                    label={s.name + (s.required ? ' *' : '（选填）')}
                  >
                    <Input.TextArea autoSize={{ minRows: 2, maxRows: 8 }} placeholder={placeholder} />
                  </Form.Item>
                )
              })}
              <Form.Item name="tags" label="tags（自由输入，可为空）">
                <Select mode="tags" placeholder="回车分隔" open={false} suffixIcon={null} />
              </Form.Item>
              <Form.Item
                name="effective_date"
                label="生效日期"
                rules={[{ required: true }]}
                initialValue={dayjs()}
              >
                <DatePicker style={{ width: 200 }} />
              </Form.Item>
              <Space>
                {!editKid && (
                  <Button loading={submitting} onClick={() => submit('draft')}>
                    保存草稿
                  </Button>
                )}
                <Button type="primary" loading={submitting} onClick={() => submit('submit')}>
                  提交
                </Button>
                <Button onClick={() => (editKid ? navigate(-1) : setStep(0))}>上一步</Button>
              </Space>
            </Form>
          </Col>
          <Col span={10}>
            <Card size="small" title="完整性校验" loading={checking && validation.length === 0}>
              {okSections.map((s) => (
                <div key={s.name} style={{ marginBottom: 8 }}>
                  <CheckCircleFilled style={{ color: '#52c41a' }} /> {s.name}已填写
                </div>
              ))}
              {validation.map((v, i) => (
                <div key={i} style={{ marginBottom: 8 }}>
                  {v.level === 'blocking' ? (
                    <CloseCircleFilled style={{ color: '#ff4d4f' }} />
                  ) : (
                    <WarningFilled style={{ color: '#faad14' }} />
                  )}{' '}
                  {v.message}
                </div>
              ))}
              <Divider style={{ margin: '12px 0' }} />
              <Typography.Text type={blocking.length ? 'danger' : 'secondary'}>
                {blocking.length
                  ? `存在 ${blocking.length} 项阻塞，提交将被拒收`
                  : '预计风险等级：低 → 提交后自动发布（4.3.1 绿色通道）'}
              </Typography.Text>
            </Card>
          </Col>
        </Row>
      )}

      {step === 2 && published && (
        <Alert
          type="success"
          showIcon
          message={`已发布　kid: ${published.kid}`}
          description={
            <Space direction="vertical">
              <Typography.Text code>{published.uri}</Typography.Text>
              <Space>
                <Button type="primary" onClick={() => navigate(`/knowledge/${published.kid}`)}>
                  查看详情
                </Button>
                <Button onClick={() => navigate('/knowledge')}>返回列表</Button>
              </Space>
            </Space>
          }
        />
      )}
    </Card>
  )
}

function splitList(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.replace(/^[-*]\s*/, '').trim())
    .filter(Boolean)
}
