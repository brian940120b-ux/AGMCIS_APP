import { Panel } from '@/components/Panel'
import { useSimulationStore } from '@/stores/simulationStore'

/** Live entity readout, straight from GET /api/entities. */
export function EntityList() {
  const entities = useSimulationStore((s) => s.entities)

  return (
    <Panel title="Entities" subtitle={`${entities.length} fictional units`}>
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
              <div className="mt-0.5 flex gap-3 text-[10px] tabular-nums text-ink-faint">
                <span>{Math.round(entity.altitude)} m</span>
                <span>{Math.round(entity.speed)} m/s</span>
                <span>{Math.round(entity.heading_deg)}°</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
