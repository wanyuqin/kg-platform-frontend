import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { Link, useParams } from 'react-router-dom'

import { api, DomainItem, DomainKeyItem, KNOWLEDGE_TYPES } from '../api/client'

// ② domain 配置页（线稿 7.2-②，平台管理员）：
// A 飞书目录映射（P2 占位）/ B 治理配置 / C Agent 白名单与 API Key / D 类型级 top_k
export default function DomainConfig() {
  const { code } = useParams<{ code: string }>()
  const [domain, setDomain] = useState<DomainItem | null>(null)
  const [keys, setKeys] = useState<DomainKeyItem[]>([])
  const [ttl, setTtl] = useState<number>()
  const [topk, setTopk] = useState<Record<string, number | null>>({})
  const [keyOpen, setKeyOpen] = useState(false)
  const [issuedKey, setIssuedKey] = useState<string | null>(null)
  const [keyForm] = Form.useForm()

  const load = useCallback(async () => {
    const [domainsResp, keysResp] = await Promise.all([
      api.get('/api/domains'),
      api.get(`/api/domains/${code}/keys`),
    ])
    const d = domainsResp.data.items.find((x: DomainItem) => x.code === code)
    setDomain(d ?? null)
    setTtl(d?.default_ttl_days)
    setTopk(d?.type_topk ?? {})
    setKeys(keysResp.data.items)
  }, [code])

  useEffect(() => {
    load()
  }, [load])

  if (!domain) return <Card loading title="domain 配置" />

  const save = async () => {
    const type_topk: Record<string, number> = {}
    Object.entries(topk).forEach(([k, v]) => {
      if (v) type_topk[k] = v
    })
    await api.patch(`/api/domains/${code}`, { default_ttl_days: ttl, type_topk })
    message.success('已保存')
    load()
  }

  const issueKey = async () => {
    const values = await keyForm.validateFields()
    const resp = await api.post(`/api/domains/${code}/keys`, values)
    setIssuedKey(resp.data.plaintext)
    keyForm.resetFields()
    load()
  }

  const revoke = async (keyId: string) => {
    await api.delete(`/api/keys/${keyId}`)
    message.success('已吊销（即时生效）；如需重发请新增 Agent Key')
    load()
  }

  return (
    <div>
      <Breadcrumb
        style={{ marginBottom: 12 }}
        items={[{ title: <Link to="/domains">domain 管理</Link> }, { title: code }]}
      />
      <Card
        title={
          <Descriptions column={4} size="small" style={{ marginTop: 8 }}>
            <Descriptions.Item label="domain 标识">
              <Typography.Text strong>{domain.code}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="名称">{domain.name}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag>启用</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="高风险域">
              <Switch disabled checkedChildren="开" unCheckedChildren="关" />
              <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                P2 风险矩阵上线后启用（4.3.1）
              </Typography.Text>
            </Descriptions.Item>
          </Descriptions>
        }
        extra={
          <Button type="primary" onClick={save}>
            保存
          </Button>
        }
      >
        <Typography.Title level={5}>A · 飞书目录映射</Typography.Title>
        <Alert
          type="info"
          style={{ marginBottom: 24 }}
          message="P2 飞书接入后启用：目录路径 → 默认 type 映射（目录先验 → 类型识别零 LLM，4.1.3）"
        />

        <Typography.Title level={5}>B · 治理配置</Typography.Title>
        <Space size="large" style={{ marginBottom: 24 }}>
          <Form.Item label="默认有效期（天）" style={{ marginBottom: 0 }}>
            <InputNumber min={1} value={ttl} onChange={(v) => setTtl(v ?? undefined)} />
          </Form.Item>
          <Form.Item label="审核人" style={{ marginBottom: 0 }}>
            <Typography.Text type="secondary">P2 审核流上线后配置</Typography.Text>
          </Form.Item>
          <Form.Item label="到期提醒" style={{ marginBottom: 0 }}>
            <Typography.Text>提前 14 天（只读，系统默认）</Typography.Text>
          </Form.Item>
        </Space>

        <Typography.Title level={5}>C · Agent 白名单与 API Key</Typography.Title>
        <Table<DomainKeyItem>
          rowKey="key_id"
          size="small"
          dataSource={keys}
          pagination={false}
          style={{ marginBottom: 8 }}
          columns={[
            { title: 'Agent 名称', dataIndex: 'agent_name' },
            {
              title: 'API Key',
              dataIndex: 'key_id',
              render: (id: string) => `kp_${id}_****`,
            },
            { title: 'QPS 限额', dataIndex: 'qps_limit', width: 100 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 90,
              render: (s: string) => (
                <Tag color={s === 'active' ? 'green' : 'red'}>
                  {s === 'active' ? '启用' : '已吊销'}
                </Tag>
              ),
            },
            {
              title: '操作',
              width: 120,
              render: (_, k) =>
                k.status === 'active' ? (
                  <Popconfirm title="吊销即时生效，确认？" onConfirm={() => revoke(k.key_id)}>
                    <a style={{ color: '#ff4d4f' }}>吊销重发</a>
                  </Popconfirm>
                ) : (
                  '—'
                ),
            },
          ]}
        />
        <Button type="dashed" onClick={() => setKeyOpen(true)} style={{ marginBottom: 8 }}>
          + 新增 Agent
        </Button>
        <Typography.Paragraph type="secondary">
          * 所有 Key 默认包含 common 域（6.1.3）
        </Typography.Paragraph>

        <Typography.Title level={5}>D · 类型级 top_k 配置</Typography.Title>
        <Space size="large" wrap>
          {KNOWLEDGE_TYPES.map((t) => (
            <Form.Item key={t.value} label={t.value} style={{ marginBottom: 0 }}>
              <InputNumber
                min={1}
                max={20}
                value={topk[t.value] ?? null}
                onChange={(v) => setTopk((prev) => ({ ...prev, [t.value]: v }))}
                placeholder="默认 5"
              />
            </Form.Item>
          ))}
          <Typography.Text type="secondary">
            其余类型默认 5；top_k 取值 = min(入参 ?? 本配置 ?? 5, 20)（6.1.1）
          </Typography.Text>
        </Space>
      </Card>

      <Modal
        title={`为 ${code} 签发 Agent API Key`}
        open={keyOpen}
        onOk={issueKey}
        onCancel={() => {
          setKeyOpen(false)
          setIssuedKey(null)
        }}
        okText="签发"
        footer={issuedKey ? null : undefined}
      >
        {issuedKey ? (
          <Alert
            type="warning"
            message="明文只显示这一次，请立即复制保存"
            description={
              <Space direction="vertical" style={{ width: '100%' }}>
                <Typography.Text code copyable>
                  {issuedKey}
                </Typography.Text>
                <Button
                  onClick={() => {
                    setKeyOpen(false)
                    setIssuedKey(null)
                  }}
                >
                  已保存，关闭
                </Button>
              </Space>
            }
          />
        ) : (
          <Form form={keyForm} layout="vertical">
            <Form.Item name="agent_name" label="Agent 名称" rules={[{ required: true }]}>
              <Input placeholder="客服 Agent" />
            </Form.Item>
            <Form.Item name="qps_limit" label="QPS 限额" initialValue={10}>
              <InputNumber min={1} style={{ width: 160 }} />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </div>
  )
}
