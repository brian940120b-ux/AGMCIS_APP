import { Radar } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { useSimulationStore } from '@/stores/simulationStore'

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 px-3 py-1.5">
      <span className="hud-label shrink-0">{label}</span>
      <span className="truncate text-right text-[11px] text-ink-dim">{value}</span>
    </div>
  )
}

/**
 * Perception readout (PHASE 5).
 *
 * Agents no longer see the world — they see an estimate. This panel shows the
 * limits that estimate is subject to, and how many contacts each unit is
 * currently holding a track on.
 */
export function PerceptionPanel() {
  const sensors = useSimulationStore((s) => s.sensors)
  const decisions = useSimulationStore((s) => s.decisions)

  // Latest observation confidence per unit, straight from the decision records.
  const latestConfidence = new Map<string, number>()
  for (const decision of decisions) {
    latestConfidence.set(decision.entity_id, decision.observation.confidence)
  }

  const tracked = Object.entries(sensors?.tracked_contacts ?? {}).sort()

  return (
    <Panel
      title="Perception"
      subtitle={sensors?.enabled ? 'agents see an estimate, not the truth' : 'sensing disabled'}
      actions={<Radar className="size-3.5 text-cyan-hud/70" strokeWidth={1.5} />}
    >
      {!sensors ? (
        <p className="px-3 py-4 text-[11px] text-ink-faint">Waiting for the sensor model…</p>
      ) : (
        <div className="divide-y divide-edge/50">
          <Row label="Range" value={`${(sensors.max_range_m / 1000).toFixed(0)} km`} />
          <Row
            label="Coverage"
            value={
              sensors.field_of_regard_deg >= 180
                ? 'all-round'
                : `±${sensors.field_of_regard_deg.toFixed(0)}°`
            }
          />
          <Row label="Latency" value={`${(sensors.latency_s * 1000).toFixed(0)} ms`} />
          <Row label="Dropout" value={`${(sensors.dropout_probability * 100).toFixed(0)}%`} />
          <Row label="Track memory" value={`${sensors.track_memory_s.toFixed(1)} s`} />

          {tracked.length > 0 && (
            <div className="px-3 py-1.5">
              <p className="hud-label mb-1">Tracks held</p>
              <ul className="space-y-0.5">
                {tracked.map(([entityId, count]) => {
                  const confidence = latestConfidence.get(entityId)
                  return (
                    <li key={entityId} className="flex items-baseline justify-between gap-2">
                      <span className="text-[10px] text-ink-faint">{entityId}</span>
                      <span className="text-[10px] tabular-nums text-ink-dim">
                        {count} {confidence !== undefined && `· ${confidence.toFixed(2)}`}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}
