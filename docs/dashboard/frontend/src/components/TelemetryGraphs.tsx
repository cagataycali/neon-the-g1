import { useEffect, useRef, useState } from 'react'
import type { Telemetry } from '../types'
import Sparkline from './Sparkline'
import { isErr, fmt, deg } from '../lib/util'
import { Ico } from './Icons'

const CAP = 120
const LOW_SOC = 20

// Realtime graphs + numbers for battery, IMU, knees. Keeps rolling buffers
// keyed by metric and re-renders on each telemetry tick.
export default function TelemetryGraphs({ t }: { t: Telemetry | null }) {
  const bufs = useRef<Record<string, number[]>>({
    soc: [], volt: [], curr: [], roll: [], pitch: [], yaw: [], knee: [], tau: [],
  })
  const [, force] = useState(0)

  useEffect(() => {
    if (!t) return
    const push = (k: string, v: number | undefined | null) => {
      if (v === null || v === undefined || Number.isNaN(v)) return
      const b = bufs.current[k]
      b.push(v); if (b.length > CAP) b.shift()
    }
    const b = t.battery, l = t.lowstate
    if (b && !isErr(b)) { push('soc', b.soc_pct); push('volt', b.voltage_v); push('curr', b.current_a) }
    if (l && !isErr(l)) {
      const r = l.imu_rpy || []
      push('roll', r[0]); push('pitch', r[1]); push('yaw', r[2])
      push('knee', l.legs?.avg_knee); push('tau', l.max_leg_tau)
    }
    force((x) => x + 1)
  }, [t?.ts])

  const B = bufs.current
  const bat = t?.battery
  const ls = t?.lowstate
  const batOff = isErr(bat)
  const soc = bat?.soc_pct ?? 0
  const low = !batOff && soc < LOW_SOC
  // the one green on this card is the state of charge; it turns to the warn colour under 20 %, muted when the BMS is silent
  const heroClass = batOff ? 'tg-big off' : low ? 'tg-big warn' : 'tg-big'

  return (
    <div className="tg">
      <div className="panel-title"><span className="ic">{Ico.battery()}</span> Telemetry</div>
      <div className="tg-hero">
        <div className="tg-hero-main">
          <span className={heroClass}>{batOff ? '--' : soc}</span>
          <span className="tg-big-unit">% SOC</span>
        </div>
        <div className="tg-hero-side">
          <div><b>{fmt(bat?.voltage_v, 1)}</b><span>V</span></div>
          <div><b>{fmt(bat?.current_a, 2)}</b><span>A</span></div>
          <div><b>{bat?.temp_max_c ?? '--'}</b><span>C</span></div>
        </div>
      </div>
      <div className="bar" role="progressbar" aria-label="State of charge" aria-valuenow={batOff ? undefined : soc} aria-valuemin={0} aria-valuemax={100}>
        <span className={low ? 'warn' : ''} style={{ width: `${batOff ? 0 : soc}%` }} />
      </div>

      <div className="tg-grid">
        <Sparkline data={B.volt} label="Voltage" unit="V" value={fmt(bat?.voltage_v, 1)} min={40} max={58} />
        <Sparkline data={B.curr} label="Current" unit="A" value={fmt(bat?.current_a, 2)} />
        <Sparkline data={B.roll} label="Roll" unit="deg" value={deg(ls?.imu_rpy?.[0])} />
        <Sparkline data={B.pitch} label="Pitch" unit="deg" value={deg(ls?.imu_rpy?.[1])} />
        <Sparkline data={B.yaw} label="Yaw" unit="deg" value={deg(ls?.imu_rpy?.[2])} />
        <Sparkline data={B.knee} label="Avg knee" unit="rad" value={fmt(ls?.legs?.avg_knee, 2)} />
      </div>
    </div>
  )
}
