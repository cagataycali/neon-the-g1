import type { G1State, LowState } from '../types'
import { isErr, fmt } from '../lib/util'
import { Ico } from './Icons'

const ARM_READY_MM = new Set([5, 6])

export default function StateCard({ s, ls, embedded = false }: { s?: G1State; ls?: LowState; embedded?: boolean }) {
  const stateOff = isErr(s)
  const mm = s?.mode_machine ?? ls?.mode_machine
  const haveFallback = mm !== undefined && mm !== null
  const armReady = stateOff ? (haveFallback ? ARM_READY_MM.has(mm!) : false) : !!s?.arm_ready
  const fsm = s?.fsm_id
  const mode = s?.mode?.name
  const degraded = stateOff && haveFallback

  const body = (stateOff && !haveFallback) ? (
    <div className="empty">loco RPC unreachable</div>
  ) : (
    <>
      <div className="badges">
        {!stateOff && <span className={`badge ${mode === 'ai' ? 'ok' : 'warn'}`}>mode: {mode ?? '?'}</span>}
        <span className={`badge ${armReady ? 'ok' : 'err'}`}>{armReady ? 'arm ready' : 'arm locked'}</span>
        {degraded && <span className="badge warn">LowState</span>}
      </div>
      <div className="metric" style={{ marginTop: 10 }}>
        <span className="val">{stateOff ? mm : (fsm ?? '--')}</span>
        <span className="unit">{stateOff ? 'mode_machine' : (s?.fsm_name ?? 'unknown')}</span>
      </div>
      {!stateOff && (
        <div style={{ marginTop: 10 }}>
          <div className="metric-row"><span className="k">FSM mode</span><span className="v">{s?.fsm_mode ?? '--'}</span></div>
          <div className="metric-row"><span className="k">Stand h</span><span className="v">{fmt(s?.stand_height, 3)}</span></div>
        </div>
      )}
    </>
  )

  if (embedded) return (
    <div className="panel">
      <div className="panel-title"><span className="ic">{Ico.cpu()}</span> Controller</div>
      {body}
    </div>
  )
  return <div className="card span-4"><div className="card-title"><span className="ic">{Ico.cpu()}</span> Controller state</div>{body}</div>
}
