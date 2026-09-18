/** Types mirroring the AIFCS backend API contract (backend/api/health.py). */

export type SubsystemState =
  | 'ONLINE'
  | 'READY'
  | 'WARNING'
  | 'ERROR'
  | 'OFFLINE'
  | 'NOT_IMPLEMENTED'

export interface Subsystem {
  key: string
  label: string
  state: SubsystemState
  detail: string
  phase: string
  metadata: Record<string, unknown>
}

export interface SystemStatus {
  operational: boolean
  phase: string
  config_hash: string
  subsystems: Subsystem[]
}

export interface HealthResponse {
  status: string
  app: string
  title: string
  version: string
  uptime_s: number
  config_hash: string
  timestamp: number
}

export interface ComputeInfo {
  cpu: string
  platform: string
  python: string
  torch_installed: boolean
  cuda_available: boolean
  gpu_name: string | null
  vram_total_mb: number | null
  device: string
  training_device: string
  detail?: string
  torch_version?: string
}

export interface WorldBounds {
  x_min: number
  x_max: number
  y_min: number
  y_max: number
  altitude_min: number
  altitude_max: number
}

export interface ConfigSummary {
  config_hash: string
  simulation: {
    tick_rate_hz: number
    dt: number
    allowed_speeds: number[]
    default_speed: number
    deterministic: boolean
    seed: number
    max_duration_s: number
  }
  world: {
    bounds: WorldBounds
    gravity_mps2: number
    air_density_kgpm3: number
  }
  telemetry: { broadcast_rate_hz: number; max_entities_per_frame: number }
  agents: { default_type: string; decision_rate_hz: number; strict_action_validation: boolean }
  scenarios: { directory: string; default_scenario: string; strict_validation: boolean }
}

// ---------------------------------------------------------------- PHASE 1

export type ClockState = 'STOPPED' | 'RUNNING' | 'PAUSED'

export interface ClockSnapshot {
  state: ClockState
  tick: number
  tick_rate_hz: number
  dt: number
  simulation_time: number
  real_time_s: number
  speed: number
  realtime_factor: number
}

export interface SimulationStatus {
  scenario: string | null
  scenario_loaded: boolean
  clock: ClockSnapshot
  seed: number
  deterministic: boolean
  integrator: string
  config_hash: string
  entity_count: number
  active_entities: number
  duration_s: number | null
  end_reason: string | null
  state_hash: string
  events_published: number
}

export type EntityTeam = 'BLUE' | 'RED' | 'NEUTRAL'
export type EntityStatus = 'ACTIVE' | 'INACTIVE' | 'OUT_OF_BOUNDS' | 'DISABLED'

export interface ControlState {
  aileron: number
  elevator: number
  rudder: number
  throttle: number
}

export interface Entity {
  id: string
  team: EntityTeam
  position: [number, number, number]
  velocity: [number, number, number]
  /** Roll, pitch, yaw in radians. */
  orientation: [number, number, number]
  attitude_quaternion: [number, number, number, number]
  controls: ControlState
  altitude: number
  speed: number
  heading_deg: number
  health: number
  energy: number
  fuel: number
  status: EntityStatus
  metadata: Record<string, unknown>
}

export interface SimEvent {
  type: string
  simulation_time: number
  tick: number
  entity_id: string | null
  agent_id: string | null
  message: string
  data: Record<string, unknown>
  wall_time: number
}

export interface ScenarioSummary {
  name: string
  description: string
  version: string
  duration_s: number
  seed: number | null
  entity_count: number
}

export interface ScenariosResponse {
  directory: string
  default: string
  available: string[]
  loaded: ScenarioSummary | null
}

// ---------------------------------------------------------------- PHASE 3

export interface AgentDecision {
  agent_id: string
  entity_id: string
  simulation_time: number
  tick: number
  behaviour: 'HOLD' | 'NAVIGATE' | 'PATROL' | 'FORMATION' | 'AVOID'
  confidence: number
  reason_codes: string[]
  observation: {
    altitude_m: number
    speed_mps: number
    heading_deg: number
    contacts: number
    nearest_contact_m: number | null
    confidence: number
  }
  metrics: Record<string, unknown>
}

export interface AgentSummary {
  agent_id: string
  entity_id: string
  team: EntityTeam
  type: string
  decision_count: number
  last_decision: AgentDecision | null
}

export interface AgentsResponse {
  agent_count: number
  decision_rate_hz: number
  decision_interval_ticks: number
  total_decisions: number
  agents: AgentSummary[]
}

// ---------------------------------------------------------------- PHASE 4

export interface ControllerStatus {
  commands_applied: number
  commands_rejected: number
  violations: Record<string, number>
  limits: {
    max_control_rate_per_s: number
    max_load_factor: number
    min_altitude_m: number
    max_altitude_m: number
  }
}

// ---------------------------------------------------------------- PHASE 5

export interface SensorStatus {
  enabled: boolean
  max_range_m: number
  field_of_regard_deg: number
  latency_s: number
  dropout_probability: number
  track_memory_s: number
  tracked_contacts: Record<string, number>
}
