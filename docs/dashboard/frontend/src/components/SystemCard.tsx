import type { Mainboard, Telemetry } from '../types'
import { fmt } from '../lib/util'
import { Ico } from './Icons'

export default function SystemCard({ t }: { t: Telemetry | null }) {
  const mb: Mainboard | undefined = t?.mainboard
  const cpu = mb?.cpu_temperature
  const fans = mb?.fan_speed ?? []
  return (
    <div className="card span-4">
      <div className="card-title"><span className="ic">{Ico.globe()}</span> System</div>
      <div className="metric-row"><span className="k">Interface</span><span className="v">{t?.iface ?? '--'}</span></div>
      <div className="metric-row"><span className="k">CPU temp</span><span className="v">{cpu !== undefined ? `${fmt(cpu, 0)} C` : '--'}</span></div>
      <div className="metric-row"><span className="k">Fans</span><span className="v">{fans.length ? fans.join(' / ') : '--'}</span></div>
    </div>
  )
}
