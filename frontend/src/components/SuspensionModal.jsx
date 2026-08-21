import { useEffect, useState } from 'react'

const METODOS = [
  { value: 'disable_secret', label: 'Deshabilitar el secret PPPoE' },
  { value: 'change_profile', label: 'Mover a un perfil de corte' },
  { value: 'address_list', label: 'Agregar la IP a una address-list' },
]

/**
 * Confirma el corte o la reactivación de un cliente.
 * El texto explica exactamente qué se va a ejecutar en el router.
 */
export default function SuspensionModal({ mode, client, profiles, onCancel, onConfirm }) {
  const suspending = mode === 'suspend'
  const [method, setMethod] = useState('disable_secret')
  const [suspendedProfile, setSuspendedProfile] = useState('CORTADO')
  const [activeProfile, setActiveProfile] = useState(client?.profile || '')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setActiveProfile(client?.profile || '')
  }, [client])

  const profileNames = (profiles || []).map((p) => p.name)

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      await onConfirm({
        username: client.username,
        method,
        suspended_profile: suspendedProfile,
        active_profile: activeProfile || null,
        comment: comment || null,
      })
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{suspending ? 'Suspender servicio' : 'Reactivar servicio'}</h2>
        <p className="page-sub">
          {suspending
            ? 'Se aplicará el corte en el router y se cerrará la sesión activa del cliente.'
            : 'Se restablece el acceso y se fuerza la reconexión del cliente.'}
        </p>

        <div className="modal-body">
          {error && <div className="alert error">{error}</div>}

          <dl style={{ margin: 0 }}>
            <div className="kv">
              <dt>Usuario PPPoE</dt>
              <dd className="mono">{client.username}</dd>
            </div>
            <div className="kv">
              <dt>Perfil actual</dt>
              <dd className="mono">{client.profile || '—'}</dd>
            </div>
            <div className="kv">
              <dt>Sesión</dt>
              <dd>{client.online ? `activa · ${client.session?.address || ''}` : 'sin sesión'}</dd>
            </div>
          </dl>

          <div className="field">
            <label htmlFor="metodo">Método</label>
            <select id="metodo" value={method} onChange={(e) => setMethod(e.target.value)}>
              {METODOS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          {method === 'change_profile' && suspending && (
            <div className="field">
              <label htmlFor="perfil-corte">Perfil de corte</label>
              <select
                id="perfil-corte"
                value={suspendedProfile}
                onChange={(e) => setSuspendedProfile(e.target.value)}
              >
                {(profileNames.length ? profileNames : ['CORTADO']).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {method === 'change_profile' && !suspending && (
            <div className="field">
              <label htmlFor="perfil-activo">Perfil del plan contratado</label>
              <select
                id="perfil-activo"
                value={activeProfile}
                onChange={(e) => setActiveProfile(e.target.value)}
              >
                <option value="">Selecciona un perfil…</option>
                {profileNames.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="field">
            <label htmlFor="nota">Nota (queda como comentario en el router)</label>
            <input
              id="nota"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={suspending ? 'Factura 0012 vencida' : 'Pago recibido 21/08'}
            />
          </div>
        </div>

        <div className="btn-row">
          <button className="btn ghost" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button
            className={suspending ? 'btn danger' : 'btn primary'}
            onClick={confirm}
            disabled={busy || (method === 'change_profile' && !suspending && !activeProfile)}
          >
            {busy ? 'Aplicando…' : suspending ? 'Suspender ahora' : 'Reactivar ahora'}
          </button>
        </div>
      </div>
    </div>
  )
}
