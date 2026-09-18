import { Panel } from '@/components/Panel'
import { useSimulationStore } from '@/stores/simulationStore'

/** Colour by severity of meaning, not decoration. */
function eventColor(type: string): string {
  if (type.includes('ENDED') || type.includes('OUT_OF_BOUNDS') || type.includes('COLLISION')) {
    return 'text-amber-hud'
  }
  if (type.includes('STARTED') || type.includes('RESUMED')) return 'text-green-hud'
  if (type.includes('PAUSED') || type.includes('RESET')) return 'text-ink-dim'
  return 'text-cyan-hud'
}

/**
 * System event feed (PHASE 1). Newest first.
 * Agent decisions join this stream in PHASE 3.
 */
export function EventFeed() {
  const events = useSimulationStore((s) => s.events)
  const ordered = [...events].reverse()

  return (
    <Panel title="Event Feed" subtitle="system event bus">
      {ordered.length === 0 ? (
        <p className="px-3 py-4 text-[11px] text-ink-faint">No events yet.</p>
      ) : (
        <ul className="divide-y divide-edge/40">
          {ordered.map((event, index) => (
            <li key={`${event.wall_time}-${index}`} className="px-3 py-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className={`text-[10px] tracking-[0.1em] ${eventColor(event.type)}`}>
                  {event.type}
                </span>
                <span className="shrink-0 text-[10px] tabular-nums text-ink-faint">
                  t+{event.simulation_time.toFixed(1)}s
                </span>
              </div>
              {event.message && (
                <p className="text-[10px] leading-snug text-ink-faint">{event.message}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
