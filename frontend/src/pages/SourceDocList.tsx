import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Input, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { api, DomainItem, SOURCE_LABEL, SourceDocItem, TYPE_COLOR } from '../api/client'

// 知识文件列表（spec §4.2）：domain → 文件 → 条目 的中间层视图
export default function SourceDocList() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [domain, setDomain] = useState<string | undefined>(searchParams.get('domain') ?? undefined)
  const [status, setStatus] = useState<string>()
  const [q, setQ] = useState('')
  const [items, setItems] = useState<SourceDocItem[]>([])
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
      .get('/api/source-docs', { params: { domain, status, q: q || undefined } })
      .then((resp) => setItems(resp.data.items))
      .finally(() => setLoading(false))
  }, [domain, status, q])

  useEffect(load, [load])

  const selectDomain = (value: string) => {
    setDomain(value)
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

  return (
    <Card
      title={
        <Space>
          知识文件
          <Select
            style={{ width: 180 }}
            value={domain}
            onChange={selectDomain}
            options={domains.map((d) => ({ value: d.code, label: d.code }))}
          />
          <Typography.Text type="secondary" style={{ fontWeight: 'normal', fontSize: 13 }}>
            点击文件进入该文件的知识条目列表
          </Typography.Text>
        </Space>
      }
      extra={
        <Button type="primary" onClick={() => navigate('/knowledge/import')}>
          + 新建（粘贴 / 上传）
        </Button>
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'active', label: '在用' },
            { value: 'archived', label: '已归档' },
          ]}
        />
        <Input.Search placeholder="搜索名称" style={{ width: 220 }} onSearch={setQ} allowClear />
      </Space>
      <Table<SourceDocItem>
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={false}
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
            title: '条目数（在架/总）',
            render: (_, r) => `${r.entry_published}/${r.entry_total}`,
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
                {r.status === 'active' && (
                  <>
                    <a onClick={() => navigate(`/knowledge/import?docId=${r.id}`)}>更新</a>
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
    </Card>
  )
}
