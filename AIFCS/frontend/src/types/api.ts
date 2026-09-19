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

// ---------------------------------------------------------------- PHASE 6

export interface CommsStats {
  sent: number
  delivered: number
  lost: number
  dropped_bandwidth: number
  blocked_blackout: number
  reordered: number
}

export interface CommunicationsStatus {
  enabled: boolean
  latency_base_s: number
  latency_jitter_s: number
  packet_loss_probability: number
  max_messages_per_second: number
  report_rate_hz: number
  blackout_windows: number[][]
  blackout_active: boolean
  in_flight: number
  participants: number
  stats: CommsStats
  datalink: {
    report_rate_hz: number
    report_interval_ticks: number
    datalink_tracks: Record<string, number>
  }
}

// ---------------------------------------------------------------- PHASE 7

/** One frame pushed over /ws/simulation. */
export interface TelemetryFrame {
  type: 'snapshot' | 'telemetry'
  sequence: number
  wall_time: number
  clock: ClockSnapshot
  scenario: string | null
  state_hash: string
  entities: Entity[]
  /** Only what this client has not already received. */
  events: SimEvent[]
  decisions: AgentDecision[]
  controller: ControllerStatus
  sensors: SensorStatus
  communications: CommunicationsStatus
  agents: {
    agent_count: number
    decision_rate_hz: number
    total_decisions: number
  }
}

export type TransportState = 'connecting' | 'live' | 'polling' | 'offline'

/* ---------------------------------------------------------------- PHASE 9 */

/** One recording on disk, as the listing reports it (header only). */
export interface RecordingSummary {
  run_id: string
  path: string
  filename: string
  size_bytes: number
  modified_at: number
  readable: boolean
  error?: string
  scenario?: string | null
  seed?: number | null
  config_hash?: string | null
  record_rate_hz?: number | null
  started_at?: number | null
}

export interface RecordingDetail {
  run_id: string
  path: string
  scenario: string
  seed: number
  config_hash: string | null
  tick_rate_hz: number
  record_rate_hz: number
  frames: number
  duration_s: number
  entities: string[]
  complete: boolean
  truncated: boolean
  end_reason: string | null
  final_state_hash: string | null
  size_bytes: number
}

/** Where the playback cursor is. `loaded: false` means nothing is open. */
export interface ReplayStatus {
  loaded: boolean
  playing: boolean
  speed?: number
  allowed_speeds?: number[]
  frame_index?: number
  frame_count?: number
  progress?: number
  tick?: number
  simulation_time?: number
  duration_s?: number
  state_hash?: string | null
  at_end?: boolean
  recording?: RecordingDetail
  event_count?: number
}

/** A recorded frame: the world at one sampled tick. */
export interface ReplayFrame {
  kind: string
  tick: number
  simulation_time: number
  state_hash: string
  entities: Entity[]
  events: SimEvent[]
  decisions: AgentDecision[]
}

export interface ReplayEventMarker {
  frame: number
  tick: number
  simulation_time: number
  type: string
  entity_id: string | null
  message: string
}

/** One scored dimension, with the measurement behind it. */
export interface ScoreTerm {
  name: string
  fraction: number
  weight: number
  points: number
  applicable: boolean
  detail: Record<string, unknown>
}

export interface EntityScore {
  entity_id: string
  team: string
  total: number
  terms: ScoreTerm[]
  metrics: Record<string, string | number | boolean>
}

export interface RunScore {
  run_id: string
  scenario: string
  seed: number
  max_points: number
  weights_hash: string
  entities: EntityScore[]
  teams: Record<string, number>
}

/** A stored score row, as the run detail returns it. */
export interface StoredScore {
  run_id: string
  subject: 'entity' | 'team'
  subject_id: string
  total: number
  breakdown: { terms?: ScoreTerm[] }
  weights_hash: string
  scored_at: number
}

export interface RunSummary {
  run_id: string
  scenario_name: string
  seed: number
  config_hash: string
  integrator: string
  tick_rate_hz: number
  app_version: string
  started_at: number
  ended_at: number | null
  end_reason: string | null
  ticks: number
  simulation_time_s: number
  final_state_hash: string | null
  entity_count: number
  replay_path?: string | null
  replay_frames?: number | null
  replay_bytes?: number | null
  scores: StoredScore[]
}

export interface RunEntity {
  run_id: string
  entity_id: string
  team: string
  agent_type: string
  agent_id: string | null
  final_status: string | null
}

export interface RunDetail extends RunSummary {
  entities: RunEntity[]
  replay: {
    run_id: string
    path: string
    format: string
    compressed: number
    record_rate_hz: number
    frames: number
    bytes: number
    created_at: number
  } | null
  metrics: Record<string, Record<string, number>>
}

/** What the current run is recording, from /api/runs/current. */
export interface CurrentRun {
  active: boolean
  run_id: string | null
  recording: boolean
  storing: boolean
  scoring_enabled: boolean
  replay_directory: string
  frames: number
  bytes: number
  truncated: boolean
  database: { path: string; size_bytes: number; schema_version: number; rows: Record<string, number> } | null
  last_run: Record<string, unknown> | null
}

export interface ScoringWeights {
  enabled: boolean
  weights: Record<string, number>
  thresholds: Record<string, number>
  redistribute_inapplicable: boolean
  max_points: number
  weights_hash: string
  notice: string
}

/* --------------------------------------------------------------- PHASE 10 */

/** One entity as declared in a scenario file — every field, nothing dropped. */
export interface ScenarioEntityDetail {
  id: string
  team: EntityTeam
  type: string
  position: [number, number, number]
  velocity: [number, number, number]
  orientation: [number, number, number]
  health: number
  energy: number
  fuel: number
  controls: ControlState
  agent: string | null
  waypoints: [number, number, number][]
  route_loop: boolean
  formation_leader: string | null
  formation_offset: [number, number, number]
}

/** A parsed scenario, as the API returns it. */
export interface ScenarioDetail {
  name: string
  description: string
  version: string
  duration_s: number
  seed: number | null
  entity_count: number
  entities: ScenarioEntityDetail[]
  environment: Record<string, unknown>
  protected?: boolean
  /** The YAML document shape, which is what gets written back. */
  document?: ScenarioDocument
}

/** The YAML shape. Kept loose because it is the file, not a view model. */
export interface ScenarioDocument {
  scenario: {
    name: string
    description?: string
    version?: string
    duration?: number
    seed?: number | null
  }
  environment?: Record<string, unknown>
  entities: Record<string, unknown>[]
}

/** A row in the scenario list; `readable: false` carries the reason. */
export interface ScenarioListEntry {
  name: string
  readable: boolean
  protected: boolean
  size_bytes: number
  modified_at: number
  error?: string
  description?: string
  version?: string
  duration_s?: number
  seed?: number | null
  entity_count?: number
  teams?: string[]
}

export interface ScenarioCatalogue {
  directory: string
  default: string
  available: string[]
  scenarios: ScenarioListEntry[]
  loaded: ScenarioDetail | null
}

/** Result of checking a draft without saving it. */
export type ScenarioValidation =
  | { valid: true; scenario: ScenarioDetail }
  | { valid: false; error: string }

/* ------------------------------------------------------------ PHASE 11-13 */

export interface TrainingStatus {
  available: boolean
  install_hint: string | null
  how_to_run: string
  /** False until the training centre phase: a long job needs progress,
   *  cancellation and reload survival before a button can honestly exist. */
  browser_control: boolean
  browser_control_note: string
  device: string
  configured_device: string
  algorithms: string[]
  scenario: string
  entity_id: string | null
  max_episode_seconds: number
  output_directory: string
  hyperparameters: Record<string, Record<string, number | null>>
}

export interface TrainingEnvironmentSpec {
  id: string
  scenario: string
  entity_id: string | null
  observation_size: number
  observation_layout_version: number
  action_channels: string[]
  decision_rate_hz: number
  ticks_per_step: number
  step_seconds: number
  max_episode_steps: number
  reward_weights: Record<string, number>
  notice: string
}

export interface TrainingReward {
  terms: string[]
  weights: Record<string, number>
  notice: string
}

export interface TrainedModel {
  model_id: string
  path: string
  size_bytes: number
  created_at: number
  algorithm: string
  card: {
    training_id?: string
    algorithm?: string
    seed?: number
    total_timesteps?: number
    elapsed_s?: number
    device?: string
    environment?: { scenario?: string; observation_layout_version?: number }
    evaluation?: {
      episodes: number
      mean_reward: number
      std_reward: number
      mean_episode_steps: number
      mean_goals_reached: number
      endings: Record<string, number>
    }
  } | null
  card_error?: string
}
