import { useState } from 'react'
import { inkOnRamp, rampColour } from '@/charts/palette'
import type { ScoreMatrix } from '@/types/api'

/**
 * Score heatmap: every scored unit against every scoring term (PHASE 17).
 *
 * A heatmap rather than six stacked colours, and that is a finding rather than
 * a preference: no six-hue categorical set passes an all-pairs colour-vision
 * check, so six terms cannot each have a hue that a reader can rely on. The
 * question this answers — *where did this unit lose points* — is magnitude, and
 * magnitude belongs on one hue, light to dark.
 *
 * A term that did not apply to a unit is not a zero. A unit with no route
 * cannot be marked down for navigation it was never given, so those cells are
 * drawn as absent rather than as an empty score.
 */

interface Props {
  matrix: ScoreMatrix
}

export function ScoreHeatmap({ matrix }: Props) {
  const [hover, setHover] = useState<{ entity: string; term: string } | null>(null)

  if (!matrix.available) {
    return <p className="px-3 py-4 text-[11px] text-ink-faint">{matrix.detail}</p>
  }

  const cell = hover ? matrix.cells[hover.entity]?.[hover.term] : null

  return (
    <div className="px-3 py-2">
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-0.5 text-[10px]">
          <caption className="sr-only">
            Points earned by each unit for each scoring term, out of {matrix.available_points} available
          </caption>
          <thead>
            <tr>
              <th scope="col" className="hud-label px-1 text-left font-normal">
                unit
              </th>
              {matrix.terms.map((term) => (
                <th key={term} scope="col" className="hud-label px-1 text-center font-normal">
                  {term.slice(0, 4)}
                </th>
              ))}
              <th scope="col" className="hud-label px-1 text-right font-normal">
                total
              </th>
            </tr>
          </thead>
          <tbody>
            {matrix.entities.map((entity) => (
              <tr key={entity}>
                <th scope="row" className="px-1 py-0.5 text-left font-normal whitespace-nowrap text-ink-dim">
                  {entity}
                </th>
                {matrix.terms.map((term) => {
                  const data = matrix.cells[entity]?.[term]
                  const applicable = (data?.applicable ?? 1) > 0
                  const fraction = data?.fraction ?? 0
                  return (
                    <td
                      key={term}
                      className="px-0 py-0"
                      onMouseEnter={() => setHover({ entity, term })}
                      onMouseLeave={() => setHover(null)}
                    >
                      <div
                        className="grid h-6 min-w-10 place-items-center tabular-nums"
                        style={
                          applicable
                            ? { backgroundColor: rampColour(fraction), color: inkOnRamp(fraction) }
                            : { backgroundColor: 'var(--color-grid)', color: 'var(--color-ink-faint)' }
                        }
                        title={
                          applicable
                            ? `${entity} ${term}: ${data?.points ?? 0} of ${data?.weight ?? 0} points`
                            : `${entity} ${term}: did not apply to this unit`
                        }
                      >
                        {applicable ? (data?.points ?? 0).toFixed(0) : '—'}
                      </div>
                    </td>
                  )
                })}
                <td className="px-1 text-right tabular-nums text-ink">
                  {(matrix.totals[entity] ?? 0).toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* The ramp is the legend: one hue, low to high. */}
      <div className="mt-2 flex items-center gap-2">
        <span className="text-[9px] text-ink-faint">0</span>
        <div className="flex h-1.5 flex-1 gap-px">
          {[0.1, 0.3, 0.5, 0.7, 0.9].map((f) => (
            <span key={f} className="flex-1" style={{ backgroundColor: rampColour(f) }} />
          ))}
        </div>
        <span className="text-[9px] text-ink-faint">full marks</span>
      </div>

      <p className="mt-1 text-[10px] text-ink-faint">
        {cell
          ? `${hover?.entity} · ${hover?.term}: ${cell.points} of ${cell.weight} points`
          : `${matrix.available_points} points available per unit · — means the term did not apply`}
      </p>
    </div>
  )
}
