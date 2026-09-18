import { useMemo } from 'react'
import * as THREE from 'three'
import { METRES_PER_UNIT } from '@/three/coordinates'

/**
 * Abstract ground reference (PHASE 8).
 *
 * A tactical grid at datum level, not terrain. Nothing here represents real
 * geography — it exists to give the eye a scale and a horizon.
 */
export function TacticalGround({ extentKm = 120 }: { extentKm?: number }) {
  const halfExtent = (extentKm * 1000) / METRES_PER_UNIT / 2
  const majorStep = 10_000 / METRES_PER_UNIT // a line every 10 km
  const minorStep = majorStep / 5 // and every 2 km

  const grid = useMemo(() => {
    // GridHelper colours its lines with a vertex-colour attribute, so they have
    // to be given to the constructor — setting material.color afterwards has no
    // effect, which is why an earlier version rendered an almost invisible grid.
    const major = new THREE.GridHelper(
      halfExtent * 2,
      Math.round((halfExtent * 2) / majorStep),
      new THREE.Color('#2b4365'),
      new THREE.Color('#223350'),
    )
    const material = major.material as THREE.LineBasicMaterial
    material.transparent = true
    material.opacity = 0.75
    return major
  }, [halfExtent, majorStep])

  const fineGrid = useMemo(() => {
    const minor = new THREE.GridHelper(
      halfExtent * 2,
      Math.round((halfExtent * 2) / minorStep),
      new THREE.Color('#152338'),
      new THREE.Color('#152338'),
    )
    const material = minor.material as THREE.LineBasicMaterial
    material.transparent = true
    material.opacity = 0.5
    return minor
  }, [halfExtent, minorStep])

  return (
    <group>
      <primitive object={fineGrid} />
      <primitive object={grid} />

      {/* Datum axes: the scenario origin, so positions are readable. */}
      <line>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            args={[new Float32Array([-halfExtent, 0, 0, halfExtent, 0, 0]), 3]}
          />
        </bufferGeometry>
        <lineBasicMaterial color="#38e1ff" transparent opacity={0.25} />
      </line>
      <line>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            args={[new Float32Array([0, 0, -halfExtent, 0, 0, halfExtent]), 3]}
          />
        </bufferGeometry>
        <lineBasicMaterial color="#38e1ff" transparent opacity={0.25} />
      </line>
    </group>
  )
}
