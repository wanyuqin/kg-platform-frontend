import { useState } from 'react'
import { Input, Segmented, Typography } from 'antd'

import MarkdownPreview from './MarkdownPreview'
import './MarkdownEditor.css'

const PANEL_HEIGHT = 480
const HEADER_HEIGHT = 40
const BODY_HEIGHT = PANEL_HEIGHT - HEADER_HEIGHT

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace'

type ViewMode = 'split' | 'edit' | 'preview'

interface MarkdownEditorProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
}

function PanelHeader({ title }: { title: string }) {
  return (
    <div
      style={{
        height: HEADER_HEIGHT,
        padding: '0 12px',
        display: 'flex',
        alignItems: 'center',
        borderBottom: '1px solid #f0f0f0',
        background: '#fafafa',
        flexShrink: 0,
      }}
    >
      <Typography.Text type="secondary">{title}</Typography.Text>
    </div>
  )
}

function EditorPanel({
  value,
  onChange,
  placeholder,
  disabled,
}: Pick<MarkdownEditorProps, 'value' | 'onChange' | 'placeholder' | 'disabled'>) {
  return (
    <div
      style={{
        height: PANEL_HEIGHT,
        border: '1px solid #d9d9d9',
        borderRadius: 6,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <PanelHeader title="Markdown 源码" />
      <Input.TextArea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        classNames={{ textarea: 'markdown-editor-textarea' }}
        styles={{
          textarea: {
            height: BODY_HEIGHT,
            resize: 'none',
            border: 'none',
            borderRadius: 0,
            boxShadow: 'none',
            padding: 12,
            fontFamily: MONO,
            fontSize: 13,
            lineHeight: 1.6,
            background: '#1e1e1e',
            color: '#d4d4d4',
          },
        }}
      />
    </div>
  )
}

function PreviewPanel({ value }: { value: string }) {
  return (
    <div
      style={{
        height: PANEL_HEIGHT,
        border: '1px solid #d9d9d9',
        borderRadius: 6,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <PanelHeader title="实时预览" />
      <div style={{ flex: 1, overflow: 'auto', padding: 12, background: '#fff' }}>
        <MarkdownPreview value={value} />
      </div>
    </div>
  )
}

/** 分屏 Markdown 编辑器：源码编辑 + 实时预览，可切换仅编辑 / 仅预览 */
export default function MarkdownEditor({
  value,
  onChange,
  placeholder,
  disabled,
}: MarkdownEditorProps) {
  const [view, setView] = useState<ViewMode>('split')

  return (
    <div style={{ width: '100%' }}>
      <Segmented
        options={[
          { label: '分屏', value: 'split' },
          { label: '编辑', value: 'edit' },
          { label: '预览', value: 'preview' },
        ]}
        value={view}
        onChange={(v) => setView(v as ViewMode)}
        style={{ marginBottom: 12 }}
      />

      {view === 'split' && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
            gap: 16,
          }}
        >
          <EditorPanel
            value={value}
            onChange={onChange}
            placeholder={placeholder}
            disabled={disabled}
          />
          <PreviewPanel value={value} />
        </div>
      )}

      {view === 'edit' && (
        <EditorPanel
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          disabled={disabled}
        />
      )}

      {view === 'preview' && <PreviewPanel value={value} />}
    </div>
  )
}
