import { useEffect, useState } from 'react'
import { authedFetch, getToken } from '../lib/auth'
import { Ico } from './Icons'

interface Cam {
  id: string; kind: string; backend: string | null
  running: boolean; frames: number; resolution: number[]; error: string | null
}
const LABELS: Record<string, string> = {
  realsense_color: 'RealSense color',
  realsense_depth: 'RealSense depth',
  brio: 'Logitech Brio',
}

// prefer: 'color' shows color+brio, 'depth' shows depth cams
export default function CameraCard({ embedded = false, prefer }: { embedded?: boolean; prefer?: 'color' | 'depth' }) {
  const [cams, setCams] = useState<Cam[]>([])
  useEffect(() => {
    let stop = false
    const poll = async () => {
      try { const r = await authedFetch('/api/cameras'); const d = await r.json(); if (!stop) setCams(d.cameras ?? []) } catch {}
    }
    poll(); const id = setInterval(poll, 4000)
    return () => { stop = true; clearInterval(id) }
  }, [])

  const tok = getToken()
  const q = tok ? `?token=${encodeURIComponent(tok)}` : ''

  let shown = cams
  if (prefer === 'depth') shown = cams.filter(c => c.id.includes('depth'))
  else if (prefer === 'color') shown = cams.filter(c => !c.id.includes('depth'))
  if (shown.length === 0) shown = cams

  const grid = (
    shown.length === 0 ? <div className="empty">no cameras</div> : (
      <div className={`cam-grid ${shown.length === 1 ? 'single' : ''}`}>
        {shown.map((c) => {
          const online = c.running && c.frames > 0
          return (
            <div className="cam-tile" key={c.id}>
              <div className="cam-tile-head">
                <span>{LABELS[c.id] ?? c.id}</span>
                <span className={`dot ${online ? 'open' : 'connecting'}`} role="img" aria-label={online ? 'streaming' : 'no frames yet'} title={online ? 'streaming' : 'no frames yet'} />
              </div>
              <div className="cam-frame">
                <img className="cam-img" alt={`${LABELS[c.id] ?? c.id} stream`} src={`/api/camera/${c.id}/stream${q}`} />
              </div>
            </div>
          )
        })}
      </div>
    )
  )
  if (embedded) return grid
  return <div className="card span-8"><div className="card-title"><span className="ic">{Ico.camera()}</span> Cameras</div>{grid}</div>
}
