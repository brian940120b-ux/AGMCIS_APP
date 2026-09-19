import { useState } from 'react'
import { Canvas } from '@react-three/fiber'
import { Box, Crosshair, Move3d, Orbit, Square } from 'lucide-react'
import type { CameraMode } from '@/three/CameraRig'
import { Scene } from '@/three/Scene'
import { useStage } from '@/hooks/useStage'

const CAMERA_MODES: { id: CameraMode; label: string; icon: typeof Orbit }[] = [
  { id: 'orbit', label: 'Orbit', icon: Orbit },
  { id: 'follow', label: 'Follow', icon: Crosshair },
  { id: 'top', label: 'Top', icon: Square },
  { id: 'side', label: 'Side', icon: Move3d },
]

/**
 * 3D tactical view (PHASE 8, replay-aware in PHASE 9).
 *
 * Draws whatever is on stage — the live telemetry stream, or a recording being
 * played back. The browser draws; it does not simulate. Every position here
 * was reported by the engine, live or recorded.
 *
 * The units are abstract delta forms, not models of any real aircraft, and
 * there is no weapon or targeting representation anywhere in this view.
 */
export function SimulationViewer3D({
  onSwitchTo2D,
}: {
  onSwitchTo2D: () => void
}) {
  // Live telemetry or a recording being played back — never a blend of both.
  const { entities, trails, scenario, mode, label: stageLabel, emptyHint } = useStage()

  const [cameraMode, setCameraMode] = useState<CameraMode>('orbit')
  const [followId, setFollowId] = useState<string | null>(null)

  const followTarget = followId ?? entities[0]?.id ?? null

  return (
    <div className="hud-panel relative flex h-full min-h-[320px] flex-col overflow-hidden">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-edge px-3 py-2">
        <div>
          <h2 className="hud-label text-ink-dim">
            Tactical View
            {mode === 'replay' && <span className="ml-2 text-amber-300">REPLAY</span>}
            {mode === 'edit' && <span className="ml-2 text-violet-300">PREVIEW</span>}
          </h2>
          <p className="text-[10px] text-ink-faint">
            {scenario ?? 'no scenario'} · {entities.length} units · abstract 3D
            {mode !== 'live' && ` · ${stageLabel}`}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {CAMERA_MODES.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setCameraMode(id)}
              className={`inline-flex items-center gap-1 border px-2 py-0.5 text-[10px] transition ${
                cameraMode === id
                  ? 'border-cyan-hud/60 bg-cyan-hud/10 text-cyan-hud'
                  : 'border-edge text-ink-faint hover:border-ink-faint'
              }`}
            >
              <Icon className="size-3" strokeWidth={1.5} />
              {label}
            </button>
          ))}
          <button
            type="button"
            onClick={onSwitchTo2D}
            title="Switch to the top-down plot"
            className="inline-flex items-center gap-1 border border-edge px-2 py-0.5 text-[10px] text-ink-faint transition hover:border-ink-faint"
          >
            <Box className="size-3" strokeWidth={1.5} />
            2D
          </button>
        </div>
      </header>

      {/* Unit selector, only meaningful in follow mode. */}
      {cameraMode === 'follow' && entities.length > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-edge/60 px-3 py-1.5">
          <span className="hud-label mr-1">follow</span>
          {entities.map((entity) => (
            <button
              key={entity.id}
              type="button"
              onClick={() => setFollowId(entity.id)}
              className={`border px-2 py-0.5 text-[10px] transition ${
                followTarget === entity.id
                  ? 'border-cyan-hud/60 bg-cyan-hud/10 text-cyan-hud'
                  : 'border-edge text-ink-faint hover:border-ink-faint'
              }`}
            >
              {entity.id}
            </button>
          ))}
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        <Canvas
          camera={{ position: [260, 240, 260], fov: 55, near: 0.5, far: 6000 }}
          gl={{ antialias: true }}
          dpr={[1, 2]}
          data-testid="tactical-canvas"
        >
          <color attach="background" args={['#04070d']} />
          <Scene
            entities={entities}
            trails={trails}
            cameraMode={cameraMode}
            followId={followTarget}
            selectedId={cameraMode === 'follow' ? followTarget : null}
          />
        </Canvas>

        {entities.length === 0 && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center">
            <p className="max-w-[22rem] px-4 text-center text-[11px] text-ink-faint">
              {emptyHint ??
                'No entities. Press START to load a scenario and run the simulation.'}
            </p>
          </div>
        )}

        <p className="pointer-events-none absolute bottom-2 left-3 text-[9px] text-ink-faint">
          drag to rotate · scroll to zoom · grid 10 km
        </p>
      </div>
    </div>
  )
}
