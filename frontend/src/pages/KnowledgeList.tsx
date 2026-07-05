import { useCallback, useEffect, useState } from 'react'
import { DownOutlined } from '@ant-design/icons'
import { Button, Card, Dropdown, Select, Space, Tabs, Typography } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'

import KnowledgeEntryTable from '../components/KnowledgeEntryTable'
import {
  api,
  domainDisplayLabel,
  domainSelectOption,
  DomainItem,
  KNOWLEDGE_TYPES,
  KnowledgeStats,
} from '../api/client'

// ⑤ 知识列表页（控制台主页）：domain 切换、类型 tab 计数、状态 chip、
// 标题/kid 搜索、owner 筛选、三合一新建入口、行内操作（线稿 7.2-⑤）
export default function KnowledgeList() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [domain, setDomain] = useState<string | undefined>(searchParams.get('domain') ?? undefined)
  const [sourceDocId, setSourceDocId] = useState<number | undefined>(() => {
    const raw = searchParams.get('source_doc_id')
    return raw ? Number(raw) : undefined
  })
  const [sourceDocName, setSourceDocName] = useState<string | undefined>(
    searchParams.get('source_doc_name') ?? undefined,
  )
  const [stats, setStats] = useState<KnowledgeStats>({ total: 0, by_type: {} })
  const [typeTab, setTypeTab] = useState('all')

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => {
      setDomains(resp.data.items)
      const requestedDomain = searchParams.get('domain') ?? undefined
      const first = resp.data.items.find((d: DomainItem) => d.code !== 'common')
      setDomain(requestedDomain ?? first?.code ?? resp.data.items[0]?.code)
    })
  }, [searchParams])

  useEffect(() => {
    const nextDomain = searchParams.get('domain') ?? undefined
    const nextSourceDocId = searchParams.get('source_doc_id')
    setDomain(nextDomain ?? domain)
    setSourceDocId(nextSourceDocId ? Number(nextSourceDocId) : undefined)
    setSourceDocName(searchParams.get('source_doc_name') ?? undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  const loadStats = useCallback(async () => {
    if (!domain) return
    const statsResp = await api.get('/api/knowledge/stats', {
      params: { domain, source_doc_id: sourceDocId },
    })
    setStats(statsResp.data)
  }, [domain, sourceDocId])

  useEffect(() => {
    loadStats()
  }, [loadStats])

  const selectDomain = (value: string) => {
    setDomain(value)
    setSourceDocId(undefined)
    setSourceDocName(undefined)
    setSearchParams({ domain: value })
  }

  return (
    <Card
      title={
        <Space>
          知识条目
          <Select
            size="small"
            style={{ width: 280 }}
            value={domain}
            onChange={selectDomain}
            options={domains.map(domainSelectOption)}
          />
          <Typography.Text type="secondary" style={{ fontWeight: 'normal', fontSize: 13 }}>
            {sourceDocId
              ? `来源文件：${sourceDocName ?? sourceDocId}`
              : `当前知识域：${domain ? domainDisplayLabel(domains, domain) : '—'}`}
          </Typography.Text>
        </Space>
      }
      extra={
        <Space>
          {domain && (
            <Button onClick={() => navigate(`/source-docs?domain=${encodeURIComponent(domain)}`)}>
              返回文件列表
            </Button>
          )}
          <Dropdown
            menu={{
              items: [
                { key: 'form', label: '表单创建' },
                { key: 'import', label: '上传 Markdown' },
                { key: 'feishu', label: '注册飞书文档' },
              ],
              onClick: ({ key }) => {
                const qs = domain ? `?domain=${encodeURIComponent(domain)}` : ''
                if (key === 'form') navigate(`/knowledge/new${qs}`)
                if (key === 'import') navigate(`/knowledge/import${qs}`)
                if (key === 'feishu') navigate(`/source-docs/feishu/new${qs}`)
              },
            }}
          >
            <Button type="primary">
              + 新建 <DownOutlined />
            </Button>
          </Dropdown>
        </Space>
      }
    >
      <Tabs
        activeKey={typeTab}
        onChange={setTypeTab}
        items={[
          { key: 'all', label: `全部(${stats.total})` },
          ...KNOWLEDGE_TYPES.map((t) => ({
            key: t.value,
            label: `${t.label.split(' ')[0]}(${stats.by_type[t.value] ?? 0})`,
          })),
        ]}
      />
      {domain && (
        <KnowledgeEntryTable
          domain={domain}
          sourceDocId={sourceDocId}
          typeFilter={typeTab === 'all' ? undefined : typeTab}
        />
      )}
    </Card>
  )
}
