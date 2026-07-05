import {
  AuditOutlined,
  DatabaseOutlined,
  KeyOutlined,
  LogoutOutlined,
  FileTextOutlined,
  CheckSquareOutlined,
  TagsOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Button, Layout, Menu, Space, Tag, Typography } from 'antd'
import { useEffect, useMemo } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from './auth/AuthContext'
import AdminRoute from './auth/AdminRoute'
import ProtectedRoute from './auth/ProtectedRoute'
import AdminKeys from './pages/AdminKeys'
import AdminUsers from './pages/AdminUsers'
import AuditLogs from './pages/AuditLogs'
import DomainConfig from './pages/DomainConfig'
import DomainList from './pages/DomainList'
import ImportPreview from './pages/ImportPreview'
import KnowledgeDetail from './pages/KnowledgeDetail'
import KnowledgeForm from './pages/KnowledgeForm'
import KnowledgeList from './pages/KnowledgeList'
import Login from './pages/Login'
import SourceDocDetail from './pages/SourceDocDetail'
import SourceDocList from './pages/SourceDocList'
import FeishuBind from './pages/FeishuBind'
import ReviewQueue from './pages/ReviewQueue'
import GovernanceTagging from './pages/GovernanceTagging'

const { Sider, Header, Content } = Layout

// P1/P2 控制台页面：主流程为 domain → 知识文件 → 知识条目
const MEMBER_MENU = [
  { key: '/knowledge', icon: <FileTextOutlined />, label: '知识条目' },
  { key: '/source-docs', icon: <DatabaseOutlined />, label: '知识文件' },
  { key: '/review-tasks', icon: <CheckSquareOutlined />, label: '审核待办' },
]

const BASE_MENU = [
  { key: '/domains', icon: <DatabaseOutlined />, label: '知识域' },
  { key: '/review-tasks', icon: <CheckSquareOutlined />, label: '审核待办' },
  { key: '/audit-logs', icon: <AuditOutlined />, label: '审计查询' },
]

const ADMIN_MENU = {
  key: 'admin',
  icon: <TeamOutlined />,
  label: '平台管理',
  children: [
    { key: '/admin/users', label: '用户管理' },
    { key: '/admin/keys', icon: <KeyOutlined />, label: 'API Key' },
    { key: '/governance/tagging', icon: <TagsOutlined />, label: '打标' },
  ],
}

/** dev-login 整页跳转回来后，恢复登录前目标路径 */
function PostLoginRedirect() {
  const { user, loading } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (!loading && user) {
      const saved = sessionStorage.getItem('auth_return_url')
      if (saved) {
        sessionStorage.removeItem('auth_return_url')
        navigate(saved, { replace: true })
      }
    }
  }, [user, loading, navigate])

  return null
}

function ConsoleLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()
  const menu = useMemo(
    () => (user?.is_platform_admin ? [...BASE_MENU, ADMIN_MENU] : MEMBER_MENU),
    [user?.is_platform_admin],
  )
  const homePath = user?.is_platform_admin ? '/domains' : '/knowledge'
  const selected = useMemo(() => {
    if (location.pathname.startsWith('/admin/users')) return '/admin/users'
    if (location.pathname.startsWith('/admin/keys')) return '/admin/keys'
    if (location.pathname.startsWith('/governance/tagging')) return '/governance/tagging'
    if (location.pathname.startsWith('/review-tasks')) return '/review-tasks'
    if (location.pathname.startsWith('/source-docs')) return '/source-docs'
    if (location.pathname.startsWith('/knowledge')) return '/knowledge'
    return menu.find((m) => 'key' in m && location.pathname.startsWith(m.key as string))?.key ?? homePath
  }, [location.pathname, menu, homePath])
  const openKeys = location.pathname.startsWith('/admin') || location.pathname.startsWith('/governance')
    ? ['admin']
    : undefined

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <PostLoginRedirect />
      <Sider theme="light" width={200}>
        <div style={{ padding: 16 }}>
          <Typography.Text strong>知识管理平台</Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected as string]}
          defaultOpenKeys={openKeys}
          items={menu}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            paddingInline: 24,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
          }}
        >
          {user && (
            <Space>
              <Typography.Text>{user.name}</Typography.Text>
              {user.is_platform_admin && <Tag color="blue">平台管理员</Tag>}
              <Button type="text" icon={<LogoutOutlined />} onClick={() => void logout()}>
                退出登录
              </Button>
            </Space>
          )}
        </Header>
        <Content style={{ margin: 24 }}>
          <Routes>
            <Route path="/" element={<Navigate to={homePath} replace />} />
            <Route path="/knowledge" element={<KnowledgeList />} />
            <Route path="/knowledge/new" element={<KnowledgeForm />} />
            <Route path="/knowledge/import" element={<ImportPreview />} />
            <Route path="/source-docs" element={<SourceDocList />} />
            <Route path="/source-docs/feishu/new" element={<FeishuBind />} />
            <Route path="/source-docs/:id" element={<SourceDocDetail />} />
            <Route path="/review-tasks" element={<ReviewQueue />} />
            <Route path="/knowledge/:kid" element={<KnowledgeDetail />} />
            <Route path="/domains" element={<AdminRoute><DomainList /></AdminRoute>} />
            <Route path="/domains/:code" element={<AdminRoute><DomainConfig /></AdminRoute>} />
            <Route path="/audit-logs" element={<AdminRoute><AuditLogs /></AdminRoute>} />
            <Route path="/governance/tagging" element={<AdminRoute><GovernanceTagging /></AdminRoute>} />
            <Route path="/admin/users" element={<AdminRoute><AdminUsers /></AdminRoute>} />
            <Route path="/admin/keys" element={<AdminRoute><AdminKeys /></AdminRoute>} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <ConsoleLayout />
          </ProtectedRoute>
        }
      />
    </Routes>
  )
}
