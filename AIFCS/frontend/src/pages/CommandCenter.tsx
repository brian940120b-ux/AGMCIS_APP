import { Suspense, lazy, useState } from 'react'
import { Activity, Cpu, Film, LayoutGrid, PencilRuler, Radio as RadioIcon } from 'lucide-react'
import { DecisionFeed } from '@/components/DecisionFeed'
import { DatalinkPanel } from '@/components/DatalinkPanel'
import { EntityList } from '@/components/EntityList'
import { EventFeed } from '@/components/EventFeed'
import { IntelPanel } from '@/components/IntelPanel'
import { PerceptionPanel } from '@/components/PerceptionPanel'
import { ReplayPanel } from '@/components/ReplayPanel'
import { ScenarioEditor } from '@/components/ScenarioEditor'
import { ScenarioYamlPanel } from '@/components/ScenarioYamlPanel'
import { ScoreboardPanel } from '@/components/ScoreboardPanel'
import { SafetyPanel } from '@/components/SafetyPanel'
import { SimulationControls } from '@/components/SimulationControls'
import { StateBadge } from '@/components/StateBadge'
import { SystemStatusPanel } from '@/components/SystemStatusPanel'
import { TacticalPlot } from '@/components/TacticalPlot'
import { useIsDesktop } from '@/hooks/useIsDesktop'
import { useSimulationPolling } from '@/hooks/useSimulationPolling'
import { useTelemetrySocket } from '@/hooks/useTelemetrySocket'
import { useStageStore } from '@/stores/stageStore'
import { useSimulationStore } from '@/stores/simulationStore'
import { useSystemStore } from '@/stores/systemStore'

// Three.js is by far the largest dependency here. Loading the 3D view on demand
// keeps it out of the initial bundle for anyone who stays on the 2D plot.
const SimulationViewer3D = lazy(() =>
  import('@/components/SimulationViewer3D').then((m) => ({
    default: m.SimulationViewer3D,
  })),
)

function ViewerLoading() {
  return (
    <div className="hud-panel grid h-full min-h-[320px] place-items-center">
      <p className="text-[11px] text-ink-faint">Loading tactical view…</p>
    </div>
  )
}

type MobileTab = 'view' | 'status' | 'units' | 'replay' | 'edit' | 'intel'

const TABS: { id: MobileTab; label: string; icon: typeof Activity }[] = [
  { id: 'view', label: 'View', icon: LayoutGrid },
  { id: 'status', label: 'Status', icon: Activity },
  { id: 'units', label: 'Units', icon: RadioIcon },
  { id: 'replay', label: 'Replay', icon: Film },
  { id: 'edit', label: 'Edit', icon: PencilRuler },
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
  // The 3D view is the default stage; the top-down plot stays one click away.
  const [view, setView] = useState<'3d' | '2d'>('3d')
  const isDesktop = useIsDesktop()
  // Which source the tactical views draw: live telemetry or a recording.
  const stageMode = useStageStore((s) => s.mode)
  const setStageMode = useStageStore((s) => s.setMode)
  useTelemetrySocket()
  useSimulationPolling()

  const health = useSystemStore((s) => s.health)
  const systemStatus = useSystemStore((s) => s.status)
  const connection = useSystemStore((s) => s.connection)
  const simStatus = useSimulationStore((s) => s.status)
  const transport = useSimulationStore((s) => s.transport)
  const framesReceived = useSimulationStore((s) => s.framesReceived)

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
          {/* LIVE / REPLAY. The views draw one source or the other, never a
              mix of the two. */}
          <div className="flex items-center border border-edge">
            {(['live', 'replay', 'edit'] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setStageMode(value)}
                className={`px-2 py-0.5 text-[10px] tracking-[0.15em] transition ${
                  stageMode === value
                    ? value === 'replay'
                      ? 'bg-amber-300/15 text-amber-300'
                      : value === 'edit'
                        ? 'bg-violet-300/15 text-violet-300'
                        : 'bg-cyan-hud/10 text-cyan-hud'
                    : 'text-ink-faint hover:text-ink-dim'
                }`}
              >
                {value.toUpperCase()}
              </button>
            ))}
          </div>
          <span className="hidden text-[10px] text-ink-faint sm:inline">
            {systemStatus?.phase}
          </span>
          {simStatus?.deterministic && (
            <span className="hidden text-[10px] text-ink-faint md:inline">
              seed {simStatus.seed}
            </span>
          )}
          {/* Transport: telemetry is pushed when the socket is up, polled when
              it is not. Showing which makes a degraded dashboard obvious. */}
          <StateBadge
            state={
              connection === 'error'
                ? 'ERROR'
                : transport === 'live'
                  ? 'ONLINE'
                  : transport === 'polling'
                    ? 'WARNING'
                    : 'OFFLINE'
            }
            label={
              connection === 'error'
                ? 'BACKEND OFFLINE'
                : transport === 'live'
                  ? `LIVE · ${framesReceived} FRAMES`
                  : transport === 'polling'
                    ? 'POLLING'
                    : 'CONNECTING'
            }
            pulse
          />
        </div>
      </header>

      {/* Desktop / tablet. Rendered only when it applies, so the 3D view
          never creates a second, invisible WebGL context. */}
      {isDesktop && (
        <div className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[300px_1fr_330px]">
          {/* Telemetry rail. Panels keep their natural height and the rail
            scrolls, rather than every panel being squeezed as more are added. */}
          <div className="flex min-h-0 flex-col gap-3 overflow-y-auto pr-1 [&>*]:shrink-0">
            <SystemStatusPanel />
            <EntityList />
            <PerceptionPanel />
            <DatalinkPanel />
            <SafetyPanel />
          </div>

          <div className="flex min-h-0 flex-col gap-3">
            {stageMode === 'replay' ? (
              <ReplayPanel />
            ) : stageMode === 'edit' ? (
              <ScenarioYamlPanel />
            ) : (
              <SimulationControls />
            )}
            <div className="min-h-0 flex-1">
              {view === '3d' ? (
                <Suspense fallback={<ViewerLoading />}>
                  <SimulationViewer3D onSwitchTo2D={() => setView('2d')} />
                </Suspense>
              ) : (
                <TacticalPlot onSwitchTo3D={() => setView('3d')} />
              )}
            </div>
          </div>

          <div className="flex min-h-0 flex-col gap-3">
            {stageMode === 'edit' ? (
              <div className="min-h-0 flex-1">
                <ScenarioEditor />
              </div>
            ) : stageMode === 'replay' ? (
              <div className="min-h-0 flex-1">
                <ScoreboardPanel />
              </div>
            ) : (
              <>
                <IntelPanel />
                {/* The decision feed carries more per entry, so it gets the larger share. */}
                <div className="min-h-0 flex-[3]">
                  <DecisionFeed />
                </div>
                <div className="min-h-0 flex-[2]">
                  <EventFeed />
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Mobile: one focused panel, controls always reachable on the view tab */}
      {!isDesktop && (
        <main className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
          {tab === 'view' && (
            <>
              {stageMode === 'replay' ? (
                <ReplayPanel />
              ) : stageMode === 'edit' ? (
                <ScenarioYamlPanel />
              ) : (
                <SimulationControls />
              )}
              <div className="h-[55vh]">
                {view === '3d' ? (
                  <Suspense fallback={<ViewerLoading />}>
                    <SimulationViewer3D onSwitchTo2D={() => setView('2d')} />
                  </Suspense>
                ) : (
                  <TacticalPlot onSwitchTo3D={() => setView('3d')} />
                )}
              </div>
            </>
          )}
          {tab === 'status' && (
            <>
              <SystemStatusPanel />
              <PerceptionPanel />
              <DatalinkPanel />
              <SafetyPanel />
            </>
          )}
          {tab === 'units' && (
            <>
              <EntityList />
              <DecisionFeed />
              <EventFeed />
            </>
          )}
          {tab === 'replay' && (
            <>
              <ReplayPanel />
              <ScoreboardPanel />
            </>
          )}
          {tab === 'edit' && (
            <>
              <ScenarioEditor />
              <ScenarioYamlPanel />
            </>
          )}
          {tab === 'intel' && <IntelPanel />}
        </main>
      )}

      {!isDesktop && (
        <nav className="grid shrink-0 grid-cols-6 border-t border-edge bg-deck">
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
      )}

      {isDesktop && (
        <footer className="flex shrink-0 items-center justify-between border-t border-edge bg-deck px-4 py-1.5 text-[10px] text-ink-faint">
          <span>
            All entities, platforms and parameters are fictional — research and
            education use only.
          </span>
          <span>
            {simStatus ? `state ${simStatus.state_hash} · ` : ''}
            {health ? `v${health.version} · cfg ${health.config_hash}` : '—'}
          </span>
        </footer>
      )}
    </div>
  )
}
