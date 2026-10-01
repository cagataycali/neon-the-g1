import { useRef, useEffect, useMemo, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { useLidar } from '../lib/useLidar'
import { Ico } from './Icons'

const MAX_POINTS = 8000

/** Viewer palette read from the Strands tokens on <html>, re-read when data-scheme flips (paper <-> dark). */
function useViewerPalette() {
  const read = () => {
    const cs = getComputedStyle(document.documentElement)
    const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
    return {
      bg: v('--sr-viewer-bg', '#f4f4f4'),
      major: v('--sr-grid-major', '#b6b6b6'),
      minor: v('--sr-grid-minor', '#d9d9d9'),
      ink: new THREE.Color(v('--sr-fg', '#000000')),
      accent: new THREE.Color(v('--sr-accent', '#007a3d')),
    }
  }
  const [pal, setPal] = useState(read)
  useEffect(() => {
    const obs = new MutationObserver(() => setPal(read()))
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-scheme'] })
    return () => obs.disconnect()
  }, [])
  return pal
}

function Cloud({ points, count, frameSeq, ink, accent }: { points: Float32Array | null; count: number; frameSeq: number; ink: THREE.Color; accent: THREE.Color }) {
  const pending = useRef<{ p: Float32Array; n: number } | null>(null)
  const palette = useRef({ ink, accent })
  useEffect(() => {
    palette.current = { ink, accent }
    // a scheme flip recolours the frame already on screen
    if (points && count) pending.current = { p: points, n: count }
  }, [ink, accent, points, count])

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
    const { ink: a, accent: b } = palette.current
    const colors = col.array as Float32Array
    for (let i = 0; i < n; i++) {
      const s = i * 4, d = i * 3
      const x = src[s], y = src[s + 1], z = src[s + 2]
      pos.array[d] = x; pos.array[d + 1] = y; pos.array[d + 2] = z
      // colour by distance: ink close to the robot, the Strands green far away
      const dist = Math.sqrt(x * x + y * y + z * z)
      const t = Math.min(1, dist / 8)
      colors[d] = a.r + (b.r - a.r) * t
      colors[d + 1] = a.g + (b.g - a.g) * t
      colors[d + 2] = a.b + (b.b - a.b) * t
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
  const pal = useViewerPalette()
  const canvas = (
    <div className="lidar-canvas">
      <Canvas camera={{ position: [0, 4, 12], fov: 60 }} style={{ background: pal.bg }}>
        <ambientLight intensity={0.6} />
        <group rotation={[Math.PI / 2, 0, 0]}>
          <Cloud points={points} count={count} frameSeq={frameSeq} ink={pal.ink} accent={pal.accent} />
        </group>
        <OrbitControls makeDefault target={[0, -1, 0]} />
        <gridHelper key={`${pal.major}${pal.minor}`} args={[20, 20, pal.major, pal.minor]} position={[0, -1.5, 0]} />
      </Canvas>
    </div>
  )
  const stat = <><span className={`dot ${connected && count > 0 ? 'open' : 'connecting'}`} /> {count} pts</>
  if (embedded) return (
    <>
      <div className="lidar-stat" role="status">{stat}</div>
      {canvas}
    </>
  )
  return (
    <div className="card span-8">
      <div className="card-title"><span className="ic">{Ico.radar()}</span> LiDAR Livox MID-360
        <span style={{ marginLeft: 'auto' }} className="txt-muted">{count} pts</span>
      </div>
      {canvas}
    </div>
  )
}
