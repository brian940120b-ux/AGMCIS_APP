import { useEffect } from 'react'
import {
  ChevronFirst,
  ChevronLast,
  ChevronLeft,
  ChevronRight,
  Film,
  Pause,
  Play,
  X,
} from 'lucide-react'
import { Panel } from '@/components/Panel'
import { useReplayStore } from '@/stores/replayStore'

const SPEEDS = [0.25, 0.5, 1.0, 2.0, 5.0, 10.0]

const bytes = (n: number) =>
  n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} kB`

const clock = (seconds: number) => {
  const total = Math.max(0, Math.floor(seconds))
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

/**
 * REPLAY TRANSPORT (PHASE 9).
 *
 * Every control moves the real cursor on the server. The scrubber reports the
 * frame the player is actually on, not a local animation of where it should
 * be, so dragging it and watching the view are always in agreement.
 */
export function ReplayPanel() {
  const {
    recordings,
    recordingsLoaded,
    status,
    markers,
    busy,
    error,
    notice,
    loadRecordings,
    open,
    close,
    play,
    pause,
    step,
    seek,
    setSpeed,
    jump,
  } = useReplayStore()

  useEffect(() => {
    if (!recordingsLoaded) void loadRecordings()
  }, [recordingsLoaded, loadRecordings])

  const loaded = status.loaded
  const frameCount = status.frame_count ?? 0
  const frameIndex = status.frame_index ?? 0
  const lastFrame = Math.max(0, frameCount - 1)

  return (
    <Panel
      title="Replay"
      subtitle={loaded ? 'reading a recording back' : 'pick a recording to review'}
      actions={<Film className="size-3.5 text-cyan-hud" strokeWidth={1.5} />}
    >
      <div className="space-y-2 px-3 py-2">
      {error && <p className="mb-2 text-[10px] text-rose-400">{error}</p>}
      {notice && !error && <p className="mb-2 text-[10px] text-amber-300">{notice}</p>}

      {/* --- recording picker ------------------------------------------- */}
      {!loaded && (
        <div className="space-y-1.5">
          {!recordingsLoaded && <p className="text-[10px] text-ink-faint">Reading recordings…</p>}
          {recordingsLoaded && recordings.length === 0 && (
            <p className="text-[10px] text-ink-faint">
              No recordings yet. Start a simulation and stop it — the run is recorded
              automatically.
            </p>
          )}
          {recordings.slice(0, 8).map((recording) => (
            <button
              key={recording.run_id}
              type="button"
              disabled={busy || !recording.readable}
              onClick={() => void open(recording.run_id)}
              className="flex w-full items-center justify-between gap-2 border border-edge px-2 py-1.5 text-left transition hover:border-cyan-hud/60 disabled:opacity-40"
            >
              <span className="min-w-0">
                <span className="block truncate font-mono text-[10px] text-ink">
                  {recording.run_id}
                </span>
                <span className="block truncate text-[9px] text-ink-faint">
                  {recording.readable
                    ? `${recording.scenario ?? 'unknown'} · seed ${recording.seed ?? '—'}`
                    : `unreadable: ${recording.error ?? 'bad header'}`}
                </span>
              </span>
              <span className="shrink-0 text-[9px] text-ink-faint">{bytes(recording.size_bytes)}</span>
            </button>
          ))}
        </div>
      )}

      {/* --- transport --------------------------------------------------- */}
      {loaded && (
        <div className="space-y-2">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate font-mono text-[10px] text-cyan-hud">
                {status.recording?.run_id}
              </p>
              <p className="truncate text-[9px] text-ink-faint">
                {status.recording?.scenario} · seed {status.recording?.seed} ·{' '}
                {status.recording?.record_rate_hz}
                &nbsp;Hz
                {status.recording?.truncated && ' · TRUNCATED'}
                {status.recording && !status.recording.complete && ' · INCOMPLETE'}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void close()}
              title="Close this recording"
              className="shrink-0 border border-edge px-1.5 py-0.5 text-[10px] text-ink-faint transition hover:border-ink-faint"
            >
              <X className="size-3" strokeWidth={1.5} />
            </button>
          </div>

          {/* Scrubber. The range input is the cursor: it reports the server's
              frame index and setting it seeks there for real. */}
          <div>
            <input
              type="range"
              min={0}
              max={lastFrame}
              value={frameIndex}
              disabled={busy}
              aria-label="Replay position"
              onChange={(e) => void seek(Number(e.target.value))}
              className="h-1 w-full cursor-pointer appearance-none bg-edge accent-cyan-hud"
            />
            <div className="mt-1 flex items-center justify-between text-[9px] text-ink-faint">
              <span>{clock(status.simulation_time ?? 0)}</span>
              <span>
                frame {frameIndex + 1} / {frameCount} · tick {status.tick ?? 0}
              </span>
              <span>{clock(status.duration_s ?? 0)}</span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1">
            <TransportButton label="Start" onClick={() => void seek(0)} disabled={busy}>
              <ChevronFirst className="size-3.5" strokeWidth={1.5} />
            </TransportButton>
            <TransportButton label="Back one frame" onClick={() => void step(-1)} disabled={busy}>
              <ChevronLeft className="size-3.5" strokeWidth={1.5} />
            </TransportButton>

            <button
              type="button"
              disabled={busy}
              onClick={() => void (status.playing ? pause() : play())}
              className="inline-flex items-center gap-1 border border-cyan-hud/60 bg-cyan-hud/10 px-2.5 py-1 text-[10px] text-cyan-hud transition hover:bg-cyan-hud/20 disabled:opacity-40"
            >
              {status.playing ? (
                <>
                  <Pause className="size-3.5" strokeWidth={1.5} /> PAUSE
                </>
              ) : (
                <>
                  <Play className="size-3.5" strokeWidth={1.5} /> PLAY
                </>
              )}
            </button>

            <TransportButton label="Forward one frame" onClick={() => void step(1)} disabled={busy}>
              <ChevronRight className="size-3.5" strokeWidth={1.5} />
            </TransportButton>
            <TransportButton label="End" onClick={() => void seek(lastFrame)} disabled={busy}>
              <ChevronLast className="size-3.5" strokeWidth={1.5} />
            </TransportButton>
          </div>

          <div className="flex flex-wrap items-center gap-1">
            <span className="hud-label mr-1">speed</span>
            {SPEEDS.map((speed) => (
              <button
                key={speed}
                type="button"
                disabled={busy}
                onClick={() => void setSpeed(speed)}
                className={`border px-1.5 py-0.5 text-[10px] transition disabled:opacity-40 ${
                  status.speed === speed
                    ? 'border-cyan-hud/60 bg-cyan-hud/10 text-cyan-hud'
                    : 'border-edge text-ink-faint hover:border-ink-faint'
                }`}
              >
                {speed}x
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-1">
            <span className="hud-label mr-1">events</span>
            <button
              type="button"
              disabled={busy || markers.length === 0}
              onClick={() => void jump(-1)}
              className="border border-edge px-1.5 py-0.5 text-[10px] text-ink-faint transition hover:border-ink-faint disabled:opacity-40"
            >
              ‹ prev
            </button>
            <button
              type="button"
              disabled={busy || markers.length === 0}
              onClick={() => void jump(1)}
              className="border border-edge px-1.5 py-0.5 text-[10px] text-ink-faint transition hover:border-ink-faint disabled:opacity-40"
            >
              next ›
            </button>
            <span className="text-[9px] text-ink-faint">
              {markers.length === 0 ? 'none in this recording' : `${markers.length} marked`}
            </span>
          </div>

          <p className="text-[9px] text-ink-faint">
            state {status.state_hash ?? '—'}
            {status.at_end && ' · at end'}
          </p>
        </div>
      )}
      </div>
    </Panel>
  )
}

function TransportButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center border border-edge px-1.5 py-1 text-ink-faint transition hover:border-ink-faint disabled:opacity-40"
    >
      {children}
    </button>
  )
}
