/**
 * Thin fetch wrapper for the AIFCS backend.
 *
 * Requests are same-origin: the Vite dev server proxies /api to the backend,
 * and in production the backend serves the built frontend.
 */

import type {
  ComputeInfo,
  ConfigSummary,
  CommunicationsStatus,
  ControllerStatus,
  AgentDecision,
  AgentsResponse,
  CurrentRun,
  Entity,
  HealthResponse,
  RecordingSummary,
  ReplayEventMarker,
  ReplayFrame,
  ReplayStatus,
  RunDetail,
  RunScore,
  RunSummary,
  ScenarioCatalogue,
  ScenarioDetail,
  ScenarioDocument,
  ScenariosResponse,
  ScenarioValidation,
  CommandersResponse,
  PhysicsStatus,
  RunAnalytics,
  StartTrainingRequest,
  RunComparison,
  ScoringWeights,
  TasksResponse,
  TeamPicture,
  TrainedModel,
  TrainingEnvironmentSpec,
  TrainingJob,
  TrainingJobs,
  TrainingReward,
  TrainingStatus,
  SensorStatus,
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
    // Surface the backend's own reason. Every refusal in this API explains
    // itself — "a simulation is running", "above the configured cap" — and
    // swallowing that for a status code would leave the operator guessing.
    let detail = ''
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // A non-JSON error body is not itself an error; fall back to the status.
    }
    throw new ApiError(
      detail || `${init?.method ?? 'GET'} ${path} failed (${response.status})`,
      response.status,
    )
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
  controller: () => request<ControllerStatus>('/api/controller'),
  sensors: () => request<SensorStatus>('/api/sensors'),
  communications: () => request<CommunicationsStatus>('/api/communications'),
  decisions: (limit = 40) =>
    request<{ count: number; decisions: AgentDecision[] }>(`/api/decisions?limit=${limit}`),

  // Replay, run history and scoring (PHASE 9). Every transport call moves the
  // real server-side cursor; nothing here is simulated in the browser.
  recordings: () =>
    request<{ directory: string; count: number; recordings: RecordingSummary[]; enabled: boolean }>(
      '/api/replay/recordings',
    ),
  replayLoad: (runId: string) => post<ReplayStatus>('/api/replay/load', { run_id: runId }),
  replayUnload: () => post<ReplayStatus>('/api/replay/unload'),
  replayStatus: () => request<ReplayStatus>('/api/replay/status'),
  replayFrame: () =>
    request<{ status: ReplayStatus; frame: ReplayFrame | null }>('/api/replay/frame'),
  replayPlay: () => post<ReplayStatus>('/api/replay/play'),
  replayPause: () => post<ReplayStatus>('/api/replay/pause'),
  replayStep: (frames: number) => post<ReplayStatus>('/api/replay/step', { frames }),
  replaySeek: (frame: number) => post<ReplayStatus>('/api/replay/seek', { frame }),
  replaySpeed: (speed: number) => post<ReplayStatus>('/api/replay/speed', { speed }),
  replayJump: (direction: number) =>
    post<ReplayStatus & { jumped_to: ReplayEventMarker | null }>('/api/replay/jump', { direction }),
  replayEvents: () =>
    request<{ count: number; events: ReplayEventMarker[] }>('/api/replay/events'),

  runs: (limit = 50) =>
    request<{ count: number; runs: RunSummary[]; current: CurrentRun }>(`/api/runs?limit=${limit}`),
  currentRun: () => request<CurrentRun>('/api/runs/current'),
  run: (runId: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`),
  scoreRun: (runId: string) => post<RunScore>(`/api/runs/${encodeURIComponent(runId)}/score`),
  deleteRun: (runId: string) =>
    request<{ run_id: string; rows_deleted: boolean; recording_deleted: boolean }>(
      `/api/runs/${encodeURIComponent(runId)}`,
      { method: 'DELETE' },
    ),
  scoringWeights: () => request<ScoringWeights>('/api/scoring/weights'),

  // Scenario editing (PHASE 10). Every write is validated by the backend
  // first, so the editor can never put a file on disk that will not load.
  scenarioCatalogue: () => request<ScenarioCatalogue>('/api/scenarios'),
  scenarioTemplate: () =>
    request<{ document: ScenarioDocument; yaml: string }>('/api/scenarios/template'),
  scenario: (name: string) => request<ScenarioDetail>(`/api/scenarios/${encodeURIComponent(name)}`),
  scenarioExport: (name: string) =>
    request<{ name: string; yaml: string }>(`/api/scenarios/${encodeURIComponent(name)}/export`),
  scenarioValidate: (document: ScenarioDocument) =>
    post<ScenarioValidation>('/api/scenarios/validate', document),
  scenarioCreate: (document: ScenarioDocument) => post<ScenarioDetail>('/api/scenarios', document),
  scenarioUpdate: (name: string, document: ScenarioDocument) =>
    request<ScenarioDetail>(`/api/scenarios/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(document),
    }),
  scenarioClone: (name: string, newName: string) =>
    post<ScenarioDetail>(`/api/scenarios/${encodeURIComponent(name)}/clone`, { new_name: newName }),
  scenarioImport: (yamlText: string, name?: string, overwrite = false) =>
    post<ScenarioDetail>('/api/scenarios/import', {
      yaml_text: yamlText,
      name: name ?? null,
      overwrite,
    }),
  scenarioDelete: (name: string) =>
    request<{ name: string; deleted: boolean }>(`/api/scenarios/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),

  // Training (PHASE 11-13). Read-only: runs are started from the command line
  // until the training centre can report progress and be cancelled.
  trainingStatus: () => request<TrainingStatus>('/api/training/status'),
  trainingEnvironment: () => request<TrainingEnvironmentSpec>('/api/training/environment'),
  trainingReward: () => request<TrainingReward>('/api/training/reward'),
  // Training control (PHASE 18). A job runs in the server, one at a time.
  trainingJobs: () => request<TrainingJobs>('/api/training/jobs'),
  trainingJob: (jobId: string) => request<TrainingJob>(`/api/training/jobs/${jobId}`),
  startTraining: (body: StartTrainingRequest) => post<TrainingJob>('/api/training/start', body),
  stopTraining: () => post<TrainingJob>('/api/training/stop'),
  // Teams, tasks and commanders (PHASE 14-15). Read-only: allocation happens
  // inside the tick, and a second source of orders would disagree with it.
  teams: () => request<{ count: number; teams: TeamPicture[] }>('/api/teams'),
  tasks: () => request<TasksResponse>('/api/tasks'),
  commanders: () => request<CommandersResponse>('/api/commanders'),
  // Which physics model is flying (PHASE 16). Read-only: the backend is a
  // configuration choice, because a run is only reproducible from its hash.
  physics: () => request<PhysicsStatus>('/api/physics'),
  // Analytics (PHASE 17). The aggregation is the backend's job: a run holds
  // thousands of samples, and two clients grouping them would disagree.
  runAnalytics: (runId: string) => request<RunAnalytics>(`/api/analytics/runs/${runId}`),
  compareRuns: (runIds: string[]) =>
    request<RunComparison>(`/api/analytics/compare?runs=${encodeURIComponent(runIds.join(','))}`),

  trainingModels: () =>
    request<{ available: boolean; count: number; models: TrainedModel[]; install_hint?: string }>(
      '/api/training/models',
    ),
}
