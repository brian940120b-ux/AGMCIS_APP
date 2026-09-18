import { useState } from 'react'
import { Activity, Cpu, LayoutGrid } from 'lucide-react'
import { IntelPanel } from '@/components/IntelPanel'
import { SimulationViewport } from '@/components/SimulationViewport'
import { SystemStatusPanel } from '@/components/SystemStatusPanel'
import { StateBadge } from '@/components/StateBadge'
import { useSystemStore } from '@/stores/systemStore'

type MobileTab = 'view' | 'status' | 'intel'

const TABS: { id: MobileTab; label: string; icon: typeof Activity }[] = [
  { id: 'view', label: 'View', icon: LayoutGrid },
  { id: 'status', label: 'Status', icon: Activity },
  { id: 'intel', label: 'Intel', icon: Cpu },
]

/**
 * COMMAND CENTER.
 *
 * Desktop: three-column tactical layout.
 * Mobile: one panel at a time with bottom navigation — not a shrunken desktop.
 */
export function CommandCenter() {
  const [tab, setTab] = useState<MobileTab>('view')
  const health = useSystemStore((s) => s.health)
  const status = useSystemStore((s) => s.status)
  const connection = useSystemStore((s) => s.connection)

  return (
    <div className="flex h-dvh flex-col bg-void">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-edge bg-deck px-3 py-2 sm:px-4">
        <div className="flex min-w-0 items-baseline gap-3">
          <span className="text-sm tracking-[0.3em] text-cyan-hud">AIFCS</span>
          <span className="hidden truncate text-[10px] tracking-[0.15em] text-ink-faint sm:inline">
            COMMAND CENTER
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="hidden text-[10px] text-ink-faint sm:inline">{status?.phase}</span>
          <StateBadge
            state={connection === 'online' ? 'ONLINE' : connection === 'error' ? 'ERROR' : 'OFFLINE'}
            label={connection === 'online' ? 'BACKEND ONLINE' : connection === 'error' ? 'BACKEND OFFLINE' : 'CONNECTING'}
            pulse
          />
        </div>
      </header>

      {/* Desktop / tablet: full command layout. */}
      <div className="hidden min-h-0 flex-1 gap-3 p-3 lg:grid lg:grid-cols-[300px_1fr_330px]">
        <SystemStatusPanel />
        <SimulationViewport />
        <IntelPanel />
      </div>

      {/* Mobile: single focused panel. */}
      <main className="min-h-0 flex-1 overflow-y-auto p-3 lg:hidden">
        {tab === 'view' && <SimulationViewport />}
        {tab === 'status' && <SystemStatusPanel />}
        {tab === 'intel' && <IntelPanel />}
      </main>

      <nav className="grid shrink-0 grid-cols-3 border-t border-edge bg-deck lg:hidden">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`flex flex-col items-center gap-1 py-2.5 text-[10px] tracking-[0.15em] transition ${
              tab === id ? 'text-cyan-hud' : 'text-ink-faint'
            }`}
          >
            <Icon className="size-4" strokeWidth={1.5} />
            {label.toUpperCase()}
          </button>
        ))}
      </nav>

      <footer className="hidden shrink-0 items-center justify-between border-t border-edge bg-deck px-4 py-1.5 text-[10px] text-ink-faint lg:flex">
        <span>All entities, platforms and parameters are fictional — research and education use only.</span>
        <span>{health ? `v${health.version} · cfg ${health.config_hash}` : '—'}</span>
      </footer>
    </div>
  )
}
