import { ReactNode, useCallback, useEffect, useState } from 'react'
import {
  Button, Card, Descriptions, Input, Popconfirm, Space, Table, Tabs, Tag, Typography, message,
} from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import {
  api, ALIGN_LABEL, SOURCE_LABEL, SourceDocDetailOut, STATUS_LABEL, TYPE_COLOR,
} from '../api/client'

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
  return parts.map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return <Typography.Text code key={index}>{part.slice(1, -1)}</Typography.Text>
    }
    if (part.startsWith('**') && part.endsWith('**')) {
      return <Typography.Text strong key={index}>{part.slice(2, -2)}</Typography.Text>
    }
    return part
  })
}

function MarkdownPreview({ value }: { value: string }) {
  const lines = value.split(/\r?\n/)
  const blocks: ReactNode[] = []

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) continue

    if (trimmed.startsWith('```')) {
      const language = trimmed.slice(3).trim()
      const code: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        code.push(lines[i])
        i += 1
      }
      blocks.push(
        <pre
          key={blocks.length}
          style={{
            background: '#f6f8fa',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            margin: '12px 0',
            overflowX: 'auto',
            padding: 12,
          }}
        >
          {language && (
            <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
              {language}
            </Typography.Text>
          )}
          <code>{code.join('\n')}</code>
        </pre>,
      )
      continue
    }

    if (/^---+$/.test(trimmed)) {
      blocks.push(<div key={blocks.length} style={{ borderTop: '1px solid #f0f0f0', margin: '16px 0' }} />)
      continue
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed)
    if (heading) {
      const level = Math.min(heading[1].length, 4) as 1 | 2 | 3 | 4
      blocks.push(
        <Typography.Title key={blocks.length} level={level} style={{ margin: '16px 0 8px' }}>
          {renderInline(heading[2])}
        </Typography.Title>,
      )
      continue
    }

    if (trimmed.startsWith('>')) {
      const quote: string[] = [trimmed.replace(/^>\s?/, '')]
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith('>')) {
        i += 1
        quote.push(lines[i].trim().replace(/^>\s?/, ''))
      }
      blocks.push(
        <div
          key={blocks.length}
          style={{
            borderLeft: '3px solid #d9d9d9',
            color: '#595959',
            margin: '12px 0',
            paddingLeft: 12,
          }}
        >
          {quote.map((item, index) => (
            <Typography.Paragraph key={index} style={{ marginBottom: index === quote.length - 1 ? 0 : 6 }}>
              {renderInline(item)}
            </Typography.Paragraph>
          ))}
        </div>,
      )
      continue
    }

    if (/^\|.+\|$/.test(trimmed) && i + 1 < lines.length && /^\|?[\s:-]+\|[\s|:-]+$/.test(lines[i + 1].trim())) {
      const rows = [trimmed]
      i += 2
      while (i < lines.length && /^\|.+\|$/.test(lines[i].trim())) {
        rows.push(lines[i].trim())
        i += 1
      }
      i -= 1
      const cells = rows.map((row) =>
        row
          .replace(/^\||\|$/g, '')
          .split('|')
          .map((cell) => cell.trim()),
      )
      const [header, ...body] = cells
      blocks.push(
        <div key={blocks.length} style={{ overflowX: 'auto', margin: '12px 0' }}>
          <table style={{ borderCollapse: 'collapse', minWidth: 480, width: '100%' }}>
            <thead>
              <tr>
                {header.map((cell, index) => (
                  <th key={index} style={{ background: '#fafafa', border: '1px solid #f0f0f0', padding: '8px 10px', textAlign: 'left' }}>
                    {renderInline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} style={{ border: '1px solid #f0f0f0', padding: '8px 10px' }}>
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [trimmed.replace(/^[-*]\s+/, '')]
      while (i + 1 < lines.length && /^[-*]\s+/.test(lines[i + 1].trim())) {
        i += 1
        items.push(lines[i].trim().replace(/^[-*]\s+/, ''))
      }
      blocks.push(
        <ul key={blocks.length} style={{ margin: '8px 0 12px', paddingLeft: 24 }}>
          {items.map((item, index) => <li key={index}>{renderInline(item)}</li>)}
        </ul>,
      )
      continue
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [trimmed.replace(/^\d+\.\s+/, '')]
      while (i + 1 < lines.length && /^\d+\.\s+/.test(lines[i + 1].trim())) {
        i += 1
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ''))
      }
      blocks.push(
        <ol key={blocks.length} style={{ margin: '8px 0 12px', paddingLeft: 24 }}>
          {items.map((item, index) => <li key={index}>{renderInline(item)}</li>)}
        </ol>,
      )
      continue
    }

    const paragraph = [trimmed]
    while (
      i + 1 < lines.length &&
      lines[i + 1].trim() &&
      !/^(#{1,4})\s+/.test(lines[i + 1].trim()) &&
      !/^([-*]|\d+\.)\s+/.test(lines[i + 1].trim()) &&
      !lines[i + 1].trim().startsWith('>') &&
      !lines[i + 1].trim().startsWith('```') &&
      !/^---+$/.test(lines[i + 1].trim())
    ) {
      i += 1
      paragraph.push(lines[i].trim())
    }

    blocks.push(
      <Typography.Paragraph key={blocks.length} style={{ lineHeight: 1.8, marginBottom: 12 }}>
        {renderInline(paragraph.join(' '))}
      </Typography.Paragraph>,
    )
  }

  if (!blocks.length) {
    return <Typography.Text type="secondary">暂无内容</Typography.Text>
  }

  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #f0f0f0',
        borderRadius: 6,
        padding: 20,
      }}
    >
      {blocks}
    </div>
  )
}

// 知识文件详情（spec §4.2/§4.3）：条目视图 / 全文视图（可在线编辑）/ 变更历史
export default function SourceDocDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [doc, setDoc] = useState<SourceDocDetailOut | null>(null)
  const [markdown, setMarkdown] = useState('')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [newName, setNewName] = useState('')

  const load = useCallback(() => {
    api.get(`/api/source-docs/${id}`).then((r) => setDoc(r.data))
    api.get(`/api/source-docs/${id}/content`).then((r) => setMarkdown(r.data.markdown))
  }, [id])
  useEffect(() => {
    load()
  }, [load])

  if (!doc) return null
  const active = doc.status === 'active'

  const submitEdit = async () => {
    const data = new FormData()
    data.append('text', draft)
    const resp = await api.post(`/api/source-docs/${id}/update`, data)
    navigate(`/knowledge/import?docId=${id}&batchId=${resp.data.id}`)
  }

  const rename = async () => {
    if (!newName.trim()) {
      message.warning('名称不能为空')
      return
    }
    await api.patch(`/api/source-docs/${id}`, { name: newName })
    message.success('已重命名')
    setRenaming(false)
    load()
  }

  return (
    <Card
      title={
        <Space>
          {renaming ? (
            <Space.Compact>
              <Input defaultValue={doc.name} onChange={(e) => setNewName(e.target.value)} />
              <Button type="primary" onClick={rename}>保存</Button>
            </Space.Compact>
          ) : (
            <>
              {doc.name}
              {active && (
                <a
                  style={{ fontSize: 13 }}
                  onClick={() => {
                    setNewName(doc.name)
                    setRenaming(true)
                  }}
                >
                  重命名
                </a>
              )}
            </>
          )}
          <Tag color={TYPE_COLOR[doc.type]}>{doc.type}</Tag>
          {active ? <Tag color="green">在用</Tag> : <Tag>已归档</Tag>}
        </Space>
      }
      extra={
        active && (
          <Space>
            <Button onClick={() => navigate(`/knowledge/import?docId=${id}`)}>粘贴新版本</Button>
            <Button
              onClick={async () => {
                const r = await api.post(`/api/source-docs/${id}/renew`, {})
                message.success(`已续期 ${r.data.renewed} 条`)
                load()
              }}
            >
              整体续期
            </Button>
            <Popconfirm title="下架全部在架条目并归档文件？" onConfirm={async () => {
              await api.post(`/api/source-docs/${id}/offline`)
              load()
            }}>
              <Button danger>整体下架</Button>
            </Popconfirm>
          </Space>
        )
      }
    >
      <Descriptions size="small" column={4} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="domain">{doc.domain}</Descriptions.Item>
        <Descriptions.Item label="来源">{SOURCE_LABEL[doc.source]}</Descriptions.Item>
        <Descriptions.Item label="条目">{doc.entry_published}/{doc.entry_total}</Descriptions.Item>
        <Descriptions.Item label="最近更新">{doc.updated_at.slice(0, 19).replace('T', ' ')}</Descriptions.Item>
      </Descriptions>

      <Tabs
        items={[
          {
            key: 'entries',
            label: `条目（${doc.entries.length}）`,
            children: (
              <Table
                rowKey="kid"
                size="small"
                pagination={false}
                dataSource={doc.entries}
                columns={[
                  { title: '#', dataIndex: 'doc_seq', width: 50 },
                  {
                    title: '标题', dataIndex: 'title',
                    render: (t, r) => <a onClick={() => navigate(`/knowledge/${r.kid}`)}>{t}</a>,
                  },
                  { title: 'kid', dataIndex: 'kid' },
                  { title: '状态', dataIndex: 'status', render: (s) => STATUS_LABEL[s] ?? s },
                  { title: '版本', dataIndex: 'version', render: (v) => `v${v}` },
                  { title: '过期日期', dataIndex: 'expire_date' },
                ]}
              />
            ),
          },
          {
            key: 'content',
            label: '全文',
            children: editing ? (
              <>
                <Input.TextArea rows={24} value={draft} onChange={(e) => setDraft(e.target.value)} />
                <Space style={{ marginTop: 12 }}>
                  <Button type="primary" onClick={submitEdit}>提交（进入对齐预览）</Button>
                  <Button onClick={() => setEditing(false)}>取消</Button>
                </Space>
              </>
            ) : (
              <>
                {active && (
                  <Space style={{ marginBottom: 12 }}>
                    <Button onClick={() => { setDraft(markdown); setEditing(true) }}>编辑全文</Button>
                    <Button onClick={() => { navigator.clipboard.writeText(markdown); message.success('已复制') }}>
                      复制全文
                    </Button>
                  </Space>
                )}
                <MarkdownPreview value={markdown} />
              </>
            ),
          },
          {
            key: 'history',
            label: `变更历史（${doc.batches.length}）`,
            children: (
              <Table
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={doc.batches}
                columns={[
                  { title: '时间', dataIndex: 'created_at', render: (v) => v.slice(0, 19).replace('T', ' ') },
                  { title: '操作人', dataIndex: 'created_by' },
                  { title: '方式', dataIndex: 'origin', render: (o) => (o === 'manual' ? '粘贴/编辑' : '上传') },
                  {
                    title: '变化',
                    dataIndex: 'stats',
                    render: (s: Record<string, number>) =>
                      Object.entries(s).map(([k, n]) => `${ALIGN_LABEL[k] ?? k} ${n}`).join('，') || '—',
                  },
                ]}
              />
            ),
          },
        ]}
      />
    </Card>
  )
}
