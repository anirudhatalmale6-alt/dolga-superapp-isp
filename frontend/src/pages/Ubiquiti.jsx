import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { formatUptime } from '../components/Stat'

const REFRESH_MS = 20000

export default function Ubiquiti() {
  const { user } = useAuth()
  const [controllers, setControllers] = useState(null)
  const [controllerId, setControllerId] = useState(null)
  const [overview, setOverview] = useState(null)
  const [topology, setTopology] = useState(null)
  const [selected, setSelected] = useState(null)
  const [stations, setStations] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(null)

  useEffect(() => {
    api
      .uispControllers()
      .then((list) => {
        setControllers(list)
        if (list.length) setControllerId(list[0].id)
      })
      .catch((err) => setError(err.message))
  }, [])

  const load = useCallback(async () => {
    if (!controllerId) return
    try {
      const [ov, topo] = await Promise.all([
        api.uispOverview(controllerId),
        api.uispTopology(controllerId),
      ])
      setOverview(ov)
      setTopology(topo)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [controllerId])

  useEffect(() => {
    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  async function openSector(sector) {
    setSelected(sector)
    setStations(null)
    try {
      setStations(await api.uispStations(controllerId, sector.id))
    } catch (err) {
      setError(err.message)
    }
  }

  async function runAction(device, action, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(`${device.id}:${action}`)
    setError(null)
    try {
      const result = await api.uispAction(controllerId, device.id, action)
      setNotice(`${device.name}: ${result.message}`)
    } catch (err) {
      // Si esta versión de UISP no ofrece la acción, el mensaje lo dice.
      setError(`${device.name}: ${err.message}`)
    } finally {
      setBusy(null)
    }
  }

  const puedeReiniciar = user?.role === 'admin' || user?.role === 'soporte'
  const puedeActualizar = user?.role === 'admin'

  if (controllers && controllers.length === 0) {
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Ubiquiti / UISP</h1>
            <p className="page-sub">Sectores, antenas, radios y clientes conectados</p>
          </div>
        </div>
        <div className="card">
          <div className="empty">
            Todavía no hay ningún UISP registrado. Un administrador debe darlo de
            alta con la dirección del servidor y una App Key de solo lectura.
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Ubiquiti / UISP</h1>
          <p className="page-sub">Sectores, antenas, radios y clientes conectados</p>
        </div>
        <div className="kpi-row">
          <Kpi tone="ok" label="Sitios" value={overview?.sites_total} />
          <Kpi tone="info" label="Sectores" value={overview?.sectors_total} />
          <Kpi tone="danger" label="Sectores caídos" value={overview?.sectors_offline} />
          <Kpi tone="ok" label="Clientes en línea" value={overview?.stations_online} />
          <Kpi tone="warn" label="Señal débil" value={overview?.weak_signal_count} />
        </div>
      </div>

      {error && <div className="alert error" style={{ marginBottom: 14 }}>{error}</div>}
      {notice && (
        <div className="alert ok" style={{ marginBottom: 14 }}>
          {notice}{' '}
          <button className="btn ghost small" style={{ marginLeft: 8 }} onClick={() => setNotice(null)}>
            ok
          </button>
        </div>
      )}

      {overview?.weak_signal_count > 0 && (
        <div className="alert info" style={{ marginBottom: 14 }}>
          {overview.weak_signal_count} cliente(s) por debajo de -80 dBm. Son los
          candidatos a revisión de alineación o cambio de equipo.
        </div>
      )}

      <div className="stack">
        <div className="card">
          <div className="card-head">
            <h2>Sectores</h2>
            <span className="faint" style={{ fontSize: 12 }}>
              {topology ? `${topology.sectors.length} sector(es)` : '…'}
            </span>
          </div>
          {!topology ? (
            <div className="empty">Consultando UISP…</div>
          ) : topology.sectors.length === 0 ? (
            <div className="empty">UISP no reporta ningún equipo con rol de sector.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Sector</th>
                    <th className="tight">Sitio</th>
                    <th className="tight">Estado</th>
                    <th className="num tight">Clientes</th>
                    <th className="num tight">Señal media</th>
                    <th className="num tight">Peor señal</th>
                    <th className="num tight">Frecuencia</th>
                    <th className="tight">Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {topology.sectors.map((sector) => (
                    <tr
                      key={sector.id}
                      className={`clickable ${selected?.id === sector.id ? 'row-selected' : ''}`}
                      onClick={() => openSector(sector)}
                    >
                      <td>
                        <div className="cell-title">{sector.name}</div>
                        <div className="cell-sub">
                          {sector.model || '—'}
                          {sector.ssid ? ` · ${sector.ssid}` : ''}
                        </div>
                      </td>
                      <td className="dim tight nowrap">{sector.site_name || '—'}</td>
                      <td className="tight">
                        {sector.online ? (
                          <span className="badge ok">
                            <span className="dot" />
                            En Línea
                          </span>
                        ) : (
                          <span className="badge danger">
                            <span className="dot" />
                            Caído
                          </span>
                        )}
                      </td>
                      <td className="num tight">
                        {sector.clients_online}
                        {sector.clients_offline > 0 && (
                          <span className="faint"> /{sector.clients_total}</span>
                        )}
                      </td>
                      <td className="num tight">
                        <Signal dbm={sector.average_signal_dbm} />
                      </td>
                      <td className="num tight">
                        <Signal dbm={sector.worst_signal_dbm} />
                      </td>
                      <td className="num dim tight nowrap">
                        {sector.frequency_mhz ? `${sector.frequency_mhz} MHz` : 'n/d'}
                      </td>
                      <td className="tight" onClick={(e) => e.stopPropagation()}>
                        <DeviceActions
                          device={sector}
                          busy={busy}
                          puedeReiniciar={puedeReiniciar}
                          puedeActualizar={puedeActualizar}
                          onAction={runAction}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <h2>{selected ? `Clientes de ${selected.name}` : 'Clientes del sector'}</h2>
            {selected && (
              <span className="faint" style={{ fontSize: 12 }}>
                peor señal primero
              </span>
            )}
          </div>
          {!selected ? (
            <div className="empty">Selecciona un sector para ver sus clientes.</div>
          ) : !stations ? (
            <div className="empty">Consultando…</div>
          ) : stations.length === 0 ? (
            <div className="empty">Este sector no tiene estaciones asociadas en UISP.</div>
          ) : (
            <div className="table-wrap client-list">
            <table>
              <thead>
                <tr>
                  <th>Cliente</th>
                  <th className="num">Señal</th>
                  <th className="num">Distancia</th>
                  <th className="num">Conectado</th>
                  <th>Estado</th>
                  <th>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {stations.map((station) => (
                  <tr key={station.id}>
                    <td>
                      <div className="cell-title">{station.name}</div>
                      <div className="cell-sub mono">{station.ip_address || station.mac || '—'}</div>
                    </td>
                    <td className="num">
                      <Signal dbm={station.signal_dbm} quality={station.signal_quality} />
                    </td>
                    <td className="num dim">
                      {station.distance_m != null ? `${(station.distance_m / 1000).toFixed(1)} km` : 'n/d'}
                    </td>
                    <td className="num dim">
                      {station.uptime_seconds != null ? formatUptime(station.uptime_seconds) : '—'}
                    </td>
                    <td>
                      {station.online ? (
                        <span className="badge ok">
                          <span className="dot" />
                          en línea
                        </span>
                      ) : (
                        <span className="badge off">
                          <span className="dot" />
                          desconectado
                        </span>
                      )}
                    </td>
                    <td>
                      <DeviceActions
                        device={station}
                        busy={busy}
                        puedeReiniciar={puedeReiniciar}
                        puedeActualizar={puedeActualizar}
                        onAction={runAction}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>
      </div>

      {topology?.stations_without_sector?.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-head">
            <h2>Estaciones sin sector asociado</h2>
            <span className="faint" style={{ fontSize: 12 }}>
              UISP no reporta a qué antena están enlazadas; casi siempre están caídas
            </span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Cliente</th>
                <th>Sitio</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {topology.stations_without_sector.map((station) => (
                <tr key={station.id}>
                  <td className="cell-title">{station.name}</td>
                  <td className="dim">{station.site_name || '—'}</td>
                  <td>
                    <span className="badge off">
                      <span className="dot" />
                      {station.status || 'desconocido'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

/**
 * Acciones que la API de UISP permite sobre un equipo.
 *
 * Localizar es inofensivo (solo parpadea los LED) y por eso lo puede usar
 * cualquier rol. Reiniciar corta el servicio de ese enlace, así que va con
 * confirmación. Actualizar firmware queda solo para el administrador.
 */
function DeviceActions({ device, busy, puedeReiniciar, puedeActualizar, onAction }) {
  const trabajando = (action) => busy === `${device.id}:${action}`
  return (
    <div className="btn-row">
      <button
        className="btn small ghost"
        disabled={!device.online || trabajando('locate')}
        title={device.online ? 'Parpadea los LED para ubicarlo en la torre' : 'El equipo no responde'}
        onClick={() => onAction(device, 'locate')}
      >
        {trabajando('locate') ? '…' : 'Localizar'}
      </button>
      {puedeReiniciar && (
        <button
          className="btn small warnish"
          disabled={!device.online || trabajando('reboot')}
          onClick={() =>
            onAction(
              device,
              'reboot',
              `¿Reiniciar "${device.name}"? El enlace se cae mientras el equipo arranca.`,
            )
          }
        >
          {trabajando('reboot') ? '…' : 'Reiniciar'}
        </button>
      )}
      {puedeActualizar && (
        <button
          className="btn small"
          disabled={!device.online || trabajando('upgrade')}
          onClick={() =>
            onAction(
              device,
              'upgrade',
              `¿Actualizar el firmware de "${device.name}"? El equipo se reinicia al terminar.`,
            )
          }
        >
          {trabajando('upgrade') ? '…' : 'Firmware'}
        </button>
      )}
    </div>
  )
}

/** Muestra siempre el dBm crudo; el color es solo una ayuda visual. */
function Signal({ dbm, quality }) {
  if (dbm == null) return <span className="faint">n/d</span>
  // Excelente y buena comparten color: lo que hay que destacar es lo flojo.
  const level = dbm >= -70 ? 'ok' : dbm >= -80 ? 'warn' : 'danger'
  return (
    <span className={`temp ${level}`} title={quality || ''}>
      {dbm} dBm
    </span>
  )
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
