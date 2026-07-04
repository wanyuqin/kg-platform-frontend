import { useCallback, useEffect, useState } from 'react'
import { DownOutlined } from '@ant-design/icons'
import {
  Button,
  Card,
  DatePicker,
  Dropdown,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import dayjs, { Dayjs } from 'dayjs'
import { useNavigate } from 'react-router-dom'

import {
  api,
  daysUntil,
  DomainItem,
  KNOWLEDGE_TYPES,
  KnowledgeItem,
  KnowledgeStats,
  STATUS_LABEL,
  TYPE_COLOR,
} from '../api/client'

// ⑤ 知识列表页（控制台主页）：domain 切换、类型 tab 计数、状态 chip、
// 标题/kid 搜索、owner 筛选、三合一新建入口、行内操作（线稿 7.2-⑤）
export default function KnowledgeList() {
  const navigate = useNavigate()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [domain, setDomain] = useState<string>()
  const [stats, setStats] = useState<KnowledgeStats>({ total: 0, by_type: {} })
  const [typeTab, setTypeTab] = useState('all')
  const [status, setStatus] = useState<string | undefined>('published')
  const [q, setQ] = useState<string>()
  const [owner, setOwner] = useState<string>()
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [renewKid, setRenewKid] = useState<string | null>(null)
  const [renewDate, setRenewDate] = useState<Dayjs | null>(null)

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => {
      setDomains(resp.data.items)
      const first = resp.data.items.find((d: DomainItem) => d.code !== 'common')
      setDomain(first?.code ?? resp.data.items[0]?.code)
    })
  }, [])

  const load = useCallback(async () => {
    if (!domain) return
    setLoading(true)
    try {
      const [listResp, statsResp] = await Promise.all([
        api.get('/api/knowledge', {
          params: {
            domain,
            page,
            page_size: 20,
            type: typeTab === 'all' ? undefined : typeTab,
            status,
            q: q || undefined,
            owner: owner || undefined,
          },
        }),
        api.get('/api/knowledge/stats', { params: { domain } }),
      ])
      setItems(listResp.data.items)
      setStats(statsResp.data)
    } finally {
      setLoading(false)
    }
  }, [domain, page, typeTab, status, q, owner])

  useEffect(() => {
    load()
  }, [load])

  const archive = async (kid: string) => {
    await api.post(`/api/knowledge/${kid}/archive`)
    message.success('已下架')
    load()
  }

  const renew = async () => {
    if (!renewKid || !renewDate) return
    await api.post(`/api/knowledge/${renewKid}/renew`, {
      expire_date: renewDate.format('YYYY-MM-DD'),
    })
    message.success('已续期')
    setRenewKid(null)
    load()
  }

  const expireCell = (d: string) => {
    const left = daysUntil(d)
    if (left < 0) return <Space size={4}>已过期 {-left} 天</Space>
    return (
      <Space size={4}>
        {d}
        {left <= 90 && <Tag color="gold">剩{left}天</Tag>}
      </Space>
    )
  }

  return (
    <Card
      title={
        <Space>
          知识管理
          <Select
            size="small"
            style={{ width: 160 }}
            value={domain}
            onChange={(v) => {
              setDomain(v)
              setPage(1)
            }}
            options={domains.map((d) => ({ value: d.code, label: d.code }))}
          />
          <Typography.Text type="secondary" style={{ fontWeight: 'normal', fontSize: 13 }}>
            当前知识域（仅显示有权限的 domain）
          </Typography.Text>
        </Space>
      }
      extra={
        <Dropdown
          menu={{
            items: [
              { key: 'form', label: '表单创建' },
              { key: 'import', label: '上传 Markdown' },
              { key: 'feishu', label: '注册飞书文档（P2）', disabled: true },
            ],
            onClick: ({ key }) => {
              if (key === 'form') navigate('/knowledge/new')
              if (key === 'import') navigate('/knowledge/import')
            },
          }}
        >
          <Button type="primary">
            + 新建 <DownOutlined />
          </Button>
        </Dropdown>
      }
    >
      <Tabs
        activeKey={typeTab}
        onChange={(k) => {
          setTypeTab(k)
          setPage(1)
        }}
        items={[
          { key: 'all', label: `全部(${stats.total})` },
          ...KNOWLEDGE_TYPES.map((t) => ({
            key: t.value,
            label: `${t.label.split(' ')[0]}(${stats.by_type[t.value] ?? 0})`,
          })),
        ]}
      />
      <Space style={{ marginBottom: 16 }} wrap>
        {Object.keys(STATUS_LABEL).map((value) => (
          <Tag.CheckableTag
            key={value}
            checked={status === value}
            onChange={(checked) => {
              setStatus(checked ? value : undefined)
              setPage(1)
            }}
          >
            {value === 'draft' ? '草稿（仅本人）' : STATUS_LABEL[value]}
            {status === value ? ' ✓' : ''}
          </Tag.CheckableTag>
        ))}
        <Input.Search
          placeholder="搜索标题 / kid…"
          style={{ width: 220 }}
          allowClear
          onSearch={(v) => {
            setQ(v || undefined)
            setPage(1)
          }}
        />
        <Input.Search
          placeholder="owner: 全部"
          style={{ width: 160 }}
          allowClear
          onSearch={(v) => {
            setOwner(v || undefined)
            setPage(1)
          }}
        />
      </Space>
      <Table<KnowledgeItem>
        rowKey="kid"
        loading={loading}
        dataSource={items}
        pagination={{ current: page, pageSize: 20, onChange: setPage }}
        columns={[
          { title: '标题', dataIndex: 'title', ellipsis: true },
          {
            title: '类型',
            dataIndex: 'type',
            width: 90,
            render: (t: string) => <Tag color={TYPE_COLOR[t]}>{t}</Tag>,
          },
          { title: 'kid', dataIndex: 'kid', width: 130 },
          {
            title: '状态',
            width: 160,
            render: (_, r) => (
              <Space size={4}>
                <Tag
              color={r.status === 'published' ? 'green' : r.status === 'expired' ? 'red' : 'default'}
                >
                  {STATUS_LABEL[r.status] ?? r.status}
                </Tag>
                {r.status === 'published' && r.index_state === 'indexing' && (
                  <Typography.Text type="secondary">·索引中</Typography.Text>
                )}
              </Space>
            ),
          },
          { title: '版本', dataIndex: 'version', width: 80, render: (v: number) => `v${v}` },
          {
            title: '来源文件',
            width: 160,
            ellipsis: true,
            render: (_, r) =>
              r.source_doc.name ? (
                <a onClick={() => navigate(`/source-docs/${r.source_doc.id}`)}>{r.source_doc.name}</a>
              ) : (
                '—'
              ),
          },
          { title: '负责人', dataIndex: 'owner', width: 110, ellipsis: true },
          {
            title: '过期日期',
            dataIndex: 'expire_date',
            width: 170,
            render: expireCell,
          },
          { title: '近30天命中', dataIndex: 'hits_30d', width: 100 },
          {
            title: '操作',
            width: 170,
            render: (_, r) => (
              <Space size="small">
                <a onClick={() => navigate(`/knowledge/${r.kid}`)}>查看</a>
                {r.status !== 'archived' && (
                  <a
                    onClick={() => {
                      setRenewKid(r.kid)
                      setRenewDate(null)
                    }}
                  >
                    续期
                  </a>
                )}
                {(r.status === 'published' || r.status === 'expired') && (
                  <Popconfirm title="下架为终态不可恢复，确认？" onConfirm={() => archive(r.kid)}>
                    <a style={{ color: '#ff4d4f' }}>下架</a>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Typography.Text type="secondary">共 {stats.total} 条</Typography.Text>

      <Modal
        title={`续期 ${renewKid ?? ''}`}
        open={renewKid !== null}
        onOk={renew}
        onCancel={() => setRenewKid(null)}
        okButtonProps={{ disabled: !renewDate }}
      >
        <DatePicker
          style={{ width: '100%' }}
          value={renewDate}
          onChange={setRenewDate}
          minDate={dayjs()}
        />
      </Modal>
    </Card>
  )
}
