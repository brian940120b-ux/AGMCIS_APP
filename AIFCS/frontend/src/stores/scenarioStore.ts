/**
 * Scenario editor store (PHASE 10).
 *
 * The draft lives here as the YAML document shape, not as a view model, so
 * what the editor holds is exactly what gets written to disk. Validation is
 * the backend's — the same parser the simulation uses — so the editor can
 * never accept something the engine would then refuse.
 */

import { create } from 'zustand'
import { api, ApiError } from '@/api/client'
import type {
  ScenarioDocument,
  ScenarioListEntry,
  ScenarioValidation,
} from '@/types/api'

interface ScenarioState {
  catalogue: ScenarioListEntry[]
  defaultName: string
  loaded: boolean

  /** The scenario currently open, and the draft being edited. */
  openName: string | null
  draft: ScenarioDocument | null
  /** Set when the open scenario exists on disk; false for a brand new one. */
  existsOnDisk: boolean
  dirty: boolean

  validation: ScenarioValidation | null
  yamlText: string
  busy: boolean
  error: string | null
  notice: string | null

  refresh: () => Promise<void>
  open: (name: string) => Promise<void>
  startNew: () => Promise<void>
  close: () => void
  edit: (mutate: (draft: ScenarioDocument) => ScenarioDocument) => void
  validate: () => Promise<void>
  save: () => Promise<void>
  clone: (newName: string) => Promise<void>
  remove: (name: string) => Promise<void>
  exportYaml: (name: string) => Promise<void>
  importYaml: (text: string, name?: string) => Promise<void>
  setYamlText: (text: string) => void
}

const message = (cause: unknown) =>
  cause instanceof ApiError ? cause.message : 'Unexpected error talking to the backend'

export const useScenarioStore = create<ScenarioState>((set, get) => {
  /** Validate the current draft and store the verdict. */
  const check = async (draft: ScenarioDocument | null) => {
    if (!draft) return
    try {
      set({ validation: await api.scenarioValidate(draft) })
    } catch (cause) {
      set({ validation: { valid: false, error: message(cause) } })
    }
  }

  return {
    catalogue: [],
    defaultName: '',
    loaded: false,
    openName: null,
    draft: null,
    existsOnDisk: false,
    dirty: false,
    validation: null,
    yamlText: '',
    busy: false,
    error: null,
    notice: null,

    refresh: async () => {
      try {
        const body = await api.scenarioCatalogue()
        set({ catalogue: body.scenarios, defaultName: body.default, loaded: true, error: null })
      } catch (cause) {
        set({ error: message(cause), loaded: true })
      }
    },

    open: async (name) => {
      set({ busy: true, error: null, notice: null })
      try {
        const scenario = await api.scenario(name)
        const draft = scenario.document ?? null
        set({
          openName: name,
          draft,
          existsOnDisk: true,
          dirty: false,
          validation: draft ? { valid: true, scenario } : null,
        })
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    // The template comes from the backend rather than being written here, so
    // the editor's starting point cannot drift from what the validator accepts.
    startNew: async () => {
      set({ busy: true, error: null, notice: null })
      try {
        const { document } = await api.scenarioTemplate()
        set({ openName: document.scenario.name, draft: document, existsOnDisk: false, dirty: true })
        await check(document)
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    close: () =>
      set({
        openName: null,
        draft: null,
        existsOnDisk: false,
        dirty: false,
        validation: null,
        error: null,
        notice: null,
      }),

    edit: (mutate) => {
      const current = get().draft
      if (!current) return
      const next = mutate(structuredClone(current))
      set({ draft: next, dirty: true, notice: null })
      void check(next)
    },

    validate: async () => {
      await check(get().draft)
    },

    save: async () => {
      const { draft, openName, existsOnDisk } = get()
      if (!draft) return
      set({ busy: true, error: null, notice: null })
      try {
        // A rename is a new file: save under the draft's own name rather than
        // silently writing the new content into the old scenario.
        const renamed = existsOnDisk && openName !== null && draft.scenario.name !== openName
        const saved =
          existsOnDisk && !renamed
            ? await api.scenarioUpdate(openName as string, draft)
            : await api.scenarioCreate(draft)

        set({
          openName: saved.name,
          existsOnDisk: true,
          dirty: false,
          notice: renamed ? `Saved as a new scenario: ${saved.name}` : `Saved ${saved.name}`,
        })
        await get().refresh()
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    clone: async (newName) => {
      const name = get().openName
      if (!name) return
      set({ busy: true, error: null, notice: null })
      try {
        const copy = await api.scenarioClone(name, newName)
        set({ notice: `Cloned to ${copy.name}` })
        await get().refresh()
        await get().open(copy.name)
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    remove: async (name) => {
      set({ busy: true, error: null, notice: null })
      try {
        await api.scenarioDelete(name)
        set({ notice: `Deleted ${name}` })
        if (get().openName === name) get().close()
        await get().refresh()
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    exportYaml: async (name) => {
      set({ busy: true, error: null })
      try {
        const body = await api.scenarioExport(name)
        set({ yamlText: body.yaml, notice: `Exported ${name}` })
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    importYaml: async (text, name) => {
      set({ busy: true, error: null, notice: null })
      try {
        const imported = await api.scenarioImport(text, name)
        set({ notice: `Imported ${imported.name}` })
        await get().refresh()
        await get().open(imported.name)
      } catch (cause) {
        set({ error: message(cause) })
      } finally {
        set({ busy: false })
      }
    },

    setYamlText: (yamlText) => set({ yamlText }),
  }
})
