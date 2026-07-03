import type { LogEntry, LogStats } from '../types'
import { ago } from '../lib/util'

export default function LogFeed({ log, stats, embedded = false }: { log: LogEntry[]; stats: LogStats; embedded?: boolean }) {
  const rev = [...log].reverse()
  const body = (
    <>
      {stats.by_persona && (
        <div className="persona-chips">
          {Object.entries(stats.by_persona).map(([p, n]) => (
            <span className="chip" key={p}>{p}<b>{n}</b></span>
          ))}
        </div>
      )}
      <div className="log-feed" style={{ marginTop: 10 }}>
        {rev.length === 0 ? <div className="empty">no activity yet</div> : rev.map((e, i) => (
          <div className={`log-item ${e.persona}`} key={i}>
            <div className="log-meta">
              <span className="log-persona">{e.persona}</span>
              <span className="log-role">{e.role}</span>
              <span className="log-ts">{ago(e.ts)}</span>
            </div>
            <div className="log-text">{e.text}</div>
          </div>
        ))}
      </div>
    </>
  )
  if (embedded) return body
  return <div className="card span-8"><div className="card-title"><span className="ic">🧬</span> Cross-Persona Reasoning Log</div>{body}</div>
}
