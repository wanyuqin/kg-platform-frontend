import { Card, Empty, Typography } from 'antd'

// 审计查询（P1 简版）：180 天日志检索与导出
export default function AuditLogs() {
  return (
    <Card title="审计查询">
      <Empty description={
        <Typography.Text type="secondary">
          待接入 GET /api/audit-logs（时间 / key / action 过滤）与 CSV 导出
        </Typography.Text>
      } />
    </Card>
  )
}
