import { Alert, Button, Descriptions, Space, Table, Tag, Typography, message } from 'antd'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import {
  FeishuSyncFailureItem,
  FeishuSyncStatus,
  SYNC_STATUS_COLOR,
  SYNC_STATUS_LABEL,
  triggerFeishuSync,
} from '../api/client'
import { useFeishuSyncPoll } from '../hooks/useFeishuSyncPoll'

interface Props {
  docId: number
  initialSyncStatus?: FeishuSyncStatus | null
}

function duplicateSourceLabel(item: FeishuSyncFailureItem): string {
  const dup = item.duplicate
  if (!dup) return '—'
  if (dup.source_doc_id && dup.source_doc_name) {
    return `${dup.source_doc_name} (#${dup.source_doc_id})`
  }
  if (dup.source_doc_id) return `文档 #${dup.source_doc_id}`
  return '未关联文档'
}

// 飞书同步面板（详情页）：状态展示 + 立即同步 / 我已授权
export default function FeishuSyncPanel({ docId, initialSyncStatus }: Props) {
  const polling = initialSyncStatus === 'syncing' || initialSyncStatus === 'pending'
  const { status, loading, refresh } = useFeishuSyncPoll(docId, true)
  const [syncing, setSyncing] = useState(false)

  const current = status
  const syncStatus = current?.sync_status as FeishuSyncStatus | null | undefined
  const errorDetail = current?.last_sync_error_detail
  const duplicateFailures =
    errorDetail?.failures?.filter((item) => item.reason === 'duplicate_content') ?? []
  const otherFailures =
    errorDetail?.failures?.filter((item) => item.reason !== 'duplicate_content') ?? []

  const doSync = async () => {
    setSyncing(true)
    try {
      await triggerFeishuSync(docId)
      message.success('已触发同步')
      await refresh()
    } finally {
      setSyncing(false)
    }
  }

  if (!current && loading) {
    return <Typography.Text type="secondary">加载同步状态…</Typography.Text>
  }
  if (!current) return null

  return (
    <div style={{ marginBottom: 16 }}>
      <Descriptions
        title="飞书同步"
        size="small"
        bordered
        column={2}
        extra={
          <Space>
            <Button size="small" loading={syncing} onClick={() => void doSync()}>
              立即同步
            </Button>
            {syncStatus === 'awaiting_auth' && (
              <Button size="small" type="primary" loading={syncing} onClick={() => void doSync()}>
                我已授权
              </Button>
            )}
          </Space>
        }
      >
        <Descriptions.Item label="同步状态">
          {syncStatus ? (
            <Tag color={SYNC_STATUS_COLOR[syncStatus]}>{SYNC_STATUS_LABEL[syncStatus]}</Tag>
          ) : (
            '—'
          )}
          {polling && (syncStatus === 'syncing' || syncStatus === 'pending') && (
            <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
              自动刷新中…
            </Typography.Text>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="最近同步">
          {current.last_sync_at?.slice(0, 19).replace('T', ' ') ?? '—'}
        </Descriptions.Item>
        <Descriptions.Item label="飞书原文" span={2}>
          {current.feishu_url ? (
            <a href={current.feishu_url} target="_blank" rel="noreferrer">
              {current.feishu_title || current.feishu_url}
            </a>
          ) : (
            '—'
          )}
        </Descriptions.Item>
        {current.last_sync_error && (
          <Descriptions.Item label="最近错误" span={2}>
            <Typography.Text type="danger">
              {errorDetail?.message || current.last_sync_error}
            </Typography.Text>
          </Descriptions.Item>
        )}
      </Descriptions>

      {syncStatus === 'failed' && errorDetail && (
        <Alert
          style={{ marginTop: 12 }}
          type="error"
          showIcon
          message="同步未完成"
          description={
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Typography.Text>{errorDetail.message}</Typography.Text>
              {duplicateFailures.length > 0 && (
                <div>
                  <Typography.Text strong>与库内重复的知识（{duplicateFailures.length} 条）</Typography.Text>
                  <Table
                    style={{ marginTop: 8 }}
                    size="small"
                    rowKey={(row) => `${row.seq}-${row.duplicate?.kid ?? 'x'}`}
                    pagination={duplicateFailures.length > 10 ? { pageSize: 10, showSizeChanger: false } : false}
                    dataSource={duplicateFailures}
                    columns={[
                      { title: '序号', dataIndex: 'seq', width: 64 },
                      {
                        title: '飞书条目标题',
                        dataIndex: 'title',
                        ellipsis: true,
                        render: (title: string | null | undefined) => title || '—',
                      },
                      {
                        title: '重复知识',
                        key: 'duplicate',
                        render: (_: unknown, row: FeishuSyncFailureItem) =>
                          row.duplicate?.kid ? (
                            <Link to={`/knowledge/${row.duplicate.kid}`}>{row.duplicate.kid}</Link>
                          ) : (
                            '—'
                          ),
                      },
                      {
                        title: '已有标题',
                        key: 'duplicate_title',
                        ellipsis: true,
                        render: (_: unknown, row: FeishuSyncFailureItem) => row.duplicate?.title || '—',
                      },
                      {
                        title: '所属文档',
                        key: 'duplicate_source',
                        ellipsis: true,
                        render: (_: unknown, row: FeishuSyncFailureItem) => duplicateSourceLabel(row),
                      },
                    ]}
                  />
                </div>
              )}
              {otherFailures.length > 0 && (
                <div>
                  <Typography.Text strong>其他失败条目（{otherFailures.length} 条）</Typography.Text>
                  <Table
                    style={{ marginTop: 8 }}
                    size="small"
                    rowKey={(row) => `${row.seq}-${row.reason}`}
                    pagination={otherFailures.length > 10 ? { pageSize: 10, showSizeChanger: false } : false}
                    dataSource={otherFailures}
                    columns={[
                      { title: '序号', dataIndex: 'seq', width: 64 },
                      {
                        title: '标题',
                        dataIndex: 'title',
                        ellipsis: true,
                        render: (title: string | null | undefined) => title || '—',
                      },
                      {
                        title: '原因',
                        dataIndex: 'reason_label',
                        width: 180,
                        render: (label: string | undefined, row: FeishuSyncFailureItem) =>
                          label || row.reason,
                      },
                      {
                        title: '说明',
                        key: 'detail',
                        ellipsis: true,
                        render: (_: unknown, row: FeishuSyncFailureItem) => row.detail || '—',
                      },
                    ]}
                  />
                </div>
              )}
            </Space>
          }
        />
      )}

      {syncStatus === 'awaiting_auth' && (
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message="等待飞书授权"
          description={
            current.awaiting_auth_since
              ? `自 ${current.awaiting_auth_since.slice(0, 19).replace('T', ' ')} 起等待授权，请完成授权后点击「我已授权」。`
              : '请在飞书中完成授权后点击「我已授权」重试同步。'
          }
        />
      )}
      {syncStatus === 'permission_revoked' && (
        <Alert
          style={{ marginTop: 12 }}
          type="error"
          showIcon
          message="飞书权限已撤销"
          description="应用已失去文档访问权限，请在飞书中重新授权后点击「立即同步」。"
        />
      )}
      {syncStatus === 'auth_timeout' && (
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message="授权等待超时"
          description="长时间未完成授权，请重新授权后点击「立即同步」。"
        />
      )}
    </div>
  )
}
