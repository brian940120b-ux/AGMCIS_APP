import { useEffect, useState } from 'react'
import { CircleStop, Play } from 'lucide-react'
import { LineChart } from '@/charts/LineChart'
import { Panel } from '@/components/Panel'
import { RLUnavailable } from '@/components/RLUnavailable'
import { StateBadge } from '@/components/StateBadge'
import { useTrainingStore } from '@/stores/trainingStore'
import type { TrainingJob } from '@/types/api'

const STATE_STYLE: Record<TrainingJob['state'], string> = {
  PENDING: 'text-ink-faint',
  RUNNING: 'text-cyan-hud',
  STOPPING: 'text-amber-hud',
  COMPLETED: 'text-green-hud',
  CANCELLED: 'text-amber-hud',
  FAILED: 'text-red-force',
}

const duration = (seconds: number) => {
  const total = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(total / 60)
  return minutes > 0 ? `${minutes}m ${String(total % 60).padStart(2, '0')}s` : `${total}s`
}

/**
 * The reward curve, drawn from what the job has reported so far.
 *
 * The mean is null until the first episode ends — there is nothing to average
 * before that — so those points are dropped rather than plotted as zero.
 */
function RewardCurve({ job }: { job: TrainingJob }) {
  const points = job.metrics
    .filter((m) => m.episode_reward_mean !== null)
    .map((m) => [m.timesteps, m.episode_reward_mean as number] as [number, number])

  if (points.length < 2) {
    return (
      <p className="px-3 py-4 text-[11px] text-ink-faint">
        No episode has finished yet, so there is no mean reward to plot. Episodes run up to
        the configured limit before they end.
      </p>
    )
  }

  return (
    <div className="px-3 py-2">
      <LineChart
        series={[{ key: 'reward', label: 'mean reward', group: 'BLUE', points }]}
        yLabel="mean episode reward"
        unit=""
        xSuffix=""
        height={180}
      />
      <p className="mt-1 text-[10px] text-ink-faint">
        Mean reward over the last episodes, against timesteps. Reported by the trainer, not
        recomputed here.
      </p>
    </div>
  )
}

/**
 * An evaluation job counts episodes in the same field a training job counts
 * timesteps, because one runner serves both. Calling five episodes "5 steps"
 * is not a rounding error in the wording: five steps is a twelfth of a second
 * of flight, and five episodes is ten minutes of it.
 */
function workLabel(job: TrainingJob): string {
  if (job.kind === 'EVALUATE') {
    const done = job.timesteps.toLocaleString()
    return `${done} of ${job.evaluate_episodes.toLocaleString()} episodes`
  }
  return `${job.timesteps.toLocaleString()} steps`
}

function progressLabel(job: TrainingJob): string {
  if (job.kind === 'EVALUATE') return workLabel(job)
  return `${job.timesteps.toLocaleString()} / ${job.requested_timesteps.toLocaleString()} steps`
}

function JobProgress({ job }: { job: TrainingJob }) {
  const percent = Math.round(job.fraction * 100)
  return (
    <div className="px-3 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className={`text-[11px] ${STATE_STYLE[job.state]}`}>{job.state}</span>
        <span className="text-[10px] tabular-nums text-ink-faint">
          {progressLabel(job)} · {duration(job.elapsed_s)}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full bg-edge">
        <div
          className={`h-full ${job.state === 'FAILED' ? 'bg-red-force' : 'bg-cyan-hud'}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2 text-[10px] text-ink-faint">
        <span>
          {job.algorithm.toUpperCase()} · seed {job.seed} · {job.job_id}
        </span>
        <span className="tabular-nums">{percent}%</span>
      </div>
      {job.error && <p className="mt-1 text-[10px] text-red-force">{job.error}</p>}
    </div>
  )
}

/**
 * TRAINING CENTRE (PHASE 18).
 *
 * `train.py` named the three things a dashboard needed before it was allowed a
 * START button: progress, cancellation, and surviving a reload. All three are
 * real here, so the button is real too.
 *
 * What is still stated rather than hidden: a job runs in the server process,
 * one at a time, and does not survive a restart. Stopping one is not a discard
 * — the policy trained so far is saved, and the run is recorded as cancelled
 * rather than filed as a short completed one.
 */
export function TrainingCentre() {
  const jobs = useTrainingStore((s) => s.jobs)
  const error = useTrainingStore((s) => s.error)
  const loading = useTrainingStore((s) => s.loading)
  const refresh = useTrainingStore((s) => s.refresh)
  const start = useTrainingStore((s) => s.start)
  const stop = useTrainingStore((s) => s.stop)
  const startPolling = useTrainingStore((s) => s.startPolling)
  const stopPolling = useTrainingStore((s) => s.stopPolling)

  const [algorithm, setAlgorithm] = useState('ppo')
  const [timesteps, setTimesteps] = useState('20000')
  const [seed, setSeed] = useState('')

  useEffect(() => {
    void refresh().then(() => {
      // Rejoin a job that was already running before this page loaded.
      if (useTrainingStore.getState().jobs?.busy) startPolling()
    })
    return () => stopPolling()
  }, [refresh, startPolling, stopPolling])

  const current = jobs?.current ?? null
  const running = Boolean(jobs?.busy)

  if (jobs && !jobs.available) {
    return (
      <Panel title="Training Centre" subtitle="reinforcement learning">
        <div className="px-3 py-4">
          <RLUnavailable
            consequence="nothing can be trained"
            installHint={jobs.install_hint}
            reason={jobs.unavailable_reason}
          />
        </div>
      </Panel>
    )
  }

  return (
    <div className="flex min-h-0 flex-col gap-3 [&>*]:shrink-0">
      <Panel
        title="Training Centre"
        subtitle={jobs?.notice ?? 'reinforcement learning'}
        actions={
          <StateBadge
            state={running ? 'ONLINE' : 'OFFLINE'}
            label={running ? 'TRAINING' : 'IDLE'}
            pulse={running}
          />
        }
      >
        <div className="flex flex-wrap items-end gap-3 px-3 py-2">
          <label className="flex flex-col gap-0.5">
            <span className="hud-label">algorithm</span>
            <select
              value={algorithm}
              onChange={(event) => setAlgorithm(event.target.value)}
              disabled={running}
              className="border border-edge bg-deck px-2 py-1 text-[11px] text-ink disabled:opacity-40"
            >
              {(jobs?.algorithms ?? ['ppo', 'sac']).map((name) => (
                <option key={name} value={name}>
                  {name.toUpperCase()}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-0.5">
            <span className="hud-label">timesteps</span>
            <input
              type="number"
              min={1}
              max={jobs?.max_timesteps ?? 500000}
              value={timesteps}
              onChange={(event) => setTimesteps(event.target.value)}
              disabled={running}
              className="w-28 border border-edge bg-deck px-2 py-1 text-[11px] tabular-nums text-ink disabled:opacity-40"
            />
          </label>

          <label className="flex flex-col gap-0.5">
            <span className="hud-label">seed</span>
            <input
              type="number"
              placeholder="config"
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
              disabled={running}
              className="w-24 border border-edge bg-deck px-2 py-1 text-[11px] tabular-nums text-ink disabled:opacity-40"
            />
          </label>

          {running ? (
            <button
              type="button"
              onClick={() => void stop()}
              disabled={loading || current?.state === 'STOPPING'}
              className="flex items-center gap-1 border border-amber-hud/50 px-3 py-1 text-[11px] tracking-[0.15em] text-amber-hud transition hover:bg-amber-hud/10 disabled:opacity-40"
            >
              <CircleStop className="size-3.5" strokeWidth={1.5} />
              STOP
            </button>
          ) : (
            <button
              type="button"
              onClick={() =>
                void start({
                  algorithm,
                  timesteps: Number(timesteps) || undefined,
                  seed: seed === '' ? undefined : Number(seed),
                })
              }
              disabled={loading}
              className="flex items-center gap-1 border border-cyan-hud/50 px-3 py-1 text-[11px] tracking-[0.15em] text-cyan-hud transition hover:bg-cyan-hud/10 disabled:opacity-40"
            >
              <Play className="size-3.5" strokeWidth={1.5} />
              START TRAINING
            </button>
          )}

          <p className="text-[10px] text-ink-faint">
            up to {(jobs?.max_timesteps ?? 0).toLocaleString()} steps per job
          </p>
        </div>

        {error && <p className="px-3 pb-2 text-[11px] text-amber-hud">{error}</p>}

        {current ? (
          <JobProgress job={current} />
        ) : (
          <p className="px-3 py-3 text-[11px] text-ink-faint">
            Nothing training. A longer run belongs on the command line, where it can outlive
            this server:{' '}
            <span className="font-mono">
              cd backend &amp;&amp; ../.venv/bin/python train.py --timesteps 200000
            </span>
          </p>
        )}
      </Panel>

      {current && (
        <Panel title="Reward" subtitle={`${current.metrics.length} samples reported`}>
          <RewardCurve job={current} />
        </Panel>
      )}

      <Panel
        title="Jobs this session"
        subtitle="a job does not survive a server restart — the database keeps the history"
      >
        {jobs && jobs.history.length > 0 ? (
          <ul className="divide-y divide-edge/40">
            {jobs.history.map((job) => (
              <li key={job.job_id} className="flex items-baseline gap-2 px-3 py-1.5 text-[10px]">
                <span className={`w-20 shrink-0 ${STATE_STYLE[job.state]}`}>{job.state}</span>
                <span className="text-ink-dim">{job.algorithm.toUpperCase()}</span>
                <span className="tabular-nums text-ink-faint">{workLabel(job)}</span>
                {job.kind === 'EVALUATE' && job.model_id && (
                  <span className="truncate text-ink-faint">{job.model_id}</span>
                )}
                <span className="ml-auto tabular-nums text-ink-faint">
                  {duration(job.elapsed_s)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="px-3 py-3 text-[11px] text-ink-faint">
            No job has run in this server yet.
          </p>
        )}
      </Panel>
    </div>
  )
}
