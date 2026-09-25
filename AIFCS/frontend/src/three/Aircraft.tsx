import { useMemo } from 'react'
import * as THREE from 'three'
import { Html } from '@react-three/drei/web/Html'
import { TEAM_COLOUR, attitudeToEuler, worldToScene } from '@/three/coordinates'
import type { Entity } from '@/types/api'

/**
 * A fictional flight unit (PHASE 8).
 *
 * Deliberately an abstract delta form built from primitives — a research marker
 * that reads clearly at a distance and shows attitude. It is not modelled on
 * any real aircraft, and there is no weapon or targeting representation.
 *
 * The model is built nose-along +Z so `attitudeToEuler` can orient it directly.
 */
/**
 * Marker size, in scene units. The aircraft is drawn as a tactical symbol
 * rather than to scale — at true size a 20 m airframe is sub-pixel across a
 * 40 km engagement, which is useless to look at.
 */
const MARKER_SCALE = 3.5

export function Aircraft({
  entity,
  selected,
  labelTier = 0,
}: {
  entity: Entity
  selected: boolean
  /** Vertical rank for the label, so units in close formation do not overlap. */
  labelTier?: number
}) {
  const colour = TEAM_COLOUR[entity.team]
  const position = useMemo(() => worldToScene(entity.position), [entity.position])
  const rotation = useMemo(() => attitudeToEuler(entity.orientation), [entity.orientation])
  const inactive = entity.status !== 'ACTIVE'

  // A flat triangle for the planform plus a small fin, so roll and pitch are
  // readable from any angle.
  const planform = useMemo(() => {
    const shape = new THREE.Shape()
    shape.moveTo(0, 2.2) // nose
    shape.lineTo(1.4, -1.4)
    shape.lineTo(0, -0.6)
    shape.lineTo(-1.4, -1.4)
    shape.closePath()
    return new THREE.ShapeGeometry(shape)
  }, [])

  const fin = useMemo(() => {
    const shape = new THREE.Shape()
    shape.moveTo(0, -0.4)
    shape.lineTo(0, -1.4)
    shape.lineTo(0.9, -1.4)
    shape.closePath()
    return new THREE.ShapeGeometry(shape)
  }, [])

  return (
    <group position={position} rotation={rotation} scale={MARKER_SCALE}>
      {/* Planform lies in the X/Z plane: rotate the shape from X/Y onto it. */}
      <mesh geometry={planform} rotation={[-Math.PI / 2, 0, 0]}>
        <meshStandardMaterial
          color={colour}
          emissive={colour}
          emissiveIntensity={inactive ? 0.1 : 0.6}
          opacity={inactive ? 0.35 : 1}
          transparent={inactive}
          side={THREE.DoubleSide}
          flatShading
        />
      </mesh>

      {/* Vertical fin, in the aircraft's plane of symmetry. */}
      <mesh geometry={fin} rotation={[0, Math.PI / 2, 0]}>
        <meshStandardMaterial
          color={colour}
          emissive={colour}
          emissiveIntensity={inactive ? 0.1 : 0.4}
          side={THREE.DoubleSide}
          flatShading
        />
      </mesh>

      {/* Selection ring, drawn flat so it never occludes the aircraft. */}
      {selected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[3.2, 3.6, 40]} />
          <meshBasicMaterial color="#38e1ff" side={THREE.DoubleSide} transparent opacity={0.8} />
        </mesh>
      )}

      {/* Label as a DOM overlay rather than 3D text.
          drei's <Text> pulls a font over the network and suspends until it
          arrives; when that fetch fails, React hides the whole Suspense subtree
          and the tactical view disappears. An HTML label has no such dependency
          and matches the dashboard's typography exactly. */}
      <Html
        position={[0, 1.1, 0]}
        center
        // No distanceFactor: a tactical label should stay the same size on
        // screen. Scaling it in 3D makes it unreadable across the map and
        // overwhelming when the camera is close.
        zIndexRange={[10, 0]}
        style={{ pointerEvents: 'none', userSelect: 'none' }}
      >
        <span
          className="block whitespace-nowrap font-mono text-[13px] tracking-[0.1em]"
          style={{
            color: colour,
            textShadow: '0 0 4px #04070d, 0 0 2px #04070d',
            // Screen-space offset. A 3D offset collapses to nothing at range,
            // which is exactly where two units in close formation need their
            // callsigns kept apart.
            transform: `translateY(${-labelTier * 15}px)`,
          }}
        >
          {entity.id}
        </span>
      </Html>
    </group>
  )
}
