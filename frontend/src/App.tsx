import {
  AppstoreOutlined,
  AuditOutlined,
  DatabaseOutlined,
} from '@ant-design/icons'
import { Layout, Menu, Typography } from 'antd'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import AuditLogs from './pages/AuditLogs'
import DomainConfig from './pages/DomainConfig'
import DomainList from './pages/DomainList'
import ImportPreview from './pages/ImportPreview'
import KnowledgeDetail from './pages/KnowledgeDetail'
import KnowledgeForm from './pages/KnowledgeForm'
import KnowledgeList from './pages/KnowledgeList'

const { Sider, Header, Content } = Layout

// P1 控制台页面（设计 7.2：⑤知识列表为主页，①详情 ②domain 配置 ③表单录入 ⑥domain 列表 ⑦拆分预览确认）
const MENU = [
  { key: '/knowledge', icon: <AppstoreOutlined />, label: '知识管理' },
  { key: '/domains', icon: <DatabaseOutlined />, label: 'domain 管理' },
  { key: '/audit-logs', icon: <AuditOutlined />, label: '审计查询' },
]

export default function App() {
  const navigate = useNavigate()
  const location = useLocation()
  const selected = MENU.find((m) => location.pathname.startsWith(m.key))?.key ?? '/knowledge'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="light" width={200}>
        <div style={{ padding: 16 }}>
          <Typography.Text strong>知识管理平台</Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={MENU}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', paddingInline: 24 }} />
        <Content style={{ margin: 24 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/knowledge" replace />} />
            <Route path="/knowledge" element={<KnowledgeList />} />
            <Route path="/knowledge/new" element={<KnowledgeForm />} />
            <Route path="/knowledge/import" element={<ImportPreview />} />
            <Route path="/knowledge/:kid" element={<KnowledgeDetail />} />
            <Route path="/domains" element={<DomainList />} />
            <Route path="/domains/:code" element={<DomainConfig />} />
            <Route path="/audit-logs" element={<AuditLogs />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}
