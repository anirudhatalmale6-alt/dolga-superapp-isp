import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { useAuth } from './auth'
import Login from './pages/Login'
import Mikrotik from './pages/Mikrotik'
import Ubiquiti from './pages/Ubiquiti'
import RouterDetail from './pages/RouterDetail'
import Placeholder from './pages/Placeholder'

export default function App() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="login-wrap">
        <div className="spinner" />
      </div>
    )
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/mikrotik" replace />} />
        <Route path="/login" element={<Navigate to="/mikrotik" replace />} />
        <Route path="/mikrotik" element={<Mikrotik />} />
        <Route path="/mikrotik/:deviceId" element={<RouterDetail />} />
        <Route path="/ubiquiti" element={<Ubiquiti />} />
        <Route
          path="/clientes"
          element={
            <Placeholder
              title="Clientes"
              text="El padrón de clientes se conecta con los usuarios PPPoE del router. Se construye en el siguiente hito, junto con Planes y Facturación."
            />
          }
        />
        <Route path="*" element={<Navigate to="/mikrotik" replace />} />
      </Routes>
    </Layout>
  )
}
