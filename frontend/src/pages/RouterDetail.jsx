import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { canManageBilling, useAuth } from '../auth'
import { formatUptime } from '../components/Stat'
import { Donut, Sparkline, TrafficChart } from '../components/charts'
import SuspensionModal from '../components/SuspensionModal'
import ShutdownModal from '../components/ShutdownModal'

const TABS = [
  { key: 'resumen', label: 'Resumen' },
  { key: 'interfaces', label: 'Interfaces' },
  { key: 'pppoe', label: 'Clientes PPPoE' },
]

const REFRESH_MS = 12000
const HISTORY_MINUTES = 30

export default function RouterDetail() {
  const { deviceId } = useParams()
  const { user } = useAuth()
  const [tab, setTab] = useState('resumen')

  const [device, setDevice] = useState(null)
  const [resource, setResource] = useState(null)
  const [board, setBoard] = useState(null)
  const [health, setHealth] = useState(null)
  const [wan, setWan] = useState(null)
  const [interfaces, setInterfaces] = useState(null)
  const [pppoe, setPppoe] = useState(null)
  const [profiles, setProfiles] = useState([])
  const [history, setHistory] = useState([])
  const [search, setSearch] = useState('')
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [modal, setModal] = useState(null)
  const [busyAction, setBusyAction] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const searchRef = useRef(search)
  searchRef.current = search

  const load = useCallback(
    async ({ silent } = {}) => {
      if (!silent) setError(null)
      setRefreshing(true)
      try {
        const [dev, res, hlt, wn, ifaces, pp, hist] = await Promise.all([
          api.device(deviceId),
          api.resource(deviceId),
          api.deviceHealth(deviceId),
          api.wan(deviceId),
          api.interfaces(deviceId),
          api.pppoeOverview(deviceId, searchRef.current),
          api.historyOf(deviceId, HISTORY_MINUTES).catch(() => ({ points: [] })),
        ])
        setDevice(dev)
        setResource(res)
        setHealth(hlt)
        setWan(wn)
        setInterfaces(ifaces)
        setPppoe(pp)
        setHistory(hist.points || [])
        setError(null)
      } catch (err) {
        setError(err.message)
      } finally {
        setRefreshing(false)
      }
    },
    [deviceId],
  )

  useEffect(() => {
    load()
    api.routerboard(deviceId).then(setBoard).catch(() => {})
    api.profiles(deviceId).then(setProfiles).catch(() => {})
  }, [deviceId, load])

  useEffect(() => {
    const timer = setInterval(() => load({ silent: true }), REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  useEffect(() => {
    const timer = setTimeout(() => {
      api
        .pppoeOverview(deviceId, search)
        .then(setPppoe)
        .catch((err) => setError(err.message))
    }, 250)
    return () => clearTimeout(timer)
  }, [search, deviceId])

  async function applySuspension(payload) {
    const action = modal.mode === 'suspend' ? api.suspend : api.restore
    const result = await action(deviceId, payload)
    setModal(null)
    setNotice(
      `${payload.username}: ${modal.mode === 'suspend' ? 'servicio suspendido' : 'servicio reactivado'} · ${result.actions.join(' · ')}`,
    )
    await load({ silent: true })
  }

  async function kick(username) {
    try {
      const result = await api.kick(deviceId, username)
      setNotice(
        result.session_closed
          ? `Sesión de ${username} cerrada; el equipo del cliente reconectará solo.`
          : `${username} no tenía sesión activa.`,
      )
      await load({ silent: true })
    } catch (err) {
      setError(err.message)
    }
  }

  async function checkUpdates() {
    setBusyAction('updates')
    setError(null)
    try {
      const result = await api.updates(deviceId)
      setNotice(
        result.update_available
          ? `Hay una versión nueva de RouterOS: ${result.latest_version} (instalada ${result.installed_version}). La instalación reinicia el equipo, conviene hacerla fuera de horario pico.`
          : `RouterOS está al día en la versión ${result.installed_version || 'instalada'}.`,
      )
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyAction(null)
    }
  }

  async function reboot() {
    if (!window.confirm(`¿Reiniciar "${device?.name}"? Vuelve solo en uno o dos minutos.`)) return
    setBusyAction('reboot')
    setError(null)
    try {
      const result = await api.reboot(deviceId)
      setNotice(result.message)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyAction(null)
    }
  }

  async function shutdown(confirmName) {
    const result = await api.shutdown(deviceId, confirmName)
    setModal(null)
    setNotice(result.message)
  }

  const esAdmin = user?.role === 'admin'
  const puedeCortar = canManageBilling(user)
  const enLinea = !!resource

  return (
    <>
      <div className="detail-head card">
        <div className="detail-head-main">
          <Link to="/mikrotik" className="btn small ghost">
            ← Volver a la lista
          </Link>
          <div>
            <div className="inline">
              <h1 style={{ margin: 0 }}>{board?.model || device?.board_name || device?.name}</h1>
              {enLinea ? (
                <span className="badge ok">
                  <span className="dot" />
                  EN LÍNEA
                </span>
              ) : (
                <span className="badge danger">
                  <span className="dot" />
                  SIN CONEXIÓN
                </span>
              )}
            </div>
            <div className="detail-meta">
              <Meta label="Sitio" value={device?.site} />
              <Meta label="Ubicación" value={device?.location} />
              <Meta label="RouterOS" value={resource?.version} mono />
              <Meta label="Uptime" value={resource?.uptime} mono />
              <Meta label="Serial" value={board?.serial_number} mono />
              <Meta label="IP" value={device?.host} mono />
            </div>
          </div>
        </div>
        <div className="inline">
          {refreshing && <div className="spinner" />}
          <button className="btn" onClick={checkUpdates} disabled={busyAction === 'updates'}>
            {busyAction === 'updates' ? 'Revisando…' : 'Revisar actualización'}
          </button>
          <button className="btn warnish" onClick={reboot} disabled={busyAction === 'reboot'}>
            Reiniciar
          </button>
          {esAdmin && (
            <button className="btn danger" onClick={() => setModal({ mode: 'shutdown' })}>
              Apagar
            </button>
          )}
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

      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
            {t.key === 'pppoe' && pppoe ? ` (${pppoe.total_secrets})` : ''}
          </button>
        ))}
      </div>

      {tab === 'resumen' && (
        <Resumen
          resource={resource}
          health={health}
          pppoe={pppoe}
          wan={wan}
          history={history}
          device={device}
        />
      )}
      {tab === 'interfaces' && <Interfaces deviceId={deviceId} interfaces={interfaces} />}
      {tab === 'pppoe' && (
        <Pppoe
          pppoe={pppoe}
          search={search}
          setSearch={setSearch}
          puedeCortar={puedeCortar}
          onSuspend={(client) => setModal({ mode: 'suspend', client })}
          onRestore={(client) => setModal({ mode: 'restore', client })}
          onKick={kick}
        />
      )}

      {modal?.mode === 'shutdown' && (
        <ShutdownModal
          deviceName={device?.name || ''}
          onCancel={() => setModal(null)}
          onConfirm={shutdown}
        />
      )}
      {(modal?.mode === 'suspend' || modal?.mode === 'restore') && (
        <SuspensionModal
          mode={modal.mode}
          client={modal.client}
          profiles={profiles}
          onCancel={() => setModal(null)}
          onConfirm={applySuspension}
        />
      )}
    </>
  )
}

function Meta({ label, value, mono }) {
  return (
    <div className="meta">
      <span className="meta-label">{label}:</span>
      <span className={mono ? 'mono' : undefined}>{value || '—'}</span>
    </div>
  )
}

function Resumen({ resource, health, pppoe, wan, history, device }) {
  if (!resource) {
    return (
      <div className="card">
        <div className="empty">Consultando el router…</div>
      </div>
    )
  }

  const rxNow = history.length ? history[history.length - 1].rx_mbps : null
  const txNow = history.length ? history[history.length - 1].tx_mbps : null

  return (
    <div className="stack">
      <div className="grid grid-6">
        <div className="card stat">
          <div className="stat-label">Estado</div>
          <div className="inline" style={{ marginTop: 4 }}>
            <span className="badge ok">
              <span className="dot" />
              En Línea
            </span>
          </div>
          <div className="stat-note">Vía {resource.transport === 'rest' ? 'REST v7' : 'API 8728'}</div>
        </div>

        <div className="card stat">
          <div className="stat-label">Clientes Conectados</div>
          <div className="stat-value">{pppoe ? pppoe.online : '—'}</div>
          <div className="stat-note">
            {pppoe ? `${pppoe.suspended} suspendidos · ${pppoe.total_secrets} en total` : 'Activos ahora'}
          </div>
        </div>

        <div className="card stat stat-donut">
          <div>
            <div className="stat-label">CPU</div>
            <Sparkline values={history.map((p) => p.cpu)} width={76} height={26} />
          </div>
          <Donut value={resource.cpu_load_percent} size={62} />
        </div>

        <div className="card stat">
          <div className="stat-label">Temperatura</div>
          {health?.available ? (
            <>
              <div className="stat-value">
                {health.temperature_c}
                <span className="stat-unit">°C</span>
              </div>
              <div className="stat-note">{health.temperature_c >= 70 ? 'Alta' : 'Normal'}</div>
            </>
          ) : (
            <>
              <div className="stat-value faint" style={{ fontSize: 20 }}>
                n/d
              </div>
              {/* No inventamos un número: esta placa no trae sensor. */}
              <div className="stat-note">Esta placa no reporta temperatura</div>
            </>
          )}
        </div>

        <div className="card stat stat-donut">
          <div>
            <div className="stat-label">Memoria RAM</div>
            <div className="stat-note" style={{ marginTop: 6 }}>
              {resource.memory_used_human} de {resource.memory_total_human}
            </div>
          </div>
          <Donut value={resource.memory_used_percent} color="#a78bfa" size={62} />
        </div>

        <div className="card stat stat-donut">
          <div>
            <div className="stat-label">Almacenamiento</div>
            <div className="stat-note" style={{ marginTop: 6 }}>
              Disco interno
            </div>
          </div>
          <Donut value={resource.disk_used_percent} color="#4c9aff" size={62} />
        </div>
      </div>

      <div className="grid grid-traffic">
        <div className="card">
          <div className="card-head">
            <h2>Tráfico hacia la Calle (WAN)</h2>
            <div className="inline legend">
              <span className="legend-item">
                <i style={{ background: '#4c9aff' }} />
                Descarga {rxNow != null ? `${rxNow.toFixed(2)} Mbps` : ''}
              </span>
              <span className="legend-item">
                <i style={{ background: '#2dd4a7' }} />
                Subida {txNow != null ? `${txNow.toFixed(2)} Mbps` : ''}
              </span>
            </div>
          </div>
          <div className="card-body">
            <TrafficChart points={history} />
            <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>
              Últimos {HISTORY_MINUTES} minutos. RouterOS no guarda histórico: estas
              muestras las toma y almacena la plataforma
              {device?.wan_interface ? ` sobre ${device.wan_interface}` : ''}.
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Interfaces WAN</h2>
          </div>
          {!wan ? (
            <div className="empty">Cargando…</div>
          ) : wan.length === 0 ? (
            <div className="empty">No se detectó ninguna ruta por defecto.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Interfaz</th>
                  <th>Estado</th>
                  <th>IP / Gateway</th>
                  <th className="num">Descarga</th>
                  <th className="num">Subida</th>
                </tr>
              </thead>
              <tbody>
                {wan.map((link) => (
                  <tr key={link.interface}>
                    <td className="mono cell-title">{link.interface}</td>
                    <td>
                      {link.connected ? (
                        <span className="text-ok">Conectado</span>
                      ) : (
                        <span className="text-danger">Desconectado</span>
                      )}
                    </td>
                    <td className="mono dim">
                      {link.address ? `${link.address.split('/')[0]} / ${link.gateway || '—'}` : '—'}
                    </td>
                    <td className="num">
                      {link.rx_mbps != null ? `${link.rx_mbps.toFixed(2)} Mbps` : '—'}
                    </td>
                    <td className="num">
                      {link.tx_mbps != null ? `${link.tx_mbps.toFixed(2)} Mbps` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

function Interfaces({ deviceId, interfaces }) {
  const [traffic, setTraffic] = useState({})
  const [loadingIface, setLoadingIface] = useState(null)

  async function measure(name) {
    setLoadingIface(name)
    try {
      const data = await api.interfaceTraffic(deviceId, name)
      setTraffic((t) => ({ ...t, [name]: data }))
    } finally {
      setLoadingIface(null)
    }
  }

  if (!interfaces)
    return (
      <div className="card">
        <div className="empty">Cargando interfaces…</div>
      </div>
    )

  return (
    <div className="card">
      <div className="card-head">
        <h2>Interfaces</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          Contadores acumulados desde el último reinicio
        </span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Interfaz</th>
            <th>Tipo</th>
            <th>MAC</th>
            <th className="num">RX</th>
            <th className="num">TX</th>
            <th className="num">Caídas</th>
            <th>Estado</th>
            <th className="num">Tráfico ahora</th>
          </tr>
        </thead>
        <tbody>
          {interfaces.map((iface) => {
            const live = traffic[iface.name]
            return (
              <tr key={iface.id || iface.name}>
                <td>
                  <div className="cell-title mono">{iface.name}</div>
                  {iface.comment && <div className="cell-sub">{iface.comment}</div>}
                </td>
                <td className="dim">{iface.type}</td>
                <td className="mono dim">{iface.mac_address || '—'}</td>
                <td className="num">{iface.rx_human || '—'}</td>
                <td className="num">{iface.tx_human || '—'}</td>
                <td className="num dim">{iface.link_downs ?? '—'}</td>
                <td>
                  {iface.disabled ? (
                    <span className="badge off">
                      <span className="dot" />
                      deshabilitada
                    </span>
                  ) : iface.running ? (
                    <span className="badge ok">
                      <span className="dot" />
                      activa
                    </span>
                  ) : (
                    <span className="badge warn">
                      <span className="dot" />
                      sin enlace
                    </span>
                  )}
                </td>
                <td className="num">
                  {live ? (
                    <span className="mono">
                      ↓ {live.rx_mbps} / ↑ {live.tx_mbps} Mbps
                    </span>
                  ) : (
                    <button
                      className="btn small ghost"
                      disabled={iface.disabled || loadingIface === iface.name}
                      onClick={() => measure(iface.name)}
                    >
                      {loadingIface === iface.name ? 'midiendo…' : 'medir'}
                    </button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Pppoe({ pppoe, search, setSearch, puedeCortar, onSuspend, onRestore, onKick }) {
  if (!pppoe)
    return (
      <div className="card">
        <div className="empty">Cargando clientes…</div>
      </div>
    )

  return (
    <div className="card">
      <div className="card-head">
        <h2>Clientes PPPoE</h2>
        <div className="inline">
          <span className="faint" style={{ fontSize: 12 }}>
            {pppoe.online} en línea · {pppoe.offline} desconectados · {pppoe.suspended} suspendidos
          </span>
          <input
            className="search"
            placeholder="Buscar por usuario o nombre…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>
      {pppoe.clients.length === 0 ? (
        <div className="empty">Ningún cliente coincide con la búsqueda.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Usuario</th>
              <th>Perfil</th>
              <th>IP</th>
              <th>MAC</th>
              <th className="num">Conectado</th>
              <th>Estado</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {pppoe.clients.slice(0, 200).map((client) => (
              <tr key={client.username}>
                <td>
                  <div className="cell-title mono">{client.username}</div>
                  {client.comment && <div className="cell-sub">{client.comment}</div>}
                </td>
                <td className="dim">{client.profile || '—'}</td>
                <td className="mono dim">{client.session?.address || client.remote_address || '—'}</td>
                <td className="mono dim">{client.session?.caller_id || '—'}</td>
                <td className="num dim">
                  {client.session ? formatUptime(client.session.uptime_seconds) : '—'}
                </td>
                <td>
                  {client.status === 'online' && (
                    <span className="badge ok">
                      <span className="dot" />
                      en línea
                    </span>
                  )}
                  {client.status === 'offline' && (
                    <span className="badge off">
                      <span className="dot" />
                      desconectado
                    </span>
                  )}
                  {client.status === 'suspendido' && (
                    <span className="badge danger">
                      <span className="dot" />
                      suspendido
                    </span>
                  )}
                </td>
                <td>
                  <div className="btn-row">
                    {client.online && (
                      <button className="btn small ghost" onClick={() => onKick(client.username)}>
                        Reconectar
                      </button>
                    )}
                    {puedeCortar &&
                      (client.status === 'suspendido' ? (
                        <button className="btn small primary" onClick={() => onRestore(client)}>
                          Reactivar
                        </button>
                      ) : (
                        <button
                          className="btn small danger"
                          disabled={!client.id}
                          title={client.id ? '' : 'Cliente autenticado por RADIUS, no hay secret local'}
                          onClick={() => onSuspend(client)}
                        >
                          Suspender
                        </button>
                      ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {pppoe.clients.length > 200 && (
        <div className="empty" style={{ padding: '14px 16px' }}>
          Mostrando los primeros 200 de {pppoe.clients.length}. Usa el buscador para
          encontrar un cliente puntual; la paginación completa llega con el módulo
          de Clientes.
        </div>
      )}
    </div>
  )
}
