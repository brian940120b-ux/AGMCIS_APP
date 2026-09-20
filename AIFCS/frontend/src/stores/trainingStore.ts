/**
 * Training centre store (PHASE 18).
 *
 * A job runs in the server, not in the page, so this store is a view onto it:
 * it polls while something is running and stops when nothing is. A reload
 * rejoins the job that is already going, which is one of the three things the
 * dashboard had to be able to do before it was allowed a START button.
 */

import { create } from 'zustand'
import { ApiError, api } from '@/api/client'
import type { StartTrainingRequest, TrainingJobs } from '@/types/api'

/** Fast enough to feel live, slow enough not to compete with the training. */
const POLL_MS = 2000

interface TrainingState {
  jobs: TrainingJobs | null
  loading: boolean
  error: string | null
  polling: boolean

  refresh: () => Promise<void>
  start: (request: StartTrainingRequest) => Promise<void>
  stop: () => Promise<void>
  startPolling: () => void
  stopPolling: () => void
}

let timer: ReturnType<typeof setInterval> | null = null

export const useTrainingStore = create<TrainingState>((set, get) => ({
  jobs: null,
  loading: false,
  error: null,
  polling: false,

  refresh: async () => {
    try {
      const jobs = await api.trainingJobs()
      set({ jobs, error: null })
      // Stop polling once there is nothing to watch, rather than asking a
      // finished job how it is getting on for ever.
      if (!jobs.busy) get().stopPolling()
    } catch (cause) {
      set({ error: cause instanceof ApiError ? cause.message : 'Could not read training jobs' })
    }
  },

  start: async (request) => {
    set({ loading: true, error: null })
    try {
      await api.startTraining(request)
      set({ loading: false })
      await get().refresh()
      get().startPolling()
    } catch (cause) {
      // The backend's refusals explain themselves; show what it said.
      set({
        loading: false,
        error: cause instanceof ApiError ? cause.message : 'Could not start training',
      })
    }
  },

  stop: async () => {
    set({ loading: true, error: null })
    try {
      await api.stopTraining()
      set({ loading: false })
      await get().refresh()
    } catch (cause) {
      set({
        loading: false,
        error: cause instanceof ApiError ? cause.message : 'Could not stop training',
      })
    }
  },

  startPolling: () => {
    if (timer !== null) return
    set({ polling: true })
    timer = setInterval(() => void get().refresh(), POLL_MS)
  },

  stopPolling: () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
    set({ polling: false })
  },
}))
