# Uso de ChatGPT y OpenCode Go en Ubuntu

Proyecto comunitario no oficial. No está afiliado a OpenAI ni a OpenCode.

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

- **Ubuntu Desktop 24.04 LTS con GNOME Shell 46**, versión comprobada.
- Otras versiones de Ubuntu/GNOME todavía no están validadas; el instalador
  rechaza versiones de Shell no admitidas, en lugar de instalar algo incompatible.
- No funciona en Windows, macOS ni escritorios sin GNOME Shell.
- Python 3.10 o posterior.
- Firefox con una sesión de ChatGPT iniciada, o Chrome/Chromium/Edge/Brave con
  la clave correspondiente en el llavero.
- `curl_cffi` y `cryptography` (el instalador los descarga en un entorno virtual).
- OpenCode Go es opcional: requiere una suscripción Go y una conexión local
  activa a OpenCode Console. No se importa la conexión del autor del proyecto.

## Instalación

Primero instalá los requisitos de Ubuntu:

```sh
sudo apt update
sudo apt install git python3-venv python3-gi gir1.2-secret-1 libglib2.0-bin
```

Después, como tu usuario normal (**sin sudo**):

```sh
git clone https://github.com/Wagl1697/chatgpt-usage.git
cd chatgpt-usage
./install.sh
```

El instalador crea `.venv`, instala la extensión completa, genera las rutas
para tu máquina, compila el esquema y activa el servicio systemd de tu usuario.
**Cerrá sesión y volvé a entrar** para que GNOME cargue la extensión.

No muevas ni borres la carpeta clonada mientras lo uses: allí están el código
y su entorno virtual. Si la movés, ejecutá nuevamente `./install.sh` desde ella.

El estado queda en `~/.local/state/chatgpt-usage/` y el registro de instalación
en `~/.config/chatgpt-usage/` (se respetan las variables XDG correspondientes).
Las cuentas, nombres y selección se guardan en GSettings, no en Git.

Para instalar también el panel web opcional:

```sh
./install.sh --with-web
```

### Actualizar o desinstalar

```sh
git pull --ff-only
./install.sh
```

La actualización conserva cuentas, preferencias y estado, incluido el panel web
si ya estaba instalado. Volvé a iniciar sesión para cargar cambios de GNOME.

```sh
./install.sh --uninstall
```

La desinstalación detiene servicios y retira la extensión, pero **conserva**
preferencias, datos locales y la carpeta clonada. No borra datos del navegador.

## Uso

1. Abrí **Preferencias** de la extensión.
2. En **Cuentas controladas**, pulsá **Actualizar detección**.
3. Activá las cuentas que querés consultar.
4. Ajustá nombres, orden e intervalo si hace falta.

La selección se guarda en GSettings. El monitor solo consulta las cuentas
activas. Si no puede leer la selección, no consulta ninguna cuenta y muestra el
error en el menú.

Detectar cuentas hace consultas HTTPS de identidad a ChatGPT usando sus sesiones
locales. Las cuentas recién detectadas quedan desactivadas hasta que las elijas;
no se consultan cuotas automáticamente antes de configurar una selección.

Las sesiones vencidas requieren volver a iniciar sesión en el navegador. Chrome,
Edge, Chromium y Brave dependen de que el llavero de tu sesión esté desbloqueado;
no todas las variantes de cifrado o instalaciones de navegador están garantizadas.

## Panel web opcional

`web.py` sirve un panel de solo lectura y endpoints compactos para widgets.
Escucha en `127.0.0.1`; para verlo desde otro dispositivo se puede publicar en
la tailnet con Tailscale Serve:

```sh
./install.sh --with-web
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
PYTHONPATH=src .venv/bin/python -m unittest discover -v
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
- ChatGPT y OpenCode usan endpoints internos: pueden cambiar sin aviso. La
  disponibilidad de límites y restablecimientos depende del plan de cada cuenta.

## Diagnóstico

```sh
systemctl --user status chatgpt-usage-monitor.service
journalctl --user -u chatgpt-usage-monitor.service -n 30 --no-pager
gnome-extensions info chatgpt-usage@wlopez.local
```

El UUID histórico de la extensión es el mismo para todos los usuarios; no
requiere una cuenta del autor. Al reportar un problema, ocultá correos y rutas
personales. Nunca compartas cookies, tokens, archivos de navegador ni `uso.json`.

Licencia: [MIT](LICENSE).
