import { ReactNode } from 'react'
import { Typography } from 'antd'

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
  return parts.map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <Typography.Text code key={index}>
          {part.slice(1, -1)}
        </Typography.Text>
      )
    }
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <Typography.Text strong key={index}>
          {part.slice(2, -2)}
        </Typography.Text>
      )
    }
    return part
  })
}

export default function MarkdownPreview({ value }: { value: string }) {
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
      blocks.push(
        <div key={blocks.length} style={{ borderTop: '1px solid #f0f0f0', margin: '16px 0' }} />,
      )
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
            <Typography.Paragraph
              key={index}
              style={{ marginBottom: index === quote.length - 1 ? 0 : 6 }}
            >
              {renderInline(item)}
            </Typography.Paragraph>
          ))}
        </div>,
      )
      continue
    }

    if (
      /^\|.+\|$/.test(trimmed) &&
      i + 1 < lines.length &&
      /^\|?[\s:-]+\|[\s|:-]+$/.test(lines[i + 1].trim())
    ) {
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
                  <th
                    key={index}
                    style={{
                      background: '#fafafa',
                      border: '1px solid #f0f0f0',
                      padding: '8px 10px',
                      textAlign: 'left',
                    }}
                  >
                    {renderInline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      style={{ border: '1px solid #f0f0f0', padding: '8px 10px' }}
                    >
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
          {items.map((item, index) => (
            <li key={index}>{renderInline(item)}</li>
          ))}
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
          {items.map((item, index) => (
            <li key={index}>{renderInline(item)}</li>
          ))}
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
    return <Typography.Text type="secondary">暂无内容，请在左侧编辑 Markdown</Typography.Text>
  }

  return (
    <div
      style={{
        background: '#fff',
        borderRadius: 6,
        padding: 20,
      }}
    >
      {blocks}
    </div>
  )
}
