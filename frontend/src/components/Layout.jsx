import { NavLink } from 'react-router-dom'
import { useAuth } from '../auth'

// El módulo MikroTik es el único construido; el resto queda visible pero
// marcado, para que nadie confunda la maqueta con lo que ya funciona.
const MODULOS = [
  { label: 'Dashboard', to: null },
  { label: 'Clientes', to: null },
  { label: 'Cobranza', to: null },
  { label: 'Pagos y Transferencias', to: null },
  { label: 'Servicios de Internet', to: null },
  { label: 'Red y Monitoreo', to: null },
  { label: 'MikroTik', to: '/mikrotik' },
  { label: 'Ubiquiti', to: null },
  { label: 'Reportes', to: null },
  { label: 'Inventario', to: null },
  { label: 'Soporte / Tickets', to: null },
  { label: 'Configuración', to: null },
]

const ROLES = {
  admin: 'Administrador',
  cobranza: 'Cobranza',
  soporte: 'Soporte',
  tecnico: 'Técnico',
}

export default function Layout({ children }) {
  const { user, logout } = useAuth()
  const initials = (user?.full_name || '?')
    .split(' ')
    .slice(0, 2)
    .map((part) => part[0])
    .join('')
    .toUpperCase()

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">D'O</div>
          <div>
            <div className="brand-name">D' OLGA</div>
            <div className="brand-sub">SuperApp</div>
          </div>
        </div>

        <nav className="nav">
          {MODULOS.map((item) =>
            item.to ? (
              <NavLink
                key={item.label}
                to={item.to}
                className={({ isActive }) => (isActive ? 'active' : undefined)}
              >
                {item.label}
              </NavLink>
            ) : (
              <div key={item.label} className="nav-item disabled">
                <span>{item.label}</span>
                <span className="pill-soon">pronto</span>
              </div>
            ),
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="avatar">{initials}</div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div className="sidebar-user">{user?.full_name}</div>
            <div className="faint" style={{ fontSize: 11 }}>
              {ROLES[user?.role] || user?.role}
            </div>
          </div>
          <button className="btn ghost small" onClick={logout} title="Cerrar sesión">
            Salir
          </button>
        </div>
      </aside>

      <main className="main">{children}</main>
    </div>
  )
}
