import { useEffect, useState } from 'react'
import { BrainCircuit, Terminal } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { RLUnavailable } from '@/components/RLUnavailable'
import { api } from '@/api/client'
import type {
  TrainedModel,
  TrainingEnvironmentSpec,
  TrainingReward,
  TrainingStatus,
} from '@/types/api'

const bytes = (n: number) =>
  n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} kB`

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="hud-label">{label}</span>
      <span className="truncate text-[11px] tabular-nums text-ink-dim">{value}</span>
    </div>
  )
}

/**
 * TRAINING (PHASE 11-13).
 *
 * Read-only on purpose. The environment and the PPO/SAC pipelines are real,
 * but a run takes minutes to hours, and a START button that cannot report
 * progress, be cancelled, or survive a page reload would be a control that
 * does not do what it appears to. So this panel shows what is true — the
 * device, the environment, the reward, and what has been trained — and gives
 * the command that actually starts a run.
 */
export function TrainingPanel() {
  const [status, setStatus] = useState<TrainingStatus | null>(null)
  const [spec, setSpec] = useState<TrainingEnvironmentSpec | null>(null)
  const [reward, setReward] = useState<TrainingReward | null>(null)
  const [models, setModels] = useState<TrainedModel[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await api.trainingStatus()
        if (cancelled) return
        setStatus(next)
        const [rewardBody, modelsBody] = await Promise.all([
          api.trainingReward(),
          api.trainingModels(),
        ])
        if (cancelled) return
        setReward(rewardBody)
        setModels(modelsBody.models)
        if (next.available) setSpec(await api.trainingEnvironment())
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'Backend unreachable')
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <Panel
      title="Training"
      subtitle={
        status
          ? status.available
            ? `PPO / SAC on ${status.device}`
            : status?.unavailable_reason && !status.install_hint
              ? 'RL stack will not load'
              : 'RL stack not installed'
          : 'reading…'
      }
      actions={<BrainCircuit className="size-3.5 text-cyan-hud" strokeWidth={1.5} />}
    >
      <div className="px-3 py-2">
        {error && <p className="text-[10px] text-rose-400">{error}</p>}

        {status && !status.available && (
          <RLUnavailable
            consequence="the training centre is closed"
            installHint={status.install_hint}
            reason={status.unavailable_reason}
          />
        )}

        {status?.available && (
          <>
            <div className="divide-y divide-edge/50">
              <Row label="Device" value={status.device} />
              <Row label="Task" value={status.scenario} />
              {spec && <Row label="Observation" value={`${spec.observation_size} values`} />}
              {spec && <Row label="Action" value={spec.action_channels.join(' · ')} />}
              {spec && (
                <Row
                  label="Episode"
                  value={`${spec.max_episode_steps} steps · ${spec.step_seconds.toFixed(2)}s each`}
                />
              )}
            </div>

            {reward && (
              <>
                <p className="hud-label mt-2">reward terms</p>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {reward.terms.map((term) => {
                    const weight = reward.weights[term] ?? 0
                    return (
                      <span
                        key={term}
                        title={`weight ${weight}`}
                        className={`border px-1.5 py-0.5 text-[9px] ${
                          weight < 0
                            ? 'border-rose-400/40 text-rose-400'
                            : 'border-edge text-ink-faint'
                        }`}
                      >
                        {term} {weight}
                      </span>
                    )
                  })}
                </div>
              </>
            )}

            <p className="hud-label mt-2">
              trained policies ({models.length})
            </p>
            {models.length === 0 && (
              <p className="text-[10px] text-ink-faint">
                None yet. Run the command below to train one.
              </p>
            )}
            <div className="mt-0.5 space-y-1">
              {models.slice(0, 5).map((model) => {
                const evaluation = model.card?.evaluation
                return (
                  <div key={model.model_id} className="border border-edge px-2 py-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-mono text-[10px] text-ink">
                        {model.model_id}
                      </span>
                      <span className="shrink-0 text-[10px] tabular-nums text-cyan-hud">
                        {evaluation ? evaluation.mean_reward.toFixed(1) : '—'}
                      </span>
                    </div>
                    <p className="truncate text-[9px] text-ink-faint">
                      {model.card?.total_timesteps?.toLocaleString() ?? '?'} steps ·{' '}
                      {bytes(model.size_bytes)}
                      {evaluation && ` · ${evaluation.mean_goals_reached} goals/episode`}
                    </p>
                  </div>
                )
              })}
            </div>

            {/* Not a button. Saying so is the point. */}
            <div className="mt-2 border border-edge/60 px-2 py-1.5">
              <p className="flex items-center gap-1 text-[9px] text-ink-faint">
                <Terminal className="size-3" strokeWidth={1.5} />
                Training runs from the command line:
              </p>
              <code className="mt-1 block break-all text-[9px] text-ink-dim">
                {status.how_to_run}
              </code>
              <p className="mt-1 text-[9px] leading-relaxed text-ink-faint">
                {status.browser_control_note}
              </p>
            </div>
          </>
        )}
      </div>
    </Panel>
  )
}
