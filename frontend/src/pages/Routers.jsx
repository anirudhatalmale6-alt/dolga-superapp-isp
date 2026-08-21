import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

export default function Routers() {
  const [devices, setDevices] = useState(null)
  const [status, setStatus] = useState({})
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    api
      .devices()
      .then(async (list) => {
        if (cancelled) return
        setDevices(list)
        // Consultamos cada equipo en paralelo: un router caído no bloquea al resto.
        list.forEach((device) => {
          api
            .testConnection(device.id)
            .then((result) => !cancelled && setStatus((s) => ({ ...s, [device.id]: result })))
            .catch((err) =>
              !cancelled && setStatus((s) => ({ ...s, [device.id]: { ok: false, error: err.message } })),
            )
        })
      })
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <>
      <div className="page-head">
        <div>
          <h1>MikroTik</h1>
          <p className="page-sub">
            Equipos administrados. La conexión sale del servidor, nunca del navegador.
          </p>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <div className="card-head">
          <h2>Routers registrados</h2>
          <span className="faint" style={{ fontSize: 12 }}>
            {devices ? `${devices.length} equipo(s)` : '…'}
          </span>
        </div>
        {!devices ? (
          <div className="empty">Cargando equipos…</div>
        ) : devices.length === 0 ? (
          <div className="empty">Todavía no hay routers registrados.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Equipo</th>
                <th>Host</th>
                <th>Transporte</th>
                <th>RouterOS</th>
                <th>Estado</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {devices.map((device) => {
                const state = status[device.id]
                return (
                  <tr key={device.id}>
                    <td>
                      <div className="cell-title">{device.name}</div>
                      {device.site && <div className="cell-sub">{device.site}</div>}
                    </td>
                    <td className="mono dim">{device.host}</td>
                    <td className="dim">
                      {state?.ok
                        ? state.transport === 'rest'
                          ? 'REST v7'
                          : 'API 8728'
                        : device.api_mode === 'auto'
                          ? 'auto'
                          : device.api_mode}
                    </td>
                    <td className="mono dim">{state?.version || device.routeros_version || '—'}</td>
                    <td>
                      {!state ? (
                        <span className="badge off">
                          <span className="dot" />
                          consultando
                        </span>
                      ) : state.ok ? (
                        <span className="badge ok">
                          <span className="dot" />
                          en línea
                        </span>
                      ) : (
                        <span className="badge danger" title={state.error}>
                          <span className="dot" />
                          sin conexión
                        </span>
                      )}
                    </td>
                    <td>
                      <div className="btn-row">
                        <Link className="btn small" to={`/mikrotik/${device.id}`}>
                          Administrar
                        </Link>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
