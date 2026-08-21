import { NavLink } from 'react-router-dom'
import { useAuth } from '../auth'

const MODULES_LISTOS = [
  { to: '/mikrotik', label: 'MikroTik' },
]

const MODULES_PENDIENTES = [
  'Dashboard',
  'Clientes',
  'Planes',
  'Facturación',
  'Cobranza',
  'Pagos',
  'Tickets',
  'Técnicos',
  'Instalaciones',
  'Inventario',
  'Ubiquiti / UISP',
  'OLT / FTTH',
  'Monitoreo',
  'WhatsApp',
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
            <div className="brand-sub">SuperApp ISP</div>
          </div>
        </div>

        <nav className="nav">
          <div className="nav-group-label">Red</div>
          {MODULES_LISTOS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? 'active' : undefined)}
            >
              {item.label}
            </NavLink>
          ))}

          <div className="nav-group-label" style={{ marginTop: 16 }}>
            En construcción
          </div>
          {MODULES_PENDIENTES.map((label) => (
            <div key={label} className="nav-item disabled">
              <span>{label}</span>
              <span className="pill-soon">pronto</span>
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="avatar">{initials}</div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 550, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {user?.full_name}
            </div>
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
