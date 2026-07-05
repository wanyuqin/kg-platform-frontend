import { useCallback, useEffect, useState } from 'react'
import {
  Button, Card, Descriptions, Input, Popconfirm, Space, Table, Tabs, Tag, message,
} from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import MarkdownEditor from '../components/MarkdownEditor'
import MarkdownPreview from '../components/MarkdownPreview'
import KnowledgeEntryTable from '../components/KnowledgeEntryTable'
import {
  api,
  ALIGN_LABEL,
  domainDisplayLabel,
  DomainItem,
  SOURCE_LABEL,
  SourceDocDetailOut,
  TYPE_COLOR,
} from '../api/client'

// 知识文件详情（spec §4.2/§4.3）：条目视图 / 全文视图（可在线编辑）/ 变更历史
export default function SourceDocDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [doc, setDoc] = useState<SourceDocDetailOut | null>(null)
  const [domains, setDomains] = useState<DomainItem[]>([])
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
    api.get('/api/domains/mine').then((resp) => setDomains(resp.data.items))
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
            <Button onClick={() => navigate(`/knowledge/import?docId=${id}`)}>编辑更新</Button>
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
        <Descriptions.Item label="知识域">
          {domainDisplayLabel(domains, doc.domain)}
        </Descriptions.Item>
        <Descriptions.Item label="来源">{SOURCE_LABEL[doc.source]}</Descriptions.Item>
        <Descriptions.Item label="条目">{doc.entry_published}/{doc.entry_total}</Descriptions.Item>
        <Descriptions.Item label="最近更新">{doc.updated_at.slice(0, 19).replace('T', ' ')}</Descriptions.Item>
      </Descriptions>

      <Tabs
        items={[
          {
            key: 'entries',
            label: `条目（${doc.entry_total}）`,
            children: (
              <KnowledgeEntryTable
                domain={doc.domain}
                sourceDocId={Number(id)}
                hideSourceDocColumn
              />
            ),
          },
          {
            key: 'content',
            label: '全文',
            children: editing ? (
              <>
                <MarkdownEditor
                  value={draft}
                  onChange={setDraft}
                  placeholder="编辑全文 Markdown（一级标题为条目边界）"
                />
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
                  { title: '方式', dataIndex: 'origin', render: (o) => (o === 'manual' ? '在线编辑' : '上传') },
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
