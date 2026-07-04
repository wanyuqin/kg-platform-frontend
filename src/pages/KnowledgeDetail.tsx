import { Card, Descriptions, Typography } from 'antd'
import { useParams } from 'react-router-dom'

// ① 知识详情页（P1）：只读提示、三卡信息栏、版本快照
export default function KnowledgeDetail() {
  const { kid } = useParams()
  return (
    <Card title={`知识详情：${kid}`}>
      <Descriptions column={2} bordered size="small">
        <Descriptions.Item label="kid">{kid}</Descriptions.Item>
        <Descriptions.Item label="状态">—</Descriptions.Item>
        <Descriptions.Item label="domain">—</Descriptions.Item>
        <Descriptions.Item label="owner">—</Descriptions.Item>
      </Descriptions>
      <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
        待接入 GET /api/knowledge/{'{kid}'}：元数据 + 当前正文 + 版本快照列表（溯源查看）。
      </Typography.Paragraph>
    </Card>
  )
}
