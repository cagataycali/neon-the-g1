interface Props {
  data: number[]
  /** 'ink' (default) draws in --sr-fg; 'accent' is reserved for the primary signal; 'warn' for a signal out of range. */
  tone?: 'ink' | 'accent' | 'warn'
  height?: number
  min?: number
  max?: number
  fill?: boolean
  label?: string
  unit?: string
  value?: number | string
}

/** A rolling sparkline. Colours come from the .spark tone classes in styles.css, never from inline paint. */
export default function Sparkline({
  data, tone = 'ink', height = 48, min, max, fill = true, label, unit, value,
}: Props) {
  const n = data.length
  const lo = min ?? (n ? Math.min(...data) : 0)
  const hi = max ?? (n ? Math.max(...data) : 1)
  const range = hi - lo || 1
  const W = 100, H = 100
  const pts = data.map((v, i) => {
    const x = n > 1 ? (i / (n - 1)) * W : 0
    const y = H - ((v - lo) / range) * H
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  const path = pts.length ? `M${pts.join(' L')}` : ''
  const area = pts.length ? `M0,${H} L${pts.join(' L')} L${W},${H} Z` : ''
  const title = label ? `${label}${value !== undefined ? ` ${value}${unit ? ` ${unit}` : ''}` : ''}` : undefined

  return (
    <div className={`spark ${tone === 'ink' ? '' : tone}`}>
      {(label || value !== undefined) && (
        <div className="spark-head">
          <span className="spark-label">{label}</span>
          <span className="spark-value">
            {value}{unit && <span className="spark-unit"> {unit}</span>}
          </span>
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height, width: '100%', display: 'block' }} role="img" aria-label={title ? `${title}, last ${n} samples` : 'sparkline'}>
        {fill && area && <path className="area" d={area} opacity={0.12} />}
        {path && <path d={path} fill="none" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />}
      </svg>
    </div>
  )
}
