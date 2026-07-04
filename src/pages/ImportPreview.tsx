import { InboxOutlined } from '@ant-design/icons'
import { Card, Typography, Upload } from 'antd'

// ⑦ 拆分预览确认页（P1）：Markdown 上传与飞书首次导入共用；逐条确认后入库
export default function ImportPreview() {
  return (
    <Card title="Markdown 上传">
      <Upload.Dragger disabled accept=".md" maxCount={1}>
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">上传 .md 文件（UTF-8，≤2MB）</p>
        <p className="ant-upload-hint">
          待接入 POST /api/imports：先选类型 → 前置校验 → 拆分预览 → 勾选确认入库
        </p>
      </Upload.Dragger>
      <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
        校验不通过将当场拒收并提供该类型标准模板下载（GET /api/templates/{'{type}'}.md）。
      </Typography.Paragraph>
    </Card>
  )
}
