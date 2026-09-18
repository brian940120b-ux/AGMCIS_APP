import { useEffect, useState } from 'react'
import { ArrowRight, TriangleAlert } from 'lucide-react'
import { StateBadge } from '@/components/StateBadge'
import { useSystemStore } from '@/stores/systemStore'

/**
 * First-run experience: the platform reports which subsystems actually came up
 * before handing over to the Command Center. Every line is a real backend
 * response — nothing is staged.
 */
export function BootScreen({ onEnter }: { onEnter: () => void }) {
  const status = useSystemStore((s) => s.status)
  const health = useSystemStore((s) => s.health)
  const connection = useSystemStore((s) => s.connection)
  const error = useSystemStore((s) => s.error)

  // Reveal subsystems progressively for readability; purely presentational.
  const [revealed, setRevealed] = useState(0)
  const subsystems = status?.subsystems ?? []

  useEffect(() => {
    if (subsystems.length === 0) return
    setRevealed(0)
    const timer = window.setInterval(() => {
      setRevealed((n) => {
        if (n >= subsystems.length) {
          window.clearInterval(timer)
          return n
        }
        return n + 1
      })
    }, 70)
    return () => window.clearInterval(timer)
  }, [subsystems.length])

  const ready = connection === 'online' && revealed >= subsystems.length && subsystems.length > 0

  return (
    <main className="bg-tactical-grid flex min-h-dvh items-center justify-center p-4 sm:p-8">
      <div className="hud-panel w-full max-w-2xl p-6 sm:p-10">
        <div className="text-center">
          <h1 className="text-3xl font-light tracking-[0.35em] text-cyan-hud sm:text-5xl">AIFCS</h1>
          <p className="mt-3 text-[10px] leading-relaxed tracking-[0.2em] text-ink-dim sm:text-xs">
            AI FLIGHT &amp; MULTI-AGENT SIMULATION PLATFORM
          </p>
          <p className="mt-2 text-[10px] text-ink-faint">
            Research · Education · Fictional entities only
          </p>
        </div>

        <div className="mt-8 border-t border-edge pt-5">
          <p className="hud-label mb-3">
            {connection === 'error' ? 'Initialization failed' : 'System initializing…'}
          </p>

          {error && (
            <div className="flex items-start gap-2 border border-red-force/40 bg-red-force/5 p-3 text-[11px] text-red-force">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              <div>
                <p>{error}</p>
                <p className="mt-2 text-ink-faint">
                  Start it with:{' '}
                  <code className="text-ink-dim">cd AIFCS/backend &amp;&amp; uvicorn main:app --reload</code>
                </p>
              </div>
            </div>
          )}

          <ul className="space-y-1">
            {subsystems.slice(0, revealed).map((sub) => (
              <li key={sub.key} className="flex items-center justify-between gap-3 py-1 text-xs">
                <span className="truncate text-ink-dim">{sub.label}</span>
                <span className="h-px flex-1 bg-edge" />
                <StateBadge state={sub.state} pulse />
              </li>
            ))}
          </ul>
        </div>

        <div className="mt-8 flex flex-col items-center gap-3">
          <button
            type="button"
            onClick={onEnter}
            disabled={!ready}
            className="group flex w-full items-center justify-center gap-3 border border-cyan-hud/50 bg-cyan-hud/5 px-6 py-3 text-xs tracking-[0.25em] text-cyan-hud transition hover:bg-cyan-hud/15 disabled:cursor-not-allowed disabled:border-edge disabled:bg-transparent disabled:text-ink-faint sm:w-auto"
          >
            ENTER COMMAND CENTER
            <ArrowRight className="size-4 transition group-enabled:group-hover:translate-x-1" />
          </button>
          {health && (
            <p className="text-[10px] text-ink-faint">
              backend v{health.version} · config {health.config_hash}
            </p>
          )}
        </div>
      </div>
    </main>
  )
}
