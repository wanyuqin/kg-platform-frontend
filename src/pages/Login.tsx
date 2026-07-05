import { LockOutlined } from '@ant-design/icons'
import { Button, Card, Checkbox, Divider, Form, Input, Typography } from 'antd'
import { useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import {
  buildDevLoginUrl,
  getExternalLoginLabel,
  getLoginRedirectUrl,
  isDevLoginEnabled,
} from '../auth/config'

interface DevLoginForm {
  user_id: string
  name: string
  platform_admin: boolean
}

export default function Login() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { user, loading, refresh } = useAuth()
  const returnUrl = searchParams.get('returnUrl') || '/domains'
  const externalUrl = getLoginRedirectUrl()
  const showDevForm = isDevLoginEnabled()

  useEffect(() => {
    if (!loading && user) {
      navigate(returnUrl, { replace: true })
    }
  }, [loading, user, navigate, returnUrl])

  const onDevLogin = (values: DevLoginForm) => {
    sessionStorage.setItem('auth_return_url', returnUrl)
    window.location.href = buildDevLoginUrl({
      user_id: values.user_id,
      name: values.name || undefined,
      platform_admin: values.platform_admin,
      returnUrl,
    })
  }

  const onExternalLogin = () => {
    if (externalUrl) window.location.href = externalUrl
  }

  // dev-login 整页跳转回来后重新探测 session
  useEffect(() => {
    void refresh()
  }, [refresh])

  if (loading || user) {
    return null
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f5f5f5',
      }}
    >
      <Card style={{ width: 400 }} bordered={false}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <LockOutlined style={{ fontSize: 32, color: '#1677ff' }} />
          <Typography.Title level={4} style={{ marginTop: 12, marginBottom: 4 }}>
            知识管理平台
          </Typography.Title>
          <Typography.Text type="secondary">请登录后继续</Typography.Text>
        </div>

        {showDevForm && (
          <>
            <Form<DevLoginForm>
              layout="vertical"
              initialValues={{ user_id: 'dev', name: '开发者', platform_admin: true }}
              onFinish={onDevLogin}
            >
              <Form.Item
                label="用户 ID"
                name="user_id"
                rules={[{ required: true, message: '请输入用户 ID' }]}
              >
                <Input placeholder="dev" />
              </Form.Item>
              <Form.Item label="显示名" name="name">
                <Input placeholder="开发者" />
              </Form.Item>
              <Form.Item name="platform_admin" valuePropName="checked">
                <Checkbox>平台管理员</Checkbox>
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" block>
                  开发环境登录
                </Button>
              </Form.Item>
            </Form>
            {externalUrl && (
              <>
                <Divider plain>或</Divider>
                <Button block onClick={onExternalLogin}>
                  {getExternalLoginLabel()}
                </Button>
              </>
            )}
          </>
        )}

        {!showDevForm && externalUrl && (
          <Button type="primary" block size="large" onClick={onExternalLogin}>
            {getExternalLoginLabel()}
          </Button>
        )}
      </Card>
    </div>
  )
}
