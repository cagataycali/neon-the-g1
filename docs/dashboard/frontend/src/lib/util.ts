export const isErr = (s?: { status?: string }) =>
  !s || s.status === 'error' || s.status === 'unavailable'

export const fmt = (n: number | null | undefined, d = 1) =>
  n === null || n === undefined || Number.isNaN(n) ? '--' : n.toFixed(d)

export const deg = (rad?: number) =>
  rad === undefined || rad === null ? '--' : (rad * 180 / Math.PI).toFixed(0)

/** agent_log stamps rows with SQLite CURRENT_TIMESTAMP: "YYYY-MM-DD HH:MM:SS" in UTC, no zone. */
export const tsMs = (ts: string) => new Date(/[zZ]$|[+-]\d\d:?\d\d$/.test(ts) ? ts : ts.replace(' ', 'T') + 'Z').getTime()

export const ago = (ts: string) => {
  const t = tsMs(ts)
  if (Number.isNaN(t)) return ts
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return `${s | 0}s`
  if (s < 3600) return `${(s / 60) | 0}m`
  if (s < 86400) return `${(s / 3600) | 0}h`
  return `${(s / 86400) | 0}d`
}
