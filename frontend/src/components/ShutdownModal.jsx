import { useState } from 'react'

/**
 * Apagar un MikroTik remoto es irreversible a distancia: el equipo no vuelve
 * hasta que alguien vaya al sitio a darle corriente. Por eso hay que escribir
 * el nombre exacto.
 *
 * El nombre no se valida solo aquí: el backend lo compara contra el equipo y
 * rechaza la petición si no coincide. Una confirmación que solo vive en el
 * navegador no es una salvaguarda.
 */
export default function ShutdownModal({ deviceName, onCancel, onConfirm }) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const matches = typed.trim() === deviceName

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      await onConfirm(typed.trim())
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Apagar el equipo</h2>
        <p className="page-sub">Esta acción no se puede deshacer desde la plataforma.</p>

        <div className="modal-body">
          <div className="alert error">
            Un equipo apagado no vuelve solo. Para encenderlo de nuevo hay que ir
            físicamente al sitio y cortarle y restituirle la corriente. Si lo que
            necesitas es que el equipo se reinicie y vuelva, usa Reiniciar.
          </div>
          {error && <div className="alert error">{error}</div>}

          <div className="field">
            <label htmlFor="confirmar">
              Escribe el nombre del equipo para confirmar: <strong>{deviceName}</strong>
            </label>
            <input
              id="confirmar"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={deviceName}
              autoComplete="off"
            />
          </div>
        </div>

        <div className="btn-row">
          <button className="btn ghost" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button className="btn danger" onClick={confirm} disabled={!matches || busy}>
            {busy ? 'Apagando…' : 'Apagar de todos modos'}
          </button>
        </div>
      </div>
    </div>
  )
}
