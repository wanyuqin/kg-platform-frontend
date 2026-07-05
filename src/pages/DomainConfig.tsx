import { useCallback, useEffect, useRef, useState } from 'react'
import { CopyOutlined } from '@ant-design/icons'
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
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { Link, useParams } from 'react-router-dom'

import { api, DomainItem, DomainKeyItem, KNOWLEDGE_TYPES } from '../api/client'

// ② domain 配置页（线稿 7.2-②，平台管理员）
export default function DomainConfig() {
  const { code } = useParams<{ code: string }>()
  const [domain, setDomain] = useState<DomainItem | null>(null)
  const [allDomains, setAllDomains] = useState<DomainItem[]>([])
  const [keys, setKeys] = useState<DomainKeyItem[]>([])
  const [ttl, setTtl] = useState<number>()
  const [topk, setTopk] = useState<Record<string, number | null>>({})
  const [keyOpen, setKeyOpen] = useState(false)
  const [issuedKey, setIssuedKey] = useState<string | null>(null)
  /** 签发响应中的明文密钥（仅本次有效）；复制时优先读 ref，避免闭包或重渲染取到旧值 */
  const issuedPlaintextRef = useRef<string | null>(null)
  const issuedKeyInputRef = useRef<HTMLTextAreaElement>(null)
  const [keyForm] = Form.useForm()

  const load = useCallback(async () => {
    const [domainsResp, keysResp] = await Promise.all([
      api.get('/api/domains'),
      api.get(`/api/domains/${code}/keys`),
    ])
    const d = domainsResp.data.items.find((x: DomainItem) => x.code === code)
    setDomain(d ?? null)
    setAllDomains(domainsResp.data.items.filter((x: DomainItem) => x.code !== 'common'))
    setTtl(d?.default_ttl_days)
    setTopk(d?.type_topk ?? {})
    setKeys(keysResp.data.items)
  }, [code])

  useEffect(() => {
    load()
  }, [load])

  if (!domain) return <Card loading title="知识域配置" />

  const save = async () => {
    const type_topk: Record<string, number> = {}
    Object.entries(topk).forEach(([k, v]) => {
      if (v) type_topk[k] = v
    })
    await api.patch(`/api/domains/${code}`, { default_ttl_days: ttl, type_topk })
    message.success('已保存')
    load()
  }

  const openIssueModal = () => {
    keyForm.setFieldsValue({
      agent_name: undefined,
      qps_limit: 10,
      domain_whitelist: code ? [code] : [],
    })
    setKeyOpen(true)
  }

  const issueKey = async () => {
    const values = await keyForm.validateFields()
    const resp = await api.post(`/api/domains/${code}/keys`, values)
    const plaintext = resp.data.plaintext as string | undefined
    if (!plaintext?.startsWith('kp_')) {
      message.error('签发成功但未返回明文密钥，请重试或联系管理员')
      return
    }
    issuedPlaintextRef.current = plaintext
    setIssuedKey(plaintext)
    keyForm.resetFields()
    load()
  }

  const revoke = async (keyId: string) => {
    await api.delete(`/api/keys/${keyId}`)
    message.success('已吊销（即时生效）；如需重发请新增 Agent Key')
    load()
  }

  const copyText = async (text: string, okMsg = '已复制到剪贴板') => {
    try {
      await navigator.clipboard.writeText(text)
      message.success(okMsg)
    } catch {
      message.error('复制失败，请手动选择复制')
    }
  }

  const copyIssuedPlaintext = () => {
    const text =
      issuedPlaintextRef.current ??
      issuedKeyInputRef.current?.value ??
      issuedKey
    if (!text?.startsWith('kp_')) {
      message.error('暂无可复制的明文密钥')
      return
    }
    void copyText(text, '已复制完整密钥原文')
  }

  return (
    <div>
      <Breadcrumb
        style={{ marginBottom: 12 }}
        items={[{ title: <Link to="/domains">知识域管理</Link> }, { title: domain.name }]}
      />
      <Card
        title={
          <Descriptions column={4} size="small" style={{ marginTop: 8 }}>
            <Descriptions.Item label="域标识（code）">
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
        <Typography.Title level={5}>飞书目录映射</Typography.Title>
        <Alert
          type="info"
          style={{ marginBottom: 24 }}
          message="P2 飞书接入后启用：目录路径 → 默认 type 映射（目录先验 → 类型识别零 LLM，4.1.3）"
        />

        <Typography.Title level={5}>治理配置</Typography.Title>
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

        <Typography.Title level={5}>Agent 接入</Typography.Title>
        <Table<DomainKeyItem>
          rowKey="key_id"
          size="small"
          dataSource={keys}
          pagination={false}
          style={{ marginBottom: 8 }}
          columns={[
            { title: 'Agent 名称', dataIndex: 'agent_name' },
            {
              title: '授权域',
              dataIndex: 'domain_whitelist',
              render: (list: string[]) => (
                <>
                  {list.map((d) => (
                    <Tag key={d}>{d}</Tag>
                  ))}
                  <Tag>common</Tag>
                </>
              ),
            },
            {
              title: 'API Key',
              dataIndex: 'key_id',
              render: (id: string) => <Typography.Text code>{`kp_${id}_****`}</Typography.Text>,
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
        <Button type="dashed" onClick={openIssueModal} style={{ marginBottom: 8 }}>
          + 新增 Agent
        </Button>
        <Typography.Paragraph type="secondary">
          * 一个 Agent 一把 Key，可多选授权域；common 域对所有 Key 自动可见（6.1.3）
        </Typography.Paragraph>

        <Typography.Title level={5}>检索返回条数</Typography.Title>
        <Space size="large" wrap style={{ marginBottom: 24 }}>
          {KNOWLEDGE_TYPES.map((t) => (
            <Form.Item key={t.value} label={t.label} style={{ marginBottom: 0 }}>
              <InputNumber
                min={1}
                max={20}
                value={topk[t.value] ?? null}
                onChange={(v) => setTopk((prev) => ({ ...prev, [t.value]: v }))}
                placeholder="留空用平台默认"
              />
            </Form.Item>
          ))}
        </Space>
      </Card>

      <Modal
        title={`为「${domain.name}」签发 Agent API Key`}
        open={keyOpen}
        onOk={issueKey}
        onCancel={() => {
          setKeyOpen(false)
          setIssuedKey(null)
          issuedPlaintextRef.current = null
        }}
        maskClosable={!issuedKey}
        closable={!issuedKey}
        okText="签发"
        footer={issuedKey ? null : undefined}
      >
        {issuedKey ? (
          <Alert
            type="warning"
            message="明文只显示这一次，请立即复制保存"
            description={
              <Space direction="vertical" style={{ width: '100%' }} size={12}>
                <Input.TextArea
                  ref={issuedKeyInputRef}
                  readOnly
                  value={issuedKey}
                  rows={2}
                  style={{ fontFamily: 'monospace' }}
                />
                <Space>
                  <Button type="primary" icon={<CopyOutlined />} onClick={copyIssuedPlaintext}>
                    一键复制密钥
                  </Button>
                  <Button
                    onClick={() => {
                      setKeyOpen(false)
                      setIssuedKey(null)
                      issuedPlaintextRef.current = null
                    }}
                  >
                    已保存，关闭
                  </Button>
                </Space>
              </Space>
            }
          />
        ) : (
          <Form form={keyForm} layout="vertical">
            <Form.Item name="agent_name" label="Agent 名称" rules={[{ required: true }]}>
              <Input placeholder="客服 Agent" />
            </Form.Item>
            <Form.Item
              name="domain_whitelist"
              label="授权域"
              rules={[{ required: true, message: '请至少选择一个授权域' }]}
            >
              <Select
                mode="multiple"
                placeholder="选择该 Agent 可访问的知识域"
                options={allDomains.map((d) => ({ value: d.code, label: `${d.name} (${d.code})` }))}
              />
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
