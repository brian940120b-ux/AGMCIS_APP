import { Pause, Play, RotateCcw, SkipForward } from 'lucide-react'
import { useSimulationStore } from '@/stores/simulationStore'
import { useSystemStore } from '@/stores/systemStore'

/** Format simulation seconds as mm:ss.d */
function formatSimTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${rest.toFixed(1).padStart(4, '0')}`
}

export function SimulationControls() {
  const status = useSimulationStore((s) => s.status)
  const busy = useSimulationStore((s) => s.busy)
  const error = useSimulationStore((s) => s.error)
  const scenarios = useSimulationStore((s) => s.scenarios)
  const selected = useSimulationStore((s) => s.selectedScenario)
  const selectScenario = useSimulationStore((s) => s.selectScenario)
  const start = useSimulationStore((s) => s.start)
  const pause = useSimulationStore((s) => s.pause)
  const resume = useSimulationStore((s) => s.resume)
  const reset = useSimulationStore((s) => s.reset)
  const step = useSimulationStore((s) => s.step)
  const setSpeed = useSimulationStore((s) => s.setSpeed)

  const speeds = useSystemStore((s) => s.config?.simulation.allowed_speeds) ?? [1]

  const clock = status?.clock
  const state = clock?.state ?? 'STOPPED'
  const running = state === 'RUNNING'
  const paused = state === 'PAUSED'

  return (
    <div className="hud-panel flex flex-col gap-3 p-3">
      {/* Clock readout — the proof that simulation time is real. */}
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex items-baseline gap-3">
          <span className="text-2xl tabular-nums text-cyan-hud">
            {formatSimTime(clock?.simulation_time ?? 0)}
          </span>
          <span className="hud-label">sim time</span>
        </div>
        <div className="flex items-center gap-3 text-[10px] text-ink-faint">
          <span>tick {clock?.tick ?? 0}</span>
          <span>{clock?.tick_rate_hz ?? 60} Hz</span>
          {/* Achieved speed. A high multiplier the host cannot sustain shows
              up here as a factor below the requested one. */}
          {clock && clock.realtime_factor > 0 && (
            <span
              title="Achieved simulation seconds per real second"
              className={
                clock.realtime_factor < clock.speed * 0.9 ? 'text-amber-hud' : 'text-ink-faint'
              }
            >
              {clock.realtime_factor.toFixed(1)}x actual
            </span>
          )}
          <span className={running ? 'text-green-hud' : paused ? 'text-amber-hud' : 'text-ink-faint'}>
            {state}
          </span>
        </div>
      </div>

      {/* Transport controls. Each one calls the engine; none is decorative. */}
      <div className="flex flex-wrap gap-2">
        {running ? (
          <button type="button" onClick={() => void pause()} disabled={busy} className={buttonClass}>
            <Pause className="size-3.5" /> PAUSE
          </button>
        ) : paused ? (
          <button type="button" onClick={() => void resume()} disabled={busy} className={buttonClass}>
            <Play className="size-3.5" /> RESUME
          </button>
        ) : (
          <button type="button" onClick={() => void start()} disabled={busy} className={primaryButtonClass}>
            <Play className="size-3.5" /> START
          </button>
        )}

        <button
          type="button"
          onClick={() => void step(60)}
          disabled={busy || running}
          title="Advance 60 ticks (1 simulation second)"
          className={buttonClass}
        >
          <SkipForward className="size-3.5" /> STEP 1s
        </button>

        <button type="button" onClick={() => void reset()} disabled={busy} className={buttonClass}>
          <RotateCcw className="size-3.5" /> RESET
        </button>
      </div>

      {/* Speed selector — pacing only; the timestep never changes. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="hud-label mr-1">speed</span>
        {speeds.map((speed) => (
          <button
            key={speed}
            type="button"
            onClick={() => void setSpeed(speed)}
            disabled={busy}
            className={`border px-2 py-0.5 text-[10px] transition disabled:opacity-40 ${
              clock?.speed === speed
                ? 'border-cyan-hud/60 bg-cyan-hud/10 text-cyan-hud'
                : 'border-edge text-ink-faint hover:border-ink-faint'
            }`}
          >
            {speed}x
          </button>
        ))}
      </div>

      {/* Scenario selector */}
      {scenarios.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="hud-label mr-1">scenario</span>
          {scenarios.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => selectScenario(name)}
              disabled={busy || running}
              className={`border px-2 py-0.5 text-[10px] transition disabled:opacity-40 ${
                selected === name
                  ? 'border-cyan-hud/60 bg-cyan-hud/10 text-cyan-hud'
                  : 'border-edge text-ink-faint hover:border-ink-faint'
              }`}
            >
              {name}
            </button>
          ))}
        </div>
      )}

      {error && <p className="border border-red-force/40 bg-red-force/5 p-2 text-[10px] text-red-force">{error}</p>}
    </div>
  )
}

const buttonClass =
  'inline-flex items-center gap-1.5 border border-edge px-3 py-1.5 text-[10px] tracking-[0.12em] text-ink-dim transition hover:border-ink-faint hover:text-ink disabled:cursor-not-allowed disabled:opacity-40'

const primaryButtonClass =
  'inline-flex items-center gap-1.5 border border-cyan-hud/50 bg-cyan-hud/10 px-3 py-1.5 text-[10px] tracking-[0.12em] text-cyan-hud transition hover:bg-cyan-hud/20 disabled:cursor-not-allowed disabled:opacity-40'
