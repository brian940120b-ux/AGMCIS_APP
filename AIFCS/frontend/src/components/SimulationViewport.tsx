import { Radar } from 'lucide-react'
import { useSystemStore } from '@/stores/systemStore'

/**
 * Centre stage of the Command Center.
 *
 * The real-time 3D viewer (Three.js) lands in PHASE 8. Until then this shows
 * the resolved world configuration and states plainly that the renderer is not
 * implemented — it does not fake a simulation.
 */
export function SimulationViewport() {
  const config = useSystemStore((s) => s.config)
  const bounds = config?.world.bounds

  return (
    <div className="hud-panel bg-tactical-grid relative flex h-full min-h-[280px] items-center justify-center overflow-hidden">
      {/* Decorative scanline; render-layer only, never coupled to simulation state. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(to_right,transparent,color-mix(in_srgb,var(--color-cyan-hud)_55%,transparent),transparent)] animate-scanline" />

      <div className="relative z-10 flex flex-col items-center gap-4 px-6 text-center">
        <div className="relative grid size-24 place-items-center rounded-full border border-cyan-hud/25">
          <div className="absolute inset-0 origin-center rounded-full animate-radar-sweep bg-[conic-gradient(from_0deg,color-mix(in_srgb,var(--color-cyan-hud)_28%,transparent),transparent_38%)]" />
          <Radar className="size-7 text-cyan-hud/70" strokeWidth={1.25} />
        </div>

        <div>
          <p className="hud-label">3D Simulation Viewer</p>
          <p className="mt-1 text-sm tracking-[0.2em] text-amber-hud">NOT IMPLEMENTED</p>
          <p className="mt-2 max-w-md text-[11px] leading-relaxed text-ink-faint">
            The Three.js tactical view is scheduled for PHASE 8, after the simulation engine
            (PHASE 1) and WebSocket telemetry (PHASE 7). No placeholder entities are rendered.
          </p>
        </div>

        {bounds && (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[10px] text-ink-faint sm:grid-cols-3">
            <div>
              <dt className="hud-label">X Range</dt>
              <dd className="text-ink-dim">
                {(bounds.x_min / 1000).toFixed(0)} … {(bounds.x_max / 1000).toFixed(0)} km
              </dd>
            </div>
            <div>
              <dt className="hud-label">Y Range</dt>
              <dd className="text-ink-dim">
                {(bounds.y_min / 1000).toFixed(0)} … {(bounds.y_max / 1000).toFixed(0)} km
              </dd>
            </div>
            <div>
              <dt className="hud-label">Altitude</dt>
              <dd className="text-ink-dim">
                {bounds.altitude_min.toFixed(0)} … {(bounds.altitude_max / 1000).toFixed(0)} km
              </dd>
            </div>
          </dl>
        )}
      </div>
    </div>
  )
}
