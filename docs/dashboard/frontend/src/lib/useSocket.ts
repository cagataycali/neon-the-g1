import { useEffect, useRef, useState, useCallback } from 'react'
import type { Telemetry, LogEntry, LogStats } from '../types'

export type ConnState = 'connecting' | 'open' | 'closed'

interface SocketData {
  telemetry: Telemetry | null
  log: LogEntry[]
  stats: LogStats
  conn: ConnState
  lastUpdate: number
}

// Live telemetry over WebSocket with auto-reconnect + REST fallback.
export function useSocket(): SocketData {
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null)
  const [log, setLog] = useState<LogEntry[]>([])
  const [stats, setStats] = useState<LogStats>({})
  const [conn, setConn] = useState<ConnState>('connecting')
  const [lastUpdate, setLastUpdate] = useState(0)
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef<number>(0)

  const connect = useCallback(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const t = localStorage.getItem('neon_session') || ''
    const url = `${proto}://${location.host}/ws${t ? '?token=' + encodeURIComponent(t) : ''}`
    setConn('connecting')
    let ws: WebSocket
    try {
      ws = new WebSocket(url)
    } catch {
      setConn('closed')
      return
    }
    wsRef.current = ws

    ws.onopen = () => {
      setConn('open')
      retryRef.current = 0
    }
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'telemetry') {
          setTelemetry((prev) => ({ ...(prev ?? {}), ...msg.data }))
          setLastUpdate(Date.now())
        } else if (msg.type === 'log') {
          setLog(msg.data ?? [])
          if (msg.stats) setStats(msg.stats)
        }
      } catch { /* ignore */ }
    }
    ws.onclose = () => {
      setConn('closed')
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000)
      retryRef.current++
      setTimeout(connect, delay)
    }
    ws.onerror = () => ws.close()
  }, [])

  useEffect(() => {
    connect()
    return () => { wsRef.current?.close() }
  }, [connect])

  return { telemetry, log, stats, conn, lastUpdate }
}
