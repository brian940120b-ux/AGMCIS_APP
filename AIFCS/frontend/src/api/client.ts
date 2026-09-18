/**
 * Thin fetch wrapper for the AIFCS backend.
 *
 * Requests are same-origin: the Vite dev server proxies /api to the backend,
 * and in production the backend serves the built frontend.
 */

import type { ComputeInfo, ConfigSummary, HealthResponse, SystemStatus } from '@/types/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    // Network-level failure: the backend is most likely not running.
    throw new ApiError(
      `Cannot reach the AIFCS backend at ${path}. Is it running on port 8000?`,
    )
  }

  if (!response.ok) {
    throw new ApiError(`${init?.method ?? 'GET'} ${path} failed (${response.status})`, response.status)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),
  systemStatus: () => request<SystemStatus>('/api/system/status'),
  compute: () => request<ComputeInfo>('/api/system/compute'),
  config: () => request<ConfigSummary>('/api/config'),
}
