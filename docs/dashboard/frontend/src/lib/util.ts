export const isErr = (s?: { status?: string }) =>
  !s || s.status === 'error' || s.status === 'unavailable'

export const fmt = (n: number | null | undefined, d = 1) =>
  n === null || n === undefined || Number.isNaN(n) ? '—' : n.toFixed(d)

export const deg = (rad?: number) =>
  rad === undefined || rad === null ? '—' : (rad * 180 / Math.PI).toFixed(0)

export const ago = (ts: string) => {
  const t = new Date(ts.replace(' ', 'T') + 'Z').getTime()
  if (Number.isNaN(t)) return ts
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return `${s | 0}s`
  if (s < 3600) return `${(s / 60) | 0}m`
  if (s < 86400) return `${(s / 3600) | 0}h`
  return `${(s / 86400) | 0}d`
}
