import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Input, Select, Space, Table, Tag } from 'antd'
import { useSearchParams } from 'react-router-dom'

import { api, AuditLogItem } from '../api/client'

// 审计查询页（P1，平台管理员）：180 天日志检索与 CSV 导出（技术 十一）
export default function AuditLogs() {
  const [searchParams] = useSearchParams()
  const [items, setItems] = useState<AuditLogItem[]>([])
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<{ action?: string; key_id?: string }>(() => ({
    key_id: searchParams.get('key_id') ?? undefined,
  }))

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await api.get('/api/audit-logs', {
        params: { page, page_size: 50, ...filters },
      })
      setItems(resp.data.items)
    } finally {
      setLoading(false)
    }
  }, [page, filters])

  useEffect(() => {
    load()
  }, [load])

  const exportCsv = () => {
    const params = new URLSearchParams(
      Object.entries(filters).filter(([, v]) => v) as [string, string][],
    )
    window.open(`/api/audit-logs/export?${params.toString()}`)
  }

  return (
    <Card title="审计查询（保留 180 天）" extra={<Button onClick={exportCsv}>导出 CSV</Button>}>
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="action"
          style={{ width: 140 }}
          options={[
            { value: 'search', label: 'search' },
            { value: 'read', label: 'read' },
          ]}
          onChange={(v) => setFilters((f) => ({ ...f, action: v }))}
        />
        <Input.Search
          placeholder="key_id"
          style={{ width: 200 }}
          allowClear
          defaultValue={searchParams.get('key_id') ?? undefined}
          onSearch={(v) => setFilters((f) => ({ ...f, key_id: v || undefined }))}
        />
      </Space>
      <Table<AuditLogItem>
        rowKey={(r) => `${r.ts}-${r.key_id}-${r.latency_ms}`}
        loading={loading}
        dataSource={items}
        pagination={{ current: page, pageSize: 50, onChange: setPage }}
        columns={[
          { title: '时间', dataIndex: 'ts', width: 200, ellipsis: true },
          { title: 'key', dataIndex: 'key_id', width: 110 },
          {
            title: 'action',
            dataIndex: 'action',
            width: 90,
            render: (a: string) => <Tag color={a === 'search' ? 'blue' : 'purple'}>{a}</Tag>,
          },
          { title: 'query', dataIndex: 'query', ellipsis: true },
          {
            title: '命中 / kid',
            render: (_, r) =>
              r.action === 'read'
                ? `${r.kid} v${r.version}`
                : (r.hits ?? []).map((h) => `${h.kid} v${h.version}`).join('，') || '无命中',
          },
          {
            title: '过期剔除',
            dataIndex: 'excluded_expired',
            width: 90,
            render: (v: number | null) => v ?? '—',
          },
          { title: '耗时(ms)', dataIndex: 'latency_ms', width: 90 },
        ]}
      />
    </Card>
  )
}
