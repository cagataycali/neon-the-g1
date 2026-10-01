import type { LowState } from '../types'
import { isErr, deg, fmt } from '../lib/util'
import { Ico } from './Icons'

export default function PostureCard({ ls, embedded = false }: { ls?: LowState; embedded?: boolean }) {
  const off = isErr(ls)
  const posture = ls?.posture ?? 'UNKNOWN'
  const rpy = ls?.imu_rpy ?? []
  const knee = ls?.legs?.avg_knee ?? 0
  // map avg_knee -> leg bend for the SVG (0 straight, ~1.5 deep squat)
  const bend = Math.min(knee, 1.6) * 18
  // standing is the nominal state (accent); sitting or anything else is flagged in the warn colour
  const standing = posture.includes('STAND')
  const headClass = standing ? 'head' : 'head warn'
  const badgeClass = standing ? 'badge ok' : 'badge warn'
  const Wrap = embedded
    ? (props: { children: any }) => (<div className="panel"><div className="panel-title"><span className="ic">{Ico.robot()}</span> Posture</div>{props.children}</div>)
    : (props: { children: any }) => (<div className="card span-4"><div className="card-title"><span className="ic">{Ico.robot()}</span> Posture and IMU</div>{props.children}</div>)
  return (
    <Wrap>
      {off ? (
        <div className="empty">offline, no LowState</div>
      ) : (
        <>
          <span className={badgeClass + ' posture-badge'}>{posture}</span>
          <div className="posture-body">
            <div className="robot-wrap">
              {/* stick figure in ink; the head carries the posture colour */}
              <svg className="robot" viewBox="0 0 120 180" role="img" aria-label={`Posture ${posture}`}>
                <circle cx="60" cy="22" r="14" className={headClass} />
                <rect x="46" y="38" width="28" height="50" rx="8" className="torso" strokeWidth="1.5" />
                <line x1="46" y1="48" x2="28" y2="78" className="ink" strokeWidth="6" strokeLinecap="round" />
                <line x1="74" y1="48" x2="92" y2="78" className="ink" strokeWidth="6" strokeLinecap="round" />
                <line x1="54" y1="88" x2={54 - bend * 0.3} y2="126" className="ink" strokeWidth="7" strokeLinecap="round" />
                <line x1="66" y1="88" x2={66 + bend * 0.3} y2="126" className="ink" strokeWidth="7" strokeLinecap="round" />
                <line x1={54 - bend * 0.3} y1="126" x2="52" y2={160 - bend} className="ink" strokeWidth="7" strokeLinecap="round" />
                <line x1={66 + bend * 0.3} y1="126" x2="68" y2={160 - bend} className="ink" strokeWidth="7" strokeLinecap="round" />
                <rect x="44" y={160 - bend} width="16" height="6" rx="3" className="ink-fill" />
                <rect x="60" y={160 - bend} width="16" height="6" rx="3" className="ink-fill" />
              </svg>
            </div>
            <div className="posture-metrics">
              <div className="metric-row"><span className="k">Avg knee</span><span className="v">{fmt(knee, 3)}</span></div>
              <div className="metric-row"><span className="k">Max torque</span><span className="v">{fmt(ls?.max_leg_tau, 1)} Nm</span></div>
              <div className="metric-row"><span className="k">Tick</span><span className="v">{ls?.tick ?? '--'}</span></div>
            </div>
          </div>
          <div className="imu-grid">
            {['roll', 'pitch', 'yaw'].map((axis, i) => (
              <div className="imu-cell" key={axis}>
                <div className="axis">{axis}</div>
                <div className="deg">{deg(rpy[i])}<span className="spark-unit"> deg</span></div>
              </div>
            ))}
          </div>
        </>
      )}
    </Wrap>
  )
}
