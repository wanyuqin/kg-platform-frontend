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
  Select,
  Space,
  Steps,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { useNavigate } from 'react-router-dom'

import {
  api,
  DomainItem,
  ImportBatchOut,
  ImportItemOut,
  KNOWLEDGE_TYPES,
  TYPE_COLOR,
} from '../api/client'

// ⑦ 拆分预览确认页（线稿 7.2-⑦）：上传/解析 → 预览确认（本页）→ 入库；
// 卡片式条目可展开看解析后字段，校验不通过黄底且自动取消勾选，底部预计汇总条
export default function ImportPreview() {
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [batch, setBatch] = useState<ImportBatchOut | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [expanded, setExpanded] = useState<number[]>([])
  const [confirming, setConfirming] = useState(false)
  const [confirmResults, setConfirmResults] = useState<
    { item_id: number; kid: string | null; error: string | null }[]
  >([])

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => setDomains(resp.data.items))
  }, [])

  const upload = async (file: File) => {
    const { domain, type } = await form.validateFields()
    const data = new FormData()
    data.append('domain', domain)
    data.append('type', type)
    data.append('file', file)
    const resp = await api.post<ImportBatchOut>('/api/imports', data)
    setBatch(resp.data)
    setSelected(resp.data.items.filter((i) => i.is_valid).map((i) => i.id))
    setConfirmResults([])
    return false
  }

  const confirm = async () => {
    if (!batch) return
    setConfirming(true)
    try {
      const resp = await api.post(`/api/imports/${batch.id}/confirm`, { item_ids: selected })
      setConfirmResults(resp.data.results)
      const ok = resp.data.results.filter((r: { kid: string | null }) => r.kid).length
      message.success(`已入库 ${ok} 条`)
    } finally {
      setConfirming(false)
    }
  }

  const toggle = (id: number, checked: boolean) =>
    setSelected((prev) => (checked ? [...prev, id] : prev.filter((x) => x !== id)))

  const allValidIds = batch?.items.filter((i) => i.is_valid).map((i) => i.id) ?? []

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

  return (
    <Card title="拆分预览确认">
      <Steps
        current={batch ? 1 : 0}
        style={{ maxWidth: 640, marginBottom: 24 }}
        items={[{ title: '上传 / 解析' }, { title: '预览确认' }, { title: '入库' }]}
      />

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

      {!batch && (
        <Upload.Dragger accept=".md" maxCount={1} beforeUpload={upload} showUploadList={false}>
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">上传 .md 文件（UTF-8，≤2MB，一级标题为条目边界）</p>
          <p className="ant-upload-hint">前置校验不通过将当场拒收并提供标准模板下载（设计 3.1）</p>
        </Upload.Dragger>
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
              checked={selected.length === allValidIds.length && allValidIds.length > 0}
              indeterminate={selected.length > 0 && selected.length < allValidIds.length}
              onChange={(e) => setSelected(e.target.checked ? allValidIds : [])}
            >
              全选（已选 {selected.length}/{batch.items.length}）
            </Checkbox>
            <Typography.Text type="secondary">
              说明：未勾选的条目不入库，仅保留在解析记录中
            </Typography.Text>
          </Space>

          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            {batch.items.map((item) => {
              const result = confirmResults.find((r) => r.item_id === item.id)
              const isOpen = expanded.includes(item.id)
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
                        disabled={!item.is_valid}
                        onChange={(e) => toggle(item.id, e.target.checked)}
                      />
                      <Tag color={TYPE_COLOR[batch.type]}>{batch.type}</Tag>
                      <Typography.Text strong>{item.title ?? '（无标题）'}</Typography.Text>
                      {statusOf(item)}
                      {!item.is_valid && (
                        <Typography.Text type="secondary">已自动取消勾选</Typography.Text>
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
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Typography.Text>
                确认入库 {selected.length} 条 → 预计：{selected.length} 条自动发布（低风险，4.3.1
                绿色通道）
              </Typography.Text>
              <Space>
                <Button onClick={() => navigate('/knowledge')}>取消</Button>
                <Button
                  type="primary"
                  disabled={selected.length === 0}
                  loading={confirming}
                  onClick={confirm}
                >
                  确认入库
                </Button>
              </Space>
            </Space>
          </Card>
        </>
      )}
    </Card>
  )
}
