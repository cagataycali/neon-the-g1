import { useEffect, useRef, useState, useCallback } from 'react'

interface LidarData {
  points: Float32Array | null  // xyzi stride 4
  count: number
  frameSeq: number
  connected: boolean
}

// Decodes the dashboard's compact wire format: 7 bytes/point
// x_i16(LE), y_i16(LE), z_i16(LE), intensity_u8 ; xyz scale = 1/100
export function useLidar(): LidarData {
  const pointsRef = useRef<Float32Array | null>(null)
  const countRef = useRef(0)
  const [frameSeq, setFrameSeq] = useState(0)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const retry = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const t = localStorage.getItem('neon_session') || ''
    const ws = new WebSocket(`${proto}://${location.host}/ws/lidar${t ? '?token=' + encodeURIComponent(t) : ''}`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws
    ws.onopen = () => setConnected(true)
    ws.onclose = () => {
      setConnected(false)
      retry.current = setTimeout(connect, 2000)
    }
    ws.onerror = () => ws.close()
    ws.onmessage = (ev) => {
      if (!(ev.data instanceof ArrayBuffer)) return
      const buf = ev.data
      const N = Math.floor(buf.byteLength / 7)
      if (N === 0) return
      const out = new Float32Array(N * 4)
      const view = new DataView(buf)
      const S = 1 / 100
      for (let i = 0; i < N; i++) {
        const b = i * 7, o = i * 4
        out[o] = view.getInt16(b, true) * S
        out[o + 1] = view.getInt16(b + 2, true) * S
        out[o + 2] = view.getInt16(b + 4, true) * S
        out[o + 3] = view.getUint8(b + 6)
      }
      pointsRef.current = out
      countRef.current = N
      setFrameSeq((s) => s + 1)
    }
  }, [])

  useEffect(() => {
    connect()
    return () => { clearTimeout(retry.current); wsRef.current?.close() }
  }, [connect])

  return { points: pointsRef.current, count: countRef.current, frameSeq, connected }
}
