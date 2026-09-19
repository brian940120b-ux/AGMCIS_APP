import { useEffect, useState } from 'react'
import { Network, TriangleAlert } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { api } from '@/api/client'
import type { CommandersResponse, TasksResponse, TeamPicture } from '@/types/api'

const TASK_COLOUR: Record<string, string> = {
  PATROL: 'text-cyan-hud',
  ESCORT: 'text-green-hud',
  TRANSIT: 'text-amber-300',
  HOLD: 'text-ink-faint',
}

/** Reason codes read better as sentences than as SHOUTING_SNAKE_CASE. */
const REASON_TEXT: Record<string, string> = {
  ROUTE_AVAILABLE: 'has a route',
  LEADER_AVAILABLE: 'leader on the link',
  LEADER_LOST: 'leader lost',
  REBALANCING_COVERAGE: 'took over the route',
  NO_TASK_ASSIGNED: 'nothing assigned',
  TASK_COMPLETE: 'finished',
  UNIT_UNRESPONSIVE: 'not answering',
}

/**
 * COORDINATION (PHASE 14-15).
 *
 * Who was told to do what, and why. The team picture shown here is the one the
 * team actually has — assembled from what its units shared over the datalink,
 * with the age of each report — not the world. A unit nobody has heard from is
 * shown as unheard rather than quietly drawn where it last was.
 *
 * There is no control here, deliberately: allocation happens inside the tick,
 * and a button that issued an order from outside would be a second source of
 * truth that disagreed with the commander the moment it next ran.
 */
export function CoordinationPanel() {
  const [teams, setTeams] = useState<TeamPicture[]>([])
  const [tasks, setTasks] = useState<TasksResponse | null>(null)
  const [commanders, setCommanders] = useState<CommandersResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    const poll = async () => {
      try {
        const [t, k, c] = await Promise.all([api.teams(), api.tasks(), api.commanders()])
        if (!live) return
        setTeams(t.teams)
        setTasks(k)
        setCommanders(c)
        setError(null)
      } catch (cause) {
        if (live) setError(cause instanceof Error ? cause.message : 'Backend unreachable')
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 2000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [])

  const active = tasks?.active ?? {}

  return (
    <Panel
      title="Coordination"
      subtitle={
        commanders?.enabled
          ? `${commanders.count} commander${commanders.count === 1 ? '' : 's'} · ${tasks?.issued ?? 0} orders`
          : 'commander disabled'
      }
      actions={<Network className="size-3.5 text-cyan-hud" strokeWidth={1.5} />}
    >
      <div className="px-3 py-2">
        {error && <p className="text-[10px] text-rose-400">{error}</p>}
        {!error && teams.length === 0 && (
          <p className="text-[10px] text-ink-faint">
            No teams yet. Press START to load a scenario.
          </p>
        )}

        {teams.map((team) => (
          <div key={team.team} className="mb-2">
            <div className="flex items-baseline justify-between gap-2">
              <span
                className={`text-[11px] ${team.team === 'RED' ? 'text-rose-400' : 'text-sky-400'}`}
              >
                {team.team}
              </span>
              <span className="text-[9px] text-ink-faint">
                {team.heard_from}/{team.active} on the link
                {team.unheard.length > 0 && (
                  <span className="ml-1 text-amber-300">· {team.unheard.join(' ')} unheard</span>
                )}
              </span>
            </div>

            <div className="mt-0.5 divide-y divide-edge/40">
              {team.members.map((member) => {
                const task = active[member.entity_id]
                return (
                  <div
                    key={member.entity_id}
                    className="flex items-baseline justify-between gap-2 py-1"
                  >
                    <span className="min-w-0">
                      <span className="font-mono text-[10px] text-ink-dim">
                        {member.entity_id}
                      </span>
                      {!member.heard_from && (
                        <TriangleAlert
                          className="ml-1 inline size-3 text-amber-300"
                          strokeWidth={1.5}
                        />
                      )}
                      <span className="block truncate text-[9px] text-ink-faint">
                        {task
                          ? task.reasons.map((r) => REASON_TEXT[r] ?? r).join(' · ')
                          : 'no order'}
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span
                        className={`block text-[10px] ${task ? (TASK_COLOUR[task.type] ?? 'text-ink-dim') : 'text-ink-faint'}`}
                      >
                        {task?.type ?? '—'}
                      </span>
                      <span className="block text-[9px] text-ink-faint tabular-nums">
                        {member.report_age_s === null
                          ? 'never heard'
                          : `${member.report_age_s.toFixed(1)}s old`}
                      </span>
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}

        {commanders && commanders.count > 0 && (
          <div className="mt-1 border-t border-edge/60 pt-1.5">
            {commanders.commanders.map((commander) => (
              <div
                key={commander.commander_id}
                className="flex items-baseline justify-between gap-2 text-[9px] text-ink-faint"
              >
                <span className="font-mono">{commander.commander_id}</span>
                <span className="tabular-nums">
                  {commander.orders_sent} sent
                  {commander.orders_refused > 0 && ` · ${commander.orders_refused} refused`}
                </span>
              </div>
            ))}
            <p className="mt-1 text-[9px] leading-relaxed text-ink-faint">
              A commander allocates tasks only. It has no aircraft and no path to a control
              surface.
            </p>
          </div>
        )}
      </div>
    </Panel>
  )
}
