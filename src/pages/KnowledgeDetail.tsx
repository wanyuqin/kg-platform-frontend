import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import dayjs, { Dayjs } from 'dayjs'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  api,
  daysUntil,
  domainDisplayLabel,
  DomainItem,
  KNOWLEDGE_TYPES,
  KnowledgeDetailOut,
  STATUS_LABEL,
  TYPE_SECTIONS,
} from '../api/client'

// ① 知识详情页（线稿 7.2-①）：面包屑、正文分段预览、右侧元数据/溯源/版本历史三卡、
// 底部治理信息条；操作：编辑元数据 / 续期 / 下架（+ manual 来源可编辑重提）
export default function KnowledgeDetail() {
  const { kid } = useParams<{ kid: string }>()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<KnowledgeDetailOut | null>(null)
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [renewOpen, setRenewOpen] = useState(false)
  const [renewDate, setRenewDate] = useState<Dayjs | null>(null)
  const [metaOpen, setMetaOpen] = useState(false)
  const [metaForm] = Form.useForm()

  const load = useCallback(async () => {
    const resp = await api.get(`/api/knowledge/${kid}`)
    setDetail(resp.data)
  }, [kid])

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => setDomains(resp.data.items))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  if (!detail) return <Card loading title="知识详情" />

  const typeLabel = KNOWLEDGE_TYPES.find((t) => t.value === detail.type)?.label ?? detail.type
  const sections = TYPE_SECTIONS[detail.type] ?? []
  const left = daysUntil(detail.expire_date)

  const archive = async () => {
    await api.post(`/api/knowledge/${kid}/archive`)
    message.success('已下架（OpenViking 文件已删除）')
    load()
  }

  const renew = async () => {
    if (!renewDate) return
    await api.post(`/api/knowledge/${kid}/renew`, {
      expire_date: renewDate.format('YYYY-MM-DD'),
    })
    message.success('已续期')
    setRenewOpen(false)
    load()
  }

  const saveMeta = async () => {
    const values = await metaForm.validateFields()
    await api.patch(`/api/knowledge/${kid}/meta`, {
      tags: values.tags,
      owner: values.owner,
      expire_date: values.expire_date?.format('YYYY-MM-DD'),
    })
    message.success('元数据已更新')
    setMetaOpen(false)
    load()
  }

  return (
    <div>
      <Breadcrumb
        style={{ marginBottom: 12 }}
        items={[
          { title: <Link to="/knowledge">知识管理</Link> },
          { title: domainDisplayLabel(domains, detail.domain) },
          { title: detail.kid },
        ]}
      />
      <Space align="center" style={{ marginBottom: 8, justifyContent: 'space-between', width: '100%' }}>
        <Space align="center">
          <Typography.Title level={3} style={{ margin: 0 }}>
            {detail.title}
          </Typography.Title>
          <Tag color={detail.status === 'published' ? 'green' : 'orange'}>
            {STATUS_LABEL[detail.status] ?? detail.status}
          </Tag>
          <Tag>v{detail.version}</Tag>
        </Space>
        <Space>
          <Button
            onClick={() => {
              metaForm.setFieldsValue({
                tags: detail.tags,
                owner: detail.owner,
                expire_date: dayjs(detail.expire_date),
              })
              setMetaOpen(true)
            }}
          >
            编辑元数据
          </Button>
          {detail.source_type === 'manual' && (
            <Button onClick={() => navigate(`/knowledge/new?edit=${detail.kid}`)}>编辑重提</Button>
          )}
          <Button onClick={() => setRenewOpen(true)}>续期</Button>
          <Popconfirm title="下架为终态不可恢复，确认？" onConfirm={archive}>
            <Button danger disabled={detail.status === 'archived'}>
              下架
            </Button>
          </Popconfirm>
        </Space>
      </Space>

      {detail.source_type.startsWith('feishu') && (
        <Alert
          style={{ marginBottom: 16 }}
          type="info"
          showIcon
          message="飞书来源 · 正文只读 —— 修改内容请编辑飞书原文（设计 3.1）"
          action={
            detail.source_url && (
              <Button size="small" type="primary" href={detail.source_url} target="_blank">
                去飞书编辑 ↗
              </Button>
            )
          }
        />
      )}

      <Row gutter={16}>
        <Col span={16}>
          <Card title={`正文预览 · L2 模板化内容（${typeLabel}模板）`}>
            {sections
              .filter((s) => detail.fields[s.name])
              .map((s) => (
                <div key={s.name} style={{ marginBottom: 16 }}>
                  <Typography.Text type="secondary">{s.name}</Typography.Text>
                  <div
                    style={{
                      background: '#fafafa',
                      border: '1px solid #f0f0f0',
                      borderRadius: 6,
                      padding: '8px 12px',
                      marginTop: 4,
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {detail.fields[s.name]}
                  </div>
                </div>
              ))}
            {Object.keys(detail.fields).length === 0 && (
              <pre style={{ whiteSpace: 'pre-wrap' }}>{detail.content}</pre>
            )}
          </Card>
        </Col>
        <Col span={8}>
          <Card title="元数据" size="small" style={{ marginBottom: 16 }}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="kid">{detail.kid}</Descriptions.Item>
              <Descriptions.Item label="知识域">
                {domainDisplayLabel(domains, detail.domain)}
              </Descriptions.Item>
              <Descriptions.Item label="type">{detail.type}</Descriptions.Item>
              <Descriptions.Item label="tags">
                {detail.tags.length ? detail.tags.map((t) => <Tag key={t}>{t}</Tag>) : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="owner">{detail.owner}</Descriptions.Item>
              <Descriptions.Item label="version">{detail.version}</Descriptions.Item>
              <Descriptions.Item label="expire_date">
                <Space size={4}>
                  {detail.expire_date}
                  {left >= 0 && left <= 90 && <Tag color="gold">剩 {left} 天</Tag>}
                </Space>
              </Descriptions.Item>
            </Descriptions>
          </Card>
          <Card title="溯源" size="small" style={{ marginBottom: 16 }}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="所属文件">
                <a onClick={() => navigate(`/source-docs/${detail.source_doc.id}`)}>
                  {detail.source_doc.title ?? detail.source_doc.name ?? `#${detail.source_doc.id}`}
                </a>
              </Descriptions.Item>
              <Descriptions.Item label="来源">{detail.source ?? detail.source_doc.source ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="原文标题">{detail.source_title ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="原文链接">
                {detail.source_url ? (
                  <a href={detail.source_url} target="_blank" rel="noreferrer">
                    打开 ↗
                  </a>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
              <Descriptions.Item label="source_ref">
                <Typography.Text ellipsis style={{ maxWidth: 180 }}>
                  {detail.source_ref}
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>
          </Card>
          <Card title="版本历史" size="small">
            <Timeline
              items={detail.versions.map((v) => ({
                color: v.version === detail.version ? 'blue' : 'gray',
                children: (
                  <>
                    <Typography.Text strong>
                      v{v.version} · {v.created_at.slice(0, 10)}
                    </Typography.Text>
                    <br />
                    <Typography.Text type="secondary">
                      {v.version === 1 ? '首次发布' : '内容更新'} · {v.created_by}
                    </Typography.Text>
                  </>
                ),
              }))}
            />
          </Card>
        </Col>
      </Row>

      <Card size="small" style={{ marginTop: 16, background: '#fafafa' }}>
        治理信息：近 30 天命中 {detail.hits_30d} 次 ｜ 最近复审 —（P3 过期复审上线后展示）
      </Card>

      <Modal
        title="续期（更新过期日期）"
        open={renewOpen}
        onOk={renew}
        onCancel={() => setRenewOpen(false)}
        okButtonProps={{ disabled: !renewDate }}
      >
        <DatePicker style={{ width: '100%' }} value={renewDate} onChange={setRenewDate} minDate={dayjs()} />
      </Modal>

      <Modal title="编辑元数据" open={metaOpen} onOk={saveMeta} onCancel={() => setMetaOpen(false)}>
        <Form form={metaForm} layout="vertical">
          <Form.Item name="tags" label="tags（自由输入）">
            <Select mode="tags" open={false} suffixIcon={null} />
          </Form.Item>
          <Form.Item name="owner" label="owner">
            <Input />
          </Form.Item>
          <Form.Item name="expire_date" label="过期日期">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
