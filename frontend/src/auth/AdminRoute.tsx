import { message } from 'antd'
import { useEffect } from 'react'
import { Navigate } from 'react-router-dom'

import { useAuth } from './AuthContext'
import ProtectedRoute from './ProtectedRoute'

/** 平台管理员专属路由：未授权时跳转知识域列表 */
export default function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()

  useEffect(() => {
    if (user && !user.is_platform_admin) {
      message.warning('需要平台管理员权限')
    }
  }, [user])

  return (
    <ProtectedRoute>
      {user?.is_platform_admin ? children : <Navigate to="/knowledge" replace />}
    </ProtectedRoute>
  )
}
