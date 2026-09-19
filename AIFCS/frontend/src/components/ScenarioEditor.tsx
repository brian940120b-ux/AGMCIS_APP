import { useEffect, useState } from 'react'
import { Check, Copy, FilePlus2, Plus, Save, Trash2, TriangleAlert, X } from 'lucide-react'
import { Panel } from '@/components/Panel'
import { useScenarioStore } from '@/stores/scenarioStore'
import type { ScenarioDocument } from '@/types/api'

const TEAMS = ['BLUE', 'RED', 'NEUTRAL'] as const
const AGENTS = ['rule', 'none'] as const

/** A raw entity block from the YAML document. Loose on purpose: it is the file. */
type EntityBlock = Record<string, unknown>

const num = (value: unknown, fallback = 0) => {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}
const vec = (value: unknown): [number, number, number] => {
  const list = Array.isArray(value) ? value : []
  return [num(list[0]), num(list[1]), num(list[2])]
}

function NumberField({
  label,
  value,
  onChange,
  step = 1,
  suffix,
}: {
  label: string
  value: number
  onChange: (next: number) => void
  step?: number
  suffix?: string
}) {
  return (
    <label className="flex min-w-0 flex-1 flex-col gap-0.5">
      <span className="hud-label truncate">
        {label}
        {suffix && <span className="ml-1 text-ink-faint">{suffix}</span>}
      </span>
      <input
        type="number"
        step={step}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full border border-edge bg-void px-1.5 py-1 text-[11px] tabular-nums text-ink outline-none focus:border-cyan-hud/60"
      />
    </label>
  )
}

function VectorField({
  label,
  value,
  onChange,
  step = 100,
}: {
  label: string
  value: [number, number, number]
  onChange: (next: [number, number, number]) => void
  step?: number
}) {
  const axes = ['X east', 'Y north', 'Z up']
  return (
    <div>
      <span className="hud-label">{label}</span>
      <div className="mt-0.5 flex gap-1">
        {value.map((component, index) => (
          <input
            key={axes[index]}
            type="number"
            step={step}
            title={axes[index]}
            value={component}
            onChange={(e) => {
              const next: [number, number, number] = [...value]
              next[index] = Number(e.target.value)
              onChange(next)
            }}
            className="w-full min-w-0 border border-edge bg-void px-1.5 py-1 text-[11px] tabular-nums text-ink outline-none focus:border-cyan-hud/60"
          />
        ))}
      </div>
    </div>
  )
}

/**
 * SCENARIO EDITOR (PHASE 10).
 *
 * Edits the YAML document directly — what this form holds is what gets written
 * to disk. Validation is the backend's, using the same parser the simulation
 * uses, so the editor cannot accept a scenario the engine would refuse.
 */
export function ScenarioEditor() {
  const {
    catalogue,
    defaultName,
    loaded,
    openName,
    draft,
    existsOnDisk,
    dirty,
    validation,
    busy,
    error,
    notice,
    refresh,
    open,
    startNew,
    close,
    edit,
    save,
    clone,
    remove,
  } = useScenarioStore()

  const [cloneName, setCloneName] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  useEffect(() => {
    if (!loaded) void refresh()
  }, [loaded, refresh])

  const header = draft?.scenario
  const entities = (draft?.entities ?? []) as EntityBlock[]

  const setHeader = (patch: Partial<ScenarioDocument['scenario']>) =>
    edit((d) => ({ ...d, scenario: { ...d.scenario, ...patch } }))

  const setEntity = (index: number, patch: EntityBlock) =>
    edit((d) => {
      const next = [...d.entities]
      next[index] = { ...next[index], ...patch }
      return { ...d, entities: next }
    })

  const addEntity = () =>
    edit((d) => {
      // Name the new unit after the team it joins, following the existing ids.
      const used = new Set(d.entities.map((e) => String(e.id)))
      let index = d.entities.length + 1
      let id = `BLUE-${String(index).padStart(2, '0')}`
      while (used.has(id)) {
        index += 1
        id = `BLUE-${String(index).padStart(2, '0')}`
      }
      return {
        ...d,
        entities: [
          ...d.entities,
          {
            id,
            type: 'fictional_aircraft',
            team: 'BLUE',
            position: [0, 0, 6000],
            velocity: [220, 0, 0],
            orientation: [0, 0, 1.5708],
            controls: { throttle: 0.26 },
            agent: 'rule',
          },
        ],
      }
    })

  const removeEntity = (index: number) =>
    edit((d) => ({ ...d, entities: d.entities.filter((_, i) => i !== index) }))

  return (
    <Panel
      title="Scenario Editor"
      subtitle={
        openName
          ? `${openName}${dirty ? ' · unsaved' : ''}${existsOnDisk ? '' : ' · new'}`
          : `${catalogue.length} scenario${catalogue.length === 1 ? '' : 's'}`
      }
      actions={
        validation ? (
          validation.valid ? (
            <Check className="size-3.5 text-green-hud" strokeWidth={1.5} />
          ) : (
            <TriangleAlert className="size-3.5 text-amber-300" strokeWidth={1.5} />
          )
        ) : undefined
      }
    >
      <div className="space-y-2 px-3 py-2">
        {error && <p className="text-[10px] text-rose-400">{error}</p>}
        {notice && !error && <p className="text-[10px] text-green-hud">{notice}</p>}

        {/* --- picker ---------------------------------------------------- */}
        {!draft && (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => void startNew()}
              className="inline-flex w-full items-center justify-center gap-1 border border-cyan-hud/60 bg-cyan-hud/10 px-2 py-1.5 text-[10px] tracking-[0.15em] text-cyan-hud transition hover:bg-cyan-hud/20 disabled:opacity-40"
            >
              <FilePlus2 className="size-3.5" strokeWidth={1.5} /> NEW SCENARIO
            </button>

            <div className="space-y-1">
              {catalogue.map((entry) => (
                <div key={entry.name} className="border border-edge">
                  <button
                    type="button"
                    disabled={busy || !entry.readable}
                    onClick={() => void open(entry.name)}
                    className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left transition hover:bg-white/5 disabled:opacity-50"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-[10px] text-ink">
                        {entry.name}
                        {entry.name === defaultName && (
                          <span className="ml-1.5 text-[9px] text-cyan-hud">DEFAULT</span>
                        )}
                      </span>
                      <span className="block truncate text-[9px] text-ink-faint">
                        {entry.readable
                          ? `${entry.entity_count} units · ${entry.duration_s}s · ${(entry.teams ?? []).join('/')}`
                          : `will not load: ${entry.error}`}
                      </span>
                    </span>
                    {!entry.readable && (
                      <TriangleAlert className="size-3.5 shrink-0 text-amber-300" strokeWidth={1.5} />
                    )}
                  </button>
                  {confirmDelete === entry.name ? (
                    <div className="flex items-center gap-1 border-t border-edge/60 px-2 py-1">
                      <span className="text-[9px] text-ink-faint">Delete {entry.name}?</span>
                      <button
                        type="button"
                        onClick={() => {
                          setConfirmDelete(null)
                          void remove(entry.name)
                        }}
                        className="border border-rose-400/60 px-1.5 py-0.5 text-[9px] text-rose-400"
                      >
                        YES
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDelete(null)}
                        className="border border-edge px-1.5 py-0.5 text-[9px] text-ink-faint"
                      >
                        NO
                      </button>
                    </div>
                  ) : (
                    !entry.protected && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setConfirmDelete(entry.name)}
                        className="flex w-full items-center gap-1 border-t border-edge/60 px-2 py-0.5 text-[9px] text-ink-faint transition hover:text-rose-400 disabled:opacity-40"
                      >
                        <Trash2 className="size-3" strokeWidth={1.5} /> delete
                      </button>
                    )
                  )}
                </div>
              ))}
            </div>
          </>
        )}

        {/* --- editor ---------------------------------------------------- */}
        {draft && header && (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <span className="hud-label">editing</span>
              <button
                type="button"
                onClick={close}
                title="Close without saving"
                className="border border-edge px-1.5 py-0.5 text-ink-faint transition hover:border-ink-faint"
              >
                <X className="size-3" strokeWidth={1.5} />
              </button>
            </div>

            <label className="block">
              <span className="hud-label">name</span>
              <input
                aria-label="Scenario name"
                value={header.name}
                onChange={(e) => setHeader({ name: e.target.value })}
                className="mt-0.5 w-full border border-edge bg-void px-1.5 py-1 font-mono text-[11px] text-ink outline-none focus:border-cyan-hud/60"
              />
            </label>

            <label className="block">
              <span className="hud-label">description</span>
              <textarea
                rows={2}
                aria-label="Scenario description"
                value={header.description ?? ''}
                onChange={(e) => setHeader({ description: e.target.value })}
                className="mt-0.5 w-full resize-y border border-edge bg-void px-1.5 py-1 text-[11px] text-ink outline-none focus:border-cyan-hud/60"
              />
            </label>

            <div className="flex gap-2">
              <NumberField
                label="duration"
                suffix="s"
                step={10}
                value={num(header.duration, 300)}
                onChange={(duration) => setHeader({ duration })}
              />
              <NumberField
                label="seed"
                step={1}
                value={num(header.seed, 0)}
                onChange={(seed) => setHeader({ seed })}
              />
            </div>

            {/* --- entities --------------------------------------------- */}
            <div className="flex items-center justify-between">
              <span className="hud-label">units ({entities.length})</span>
              <button
                type="button"
                onClick={addEntity}
                aria-label="Add unit"
                className="inline-flex items-center gap-1 border border-edge px-1.5 py-0.5 text-[10px] text-ink-faint transition hover:border-cyan-hud/60 hover:text-cyan-hud"
              >
                <Plus className="size-3" strokeWidth={1.5} /> add
              </button>
            </div>

            {entities.map((entity, index) => {
              const team = String(entity.team ?? 'NEUTRAL')
              const waypoints = Array.isArray(entity.waypoints) ? entity.waypoints : []
              const formation = (entity.formation ?? null) as Record<string, unknown> | null
              return (
                <div key={index} className="space-y-2 border border-edge/70 p-2">
                  <div className="flex items-center gap-1">
                    <input
                      aria-label={`Unit ${index + 1} id`}
                      value={String(entity.id ?? '')}
                      onChange={(e) => setEntity(index, { id: e.target.value })}
                      className={`min-w-0 flex-1 border border-edge bg-void px-1.5 py-1 font-mono text-[11px] outline-none focus:border-cyan-hud/60 ${
                        team === 'RED' ? 'text-rose-400' : 'text-sky-400'
                      }`}
                    />
                    <select
                      value={team}
                      onChange={(e) => setEntity(index, { team: e.target.value })}
                      className="border border-edge bg-void px-1 py-1 text-[10px] text-ink-dim outline-none"
                    >
                      {TEAMS.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                    <select
                      value={String(entity.agent ?? 'none')}
                      onChange={(e) =>
                        setEntity(index, {
                          agent: e.target.value === 'none' ? null : e.target.value,
                        })
                      }
                      title="Which pilot flies it"
                      className="border border-edge bg-void px-1 py-1 text-[10px] text-ink-dim outline-none"
                    >
                      {AGENTS.map((a) => (
                        <option key={a} value={a}>
                          {a}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => removeEntity(index)}
                      title="Remove this unit"
                      className="border border-edge px-1 py-1 text-ink-faint transition hover:border-rose-400/60 hover:text-rose-400"
                    >
                      <Trash2 className="size-3" strokeWidth={1.5} />
                    </button>
                  </div>

                  <VectorField
                    label="position (m)"
                    value={vec(entity.position)}
                    onChange={(position) => setEntity(index, { position })}
                  />
                  <VectorField
                    label="velocity (m/s)"
                    step={10}
                    value={vec(entity.velocity)}
                    onChange={(velocity) => setEntity(index, { velocity })}
                  />
                  <div className="flex gap-2">
                    <NumberField
                      label="yaw"
                      suffix="rad"
                      step={0.1}
                      value={vec(entity.orientation)[2]}
                      onChange={(yaw) => {
                        const o = vec(entity.orientation)
                        setEntity(index, { orientation: [o[0], o[1], yaw] })
                      }}
                    />
                    <NumberField
                      label="throttle"
                      step={0.01}
                      value={num((entity.controls as Record<string, unknown>)?.throttle, 0)}
                      onChange={(throttle) =>
                        setEntity(index, {
                          controls: {
                            ...((entity.controls ?? {}) as Record<string, unknown>),
                            throttle,
                          },
                        })
                      }
                    />
                  </div>

                  <div className="flex items-center justify-between">
                    <span className="hud-label">waypoints ({waypoints.length})</span>
                    <button
                      type="button"
                      onClick={() =>
                        setEntity(index, {
                          waypoints: [...waypoints, [...vec(entity.position)]],
                        })
                      }
                      aria-label={`Add waypoint to unit ${index + 1}`}
                      className="border border-edge px-1.5 py-0.5 text-[9px] text-ink-faint transition hover:border-cyan-hud/60 hover:text-cyan-hud"
                    >
                      + add
                    </button>
                  </div>
                  {waypoints.map((waypoint, w) => (
                    <div key={w} className="flex items-end gap-1">
                      <div className="min-w-0 flex-1">
                        <VectorField
                          label={`wp ${w + 1}`}
                          value={vec(waypoint)}
                          onChange={(next) => {
                            const list = [...waypoints]
                            list[w] = next
                            setEntity(index, { waypoints: list })
                          }}
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          setEntity(index, { waypoints: waypoints.filter((_, i) => i !== w) })
                        }
                        className="border border-edge px-1 py-1 text-ink-faint transition hover:border-rose-400/60 hover:text-rose-400"
                      >
                        <Trash2 className="size-3" strokeWidth={1.5} />
                      </button>
                    </div>
                  ))}

                  <label className="block">
                    <span className="hud-label">formation leader</span>
                    <select
                      aria-label={`Unit ${index + 1} formation leader`}
                      value={String(formation?.leader ?? '')}
                      onChange={(e) =>
                        setEntity(
                          index,
                          e.target.value
                            ? {
                                formation: {
                                  leader: e.target.value,
                                  offset: (formation?.offset as number[]) ?? [-600, -600, 0],
                                },
                              }
                            : { formation: undefined },
                        )
                      }
                      className="mt-0.5 w-full border border-edge bg-void px-1.5 py-1 text-[10px] text-ink-dim outline-none"
                    >
                      <option value="">— none (flies its own route) —</option>
                      {entities
                        .filter((_, i) => i !== index)
                        .map((other) => (
                          <option key={String(other.id)} value={String(other.id)}>
                            {String(other.id)}
                          </option>
                        ))}
                    </select>
                  </label>
                  {formation?.leader != null && (
                    <VectorField
                      label="station offset (m)"
                      step={50}
                      value={vec(formation.offset)}
                      onChange={(offset) =>
                        setEntity(index, { formation: { ...formation, offset } })
                      }
                    />
                  )}
                </div>
              )
            })}

            {/* --- verdict and actions ---------------------------------- */}
            {validation && !validation.valid && (
              <p className="border border-amber-300/40 bg-amber-300/5 px-2 py-1.5 text-[10px] text-amber-300">
                {validation.error}
              </p>
            )}
            {validation?.valid && (
              <p className="text-[10px] text-green-hud">
                Valid · {validation.scenario.entity_count} units ·{' '}
                {validation.scenario.duration_s}s
              </p>
            )}

            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                disabled={busy || !validation?.valid}
                onClick={() => void save()}
                title={
                  validation?.valid ? 'Write this scenario to disk' : 'Fix the error above first'
                }
                className="inline-flex items-center gap-1 border border-cyan-hud/60 bg-cyan-hud/10 px-2 py-1 text-[10px] text-cyan-hud transition hover:bg-cyan-hud/20 disabled:opacity-40"
              >
                <Save className="size-3" strokeWidth={1.5} /> SAVE
              </button>
              {existsOnDisk && (
                <div className="flex items-center gap-1">
                  <input
                    value={cloneName}
                    placeholder="copy name"
                    onChange={(e) => setCloneName(e.target.value)}
                    className="w-28 border border-edge bg-void px-1.5 py-1 font-mono text-[10px] text-ink outline-none focus:border-cyan-hud/60"
                  />
                  <button
                    type="button"
                    disabled={busy || !cloneName}
                    onClick={() => {
                      void clone(cloneName)
                      setCloneName('')
                    }}
                    className="inline-flex items-center gap-1 border border-edge px-2 py-1 text-[10px] text-ink-faint transition hover:border-cyan-hud/60 hover:text-cyan-hud disabled:opacity-40"
                  >
                    <Copy className="size-3" strokeWidth={1.5} /> CLONE
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}
