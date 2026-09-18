import { useEffect } from 'react'
import { useSimulationStore } from '@/stores/simulationStore'

/**
 * Poll the engine for world state.
 *
 * Polls quickly while the simulation is running and slowly when it is not, so
 * an idle dashboard does not hammer the backend. This is a stand-in until the
 * WebSocket telemetry channel arrives in PHASE 7.
 */
export function useSimulationPolling(): void {
  const refresh = useSimulationStore((s) => s.refresh)
  const loadScenarios = useSimulationStore((s) => s.loadScenarios)
  const clockState = useSimulationStore((s) => s.status?.clock.state)

  useEffect(() => {
    void loadScenarios()
  }, [loadScenarios])

  useEffect(() => {
    const intervalMs = clockState === 'RUNNING' ? 200 : 1000
    void refresh()
    const timer = window.setInterval(() => void refresh(), intervalMs)
    return () => window.clearInterval(timer)
  }, [refresh, clockState])
}
