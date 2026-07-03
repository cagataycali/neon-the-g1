import { useRef, useEffect, useMemo } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { useLidar } from '../lib/useLidar'

const MAX_POINTS = 8000

function Cloud({ points, count, frameSeq }: { points: Float32Array | null; count: number; frameSeq: number }) {
  const pending = useRef<{ p: Float32Array; n: number } | null>(null)

  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 3), 3))
    g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 3), 3))
    g.setDrawRange(0, 0)
    return g
  }, [])
  useEffect(() => () => geom.dispose(), [geom])

  useEffect(() => {
    if (points && count) pending.current = { p: points, n: count }
  }, [frameSeq, points, count])

  useFrame(() => {
    const pend = pending.current
    if (!pend) return
    pending.current = null
    const pos = geom.attributes.position as THREE.BufferAttribute
    const col = geom.attributes.color as THREE.BufferAttribute
    const n = Math.min(pend.n, MAX_POINTS)
    const src = pend.p
    for (let i = 0; i < n; i++) {
      const s = i * 4, d = i * 3
      const x = src[s], y = src[s + 1], z = src[s + 2]
      pos.array[d] = x; pos.array[d + 1] = y; pos.array[d + 2] = z
      // color by distance (cyan→pink neon ramp)
      const dist = Math.sqrt(x * x + y * y + z * z)
      const t = Math.min(1, dist / 8)
      ;(col.array as Float32Array)[d] = 0.0 + t          // R
      ;(col.array as Float32Array)[d + 1] = 0.9 - t * 0.6 // G
      ;(col.array as Float32Array)[d + 2] = 1.0 - t * 0.3 // B
    }
    pos.needsUpdate = true
    col.needsUpdate = true
    geom.setDrawRange(0, n)
  })

  return (
    <points geometry={geom}>
      <pointsMaterial size={0.04} vertexColors sizeAttenuation />
    </points>
  )
}

export default function LidarView({ embedded = false }: { embedded?: boolean }) {
  const { points, count, frameSeq, connected } = useLidar()
  const canvas = (
    <div className="lidar-canvas">
        <Canvas camera={{ position: [0, 4, 12], fov: 60 }} style={{ background: '#14120e' }}>
          <ambientLight intensity={0.6} />
          <group rotation={[Math.PI / 2, 0, 0]}>
            <Cloud points={points} count={count} frameSeq={frameSeq} />
          </group>
          <OrbitControls makeDefault target={[0, -1, 0]} />
          <gridHelper args={[20, 20, '#3a3428', '#231f18']} position={[0, -1.5, 0]} />
          <axesHelper args={[1.5]} />
        </Canvas>
    </div>
  )
  if (embedded) return (
    <>
      <div className="lidar-stat"><span className={`dot ${connected && count > 0 ? 'open' : 'connecting'}`} /> {count} pts</div>
      {canvas}
    </>
  )
  return (
    <div className="card span-8">
      <div className="card-title"><span className="ic">📡</span> LiDAR · Livox MID-360
        <span style={{ marginLeft: 'auto' }} className="txt-muted">{count} pts</span>
      </div>
      {canvas}
    </div>
  )
}
