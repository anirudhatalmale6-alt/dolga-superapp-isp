const TOKEN_KEY = 'dolga.token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

async function request(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`/api/v1${path}`, { ...options, headers })

  if (response.status === 401) {
    setToken(null)
    throw new ApiError('Tu sesión expiró. Vuelve a iniciar sesión.', 401)
  }
  if (response.status === 204) return null

  const text = await response.text()
  const data = text ? JSON.parse(text) : null

  if (!response.ok) {
    const detail = data?.detail
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join(', ')
      : detail || `Error ${response.status}`
    throw new ApiError(message, response.status)
  }
  return data
}

export const api = {
  login: (email, password) =>
    request('/auth/login-json', { method: 'POST', body: JSON.stringify({ email, password }) }),
  me: () => request('/auth/me'),

  devices: () => request('/devices'),
  device: (id) => request(`/devices/${id}`),
  testConnection: (id) => request(`/devices/${id}/test-connection`, { method: 'POST' }),

  // Ubiquiti / UISP
  uispControllers: () => request('/uisp/controllers'),
  uispTestConnection: (id) => request(`/uisp/controllers/${id}/test-connection`, { method: 'POST' }),
  uispOverview: (id) => request(`/uisp/${id}/overview`),
  uispTopology: (id) => request(`/uisp/${id}/topology`),
  uispStations: (id, apId) => request(`/uisp/${id}/sectors/${encodeURIComponent(apId)}/stations`),
  uispDevice: (id, deviceId) => request(`/uisp/${id}/devices/${encodeURIComponent(deviceId)}`),
  uispStatistics: (id, deviceId, interval = 'hour') =>
    request(`/uisp/${id}/devices/${encodeURIComponent(deviceId)}/statistics?interval=${interval}`),
  uispAction: (id, deviceId, action) =>
    request(`/uisp/${id}/devices/${encodeURIComponent(deviceId)}/${action}`, { method: 'POST' }),

  fleet: () => request('/monitoring/fleet'),
  historyOf: (id, minutes = 60) =>
    request(`/monitoring/devices/${id}/history?minutes=${minutes}`),

  resource: (id) => request(`/mikrotik/${id}/resource`),
  deviceHealth: (id) => request(`/mikrotik/${id}/health`),
  wan: (id) => request(`/mikrotik/${id}/wan`),
  updates: (id) => request(`/mikrotik/${id}/updates`),
  reboot: (id) => request(`/mikrotik/${id}/reboot`, { method: 'POST' }),
  shutdown: (id, confirmName) =>
    request(`/mikrotik/${id}/shutdown`, {
      method: 'POST',
      body: JSON.stringify({ confirm_name: confirmName }),
    }),
  routerboard: (id) => request(`/mikrotik/${id}/routerboard`),
  interfaces: (id) => request(`/mikrotik/${id}/interfaces`),
  interfaceTraffic: (id, iface) =>
    request(`/mikrotik/${id}/interfaces/${encodeURIComponent(iface)}/traffic`),
  ipAddresses: (id) => request(`/mikrotik/${id}/ip-addresses`),
  pppoeOverview: (id, search) =>
    request(`/mikrotik/${id}/pppoe/overview${search ? `?search=${encodeURIComponent(search)}` : ''}`),
  profiles: (id) => request(`/mikrotik/${id}/pppoe/profiles`),
  dashboard: (id) => request(`/mikrotik/${id}/dashboard`),

  suspend: (id, body) =>
    request(`/mikrotik/${id}/pppoe/suspend`, { method: 'POST', body: JSON.stringify(body) }),
  restore: (id, body) =>
    request(`/mikrotik/${id}/pppoe/restore`, { method: 'POST', body: JSON.stringify(body) }),
  kick: (id, username) =>
    request(`/mikrotik/${id}/pppoe/${encodeURIComponent(username)}/kick`, { method: 'POST' }),
}
