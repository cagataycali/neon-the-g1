interface Props {
  data: number[]
  color?: string
  height?: number
  min?: number
  max?: number
  fill?: boolean
  label?: string
  unit?: string
  value?: number | string
}

export default function Sparkline({
  data, color = 'var(--cyan)', height = 56, min, max, fill = true, label, unit, value,
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

  return (
    <div className="spark">
      {(label || value !== undefined) && (
        <div className="spark-head">
          <span className="spark-label">{label}</span>
          <span className="spark-value" style={{ color }}>
            {value}{unit && <span className="spark-unit"> {unit}</span>}
          </span>
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height, width: '100%', display: 'block' }}>
        {fill && area && <path d={area} fill={color} opacity={0.12} />}
        {path && <path d={path} fill="none" stroke={color} strokeWidth={1.6} vectorEffect="non-scaling-stroke" />}
      </svg>
    </div>
  )
}
