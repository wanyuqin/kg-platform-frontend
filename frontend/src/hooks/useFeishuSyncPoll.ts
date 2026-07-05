import { useCallback, useEffect, useRef, useState } from 'react'

import {
  FEISHU_SYNC_POLLING,
  FeishuSyncStatus,
  FeishuSyncStatusOut,
  fetchFeishuSyncStatus,
} from '../api/client'

const POLL_MS = 3000

/** sync_status 为 pending/syncing 时每 3s 拉 sync-status，直到终态。 */
export function useFeishuSyncPoll(docId: number | undefined, enabled: boolean) {
  const [status, setStatus] = useState<FeishuSyncStatusOut | null>(null)
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    if (!docId || !enabled) return null
    setLoading(true)
    try {
      const data = await fetchFeishuSyncStatus(docId)
      setStatus(data)
      return data
    } finally {
      setLoading(false)
    }
  }, [docId, enabled])

  useEffect(() => {
    if (!docId || !enabled) {
      setStatus(null)
      return
    }
    void refresh()
  }, [docId, enabled, refresh])

  useEffect(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    const syncStatus = status?.sync_status as FeishuSyncStatus | null | undefined
    if (!docId || !enabled || !syncStatus || !FEISHU_SYNC_POLLING.includes(syncStatus)) {
      return
    }
    timerRef.current = setInterval(() => {
      void refresh()
    }, POLL_MS)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [docId, enabled, status?.sync_status, refresh])

  return { status, loading, refresh }
}
