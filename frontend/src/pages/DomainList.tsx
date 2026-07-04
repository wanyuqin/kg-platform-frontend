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

import { api, DomainItem } from '../api/client'

const BAR_COLORS: Record<string, string> = {
  faq: '#2f54eb',
  policy: '#69b1ff',
  sop: '#bae0ff',
  term: '#597ef7',
}

// ⑥ domain 列表页（线稿 7.2-⑥，平台管理员）：卡片式全域概览、
// 知识量与类型分布条、Agent 数、「配置 →」进配置页
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
    message.success(`domain ${values.code} 已注册`)
    setCreateOpen(false)
    createForm.resetFields()
    load()
  }

  const distribution = (d: DomainItem) => {
    const byType = d.stats?.by_type ?? {}
    const total = d.stats?.total ?? 0
    if (!total) return <div style={{ height: 8, background: '#f0f0f0', borderRadius: 4 }} />
    const known = ['faq', 'policy', 'sop', 'term']
    const parts = known
      .filter((t) => byType[t])
      .map((t) => ({ type: t, n: byType[t], color: BAR_COLORS[t] }))
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
          {parts.map((p) => `${p.type} ${p.n}`).join(' · ')}
        </Typography.Text>
      </>
    )
  }

  return (
    <Card
      title="domain 管理（平台管理员）"
      loading={loading}
      extra={
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          + 新建 domain
        </Button>
      }
    >
      <Row gutter={[16, 16]}>
        {items.map((d) => (
          <Col span={12} key={d.code}>
            <Card>
              <Space align="baseline">
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {d.code}
                </Typography.Title>
                <Typography.Text type="secondary">{d.name}</Typography.Text>
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
              <Space style={{ width: '100%', justifyContent: 'space-between', marginTop: 12 }}>
                <Typography.Text type="secondary">
                  默认有效期 {d.default_ttl_days} 天
                </Typography.Text>
                <Button size="small" onClick={() => navigate(`/domains/${d.code}`)}>
                  配置 →
                </Button>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      <Modal
        title="注册 domain（code / short_code 全局唯一且不可改）"
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
