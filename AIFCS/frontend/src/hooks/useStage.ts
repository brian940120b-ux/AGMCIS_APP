/**
 * What the tactical views should draw (PHASE 9).
 *
 * Before replay there was one source of entities: the live telemetry stream.
 * Now there are two, and the viewers must never blend them — a replay frame
 * drawn alongside live positions would be a picture of something that never
 * happened.
 *
 * This hook is the single place that decides which one is on stage, so the 3D
 * view and the 2D plot cannot disagree about what they are showing.
 */

import { useReplayStore } from '@/stores/replayStore'
import { useSimulationStore } from '@/stores/simulationStore'
import type { Entity } from '@/types/api'

export interface Stage {
  mode: 'live' | 'replay'
  entities: Entity[]
  trails: Record<string, [number, number, number][]>
  /** Scenario name for the header, from whichever source is on stage. */
  scenario: string | null
  /** Simulation time of what is being shown, in seconds. */
  simulationTime: number
  /** Short label for the viewer header: "LIVE" or the replay's run id. */
  label: string
  /** True when replay is selected but no recording is open yet. */
  awaitingRecording: boolean
}

export function useStage(): Stage {
  const mode = useReplayStore((s) => s.mode)

  const liveEntities = useSimulationStore((s) => s.entities)
  const liveTrails = useSimulationStore((s) => s.trails)
  const liveStatus = useSimulationStore((s) => s.status)

  const replayEntities = useReplayStore((s) => s.entities)
  const replayTrails = useReplayStore((s) => s.trails)
  const replayStatus = useReplayStore((s) => s.status)

  if (mode === 'replay') {
    return {
      mode,
      entities: replayEntities,
      trails: replayTrails,
      scenario: replayStatus.recording?.scenario ?? null,
      simulationTime: replayStatus.simulation_time ?? 0,
      label: replayStatus.recording?.run_id ?? 'no recording',
      awaitingRecording: !replayStatus.loaded,
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
  }
}
