import type { LowState } from '../types'
import { isErr, deg, fmt } from '../lib/util'

export default function PostureCard({ ls, embedded = false }: { ls?: LowState; embedded?: boolean }) {
  const off = isErr(ls)
  const posture = ls?.posture ?? 'UNKNOWN'
  const rpy = ls?.imu_rpy ?? []
  const knee = ls?.legs?.avg_knee ?? 0
  // map avg_knee → leg bend for the SVG (0 straight, ~1.5 deep squat)
  const bend = Math.min(knee, 1.6) * 18
  const pColor = posture.includes('STAND') ? 'var(--green)'
    : posture.includes('SIT') ? 'var(--pink)' : 'var(--amber)'
  const Wrap = embedded
    ? (props: { children: any }) => (<div className="panel"><div className="panel-title"><span className="ic">🤖</span> Posture</div>{props.children}</div>)
    : (props: { children: any }) => (<div className="card span-4"><div className="card-title"><span className="ic">🤖</span> Posture & IMU</div>{props.children}</div>)
  return (
    <Wrap>
      {off ? (
        <div className="empty">offline · no LowState</div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
            <div className="robot-wrap">
              <svg className="robot" viewBox="0 0 120 180">
                {/* head */}
                <circle cx="60" cy="22" r="14" className="joint" fill={pColor} opacity="0.9" />
                {/* torso */}
                <rect x="46" y="38" width="28" height="50" rx="8" fill="var(--surface-2)" stroke="var(--border-2)" strokeWidth="1.5" />
                {/* arms */}
                <line x1="46" y1="48" x2="28" y2="78" stroke="var(--cyan)" strokeWidth="6" strokeLinecap="round" />
                <line x1="74" y1="48" x2="92" y2="78" stroke="var(--cyan)" strokeWidth="6" strokeLinecap="round" />
                {/* upper legs */}
                <line x1="54" y1="88" x2={54 - bend * 0.3} y2="126" stroke="var(--purple)" strokeWidth="7" strokeLinecap="round" />
                <line x1="66" y1="88" x2={66 + bend * 0.3} y2="126" stroke="var(--purple)" strokeWidth="7" strokeLinecap="round" />
                {/* lower legs (bend with knee) */}
                <line x1={54 - bend * 0.3} y1="126" x2="52" y2={160 - bend} stroke="var(--purple)" strokeWidth="7" strokeLinecap="round" />
                <line x1={66 + bend * 0.3} y1="126" x2="68" y2={160 - bend} stroke="var(--purple)" strokeWidth="7" strokeLinecap="round" />
                {/* feet */}
                <rect x="44" y={160 - bend} width="16" height="6" rx="3" fill="var(--cyan)" />
                <rect x="60" y={160 - bend} width="16" height="6" rx="3" fill="var(--cyan)" />
              </svg>
            </div>
            <div style={{ flex: 1 }}>
              <span className="badge" style={{ background: 'rgba(255,255,255,0.06)', color: pColor }}>{posture}</span>
              <div style={{ marginTop: 12 }}>
                <div className="metric-row"><span className="k">Avg knee</span><span className="v">{fmt(knee, 3)}</span></div>
                <div className="metric-row"><span className="k">Max τ</span><span className="v">{fmt(ls?.max_leg_tau, 1)} Nm</span></div>
                <div className="metric-row"><span className="k">Tick</span><span className="v">{ls?.tick ?? '—'}</span></div>
              </div>
            </div>
          </div>
          <div className="imu-grid">
            {['roll', 'pitch', 'yaw'].map((axis, i) => (
              <div className="imu-cell" key={axis}>
                <div className="axis">{axis}</div>
                <div className="deg txt-cyan">{deg(rpy[i])}°</div>
              </div>
            ))}
          </div>
        </>
      )}
    </Wrap>
  )
}