import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

const REFRESH_MS = 15000

/**
 * Bitácora de operaciones.
 *
 * Cada fila se puede abrir para ver las llamadas que salieron de verdad hacia
 * el equipo: la URL de UISP o la sentencia de RouterOS, con su código de
 * respuesta y su demora. Es la pantalla que responde a dos preguntas
 * distintas: "¿quién hizo esto?" y "¿esto salió del equipo o es un dibujo?".
 */
export default function Registros() {
  const [rows, setRows] = useState(null)
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(null)
  const [filters, setFilters] = useState({ only_actions: false, only_errors: false, target_kind: '' })

  const load = useCallback(async () => {
    try {
      const [ops, sum] = await Promise.all([
        api.auditOperations({
          limit: 150,
          hours: 24,
          only_actions: filters.only_actions || undefined,
          only_errors: filters.only_errors || undefined,
          target_kind: filters.target_kind || undefined,
        }),
        api.auditSummary(24),
      ])
      setRows(ops)
      setSummary(sum)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [filters])

  useEffect(() => {
    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  function toggle(key) {
    setFilters((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Bitácora de operaciones</h1>
          <p className="page-sub">
            Todo lo que la plataforma le pidió a un equipo, con la llamada exacta que salió
          </p>
        </div>
        <div className="kpi-row">
          <Kpi tone="info" label="Operaciones (24 h)" value={summary?.operations} />
          <Kpi tone="warn" label="Acciones" value={summary?.actions} />
          <Kpi tone="danger" label="Con error" value={summary?.failed} />
          <Kpi tone="ok" label="Llamadas a equipos" value={summary?.upstream_calls} />
        </div>
      </div>

      {error && <div className="alert error" style={{ marginBottom: 14 }}>{error}</div>}

      <div className="card">
        <div className="card-head">
          <h2>Últimas 24 horas</h2>
          <div className="btn-row">
            <button
              className={`btn small ${filters.only_actions ? '' : 'ghost'}`}
              onClick={() => toggle('only_actions')}
            >
              Solo acciones
            </button>
            <button
              className={`btn small ${filters.only_errors ? '' : 'ghost'}`}
              onClick={() => toggle('only_errors')}
            >
              Solo errores
            </button>
            <select
              className="select small"
              value={filters.target_kind}
              onChange={(e) => setFilters((prev) => ({ ...prev, target_kind: e.target.value }))}
            >
              <option value="">Todos los equipos</option>
              <option value="mikrotik">MikroTik</option>
              <option value="uisp">Ubiquiti / UISP</option>
            </select>
          </div>
        </div>

        {!rows ? (
          <div className="empty">Cargando…</div>
        ) : rows.length === 0 ? (
          <div className="empty">
            Todavía no hay operaciones registradas con estos filtros. Cada consulta o acción
            sobre un equipo deja aquí su rastro.
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="tight">Hora</th>
                  <th>Operación</th>
                  <th className="tight">Equipo</th>
                  <th className="tight">Usuario</th>
                  <th className="tight">Resultado</th>
                  <th className="num tight">Llamadas</th>
                  <th className="num tight">Demora</th>
                  <th className="tight" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <FilaOperacion
                    key={row.id}
                    row={row}
                    abierta={open === row.id}
                    onToggle={() => setOpen(open === row.id ? null : row.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <p className="faint" style={{ fontSize: 12, marginTop: 12 }}>
        Las consultas se conservan 14 días; las acciones sobre equipos, un año. En la bitácora
        no se guarda ninguna credencial: solo la dirección o el comando, sin las cabeceras de
        autenticación.
      </p>
    </>
  )
}

function FilaOperacion({ row, abierta, onToggle }) {
  const hora = new Date(row.occurred_at)
  return (
    <>
      <tr className="clickable" onClick={onToggle}>
        <td className="tight mono nowrap">{hora.toLocaleTimeString('es', { hour12: false })}</td>
        <td>
          <div className="cell-title">{row.operation}</div>
          <div className="cell-sub mono">{row.endpoint}</div>
        </td>
        <td className="tight">
          <div className="cell-title">{row.target_name || '—'}</div>
          <div className="cell-sub">{row.target_kind === 'uisp' ? 'Ubiquiti' : 'MikroTik'}</div>
        </td>
        <td className="tight dim nowrap">{row.user_email || '—'}</td>
        <td className="tight">
          {row.ok ? (
            <span className={`badge ${row.is_action ? 'warn' : 'ok'}`}>
              <span className="dot" />
              {row.is_action ? 'acción' : 'consulta'}
            </span>
          ) : (
            <span className="badge danger">
              <span className="dot" />
              error
            </span>
          )}
        </td>
        <td className="num tight">{row.upstream_calls}</td>
        <td className="num tight nowrap">{row.duration_ms} ms</td>
        <td className="chevron">{abierta ? '▾' : '▸'}</td>
      </tr>
      {abierta && (
        <tr>
          <td colSpan={8} className="detail-cell">
            {row.detail && <div className="alert error" style={{ marginBottom: 10 }}>{row.detail}</div>}
            <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>
              Llamadas que salieron del servidor hacia el equipo:
            </div>
            {row.upstream.length === 0 ? (
              <div className="empty">Esta operación no llegó a contactar al equipo.</div>
            ) : (
              <ul className="upstream">
                {row.upstream.map((call, index) => (
                  <li key={index} className={call.ok ? '' : 'failed'}>
                    <span className="tag">{call.target}</span>
                    <span className="mono">{call.request}</span>
                    <span className="faint">
                      {' → '}
                      {call.http_status ? `HTTP ${call.http_status}` : call.ok ? 'ok' : 'error'}
                      {` · ${call.duration_ms} ms`}
                      {call.rows != null ? ` · ${call.rows} fila(s)` : ''}
                    </span>
                    {call.error && <span className="err"> — {call.error}</span>}
                  </li>
                ))}
              </ul>
            )}
          </td>
        </tr>
      )}
    </>
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
