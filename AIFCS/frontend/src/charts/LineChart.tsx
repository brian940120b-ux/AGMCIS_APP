import { useMemo, useRef, useState } from 'react'
import { groupColour } from '@/charts/palette'
import type { ChartSeries } from '@/types/api'

/**
 * Multi-series line chart (PHASE 17).
 *
 * Series are coloured by *group* — team — rather than one hue each: eight units
 * would need eight categorical hues, and no such set is distinguishable under
 * colour-vision deficiency. Identity comes from a direct label at the end of
 * each line, with the legend as the dependable channel behind it.
 *
 * Every value drawn here came from the backend's aggregation of what a run
 * stored. Nothing is interpolated or smoothed: a gap in the samples is a gap.
 */

interface Props {
  series: ChartSeries[]
  yLabel: string
  unit: string
  /** Force the y axis to start at zero — right for counts, wrong for altitude. */
  zeroBased?: boolean
  height?: number
}

const PAD = { top: 12, right: 64, bottom: 24, left: 46 }

function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return [min]
  const raw = (max - min) / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude * 10
  const first = Math.ceil(min / step) * step
  const ticks: number[] = []
  for (let v = first; v <= max + step * 0.001; v += step) ticks.push(Number(v.toFixed(6)))
  return ticks
}

const format = (value: number) =>
  Math.abs(value) >= 1000 ? value.toLocaleString('en-US', { maximumFractionDigits: 0 }) : String(Number(value.toFixed(1)))

export function LineChart({ series, yLabel, unit, zeroBased = false, height = 200 }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [hoverX, setHoverX] = useState<number | null>(null)
  const width = 640

  const bounds = useMemo(() => {
    const xs: number[] = []
    const ys: number[] = []
    for (const s of series) {
      for (const [x, y] of s.points) {
        xs.push(x)
        ys.push(y)
      }
    }
    if (xs.length === 0) return null
    const yMin = zeroBased ? 0 : Math.min(...ys)
    const yMax = Math.max(...ys)
    // A flat series would otherwise divide by zero; give it room to sit in.
    const span = yMax - yMin || Math.max(1, Math.abs(yMax) * 0.1)
    return { xMin: Math.min(...xs), xMax: Math.max(...xs), yMin, yMax: yMin + span }
  }, [series, zeroBased])

  if (!bounds) return null

  const plotW = width - PAD.left - PAD.right
  const plotH = height - PAD.top - PAD.bottom
  const sx = (x: number) =>
    PAD.left + (bounds.xMax === bounds.xMin ? plotW / 2 : ((x - bounds.xMin) / (bounds.xMax - bounds.xMin)) * plotW)
  const sy = (y: number) => PAD.top + plotH - ((y - bounds.yMin) / (bounds.yMax - bounds.yMin)) * plotH

  const yTicks = niceTicks(bounds.yMin, bounds.yMax)
  const xTicks = niceTicks(bounds.xMin, bounds.xMax, 5)

  // End labels ride the line ends. Sorted by where they land on screen, not by
  // value: the y axis is inverted, so ordering by value walks the labels up the
  // chart while the nudge below pushes them down, and they collide anyway.
  const ends = series
    .map((s) => {
      const last = s.points[s.points.length - 1]
      return last ? { key: s.key, label: s.label, group: s.group, screenY: sy(last[1]) } : null
    })
    .filter((e): e is NonNullable<typeof e> => e !== null)
    .sort((a, b) => a.screenY - b.screenY)

  // Nudge collisions downwards, keeping the order they appear in.
  const placed: { key: string; label: string; group: string; y: number }[] = []
  for (const end of ends) {
    const previous = placed[placed.length - 1]
    const y = previous && end.screenY - previous.y < 10 ? previous.y + 10 : end.screenY
    placed.push({ key: end.key, label: end.label, group: end.group, y })
  }

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    const localX = ((event.clientX - rect.left) / rect.width) * width
    if (localX < PAD.left || localX > width - PAD.right) {
      setHoverX(null)
      return
    }
    setHoverX(bounds.xMin + ((localX - PAD.left) / plotW) * (bounds.xMax - bounds.xMin))
  }

  const readings =
    hoverX === null
      ? []
      : series
          .map((s) => {
            let nearest = s.points[0]
            for (const point of s.points) {
              if (Math.abs(point[0] - hoverX) < Math.abs(nearest[0] - hoverX)) nearest = point
            }
            return nearest ? { key: s.key, label: s.label, group: s.group, value: nearest[1], at: nearest[0] } : null
          })
          .filter((r): r is NonNullable<typeof r> => r !== null)
          .sort((a, b) => b.value - a.value)

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={`${yLabel} against simulation time for ${series.length} units`}
        onMouseMove={onMove}
        onMouseLeave={() => setHoverX(null)}
      >
        {/* Gridlines: hairline, solid, one step off the surface. */}
        {yTicks.map((tick) => (
          <g key={`y${tick}`}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={sy(tick)}
              y2={sy(tick)}
              stroke="var(--color-edge)"
              strokeWidth={1}
            />
            <text x={PAD.left - 6} y={sy(tick) + 3} textAnchor="end" className="fill-ink-faint text-[9px]">
              {format(tick)}
            </text>
          </g>
        ))}
        {xTicks.map((tick) => (
          <text
            key={`x${tick}`}
            x={sx(tick)}
            y={height - 8}
            textAnchor="middle"
            className="fill-ink-faint text-[9px]"
          >
            {format(tick)}s
          </text>
        ))}

        {hoverX !== null && (
          <line
            x1={sx(hoverX)}
            x2={sx(hoverX)}
            y1={PAD.top}
            y2={PAD.top + plotH}
            stroke="var(--color-ink-faint)"
            strokeWidth={1}
          />
        )}

        {series.map((s) =>
          // One sample is a real measurement, and a polyline through a single
          // point draws nothing at all. Give it a marker instead.
          s.points.length === 1 ? (
            <circle
              key={s.key}
              cx={sx(s.points[0][0])}
              cy={sy(s.points[0][1])}
              r={4}
              fill={groupColour(s.group)}
              stroke="var(--color-panel)"
              strokeWidth={2}
            />
          ) : (
            <polyline
              key={s.key}
              points={s.points.map(([x, y]) => `${sx(x)},${sy(y)}`).join(' ')}
              fill="none"
              stroke={groupColour(s.group)}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ),
        )}

        {/* Direct labels ride the line ends; text wears ink, never the series colour. */}
        {placed.map((end) => (
          <text
            key={end.key}
            x={width - PAD.right + 6}
            y={end.y + 3}
            className="fill-ink-dim text-[9px]"
          >
            {end.label}
          </text>
        ))}

        <text
          x={PAD.left - 6}
          y={PAD.top - 3}
          textAnchor="end"
          className="fill-ink-faint text-[9px]"
        >
          {unit}
        </text>
      </svg>

      {hoverX !== null && readings.length > 0 && (
        <div
          className={`pointer-events-none absolute top-1 border border-edge bg-deck/95 px-2 py-1 ${
            // Sit on the opposite side to the cursor, so the tooltip never
            // covers the line ends it is describing.
            sx(hoverX) > width / 2 ? 'left-12' : 'right-16'
          }`}
        >
          <p className="text-[9px] text-ink-faint">t+{readings[0].at.toFixed(1)}s</p>
          {readings.map((r) => (
            <p key={r.key} className="flex items-baseline gap-1.5 text-[10px] whitespace-nowrap">
              <span
                className="inline-block size-1.5 shrink-0"
                style={{ backgroundColor: groupColour(r.group) }}
                aria-hidden
              />
              <span className="text-ink-dim">{r.label}</span>
              <span className="ml-auto tabular-nums text-ink">
                {format(r.value)} {unit}
              </span>
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
