import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Input, Popconfirm, Progress, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'

import {
  api,
  domainSelectOption,
  DomainItem,
  SOURCE_LABEL,
  SourceDocItem,
  SYNC_STATUS_COLOR,
  SYNC_STATUS_LABEL,
  FeishuSyncStatus,
  TYPE_COLOR,
  triggerFeishuSync,
} from '../api/client'

function IndexProgressCell({ r }: { r: SourceDocItem }) {
  const { entry_published, index_ready, index_indexing, index_failed } = r
  if (entry_published === 0) {
    return <Typography.Text type="secondary">—</Typography.Text>
  }
  const percent = Math.round((index_ready / entry_published) * 100)
  const hasFailed = index_failed > 0 && index_ready < entry_published
  const status = index_indexing > 0 ? 'active' : hasFailed ? 'exception' : 'success'
  const parts = [`${index_ready}/${entry_published} 已索引`]
  if (index_indexing > 0) parts.push(`${index_indexing} 索引中`)
  if (index_failed > 0) parts.push(`${index_failed} 失败`)

  return (
    <Tooltip title={`在架/总量：${r.entry_published}/${r.entry_total}`}>
      <div style={{ minWidth: 160 }}>
        <Progress percent={percent} size="small" status={status} showInfo={false} />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {parts.join(' · ')}
        </Typography.Text>
      </div>
    </Tooltip>
  )
}

// 知识文件列表（spec §4.2）：domain → 文件 → 条目 的中间层视图
export default function SourceDocList() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [domain, setDomain] = useState<string | undefined>(searchParams.get('domain') ?? undefined)
  const [status, setStatus] = useState<string>()
  const [q, setQ] = useState('')
  const [items, setItems] = useState<SourceDocItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => {
      const list = resp.data.items
      setDomains(list)
      const requestedDomain = searchParams.get('domain') ?? undefined
      const fallbackDomain = list[0]?.code
      setDomain(list.some((d: DomainItem) => d.code === requestedDomain) ? requestedDomain : fallbackDomain)
    })
  }, [searchParams])

  const load = useCallback(() => {
    if (!domain) return
    setLoading(true)
    api
      .get('/api/source-docs', { params: { domain, status, q: q || undefined, page, page_size: 20 } })
      .then((resp) => {
        setItems(resp.data.items)
        setTotal(resp.data.total)
      })
      .finally(() => setLoading(false))
  }, [domain, status, q, page])

  useEffect(load, [load])

  const selectDomain = (value: string) => {
    setDomain(value)
    setPage(1)
    setSearchParams({ domain: value })
  }

  const goEntries = (doc: SourceDocItem) => {
    navigate(
      `/knowledge?domain=${encodeURIComponent(doc.domain)}&source_doc_id=${doc.id}&source_doc_name=${encodeURIComponent(doc.name)}`,
    )
  }

  const offline = async (id: number) => {
    const resp = await api.post(`/api/source-docs/${id}/offline`)
    message.success(`已下架 ${resp.data.archived_entries} 条并归档文件`)
    load()
  }

  const renew = async (id: number) => {
    const resp = await api.post(`/api/source-docs/${id}/renew`, {})
    message.success(`已续期 ${resp.data.renewed} 条至 ${resp.data.expire_date}`)
    load()
  }

  const syncFeishu = async (id: number) => {
    await triggerFeishuSync(id)
    message.success('已触发飞书同步')
    load()
  }

  return (
    <Card
      title={
        <Space>
          知识文件
          <Select
            style={{ width: 280 }}
            value={domain}
            onChange={selectDomain}
            options={domains.map(domainSelectOption)}
          />
          <Typography.Text type="secondary" style={{ fontWeight: 'normal', fontSize: 13 }}>
            点击文件进入该文件的知识条目列表
          </Typography.Text>
        </Space>
      }
      extra={
        <Space>
          <Button
            type="primary"
            onClick={() =>
              navigate(
                `/knowledge/import?mode=create${domain ? `&domain=${encodeURIComponent(domain)}` : ''}`,
              )
            }
          >
            + 新建
          </Button>
          <Button
            onClick={() =>
              navigate(
                `/knowledge/import?mode=upload${domain ? `&domain=${encodeURIComponent(domain)}` : ''}`,
              )
            }
          >
            上传
          </Button>
          <Button
            onClick={() =>
              navigate(
                `/source-docs/feishu/new${domain ? `?domain=${encodeURIComponent(domain)}` : ''}`,
              )
            }
          >
            注册飞书文档
          </Button>
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={[
            { value: 'active', label: '在用' },
            { value: 'archived', label: '已归档' },
          ]}
        />
        <Input.Search
          placeholder="搜索名称"
          style={{ width: 220 }}
          onSearch={(v) => {
            setQ(v)
            setPage(1)
          }}
          allowClear
        />
      </Space>
      <Table<SourceDocItem>
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={{ current: page, pageSize: 20, total, onChange: setPage }}
        columns={[
          {
            title: '名称',
            dataIndex: 'name',
            render: (name, r) => <a onClick={() => goEntries(r)}>{name}</a>,
          },
          {
            title: '类型',
            dataIndex: 'type',
            render: (t) => <Tag color={TYPE_COLOR[t]}>{t}</Tag>,
          },
          { title: '来源', dataIndex: 'source', render: (s) => SOURCE_LABEL[s] ?? s },
          {
            title: '同步状态',
            render: (_, r) =>
              r.source === 'feishu' && r.sync_status ? (
                <Tag color={SYNC_STATUS_COLOR[r.sync_status as FeishuSyncStatus]}>
                  {SYNC_STATUS_LABEL[r.sync_status as FeishuSyncStatus]}
                </Tag>
              ) : (
                <Typography.Text type="secondary">—</Typography.Text>
              ),
          },
          {
            title: '条目数（在架/总）',
            render: (_, r) => `${r.entry_published}/${r.entry_total}`,
          },
          {
            title: '索引进度',
            width: 200,
            render: (_, r) => <IndexProgressCell r={r} />,
          },
          {
            title: '状态',
            dataIndex: 'status',
            render: (s) => (s === 'active' ? <Tag color="green">在用</Tag> : <Tag>已归档</Tag>),
          },
          { title: '最近更新', dataIndex: 'updated_at', render: (v) => v.slice(0, 19).replace('T', ' ') },
          {
            title: '操作',
            render: (_, r) => (
              <Space>
                <a onClick={() => goEntries(r)}>条目</a>
                <a onClick={() => navigate(`/source-docs/${r.id}`)}>详情</a>
                {r.source === 'feishu' && r.status === 'active' && (
                  <a onClick={() => void syncFeishu(r.id)}>立即同步</a>
                )}
                {r.status === 'active' && r.source !== 'feishu' && (
                  <a onClick={() => navigate(`/knowledge/import?docId=${r.id}`)}>更新</a>
                )}
                {r.status === 'active' && (
                  <>
                    <a onClick={() => renew(r.id)}>整体续期</a>
                    <Popconfirm
                      title={`将下架该文件全部 ${r.entry_published} 条在架条目并归档，确认？`}
                      onConfirm={() => offline(r.id)}
                    >
                      <a style={{ color: '#cf1322' }}>整体下架</a>
                    </Popconfirm>
                  </>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Typography.Text type="secondary">共 {total} 个文件</Typography.Text>
    </Card>
  )
}
