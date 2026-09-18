import { useEffect } from 'react'
import { useSystemStore } from '@/stores/systemStore'

/**
 * Poll backend system state on an interval.
 *
 * This is deliberately slow: PHASE 0 has no WebSocket yet, and system status
 * changes rarely. Live telemetry arrives over /ws/simulation in PHASE 7.
 */
export function useSystemPolling(intervalMs = 5000): void {
  const refresh = useSystemStore((state) => state.refresh)

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), intervalMs)
    return () => window.clearInterval(timer)
  }, [refresh, intervalMs])
}
