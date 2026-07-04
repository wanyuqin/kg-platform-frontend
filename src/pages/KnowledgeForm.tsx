import { Card, Form, Input, Select, Typography } from 'antd'
import { useState } from 'react'

import { KNOWLEDGE_TYPES } from '../api/client'

// ③ 表单录入页（P1）：模板动态字段、实时校验；tags 自由输入（2026-07-04 决策）
export default function KnowledgeForm() {
  const [type, setType] = useState<string>()
  return (
    <Card title="表单创建知识">
      <Form layout="vertical" style={{ maxWidth: 640 }}>
        <Form.Item label="domain" required>
          <Select placeholder="仅列出有权限的 domain（待接入 GET /api/domains）" />
        </Form.Item>
        <Form.Item label="知识类型" required>
          <Select
            options={[...KNOWLEDGE_TYPES]}
            placeholder="选择后按该类型模板动态渲染字段"
            onChange={setType}
          />
        </Form.Item>
        <Form.Item label="tags">
          <Select mode="tags" placeholder="自由输入，可为空" open={false} />
        </Form.Item>
        {type && (
          <Typography.Text type="secondary">
            待实现：按附录 A 段名映射渲染「{type}」模板字段，提交走 POST /api/knowledge
          </Typography.Text>
        )}
        <Form.Item label="正文占位">
          <Input.TextArea rows={4} disabled placeholder="模板字段动态渲染区" />
        </Form.Item>
      </Form>
    </Card>
  )
}
