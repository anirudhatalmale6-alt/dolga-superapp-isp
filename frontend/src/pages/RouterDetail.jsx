import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { canManageBilling, useAuth } from '../auth'
import { Stat, formatUptime } from '../components/Stat'
import SuspensionModal from '../components/SuspensionModal'

const TABS = [
  { key: 'resumen', label: 'Resumen' },
  { key: 'interfaces', label: 'Interfaces' },
  { key: 'pppoe', label: 'Clientes PPPoE' },
]

const REFRESH_MS = 15000

export default function RouterDetail() {
  const { deviceId } = useParams()
  const { user } = useAuth()
  const [tab, setTab] = useState('resumen')

  const [device, setDevice] = useState(null)
  const [resource, setResource] = useState(null)
  const [board, setBoard] = useState(null)
  const [interfaces, setInterfaces] = useState(null)
  const [pppoe, setPppoe] = useState(null)
  const [profiles, setProfiles] = useState([])
  const [search, setSearch] = useState('')
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [modal, setModal] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const searchRef = useRef(search)
  searchRef.current = search

  const load = useCallback(
    async ({ silent } = {}) => {
      if (!silent) setError(null)
      setRefreshing(true)
      try {
        const [dev, res, ifaces, pp] = await Promise.all([
          api.device(deviceId),
          api.resource(deviceId),
          api.interfaces(deviceId),
          api.pppoeOverview(deviceId, searchRef.current),
        ])
        setDevice(dev)
        setResource(res)
        setInterfaces(ifaces)
        setPppoe(pp)
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

  // Refresco periódico: el estado de la red cambia solo.
  useEffect(() => {
    const timer = setInterval(() => load({ silent: true }), REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  // Buscar clientes sin recargar todo el router.
  useEffect(() => {
    const timer = setTimeout(() => {
      api
        .pppoeOverview(deviceId, search)
        .then(setPppoe)
        .catch((err) => setError(err.message))
    }, 250)
    return () => clearTimeout(timer)
  }, [search, deviceId])

  async function applyAction(payload) {
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

  const puedeCortar = canManageBilling(user)

  return (
    <>
      <div className="page-head">
        <div>
          <div style={{ fontSize: 12, marginBottom: 6 }}>
            <Link to="/mikrotik" className="faint">
              MikroTik
            </Link>
            <span className="faint"> / </span>
            <span className="dim">{device?.name || '…'}</span>
          </div>
          <h1>{resource?.identity || device?.name || 'Router'}</h1>
          <p className="page-sub">
            {device?.host}
            {board?.model ? ` · ${board.model}` : ''}
            {resource?.version ? ` · RouterOS ${resource.version}` : ''}
            {resource?.transport ? ` · vía ${resource.transport === 'rest' ? 'REST v7' : 'API 8728'}` : ''}
          </p>
        </div>
        <div className="inline">
          {refreshing && <div className="spinner" />}
          <button className="btn small" onClick={() => load()}>
            Actualizar
          </button>
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
        <Resumen resource={resource} board={board} pppoe={pppoe} interfaces={interfaces} />
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

      {modal && (
        <SuspensionModal
          mode={modal.mode}
          client={modal.client}
          profiles={profiles}
          onCancel={() => setModal(null)}
          onConfirm={applyAction}
        />
      )}
    </>
  )
}

function Resumen({ resource, board, pppoe, interfaces }) {
  if (!resource) return <div className="card"><div className="empty">Consultando el router…</div></div>

  const memGb = resource.memory_total_bytes
    ? (resource.memory_total_bytes / 1073741824).toFixed(1)
    : null

  return (
    <div className="stack">
      <div className="grid grid-4">
        <Stat
          label="CPU"
          value={resource.cpu_load_percent ?? '—'}
          unit="%"
          note={`${resource.cpu_count || '?'} núcleos · ${resource.cpu || ''}`}
          percent={resource.cpu_load_percent}
        />
        <Stat
          label="Memoria"
          value={resource.memory_used_percent ?? '—'}
          unit="%"
          note={`${resource.memory_used_human || '—'} de ${resource.memory_total_human || `${memGb} GiB`}`}
          percent={resource.memory_used_percent}
        />
        <Stat
          label="Almacenamiento"
          value={resource.disk_used_percent ?? '—'}
          unit="%"
          note="Espacio usado en el disco interno"
          percent={resource.disk_used_percent}
        />
        <Stat
          label="Uptime"
          value={formatUptime(resource.uptime_seconds)}
          note={`Encendido desde hace ${resource.uptime || '—'}`}
        />
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Clientes PPPoE</h2>
          </div>
          <div className="card-body">
            {pppoe ? (
              <div className="grid" style={{ gap: 10, gridTemplateColumns: "repeat(4, 1fr)" }}>
                <MiniStat label="En línea" value={pppoe.online} tone="ok" />
                <MiniStat label="Desconectados" value={pppoe.offline} tone="off" />
                <MiniStat label="Suspendidos" value={pppoe.suspended} tone="danger" />
                <MiniStat label="Total" value={pppoe.total_secrets} tone="info" />
              </div>
            ) : (
              <div className="dim">Cargando…</div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Equipo</h2>
          </div>
          <div className="card-body">
            <dl style={{ margin: 0 }}>
              <div className="kv">
                <dt>Modelo</dt>
                <dd>{board?.model || resource.board_name || '—'}</dd>
              </div>
              <div className="kv">
                <dt>Serie</dt>
                <dd className="mono">{board?.serial_number || '—'}</dd>
              </div>
              <div className="kv">
                <dt>RouterOS</dt>
                <dd className="mono">{resource.version || '—'}</dd>
              </div>
              <div className="kv">
                <dt>Firmware</dt>
                <dd className="mono">{board?.current_firmware || '—'}</dd>
              </div>
              <div className="kv">
                <dt>Arquitectura</dt>
                <dd>{resource.architecture || '—'}</dd>
              </div>
              <div className="kv">
                <dt>Interfaces activas</dt>
                <dd>
                  {interfaces
                    ? `${interfaces.filter((i) => i.running).length} de ${interfaces.length}`
                    : '—'}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </div>
  )
}

function MiniStat({ label, value, tone }) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="inline" style={{ gap: 8 }}>
        <span className={`badge ${tone}`}>
          <span className="dot" />
        </span>
        <span style={{ fontSize: 21, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
          {value ?? '—'}
        </span>
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

  if (!interfaces) return <div className="card"><div className="empty">Cargando interfaces…</div></div>

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
  if (!pppoe) return <div className="card"><div className="empty">Cargando clientes…</div></div>

  return (
    <div className="card">
      <div className="card-head">
        <h2>Clientes PPPoE</h2>
        <div className="inline">
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
            {pppoe.clients.map((client) => (
              <tr key={client.username}>
                <td>
                  <div className="cell-title mono">{client.username}</div>
                  {client.comment && <div className="cell-sub">{client.comment}</div>}
                </td>
                <td className="dim">{client.profile || '—'}</td>
                <td className="mono dim">
                  {client.session?.address || client.remote_address || '—'}
                </td>
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
    </div>
  )
}
