import { useState } from 'react'
import { Activity, Cpu, LayoutGrid, Radio } from 'lucide-react'
import { EntityList } from '@/components/EntityList'
import { EventFeed } from '@/components/EventFeed'
import { IntelPanel } from '@/components/IntelPanel'
import { SimulationControls } from '@/components/SimulationControls'
import { StateBadge } from '@/components/StateBadge'
import { SystemStatusPanel } from '@/components/SystemStatusPanel'
import { TacticalPlot } from '@/components/TacticalPlot'
import { useSimulationPolling } from '@/hooks/useSimulationPolling'
import { useSimulationStore } from '@/stores/simulationStore'
import { useSystemStore } from '@/stores/systemStore'

type MobileTab = 'view' | 'status' | 'units' | 'intel'

const TABS: { id: MobileTab; label: string; icon: typeof Activity }[] = [
  { id: 'view', label: 'View', icon: LayoutGrid },
  { id: 'status', label: 'Status', icon: Activity },
  { id: 'units', label: 'Units', icon: Radio },
  { id: 'intel', label: 'Intel', icon: Cpu },
]

/**
 * COMMAND CENTER.
 *
 * Desktop: three-column tactical layout with live transport controls.
 * Mobile: one panel at a time with bottom navigation — not a shrunken desktop.
 */
export function CommandCenter() {
  const [tab, setTab] = useState<MobileTab>('view')
  useSimulationPolling()

  const health = useSystemStore((s) => s.health)
  const systemStatus = useSystemStore((s) => s.status)
  const connection = useSystemStore((s) => s.connection)
  const simStatus = useSimulationStore((s) => s.status)

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
          <span className="hidden text-[10px] text-ink-faint sm:inline">{systemStatus?.phase}</span>
          {simStatus?.deterministic && (
            <span className="hidden text-[10px] text-ink-faint md:inline">
              seed {simStatus.seed}
            </span>
          )}
          <StateBadge
            state={connection === 'online' ? 'ONLINE' : connection === 'error' ? 'ERROR' : 'OFFLINE'}
            label={
              connection === 'online'
                ? 'BACKEND ONLINE'
                : connection === 'error'
                  ? 'BACKEND OFFLINE'
                  : 'CONNECTING'
            }
            pulse
          />
        </div>
      </header>

      {/* Desktop / tablet */}
      <div className="hidden min-h-0 flex-1 gap-3 p-3 lg:grid lg:grid-cols-[300px_1fr_330px]">
        <div className="flex min-h-0 flex-col gap-3">
          <SystemStatusPanel />
          <EntityList />
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          <SimulationControls />
          <div className="min-h-0 flex-1">
            <TacticalPlot />
          </div>
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          <IntelPanel />
          <div className="min-h-0 flex-1">
            <EventFeed />
          </div>
        </div>
      </div>

      {/* Mobile: one focused panel, controls always reachable on the view tab */}
      <main className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 lg:hidden">
        {tab === 'view' && (
          <>
            <SimulationControls />
            <div className="h-[55vh]">
              <TacticalPlot />
            </div>
          </>
        )}
        {tab === 'status' && <SystemStatusPanel />}
        {tab === 'units' && (
          <>
            <EntityList />
            <EventFeed />
          </>
        )}
        {tab === 'intel' && <IntelPanel />}
      </main>

      <nav className="grid shrink-0 grid-cols-4 border-t border-edge bg-deck lg:hidden">
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
        <span>
          All entities, platforms and parameters are fictional — research and education use only.
        </span>
        <span>
          {simStatus ? `state ${simStatus.state_hash} · ` : ''}
          {health ? `v${health.version} · cfg ${health.config_hash}` : '—'}
        </span>
      </footer>
    </div>
  )
}
