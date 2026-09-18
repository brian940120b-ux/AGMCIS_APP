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
  TelemetryFrame,
  TransportState,
  CommunicationsStatus,
  ControllerStatus,
  SensorStatus,
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
  sensors: SensorStatus | null
  communications: CommunicationsStatus | null
  scenarios: string[]
  selectedScenario: string | null
  busy: boolean
  error: string | null
  /** How state is arriving: pushed over WebSocket, or polled as a fallback. */
  transport: TransportState
  /** Telemetry frames received on the current connection. */
  framesReceived: number

  applyFrame: (frame: TelemetryFrame) => void
  setTransport: (transport: TransportState) => void
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
    sensors: null,
    communications: null,
    scenarios: [],
    selectedScenario: null,
    busy: false,
    error: null,
    transport: 'connecting',
    framesReceived: 0,

    /**
     * Fold one pushed telemetry frame into the store.
     *
     * Events and decisions arrive incrementally — only what this client has not
     * seen — so they are appended to a bounded buffer rather than replacing it.
     */
    applyFrame: (frame) =>
      set((state) => {
        const FEED_LIMIT = 60
        const events = frame.events.length
          ? [...state.events, ...frame.events].slice(-FEED_LIMIT)
          : state.events
        const decisions = frame.decisions.length
          ? [...state.decisions, ...frame.decisions].slice(-FEED_LIMIT)
          : state.decisions

        return {
          status: {
            scenario: frame.scenario,
            scenario_loaded: frame.scenario !== null,
            clock: frame.clock,
            seed: state.status?.seed ?? 0,
            deterministic: state.status?.deterministic ?? true,
            integrator: state.status?.integrator ?? '',
            config_hash: state.status?.config_hash ?? '',
            entity_count: frame.entities.length,
            active_entities: frame.entities.filter((e) => e.status === 'ACTIVE').length,
            duration_s: state.status?.duration_s ?? null,
            end_reason: state.status?.end_reason ?? null,
            state_hash: frame.state_hash,
            events_published: state.status?.events_published ?? 0,
          },
          entities: frame.entities,
          events,
          decisions,
          controller: frame.controller,
          sensors: frame.sensors,
          communications: frame.communications,
          agents: {
            agent_count: frame.agents.agent_count,
            decision_rate_hz: frame.agents.decision_rate_hz,
            decision_interval_ticks: state.agents?.decision_interval_ticks ?? 0,
            total_decisions: frame.agents.total_decisions,
            agents: state.agents?.agents ?? [],
          },
          framesReceived: state.framesReceived + 1,
          error: null,
        }
      }),

    setTransport: (transport) => set({ transport }),

    refresh: async () => {
      try {
        const [status, entities, events, agents, decisions, controller, sensors, communications] =
          await Promise.all([
            api.simulationStatus(),
            api.entities(),
            api.events(40),
            api.agents(),
            api.decisions(40),
            api.controller(),
            api.sensors(),
            api.communications(),
          ])
        set({
          status,
          entities: entities.entities,
          events: events.events,
          agents,
          decisions: decisions.decisions,
          controller,
          sensors,
          communications,
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
