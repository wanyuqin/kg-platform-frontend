import { useCallback, useEffect, useState } from 'react'
import { DatePicker, Input, Modal, Popconfirm, Space, Table, Tag, Typography, message } from 'antd'
import dayjs, { Dayjs } from 'dayjs'
import { useNavigate } from 'react-router-dom'

import {
  api,
  daysUntil,
  KnowledgeItem,
  STATUS_LABEL,
  TYPE_COLOR,
} from '../api/client'

export interface KnowledgeEntryTableProps {
  domain: string
  sourceDocId?: number
  hideSourceDocColumn?: boolean
  /** 类型筛选；undefined 表示全部类型 */
  typeFilter?: string
}

export default function KnowledgeEntryTable({
  domain,
  sourceDocId,
  hideSourceDocColumn = false,
  typeFilter,
}: KnowledgeEntryTableProps) {
  const navigate = useNavigate()
  const [status, setStatus] = useState<string | undefined>('published')
  const [q, setQ] = useState<string>()
  const [owner, setOwner] = useState<string>()
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [renewKid, setRenewKid] = useState<string | null>(null)
  const [renewDate, setRenewDate] = useState<Dayjs | null>(null)

  useEffect(() => {
    setPage(1)
  }, [domain, sourceDocId, typeFilter])

  const load = useCallback(async () => {
    if (!domain) return
    setLoading(true)
    try {
      const resp = await api.get('/api/knowledge', {
        params: {
          domain,
          source_doc_id: sourceDocId,
          page,
          page_size: 20,
          type: typeFilter,
          status,
          q: q || undefined,
          owner: owner || undefined,
        },
      })
      setItems(resp.data.items)
      setTotal(resp.data.total)
    } finally {
      setLoading(false)
    }
  }, [domain, sourceDocId, typeFilter, page, status, q, owner])

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

  const columns = [
    { title: '标题', dataIndex: 'title', width: 320, ellipsis: { showTitle: true } },
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
      render: (_: unknown, r: KnowledgeItem) => (
        <Space size={4}>
          <Tag
            color={
              r.status === 'published' ? 'green' : r.status === 'expired' ? 'red' : 'default'
            }
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
    ...(!hideSourceDocColumn
      ? [
          {
            title: '来源文件',
            width: 160,
            ellipsis: true,
            render: (_: unknown, r: KnowledgeItem) =>
              r.source_doc.name ? (
                <a onClick={() => navigate(`/source-docs/${r.source_doc.id}`)}>{r.source_doc.name}</a>
              ) : (
                '—'
              ),
          },
        ]
      : []),
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
      render: (_: unknown, r: KnowledgeItem) => (
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
  ]

  return (
    <>
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
        pagination={{ current: page, pageSize: 20, total, onChange: setPage }}
        scroll={{ x: hideSourceDocColumn ? 1300 : 1460 }}
        tableLayout="fixed"
        columns={columns}
      />
      <Typography.Text type="secondary">共 {total} 条</Typography.Text>

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
    </>
  )
}
