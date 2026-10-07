import { useEffect, useState } from 'react'
import { authedFetch, getToken } from '../lib/auth'
import { Ico } from './Icons'

interface Cam {
  id: string; kind: string; backend: string | null
  running: boolean; frames: number; resolution: number[]; error: string | null
  last_frame_age: number | null
}
/** A feed is live when a frame arrived recently; frames>0 alone hides a frozen stream. */
export const STALE_S = 5
export const isLive = (c: Cam) => c.running && c.frames > 0 && c.last_frame_age !== null && c.last_frame_age < STALE_S
export const feedState = (c: Cam): 'live' | 'frozen' | 'waiting' | 'off' =>
  isLive(c) ? 'live' : c.running && c.frames > 0 ? 'frozen' : c.running ? 'waiting' : 'off'

const LABELS: Record<string, string> = {
  realsense_color: 'RealSense color',
  realsense_depth: 'RealSense depth',
  brio: 'Logitech Brio',
}

// prefer: 'color' shows color+brio, 'depth' shows depth cams
export default function CameraCard({ embedded = false, prefer }: { embedded?: boolean; prefer?: 'color' | 'depth' }) {
  const [cams, setCams] = useState<Cam[]>([])
  const [resetting, setResetting] = useState(false)
  const [gen, setGen] = useState(0)   // bumps the <img> src so a dead MJPEG socket is reopened after a reset
  useEffect(() => {
    let stop = false
    const poll = async () => {
      try { const r = await authedFetch('/api/cameras'); const d = await r.json(); if (!stop) setCams(d.cameras ?? []) } catch {}
    }
    poll(); const id = setInterval(poll, 4000)
    return () => { stop = true; clearInterval(id) }
  }, [])
  const reset = async () => {
    if (resetting) return
    setResetting(true)
    try { await authedFetch('/api/camera/reset', { method: 'POST' }) } catch {}
    setTimeout(() => { setGen(g => g + 1); setResetting(false) }, 6000)   // reset + re-enumeration + first frame
  }

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
          const state = feedState(c)
          const word = state === 'live' ? 'streaming' : state === 'frozen' ? `frozen ${Math.round(c.last_frame_age ?? 0)}s` : state === 'waiting' ? 'no frames yet' : (c.error ? 'off: ' + c.error : 'off')
          return (
            <div className="cam-tile" key={c.id} data-state={state}>
              <div className="cam-tile-head">
                <span>{LABELS[c.id] ?? c.id}</span>
                <span className="cam-tile-right">
                  {state !== 'live' && (
                    <button className="mini-btn cam-reset" onClick={reset} disabled={resetting} aria-label="Reset the cameras" title="Power-cycle the RealSense and restart the feeds">{resetting ? 'resetting' : 'reset'}</button>
                  )}
                  <span className={`dot ${state === 'live' ? 'open' : state === 'frozen' || state === 'off' ? 'closed' : 'connecting'}`} role="img" aria-label={word} title={word} />
                </span>
              </div>
              <div className="cam-frame">
                <img className="cam-img" alt={`${LABELS[c.id] ?? c.id} stream`} src={`/api/camera/${c.id}/stream${q}${q ? '&' : '?'}g=${gen}`} />
                {state !== 'live' && <div className="cam-overlay" role="status">{word}</div>}
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
