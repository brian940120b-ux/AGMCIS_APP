import { behaviourColour } from '@/charts/palette'
import type { BehaviourShares } from '@/types/api'

/**
 * How each unit spent its decisions (PHASE 17).
 *
 * A horizontal stacked bar: the job is part-to-whole, and each unit's decisions
 * sum to all of them. Five behaviours is inside the categorical cap, and the
 * palette was validated in exactly this stacking order — the colour-vision
 * check is on adjacent pairs, so reordering the stack would void it.
 *
 * Segments are separated by a 2px gap in the surface colour rather than by a
 * stroke, so nothing but data carries ink. A label goes inside a segment only
 * when it fits; the rest is carried by the legend and the title attribute.
 */

interface Props {
  shares: BehaviourShares
}

export function BehaviourBars({ shares }: Props) {
  if (!shares.available) {
    return <p className="px-3 py-4 text-[11px] text-ink-faint">{shares.detail}</p>
  }

  return (
    <div className="px-3 py-2">
      {/* Legend first: identity never rests on colour alone. */}
      <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1">
        {shares.behaviours.map((behaviour) => (
          <span key={behaviour} className="flex items-center gap-1 text-[9px] text-ink-dim">
            <span
              className="inline-block size-2"
              style={{ backgroundColor: behaviourColour(behaviour) }}
              aria-hidden
            />
            {behaviour}
          </span>
        ))}
      </div>

      <div className="space-y-1">
        {shares.entities.map((entity) => (
          <div key={entity} className="flex items-center gap-2">
            <span className="w-16 shrink-0 text-[10px] text-ink-dim">{entity}</span>
            <div className="flex h-4 flex-1 gap-0.5">
              {shares.behaviours.map((behaviour) => {
                const share = shares.shares[entity]?.[behaviour] ?? 0
                if (share <= 0) return null
                const percent = Math.round(share * 100)
                return (
                  <div
                    key={behaviour}
                    className="grid place-items-center overflow-visible"
                    style={{ width: `${share * 100}%`, backgroundColor: behaviourColour(behaviour) }}
                    title={`${entity} ${behaviour}: ${percent}% of ${shares.counts[entity]} decisions`}
                  >
                    {/* Only label a segment wide enough to hold the text. */}
                    {share >= 0.14 && (
                      <span className="text-[9px] tabular-nums text-void">{percent}%</span>
                    )}
                  </div>
                )
              })}
            </div>
            <span className="w-14 shrink-0 text-right text-[9px] tabular-nums text-ink-faint">
              {shares.counts[entity]}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-1 text-[10px] text-ink-faint">
        Share of each unit&apos;s own decisions. The number on the right is how many it made.
      </p>
    </div>
  )
}
