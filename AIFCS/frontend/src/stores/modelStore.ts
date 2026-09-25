/**
 * Model centre store (PHASE 19).
 *
 * Evaluation is a background job on the same runner training uses, so this
 * store asks the training store to watch it rather than polling it separately —
 * there is one job at a time, and two pollers would disagree about which.
 */

import { create } from 'zustand'
import { ApiError, api } from '@/api/client'
import { useTrainingStore } from '@/stores/trainingStore'
import type { ModelComparison, ModelList } from '@/types/api'

interface ModelState {
  list: ModelList | null
  comparison: ModelComparison | null
  selected: string[]
  includeArchived: boolean
  busy: boolean
  error: string | null
  notice: string | null

  refresh: () => Promise<void>
  setIncludeArchived: (include: boolean) => void
  toggleSelected: (modelId: string) => void
  clearSelection: () => void
  compare: () => Promise<void>
  evaluate: (modelId: string, episodes: number) => Promise<void>
  archive: (modelId: string) => Promise<void>
  restore: (modelId: string) => Promise<void>
  remove: (modelId: string) => Promise<void>
}

const message = (cause: unknown, fallback: string) =>
  cause instanceof ApiError ? cause.message : fallback

export const useModelStore = create<ModelState>((set, get) => ({
  list: null,
  comparison: null,
  selected: [],
  includeArchived: false,
  busy: false,
  error: null,
  notice: null,

  refresh: async () => {
    try {
      set({ list: await api.models(get().includeArchived), error: null })
    } catch (cause) {
      set({ error: message(cause, 'Could not read the saved policies') })
    }
  },

  setIncludeArchived: (include) => {
    set({ includeArchived: include })
    void get().refresh()
  },

  toggleSelected: (modelId) => {
    const selected = get().selected
    set({
      selected: selected.includes(modelId)
        ? selected.filter((id) => id !== modelId)
        : [...selected, modelId].slice(-6),
      comparison: null,
    })
  },

  clearSelection: () => set({ selected: [], comparison: null }),

  compare: async () => {
    const selected = get().selected
    if (selected.length < 2) {
      set({ error: 'Pick at least two policies to compare.' })
      return
    }
    set({ busy: true, error: null })
    try {
      set({ comparison: await api.compareModels(selected), busy: false })
    } catch (cause) {
      set({ busy: false, comparison: null, error: message(cause, 'Could not compare those policies') })
    }
  },

  evaluate: async (modelId, episodes) => {
    set({ busy: true, error: null, notice: null })
    try {
      await api.evaluateModel(modelId, episodes)
      set({
        busy: false,
        notice: `Evaluating ${modelId} over ${episodes} episode${episodes === 1 ? '' : 's'}. Watch it above.`,
      })
      // One runner, one watcher: the training panel already follows the job.
      await useTrainingStore.getState().refresh()
      useTrainingStore.getState().startPolling()
    } catch (cause) {
      set({ busy: false, error: message(cause, 'Could not start the evaluation') })
    }
  },

  archive: async (modelId) => {
    set({ busy: true, error: null })
    try {
      await api.archiveModel(modelId)
      set({ busy: false, notice: `${modelId} archived — the file is still on disk.` })
      await get().refresh()
    } catch (cause) {
      set({ busy: false, error: message(cause, 'Could not archive that policy') })
    }
  },

  restore: async (modelId) => {
    set({ busy: true, error: null })
    try {
      await api.restoreModel(modelId)
      set({ busy: false, notice: `${modelId} restored.` })
      await get().refresh()
    } catch (cause) {
      set({ busy: false, error: message(cause, 'Could not restore that policy') })
    }
  },

  remove: async (modelId) => {
    set({ busy: true, error: null })
    try {
      await api.deleteModel(modelId)
      set({
        busy: false,
        notice: `${modelId} deleted.`,
        selected: get().selected.filter((id) => id !== modelId),
      })
      await get().refresh()
    } catch (cause) {
      set({ busy: false, error: message(cause, 'Could not delete that policy') })
    }
  },
}))
