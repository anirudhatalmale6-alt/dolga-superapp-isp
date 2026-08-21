import { useState } from 'react'
import { useAuth } from '../auth'

export default function Login() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(event) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(email, password)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="brand" style={{ padding: 0, marginBottom: 20 }}>
          <div className="brand-mark">D'O</div>
          <div>
            <div className="brand-name">D' OLGA</div>
            <div className="brand-sub">SuperApp ISP</div>
          </div>
        </div>

        <h1>Iniciar sesión</h1>
        <p className="page-sub">Administración de red y clientes</p>

        <form className="login-form" onSubmit={onSubmit}>
          {error && <div className="alert error">{error}</div>}
          <div className="field">
            <label htmlFor="email">Correo</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="admin@dolga.net"
              required
            />
          </div>
          <div className="field">
            <label htmlFor="password">Contraseña</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
            />
          </div>
          <button className="btn primary" type="submit" disabled={busy} style={{ justifyContent: 'center' }}>
            {busy ? 'Verificando…' : 'Entrar'}
          </button>
        </form>

        <p className="hint">
          Las credenciales de los routers no se guardan en el navegador. El acceso a MikroTik ocurre
          solo desde el servidor.
        </p>
      </div>
    </div>
  )
}
