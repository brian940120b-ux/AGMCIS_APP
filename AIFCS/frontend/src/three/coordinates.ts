/**
 * World ↔ scene coordinate mapping (PHASE 8).
 *
 * The simulation world is ENU: +X east, +Y north, +Z up, in metres.
 * Three.js is Y-up with +Z toward the viewer, so:
 *
 *     scene.x =  world.x   (east)
 *     scene.y =  world.z   (up)
 *     scene.z = -world.y   (north points into the screen)
 *
 * Distances are divided by METRES_PER_UNIT so a 40 km engagement fits a scene a
 * camera can frame without fighting the near/far planes. The scale is uniform:
 * altitude is not exaggerated, because a research plot that distorts geometry
 * is worse than one that is hard to read.
 */

import * as THREE from 'three'
import type { Entity } from '@/types/api'

export const METRES_PER_UNIT = 100

/** ENU metres → scene units. */
export function worldToScene(position: readonly number[]): THREE.Vector3 {
  return new THREE.Vector3(
    position[0] / METRES_PER_UNIT,
    position[2] / METRES_PER_UNIT,
    -position[1] / METRES_PER_UNIT,
  )
}

/** Same mapping, writing into an existing vector to avoid per-frame allocation. */
export function worldToSceneInto(target: THREE.Vector3, position: readonly number[]): THREE.Vector3 {
  return target.set(
    position[0] / METRES_PER_UNIT,
    position[2] / METRES_PER_UNIT,
    -position[1] / METRES_PER_UNIT,
  )
}

/**
 * Aircraft attitude as a scene rotation.
 *
 * The model is built nose-along +Z. Yaw is a compass bearing (0 = north), and
 * north is -Z in the scene, so a yaw of 0 needs no turn and the sign of the
 * rotation follows from the handedness of the mapping.
 */
export function attitudeToEuler(orientation: readonly number[]): THREE.Euler {
  const [roll, pitch, yaw] = orientation
  return new THREE.Euler(pitch, -yaw, -roll, 'YXZ')
}

/** Team colours, matching the rest of the dashboard. */
export const TEAM_COLOUR: Record<Entity['team'], string> = {
  BLUE: '#4da3ff',
  RED: '#ff5a6e',
  NEUTRAL: '#8ea3c4',
}

/** How far apart the units are, in scene units — used to frame the camera. */
export function sceneSpread(entities: Entity[]): number {
  if (entities.length < 2) return 120

  const points = entities.map((entity) => worldToScene(entity.position))
  const box = new THREE.Box3().setFromPoints(points)
  return Math.max(box.getSize(new THREE.Vector3()).length(), 60)
}

/** Centroid of the units, in scene units — what the camera frames by default. */
export function sceneCentroid(entities: Entity[]): THREE.Vector3 {
  if (entities.length === 0) return new THREE.Vector3(0, 60, 0)

  const sum = entities.reduce(
    (acc, entity) => acc.add(worldToScene(entity.position)),
    new THREE.Vector3(),
  )
  return sum.divideScalar(entities.length)
}
