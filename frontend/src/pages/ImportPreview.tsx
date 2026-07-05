import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircleFilled,
  DownOutlined,
  InboxOutlined,
  RightOutlined,
  StopFilled,
  WarningFilled,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  Modal,
  Pagination,
  Segmented,
  Select,
  Space,
  Steps,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'

import MarkdownEditor from '../components/MarkdownEditor'
import {
  ALIGN_COLOR,
  ALIGN_LABEL,
  api,
  domainDisplayLabel,
  domainSelectOption,
  DomainItem,
  fetchTemplate,
  downloadTemplate,
  ImportBatchOut,
  ImportConfirmOut,
  ImportConfirmResult,
  ImportItemOut,
  KNOWLEDGE_TYPES,
  TYPE_COLOR,
  ValidationFinding,
} from '../api/client'

type PreviewFilter = 'all' | 'duplicate' | 'duplicate_keep' | 'invalid' | 'valid'

const defaultSelected = (items: ImportItemOut[]) =>
  items
    .filter((i) => i.is_valid && i.align_action !== 'unchanged')
    .filter((i) => !(i.align_action === 'disappeared' && i.is_form))
    .map((i) => i.id)

function applyPreviewFilter(items: ImportItemOut[], filter: PreviewFilter): ImportItemOut[] {
  switch (filter) {
    case 'duplicate':
      return items.filter((i) => i.validation.some((v) => v.rule === 'duplicate_in_batch'))
    case 'duplicate_keep': {
      const keepIds = new Set<number>()
      for (const i of items) {
        const hit = i.validation.find((v) => v.rule === 'duplicate_in_batch')
        const id = hit?.meta?.duplicate_item_id as number | undefined
        if (id != null) keepIds.add(id)
      }
      return items.filter((i) => keepIds.has(i.id))
    }
    case 'invalid':
      return items.filter((i) => !i.is_valid)
    case 'valid':
      return items.filter((i) => i.is_valid && i.align_action !== 'unchanged')
    default:
      return items
  }
}

function slicePage<T>(items: T[], page: number, pageSize: number): T[] {
  const start = (page - 1) * pageSize
  return items.slice(start, start + pageSize)
}

function duplicateFinding(item: ImportItemOut): ValidationFinding | undefined {
  return item.validation.find((v) => v.rule === 'duplicate_in_batch')
}

type ImportMode = 'create' | 'upload'

export default function ImportPreview() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const docId = params.get('docId')
  const batchId = params.get('batchId')
  const isUpdateMode = !!docId
  const mode: ImportMode = isUpdateMode
    ? 'create'
    : params.get('mode') === 'upload'
      ? 'upload'
      : 'create'

  const [form] = Form.useForm()
  const selectedType = Form.useWatch('type', form)
  const [domains, setDomains] = useState<DomainItem[]>([])
  const [batch, setBatch] = useState<ImportBatchOut | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [expanded, setExpanded] = useState<number[]>([])
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [confirmedSourceDocId, setConfirmedSourceDocId] = useState<number | null>(null)
  const [confirmOut, setConfirmOut] = useState<ImportConfirmOut | null>(null)
  const [markdownText, setMarkdownText] = useState('')
  const [docName, setDocName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [loadingTemplate, setLoadingTemplate] = useState(false)
  const [step, setStep] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [resultPage, setResultPage] = useState(1)
  const [resultPageSize, setResultPageSize] = useState(20)
  const [filter, setFilter] = useState<PreviewFilter>('all')
  const [highlightId, setHighlightId] = useState<number | null>(null)

  const baselineTemplateRef = useRef('')
  const loadedTemplateTypeRef = useRef<string | null>(null)
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const resetPreviewView = () => {
    setPage(1)
    setFilter('all')
    setHighlightId(null)
  }

  const isEditorDirty = useCallback(
    () => markdownText.trim() !== '' && markdownText !== baselineTemplateRef.current,
    [markdownText],
  )

  const applyTemplate = useCallback((type: string, content: string) => {
    setMarkdownText(content)
    baselineTemplateRef.current = content
    loadedTemplateTypeRef.current = type
  }, [])

  const loadTemplateForType = useCallback(
    async (type: string) => {
      setLoadingTemplate(true)
      try {
        const content = await fetchTemplate(type)
        applyTemplate(type, content)
      } finally {
        setLoadingTemplate(false)
      }
    },
    [applyTemplate],
  )

  const handleTypeChange = useCallback(
    async (newType: string, prevType: string | null) => {
      if (!newType || prevType === newType) return

      const doLoad = () => {
        void loadTemplateForType(newType)
      }

      if (prevType && isEditorDirty()) {
        Modal.confirm({
          title: '切换类型',
          content: '切换类型将替换当前内容为新标准模板，是否继续？',
          onOk: doLoad,
          onCancel: () => form.setFieldValue('type', prevType),
        })
        return
      }

      doLoad()
    },
    [form, isEditorDirty, loadTemplateForType],
  )

  useEffect(() => {
    if (isUpdateMode || mode !== 'create' || batch || !selectedType) return
    const prevType = loadedTemplateTypeRef.current
    if (prevType === selectedType) return
    void handleTypeChange(selectedType, prevType)
  }, [selectedType, isUpdateMode, mode, batch, handleTypeChange])

  useEffect(() => {
    api.get('/api/domains/mine').then((resp) => setDomains(resp.data.items))
  }, [])

  useEffect(() => {
    const domain = params.get('domain')
    if (domain) form.setFieldValue('domain', domain)
  }, [params, form])

  useEffect(() => {
    if (!batchId) return
    api.get<ImportBatchOut>(`/api/imports/${batchId}`).then((resp) => {
      setBatch(resp.data)
      setSelected(defaultSelected(resp.data.items))
      setConfirmOut(null)
      resetPreviewView()
      setStep(1)
    })
  }, [batchId])

  useEffect(
    () => () => {
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current)
    },
    [],
  )

  const submit = async (payload: { file?: File; text?: string }) => {
    setSubmitting(true)
    try {
      const data = new FormData()
      if (payload.file) data.append('file', payload.file)
      if (payload.text) data.append('text', payload.text)

      let resp
      if (isUpdateMode) {
        resp = await api.post<ImportBatchOut>(`/api/source-docs/${docId}/update`, data)
      } else {
        const { domain, type } = await form.validateFields()
        data.append('domain', domain)
        data.append('type', type)
        if (docName.trim()) data.append('doc_name', docName.trim())
        resp = await api.post<ImportBatchOut>('/api/imports', data)
      }
      setBatch(resp.data)
      setSelected(defaultSelected(resp.data.items))
      setConfirmOut(null)
      setConfirmed(false)
      setConfirmedSourceDocId(null)
      resetPreviewView()
      setStep(1)
    } finally {
      setSubmitting(false)
    }
  }

  const uploadFile = async (file: File) => {
    await submit({ file })
    return false
  }

  const submitMarkdown = async () => {
    if (!markdownText.trim()) {
      message.warning('请先填写 Markdown 内容')
      return
    }
    if (!isUpdateMode && !docName.trim()) {
      message.warning('请先填写文件名')
      return
    }
    await submit({ text: markdownText })
  }

  const confirm = async () => {
    if (!batch || confirming || confirmed) return
    setConfirming(true)
    try {
      const resp = await api.post<ImportConfirmOut>(`/api/imports/${batch.id}/confirm`, {
        item_ids: selected,
      })
      setConfirmOut(resp.data)
      const ok = resp.data.summary.succeeded
      if (resp.data.requires_review) {
        message.success(`${ok} 条已提交待审核`)
      } else {
        message.success(`已入库 ${ok} 条`)
      }
      setConfirmed(true)
      setConfirmedSourceDocId(resp.data.source_doc_id ?? null)
      setResultPage(1)
      setStep(2)
    } finally {
      setConfirming(false)
    }
  }

  const toggle = (id: number, checked: boolean) =>
    setSelected((prev) => (checked ? [...prev, id] : prev.filter((x) => x !== id)))

  const selectableIds = useMemo(
    () =>
      batch?.items.filter((i) => i.is_valid && i.align_action !== 'unchanged').map((i) => i.id) ??
      [],
    [batch],
  )

  const filteredItems = useMemo(
    () => (batch ? applyPreviewFilter(batch.items, filter) : []),
    [batch, filter],
  )

  const pageItems = useMemo(
    () => slicePage(filteredItems, page, pageSize),
    [filteredItems, page, pageSize],
  )

  const confirmResults = confirmOut?.results ?? []
  const resultPageItems = useMemo(
    () => slicePage(confirmResults, resultPage, resultPageSize),
    [confirmResults, resultPage, resultPageSize],
  )

  const jumpToKeepItem = (finding: ValidationFinding) => {
    if (!batch) return
    const keepId = finding.meta?.duplicate_item_id as number | undefined
    const keepSeq = finding.meta?.duplicate_seq as number | undefined
    const target =
      keepId != null
        ? batch.items.find((i) => i.id === keepId)
        : keepSeq != null
          ? batch.items.find((i) => i.seq === keepSeq)
          : undefined
    if (!target) return
    setFilter('all')
    const index = batch.items.findIndex((i) => i.id === target.id)
    if (index >= 0) {
      setPage(Math.floor(index / pageSize) + 1)
    }
    setHighlightId(target.id)
    setExpanded((prev) => (prev.includes(target.id) ? prev : [...prev, target.id]))
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current)
    highlightTimerRef.current = setTimeout(() => setHighlightId(null), 2000)
  }

  const statusOf = (item: ImportItemOut) => {
    const dup = duplicateFinding(item)
    if (dup) {
      const seq = dup.meta?.duplicate_seq as number | undefined
      return (
        <Typography.Text type="danger">
          <StopFilled />{' '}
          {seq != null ? (
            <>
              与本文件第{' '}
              <Button type="link" size="small" style={{ padding: 0 }} onClick={() => jumpToKeepItem(dup)}>
                {seq}
              </Button>{' '}
              条内容重复
            </>
          ) : (
            dup.message
          )}
        </Typography.Text>
      )
    }
    if (!item.is_valid) {
      const blocking = item.validation.find((v) => v.level === 'blocking')
      return (
        <Typography.Text type="danger">
          <StopFilled /> 校验不通过：{blocking?.message}
        </Typography.Text>
      )
    }
    const warning = item.validation.find((v) => v.level === 'warning')
    if (warning)
      return (
        <Typography.Text type="warning">
          <WarningFilled /> {warning.message}
        </Typography.Text>
      )
    return (
      <Typography.Text type="success">
        <CheckCircleFilled /> 校验通过
      </Typography.Text>
    )
  }

  const countByAction = (action: string) =>
    batch?.items.filter((i) => selected.includes(i.id) && i.align_action === action).length ?? 0

  const stats = batch?.stats
  const requiresReview = batch?.stats?.requires_review ?? false
  const dupCount = stats?.duplicate_in_batch ?? 0
  const hasInvalid = batch?.items.some((i) => !i.is_valid) ?? false

  const filterOptions = useMemo(() => {
    const opts: { label: string; value: PreviewFilter }[] = [
      { label: '全部', value: 'all' },
    ]
    if (dupCount > 0) {
      opts.push({ label: '重复项', value: 'duplicate' })
      opts.push({ label: '重复保留项', value: 'duplicate_keep' })
    }
    if (hasInvalid) opts.push({ label: '校验不通过', value: 'invalid' })
    opts.push({ label: '可入库', value: 'valid' })
    return opts
  }, [dupCount, hasInvalid])

  const renderItemCard = (item: ImportItemOut, result?: ImportConfirmResult) => {
    const isOpen = expanded.includes(item.id)
    const isUnchanged = item.align_action === 'unchanged'
    const highlighted = highlightId === item.id
    return (
      <Card
        key={item.id}
        size="small"
        style={{
          background: item.is_valid ? undefined : '#fffbe6',
          outline: highlighted ? '2px solid #faad14' : undefined,
        }}
      >
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            {step < 2 && (
              <Checkbox
                checked={selected.includes(item.id)}
                disabled={!item.is_valid || isUnchanged}
                onChange={(e) => toggle(item.id, e.target.checked)}
              />
            )}
            <Tag color={TYPE_COLOR[batch!.type]}>{batch!.type}</Tag>
            <Tag>#{item.seq}</Tag>
            {item.align_action && (
              <Tag color={ALIGN_COLOR[item.align_action]}>{ALIGN_LABEL[item.align_action]}</Tag>
            )}
            <Typography.Text strong>{item.title ?? '（无标题）'}</Typography.Text>
            {step < 2 ? statusOf(item) : null}
            {!item.is_valid && step < 2 && (
              <Typography.Text type="secondary">已自动取消勾选</Typography.Text>
            )}
            {item.align_action === 'disappeared' && item.is_form && (
              <Typography.Text type="secondary">表单添加，未在新文本中</Typography.Text>
            )}
          </Space>
          <Space>
            {result &&
              (result.kid ? (
                <Space>
                  {result.status === 'pending_review' && <Tag color="orange">待审核</Tag>}
                  {result.status === 'published' && <Tag color="green">已发布</Tag>}
                  <a onClick={() => navigate(`/knowledge/${result.kid}`)}>{result.kid}</a>
                </Space>
              ) : (
                <Typography.Text type="danger">{result.error}</Typography.Text>
              ))}
            <Button
              type="text"
              size="small"
              icon={isOpen ? <DownOutlined /> : <RightOutlined />}
              onClick={() =>
                setExpanded((prev) =>
                  isOpen ? prev.filter((x) => x !== item.id) : [...prev, item.id],
                )
              }
            />
          </Space>
        </Space>
        {isOpen && (
          <div
            style={{
              marginTop: 12,
              marginLeft: 24,
              background: '#fafafa',
              borderRadius: 6,
              padding: 12,
            }}
          >
            {Object.entries(item.fields).map(([name, value]) => (
              <div key={name} style={{ marginBottom: 4 }}>
                <Typography.Text type="secondary">{name}：</Typography.Text>
                <Typography.Text>{value.slice(0, 120)}</Typography.Text>
              </div>
            ))}
          </div>
        )}
      </Card>
    )
  }

  const pageTitle = isUpdateMode
    ? '更新知识文件'
    : mode === 'upload'
      ? '上传知识文件'
      : '新建知识文件'

  const stepOneTitle =
    isUpdateMode || mode === 'create' ? '编辑内容 / 解析' : '上传 / 解析'

  const showMarkdownEditor = isUpdateMode || mode === 'create'
  const showUploadDragger = !isUpdateMode && mode === 'upload'

  return (
    <Card title={pageTitle}>
      <Steps
        current={step}
        style={{ maxWidth: 640, marginBottom: 24 }}
        items={[{ title: stepOneTitle }, { title: '预览确认' }, { title: '入库' }]}
      />

      {!isUpdateMode && (
        <Form form={form} layout="inline" style={{ marginBottom: 16 }}>
          <Form.Item name="domain" label="知识域" rules={[{ required: true }]}>
            <Select
              style={{ width: 280 }}
              placeholder="选择知识域"
              options={domains.filter((d) => d.code !== 'common').map(domainSelectOption)}
            />
          </Form.Item>
          <Form.Item name="type" label="类型" rules={[{ required: true }]}>
            <Select options={[...KNOWLEDGE_TYPES]} style={{ width: 180 }} placeholder="先选类型" />
          </Form.Item>
        </Form>
      )}

      {!batch && (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          {!isUpdateMode && (
            <Input
              placeholder={mode === 'upload' ? '文件名（可选，默认取上传文件名）' : '文件名（必填）'}
              value={docName}
              onChange={(e) => setDocName(e.target.value)}
              style={{ maxWidth: 400 }}
            />
          )}

          {showMarkdownEditor && (
            <>
              <MarkdownEditor
                value={markdownText}
                onChange={setMarkdownText}
                placeholder="请先选择知识类型，将自动加载标准模板；一级标题为条目边界"
                disabled={loadingTemplate}
              />
              <Space style={{ marginTop: 12 }}>
                <Button type="primary" loading={submitting} onClick={submitMarkdown}>
                  解析预览
                </Button>
                {!isUpdateMode && selectedType && (
                  <Button type="link" onClick={() => void downloadTemplate(selectedType)}>
                    下载模板
                  </Button>
                )}
              </Space>
            </>
          )}

          {showUploadDragger && (
            <Upload.Dragger
              accept=".md"
              maxCount={1}
              beforeUpload={uploadFile}
              showUploadList={false}
              disabled={submitting}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">上传 .md 文件（UTF-8，≤2MB，一级标题为条目边界）</p>
              <p className="ant-upload-hint">
                前置校验不通过将当场拒收并提供标准模板下载（设计 3.1）
              </p>
            </Upload.Dragger>
          )}
        </Space>
      )}

      {batch && step < 2 && (
        <>
          <Alert
            style={{ marginBottom: 16 }}
            type="info"
            message={
              <Space split="｜" wrap>
                <span>来源：{batch.file_name}</span>
                <span>知识域：{domainDisplayLabel(domains, batch.domain)}</span>
                <span>
                  拆出 {stats?.total ?? batch.items.length} 条 · 可入库 {stats?.valid ?? 0} 条
                  {dupCount > 0 && (
                    <>
                      {' '}
                      ·{' '}
                      <Button
                        type="link"
                        size="small"
                        style={{ padding: 0 }}
                        onClick={() => {
                          setFilter('duplicate')
                          setPage(1)
                        }}
                      >
                        文件内重复 {dupCount} 条
                      </Button>
                    </>
                  )}
                </span>
              </Space>
            }
            action={
              <Button size="small" onClick={() => void downloadTemplate(batch.type)}>
                下载模板
              </Button>
            }
          />

          {dupCount > 0 && (
            <Alert
              style={{ marginBottom: 16 }}
              type="warning"
              showIcon
              message={
                <Space>
                  <span>
                    检测到 <strong>{dupCount}</strong> 条文件内重复，请核对并修正源文件。确认入库后，本批成功条目将进入{' '}
                    <strong>待审核</strong>，暂不写入检索索引。
                  </span>
                  <Button
                    size="small"
                    onClick={() => {
                      setFilter('duplicate')
                      setPage(1)
                    }}
                  >
                    查看重复项
                  </Button>
                </Space>
              }
            />
          )}

          <Segmented
            style={{ marginBottom: 12 }}
            value={filter}
            options={filterOptions}
            onChange={(v) => {
              setFilter(v as PreviewFilter)
              setPage(1)
            }}
          />

          <Space style={{ marginBottom: 12 }}>
            <Checkbox
              checked={selected.length === selectableIds.length && selectableIds.length > 0}
              indeterminate={selected.length > 0 && selected.length < selectableIds.length}
              onChange={(e) => setSelected(e.target.checked ? selectableIds : [])}
            >
              全选（已选 {selected.length}/{selectableIds.length} 可勾选）
            </Checkbox>
            <Typography.Text type="secondary">
              文件内重复在此处理；与平台已有知识是否重复，确认入库后校验。"未变"条目无需重新入库
            </Typography.Text>
          </Space>

          {pageItems.length === 0 ? (
            <Empty description="当前筛选下无条目" />
          ) : (
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              {pageItems.map((item) => renderItemCard(item))}
            </Space>
          )}

          {filteredItems.length > pageSize && (
            <Pagination
              style={{ marginTop: 16, textAlign: 'right' }}
              current={page}
              pageSize={pageSize}
              total={filteredItems.length}
              showSizeChanger
              pageSizeOptions={['10', '20', '50']}
              showTotal={(total) => `共 ${total} 条`}
              onChange={(p, ps) => {
                setPage(p)
                setPageSize(ps)
              }}
            />
          )}

          <Card size="small" style={{ marginTop: 16, background: '#fafafa' }}>
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              {isUpdateMode && (
                <Typography.Text>
                  预计：新增 {countByAction('new')} 条、更新 {countByAction('changed')} 条、下架{' '}
                  {countByAction('disappeared')} 条
                </Typography.Text>
              )}
              <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                <Typography.Text>
                  确认入库 {selected.length} 条
                  {requiresReview
                    ? ' → 待审核，不写检索索引'
                    : ' → 低风险自动发布（绿色通道）'}
                </Typography.Text>
                <Space>
                  <Button onClick={() => navigate('/knowledge')}>取消</Button>
                  <Button
                    type="primary"
                    disabled={selected.length === 0}
                    loading={confirming}
                    onClick={confirm}
                  >
                    {requiresReview ? '确认提交审核' : '确认入库'}
                  </Button>
                </Space>
              </Space>
            </Space>
          </Card>
        </>
      )}

      {batch && step >= 2 && confirmOut && (
        <>
          <Alert
            style={{ marginBottom: 16 }}
            type={confirmOut.requires_review ? 'warning' : 'success'}
            message={
              <Space direction="vertical" size={4}>
                <span>
                  成功 {confirmOut.summary.succeeded} 条
                  {confirmOut.summary.pending_review > 0 &&
                    `（待审核 ${confirmOut.summary.pending_review} 条）`}
                  ，库内重复 {confirmOut.summary.failed_duplicate} 条，校验未通过{' '}
                  {confirmOut.summary.failed_blocking} 条，其他失败 {confirmOut.summary.failed_other}{' '}
                  条
                </span>
                {confirmOut.requires_review && (
                  <Typography.Text type="secondary">
                    {confirmOut.summary.pending_review} 条已进入待审核，暂未写入检索索引，审核通过后可检索。
                  </Typography.Text>
                )}
              </Space>
            }
          />

          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            {resultPageItems.map((result) => {
              const item = batch.items.find((i) => i.id === result.item_id)
              return item ? renderItemCard(item, result) : null
            })}
          </Space>

          {confirmResults.length > resultPageSize && (
            <Pagination
              style={{ marginTop: 16, textAlign: 'right' }}
              current={resultPage}
              pageSize={resultPageSize}
              total={confirmResults.length}
              showSizeChanger
              pageSizeOptions={['10', '20', '50']}
              showTotal={(total) => `共 ${total} 条`}
              onChange={(p, ps) => {
                setResultPage(p)
                setResultPageSize(ps)
              }}
            />
          )}

          <Card size="small" style={{ marginTop: 16, background: '#fafafa' }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => navigate('/knowledge')}>返回列表</Button>
              <Button
                type="primary"
                onClick={() =>
                  navigate(
                    confirmedSourceDocId ? `/source-docs/${confirmedSourceDocId}` : '/source-docs',
                  )
                }
              >
                查看知识文件
              </Button>
            </Space>
          </Card>
        </>
      )}
    </Card>
  )
}
