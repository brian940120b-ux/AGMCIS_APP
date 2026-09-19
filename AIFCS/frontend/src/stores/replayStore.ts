/**
 * Replay store (PHASE 9).
 *
 * The playback cursor lives on the server, not here. Every control calls the
 * backend and stores what it returns, so the transport bar always shows where
 * the player actually is rather than where the browser hoped it would be.
 *
 * While playing, the store polls for the current frame. That is deliberate: a
 * recording is being read back, not streamed live, and a poll the UI controls
 * is simpler to reason about than a second WebSocket carrying a second kind of
 * truth.
 */

import { create } from 'zustand'
import { api, ApiError } from '@/api/client'
import type {
  Entity,
  RecordingSummary,
  ReplayEventMarker,
  ReplayFrame,
  ReplayStatus,
  RunDetail,
  RunSummary,
  ScoringWeights,
} from '@/types/api'

/** Matches the live view's trail length, so the two look the same. */
const TRAIL_LIMIT = 300
/** Frame poll interval while playing, in ms. The recorder writes at 20 Hz. */
const POLL_MS = 100

type Mode = 'live' | 'replay'

interface ReplayState {
  mode: Mode
  recordings: RecordingSummary[]
  recordingsLoaded: boolean
  status: ReplayStatus
  frame: ReplayFrame | null
  entities: Entity[]
  trails: Record<string, [number, number, number][]>
  markers: ReplayEventMarker[]
  runs: RunSummary[]
  selectedRun: RunDetail | null
  weights: ScoringWeights | null
  busy: boolean
  error: string | null
  notice: string | null

  setMode: (mode: Mode) => void
  loadRecordings: () => Promise<void>
  loadRuns: () => Promise<void>
  selectRun: (runId: string | null) => Promise<void>
  loadWeights: () => Promise<void>
  open: (runId: string) => Promise<void>
  close: () => Promise<void>
  play: () => Promise<void>
  pause: () => Promise<void>
  step: (frames: number) => Promise<void>
  seek: (frame: number) => Promise<void>
  setSpeed: (speed: number) => Promise<void>
  jump: (direction: number) => Promise<void>
  rescore: (runId: string) => Promise<void>
  remove: (runId: string) => Promise<void>
  poll: () => Promise<void>
  startPolling: () => void
  stopPolling: () => void
}

const message = (cause: unknown) =>
  cause instanceof ApiError ? cause.message : 'Unexpected error talking to the backend'

const EMPTY_STATUS: ReplayStatus = { loaded: false, playing: false }

export const useReplayStore = create<ReplayState>((set, get) => {
  let timer: ReturnType<typeof setInterval> | null = null

  /** Run a transport command and store the status the server reports back. */
  const transport = async (action: () => Promise<ReplayStatus>) => {
    set({ busy: true, error: null })
    try {
      set({ status: await action() })
      await get().poll()
    } catch (cause) {
      set({ error: message(cause) })
    } finally {
      set({ busy: false })
    }
  }

  return {
    mode: 'live',
    recordings: [],
    recordingsLoaded: false,
    status: EMPTY_STATUS,
    frame: null,
    entities: [],
    trails: {},
    markers: [],
    runs: [],
    selectedRun: null,
    weights: null,
    busy: false,
    error: null,
    notice: null,

    setMode: (mode) => {
      set({ mode, error: null, notice: null })
      if (mode === 'replay') {
        void get().loadRecordings()
        void get().loadRuns()
      } else {
        get().stopPolling()
      }
    },

    loadRecordings: async () => {
      try {
        const response = await api.recordings()
        set({
          recordings: response.recordings,
          recordingsLoaded: true,
          notice: response.enabled ? null : 'Recording is disabled in configs/analysis.yaml',
        })
      } catch (cause) {
        set({ error: message(cause), recordingsLoaded: true })
      }
    },

    loadRuns: async () => {
      try {
        set({ runs: (await api.runs()).runs })
      } catch (cause) {
        set({ error: message(cause) })
      }
    },

    selectRun: async (runId) => {
      if (runId === null) {
        set({ selectedRun: null })
        return
      }
      try {
        set({ selectedRun: await api.run(runId) })
      } catch (cause) {
        set({ error: message(cause) })
      }
    },

    loadWeights: async () => {
      try {
        set({ weights: await api.scoringWeights() })
      } catch (cause) {
        set({ error: message(cause) })
      }
    },

    open: async (runId) => {
      set({ busy: true, error: null, trails: {}, frame: null, entities: [] })
      try {
        const status = await api.replayLoad(runId)
        const markers = await api.replayEvents()
        set({ status, markers: markers.events })
        await get().poll()
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    close: async () => {
      get().stopPolling()
      try {
        await api.replayUnload()
      } catch (cause) {
        set({ error: message(cause) })
      }
      set({ status: EMPTY_STATUS, frame: null, entities: [], trails: {}, markers: [] })
    },

    play: async () => {
      await transport(() => api.replayPlay())
      get().startPolling()
    },

    pause: async () => {
      await transport(() => api.replayPause())
      get().stopPolling()
    },

    // Seeking anywhere clears the trail: the frames it was built from are no
    // longer the ones leading up to the cursor, and a stale trail would be a
    // picture of something that did not happen.
    step: async (frames) => {
      get().stopPolling()
      set({ trails: {} })
      await transport(() => api.replayStep(frames))
    },

    seek: async (frame) => {
      get().stopPolling()
      set({ trails: {} })
      await transport(() => api.replaySeek(frame))
    },

    setSpeed: async (speed) => {
      await transport(() => api.replaySpeed(speed))
    },

    jump: async (direction) => {
      get().stopPolling()
      set({ trails: {} })
      await transport(async () => {
        const response = await api.replayJump(direction)
        set({
          notice: response.jumped_to
            ? `${response.jumped_to.type} at ${response.jumped_to.simulation_time.toFixed(1)}s`
            : 'No further events in that direction',
        })
        return response
      })
    },

    rescore: async (runId) => {
      set({ busy: true, error: null })
      try {
        const result = await api.scoreRun(runId)
        set({ notice: `Rescored under ruler ${result.weights_hash}` })
        await get().loadRuns()
        await get().selectRun(runId)
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    remove: async (runId) => {
      set({ busy: true, error: null })
      try {
        if (get().status.recording?.run_id === runId) await get().close()
        await api.deleteRun(runId)
        set({ notice: `Deleted run ${runId}`, selectedRun: null })
        await get().loadRuns()
        await get().loadRecordings()
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    poll: async () => {
      if (!get().status.loaded) return
      try {
        const { status, frame } = await api.replayFrame()
        set((state) => {
          if (!frame) return { status }
          const trails: Record<string, [number, number, number][]> = {}
          for (const entity of frame.entities) {
            const previous = state.trails[entity.id] ?? []
            trails[entity.id] = [...previous, entity.position].slice(-TRAIL_LIMIT)
          }
          return { status, frame, entities: frame.entities, trails }
        })
        // The server stops at the last frame on its own; stop polling with it.
        if (!status.playing) get().stopPolling()
      } catch (cause) {
        set({ error: message(cause) })
        get().stopPolling()
      }
    },

    startPolling: () => {
      if (timer !== null) return
      timer = setInterval(() => void get().poll(), POLL_MS)
    },

    stopPolling: () => {
      if (timer === null) return
      clearInterval(timer)
      timer = null
    },
  }
})
