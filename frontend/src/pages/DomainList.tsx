import { Button, Card, Empty, Typography } from 'antd'

// ⑥ domain 列表页 + ② domain 配置页（P1）：注册 domain、Key 管理、类型级 top_k
export default function DomainList() {
  return (
    <Card title="domain 管理" extra={<Button type="primary" disabled>注册 domain</Button>}>
      <Empty description={
        <Typography.Text type="secondary">
          待接入 GET /api/domains：全域概览、知识量分布、API Key 管理、类型级 top_k 配置
        </Typography.Text>
      } />
    </Card>
  )
}
