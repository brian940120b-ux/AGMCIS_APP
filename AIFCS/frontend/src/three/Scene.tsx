import { Aircraft } from '@/three/Aircraft'
import { CameraRig, type CameraMode } from '@/three/CameraRig'
import { TacticalGround } from '@/three/TacticalGround'
import { Trail } from '@/three/Trail'
import { worldToScene } from '@/three/coordinates'
import type { Entity } from '@/types/api'

/**
 * Scene contents (PHASE 8).
 *
 * Everything drawn here comes from the telemetry stream. Nothing is simulated
 * or interpolated in the browser — the render layer only displays what the
 * engine reported.
 */
export function Scene({
  entities,
  trails,
  cameraMode,
  followId,
  selectedId,
}: {
  entities: Entity[]
  trails: Record<string, [number, number, number][]>
  cameraMode: CameraMode
  followId: string | null
  selectedId: string | null
}) {
  return (
    <>
      {/* Dim ambient plus one key light: enough to read attitude, dark enough
          to keep the tactical feel. */}
      <ambientLight intensity={0.55} />
      <directionalLight position={[120, 220, 90]} intensity={1.1} />
      <hemisphereLight args={['#38e1ff', '#04070d', 0.25]} />

      <fog attach="fog" args={['#04070d', 400, 2200]} />

      <TacticalGround />

      {entities.map((entity) => (
        <Trail key={`${entity.id}-trail`} history={trails[entity.id] ?? []} team={entity.team} />
      ))}

      {entities.map((entity, index) => (
        <group key={entity.id}>
          {/* Labels are tiered by index so a two-ship in close formation does
              not render its two callsigns on top of each other. */}
          <Aircraft
            entity={entity}
            selected={entity.id === selectedId}
            labelTier={index % 2}
          />
          {/* Altitude stalk down to the datum: without it, height is ambiguous
              from most angles. */}
          <line>
            <bufferGeometry>
              <bufferAttribute
                attach="attributes-position"
                args={[
                  new Float32Array([
                    worldToScene(entity.position).x,
                    0,
                    worldToScene(entity.position).z,
                    ...worldToScene(entity.position).toArray(),
                  ]),
                  3,
                ]}
              />
            </bufferGeometry>
            <lineBasicMaterial
              color={entity.team === 'RED' ? '#ff5a6e' : '#4da3ff'}
              transparent
              opacity={0.18}
            />
          </line>
        </group>
      ))}

      <CameraRig mode={cameraMode} entities={entities} followId={followId} />
    </>
  )
}
