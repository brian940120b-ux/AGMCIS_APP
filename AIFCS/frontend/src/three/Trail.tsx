import { useMemo } from 'react'
import * as THREE from 'three'
import { TEAM_COLOUR, worldToScene } from '@/three/coordinates'
import type { Entity } from '@/types/api'

/**
 * Motion trail (PHASE 8).
 *
 * Drawn from recorded truth positions, not interpolated or embellished — the
 * line is where the aircraft actually was.
 */
export function Trail({
  history,
  team,
}: {
  history: [number, number, number][]
  team: Entity['team']
}) {
  const geometry = useMemo(() => {
    if (history.length < 2) return null

    const points = history.map((position) => worldToScene(position))
    return new THREE.BufferGeometry().setFromPoints(points)
  }, [history])

  if (!geometry) return null

  return (
    <line>
      <primitive object={geometry} attach="geometry" />
      <lineBasicMaterial color={TEAM_COLOUR[team]} transparent opacity={0.45} />
    </line>
  )
}
