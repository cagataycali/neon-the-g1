import type { LogEntry } from '../types'
import { ago, tsMs } from '../lib/util'
import { Ico } from './Icons'

/** What one agent_log row is, in one line: who did what. */
export function describe(e: LogEntry): { label: string; text: string } {
  const meta = (e.meta ?? {}) as Record<string, unknown>
  if (e.role === 'tool') {
    const name = String(meta.tool_name ?? meta.tool ?? 'tool')
    const status = meta.status ? String(meta.status) : ''
    const ms = typeof meta.duration_ms === 'number' ? ` ${Math.round(meta.duration_ms as number)} ms` : ''
    return { label: name, text: (status ? `${status}${ms}` : e.text).trim() }
  }
  if (e.role === 'user') return { label: 'heard', text: e.text }
  if (e.role === 'assistant') return { label: 'said', text: e.text }
  return { label: e.role, text: e.text }
}

const LIVE_MS = 15_000

/** Compact live activity on the cockpit: the last few cross-persona turns and
 *  tool calls, newest first. The full history stays in the Activity log drawer. */
export default function ActivityStrip({ log, limit = 6, onMore, now = Date.now() }: {
  log: LogEntry[]; limit?: number; onMore: () => void; now?: number
}) {
  const rows = [...log].reverse().slice(0, limit)
  const newest = rows[0]
  const live = !!newest && now - tsMs(newest.ts) < LIVE_MS
  return (
    <div className="panel activity" data-testid="activity-strip">
      <div className="panel-title act-head">
        <span className="ic">{Ico.activity()}</span>
        <span>Activity</span>
        <span className={`act-live ${live ? 'on' : ''}`} role="img" aria-label={live ? 'active now' : 'idle'} title={live ? 'active now' : 'idle'} />
        <button className="mini-btn act-more" onClick={onMore} aria-label="Open the activity log" title="Full activity log">all</button>
      </div>
      {rows.length === 0 ? <div className="empty">no activity yet</div> : (
        <ol className="act-list" aria-label="Recent activity">
          {rows.map((e, i) => {
            const d = describe(e)
            return (
              <li className={`act-row ${e.persona} ${e.role}`} key={`${e.ts}-${i}`} onClick={onMore} title={e.text}>
                <span className="act-persona">{e.persona}</span>
                <span className={`act-label ${e.role === 'tool' ? 'mono' : ''}`}>{d.label}</span>
                <span className="act-text">{d.text}</span>
                <time className="act-ts" dateTime={e.ts}>{ago(e.ts)}</time>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
