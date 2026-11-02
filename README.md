# Monitor local del uso del plan, sin navegador

Consulta `https://chatgpt.com/backend-api/wham/usage` directamente por HTTPS
cada 10 segundos y sobrescribe `uso.txt` de forma atómica. No abre Chrome,
Firefox, Playwright ni ningún otro navegador, ni siquiera en modo invisible.

También lee OpenCode Go desde el endpoint oficial de uso cada 10 segundos,
usando la conexión OAuth de OpenCode guardada localmente. Muestra cuota rolling
(5 horas), semanal y mensual; la respuesta contiene porcentaje de uso y se
calcula el restante como `100 - usado`. Este origen puede tener un estado/fecha
de actualización diferente al de ChatGPT.

ChatGPT consulta por separado cada perfil de los navegadores seleccionados en
Preferencias. Los informes identifican las cuentas por correo para que dos
sesiones no se mezclen; si una sesión falla, su propia fila muestra el error y
la otra cuenta continúa independiente. Si todavía no hay una selección guardada,
se conserva el comportamiento anterior y se leen los perfiles de Firefox.

La extensión nativa rota entre Go y las cuentas ChatGPT en la barra, con un
intervalo configurable (10 segundos por defecto). El menú conserva el desglose completo.
Si la extensión nativa no carga, el sector izquierdo de Executor muestra el
resumen compacto como alternativa.

## Interfaz local actualizada (versión 6)

- La barra muestra una sola fuente por vez: Go y cada cuenta ChatGPT.
- `Preferencias…` incluye **Cuentas controladas**: una fila por cuenta, con
  interruptor, correo y navegadores/perfiles de origen. **Actualizar detección**
  busca sesiones en los navegadores instalados.
- El monitor consulta solo las cuentas activas. Las cookies se leen en memoria y
  no se guardan; la selección se guarda en GSettings.
- Soporta Firefox (Snap, Flatpak y paquete del sistema) y Chrome, Chromium, Edge
  y Brave mediante la clave del llavero del sistema.
- `Preferencias…` permite cambiar los nombres visibles, el orden y los segundos
  entre fuentes. Los nombres predeterminados son el correo sin su dominio.
- El menú muestra las cuotas en columnas de anillos, con el reinicio debajo.
- El hover se alinea a la izquierda, con margen interior y esquinas de 5 px.
- `Actualizar límites automáticamente` pausa o reanuda todas las consultas.
  Se aplica en el siguiente ciclo del servicio. Mientras está apagado se conservan
  los últimos datos con la indicación de pausa. El ajuste se guarda en `polling.json`.
- Se quitó la acción de abrir el informe de texto del menú.
- Las filas de cuentas que dejan de estar activas se eliminan del menú en vez de
  quedar como perfiles obsoletos.

Los cambios de código de GNOME se cargan al volver a iniciar sesión; los cambios
de preferencias se aplican en vivo una vez cargada esta versión.

Los porcentajes provienen del servidor, no de un conteo de mensajes.
El porcentaje restante es `100 - used_percent`. Las fechas son locales.
Es un endpoint interno: puede cambiar sin aviso.

## Iniciar

```sh
./iniciar.sh
```

Para detenerlo, presioná Ctrl+C. Si está en segundo plano:

```sh
kill -TERM "$(cat monitor.pid)"
```

No hace falta dejar un navegador abierto. La computadora sí debe permanecer
encendida. En este equipo el monitor ya está configurado como servicio permanente
de usuario: arranca automáticamente al iniciar sesión y se reinicia si falla.

```sh
systemctl --user status chatgpt-usage-monitor.service
systemctl --user restart chatgpt-usage-monitor.service
```

## Autenticación y privacidad

- Lee exclusivamente cookies de `chatgpt.com` ya guardadas en el perfil local
  de Firefox, en modo de solo lectura. No modifica ni copia el perfil.
- `curl_cffi` hace peticiones HTTPS con TLS/HTTP compatible con Chrome;
  es una biblioteca, no un navegador. Mantiene la validación de certificados.
- Cookies y tokens se usan en memoria y solo se envían a `chatgpt.com`.
  No se guardan credenciales nuevas en disco, logs o archivos de salida.
- Consulta cada perfil de Firefox como una cuenta independiente; no usa los
  límites de una sesión para etiquetar otra. Guarda el correo de cada cuenta
  en los informes locales privados para poder distinguir los medidores.
- No guarda tokens, identificadores internos de cuenta ni conversaciones.
- Obtiene el token desde `/api/auth/session` y lo renueva periódicamente
  o al recibir HTTP 401. No es una credencial permanente: si la sesión de
  ChatGPT se revoca o vence, será necesario iniciar sesión otra vez.
- El archivo indica la hora de la última lectura válida y los errores.
  Ante errores conserva los últimos datos con una advertencia explícita.
- Aumenta el intervalo cuando hay errores. Ante HTTP 429 respeta `Retry-After`
  y espera al menos 60 segundos.
- Para OpenCode Go, lee en modo de solo lectura la credencial de la conexión
  `opencode` de `~/.local/share/opencode/opencode.db`, limita el destino HTTPS a
  `opencode.ai` y usa únicamente el workspace asociado a esa conexión activa.
  No consulta ni almacena el historial local de sesiones de OpenCode.
- La credencial Go guardada en `auth.json` no se usa. Si la conexión OAuth de
  OpenCode vence, el monitor advierte que hay que volver a conectar la cuenta.

## Instalación

Requiere Python 3 y una sesión de ChatGPT guardada localmente en Firefox.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Prueba finita: `./iniciar.sh --samples 3`.
Pruebas unitarias: `.venv/bin/python -m unittest -v test_monitor.py test_accounts.py`.
Las pruebas del panel requieren PyGObject: `/usr/bin/python3 -m unittest -v test_panel.py`.

La implementación anterior con navegador está conservada en `monitor_browser.py`
como referencia. El monitor actual no la importa ni la ejecuta.

## Indicador en la barra superior de Ubuntu

`panel.py` muestra `GPT  5h 72% · Sem 96%` con **porcentajes restantes**.
Al pulsarlo muestra las cuentas regresivas y fechas de restablecimiento,
la hora de actualización y un acceso al informe completo.

Solo lee `uso.txt`: no hace consultas adicionales ni accede a credenciales.
Si el informe tiene más de 30 segundos o indica un error, muestra `⚠` para
no presentar información antigua como actual.

Utiliza GTK 3, AyatanaAppIndicator y la extensión Ubuntu AppIndicators,
ya disponibles en este equipo. No modifica la configuración de Executor.
La lista de extensiones anterior está respaldada en
`gnome-enabled-extensions-before-panel.json`.

El indicador está configurado como servicio permanente de la sesión gráfica:

```sh
systemctl --user status chatgpt-usage-panel.service
systemctl --user stop chatgpt-usage-panel.service
```

También se puede iniciar manualmente con `./panel.sh`. Cerrar el indicador
no detiene el monitor de datos. Ambos servicios tienen inicio automático habilitado;
el indicador arranca al entrar a la sesión gráfica.

Pruebas del panel: `/usr/bin/python3 -m unittest -v test_panel.py`.

### Porcentajes a la izquierda

Executor ejecuta `panel_text.py` cada 5 segundos en el sector izquierdo.
Los comandos previos de ese sector, que estaba desactivado, se conservaron
desactivados. Su configuración original está en `executor-before-chatgpt-left.json`.

`panel-settings.json` contiene `"icon_only": true` para que a la derecha quede
solo el icono con el menú de detalles, sin duplicar el texto. No se cambió la
posición del resto de los iconos. El texto izquierdo depende de Executor;
el menú derecho es el servicio `chatgpt-usage-panel.service`.

### Menú nativo al hacer clic en los porcentajes (instalado)

La extensión `chatgpt-usage@wlopez.local` está instalada y habilitada. GNOME 46
la descubrirá al **cerrar sesión y volver a entrar**. Mostrará los porcentajes
como un botón a la izquierda que abre su propio menú de restablecimientos.
Lee `uso.json`, generado junto con el informe de texto por el monitor existente.

La interfaz anterior permanece funcionando hasta ese momento. Cuando la nueva
extensión esté realmente activa, `migrate_panel.py` retirará únicamente nuestro
comando de Executor y deshabilitará el antiguo servicio del icono derecho.
No modifica los otros comandos ni detiene el monitor. Guarda una copia de la
configuración de Executor antes de retirar nuestro comando.

No se cerró ni reinició la sesión gráfica automáticamente. El código instalado
es una copia de `gnome-extension/` dentro de esta carpeta.
