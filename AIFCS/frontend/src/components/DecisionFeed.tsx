import { Panel } from '@/components/Panel'
import { useSimulationStore } from '@/stores/simulationStore'
import type { AgentDecision } from '@/types/api'

/** Behaviour colours. AVOID is the one that warrants attention. */
const BEHAVIOUR_STYLE: Record<AgentDecision['behaviour'], string> = {
  HOLD: 'text-ink-dim',
  NAVIGATE: 'text-cyan-hud',
  PATROL: 'text-cyan-hud',
  FORMATION: 'text-violet-hud',
  AVOID: 'text-amber-hud',
}

/** Confidence rendered as a small bar — quicker to scan than a number. */
function ConfidenceBar({ value }: { value: number }) {
  return (
    <span className="inline-flex items-center gap-1" title={`confidence ${value.toFixed(2)}`}>
      <span className="h-1 w-8 bg-edge">
        <span
          className="block h-full bg-cyan-hud"
          style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }}
        />
      </span>
      <span className="text-[9px] text-ink-faint">{value.toFixed(2)}</span>
    </span>
  )
}

/**
 * AI decision feed (PHASE 3).
 *
 * Shows what each agent decided, how confident it was and the reason codes
 * behind it. Every reason code is emitted by the agent from a condition it
 * actually measured — none of this text is generated for display.
 */
export function DecisionFeed() {
  const decisions = useSimulationStore((s) => s.decisions)
  const agents = useSimulationStore((s) => s.agents)
  const newestFirst = [...decisions].reverse()

  return (
    <Panel
      title="AI Decision Feed"
      subtitle={
        agents
          ? `${agents.agent_count} agents · ${agents.decision_rate_hz} Hz · ${agents.total_decisions} decisions`
          : 'no agents'
      }
    >
      {newestFirst.length === 0 ? (
        <p className="px-3 py-4 text-[11px] text-ink-faint">
          No decisions yet. Press START to run the agents.
        </p>
      ) : (
        <ul className="divide-y divide-edge/40">
          {newestFirst.map((decision, index) => (
            <li key={`${decision.agent_id}-${decision.tick}-${index}`} className="px-3 py-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[11px] text-ink">{decision.entity_id}</span>
                <span className="text-[9px] tabular-nums text-ink-faint">
                  t+{decision.simulation_time.toFixed(1)}s
                </span>
              </div>
              <div className="mt-0.5 flex items-center justify-between gap-2">
                <span className={`text-[10px] tracking-[0.12em] ${BEHAVIOUR_STYLE[decision.behaviour]}`}>
                  {decision.behaviour}
                </span>
                <ConfidenceBar value={decision.confidence} />
              </div>
              {decision.reason_codes.length > 0 && (
                <ul className="mt-0.5 space-y-px">
                  {decision.reason_codes.map((code) => (
                    <li key={code} className="text-[9px] leading-tight text-ink-faint">
                      · {code.replaceAll('_', ' ').toLowerCase()}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
