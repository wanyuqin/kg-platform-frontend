import { CopyOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { Link, useSearchParams } from 'react-router-dom'

import { api, DomainItem, DomainKeyItem, domainSelectOption } from '../api/client'

export default function AdminKeys() {
  const [searchParams, setSearchParams] = useSearchParams()
  const createdByFilter = searchParams.get('created_by') ?? undefined
  const [keys, setKeys] = useState<DomainKeyItem[]>([])
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [loading, setLoading] = useState(false)
  const [issueOpen, setIssueOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editingKey, setEditingKey] = useState<DomainKeyItem | null>(null)
  const [issuedKey, setIssuedKey] = useState<string | null>(null)
  const issuedPlaintextRef = useRef<string | null>(null)
  const issuedKeyInputRef = useRef<HTMLTextAreaElement>(null)
  const [issueForm] = Form.useForm()
  const [editForm] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = {}
      if (createdByFilter) params.created_by = createdByFilter
      const [keysResp, domainsResp] = await Promise.all([
        api.get('/api/keys', { params }),
        api.get('/api/domains'),
      ])
      setKeys(keysResp.data.items)
      setDomains(domainsResp.data.items.filter((d: DomainItem) => d.code !== 'common'))
    } finally {
      setLoading(false)
    }
  }, [createdByFilter])

  useEffect(() => {
    load()
  }, [load])

  const copyText = async (text: string, okMsg = '已复制到剪贴板') => {
    try {
      await navigator.clipboard.writeText(text)
      message.success(okMsg)
    } catch {
      message.error('复制失败，请手动选择复制')
    }
  }

  const openIssueModal = () => {
    issueForm.setFieldsValue({ agent_name: undefined, qps_limit: 10, domain_whitelist: [] })
    setIssuedKey(null)
    issuedPlaintextRef.current = null
    setIssueOpen(true)
  }

  const issueKey = async () => {
    const values = await issueForm.validateFields()
    const resp = await api.post('/api/keys', values)
    const plaintext = resp.data.plaintext as string | undefined
    if (!plaintext?.startsWith('kp_')) {
      message.error('签发成功但未返回明文密钥，请重试或联系管理员')
      return
    }
    issuedPlaintextRef.current = plaintext
    setIssuedKey(plaintext)
    issueForm.resetFields()
    load()
  }

  const openEditModal = (key: DomainKeyItem) => {
    setEditingKey(key)
    editForm.setFieldsValue({
      domain_whitelist: key.domain_whitelist,
      qps_limit: key.qps_limit,
    })
    setEditOpen(true)
  }

  const saveEdit = async () => {
    if (!editingKey) return
    const values = await editForm.validateFields()
    await api.patch(`/api/keys/${editingKey.key_id}`, values)
    message.success('已更新')
    setEditOpen(false)
    setEditingKey(null)
    load()
  }

  const revoke = async (keyId: string) => {
    await api.delete(`/api/keys/${keyId}`)
    message.success('已吊销（即时生效）')
    load()
  }

  const copyIssuedPlaintext = () => {
    const text =
      issuedPlaintextRef.current ?? issuedKeyInputRef.current?.value ?? issuedKey
    if (!text?.startsWith('kp_')) {
      message.error('暂无可复制的明文密钥')
      return
    }
    void copyText(text, '已复制完整密钥原文')
  }

  return (
    <Card
      title="API Key 管理"
      extra={
        <Space>
          {createdByFilter && (
            <Tag closable onClose={() => setSearchParams({})}>
              归属：{createdByFilter}
            </Tag>
          )}
          <Button type="primary" onClick={openIssueModal}>
            签发 Key
          </Button>
        </Space>
      }
    >
      <Table<DomainKeyItem>
        rowKey="key_id"
        loading={loading}
        dataSource={keys}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 把 Key` }}
        columns={[
          { title: 'Agent 名称', dataIndex: 'agent_name' },
          {
            title: 'Key ID',
            dataIndex: 'key_id',
            render: (id: string) => <Typography.Text code>{`kp_${id}_****`}</Typography.Text>,
          },
          {
            title: '归属人',
            render: (_, k) => (
              <Space direction="vertical" size={0}>
                <Typography.Text>{k.created_by_name}</Typography.Text>
                <Typography.Text type="secondary" code style={{ fontSize: 12 }}>
                  {k.created_by}
                </Typography.Text>
              </Space>
            ),
          },
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
          { title: 'QPS', dataIndex: 'qps_limit', width: 70 },
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
          { title: '近 30 天调用', dataIndex: 'calls_30d', width: 110 },
          {
            title: '最后调用',
            dataIndex: 'last_used_at',
            width: 170,
            render: (t: string | null) => (t ? new Date(t).toLocaleString() : '—'),
          },
          {
            title: '创建时间',
            dataIndex: 'created_at',
            width: 170,
            render: (t: string) => new Date(t).toLocaleString(),
          },
          {
            title: '操作',
            width: 180,
            fixed: 'right',
            render: (_, k) => (
              <Space>
                {k.status === 'active' && (
                  <Button type="link" size="small" onClick={() => openEditModal(k)}>
                    编辑
                  </Button>
                )}
                {k.status === 'active' ? (
                  <Popconfirm title="吊销即时生效，确认？" onConfirm={() => void revoke(k.key_id)}>
                    <Button type="link" size="small" danger>
                      吊销
                    </Button>
                  </Popconfirm>
                ) : null}
                <Link to={`/audit-logs?key_id=${k.key_id}`}>审计</Link>
              </Space>
            ),
          },
        ]}
        scroll={{ x: 1200 }}
      />

      <Modal
        title="签发 Agent API Key"
        open={issueOpen}
        onOk={issuedKey ? undefined : () => void issueKey()}
        onCancel={() => {
          setIssueOpen(false)
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
                      setIssueOpen(false)
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
          <Form form={issueForm} layout="vertical">
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
                options={domains.map(domainSelectOption)}
              />
            </Form.Item>
            <Form.Item name="qps_limit" label="QPS 限额" initialValue={10}>
              <InputNumber min={1} style={{ width: 160 }} />
            </Form.Item>
          </Form>
        )}
      </Modal>

      <Modal
        title={`编辑 Key · ${editingKey?.agent_name ?? ''}`}
        open={editOpen}
        onOk={() => void saveEdit()}
        onCancel={() => {
          setEditOpen(false)
          setEditingKey(null)
        }}
        okText="保存"
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="domain_whitelist"
            label="授权域"
            rules={[{ required: true, message: '请至少选择一个授权域' }]}
          >
            <Select mode="multiple" options={domains.map(domainSelectOption)} />
          </Form.Item>
          <Form.Item name="qps_limit" label="QPS 限额" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: 160 }} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
