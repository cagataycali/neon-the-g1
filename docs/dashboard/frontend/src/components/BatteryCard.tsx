import type { Battery } from '../types'
import { isErr, fmt } from '../lib/util'
import { Ico } from './Icons'

export default function BatteryCard({ b }: { b?: Battery }) {
  const soc = b?.soc_pct ?? 0
  const off = isErr(b)
  const low = soc < 20
  return (
    <div className="card span-4">
      <div className="card-title"><span className="ic">{Ico.battery()}</span> Battery</div>
      {off ? (
        <div className="empty">offline, no BMS data</div>
      ) : (
        <>
          <div className="metric">
            <span className={low ? 'val txt-warn' : 'val txt-green'}>{soc}</span>
            <span className="unit">% SOC</span>
          </div>
          <div className="bar" role="progressbar" aria-label="State of charge" aria-valuenow={soc} aria-valuemin={0} aria-valuemax={100}><span className={low ? 'warn' : ''} style={{ width: `${soc}%` }} /></div>
          <div style={{ marginTop: 16 }}>
            <div className="metric-row"><span className="k">Voltage</span><span className="v">{fmt(b?.voltage_v, 1)} V</span></div>
            <div className="metric-row"><span className="k">Current</span><span className="v">{fmt(b?.current_a, 2)} A</span></div>
            <div className="metric-row"><span className="k">Health</span><span className="v">{b?.soh_pct ?? '--'} %</span></div>
            <div className="metric-row"><span className="k">Max temp</span><span className="v">{b?.temp_max_c ?? '--'} C</span></div>
            <div className="metric-row"><span className="k">Cycles</span><span className="v">{b?.cycle ?? '--'}</span></div>
          </div>
        </>
      )}
    </div>
  )
}
