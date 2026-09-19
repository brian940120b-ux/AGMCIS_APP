/**
 * What the tactical views should draw (PHASE 9, extended in PHASE 10).
 *
 * There are now three possible sources of entities — live telemetry, a
 * recording being played back, and the scenario being edited — and the viewers
 * must never blend them. A replay frame drawn alongside live positions, or a
 * draft's starting positions mixed into a running simulation, would each be a
 * picture of something that never happened.
 *
 * This hook is the single place that decides which one is on stage, so the 3D
 * view, the 2D plot and the entity list cannot disagree about what they show.
 */

import { useReplayStore } from '@/stores/replayStore'
import { useScenarioStore } from '@/stores/scenarioStore'
import { useSimulationStore } from '@/stores/simulationStore'
import { useStageStore, type StageMode } from '@/stores/stageStore'
import type { Entity, ScenarioEntityDetail } from '@/types/api'

export interface Stage {
  mode: StageMode
  entities: Entity[]
  trails: Record<string, [number, number, number][]>
  /** Scenario name for the header, from whichever source is on stage. */
  scenario: string | null
  /** Simulation time of what is being shown, in seconds. */
  simulationTime: number
  /** Short label for the viewer header: "LIVE", a run id, or a scenario name. */
  label: string
  /** True when replay is selected but no recording is open yet. */
  awaitingRecording: boolean
  /** Why the stage is empty, when it is and the reason is not obvious. */
  emptyHint: string | null
}

const NO_TRAILS: Record<string, [number, number, number][]> = {}

/**
 * Turn a declared scenario unit into something the renderer can draw.
 *
 * This is the scenario's *initial* state — tick zero, before any physics — so
 * the derived values are computed from the declaration rather than reported by
 * the engine. It is a preview of where units start, not a simulation of them.
 */
function previewEntity(declared: ScenarioEntityDetail): Entity {
  const [east, north] = declared.velocity
  const speed = Math.hypot(...declared.velocity)
  const heading =
    east === 0 && north === 0
      ? ((declared.orientation[2] * 180) / Math.PI + 360) % 360
      : ((Math.atan2(east, north) * 180) / Math.PI + 360) % 360

  return {
    id: declared.id,
    team: declared.team,
    position: declared.position,
    velocity: declared.velocity,
    orientation: declared.orientation,
    // Identity: the renderer reads Euler angles, and a declared scenario has
    // no integrated attitude to report.
    attitude_quaternion: [1, 0, 0, 0],
    controls: declared.controls,
    altitude: declared.position[2],
    speed,
    heading_deg: heading,
    health: declared.health,
    energy: declared.energy,
    fuel: declared.fuel,
    status: 'ACTIVE',
    metadata: { type: declared.type, preview: true },
  }
}

export function useStage(): Stage {
  const mode = useStageStore((s) => s.mode)

  const liveEntities = useSimulationStore((s) => s.entities)
  const liveTrails = useSimulationStore((s) => s.trails)
  const liveStatus = useSimulationStore((s) => s.status)

  const replayEntities = useReplayStore((s) => s.entities)
  const replayTrails = useReplayStore((s) => s.trails)
  const replayStatus = useReplayStore((s) => s.status)

  const validation = useScenarioStore((s) => s.validation)
  const openName = useScenarioStore((s) => s.openName)

  if (mode === 'replay') {
    return {
      mode,
      entities: replayEntities,
      trails: replayTrails,
      scenario: replayStatus.recording?.scenario ?? null,
      simulationTime: replayStatus.simulation_time ?? 0,
      label: replayStatus.recording?.run_id ?? 'no recording',
      awaitingRecording: !replayStatus.loaded,
      emptyHint: replayStatus.loaded
        ? null
        : 'Replay mode. Choose a recording in the Replay panel to load it.',
    }
  }

  if (mode === 'edit') {
    // Only a scenario that validates can be previewed. Drawing a draft the
    // parser rejects would show positions the engine would never produce.
    const preview = validation?.valid ? validation.scenario : null
    return {
      mode,
      entities: preview ? preview.entities.map(previewEntity) : [],
      trails: NO_TRAILS,
      scenario: preview?.name ?? openName,
      simulationTime: 0,
      label: preview ? `${preview.name} · start` : 'no scenario open',
      awaitingRecording: false,
      emptyHint: preview
        ? null
        : openName
          ? 'This draft has an error. Fix it in the editor and the preview returns.'
          : 'Edit mode. Open a scenario, or start a new one, to see where its units begin.',
    }
  }

  return {
    mode,
    entities: liveEntities,
    trails: liveTrails,
    scenario: liveStatus?.scenario ?? null,
    simulationTime: liveStatus?.clock?.simulation_time ?? 0,
    label: 'LIVE',
    awaitingRecording: false,
    emptyHint: null,
  }
}
