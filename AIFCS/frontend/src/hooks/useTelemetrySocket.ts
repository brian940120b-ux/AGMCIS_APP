import { useEffect, useRef } from 'react'
import { useSimulationStore } from '@/stores/simulationStore'
import type { TelemetryFrame } from '@/types/api'

const RECONNECT_DELAY_MS = 2000
/** If the socket cannot be established, fall back to polling rather than showing nothing. */
const FALLBACK_AFTER_FAILURES = 2

/**
 * Subscribe to /ws/simulation (PHASE 7).
 *
 * The server pushes at its own rate, decoupled from both the physics tick and
 * the browser's frame rate. If the socket cannot be established the hook says
 * so and the polling fallback takes over — the dashboard never silently shows
 * stale data.
 */
export function useTelemetrySocket(enabled = true): void {
  const applyFrame = useSimulationStore((s) => s.applyFrame)
  const setTransport = useSimulationStore((s) => s.setTransport)
  const failures = useRef(0)

  useEffect(() => {
    if (!enabled) return

    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined
    let closed = false

    const connect = () => {
      if (closed) return

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/ws/simulation`

      setTransport(failures.current >= FALLBACK_AFTER_FAILURES ? 'polling' : 'connecting')

      try {
        socket = new WebSocket(url)
      } catch {
        scheduleReconnect()
        return
      }

      socket.onopen = () => {
        failures.current = 0
        setTransport('live')
      }

      socket.onmessage = (message) => {
        try {
          applyFrame(JSON.parse(message.data as string) as TelemetryFrame)
        } catch {
          // A malformed frame is dropped; the next one will arrive shortly.
        }
      }

      socket.onerror = () => {
        // onclose always follows, which is where reconnection is handled.
      }

      socket.onclose = () => {
        if (closed) return
        failures.current += 1
        setTransport(failures.current >= FALLBACK_AFTER_FAILURES ? 'polling' : 'connecting')
        scheduleReconnect()
      }
    }

    const scheduleReconnect = () => {
      if (closed) return
      reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS)
    }

    connect()

    return () => {
      closed = true
      window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [enabled, applyFrame, setTransport])
}
