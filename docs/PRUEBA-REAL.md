# Prueba con equipos reales

Esta es la guía para comprobar, sobre hardware físico, que la plataforma habla
de verdad con los equipos y no muestra datos preparados.

El criterio es simple: **todo lo que se ve en pantalla tiene que poder
rastrearse hasta una llamada concreta a un equipo concreto**, con su URL o su
sentencia, su código de respuesta y su demora. Eso es lo que hace la bitácora
de operaciones, y es lo que se revisa al final de cada prueba.

---

## Tres formas de hacerla

### Opción A — La plataforma corre en tu red (no necesito ningún acceso)

Es la más limpia: nada de tu red se expone, y los datos que ves los pide tu
propia máquina. Sirve para las siete comprobaciones de esta guía.

1. En una máquina de tu red (una PC o un servidor pequeño) se clona el
   repositorio y se levanta el backend y el frontend.
2. Se registra tu UISP con su App Key y tu router con un usuario de consulta.
3. Se navega la plataforma con tus equipos reales.

Los pasos están en la sección "Puesta en marcha" del `README.md`. Si querés
verlo funcionando antes de conectar nada tuyo, `python -m scripts.demo_server`
levanta la plataforma completa con equipos simulados, sin PostgreSQL ni router.

Para una comprobación más rápida todavía, sin levantar nada, está el script
`backend/scripts/verify_real.py`: se conecta a tu UISP o a tu MikroTik, imprime
cada llamada que hace y el dato que recibe, y no envía nada a ningún lado. Es
el mismo conector que usa el backend, así que si el script trae tus datos, la
plataforma también.

### Opción B — Yo me conecto desde mi servidor

Sirve si tu UISP ya es accesible desde internet (lo normal cuando está en la
nube de Ubiquiti o publicado en un dominio).

Necesito dos cosas y nada más:

- la dirección de tu UISP,
- una **App Key con permiso de lectura** (UISP → Configuración → Usuarios →
  App keys). Se revoca desde ahí en un clic cuando terminemos.

Para el MikroTik: un **usuario de solo lectura** (grupo `read`) y el puerto de
la API accesible únicamente desde mi IP, que te paso cuando coordinemos. Se
borra el usuario al terminar.

Lo que **no** necesito y te pido que no me mandes: tu usuario y contraseña de
UISP, la clave de administrador del router, ni las claves de las antenas.

### Opción C — Un túnel temporal que abres tú

Si no querés publicar nada, se puede levantar un túnel de salida desde tu red
(Cloudflare Tunnel o similar). Lo iniciás vos, dura lo que dure la prueba y lo
cortás cuando quieras: no queda ningún puerto abierto y el control es tuyo.

Dos aclaraciones honestas sobre esto:

- **No uso escritorio remoto** (TeamViewer, AnyDesk y parecidos). No trabajo
  así.
- Para la prueba, un túnel de salida que abrís vos es más simple y más seguro
  que armar una VPN. Para producción sí recomiendo WireGuard sitio a sitio
  entre el servidor y tu router de borde, que es lo que te comenté antes.

---

## Las siete comprobaciones

### 1. El sistema obtiene la información del equipo por la API de UISP

**Cómo se comprueba:** se abre el módulo Ubiquiti y después la bitácora. Cada
carga de pantalla deja una fila con las llamadas que salieron:

    GET https://tu-uisp/nms/api/v2.1/devices -> HTTP 200 en 312 ms, 47 fila(s)
    GET https://tu-uisp/nms/api/v2.1/sites   -> HTTP 200 en 94 ms, 3 fila(s)

**Qué probaría lo contrario:** si el dato estuviera preparado, no habría
llamada que mostrar, o apuntaría a algo que no es tu servidor.

### 2. Aparecen los dispositivos, sectores y clientes reales

**Cómo se comprueba:** los nombres. Tus sectores y tus clientes se llaman como
los llamaste vos en UISP. Los totales de arriba (sitios, sectores, clientes en
línea) tienen que dar lo mismo que la pantalla principal de UISP.

### 3. Señal, estado y tráfico coinciden con lo que muestra UISP

**Cómo se comprueba:** se abre un sector, se toman tres clientes de la lista y
se comparan sus dBm con los de UISP.

**Una diferencia esperable:** UISP y la plataforma consultan en momentos
distintos, así que la señal puede variar uno o dos dB entre una pantalla y la
otra. Lo que no puede pasar es que difieran diez.

**Un detalle a mirar:** los equipos sin radio (un switch, por ejemplo) tienen
que decir `n/d`, no `0 dBm`. Un cero ahí se leería como enlace perfecto y sería
justo el tipo de dato inventado que estamos buscando.

### 4. Si un equipo se desconecta, el sistema lo detecta

**Cómo se comprueba:** se desconecta una antena de prueba (o se la apaga desde
UISP). El sector pasa a "Caído" y sus clientes a `0 de N`. Las señales del
sector pasan a `n/d`.

**Un límite que conviene tener claro de entrada:** la plataforma no puede
enterarse antes que UISP. UISP tarda uno o dos minutos en marcar un equipo como
caído; hasta entonces la plataforma va a mostrar lo mismo que muestra UISP,
porque es de ahí de donde saca el dato. Si hiciera falta detección más rápida,
eso se hace con ping o SNMP directo al equipo, y es otro trabajo.

Para los MikroTik sí hay muestreo propio cada 30 segundos, y ahí la detección
es más rápida.

### 5. Ejecutar una acción real y comprobar que se ejecutó

**La acción recomendada para la prueba es "Localizar".** Hace parpadear los LED
del equipo: se ve físicamente desde abajo de la torre, no interrumpe el
servicio de nadie y se corta sola. Es la única acción que se puede probar en
horario de trabajo sin molestar a un cliente.

Después, en la bitácora:

    POST https://tu-uisp/nms/api/v2.1/devices/<id>/locate -> HTTP 200 en 210 ms

Si querés probar "Reiniciar", se hace sobre un equipo de prueba y fuera de
horario: corta el enlace mientras arranca.

**Si tu versión de UISP no ofrece alguna de estas acciones**, la plataforma lo
dice tal cual ("esta versión de UISP no ofrece esa acción") en vez de mostrar
un mensaje de éxito. Eso también se puede comprobar en la bitácora: la llamada
queda registrada con su HTTP 404.

### 6. Lo mismo con un MikroTik real

**Cómo se comprueba:** con un usuario de solo lectura alcanza para los pasos 1
a 4. Se comparan CPU, memoria, uptime e interfaces contra System → Resources en
Winbox, y la lista de clientes PPPoE contra PPP → Active Connections.

Para probar una suspensión hace falta un usuario con permiso de escritura y un
cliente de prueba (no uno real). La suspensión deshabilita el secret y además
tumba la sesión activa; se ve en Winbox al instante.

En la bitácora quedan las sentencias exactas de RouterOS:

    /ppp/secret/print ?name=cliente-prueba -> ok en 41 ms, 1 fila(s)
    /ppp/secret/set =.id=*3 =disabled=true -> ok en 38 ms
    /ppp/active/remove =.id=*A             -> ok en 35 ms

### 7. Ver los registros para comprobar que no es información simulada

La bitácora está en el menú lateral ("Bitácora"). Cada fila se abre y muestra
las llamadas reales. Se puede filtrar por acciones, por errores y por tipo de
equipo.

Tres cosas que conviene mirar ahí:

- **Los errores también se guardan.** Si el equipo no contesta, queda la fila
  con el motivo. Un sistema que muestra datos preparados no tiene errores que
  registrar.
- **Las demoras son reales.** Una consulta a UISP tarda cientos de
  milisegundos; una respuesta instantánea y siempre igual sería sospechosa.
- **No hay credenciales.** La bitácora guarda la URL o el comando, nunca la App
  Key, la clave del router ni las cabeceras de autenticación. Está probado en
  `tests/test_audit.py`.

---

## Lo que esta prueba no cubre

Para que quede dicho antes y no después:

- **La OLT.** No hay nada construido todavía. Cada marca se maneja distinto y
  no quiero adivinar la tuya: en cuanto me digas marca, modelo exacto y versión
  de firmware, empiezo.
- **La configuración de radio de las antenas** (frecuencia, potencia, ancho de
  canal, SSID). Eso vive en airOS, dentro de cada antena, y la API de UISP no
  lo expone. Se puede hacer, pero es otro trabajo y no está incluido.
- **Los demás módulos** (Clientes, Facturación, Cobranza, Tickets, OLT,
  WhatsApp, IA). Todavía no están construidos.
