import { useRef, useCallback } from 'react'

// Fixed-capacity ring buffer for realtime charts. Mutates in place;
// returns a snapshot array on demand (caller triggers re-render via parent).
export function useTimeSeries(capacity = 120) {
  const buf = useRef<number[]>([])
  const push = useCallback((v: number) => {
    if (v === null || v === undefined || Number.isNaN(v)) return
    const b = buf.current
    b.push(v)
    if (b.length > capacity) b.shift()
  }, [capacity])
  const values = useCallback(() => buf.current, [])
  return { push, values }
}
