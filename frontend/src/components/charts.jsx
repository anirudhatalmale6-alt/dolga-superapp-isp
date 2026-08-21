/**
 * Gráficas en SVG puro, sin librerías externas.
 *
 * Todas reciben los datos que el backend guardó en el histórico; ninguna
 * inventa valores. Cuando no hay muestras suficientes lo dicen en vez de
 * dibujar una línea plana que parezca un dato real.
 */

function buildPath(values, width, height, padding = 2) {
  if (values.length < 2) return null
  const max = Math.max(...values, 0.0001)
  const min = Math.min(...values, 0)
  const span = max - min || 1
  const stepX = (width - padding * 2) / (values.length - 1)
  const points = values.map((value, index) => {
    const x = padding + index * stepX
    const y = height - padding - ((value - min) / span) * (height - padding * 2)
    return [x, y]
  })
  const line = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
  const area = `${line} L${points[points.length - 1][0].toFixed(2)},${height} L${points[0][0].toFixed(2)},${height} Z`
  return { line, area }
}

/** Minigráfica de la tabla de equipos. */
export function Sparkline({ values = [], width = 68, height = 22, color = 'var(--accent)' }) {
  const clean = values.filter((v) => v != null)
  const paths = buildPath(clean, width, height)
  if (!paths) {
    return <span className="faint" style={{ fontSize: 11 }}>—</span>
  }
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-hidden="true">
      <path d={paths.line} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  )
}

/** Anillo de porcentaje (CPU, RAM, almacenamiento). */
export function Donut({ value, size = 74, stroke = 7, color = 'var(--accent)', label }) {
  const safe = value == null ? null : Math.max(0, Math.min(100, value))
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const offset = safe == null ? circumference : circumference * (1 - safe / 100)

  return (
    <div className="donut" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--line)"
          strokeWidth={stroke}
        />
        {safe != null && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
            style={{ transition: 'stroke-dashoffset 0.5s ease' }}
          />
        )}
      </svg>
      <div className="donut-label">
        {safe == null ? <span className="faint">n/d</span> : `${Math.round(safe)}%`}
        {label && <div className="donut-sub">{label}</div>}
      </div>
    </div>
  )
}

/**
 * Curva de tráfico WAN con eje de tiempo y de Mbps.
 * `points` = [{ t, rx_mbps, tx_mbps, reachable }]
 */
export function TrafficChart({ points = [], height = 190 }) {
  const usable = points.filter((p) => p.rx_mbps != null || p.tx_mbps != null)

  if (usable.length < 2) {
    return (
      <div className="chart-empty">
        Todavía no hay suficientes muestras. El monitoreo va guardando una cada
        pocos segundos y la curva se dibuja sola.
      </div>
    )
  }

  const width = 640
  const padLeft = 46
  const padBottom = 22
  const padTop = 10
  const plotW = width - padLeft - 8
  const plotH = height - padBottom - padTop

  const rx = usable.map((p) => p.rx_mbps ?? 0)
  const tx = usable.map((p) => p.tx_mbps ?? 0)
  const max = Math.max(...rx, ...tx, 1)
  // Escala redondeada hacia arriba para que las guías caigan en números limpios.
  const step = max <= 10 ? 2 : max <= 50 ? 10 : max <= 120 ? 25 : 50
  const top = Math.ceil(max / step) * step
  const ticks = []
  for (let v = 0; v <= top; v += step) ticks.push(v)

  const x = (i) => padLeft + (i * plotW) / (usable.length - 1)
  const y = (v) => padTop + plotH - (v / top) * plotH

  const toPath = (values) =>
    values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const toArea = (values) =>
    `${toPath(values)} L${x(values.length - 1).toFixed(1)},${(padTop + plotH).toFixed(1)} L${padLeft},${(padTop + plotH).toFixed(1)} Z`

  const timeOf = (p) => {
    const d = new Date(p.t)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  const labelIndexes = [0, Math.floor((usable.length - 1) / 2), usable.length - 1]

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      height={height}
      preserveAspectRatio="none"
      className="traffic-chart"
    >
      <defs>
        <linearGradient id="gradRx" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#4c9aff" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#4c9aff" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="gradTx" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2dd4a7" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#2dd4a7" stopOpacity="0" />
        </linearGradient>
      </defs>

      {ticks.map((v) => (
        <g key={v}>
          <line x1={padLeft} x2={width - 8} y1={y(v)} y2={y(v)} stroke="var(--line-soft)" strokeWidth="1" />
          <text x={padLeft - 8} y={y(v) + 3.5} textAnchor="end" className="axis">
            {v}
          </text>
        </g>
      ))}

      <path d={toArea(rx)} fill="url(#gradRx)" />
      <path d={toPath(rx)} fill="none" stroke="#4c9aff" strokeWidth="1.8" strokeLinejoin="round" />
      <path d={toArea(tx)} fill="url(#gradTx)" />
      <path d={toPath(tx)} fill="none" stroke="#2dd4a7" strokeWidth="1.8" strokeLinejoin="round" />

      {labelIndexes.map((i) => (
        <text
          key={i}
          x={x(i)}
          y={height - 6}
          textAnchor={i === 0 ? 'start' : i === usable.length - 1 ? 'end' : 'middle'}
          className="axis"
        >
          {timeOf(usable[i])}
        </text>
      ))}
    </svg>
  )
}
