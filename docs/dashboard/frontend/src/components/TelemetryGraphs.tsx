import { useEffect, useRef, useState } from 'react'
import type { Telemetry } from '../types'
import Sparkline from './Sparkline'
import { isErr, fmt, deg } from '../lib/util'

const CAP = 120

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
  const soc = bat?.soc_pct ?? 0
  const socColor = soc > 50 ? 'var(--green)' : soc > 20 ? 'var(--amber)' : 'var(--pink)'

  return (
    <div className="tg">
      {/* battery hero */}
      <div className="tg-hero">
        <div className="tg-hero-main">
          <span className="tg-big" style={{ color: socColor }}>{soc}</span>
          <span className="tg-big-unit">% SOC</span>
        </div>
        <div className="tg-hero-side">
          <div><b>{fmt(bat?.voltage_v, 1)}</b><span>V</span></div>
          <div><b>{fmt(bat?.current_a, 2)}</b><span>A</span></div>
          <div><b>{bat?.temp_max_c ?? '—'}</b><span>°C</span></div>
        </div>
      </div>
      <div className="bar"><span style={{ width: `${soc}%`, background: socColor, boxShadow: `0 0 12px ${socColor}` }} /></div>

      <div className="tg-grid">
        <Sparkline data={B.volt} color="var(--cyan)" label="Voltage" unit="V" value={fmt(bat?.voltage_v, 1)} min={40} max={58} />
        <Sparkline data={B.curr} color="var(--purple)" label="Current" unit="A" value={fmt(bat?.current_a, 2)} />
        <Sparkline data={B.roll} color="var(--green)" label="Roll" unit="°" value={deg(ls?.imu_rpy?.[0])} />
        <Sparkline data={B.pitch} color="var(--amber)" label="Pitch" unit="°" value={deg(ls?.imu_rpy?.[1])} />
        <Sparkline data={B.yaw} color="var(--cyan)" label="Yaw" unit="°" value={deg(ls?.imu_rpy?.[2])} />
        <Sparkline data={B.knee} color="var(--pink)" label="Avg Knee" unit="rad" value={fmt(ls?.legs?.avg_knee, 2)} />
      </div>
    </div>
  )
}
