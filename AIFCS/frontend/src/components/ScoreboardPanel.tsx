import { useEffect, useState } from 'react'
import { Trash2, Trophy } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { useReplayStore } from '@/stores/replayStore'
import type { ScoreTerm, StoredScore } from '@/types/api'

const when = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })

/** Bar showing how much of a term's available weight was earned. */
function TermBar({ term }: { term: ScoreTerm }) {
  if (!term.applicable) {
    return (
      <div className="flex items-baseline justify-between gap-2 py-0.5">
        <span className="text-[10px] text-ink-faint">{term.name}</span>
        <span className="text-[9px] text-ink-faint italic">
          not applicable — weight redistributed
        </span>
      </div>
    )
  }
  const earned = term.weight > 0 ? term.points / term.weight : 0
  return (
    <div className="py-0.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] text-ink-dim">{term.name}</span>
        <span className="text-[10px] tabular-nums text-ink-faint">
          {term.points.toFixed(1)} / {term.weight.toFixed(1)}
        </span>
      </div>
      <div className="mt-0.5 h-1 w-full bg-edge">
        <div
          className="h-full bg-cyan-hud/70"
          style={{ width: `${Math.round(Math.min(1, Math.max(0, earned)) * 100)}%` }}
        />
      </div>
    </div>
  )
}

/**
 * RUN HISTORY AND SCORES (PHASE 9).
 *
 * Scores come from the database, computed by the scoring engine from the
 * recording — never from the live engine. `weights_hash` is shown because two
 * scores are only comparable when it matches: change a weight and every older
 * score was measured with a different ruler.
 */
export function ScoreboardPanel() {
  const { runs, selectedRun, weights, busy, loadRuns, selectRun, loadWeights, rescore, remove } =
    useReplayStore()
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    void loadRuns()
    void loadWeights()
  }, [loadRuns, loadWeights])

  const entityScores = (selectedRun?.scores ?? []).filter(
    (s: StoredScore) => s.subject === 'entity',
  )
  const teamScores = (selectedRun?.scores ?? []).filter((s: StoredScore) => s.subject === 'team')

  return (
    <Panel
      title="Run History"
      subtitle={
        weights ? `${weights.max_points} points · ruler ${weights.weights_hash}` : 'past runs'
      }
      actions={<Trophy className="size-3.5 text-cyan-hud" strokeWidth={1.5} />}
    >
      <div className="px-3 py-2">
        {runs.length === 0 && (
          <p className="text-[10px] text-ink-faint">
            No runs stored yet. Start a simulation and stop it — the run is recorded and scored
            automatically.
          </p>
        )}

        <div className="space-y-1">
          {runs.slice(0, 12).map((run) => {
            const best = run.scores
              .filter((s) => s.subject === 'team')
              .sort((a, b) => b.total - a.total)[0]
            const open = selectedRun?.run_id === run.run_id
            return (
              <div key={run.run_id} className="border border-edge">
                <button
                  type="button"
                  onClick={() => void selectRun(open ? null : run.run_id)}
                  className={`flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left transition ${
                    open ? 'bg-cyan-hud/5' : 'hover:bg-white/5'
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-[10px] text-ink">
                      {run.run_id}
                    </span>
                    <span className="block truncate text-[9px] text-ink-faint">
                      {run.scenario_name} · {run.simulation_time_s.toFixed(0)}s ·{' '}
                      {run.end_reason ?? 'unfinished'} · {when(run.started_at)}
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block text-[11px] tabular-nums text-cyan-hud">
                      {best ? best.total.toFixed(1) : '—'}
                    </span>
                    <span className="block text-[9px] text-ink-faint">
                      {best ? best.subject_id : 'unscored'}
                    </span>
                  </span>
                </button>

                {open && selectedRun && (
                  <div className="space-y-2 border-t border-edge/60 px-2 py-2">
                    <div className="flex flex-wrap gap-2 text-[9px] text-ink-faint">
                      <span>seed {selectedRun.seed}</span>
                      <span>cfg {selectedRun.config_hash}</span>
                      <span>{selectedRun.ticks} ticks</span>
                      <span>state {selectedRun.final_state_hash ?? '—'}</span>
                      {selectedRun.replay && <span>{selectedRun.replay.frames} frames</span>}
                    </div>

                    {teamScores.length > 0 && (
                      <div className="flex flex-wrap gap-3">
                        {teamScores.map((team) => (
                          <span key={team.subject_id} className="text-[10px]">
                            <span
                              className={
                                team.subject_id === 'RED' ? 'text-rose-400' : 'text-sky-400'
                              }
                            >
                              {team.subject_id}
                            </span>{' '}
                            <span className="tabular-nums text-ink-dim">
                              {team.total.toFixed(1)}
                            </span>
                          </span>
                        ))}
                      </div>
                    )}

                    {entityScores.length === 0 && (
                      <p className="text-[10px] text-ink-faint">
                        No score stored for this run. Press RESCORE to compute one from its
                        recording.
                      </p>
                    )}

                    {entityScores
                      .slice()
                      .sort((a, b) => b.total - a.total)
                      .map((score) => {
                        const showTerms = expanded === score.subject_id
                        return (
                          <div key={score.subject_id} className="border border-edge/60">
                            <button
                              type="button"
                              onClick={() =>
                                setExpanded(showTerms ? null : score.subject_id)
                              }
                              className="flex w-full items-baseline justify-between gap-2 px-2 py-1 text-left hover:bg-white/5"
                            >
                              <span className="font-mono text-[10px] text-ink-dim">
                                {score.subject_id}
                              </span>
                              <span className="text-[11px] tabular-nums text-cyan-hud">
                                {score.total.toFixed(1)}
                              </span>
                            </button>
                            {showTerms && (
                              <div className="border-t border-edge/60 px-2 py-1">
                                {(score.breakdown.terms ?? []).map((term) => (
                                  <TermBar key={term.name} term={term} />
                                ))}
                                <p className="mt-1 text-[9px] text-ink-faint">
                                  ruler {score.weights_hash}
                                </p>
                              </div>
                            )}
                          </div>
                        )
                      })}

                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void rescore(run.run_id)}
                        title="Recompute the score from the recording, under the current weights"
                        className="border border-edge px-2 py-0.5 text-[10px] text-ink-faint transition hover:border-cyan-hud/60 hover:text-cyan-hud disabled:opacity-40"
                      >
                        RESCORE
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void remove(run.run_id)}
                        title="Delete this run and its recording"
                        className="inline-flex items-center gap-1 border border-edge px-2 py-0.5 text-[10px] text-ink-faint transition hover:border-rose-400/60 hover:text-rose-400 disabled:opacity-40"
                      >
                        <Trash2 className="size-3" strokeWidth={1.5} />
                        DELETE
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {weights && (
          <p className="mt-2 text-[9px] leading-relaxed text-ink-faint">{weights.notice}</p>
        )}
      </div>
    </Panel>
  )
}
