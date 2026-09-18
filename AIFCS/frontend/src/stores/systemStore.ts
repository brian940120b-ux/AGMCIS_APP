/**
 * System store — the live backend state shared by the whole dashboard.
 *
 * Everything here comes from a real API response. Nothing is mocked: when the
 * backend is unreachable the store holds the error and the UI says so.
 */

import { create } from 'zustand'
import { api, ApiError } from '@/api/client'
import type { ComputeInfo, ConfigSummary, HealthResponse, SystemStatus } from '@/types/api'

export type ConnectionState = 'idle' | 'connecting' | 'online' | 'error'

interface SystemState {
  connection: ConnectionState
  error: string | null
  health: HealthResponse | null
  status: SystemStatus | null
  compute: ComputeInfo | null
  config: ConfigSummary | null
  lastUpdated: number | null
  refresh: () => Promise<void>
}

export const useSystemStore = create<SystemState>((set) => ({
  connection: 'idle',
  error: null,
  health: null,
  status: null,
  compute: null,
  config: null,
  lastUpdated: null,

  refresh: async () => {
    set((state) => ({
      connection: state.connection === 'online' ? 'online' : 'connecting',
      error: null,
    }))
    try {
      const [health, status, compute, config] = await Promise.all([
        api.health(),
        api.systemStatus(),
        api.compute(),
        api.config(),
      ])
      set({
        connection: 'online',
        error: null,
        health,
        status,
        compute,
        config,
        lastUpdated: Date.now(),
      })
    } catch (cause) {
      const message = cause instanceof ApiError ? cause.message : 'Unknown backend error'
      set({ connection: 'error', error: message })
    }
  },
}))
