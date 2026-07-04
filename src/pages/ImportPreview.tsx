import { useEffect, useState } from 'react'
import {
  CheckCircleFilled,
  DownOutlined,
  InboxOutlined,
  RightOutlined,
  StopFilled,
  WarningFilled,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Select,
  Space,
  Steps,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'

import {
  ALIGN_COLOR,
  ALIGN_LABEL,
  api,
  DomainItem,
  ImportBatchOut,
  ImportItemOut,
  KNOWLEDGE_TYPES,
  TYPE_COLOR,
} from '../api/client'

// 默认勾选规则（首次导入 / 更新批次共用，改造要点 3）：
// 校验通过 && 非 unchanged（未变化条目无需重复入库）
// && 不是"表单添加但未在新文本中出现"的消失条目（这类需人工确认是否真的要下架）
const defaultSelected = (items: ImportItemOut[]) =>
  items
    .filter((i) => i.is_valid && i.align_action !== 'unchanged')
    .filter((i) => !(i.align_action === 'disappeared' && i.is_form))
    .map((i) => i.id)

// ⑦ 拆分预览确认页（线稿 7.2-⑦）：上传/解析 → 预览确认（本页）→ 入库；
// 卡片式条目可展开看解析后字段，校验不通过黄底且自动取消勾选，底部预计汇总条
//
// 支持两种进入方式（改造要点，见 task-11-brief）：
// - ?docId=xxx：更新模式，隐藏 domain/type/doc_name，提交到 /api/source-docs/{docId}/update
// - ?batchId=xxx：直接加载既有批次预览（在线编辑/重新查看跳转用）
export default function ImportPreview() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const docId = params.get('docId')
  const batchId = params.get('batchId')
  const isUpdateMode = !!docId

  const [form] = Form.useForm()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [batch, setBatch] = useState<ImportBatchOut | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [expanded, setExpanded] = useState<number[]>([])
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [confirmedSourceDocId, setConfirmedSourceDocId] = useState<number | null>(null)
  const [confirmResults, setConfirmResults] = useState<
    { item_id: number; kid: string | null; error: string | null }[]
  >([])
  const [pasteText, setPasteText] = useState('')
  const [docName, setDocName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  // 入库前：0=上传/解析 1=预览确认；入库后 confirm 成功推进到 2
  const [step, setStep] = useState(0)

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => setDomains(resp.data.items))
  }, [])

  // batchId 存在则直接加载既有批次预览（改造要点 2）
  useEffect(() => {
    if (!batchId) return
    api.get<ImportBatchOut>(`/api/imports/${batchId}`).then((resp) => {
      setBatch(resp.data)
      setSelected(defaultSelected(resp.data.items))
      setConfirmResults([])
      setStep(1)
    })
  }, [batchId])

  // 提交函数（改造要点 4）：普通模式带 domain/type/doc_name，POST /api/imports；
  // 更新模式只带 file 或 text，POST /api/source-docs/{docId}/update
  const submit = async (payload: { file?: File; text?: string }) => {
    setSubmitting(true)
    try {
      const data = new FormData()
      if (payload.file) data.append('file', payload.file)
      if (payload.text) data.append('text', payload.text)

      let resp
      if (isUpdateMode) {
        resp = await api.post<ImportBatchOut>(`/api/source-docs/${docId}/update`, data)
      } else {
        const { domain, type } = await form.validateFields()
        data.append('domain', domain)
        data.append('type', type)
        if (docName.trim()) data.append('doc_name', docName.trim())
        resp = await api.post<ImportBatchOut>('/api/imports', data)
      }
      setBatch(resp.data)
      setSelected(defaultSelected(resp.data.items))
      setConfirmResults([])
      setConfirmed(false)
      setConfirmedSourceDocId(null)
      setStep(1)
    } finally {
      setSubmitting(false)
    }
  }

  const uploadFile = async (file: File) => {
    await submit({ file })
    return false
  }

  const submitPaste = async () => {
    if (!pasteText.trim()) {
      message.warning('请先粘贴文本内容')
      return
    }
    await submit({ text: pasteText })
  }

  const confirm = async () => {
    if (!batch || confirming || confirmed) return
    setConfirming(true)
    try {
      const resp = await api.post(`/api/imports/${batch.id}/confirm`, { item_ids: selected })
      setConfirmResults(resp.data.results)
      const ok = resp.data.results.filter((r: { kid: string | null }) => r.kid).length
      message.success(`已入库 ${ok} 条`)
      setConfirmed(true)
      setConfirmedSourceDocId(resp.data.source_doc_id ?? null)
      setStep(2)
    } finally {
      setConfirming(false)
    }
  }

  const toggle = (id: number, checked: boolean) =>
    setSelected((prev) => (checked ? [...prev, id] : prev.filter((x) => x !== id)))

  // unchanged 行不可勾选（未变化条目无需重新入库）
  const selectableIds =
    batch?.items.filter((i) => i.is_valid && i.align_action !== 'unchanged').map((i) => i.id) ?? []

  const statusOf = (item: ImportItemOut) => {
    if (!item.is_valid)
      return (
        <Typography.Text type="danger">
          <StopFilled /> 校验不通过：{item.validation.find((v) => v.level === 'blocking')?.message}
        </Typography.Text>
      )
    const warning = item.validation.find((v) => v.level === 'warning')
    if (warning)
      return (
        <Typography.Text type="warning">
          <WarningFilled /> {warning.message}
        </Typography.Text>
      )
    return (
      <Typography.Text type="success">
        <CheckCircleFilled /> 校验通过
      </Typography.Text>
    )
  }

  // 更新模式底部汇总（改造要点 7）
  const countByAction = (action: string) =>
    batch?.items.filter((i) => selected.includes(i.id) && i.align_action === action).length ?? 0

  return (
    <Card title={isUpdateMode ? '更新知识文件' : '拆分预览确认'}>
      <Steps
        current={step}
        style={{ maxWidth: 640, marginBottom: 24 }}
        items={[{ title: '上传 / 解析' }, { title: '预览确认' }, { title: '入库' }]}
      />

      {!isUpdateMode && (
        <Form form={form} layout="inline" style={{ marginBottom: 16 }}>
          <Form.Item name="domain" label="domain" rules={[{ required: true }]}>
            <Select
              style={{ width: 200 }}
              placeholder="选择知识域"
              options={domains
                .filter((d) => d.code !== 'common')
                .map((d) => ({ value: d.code, label: d.code }))}
            />
          </Form.Item>
          <Form.Item name="type" label="类型" rules={[{ required: true }]}>
            <Select options={[...KNOWLEDGE_TYPES]} style={{ width: 180 }} placeholder="先选类型" />
          </Form.Item>
        </Form>
      )}

      {!batch && (
        <Tabs
          defaultActiveKey="paste"
          items={[
            {
              key: 'paste',
              label: '粘贴文本',
              children: (
                <Space direction="vertical" style={{ width: '100%' }} size={12}>
                  {!isUpdateMode && (
                    <Input
                      placeholder="文件名（可选，用于知识文件列表展示）"
                      value={docName}
                      onChange={(e) => setDocName(e.target.value)}
                      style={{ maxWidth: 400 }}
                    />
                  )}
                  <Input.TextArea
                    rows={16}
                    placeholder="粘贴 Markdown 文本内容（一级标题为条目边界）"
                    value={pasteText}
                    onChange={(e) => setPasteText(e.target.value)}
                  />
                  <Button type="primary" loading={submitting} onClick={submitPaste}>
                    解析预览
                  </Button>
                </Space>
              ),
            },
            {
              key: 'upload',
              label: '上传 .md',
              children: (
                <Space direction="vertical" style={{ width: '100%' }} size={12}>
                  {!isUpdateMode && (
                    <Input
                      placeholder="文件名（可选，默认取上传文件名）"
                      value={docName}
                      onChange={(e) => setDocName(e.target.value)}
                      style={{ maxWidth: 400 }}
                    />
                  )}
                  <Upload.Dragger
                    accept=".md"
                    maxCount={1}
                    beforeUpload={uploadFile}
                    showUploadList={false}
                    disabled={submitting}
                  >
                    <p className="ant-upload-drag-icon">
                      <InboxOutlined />
                    </p>
                    <p className="ant-upload-text">
                      上传 .md 文件（UTF-8，≤2MB，一级标题为条目边界）
                    </p>
                    <p className="ant-upload-hint">
                      前置校验不通过将当场拒收并提供标准模板下载（设计 3.1）
                    </p>
                  </Upload.Dragger>
                </Space>
              ),
            },
          ]}
        />
      )}

      {batch && (
        <>
          <Alert
            style={{ marginBottom: 16 }}
            type="info"
            message={
              <Space split="｜">
                <span>来源：{batch.file_name}</span>
                <span>domain: {batch.domain}</span>
                <span>拆出 {batch.items.length} 条</span>
              </Space>
            }
            action={
              <Button size="small" onClick={() => window.open(batch.template_url)}>
                下载模板
              </Button>
            }
          />
          <Space style={{ marginBottom: 12 }}>
            <Checkbox
              checked={selected.length === selectableIds.length && selectableIds.length > 0}
              indeterminate={selected.length > 0 && selected.length < selectableIds.length}
              onChange={(e) => setSelected(e.target.checked ? selectableIds : [])}
            >
              全选（已选 {selected.length}/{batch.items.length}）
            </Checkbox>
            <Typography.Text type="secondary">
              说明：未勾选的条目不入库，仅保留在解析记录中；"未变"条目无需重新入库，不可勾选
            </Typography.Text>
          </Space>

          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            {batch.items.map((item) => {
              const result = confirmResults.find((r) => r.item_id === item.id)
              const isOpen = expanded.includes(item.id)
              const isUnchanged = item.align_action === 'unchanged'
              return (
                <Card
                  key={item.id}
                  size="small"
                  style={{ background: item.is_valid ? undefined : '#fffbe6' }}
                >
                  <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Space>
                      <Checkbox
                        checked={selected.includes(item.id)}
                        disabled={!item.is_valid || isUnchanged}
                        onChange={(e) => toggle(item.id, e.target.checked)}
                      />
                      <Tag color={TYPE_COLOR[batch.type]}>{batch.type}</Tag>
                      {item.align_action && (
                        <Tag color={ALIGN_COLOR[item.align_action]}>
                          {ALIGN_LABEL[item.align_action]}
                        </Tag>
                      )}
                      <Typography.Text strong>{item.title ?? '（无标题）'}</Typography.Text>
                      {statusOf(item)}
                      {!item.is_valid && (
                        <Typography.Text type="secondary">已自动取消勾选</Typography.Text>
                      )}
                      {item.align_action === 'disappeared' && item.is_form && (
                        <Typography.Text type="secondary">
                          表单添加，未在新文本中
                        </Typography.Text>
                      )}
                    </Space>
                    <Space>
                      {result &&
                        (result.kid ? (
                          <a onClick={() => navigate(`/knowledge/${result.kid}`)}>{result.kid}</a>
                        ) : (
                          <Typography.Text type="danger">{result.error}</Typography.Text>
                        ))}
                      <Button
                        type="text"
                        size="small"
                        icon={isOpen ? <DownOutlined /> : <RightOutlined />}
                        onClick={() =>
                          setExpanded((prev) =>
                            isOpen ? prev.filter((x) => x !== item.id) : [...prev, item.id],
                          )
                        }
                      />
                    </Space>
                  </Space>
                  {isOpen && (
                    <div
                      style={{
                        marginTop: 12,
                        marginLeft: 24,
                        background: '#fafafa',
                        borderRadius: 6,
                        padding: 12,
                      }}
                    >
                      {Object.entries(item.fields).map(([name, value]) => (
                        <div key={name} style={{ marginBottom: 4 }}>
                          <Typography.Text type="secondary">{name}：</Typography.Text>
                          <Typography.Text>{value.slice(0, 120)}</Typography.Text>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              )
            })}
          </Space>

          <Card size="small" style={{ marginTop: 16, background: '#fafafa' }}>
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              {isUpdateMode && (
                <Typography.Text>
                  预计：新增 {countByAction('new')} 条、更新 {countByAction('changed')} 条、下架{' '}
                  {countByAction('disappeared')} 条
                </Typography.Text>
              )}
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Typography.Text>
                  确认入库 {selected.length} 条 → 预计：{selected.length} 条自动发布（低风险，4.3.1
                  绿色通道）
                </Typography.Text>
                <Space>
                  <Button onClick={() => navigate('/knowledge')}>取消</Button>
                  {confirmed ? (
                    <Button
                      type="primary"
                      onClick={() =>
                        navigate(
                          confirmedSourceDocId
                            ? `/source-docs/${confirmedSourceDocId}`
                            : '/source-docs',
                        )
                      }
                    >
                      查看知识文件
                    </Button>
                  ) : (
                    <Button
                      type="primary"
                      disabled={selected.length === 0}
                      loading={confirming}
                      onClick={confirm}
                    >
                      确认入库
                    </Button>
                  )}
                </Space>
              </Space>
            </Space>
          </Card>
        </>
      )}
    </Card>
  )
}
