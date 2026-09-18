/**
 * Thin fetch wrapper for the AIFCS backend.
 *
 * Requests are same-origin: the Vite dev server proxies /api to the backend,
 * and in production the backend serves the built frontend.
 */

import type {
  ComputeInfo,
  ConfigSummary,
  AgentDecision,
  AgentsResponse,
  Entity,
  HealthResponse,
  ScenariosResponse,
  SimEvent,
  SimulationStatus,
  SystemStatus,
} from '@/types/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    // Network-level failure: the backend is most likely not running.
    throw new ApiError(
      `Cannot reach the AIFCS backend at ${path}. Is it running on port 8000?`,
    )
  }

  if (!response.ok) {
    throw new ApiError(`${init?.method ?? 'GET'} ${path} failed (${response.status})`, response.status)
  }
  return (await response.json()) as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  health: () => request<HealthResponse>('/api/health'),
  systemStatus: () => request<SystemStatus>('/api/system/status'),
  compute: () => request<ComputeInfo>('/api/system/compute'),
  config: () => request<ConfigSummary>('/api/config'),

  // Simulation control (PHASE 1). Each call drives the real engine.
  simulationStatus: () => request<SimulationStatus>('/api/simulation/status'),
  start: (scenario?: string, seed?: number) =>
    post<SimulationStatus>('/api/simulation/start', { scenario: scenario ?? null, seed: seed ?? null }),
  pause: () => post<SimulationStatus>('/api/simulation/pause'),
  resume: () => post<SimulationStatus>('/api/simulation/resume'),
  stop: () => post<SimulationStatus>('/api/simulation/stop'),
  reset: () => post<SimulationStatus>('/api/simulation/reset'),
  step: (ticks: number) => post<SimulationStatus>('/api/simulation/step', { ticks }),
  setSpeed: (speed: number) => post<SimulationStatus>('/api/simulation/speed', { speed }),

  entities: () => request<{ count: number; entities: Entity[] }>('/api/entities'),
  events: (limit = 40) => request<{ count: number; events: SimEvent[] }>(`/api/events?limit=${limit}`),
  scenarios: () => request<ScenariosResponse>('/api/scenarios'),

  // Agents (PHASE 3).
  agents: () => request<AgentsResponse>('/api/agents'),
  decisions: (limit = 40) =>
    request<{ count: number; decisions: AgentDecision[] }>(`/api/decisions?limit=${limit}`),
}
