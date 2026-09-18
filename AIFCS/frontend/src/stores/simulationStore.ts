/**
 * Simulation store — control state and live world data (PHASE 1).
 *
 * Every action calls the backend and stores what it returns. The UI never
 * simulates anything locally: if the engine refuses a command (for example
 * "cannot pause while STOPPED"), that refusal is what the user sees.
 *
 * Polling is temporary. PHASE 7 replaces it with the /ws/simulation WebSocket.
 */

import { create } from 'zustand'
import { api, ApiError } from '@/api/client'
import type {
  AgentDecision,
  AgentsResponse,
  ControllerStatus,
  Entity,
  SimEvent,
  SimulationStatus,
} from '@/types/api'

interface SimulationState {
  status: SimulationStatus | null
  entities: Entity[]
  events: SimEvent[]
  agents: AgentsResponse | null
  decisions: AgentDecision[]
  controller: ControllerStatus | null
  scenarios: string[]
  selectedScenario: string | null
  busy: boolean
  error: string | null

  refresh: () => Promise<void>
  loadScenarios: () => Promise<void>
  selectScenario: (name: string) => void
  start: () => Promise<void>
  pause: () => Promise<void>
  resume: () => Promise<void>
  reset: () => Promise<void>
  step: (ticks: number) => Promise<void>
  setSpeed: (speed: number) => Promise<void>
}

const message = (cause: unknown) =>
  cause instanceof ApiError ? cause.message : 'Unexpected error talking to the backend'

export const useSimulationStore = create<SimulationState>((set, get) => {
  /** Run a control action, then refresh so the UI shows the engine's real state. */
  const command = async (action: () => Promise<SimulationStatus>) => {
    set({ busy: true, error: null })
    try {
      const status = await action()
      set({ status })
      await get().refresh()
    } catch (cause) {
      set({ error: message(cause) })
    } finally {
      set({ busy: false })
    }
  }

  return {
    status: null,
    entities: [],
    events: [],
    agents: null,
    decisions: [],
    controller: null,
    scenarios: [],
    selectedScenario: null,
    busy: false,
    error: null,

    refresh: async () => {
      try {
        const [status, entities, events, agents, decisions, controller] = await Promise.all([
          api.simulationStatus(),
          api.entities(),
          api.events(40),
          api.agents(),
          api.decisions(40),
          api.controller(),
        ])
        set({
          status,
          entities: entities.entities,
          events: events.events,
          agents,
          decisions: decisions.decisions,
          controller,
          error: null,
        })
      } catch (cause) {
        set({ error: message(cause) })
      }
    },

    loadScenarios: async () => {
      try {
        const response = await api.scenarios()
        set((state) => ({
          scenarios: response.available,
          selectedScenario: state.selectedScenario ?? response.loaded?.name ?? response.default,
        }))
      } catch (cause) {
        set({ error: message(cause) })
      }
    },

    selectScenario: (name) => set({ selectedScenario: name }),

    start: () => command(() => api.start(get().selectedScenario ?? undefined)),
    pause: () => command(() => api.pause()),
    resume: () => command(() => api.resume()),
    reset: () => command(() => api.reset()),
    step: (ticks) => command(() => api.step(ticks)),
    setSpeed: (speed) => command(() => api.setSpeed(speed)),
  }
})
