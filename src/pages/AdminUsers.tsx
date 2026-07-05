import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { Link, useNavigate } from 'react-router-dom'

import {
  api,
  ConsoleUserDetail,
  ConsoleUserItem,
  DomainItem,
  domainSelectOption,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'

const ROLE_LABEL: Record<string, string> = {
  admin: '域管理员',
  member: '成员',
}

export default function AdminUsers() {
  const { user: me } = useAuth()
  const navigate = useNavigate()
  const [items, setItems] = useState<ConsoleUserItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [q, setQ] = useState('')
  const [detail, setDetail] = useState<ConsoleUserDetail | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [memberForm] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await api.get('/api/users', {
        params: { page, page_size: 20, q: q || undefined },
      })
      setItems(resp.data.items)
      setTotal(resp.data.total)
    } finally {
      setLoading(false)
    }
  }, [page, q])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    api.get('/api/domains').then((resp) => {
      setDomains(resp.data.items.filter((d: DomainItem) => d.code !== 'common'))
    })
  }, [])

  const togglePlatformAdmin = async (row: ConsoleUserItem, checked: boolean) => {
    await api.patch(`/api/users/${row.user_id}`, { is_platform_admin: checked })
    message.success(checked ? '已授予平台管理员' : '已撤销平台管理员')
    load()
  }

  const openDetail = async (userId: string) => {
    const resp = await api.get<ConsoleUserDetail>(`/api/users/${userId}`)
    setDetail(resp.data)
    memberForm.resetFields()
    setDrawerOpen(true)
  }

  const addOrUpdateMember = async () => {
    if (!detail) return
    const values = await memberForm.validateFields()
    await api.post(`/api/domains/${values.domain_code}/members`, {
      user_id: detail.user_id,
      role: values.role,
    })
    message.success('域成员已更新')
    memberForm.resetFields()
    const resp = await api.get<ConsoleUserDetail>(`/api/users/${detail.user_id}`)
    setDetail(resp.data)
    load()
  }

  const removeMember = async (domainCode: string) => {
    if (!detail) return
    await api.delete(`/api/domains/${domainCode}/members`, {
      params: { user_id: detail.user_id },
    })
    message.success('已移除域成员')
    const resp = await api.get<ConsoleUserDetail>(`/api/users/${detail.user_id}`)
    setDetail(resp.data)
    load()
  }

  return (
    <Card title="用户管理">
      <Space style={{ marginBottom: 16 }}>
        <Input.Search
          placeholder="搜索姓名或 user_id"
          allowClear
          onSearch={(v) => {
            setPage(1)
            setQ(v)
          }}
          style={{ width: 280 }}
        />
      </Space>
      <Table<ConsoleUserItem>
        rowKey="user_id"
        loading={loading}
        dataSource={items}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 人`,
        }}
        columns={[
          { title: '姓名', dataIndex: 'name' },
          {
            title: 'user_id',
            dataIndex: 'user_id',
            render: (id: string) => <Typography.Text code>{id}</Typography.Text>,
          },
          {
            title: '平台管理员',
            dataIndex: 'is_platform_admin',
            width: 120,
            render: (v: boolean, row) => (
              <Switch
                checked={v}
                disabled={row.user_id === me?.user_id}
                checkedChildren="是"
                unCheckedChildren="否"
                onChange={(checked) => void togglePlatformAdmin(row, checked)}
              />
            ),
          },
          {
            title: '域角色',
            dataIndex: 'domains',
            render: (list: ConsoleUserItem['domains']) =>
              list.length ? (
                <>
                  {list.map((d) => (
                    <Tag key={d.code}>
                      {d.name} · {ROLE_LABEL[d.role] ?? d.role}
                    </Tag>
                  ))}
                </>
              ) : (
                <Typography.Text type="secondary">—</Typography.Text>
              ),
          },
          { title: '活跃 Key', dataIndex: 'active_key_count', width: 90 },
          {
            title: '注册时间',
            dataIndex: 'created_at',
            width: 180,
            render: (t: string) => new Date(t).toLocaleString(),
          },
          {
            title: '操作',
            width: 160,
            render: (_, row) => (
              <Space>
                <Button type="link" size="small" onClick={() => void openDetail(row.user_id)}>
                  详情
                </Button>
                <Button
                  type="link"
                  size="small"
                  onClick={() => navigate(`/admin/keys?created_by=${row.user_id}`)}
                >
                  查看 Key
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Drawer
        title={detail ? `${detail.name}（${detail.user_id}）` : '用户详情'}
        width={560}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      >
        {detail && (
          <>
            <Descriptions column={1} size="small" style={{ marginBottom: 24 }}>
              <Descriptions.Item label="平台管理员">
                {detail.is_platform_admin ? '是' : '否'}
              </Descriptions.Item>
              <Descriptions.Item label="活跃 Key">{detail.active_key_count}</Descriptions.Item>
              <Descriptions.Item label="注册时间">
                {new Date(detail.created_at).toLocaleString()}
              </Descriptions.Item>
            </Descriptions>

            <Typography.Title level={5}>域成员关系</Typography.Title>
            <Table
              rowKey="code"
              size="small"
              pagination={false}
              dataSource={detail.domains}
              style={{ marginBottom: 16 }}
              columns={[
                { title: '知识域', dataIndex: 'name' },
                { title: 'code', dataIndex: 'code' },
                {
                  title: '角色',
                  dataIndex: 'role',
                  render: (r: string) => ROLE_LABEL[r] ?? r,
                },
                {
                  title: '操作',
                  render: (_, d) => (
                    <Popconfirm title="确认移除该域成员？" onConfirm={() => void removeMember(d.code)}>
                      <a style={{ color: '#ff4d4f' }}>移除</a>
                    </Popconfirm>
                  ),
                },
              ]}
            />

            <Form form={memberForm} layout="inline" style={{ marginBottom: 24 }}>
              <Form.Item
                name="domain_code"
                rules={[{ required: true, message: '选择知识域' }]}
              >
                <Select
                  placeholder="知识域"
                  style={{ width: 200 }}
                  options={domains.map(domainSelectOption)}
                />
              </Form.Item>
              <Form.Item name="role" initialValue="member" rules={[{ required: true }]}>
                <Select
                  style={{ width: 120 }}
                  options={[
                    { value: 'admin', label: '域管理员' },
                    { value: 'member', label: '成员' },
                  ]}
                />
              </Form.Item>
              <Form.Item>
                <Button type="primary" onClick={() => void addOrUpdateMember()}>
                  添加/更新
                </Button>
              </Form.Item>
            </Form>

            <Typography.Title level={5}>签发的 API Key</Typography.Title>
            {detail.keys.length === 0 ? (
              <Typography.Text type="secondary">暂无</Typography.Text>
            ) : (
              <ul style={{ paddingLeft: 20 }}>
                {detail.keys.map((k) => (
                  <li key={k.key_id}>
                    <Link to={`/admin/keys?created_by=${detail.user_id}`}>
                      {k.agent_name}
                    </Link>
                    {' · '}
                    <Tag color={k.status === 'active' ? 'green' : 'red'}>
                      {k.status === 'active' ? '启用' : '已吊销'}
                    </Tag>
                    {' · '}
                    近 30 天 {k.calls_30d} 次
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Drawer>
    </Card>
  )
}
