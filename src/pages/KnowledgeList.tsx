import { Button, Card, Empty, Space, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'

// ⑤ 知识列表页（控制台主页，P1）：type 徽章、状态筛选（含草稿视图）、三合一新建入口
export default function KnowledgeList() {
  const navigate = useNavigate()
  return (
    <Card
      title="知识列表"
      extra={
        <Space>
          <Button type="primary" onClick={() => navigate('/knowledge/new')}>
            表单创建
          </Button>
          <Button onClick={() => navigate('/knowledge/import')}>Markdown 上传</Button>
          <Button disabled>飞书文档注册（P2）</Button>
        </Space>
      }
    >
      <Empty description={
        <Typography.Text type="secondary">
          待接入 GET /api/knowledge（domain / type / status / tag 筛选 + 分页）
        </Typography.Text>
      } />
    </Card>
  )
}
