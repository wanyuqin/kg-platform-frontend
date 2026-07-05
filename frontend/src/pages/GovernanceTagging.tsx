import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, Input, Modal, Select, Space, Table, Tag, message } from 'antd'
import { useNavigate } from 'react-router-dom'

import { AuditLogItem, api, patchKnowledgeMeta } from '../api/client'

interface SearchHitRow {
  key: string
  ts: string
  query: string
  kid: string
  version: number
  score: number
  key_id: string
}

// 打标页（ADR-0010）：从 search 审计命中定位知识，人工改 tags
export default function GovernanceTagging() {
  const navigate = useNavigate()
  const [items, setItems] = useState<AuditLogItem[]>([])
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [keyId, setKeyId] = useState<string>()
  const [tagOpen, setTagOpen] = useState(false)
  const [tagKid, setTagKid] = useState('')
  const [tags, setTags] = useState<string[]>(['治理:待复核'])
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await api.get('/api/audit-logs', {
        params: { action: 'search', page, page_size: 50, key_id: keyId || undefined },
      })
      setItems(resp.data.items)
    } finally {
      setLoading(false)
    }
  }, [page, keyId])

  useEffect(() => {
    void load()
  }, [load])

  const rows: SearchHitRow[] = useMemo(() => {
    const flat: SearchHitRow[] = []
    for (const log of items) {
      for (const hit of log.hits ?? []) {
        flat.push({
          key: `${log.ts}-${hit.kid}-${hit.version}`,
          ts: log.ts,
          query: log.query ?? '',
          kid: hit.kid,
          version: hit.version,
          score: hit.score,
          key_id: log.key_id,
        })
      }
    }
    return flat
  }, [items])

  const openTag = (kid: string) => {
    setTagKid(kid)
    setTags(['治理:待复核'])
    setTagOpen(true)
  }

  const saveTags = async () => {
    if (!tagKid) return
    setSaving(true)
    try {
      await patchKnowledgeMeta(tagKid, { tags })
      message.success('已更新 tags')
      setTagOpen(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title="打标（search 审计命中）">
      <Space style={{ marginBottom: 16 }}>
        <Input.Search
          placeholder="key_id 过滤"
          style={{ width: 200 }}
          allowClear
          onSearch={(v) => {
            setKeyId(v || undefined)
            setPage(1)
          }}
        />
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table<SearchHitRow>
        rowKey="key"
        loading={loading}
        dataSource={rows}
        pagination={{ current: page, pageSize: 50, onChange: setPage }}
        columns={[
          { title: '时间', dataIndex: 'ts', width: 200, ellipsis: true },
          { title: 'query', dataIndex: 'query', ellipsis: true },
          {
            title: 'kid',
            dataIndex: 'kid',
            render: (kid: string) => <a onClick={() => navigate(`/knowledge/${kid}`)}>{kid}</a>,
          },
          { title: 'version', dataIndex: 'version', width: 80 },
          {
            title: 'score',
            dataIndex: 'score',
            width: 80,
            render: (s: number) => <Tag>{s.toFixed(3)}</Tag>,
          },
          { title: 'key_id', dataIndex: 'key_id', width: 110 },
          {
            title: '操作',
            width: 120,
            render: (_: unknown, r: SearchHitRow) => (
              <Space>
                <a onClick={() => navigate(`/knowledge/${r.kid}`)}>详情</a>
                <a onClick={() => openTag(r.kid)}>打标</a>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={`打标 · ${tagKid}`}
        open={tagOpen}
        onCancel={() => setTagOpen(false)}
        onOk={() => void saveTags()}
        confirmLoading={saving}
      >
        <Select
          mode="tags"
          style={{ width: '100%' }}
          placeholder="输入 tags，回车添加"
          value={tags}
          onChange={setTags}
        />
      </Modal>
    </Card>
  )
}
