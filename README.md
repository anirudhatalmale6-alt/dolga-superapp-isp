# D' OLGA SUPERAPP — plataforma ISP/WISP

Plataforma web para administrar un ISP/WISP conectando la administración del
negocio con la infraestructura real de la red.

**Arquitectura:** `React → FastAPI/Python → MikroTik`
Las credenciales de los equipos nunca llegan al navegador: se guardan cifradas
en PostgreSQL y solo se descifran dentro del proceso de FastAPI, en memoria,
justo antes de abrir la conexión con el router.

---

## Estado actual

### Listo y probado

| Pieza | Detalle |
|---|---|
| Backend FastAPI | Autenticación JWT, roles (admin / cobranza / soporte / técnico), CORS |
| PostgreSQL | Modelos de usuarios y equipos de red (SQLAlchemy 2.0 async + asyncpg) |
| Cifrado de credenciales | Fernet (AES-128-CBC + HMAC-SHA256) con clave dedicada `DEVICE_SECRET_KEY` |
| Conector MikroTik REST | RouterOS v7 (`/rest/...`), Basic Auth, TLS opcional |
| Conector MikroTik API binaria | RouterOS 6 y 7 (puerto 8728 / 8729 TLS), login moderno y login legacy MD5 |
| Modo AUTO | Intenta REST y cae a la API binaria si el servicio no está disponible |
| Lectura | CPU, memoria, disco, uptime, versión, RouterBOARD, interfaces, contadores, tráfico instantáneo, direcciones IP |
| PPPoE | Secrets, sesiones activas, perfiles, y vista cruzada online / offline / suspendido |
| Acciones | Suspender, reactivar y cerrar sesión de un cliente (3 métodos de corte) |
| Salud del equipo | Temperatura y voltaje de `/system/health`, informando "no disponible" en placas sin sensor |
| WAN | Detección de las interfaces con ruta por defecto, con IP, gateway y velocidad |
| Mantenimiento | Revisar actualizaciones de RouterOS, reiniciar, y apagar con confirmación validada en el servidor |
| Monitoreo | Servicio de fondo que muestrea todos los equipos y guarda el histórico para las gráficas |
| Vista de flota | Totales de la red y tabla de equipos servidos desde el histórico, sin golpear los routers |
| Ubiquiti / UISP | Sitios, sectores, estaciones, señal, frecuencia, tráfico e histórico que UISP ya guarda |
| Topología inalámbrica | Clientes agrupados por sector, con la peor señal primero y el listado de enlaces flojos |
| Frontend React | Login, vista de flota, detalle de equipo con gráficas en SVG, y módulo Ubiquiti |
| Pruebas | 127 pruebas automáticas contra un RouterOS y un UISP simulados |

### Todavía NO construido

Dashboard general, Clientes, Planes, Facturación, Cobranza, Pagos, Técnicos,
Tickets, Instalaciones, Averías, Inventario, OLT/FTTH, Notificaciones,
WhatsApp, Automatización e IA.

De Ubiquiti falta todavía el histórico propio (hoy se usa el que entrega UISP)
y el enlace de cada estación con la ficha del cliente, que llega con el módulo
de Clientes.

---

## Qué NO puede entregar RouterOS, y cómo se resuelve

Vale la pena dejarlo escrito para que nadie espere magia del equipo:

| Dato | Situación real | Solución aplicada |
|---|---|---|
| Histórico de tráfico y CPU | RouterOS entrega el valor de *ahora*, no la serie de tiempo | Servicio de monitoreo propio que muestrea cada `MONITOR_INTERVAL_SECONDS` y guarda en PostgreSQL |
| Temperatura | Solo la reportan las placas con sensor. Un RB4011 sí; un RB750Gr3 o un RB2011 no | Se muestra donde existe y "n/d" donde no. Nunca se inventa un valor |
| Apagar el equipo | Se ejecuta, pero el equipo no vuelve sin corriente en sitio | Restringido al rol admin y con el nombre del equipo validado **en el servidor** |
| Actualizar RouterOS | La instalación reinicia el equipo | La plataforma solo consulta si hay versión nueva; instalar queda como acción explícita |
| Señal en equipos que no son antenas | Un switch o un router de UISP no reportan señal ni frecuencia | Esos campos quedan en `None`, nunca en 0 |
| Estación sin sector en UISP | UISP no siempre dice a qué antena está enlazada | Se listan aparte en vez de desaparecer del conteo |

Sobre Ubiquiti: se habla con UISP, no con cada antena. Además de ser lo que
Ubiquiti recomienda, evita consultar cada radio cada pocos segundos, que es
tráfico que se le quita al cliente conectado a ese sector. UISP ya guarda su
propio histórico, así que ahí no hace falta el muestreo que sí necesita MikroTik.

Se autentica con una App Key (Configuración -> Usuarios -> App keys), no con la
contraseña del operador: se puede revocar sola sin tocar la cuenta.

---

## Métodos de suspensión soportados

El corte se adapta a cómo ya opera el ISP; no al revés.

| Método | Qué hace en el router | Cuándo conviene |
|---|---|---|
| `disable_secret` | `/ppp/secret set disabled=yes` y cierra la sesión activa | El cliente simplemente no puede autenticar |
| `change_profile` | Mueve el secret a un perfil de corte (p. ej. `CORTADO`) y cierra la sesión | Se quiere mostrar un portal de aviso de pago |
| `address_list` | Agrega la IP del cliente a una address-list (p. ej. `morosos`) | Ya existen reglas de firewall que redirigen esa lista |

En los tres casos se cierra la sesión PPPoE activa para que el cambio aplique al
instante, sin esperar a que el cliente reconecte. La reactivación revierte
exactamente la acción aplicada.

---

## Puesta en marcha

### Backend

```
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # y completar DATABASE_URL, SECRET_KEY, DEVICE_SECRET_KEY
python -m scripts.create_admin admin@midominio.net "Nombre Apellido" ClaveSegura
uvicorn app.main:app --reload
```

Documentación interactiva de la API: `http://127.0.0.1:8000/docs`

Generar la clave de cifrado de credenciales:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Frontend

```
cd frontend
npm install
npm run dev        # http://localhost:5173
```

### Modo demostración (sin PostgreSQL ni router real)

Levanta cinco RouterOS simulados (uno de ellos caído a propósito), un UISP
simulado con 5 sectores y 114 estaciones, la API sobre SQLite y el servicio de
monitoreo llenando el histórico:

```
cd backend
python -m scripts.demo_server        # http://127.0.0.1:8000
```

Usuarios de prueba: `admin@dolga.net / Admin12345` y `cobranza@dolga.net / Cobranza12345`.

---

## Pruebas

```
cd backend
python -m pytest -q
```

Las pruebas levantan un RouterOS simulado que habla el protocolo binario real
sobre un socket y un servidor HTTP real para el REST v7, así que se ejercita el
conector completo, no mocks. Cada prueba de lectura y de corte corre dos veces,
una por cada transporte.

Ubiquiti se prueba igual: un UISP simulado sirviendo HTTP real, con equipos que
sí reportan señal y equipos que no, estaciones sin sector asociado y colecciones
en los dos formatos que UISP usa según versión.

---

## Configuración necesaria en el MikroTik

Crear un usuario dedicado para la plataforma, con permisos mínimos:

- Grupo con las políticas `api`, `read`, `write` y `test`.
  Para RouterOS v7 por REST agregar también `rest-api`.
- Restringir el acceso del usuario a la IP del servidor de la SuperApp
  (campo *Allowed Address*).
- Habilitar el servicio correspondiente en `/ip/service`:
  `api` (8728) o `api-ssl` (8729) para RouterOS 6, `www-ssl` para REST en v7.

## Configuración necesaria en UISP

1. Entrar a UISP -> Configuración -> Usuarios -> **App keys**.
2. Crear una App Key nueva con permiso de **lectura**.
3. Copiar la clave (se muestra una sola vez) y registrarla en la plataforma
   junto con la dirección del servidor de UISP.

No hace falta la contraseña del operador ni una cuenta de administrador. Si la
clave se compromete, se revoca desde esa misma pantalla sin afectar a nadie más.

---

## Estructura

```
backend/
  app/
    core/        configuración, JWT, cifrado de credenciales
    db/          motor async y base declarativa
    models/      usuarios y equipos de red
    schemas/     contratos de entrada/salida (Pydantic)
    api/v1/      auth, devices, mikrotik, uisp, monitoring
    services/
      mikrotik/  rest.py, binary_api.py, client.py (unificado), service.py (negocio)
      uisp/      client.py (API de UISP), service.py (sectores, estaciones, señal)
      monitoring.py  muestreo periódico, histórico y resumen de flota
  scripts/       create_admin.py, demo_server.py
  tests/         RouterOS y UISP simulados + suite de pruebas
frontend/
  src/
    pages/       Login, Mikrotik, RouterDetail, Ubiquiti
    components/  Layout, Stat, charts (SVG), SuspensionModal, ShutdownModal
    api.js       único punto de salida hacia FastAPI
```
