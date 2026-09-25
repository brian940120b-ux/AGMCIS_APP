import { useEffect, useState } from 'react'
import { Archive, ArchiveRestore, GitCompareArrows, Gauge, Trash2 } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { useModelStore } from '@/stores/modelStore'
import { useTrainingStore } from '@/stores/trainingStore'
import type { ModelVerdict, SavedModel } from '@/types/api'

const VERDICT_STYLE: Record<ModelVerdict, string> = {
  COMPATIBLE: 'text-green-hud',
  DIFFERENT_REWARD: 'text-amber-hud',
  INCOMPATIBLE: 'text-red-force',
  UNKNOWN: 'text-ink-faint',
}

const VERDICT_LABEL: Record<ModelVerdict, string> = {
  COMPATIBLE: 'COMPATIBLE',
  DIFFERENT_REWARD: 'OTHER REWARD',
  INCOMPATIBLE: 'INCOMPATIBLE',
  UNKNOWN: 'UNKNOWN',
}

const when = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })

/**
 * MODEL CENTRE (PHASE 19).
 *
 * A `.zip` on disk is not a usable policy: it is weights that expect a
 * particular observation vector, shaped by a particular reward. Run it against
 * a different one and nothing fails — it flies badly and the numbers look real.
 *
 * So every policy here leads with a verdict rather than a size and a date, and
 * one whose observation layout has moved on cannot be evaluated at all. The
 * refusal is the feature.
 */
export function ModelCentre() {
  const list = useModelStore((s) => s.list)
  const comparison = useModelStore((s) => s.comparison)
  const selected = useModelStore((s) => s.selected)
  const includeArchived = useModelStore((s) => s.includeArchived)
  const busy = useModelStore((s) => s.busy)
  const error = useModelStore((s) => s.error)
  const notice = useModelStore((s) => s.notice)
  const refresh = useModelStore((s) => s.refresh)
  const setIncludeArchived = useModelStore((s) => s.setIncludeArchived)
  const toggleSelected = useModelStore((s) => s.toggleSelected)
  const clearSelection = useModelStore((s) => s.clearSelection)
  const compare = useModelStore((s) => s.compare)
  const evaluate = useModelStore((s) => s.evaluate)
  const archive = useModelStore((s) => s.archive)
  const restore = useModelStore((s) => s.restore)
  const remove = useModelStore((s) => s.remove)

  const [episodes, setEpisodes] = useState('5')
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    void refresh()
  }, [refresh])

  // An evaluation writes its score onto the card, so the list is stale the
  // moment the job ends. Watch the job rather than asking the operator to
  // reload a page to see the number they just asked for.
  const job = useTrainingStore((s) => s.jobs?.current)
  const finishedEvaluation =
    job && job.kind === 'EVALUATE' && job.finished ? job.job_id : null
  useEffect(() => {
    if (finishedEvaluation) void refresh()
  }, [finishedEvaluation, refresh])

  if (list && !list.available) {
    return (
      <Panel title="Model Centre" subtitle="saved policies">
        <div className="px-3 py-4">
          <p className="text-[11px] text-amber-hud">
            The reinforcement-learning stack is not installed, so no policy can be loaded or
            measured.
          </p>
          <p className="mt-1 font-mono text-[10px] text-ink-faint">{list.install_hint}</p>
        </div>
      </Panel>
    )
  }

  const renderModel = (model: SavedModel) => {
    const verdict = model.compatibility.verdict
    const open = expanded === model.model_id
    return (
      <li key={model.model_id} className="px-3 py-2">
        <div className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={selected.includes(model.model_id)}
            onChange={() => toggleSelected(model.model_id)}
            aria-label={`Compare ${model.model_id}`}
            className="mt-1 size-3 shrink-0 accent-cyan-hud"
          />
          <button
            type="button"
            onClick={() => setExpanded(open ? null : model.model_id)}
            className="min-w-0 flex-1 text-left"
          >
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span className="truncate text-[11px] text-ink">{model.model_id}</span>
              <span className={`text-[9px] tracking-[0.15em] ${VERDICT_STYLE[verdict]}`}>
                {VERDICT_LABEL[verdict]}
              </span>
              {model.archived && (
                <span className="text-[9px] tracking-[0.15em] text-ink-faint">ARCHIVED</span>
              )}
            </span>
            <span className="block text-[10px] text-ink-faint">
              {when(model.created_at)} · {model.total_timesteps?.toLocaleString() ?? '?'} steps ·{' '}
              {model.scenario ?? 'unknown scenario'}
              {model.evaluation
                ? ` · scored ${model.evaluation.mean_reward} over ${model.evaluation.episodes} ep`
                : ' · not evaluated'}
            </span>
          </button>

          <div className="flex shrink-0 gap-1">
            <button
              type="button"
              onClick={() => void evaluate(model.model_id, Number(episodes) || 5)}
              disabled={busy || !model.compatibility.runnable}
              title={
                model.compatibility.runnable
                  ? `Evaluate over ${episodes} episodes`
                  : model.compatibility.detail
              }
              className="border border-edge px-1.5 py-0.5 text-cyan-hud transition hover:bg-cyan-hud/10 disabled:opacity-30"
            >
              <Gauge className="size-3" strokeWidth={1.5} />
              <span className="sr-only">Evaluate {model.model_id}</span>
            </button>
            {model.archived ? (
              <button
                type="button"
                onClick={() => void restore(model.model_id)}
                disabled={busy}
                title="Restore to the active list"
                className="border border-edge px-1.5 py-0.5 text-ink-dim transition hover:text-ink disabled:opacity-30"
              >
                <ArchiveRestore className="size-3" strokeWidth={1.5} />
                <span className="sr-only">Restore {model.model_id}</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void archive(model.model_id)}
                disabled={busy}
                title="Archive — the file stays on disk"
                className="border border-edge px-1.5 py-0.5 text-ink-dim transition hover:text-ink disabled:opacity-30"
              >
                <Archive className="size-3" strokeWidth={1.5} />
                <span className="sr-only">Archive {model.model_id}</span>
              </button>
            )}
            <button
              type="button"
              onClick={() => void remove(model.model_id)}
              disabled={busy}
              title="Delete the policy and its card for good"
              className="border border-edge px-1.5 py-0.5 text-red-force/70 transition hover:text-red-force disabled:opacity-30"
            >
              <Trash2 className="size-3" strokeWidth={1.5} />
              <span className="sr-only">Delete {model.model_id}</span>
            </button>
          </div>
        </div>

        {open && (
          <div className="mt-1.5 border-l border-edge pl-2 text-[10px] text-ink-faint">
            <p className={VERDICT_STYLE[verdict]}>{model.compatibility.detail}</p>
            {Object.keys(model.compatibility.reward_differences).length > 0 && (
              <ul className="mt-1">
                {Object.entries(model.compatibility.reward_differences).map(([term, [was, now]]) => (
                  <li key={term} className="tabular-nums">
                    {term}: trained at {was}, now {now}
                  </li>
                ))}
              </ul>
            )}
            {model.evaluation && (
              <p className="mt-1 tabular-nums">
                mean {model.evaluation.mean_reward} ± {model.evaluation.std_reward} ·{' '}
                {model.evaluation.mean_goals_reached} goals · {model.evaluation.mean_episode_steps}{' '}
                steps · {Object.entries(model.evaluation.endings).map(([k, v]) => `${k} ×${v}`).join(', ')}
              </p>
            )}
            {model.card_error && <p className="mt-1 text-red-force">card: {model.card_error}</p>}
          </div>
        )}
      </li>
    )
  }

  return (
    <div className="flex min-h-0 flex-col gap-3 [&>*]:shrink-0">
      <Panel
        title="Model Centre"
        subtitle={`${list?.count ?? 0} saved · observation layout v${list?.current_layout ?? '?'}`}
        actions={
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1 text-[9px] tracking-[0.15em] text-ink-faint">
              <input
                type="checkbox"
                checked={includeArchived}
                onChange={(event) => setIncludeArchived(event.target.checked)}
                className="size-3 accent-cyan-hud"
              />
              ARCHIVED
            </label>
            <label className="flex items-center gap-1 border-l border-edge pl-3 text-[9px] tracking-[0.15em] text-ink-faint">
              EPISODES
              <input
                type="number"
                min={1}
                max={20}
                value={episodes}
                onChange={(event) => setEpisodes(event.target.value)}
                aria-label="Evaluation episodes"
                className="w-12 border border-edge bg-deck px-1 py-0.5 text-[10px] tabular-nums text-ink"
              />
            </label>
          </div>
        }
      >
        {error && <p className="px-3 pt-2 text-[11px] text-amber-hud">{error}</p>}
        {notice && !error && <p className="px-3 pt-2 text-[11px] text-cyan-hud">{notice}</p>}

        {list && list.models.length > 0 ? (
          <ul className="divide-y divide-edge/40">{list.models.map(renderModel)}</ul>
        ) : (
          <p className="px-3 py-4 text-[11px] text-ink-faint">
            No policies saved yet. Train one above.
          </p>
        )}

        <p className="border-t border-edge/40 px-3 py-2 text-[10px] text-ink-faint italic">
          {list?.notice}
        </p>
      </Panel>

      {selected.length > 0 && (
        <Panel
          title="Compare policies"
          subtitle={`${selected.length} selected`}
          actions={
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => void compare()}
                disabled={selected.length < 2 || busy}
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
              <p
                className={`mb-2 text-[10px] ${comparison.comparable ? 'text-ink-faint' : 'text-amber-hud'}`}
              >
                {comparison.detail}
              </p>
              <table className="w-full text-[10px]">
                <thead>
                  <tr className="text-ink-faint">
                    <th scope="col" className="hud-label text-left font-normal">policy</th>
                    <th scope="col" className="hud-label text-left font-normal">verdict</th>
                    <th scope="col" className="hud-label text-right font-normal">steps</th>
                    <th scope="col" className="hud-label text-right font-normal">mean reward</th>
                    <th scope="col" className="hud-label text-right font-normal">goals</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {comparison.models.map((model) => (
                    <tr key={model.model_id} className="border-t border-edge/40">
                      <td className="truncate py-0.5 text-ink-dim">{model.model_id.slice(-13)}</td>
                      <td className={`py-0.5 ${VERDICT_STYLE[model.compatibility.verdict]}`}>
                        {VERDICT_LABEL[model.compatibility.verdict]}
                      </td>
                      <td className="py-0.5 text-right text-ink-faint">
                        {model.total_timesteps?.toLocaleString() ?? '—'}
                      </td>
                      <td className="py-0.5 text-right text-ink">
                        {model.evaluation?.mean_reward ?? '—'}
                      </td>
                      <td className="py-0.5 text-right text-ink-faint">
                        {model.evaluation?.mean_goals_reached ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="px-3 py-3 text-[11px] text-ink-faint">
              {selected.length < 2
                ? 'Select one more policy, then press COMPARE.'
                : 'Press COMPARE to line these up.'}
            </p>
          )}
        </Panel>
      )}
    </div>
  )
}
