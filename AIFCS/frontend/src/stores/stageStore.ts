/**
 * What the tactical views are showing (PHASE 10).
 *
 * Five stages, and only ever one at a time:
 *
 *   live       the telemetry stream from a running simulation
 *   replay     a recording being read back
 *   edit       the starting positions of the scenario being edited
 *   analytics  charts of a finished run (PHASE 17) — no tactical view at all,
 *              because it is about runs rather than a run
 *   training   starting and watching a training job (PHASE 18)
 *
 * Kept in its own store because it is not a replay concern, nor an editor
 * concern — it is the question "which of these is on stage", and exactly one
 * module should own it.
 */

import { create } from 'zustand'
import { useReplayStore } from '@/stores/replayStore'
import { useScenarioStore } from '@/stores/scenarioStore'

export type StageMode = 'live' | 'replay' | 'edit' | 'analytics' | 'training'

interface StageState {
  mode: StageMode
  setMode: (mode: StageMode) => void
}

export const useStageStore = create<StageState>((set) => ({
  mode: 'live',

  setMode: (mode) => {
    set({ mode })

    // Leaving replay must stop the frame poll; nothing else should keep
    // running in a mode the operator has left.
    if (mode !== 'replay') useReplayStore.getState().stopPolling()

    if (mode === 'replay') {
      void useReplayStore.getState().loadRecordings()
      void useReplayStore.getState().loadRuns()
    }
    if (mode === 'edit') {
      void useScenarioStore.getState().refresh()
    }
    if (mode === 'analytics') {
      void useReplayStore.getState().loadRuns()
    }
  },
}))
