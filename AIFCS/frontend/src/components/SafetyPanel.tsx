import { ShieldCheck } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { useSimulationStore } from '@/stores/simulationStore'

/** Human-readable names for the violation types the backend reports. */
const VIOLATION_LABEL: Record<string, string> = {
  NON_FINITE: 'Non-finite demand',
  OUT_OF_RANGE: 'Out of range',
  RATE_LIMITED: 'Actuator rate limited',
  LOAD_FACTOR: 'Load factor limit',
  ALTITUDE_FLOOR: 'Altitude floor',
  ALTITUDE_CEILING: 'Altitude ceiling',
  INVALID_STATE: 'Invalid aircraft state',
}

/**
 * Safety layer readout (PHASE 4).
 *
 * Every command an agent issues passes through validation, envelope protection
 * and actuator rate limiting. This panel shows what that layer actually did —
 * corrections are counted, never applied silently.
 */
export function SafetyPanel() {
  const controller = useSimulationStore((s) => s.controller)
  const violations = Object.entries(controller?.violations ?? {}).sort((a, b) => b[1] - a[1])

  return (
    <Panel
      title="Safety Layer"
      subtitle="every command is validated before it reaches the physics"
      actions={<ShieldCheck className="size-3.5 text-green-hud" strokeWidth={1.5} />}
    >
      {!controller ? (
        <p className="px-3 py-4 text-[11px] text-ink-faint">Waiting for the controller…</p>
      ) : (
        <div className="divide-y divide-edge/50">
          <div className="flex items-baseline justify-between gap-3 px-3 py-1.5">
            <span className="hud-label">Applied</span>
            <span className="text-[11px] tabular-nums text-ink-dim">
              {controller.commands_applied.toLocaleString()}
            </span>
          </div>
          <div className="flex items-baseline justify-between gap-3 px-3 py-1.5">
            <span className="hud-label">Rejected</span>
            <span
              className={`text-[11px] tabular-nums ${
                controller.commands_rejected > 0 ? 'text-red-force' : 'text-ink-dim'
              }`}
            >
              {controller.commands_rejected.toLocaleString()}
            </span>
          </div>
          <div className="flex items-baseline justify-between gap-3 px-3 py-1.5">
            <span className="hud-label">g Limit</span>
            <span className="text-[11px] tabular-nums text-ink-dim">
              {controller.limits.max_load_factor} g
            </span>
          </div>

          {violations.length > 0 && (
            <div className="px-3 py-1.5">
              <p className="hud-label mb-1">Corrections</p>
              <ul className="space-y-0.5">
                {violations.map(([type, count]) => (
                  <li key={type} className="flex items-baseline justify-between gap-2">
                    <span className="text-[10px] text-ink-faint">
                      {VIOLATION_LABEL[type] ?? type}
                    </span>
                    <span className="text-[10px] tabular-nums text-amber-hud">{count}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}
