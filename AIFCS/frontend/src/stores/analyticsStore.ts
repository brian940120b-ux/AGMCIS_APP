/**
 * Analytics store (PHASE 17).
 *
 * Holds the charts for one selected run, and a comparison of several. Nothing
 * is computed here: the backend answers in the shape a chart draws, so the
 * store is a cache and a selection, not a second place where a run gets
 * interpreted.
 */

import { create } from 'zustand'
import { ApiError, api } from '@/api/client'
import type { RunAnalytics, RunComparison } from '@/types/api'

interface AnalyticsState {
  runId: string | null
  analytics: RunAnalytics | null
  comparison: RunComparison | null
  /** Runs ticked for comparison, in the order they were ticked. */
  selected: string[]
  loading: boolean
  error: string | null

  load: (runId: string) => Promise<void>
  toggleSelected: (runId: string) => void
  clearSelection: () => void
  compare: () => Promise<void>
}

export const useAnalyticsStore = create<AnalyticsState>((set, get) => ({
  runId: null,
  analytics: null,
  comparison: null,
  selected: [],
  loading: false,
  error: null,

  load: async (runId) => {
    set({ loading: true, error: null, runId })
    try {
      const analytics = await api.runAnalytics(runId)
      // A slower earlier request must not overwrite a newer selection.
      if (get().runId !== runId) return
      set({ analytics, loading: false })
    } catch (cause) {
      const message = cause instanceof ApiError ? cause.message : 'Could not load analytics'
      set({ error: message, loading: false, analytics: null })
    }
  },

  toggleSelected: (runId) => {
    const selected = get().selected
    set({
      selected: selected.includes(runId)
        ? selected.filter((id) => id !== runId)
        : // Six is the API's limit, and more than that is unreadable anyway.
          [...selected, runId].slice(-6),
      comparison: null,
    })
  },

  clearSelection: () => set({ selected: [], comparison: null }),

  compare: async () => {
    const selected = get().selected
    if (selected.length < 2) {
      set({ error: 'Pick at least two runs to compare.' })
      return
    }
    set({ loading: true, error: null })
    try {
      set({ comparison: await api.compareRuns(selected), loading: false })
    } catch (cause) {
      const message = cause instanceof ApiError ? cause.message : 'Could not compare those runs'
      set({ error: message, loading: false, comparison: null })
    }
  },
}))
