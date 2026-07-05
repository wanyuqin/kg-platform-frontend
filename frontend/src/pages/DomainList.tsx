import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { useNavigate } from 'react-router-dom'

import { api, DomainItem, KNOWLEDGE_TYPES, TYPE_LABEL } from '../api/client'

const BAR_COLORS: Record<string, string> = {
  faq: '#2f54eb',
  policy: '#69b1ff',
  sop: '#bae0ff',
  product: '#9254de',
  case: '#f5222d',
  term: '#597ef7',
}

// ⑥ domain 列表页：三层主流程入口（domain → 知识文件 → 知识条目），
// 同时保留平台管理员配置入口
export default function DomainList() {
  const navigate = useNavigate()
  const [items, setItems] = useState<DomainItem[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await api.get('/api/domains')
      setItems(resp.data.items)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const createDomain = async () => {
    const values = await createForm.validateFields()
    await api.post('/api/domains', values)
    message.success(`知识域 ${values.name}（${values.code}）已注册`)
    setCreateOpen(false)
    createForm.resetFields()
    load()
  }

  const distribution = (d: DomainItem) => {
    const byType = d.stats?.by_type ?? {}
    const total = d.stats?.total ?? 0
    if (!total) return <div style={{ height: 8, background: '#f0f0f0', borderRadius: 4 }} />
    const known = KNOWLEDGE_TYPES.map((t) => t.value)
    const parts: { type: string; n: number; color: string }[] = known
      .filter((t) => byType[t])
      .map((t) => ({ type: t, n: byType[t], color: BAR_COLORS[t] ?? '#d9d9d9' }))
    const other = total - parts.reduce((s, p) => s + p.n, 0)
    if (other > 0) parts.push({ type: '其他', n: other, color: '#d9d9d9' })
    return (
      <>
        <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden' }}>
          {parts.map((p) => (
            <div key={p.type} style={{ flex: p.n, background: p.color }} />
          ))}
        </div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {parts.map((p) => `${TYPE_LABEL[p.type] ?? p.type} ${p.n}`).join(' · ')}
        </Typography.Text>
      </>
    )
  }

  return (
    <Card
      title="知识域"
      loading={loading}
      extra={
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          + 新建知识域
        </Button>
      }
    >
      <Row gutter={[16, 16]}>
        {items.map((d) => (
          <Col xs={24} lg={12} key={d.code} style={{ display: 'flex' }}>
            <Card
              hoverable
              style={{ width: '100%', height: '100%' }}
              styles={{ body: { minHeight: 180, display: 'flex', flexDirection: 'column' } }}
              onClick={() => navigate(`/source-docs?domain=${encodeURIComponent(d.code)}`)}
            >
              <div style={{ flex: 1 }}>
                <Space align="baseline">
                  <Typography.Title level={4} style={{ margin: 0 }}>
                    {d.name}
                  </Typography.Title>
                  <Typography.Text type="secondary">{d.code}</Typography.Text>
                </Space>
                <div style={{ margin: '8px 0' }}>
                  <Tag>启用</Tag>
                  {d.code === 'common' && <Tag color="blue">所有 Agent 默认可见（6.1.3）</Tag>}
                </div>
                <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 4 }}>
                  <Typography.Text>知识 {d.stats?.total ?? 0} 条</Typography.Text>
                  <Typography.Text>Agent {d.stats?.agents ?? 0} 个</Typography.Text>
                </Space>
                {distribution(d)}
              </div>
              <Space style={{ width: '100%', justifyContent: 'space-between', marginTop: 16 }}>
                <Typography.Text type="secondary">
                  默认有效期 {d.default_ttl_days} 天
                </Typography.Text>
                <Space>
                  <Button
                    size="small"
                    type="primary"
                    onClick={(e) => {
                      e.stopPropagation()
                      navigate(`/source-docs?domain=${encodeURIComponent(d.code)}`)
                    }}
                  >
                    查看文件
                  </Button>
                  <Button
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation()
                      navigate(`/domains/${d.code}`)
                    }}
                  >
                    配置
                  </Button>
                </Space>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      <Modal
        title="注册知识域（标识 code / short_code 全局唯一且不可改）"
        open={createOpen}
        onOk={createDomain}
        onCancel={() => setCreateOpen(false)}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="code"
            label="code（viking:// 一级目录名）"
            rules={[
              {
                required: true,
                pattern: /^[a-z][a-z0-9-]{1,31}$/,
                message: '小写字母开头，字母/数字/连字符',
              },
            ]}
          >
            <Input placeholder="free-order" />
          </Form.Item>
          <Form.Item
            name="short_code"
            label="short_code（kid 域段，2~4 位小写字母）"
            rules={[{ required: true, pattern: /^[a-z]{2,4}$/, message: '2~4 位小写字母' }]}
          >
            <Input placeholder="fo" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="免单域" />
          </Form.Item>
          <Form.Item name="default_ttl_days" label="默认有效期（天）" initialValue={365}>
            <InputNumber min={1} style={{ width: 160 }} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
