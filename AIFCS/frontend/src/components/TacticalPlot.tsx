import { useSimulationStore } from '@/stores/simulationStore'
import { useSystemStore } from '@/stores/systemStore'
import type { Entity } from '@/types/api'

/**
 * Top-down tactical plot (PHASE 1).
 *
 * A 2D projection of the real truth state onto the X/Y plane, drawn from the
 * positions the engine actually reports. The full 3D viewer arrives in PHASE 8;
 * this is not a stand-in for it and does not pretend to be.
 */

const TEAM_COLOR: Record<Entity['team'], string> = {
  BLUE: 'var(--color-blue-force)',
  RED: 'var(--color-red-force)',
  NEUTRAL: 'var(--color-ink-faint)',
}

const VIEW = 1000 // SVG user-space extent; world metres are mapped into this.

export function TacticalPlot() {
  const entities = useSimulationStore((s) => s.entities)
  const status = useSimulationStore((s) => s.status)
  const bounds = useSystemStore((s) => s.config?.world.bounds)

  /*
   * Fit the view to the units rather than to the whole 200 km world box —
   * otherwise four aircraft a few km apart render as one indistinguishable dot.
   * Falls back to the configured world bounds when nothing is loaded.
   */
  const extent = (() => {
    if (entities.length === 0) {
      return bounds
        ? { minX: bounds.x_min, maxX: bounds.x_max, minY: bounds.y_min, maxY: bounds.y_max }
        : { minX: -1000, maxX: 1000, minY: -1000, maxY: 1000 }
    }

    const xs = entities.map((e) => e.position[0])
    const ys = entities.map((e) => e.position[1])
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...ys)
    const maxY = Math.max(...ys)

    // Square the view so the plot never distorts distances, and pad the edges
    // so labels stay inside the panel.
    const span = Math.max(maxX - minX, maxY - minY, 2000) * 1.45
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2
    return { minX: cx - span / 2, maxX: cx + span / 2, minY: cy - span / 2, maxY: cy + span / 2 }
  })()

  const spanMetres = extent.maxX - extent.minX

  // Map world metres to SVG coordinates. +Y is north, so the axis is flipped.
  const toView = (x: number, y: number): [number, number] => {
    const nx = (x - extent.minX) / (extent.maxX - extent.minX)
    const ny = (y - extent.minY) / (extent.maxY - extent.minY)
    return [nx * VIEW, (1 - ny) * VIEW]
  }

  const gridLines = Array.from({ length: 9 }, (_, i) => ((i + 1) * VIEW) / 10)

  /*
   * Units in formation can sit a few hundred metres apart, which is well under
   * one pixel at this zoom. Stack their labels instead of letting them overlap
   * into an unreadable smear.
   */
  const LABEL_HEIGHT = 44
  const placed = entities.map((entity, index) => {
    const [cx, cy] = toView(entity.position[0], entity.position[1])
    const leaderSeconds = spanMetres / 8000
    const [vx, vy] = toView(
      entity.position[0] + entity.velocity[0] * leaderSeconds,
      entity.position[1] + entity.velocity[1] * leaderSeconds,
    )

    const crowding = entities
      .slice(0, index)
      .filter((other) => {
        const [ox, oy] = toView(other.position[0], other.position[1])
        return Math.hypot(ox - cx, oy - cy) < LABEL_HEIGHT
      }).length

    // Nose direction from yaw, drawn separately from the velocity leader: with
    // real aerodynamics the two differ whenever the unit is sideslipping.
    const yaw = entity.orientation[2]
    const noseLength = VIEW * 0.035
    const nx = cx + Math.sin(yaw) * noseLength
    const ny = cy - Math.cos(yaw) * noseLength

    return { entity, cx, cy, vx, vy, nx, ny, labelOffset: crowding * LABEL_HEIGHT }
  })

  return (
    <div className="hud-panel relative flex h-full min-h-[320px] flex-col overflow-hidden">
      <header className="flex shrink-0 items-center justify-between border-b border-edge px-3 py-2">
        <div>
          <h2 className="hud-label text-ink-dim">Tactical Plot</h2>
          <p className="text-[10px] text-ink-faint">
            Top-down X/Y · thin line = nose, thick = velocity · 3D in PHASE 8
          </p>
        </div>
        <span className="text-[10px] text-ink-faint">
          {status?.scenario ?? 'no scenario'} · {entities.length} units ·{' '}
          {(spanMetres / 1000).toFixed(0)} km across
        </span>
      </header>

      <div className="relative min-h-0 flex-1">
        <svg
          viewBox={`0 0 ${VIEW} ${VIEW}`}
          className="size-full"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Top-down plot of simulated flight units"
        >
          {/* Grid */}
          <g stroke="var(--color-grid)" strokeWidth={1}>
            {gridLines.map((pos) => (
              <line key={`v${pos}`} x1={pos} y1={0} x2={pos} y2={VIEW} />
            ))}
            {gridLines.map((pos) => (
              <line key={`h${pos}`} x1={0} y1={pos} x2={VIEW} y2={pos} />
            ))}
          </g>

          {/* Datum axes through the scenario origin */}
          <g stroke="var(--color-edge)" strokeWidth={1.5}>
            <line x1={VIEW / 2} y1={0} x2={VIEW / 2} y2={VIEW} />
            <line x1={0} y1={VIEW / 2} x2={VIEW} y2={VIEW / 2} />
          </g>

          {placed.map(({ entity, cx, cy, vx, vy, nx, ny, labelOffset }) => {
            const color = TEAM_COLOR[entity.team]
            const inactive = entity.status !== 'ACTIVE'
            const labelY = cy - 10 + labelOffset

            return (
              <g key={entity.id} opacity={inactive ? 0.4 : 1}>
                <line x1={cx} y1={cy} x2={vx} y2={vy} stroke={color} strokeWidth={2} opacity={0.55} />
                <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={color} strokeWidth={1} opacity={0.9} />
                <circle cx={cx} cy={cy} r={16} fill="none" stroke={color} strokeWidth={1} opacity={0.35} />
                <circle cx={cx} cy={cy} r={6} fill={color} />
                {/* Leader line to the label, so stacked units stay readable. */}
                {labelOffset !== 0 && (
                  <line x1={cx} y1={cy} x2={cx + 20} y2={labelY - 5} stroke={color} strokeWidth={0.75} opacity={0.4} />
                )}
                <text x={cx + 24} y={labelY} fill={color} fontSize={19} fontFamily="var(--font-mono)">
                  {entity.id}
                </text>
                <text x={cx + 24} y={labelY + 20} fill="var(--color-ink-faint)" fontSize={15}>
                  {Math.round(entity.altitude)}m · {Math.round(entity.speed)}m/s ·{' '}
                  {(entity.orientation[1] * (180 / Math.PI)).toFixed(0)}°
                </text>
              </g>
            )
          })}
        </svg>

        {entities.length === 0 && (
          <div className="absolute inset-0 grid place-items-center">
            <p className="text-[11px] text-ink-faint">
              No entities. Press START to load a scenario and run the simulation.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
