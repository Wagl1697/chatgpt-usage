# Uso de ChatGPT y OpenCode Go en GNOME

Monitor local que consulta los límites de ChatGPT y OpenCode Go por HTTPS y los
muestra en la barra superior de GNOME. No abre navegadores durante las consultas:
lee las cookies de los navegadores seleccionados en modo de solo lectura y usa
esas sesiones para consultar los límites directamente.

## Funciones

- Una fuente por vez en la barra, con carrusel configurable.
- Menú con anillos de 5 horas y semanal para cada cuenta de ChatGPT, y 5 horas,
  semanal y mensual para OpenCode Go.
- Restablecimientos disponibles por cuenta, con vencimiento y botón de dos
  pasos (`Usar` → `Confirmar`). La confirmación vuelve a
  comprobar cuenta, disponibilidad y compatibilidad antes de canjearlo.
- Reinicios en formato `⏰ dd/mm/aa hh:mm`.
- Resumen al pasar el cursor, alineado a la izquierda.
- Pausa y reanudación de todas las consultas desde el menú.
- Cuentas controladas explícitamente desde Preferencias: una fila por cuenta,
  con interruptor, correo y navegadores/perfiles de origen.
- Nombres visibles, orden del carrusel e intervalo configurables.
- Panel web opcional, de solo lectura, para abrir desde otro dispositivo.
- Soporte de Firefox (Snap, Flatpak y paquete del sistema) y de Chrome,
  Chromium, Edge y Brave mediante el llavero del sistema.

## Arquitectura

```text
navegadores seleccionados
        │ cookies en solo lectura
        ▼
monitor.py ──────────────► uso.json / uso.txt
        │                         │
        │                         ├── extensión GNOME
        │                         └── web.py (opcional)
        │
        └── OpenCode Go por HTTPS

Preferencias GNOME ──► GSettings ──► monitor.py / scan_accounts.py
```

El monitor no conoce la interfaz: publica un informe local y las interfaces lo
leen. El informe no contiene cookies, tokens ni contraseñas.

## Requisitos

- Linux con GNOME Shell 46.
- Python 3.10 o posterior.
- Firefox con una sesión de ChatGPT iniciada, o Chrome/Chromium/Edge/Brave con
  la clave correspondiente en el llavero.
- `curl_cffi` y `cryptography` (se instalan en el entorno virtual).

## Instalación local

```sh
cd chatgpt-usage
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python3 scripts/install_local.py
```

El instalador copia la extensión, genera su `metadata.json` con las rutas
absolutas de esta máquina y compila el esquema. Después:

```sh
systemctl --user restart chatgpt-usage-monitor.service
```
La extensión se carga al cerrar sesión y volver a entrar en GNOME.

## Uso

1. Abrí **Preferencias** de la extensión.
2. En **Cuentas controladas**, pulsá **Actualizar detección**.
3. Activá las cuentas que querés consultar.
4. Ajustá nombres, orden e intervalo si hace falta.

La selección se guarda en GSettings. El monitor solo consulta las cuentas
activas. Si no puede leer la selección, no consulta ninguna cuenta y muestra el
error en el menú.

## Panel web opcional

`web.py` sirve un panel de solo lectura y endpoints compactos para widgets.
Escucha en `127.0.0.1`; para verlo desde otro dispositivo se puede publicar en
la tailnet con Tailscale Serve:

```sh
PYTHONPATH=src .venv/bin/python -m chatgpt_usage.web
tailscale serve --bg --https=8445 http://127.0.0.1:8088
```

Rutas:

- `/` panel completo.
- `/widget` vista compacta.
- `/api/summary.txt` texto para widgets.
- `/api/summary.json` resumen estructurado.
- `/api/uso.json` informe de solo lectura (sin identificadores de canje).

No tiene autenticación propia: la seguridad depende de Tailscale y de sus ACL.
No uses Tailscale Funnel para exponerlo a Internet.

## Privacidad y seguridad

- Las cookies se leen en modo de solo lectura y se usan en memoria.
- No se guardan contraseñas, tokens ni cookies en `uso.json`.
- El informe local contiene correos para distinguir cuentas; tratalo como dato
  privado.
- `uso.json` se escribe de forma atómica y con permisos restringidos.
- Las consultas usan endpoints internos de ChatGPT y OpenCode; pueden cambiar o
  rechazar el flujo.
- Consultar restablecimientos es de solo lectura. El único cambio remoto ocurre
  cuando se pulsa **Confirmar**; ese canje puede ser irreversible. El informe
  local conserva el identificador y el vencimiento de los restablecimientos
  disponibles, pero nunca credenciales.

## Pruebas

```sh
PYTHONPATH=src .venv/bin/python -m unittest -v test_monitor.py test_accounts.py test_redeem_reset.py test_web.py
node --test test_ui.mjs
```

Las pruebas usan cuentas ficticias y no necesitan red. Las pruebas JavaScript
recorren el renderizado de varias cuentas y de OpenCode Go, incluyendo botones
con la API de St (sin métodos de Gtk).

En una máquina con GNOME se puede comprobar además la extensión completa con
actores reales y medir el tamaño del botón:

```sh
python3 scripts/test_gnome.py
```

Esta prueba arranca una Shell Wayland headless con HOME, configuración y bus
D-Bus aislados; no toca la sesión gráfica, cuentas ni preferencias reales. El
ayudante de canje está deshabilitado y solo se comprueba el primer clic.

## Limitaciones

- GNOME Shell 46 es la versión validada actualmente.
- Chrome/Edge/Brave dependen del llavero del sistema y de que la clave esté
  disponible.
- El panel web y el widget de Android son funciones opcionales; el widget nativo
  no está implementado.
- La instalación pública y el empaquetado están en preparación.
