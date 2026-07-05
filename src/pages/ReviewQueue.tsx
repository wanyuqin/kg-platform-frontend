import { useCallback, useEffect, useState } from 'react'
import { Card, Input, Modal, Select, Space, Table, Tabs, message } from 'antd'
import { useNavigate } from 'react-router-dom'

import {
  REVIEW_TASK_TYPE_LABEL,
  ReviewTaskItem,
  ReviewTaskType,
  approveReviewTask,
  domainSelectOption,
  DomainItem,
  fetchReviewTasks,
  rejectReviewTask,
  api,
} from '../api/client'

const TASK_TABS: { key: ReviewTaskType; label: string }[] = [
  { key: 'risk', label: REVIEW_TASK_TYPE_LABEL.risk },
  { key: 'manual_fill', label: REVIEW_TASK_TYPE_LABEL.manual_fill },
  { key: 'conflict', label: REVIEW_TASK_TYPE_LABEL.conflict },
]

// 审核待办（P2，设计 7.2）：三 tab + 通过/驳回
export default function ReviewQueue() {
  const navigate = useNavigate()
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [domain, setDomain] = useState<string>()
  const [taskType, setTaskType] = useState<ReviewTaskType>('risk')
  const [items, setItems] = useState<ReviewTaskItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState('')
  const [rejectId, setRejectId] = useState<number | null>(null)
  const [acting, setActing] = useState(false)

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => {
      const list = resp.data.items as DomainItem[]
      setDomains(list)
      setDomain(list[0]?.code)
    })
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchReviewTasks({
        domain,
        task_type: taskType,
        status: 'pending',
        page,
        page_size: 20,
      })
      setItems(data.items)
      setTotal(data.total)
    } finally {
      setLoading(false)
    }
  }, [domain, taskType, page])

  useEffect(() => {
    void load()
  }, [load])

  const approve = async (id: number) => {
    setActing(true)
    try {
      await approveReviewTask(id)
      message.success('已通过审核')
      void load()
    } finally {
      setActing(false)
    }
  }

  const openReject = (id: number) => {
    setRejectId(id)
    setRejectReason('')
    setRejectOpen(true)
  }

  const submitReject = async () => {
    if (!rejectId || !rejectReason.trim()) {
      message.warning('请填写驳回理由')
      return
    }
    setActing(true)
    try {
      await rejectReviewTask(rejectId, rejectReason.trim())
      message.success('已驳回')
      setRejectOpen(false)
      void load()
    } finally {
      setActing(false)
    }
  }

  const columns = [
    {
      title: 'kid',
      dataIndex: 'kid',
      render: (kid: string) => <a onClick={() => navigate(`/knowledge/${kid}`)}>{kid}</a>,
    },
    {
      title: '标题',
      render: (_: unknown, r: ReviewTaskItem) => r.knowledge?.title ?? '—',
    },
    { title: '知识域', dataIndex: 'domain' },
    {
      title: '风险说明',
      dataIndex: 'risk_note',
      ellipsis: true,
      render: (v: string | null, r: ReviewTaskItem) => v || r.knowledge?.risk_note || '—',
    },
    {
      title: '提交人',
      render: (_: unknown, r: ReviewTaskItem) => r.submitter_name || r.submitter_id,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      render: (v: string) => v.slice(0, 19).replace('T', ' '),
    },
    {
      title: '操作',
      render: (_: unknown, r: ReviewTaskItem) => (
        <Space>
          <a onClick={() => navigate(`/knowledge/${r.kid}`)}>详情</a>
          <a onClick={() => void approve(r.id)}>通过</a>
          <a style={{ color: '#cf1322' }} onClick={() => openReject(r.id)}>
            驳回
          </a>
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="审核待办"
      extra={
        <Select
          allowClear
          placeholder="知识域"
          style={{ width: 280 }}
          value={domain}
          onChange={(v) => {
            setDomain(v)
            setPage(1)
          }}
          options={domains.map(domainSelectOption)}
        />
      }
    >
      <Tabs
        activeKey={taskType}
        onChange={(k) => {
          setTaskType(k as ReviewTaskType)
          setPage(1)
        }}
        items={TASK_TABS.map((t) => ({ key: t.key, label: t.label }))}
      />
      <Table<ReviewTaskItem>
        rowKey="id"
        loading={loading || acting}
        dataSource={items}
        columns={columns}
        pagination={{ current: page, pageSize: 20, total, onChange: setPage }}
      />
      <Modal
        title="驳回审核"
        open={rejectOpen}
        onCancel={() => setRejectOpen(false)}
        onOk={() => void submitReject()}
        confirmLoading={acting}
        okText="确认驳回"
      >
        <Input.TextArea
          rows={4}
          placeholder="驳回理由（必填）"
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
        />
      </Modal>
    </Card>
  )
}
