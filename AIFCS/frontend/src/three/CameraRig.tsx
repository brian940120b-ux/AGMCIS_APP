import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import { sceneCentroid, sceneSpread, worldToScene } from '@/three/coordinates'
import type { Entity } from '@/types/api'

export type CameraMode = 'orbit' | 'follow' | 'top' | 'side'

/**
 * Camera rig (PHASE 8).
 *
 * `orbit` is a free camera the user drives. The other modes move the orbit
 * target for them: `follow` tracks one unit, `top` looks straight down, `side`
 * gives an altitude profile. Every mode still allows manual rotation, so a
 * preset is a starting point rather than a cage.
 */
export function CameraRig({
  mode,
  entities,
  followId,
}: {
  mode: CameraMode
  entities: Entity[]
  followId: string | null
}) {
  const controls = useRef<OrbitControlsImpl>(null)
  const { camera } = useThree()
  const target = useRef(new THREE.Vector3(0, 60, 0))

  /*
   * Snap to a viewpoint that frames the units, on a mode change and whenever
   * the number of units changes (which is when a scenario loads). A fixed
   * distance left the aircraft as specks the first time a run started.
   */
  const entityCount = entities.length
  useEffect(() => {
    const centre = sceneCentroid(entities)
    const spread = sceneSpread(entities)

    if (mode === 'top') {
      camera.position.set(centre.x, centre.y + spread * 1.3, centre.z + 0.01)
    } else if (mode === 'side') {
      camera.position.set(centre.x + spread * 1.2, centre.y + spread * 0.15, centre.z)
    } else if (mode === 'orbit') {
      camera.position.set(
        centre.x + spread * 0.75,
        centre.y + spread * 0.55,
        centre.z + spread * 0.75,
      )
    }
    // `follow` sets its position every frame below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, camera, entityCount])

  useFrame(() => {
    const followed = followId ? entities.find((e) => e.id === followId) : undefined

    if (mode === 'follow' && followed) {
      const position = worldToScene(followed.position)
      target.current.lerp(position, 0.25)

      // Sit behind and above the unit, along its velocity where it has one.
      const velocity = new THREE.Vector3(
        followed.velocity[0],
        followed.velocity[2],
        -followed.velocity[1],
      )
      // Far enough back to see the unit in its surroundings rather than
      // filling the frame with it.
      const CHASE_DISTANCE = 70
      const behind =
        velocity.lengthSq() > 1
          ? velocity.clone().normalize().multiplyScalar(-CHASE_DISTANCE)
          : new THREE.Vector3(0, 0, -CHASE_DISTANCE)
      behind.y += CHASE_DISTANCE * 0.32
      camera.position.lerp(position.clone().add(behind), 0.12)
    } else {
      target.current.lerp(sceneCentroid(entities), 0.05)
    }

    if (controls.current) {
      controls.current.target.copy(target.current)
      controls.current.update()
    }
  })

  return (
    <OrbitControls
      ref={controls}
      enablePan={mode === 'orbit'}
      enableDamping
      dampingFactor={0.08}
      minDistance={12}
      maxDistance={2500}
    />
  )
}
