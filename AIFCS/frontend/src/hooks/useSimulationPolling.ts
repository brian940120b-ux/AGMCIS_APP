import { useEffect } from 'react'
import { useSimulationStore } from '@/stores/simulationStore'

/**
 * Poll the engine for world state.
 *
 * Since PHASE 7 the WebSocket carries live telemetry, so this runs slowly: it
 * fetches the things the frame does not carry (scenario list, seed, config
 * hash) and acts as the fallback when the socket is unavailable.
 */
export function useSimulationPolling(): void {
  const refresh = useSimulationStore((s) => s.refresh)
  const loadScenarios = useSimulationStore((s) => s.loadScenarios)
  const transport = useSimulationStore((s) => s.transport)

  useEffect(() => {
    void loadScenarios()
  }, [loadScenarios])

  useEffect(() => {
    // The socket is carrying state, so poll rarely just to stay in sync on the
    // fields it does not include. Without it, poll fast enough to be usable.
    const intervalMs = transport === 'live' ? 5000 : 500
    void refresh()
    const timer = window.setInterval(() => void refresh(), intervalMs)
    return () => window.clearInterval(timer)
  }, [refresh, transport])
}
