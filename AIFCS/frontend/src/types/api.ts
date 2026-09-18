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
