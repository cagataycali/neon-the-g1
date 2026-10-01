import type { Mainboard, SlamPose, Telemetry } from '../types'
import { isErr, fmt, deg } from '../lib/util'
import { Ico } from './Icons'

export default function SystemCard({ t }: { t: Telemetry | null }) {
  const mb: Mainboard | undefined = t?.mainboard
  const slam: SlamPose | undefined = t?.slam
  const cpu = mb?.cpu_temperature
  const fans = mb?.fan_speed ?? []
  return (
    <div className="card span-4">
      <div className="card-title"><span className="ic">{Ico.globe()}</span> System and SLAM</div>
      <div className="metric-row"><span className="k">Interface</span><span className="v">{t?.iface ?? '--'}</span></div>
      <div className="metric-row"><span className="k">CPU temp</span><span className="v">{cpu !== undefined ? `${fmt(cpu, 0)} C` : '--'}</span></div>
      <div className="metric-row"><span className="k">Fans</span><span className="v">{fans.length ? fans.join(' / ') : '--'}</span></div>
      <div style={{ height: 1, background: 'var(--sr-rule)', margin: '10px 0' }} />
      {isErr(slam) ? (
        <div className="metric-row"><span className="k">SLAM</span><span className="v txt-muted">idle</span></div>
      ) : (
        <>
          <div className="metric-row"><span className="k">SLAM x</span><span className="v">{fmt(slam?.x, 2)} m</span></div>
          <div className="metric-row"><span className="k">SLAM y</span><span className="v">{fmt(slam?.y, 2)} m</span></div>
          <div className="metric-row"><span className="k">Heading</span><span className="v">{deg(slam?.theta)} deg</span></div>
        </>
      )}
    </div>
  )
}
