import { useEffect } from 'react'
import { BarChart3, GitCompareArrows } from 'lucide-react'
import { BehaviourBars } from '@/charts/BehaviourBars'
import { LineChart } from '@/charts/LineChart'
import { ScoreHeatmap } from '@/charts/ScoreHeatmap'
import { Panel } from '@/components/Panel'
import { useAnalyticsStore } from '@/stores/analyticsStore'
import { useReplayStore } from '@/stores/replayStore'
import type { Chart } from '@/types/api'

const when = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })

/**
 * One chart, or an honest statement of why there is nothing to draw.
 *
 * A run that recorded nothing would otherwise be drawn as a flat line at zero,
 * which is a claim about the run rather than an absence of data.
 */
function ChartBlock({ chart }: { chart: Chart }) {
  return (
    <div className="border-t border-edge/50 px-3 py-2 first:border-t-0">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="hud-label text-ink-dim">{chart.title}</h3>
        {chart.available && (
          <span className="text-[9px] text-ink-faint">
            {chart.series.length} series · {chart.x_label}
          </span>
        )}
      </div>
      {chart.available ? (
        <LineChart
          series={chart.series}
          yLabel={chart.y_label}
          unit={chart.unit}
          zeroBased={chart.key === 'survival' || chart.key === 'coordination'}
        />
      ) : (
        <p className="py-3 text-[11px] text-ink-faint">{chart.detail}</p>
      )}
    </div>
  )
}

/**
 * ANALYTICS (PHASE 17).
 *
 * Charts of a finished run, drawn from what that run stored. Analysis is
 * downstream of truth: these numbers were computed from rows a completed run
 * wrote, and nothing on this screen can reach a tick.
 *
 * There is no live chart here on purpose. A chart of a run in progress would
 * redraw against a moving denominator and read as a measurement when it is a
 * partial one; the Command Center's live panels are where the current run is
 * watched.
 */
export function AnalyticsPanel() {
  const runs = useReplayStore((s) => s.runs)
  const loadRuns = useReplayStore((s) => s.loadRuns)

  const runId = useAnalyticsStore((s) => s.runId)
  const analytics = useAnalyticsStore((s) => s.analytics)
  const comparison = useAnalyticsStore((s) => s.comparison)
  const selected = useAnalyticsStore((s) => s.selected)
  const loading = useAnalyticsStore((s) => s.loading)
  const error = useAnalyticsStore((s) => s.error)
  const load = useAnalyticsStore((s) => s.load)
  const toggleSelected = useAnalyticsStore((s) => s.toggleSelected)
  const clearSelection = useAnalyticsStore((s) => s.clearSelection)
  const compare = useAnalyticsStore((s) => s.compare)

  useEffect(() => {
    void loadRuns()
  }, [loadRuns])

  // Open the newest run that actually has something to show. Telemetry is
  // sampled once a second, so a run shorter than that stored nothing and would
  // open on three charts explaining their own emptiness.
  useEffect(() => {
    if (runId !== null || runs.length === 0) return
    const sampled = runs.find((run) => run.ticks >= run.tick_rate_hz)
    void load((sampled ?? runs[0]).run_id)
  }, [runId, runs, load])

  return (
    <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[260px_1fr]">
      <Panel
        title="Runs"
        subtitle={`${runs.length} recorded · tick two or more to compare`}
        className="min-h-0"
      >
        {runs.length === 0 ? (
          <p className="px-3 py-4 text-[11px] text-ink-faint">
            No runs recorded yet. Press START, let it fly, then STOP.
          </p>
        ) : (
          <ul className="divide-y divide-edge/40">
            {runs.map((run) => {
              const active = run.run_id === runId
              const ticked = selected.includes(run.run_id)
              return (
                <li key={run.run_id} className="flex items-center gap-2 px-2 py-1.5">
                  <input
                    type="checkbox"
                    checked={ticked}
                    onChange={() => toggleSelected(run.run_id)}
                    aria-label={`Compare run ${run.run_id}`}
                    className="size-3 shrink-0 accent-cyan-hud"
                  />
                  <button
                    type="button"
                    onClick={() => void load(run.run_id)}
                    className={`min-w-0 flex-1 text-left transition ${
                      active ? 'text-cyan-hud' : 'text-ink-dim hover:text-ink'
                    }`}
                  >
                    <span className="block truncate text-[11px]">{run.scenario_name}</span>
                    <span className="block truncate text-[9px] text-ink-faint">
                      {when(run.started_at)} · {run.ticks} ticks · {run.integrator || 'unknown'}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </Panel>

      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto pr-1 [&>*]:shrink-0">
        {error && (
          <div className="hud-panel px-3 py-2">
            <p className="text-[11px] text-red-force">{error}</p>
          </div>
        )}

        {selected.length > 0 && (
          <Panel
            title="Compare"
            subtitle={`${selected.length} run${selected.length === 1 ? '' : 's'} ticked`}
            actions={
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => void compare()}
                  disabled={selected.length < 2 || loading}
                  className="flex items-center gap-1 border border-edge px-2 py-0.5 text-[10px] tracking-[0.15em] text-ink-dim transition hover:text-cyan-hud disabled:opacity-40"
                >
                  <GitCompareArrows className="size-3" strokeWidth={1.5} />
                  COMPARE
                </button>
                <button
                  type="button"
                  onClick={clearSelection}
                  className="border border-edge px-2 py-0.5 text-[10px] tracking-[0.15em] text-ink-faint transition hover:text-ink-dim"
                >
                  CLEAR
                </button>
              </div>
            }
          >
            {comparison ? (
              <div className="px-3 py-2">
                {!comparison.comparable && (
                  <p className="mb-2 text-[10px] text-amber-hud">{comparison.detail}</p>
                )}
                <table className="w-full text-[10px]">
                  <thead>
                    <tr className="text-ink-faint">
                      <th scope="col" className="hud-label text-left font-normal">
                        run
                      </th>
                      <th scope="col" className="hud-label text-left font-normal">
                        scenario
                      </th>
                      <th scope="col" className="hud-label text-left font-normal">
                        physics
                      </th>
                      <th scope="col" className="hud-label text-right font-normal">
                        BLUE
                      </th>
                      <th scope="col" className="hud-label text-right font-normal">
                        RED
                      </th>
                    </tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {comparison.runs.map((run) => (
                      <tr key={run.run_id} className="border-t border-edge/40">
                        <td className="truncate py-0.5 text-ink-dim">{run.run_id.slice(-9)}</td>
                        <td className="truncate py-0.5 text-ink-faint">{run.scenario}</td>
                        <td className="truncate py-0.5 text-ink-faint">{run.integrator}</td>
                        <td className="py-0.5 text-right text-ink">
                          {run.teams.BLUE?.toFixed(1) ?? '—'}
                        </td>
                        <td className="py-0.5 text-right text-ink">
                          {run.teams.RED?.toFixed(1) ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-1 text-[10px] text-ink-faint">{comparison.detail}</p>
              </div>
            ) : (
              <p className="px-3 py-3 text-[11px] text-ink-faint">
                {selected.length < 2
                  ? 'Tick one more run, then press COMPARE.'
                  : 'Press COMPARE to score these runs side by side.'}
              </p>
            )}
          </Panel>
        )}

        {analytics ? (
          <>
            <Panel
              title="Run"
              subtitle={`${analytics.run.scenario_name} · seed ${analytics.run.seed} · ${analytics.run.integrator}`}
              actions={<BarChart3 className="size-3.5 text-ink-faint" strokeWidth={1.5} />}
            >
              <div className="grid grid-cols-2 gap-x-3 px-3 py-2 text-[10px] sm:grid-cols-4">
                <p className="text-ink-faint">
                  ticks <span className="text-ink-dim tabular-nums">{analytics.run.ticks}</span>
                </p>
                <p className="text-ink-faint">
                  sim time{' '}
                  <span className="text-ink-dim tabular-nums">
                    {analytics.run.simulation_time_s.toFixed(1)}s
                  </span>
                </p>
                <p className="text-ink-faint">
                  samples{' '}
                  <span className="text-ink-dim tabular-nums">
                    {analytics.sampled.telemetry_samples}
                  </span>
                </p>
                <p className="text-ink-faint">
                  decisions{' '}
                  <span className="text-ink-dim tabular-nums">{analytics.sampled.decisions}</span>
                </p>
              </div>
              {analytics.sampled.decisions_truncated && (
                <p className="px-3 pb-2 text-[10px] text-amber-hud">
                  This run made more decisions than are stored ({analytics.sampled.decision_limit}).
                  The behaviour shares below describe the stored ones.
                </p>
              )}
            </Panel>

            <Panel title="Flight" subtitle="measured from the run's own telemetry samples">
              {analytics.charts.map((chart) => (
                <ChartBlock key={chart.key} chart={chart} />
              ))}
            </Panel>

            <Panel
              title="Score"
              subtitle={`${analytics.scores.available_points || 0} points available per unit`}
            >
              <ScoreHeatmap matrix={analytics.scores} />
            </Panel>

            <Panel title="Behaviour" subtitle="what each unit decided, as a share of its decisions">
              <BehaviourBars shares={analytics.behaviours} />
            </Panel>

            <p className="px-1 text-[10px] text-ink-faint italic">{analytics.notice}</p>
          </>
        ) : (
          <div className="hud-panel grid flex-1 place-items-center">
            <p className="text-[11px] text-ink-faint">
              {loading ? 'Reading the run…' : 'Pick a run on the left.'}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
