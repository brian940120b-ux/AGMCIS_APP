import { Panel } from '@/components/Panel'
import { useStage } from '@/hooks/useStage'

/** Radians to a signed whole-degree string. */
const deg = (radians: number) => `${(radians * (180 / Math.PI)).toFixed(0)}°`

/** Live entity readout, straight from GET /api/entities. */
export function EntityList() {
  // Follows whatever is on stage. In replay mode this tracks the playback
  // cursor; showing the live world beside a replay would be two different
  // moments presented as one.
  const { entities, mode } = useStage()

  return (
    <Panel
      title="Entities"
      subtitle={`${entities.length} fictional units${mode === 'replay' ? ' · replay' : ''}`}
    >
      {entities.length === 0 ? (
        <p className="px-3 py-4 text-[11px] text-ink-faint">No entities loaded.</p>
      ) : (
        <ul className="divide-y divide-edge/60">
          {entities.map((entity) => (
            <li key={entity.id} className="px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <span
                  className={`text-xs ${entity.team === 'BLUE' ? 'text-blue-force' : entity.team === 'RED' ? 'text-red-force' : 'text-ink-dim'}`}
                >
                  {entity.id}
                </span>
                <span className="text-[10px] text-ink-faint">{entity.status}</span>
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-[10px] tabular-nums text-ink-faint">
                <span>{Math.round(entity.altitude)} m</span>
                <span>{Math.round(entity.speed)} m/s</span>
                <span>HDG {Math.round(entity.heading_deg)}°</span>
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-[10px] tabular-nums text-ink-faint">
                <span title="Roll">RLL {deg(entity.orientation[0])}</span>
                <span title="Pitch">PCH {deg(entity.orientation[1])}</span>
                <span title="Throttle">THR {Math.round(entity.controls.throttle * 100)}%</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
