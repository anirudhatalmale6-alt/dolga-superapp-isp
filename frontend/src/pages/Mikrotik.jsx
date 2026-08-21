import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Sparkline } from '../components/charts'
import { formatUptime } from '../components/Stat'

const REFRESH_MS = 10000
const SPARK_MINUTES = 30

export default function Mikrotik() {
  const navigate = useNavigate()
  const [fleet, setFleet] = useState(null)
  const [spark, setSpark] = useState({})
  const [search, setSearch] = useState('')
  const [site, setSite] = useState('')
  const [error, setError] = useState(null)
  const [now, setNow] = useState(new Date())

  const load = useCallback(async () => {
    try {
      const data = await api.fleet()
      setFleet(data)
      setNow(new Date())
      setError(null)
      // Las minigráficas salen del histórico guardado, no de nuevas consultas
      // a los routers: la tabla abre igual de rápido con 5 equipos que con 50.
      const online = data.devices.filter((d) => d.has_data)
      const series = await Promise.all(
        online.map((d) =>
          api
            .historyOf(d.id, SPARK_MINUTES)
            .then((h) => [d.id, h.points])
            .catch(() => [d.id, []]),
        ),
      )
      setSpark(Object.fromEntries(series))
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  const sites = fleet ? [...new Set(fleet.devices.map((d) => d.site).filter(Boolean))].sort() : []
  const rows = (fleet?.devices || []).filter((d) => {
    const needle = search.trim().toLowerCase()
    const matchSearch =
      !needle ||
      d.name.toLowerCase().includes(needle) ||
      (d.host || '').includes(needle) ||
      (d.model || '').toLowerCase().includes(needle)
    return matchSearch && (!site || d.site === site)
  })

  return (
    <>
      <div className="page-head">
        <div>
          <h1>MikroTik</h1>
          <p className="page-sub">Monitoreo y gestión de todos tus equipos MikroTik</p>
        </div>
        <div className="kpi-row">
          <Kpi tone="ok" label="Equipos Totales" value={fleet?.devices_total} />
          <Kpi tone="info" label="En Línea" value={fleet?.devices_online} />
          <Kpi tone="danger" label="Fuera de Línea" value={fleet?.devices_offline} />
          <Kpi tone="warn" label="Clientes Totales" value={fleet?.clients_total} />
          <div className="kpi kpi-clock">
            <div className="kpi-label">
              {now.toLocaleDateString('es', { day: '2-digit', month: 'short', year: 'numeric' })}
            </div>
            <div className="kpi-value-sm">
              {now.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })}
            </div>
          </div>
        </div>
      </div>

      {error && <div className="alert error" style={{ marginBottom: 14 }}>{error}</div>}

      {fleet && !fleet.devices.some((d) => d.has_data) && (
        <div className="alert info" style={{ marginBottom: 14 }}>
          El servicio de monitoreo todavía no ha tomado su primera muestra. Los
          indicadores y las gráficas se llenan en cuanto complete el primer ciclo
          (cada {fleet.monitor_interval_seconds} segundos).
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>Equipos MikroTik</h2>
          <div className="inline">
            <input
              className="search"
              placeholder="Buscar equipo…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select value={site} onChange={(e) => setSite(e.target.value)}>
              <option value="">Todos los sitios</option>
              {sites.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </div>

        {!fleet ? (
          <div className="empty">Cargando equipos…</div>
        ) : rows.length === 0 ? (
          <div className="empty">Ningún equipo coincide con el filtro.</div>
        ) : (
          <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Nombre del Equipo</th>
                <th className="tight">Modelo</th>
                <th className="tight">Sitio</th>
                <th className="tight">Estado</th>
                <th className="num tight">Clientes</th>
                <th className="num tight">CPU</th>
                <th className="num tight">Temp.</th>
                <th className="num tight">Uptime</th>
                <th className="num tight">Tráfico WAN</th>
                <th className="tight" />
              </tr>
            </thead>
            <tbody>
              {rows.map((device) => {
                const points = spark[device.id] || []
                return (
                  <tr key={device.id} onClick={() => navigate(`/mikrotik/${device.id}`)} className="clickable">
                    <td>
                      <div className="cell-title">{device.name}</div>
                      <div className="cell-sub mono">{device.host}</div>
                    </td>
                    <td className="dim tight nowrap">{device.model || '—'}</td>
                    <td className="dim tight nowrap">{device.site || '—'}</td>
                    <td className="tight">
                      {device.online ? (
                        <span className="badge ok">
                          <span className="dot" />
                          En Línea
                        </span>
                      ) : device.has_data ? (
                        <span className="badge danger" title={device.last_error || ''}>
                          <span className="dot" />
                          Fuera de Línea
                        </span>
                      ) : (
                        <span className="badge off">
                          <span className="dot" />
                          sin datos
                        </span>
                      )}
                    </td>
                    <td className="num tight">{device.online ? device.clients_online : '—'}</td>
                    <td className="num tight">
                      <div className="inline-right">
                        <span>{device.cpu_load_percent != null ? `${device.cpu_load_percent}%` : '—'}</span>
                        <Sparkline values={points.map((p) => p.cpu)} />
                      </div>
                    </td>
                    <td className="num tight">
                      {device.temperature_c != null ? (
                        <span className={tempClass(device.temperature_c)}>{device.temperature_c}°C</span>
                      ) : (
                        <span className="faint" title="Esta placa no trae sensor de temperatura">
                          n/d
                        </span>
                      )}
                    </td>
                    <td className="num dim tight nowrap">
                      {device.uptime_seconds != null ? formatUptime(device.uptime_seconds) : '—'}
                    </td>
                    <td className="num tight nowrap">
                      {device.wan_rx_mbps != null ? (
                        <>
                          <span className="wan-rx">↓ {device.wan_rx_mbps.toFixed(1)}</span>
                          <span className="wan-sep">/</span>
                          <span className="wan-tx">↑ {device.wan_tx_mbps?.toFixed(1) ?? '—'}</span>
                          <span className="faint"> Mbps</span>
                        </>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="tight chevron" onClick={(e) => e.stopPropagation()}>
                      <Link to={`/mikrotik/${device.id}`} title={`Abrir ${device.name}`}>
                        ›
                      </Link>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>
    </>
  )
}

function tempClass(value) {
  if (value >= 70) return 'temp danger'
  if (value >= 58) return 'temp warn'
  return 'temp ok'
}

function Kpi({ label, value, tone }) {
  return (
    <div className="kpi">
      <span className={`kpi-dot ${tone}`} />
      <div>
        <div className="kpi-label">{label}</div>
        <div className="kpi-value">{value ?? '—'}</div>
      </div>
    </div>
  )
}
