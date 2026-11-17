# linux-use (Español)

Read this in: [English](README.md) · [Português](README.pt-BR.md)

### Qué funciona actualmente

`linux-use` ofrece un servidor MCP mediante la entrada y salida estándar. En una sesión X11, `doctor` informa sobre el entorno y `list_windows` devuelve identificadores de ventana, PID y títulos. También admite capturas PNG y acciones de clic izquierdo, escritura, teclas y desplazamiento en una ventana identificada explícitamente y con un token de estado vigente. Wayland no es compatible.

La herramienta `screenshot` devuelve la imagen al modelo visual del cliente MCP; `jev_decide` y `verify_state` hacen su propia captura para el modelo de decisión. El modelo puede registrar una propuesta nativa de un solo uso con `native_visual_propose`; `native_visual_execute` vuelve a validar la ventana y los píxeles antes de enviar la entrada. Las propuestas vencen en dos minutos, hay un máximo de 32 y cada una se consume una sola vez. El token vincula el estado de la captura, pero no demuestra quién tomó la decisión ni que exista autorización. Con Python `gi`/AT-SPI instalado, `get_ui_tree` lee el árbol de elementos de una ventana y `click_element`, `set_value` y `run_subtask` actúan sobre esos elementos sin mover el puntero. El modelo de decisión asesora sobre controles filtrados de la pestaña propia de Chrome y de ventanas nativas vía AT-SPI, y evalúa los criterios de `verify_state`; consulta "Modelo de decisión primero".

### Herramientas de paridad con cua-driver

Inspirados en [cua-driver de trycua/cua](https://github.com/trycua/cua/tree/main/libs/cua-driver), estos grupos amplían las herramientas existentes:

- **Aplicaciones:** `list_apps`, `launch_app`, `kill_app`, `bring_to_front`. Inicia en segundo plano; fuerza la terminación solo de aplicaciones que hayas iniciado. `bring_to_front` activa de forma explícita y persistente; no es una alternativa automática para enviar entrada.
- **Escritorio:** `list_windows`, `get_accessibility_tree` (resumen de aplicaciones y ventanas), `get_desktop_state` (captura de toda la pantalla), `get_screen_size`, `get_cursor_position`, `move_cursor`. El movimiento con ámbito de ventana solo actualiza la superposición del agente; `scope: "desktop"` mueve el puntero real.
- **Entrada:** `click`, `double_click`, `right_click`, `drag`, `scroll`, `type_text`, `press_key`, `hotkey` y las herramientas de entrada existentes. El segundo plano es el modo predeterminado. Las herramientas que exponen `delivery_mode` aceptan `"background"` o `"foreground"`; solo un `delivery_mode: "foreground"` explícito activa temporalmente el objetivo y restaura después la aplicación anterior. Las mutaciones se rechazan con `human_activity` cuando la protección detecta al usuario en el objetivo; la entrada enviada por este servidor no cuenta. Cuando ninguna ruta silenciosa sirve, reintenta con `allow_while_user_active: true`, salvo que el usuario haya pedido no ser interrumpido, y di qué interrumpiste. GTK 3 ignora clics y teclas en segundo plano; ahí usa `click_element`/`set_value` o la entrega en primer plano.
- **Elementos de accesibilidad:** `get_ui_tree` devuelve una fila por elemento (`- [ax_12] button "Save" [click]`), 250 por defecto; `query` devuelve solo las filas que coinciden, con sus ancestros, `max_elements` cambia el límite y `format: "json"` devuelve el árbol anidado. Un id `ax_<n>` sigue apuntando al elemento leído mientras el resto de la ventana cambia, siempre que pases el `state_token` de esa lectura como `expected_state_token`; un elemento que cambió devuelve `stale_state_token`. `click_element` acciona el elemento con su acción AT-SPI sin mover el puntero e informa `effect`: `ui_changed` cuando la ventana reaccionó, si no `unverified`.
- **Menús y geometría de ventana:** `invoke_menu` recorre una ruta exacta de menú y rechaza elementos ausentes, ambiguos o deshabilitados; `set_value` modifica un control elegido por `element_id` o por rol/etiqueta inequívocos y lo verifica por lectura; `set_window_frame` cambia posición y tamaño y los verifica mediante lectura posterior.
- **Verificación:** `verify_state` devuelve `pass`, `fail` o `unknown` para expectativas estructuradas (incluidas `element_exists`, `element_value` y `focused_element`, leídas vía AT-SPI) y comprobaciones en lenguaje natural mediante `expect.criteria`, evaluadas por el modelo de decisión (de 1 a 8 cadenas no vacías, de hasta 200 caracteres cada una; por ejemplo, `expect: {"criteria": ["El botón Guardar está visible"]}`). Un resultado desconocido no demuestra el éxito.
- **Portapapeles:** `clipboard_read` (texto solo con `include_text: true`) y `clipboard_write` (texto, imagen o archivo). Registra el contenido anterior antes de sobrescribirlo y restáuralo solo si el usuario no lo ha cambiado desde entonces.
- **Configuración, permisos y diagnóstico:** `get_config`, `set_config`, `check_permissions`, `health_report` y `doctor`. La configuración se guarda en `~/.linux-use/config.json`; usa el diagnóstico para comprobar los requisitos de la plataforma en vez de dar por disponibles permisos o utilidades.
- **Sesiones y cursor del agente:** `start_session`, `end_session`, `get_session_state`; `set_agent_cursor_enabled`, `set_agent_cursor_theme`, `set_agent_cursor_motion`, `get_agent_cursor_state`. La superposición visual deja pasar los clics, no toma el foco y nunca mueve el puntero real. Registra la identidad de la sesión y termina las sesiones que inicies.
- **Grabación y reproducción de trayectorias:** `start_recording`, `stop_recording`, `get_recording_state`, `replay_trajectory`. Cada `turn-0000N/` guarda `action.json`, `before.png` y `after.png`. El vídeo opcional usa captura X11 mediante `ffmpeg` si está instalado. Detén las grabaciones que inicies. Pueden contener argumentos de acciones e imágenes sensibles; protege los archivos. La reproducción repite acciones reales y exige la misma autorización y las mismas comprobaciones que la ejecución en directo.
- **Pestaña propia de Chrome:** las herramientas existentes `browser_open`, `browser_snapshot`, `browser_act`, `browser_close`, `browser_status`, más `browser_navigate`, `browser_click`, `browser_type` (`insert_text` o `keystrokes`), `browser_pointer` (pasar el puntero, clic derecho, doble clic, desplazamiento, arrastre), `browser_dialog` (inspeccionar, aceptar, descartar, texto de un diálogo de entrada), `browser_set_input_files`, `browser_download` y `page` (`execute_javascript`, `get_text`, `query_dom`, `click_element`, `insert_text`, `type_keystrokes`). Solo actúan sobre la pestaña de la sesión, nunca sobre pestañas arbitrarias del usuario. Usa referencias nuevas tras las mutaciones y cede el control cuando el usuario lo tome.

La extensión de Chrome añade los permisos `"debugger"`, `"downloads"` y `"tabGroups"` para entrada de confianza, diálogos, selección de archivos, descargas y agrupar sus pestañas. Recarga la extensión actualizada y acepta los nuevos permisos si se solicitan. Chrome muestra su barra amarilla de **depuración** desde `browser_open` hasta `browser_close`: el depurador sigue conectado para que la pestaña en segundo plano siga renderizando y Chrome no la congele. Esto no concede control sobre otras pestañas.

Linux sigue limitado a X11. `get_accessibility_tree` resume ventanas, no es un árbol de elementos AT-SPI; `invoke_menu` y `set_value` requieren un entorno Python `gi`/AT-SPI ya instalado. El portapapeles necesita `xclip` o `xsel` (imágenes/archivos requieren `xclip`); la superposición depende de un entorno gráfico disponible. Comprueba `health_report` y `overlay_available`. La detección de actividad necesita `xprintidle`; el diagnóstico indica que se omite si falta, así que no supongas que la entrada en un escritorio compartido está protegida sin él. Algunas acciones de puntero X11 mueven el puntero real incluso en segundo plano; usa un escritorio aislado si pueden interferir. `get_ui_tree`, `click_element` y `run_subtask` necesitan Python `gi`/AT-SPI y una aplicación que exponga AT-SPI (las apps GTK y Qt lo hacen; Chromium y Electron solo si se inician con la accesibilidad activada). `zoom` devuelve un recorte a resolución completa de una región de la ventana; la herramienta anterior `cursor_position` sigue sin ser compatible, así que usa `get_cursor_position` para consultar el cursor.

### Modelo de decisión primero

Un modelo de decisión es la principal ventaja: prefiere decisiones tipadas y baratas en vez de pedir al LLM de la sesión que lea capturas. Usa `jev_decide` para un paso, `run_subtask` para una tarea nativa de varios pasos acotada, y `verify_state` con `expect.criteria` en lenguaje natural para verificar el resultado. El modo nativo recibe `target_pid` y `target_window_id`; sin ellos, o con `browser: true`, `jev_decide` asesora sobre la pestaña propia de Chrome. Los nombres de las herramientas conservan el prefijo `jev` por compatibilidad.

Con `OPENROUTER_API_KEY`, toda decisión va a `liquid/d1.2-ultra` de Liquid en OpenRouter. Jev (`typesafe/jev-1.14`, mismo endpoint y misma clave) solo responde cuando d1.2-ultra no está disponible: error de red, timeout (5 s para texto, 10 s con imagen) o HTTP 404, 408, 429 o 5xx. Una solicitud rechazada (HTTP 400, 401, 402 o 422) es un error y nunca se repite en Jev. Sin `OPENROUTER_API_KEY`, `JEV_API_KEY` o `TYPESAFE_API_KEY` llaman directamente al Jev de TypeSafe, sin fallback. Cada respuesta indica en `engine` el modelo que decidió; una respuesta de fallback añade `fallback_reason` y está menos calibrada que d1.2-ultra.

`jev_decide` y `verify_state` nativos envían al modelo etiquetas y roles AT-SPI filtrados, el título de la ventana y el texto del objetivo o de los criterios, nunca valores de campos ni secretos como texto, junto con una captura de la ventana exacta de como máximo 1024 px en su lado mayor. La captura muestra todo lo que muestra la ventana, incluido el contenido de los campos: una exportación terminada, un interruptor ya activado o una subida en curso a menudo solo se ven en los píxeles, y solo con etiquetas el modelo puede elegir el control equivocado con mucha confianza. Una ventana con un campo de contraseña AT-SPI nunca se captura; en ese caso, o cuando la captura falla, la decisión usa solo las etiquetas. Sin AT-SPI, linux-use no ve los campos de contraseña y envía la captura igualmente, donde aparecen enmascarados. Los resultados incluyen `image_attached`, e `image_skip_reason` cuando no se envió ninguna captura. Las capturas y las etiquetas van a OpenRouter y al proveedor del modelo y pueden contener información privada. `run_subtask` envía etiquetas, valores de los elementos y los textos fijos de la ventana, con las entradas secretas ocultas, y ninguna captura; `browser: true` envía solo las etiquetas de la pestaña.

`jev_decide` sugiere un paso y nunca actúa. Con clave, la respuesta incluye la propuesta (`click_element` con `element_id` y `expected_state_token` en una ventana nativa, `browser_act` con `ref` en Chrome), hasta tres `alternatives`, las `signals` (`complete`, `consequential`, `authorized`) y un `reason` cuando devuelve `BLOCKED`, para que el LLM de tu sesión revise la sugerencia o te pregunte. `DONE` exige probabilidad y evidencia de finalización de al menos 0,9. Sin clave, devuelve `DEFER_TO_LLM` con candidatos filtrados para que el LLM de la sesión elija; no afirmes que una comprobación del modelo pasó. Si los modelos configurados fallan, la herramienta devuelve un error en lugar de cambiar en silencio. El modelo asesora; nunca autoriza acciones con consecuencias.

`run_subtask` recibe `goal`, `verification` (lista no vacía) y, opcionalmente, `constraints`, `inputs`, `secret_inputs` (claves de `inputs` que se escriben pero nunca se muestran al modelo, se devuelven ni se registran), `max_actions` (por defecto 30), `allowed_risks`, `dry_run`, `min_confidence`, `min_margin` y `log_path`. Lee el árbol AT-SPI de la ventana, pide un paso al modelo (`CLICK`, `SET_VALUE`, `WAIT`, `DONE`, `BLOCKED` o `NEEDS_INPUT`), actúa vía AT-SPI y vuelve a leer; devuelve `SUBTASK_COMPLETE`, `BLOCKED`, `NEEDS_INPUT`, `NEEDS_AGENT` o `DRY_RUN`. El texto escrito sale solo de `inputs`. Los atajos de teclado no se ofrecen en Linux, donde ninguna ruta en segundo plano los pulsa de forma fiable.

### Requisitos

- Linux con una sesión de escritorio X11 (`echo "$XDG_SESSION_TYPE"` debería mostrar `x11`)
- Python 3
- `wmctrl`
- `xdotool` para acciones de entrada y comprobar la ventana activa
- `xwd` e ImageMagick (`convert` o `magick`) para capturas PNG
- Un cliente MCP: Distill, Codex, Claude Code o Grok Build

Las sesiones Wayland no son compatibles. Si falta alguna utilidad necesaria, la operación correspondiente informa del error sin ejecutarse.

### 1. Instalar e iniciar el servidor MCP

Abre una terminal y ejecuta:

```sh
git clone git@github.com:samuelfaj/linux-use.git
cd linux-use
python3 server.py
```

Si no utilizas GitHub SSH, sustituye el comando de clonación por la URL que usas normalmente. Conserva esta carpeta después de la instalación; cada cliente MCP inicia el servidor desde aquí.

### 2. Conectarlo a tu cliente MCP

Ejecuta **un solo** comando para el cliente que utilices, desde la carpeta `linux-use`:

- **Distill:**

  ```sh
  distill mcp add linux-use -- python3 "$(pwd)/server.py"
  distill mcp doctor linux-use
  ```

- **Codex:**

  ```sh
  codex mcp add linux-use -- python3 "$(pwd)/server.py"
  ```

- **Claude Code:**

  ```sh
  claude mcp add --scope user linux-use -- python3 "$(pwd)/server.py"
  ```

- **Grok Build:**

  ```sh
  grok mcp add --scope user linux-use -- python3 "$(pwd)/server.py"
  ```

Si el cliente ya estaba abierto, reinícialo después de añadir el servidor. Conserva el repositorio en la misma ruta; el cliente inicia `server.py` desde allí.

### Instalar la skill del agente globalmente

Instala la [skill linux-use](skills/linux-use/SKILL.md) incluida en este repositorio para Codex, Claude Code y Distill/Grok Build (que comparten `~/.grok/skills`):

```sh
for root in "$HOME/.codex/skills" "$HOME/.claude/skills" "$HOME/.grok/skills"; do
  mkdir -p "$root/linux-use"
  cp "skills/linux-use/SKILL.md" "$root/linux-use/SKILL.md"
done
```

Inicia una sesión nueva del cliente después de instalarla. La skill exige cerrar los recursos creados por el agente incluso ante errores, preservar los recursos del usuario y verificar la limpieza. El MCP también comunica un recordatorio. Son instrucciones para el agente, no un entorno aislado automático; el perfil compartido de Chrome conserva su historial y los datos de los sitios.

### 3. Comprobar la sesión de Linux

Llama a `doctor` para comprobar si hay una pantalla X11 disponible y si están instaladas las utilidades necesarias. Llama a `list_windows` para obtener las ventanas X11 detectadas. Los títulos y los identificadores de procesos pueden contener información privada; trata estos resultados con cuidado.

Funcionan `doctor`, `list_windows`, `restore_window`, `screenshot`, `left_click`, `type`, `key`, `scroll`, `native_visual_propose` y `native_visual_execute` cuando están instaladas las utilidades requeridas. La imagen de `screenshot` se entrega al modelo visual del cliente MCP; `jev_decide` y `verify_state` hacen su propia captura para el modelo de decisión. La propuesta se vincula al objetivo y al token; la ejecución la revalida y consume una sola vez. Prioriza el modelo de decisión para ventanas AT-SPI, controles de la pestaña propia de Chrome y criterios de `verify_state`; las propuestas visuales nativas quedan como alternativa con capturas. `cursor_position` sigue sin estar disponible; usa `get_cursor_position`.

### Estado de la extensión de Chrome

La carpeta `extension/` contiene la extensión de Chrome. Registra el host con `python3 server.py install-chrome-host EXTENSION_ID`; la extensión se conecta sola y se reconecta automáticamente cada 30 segundos si se cae (un clic en el icono reintenta al instante). En tu cliente MCP, llama a `browser_status`; debe indicar `state: "connected"`. Cualquier otro estado (`busy`, `outdated`, `not_connected`, `not_installed`) incluye una pista sobre qué hacer, y `doctor` y `health_report` incluyen la misma comprobación. El servidor MCP reenvía las solicitudes mediante un socket Unix local con permisos restringidos. La automatización del navegador es independiente de los controles de escritorio X11.

Las pestañas propias se crean en segundo plano dentro de un grupo de pestañas "linux-use" contraído; sacar una pestaña del grupo o seleccionarla te devuelve el control. A partir de ahí la extensión rechaza lecturas y acciones en esa pestaña, salvo que el agente reintente con `allow_while_user_active: true`, cosa que solo debe hacer cuando ninguna pestaña en segundo plano sirve y no pediste que no te interrumpieran. `browser_snapshot` acepta `query` (una subcadena del rol, nombre o valor de un control, `a|b` para alternativas) y devuelve solo los controles que coinciden, sin el texto de la página. Un diálogo de JavaScript se devuelve como `dialog` en lugar de bloquear la página; respóndelo con `browser_dialog`. Cada solicitud responde en 30 segundos como máximo: una página que no responde devuelve `tab_unresponsive`, y su pestaña se libera y se cierra salvo que la hayas seleccionado. Cuando el cliente MCP termina, el servidor cierra la pestaña que aún le pertenece. La extensión solo intenta cerrar pestañas que siguen inactivas; Chrome no puede hacer atómicas la comprobación de actividad y la eliminación de la pestaña, así que una selección que coincida con la eliminación todavía podría cerrarse.

### Pruebas

Ejecuta las pruebas disponibles desde la carpeta `linux-use`:

```sh
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

Estas pruebas cubren el comportamiento MCP y las funciones auxiliares, además de la lógica de propiedad de pestañas de la extensión con Chrome simulado. No verifican el funcionamiento en un escritorio Linux X11 real ni la integración completa con Chrome. Aún hace falta verificarlo en Linux.

### Estabilización del navegador, selects y protecciones de las decisiones

`browser_act` ahora espera a que la página se estabilice (fetch/XHR iniciados por la acción más 150 ms sin cambios en el DOM, con límite de 2 s, o 10 s mientras haya solicitudes iniciadas por la acción pendientes) y devuelve una **instantánea nueva** con `settle_ms` y `settled`; las referencias de la instantánea anterior quedan obsoletas. Las instantáneas listan las opciones de `<select>` (hasta 50, con el valor seleccionado) y `fill` en un select acepta el valor o el texto de una opción. Los elementos cubiertos se marcan con `covered: true` y `browser_act` los rechaza (`element is covered`). Las regiones activas (`role=status`/`alert`, `aria-live`) permanecen en la instantánea con `offscreen: true` si están fuera de la vista.

`jev_decide` retiene los controles que nombran efectos de eliminar, enviar, comprar o cerrar, salvo que figuren en el parámetro opcional `allowed_risks` (solo para categorías que el objetivo autoriza). Un destino riesgoso elegido cuenta como material. Devuelve `margin` (probabilidad elegida menos la mejor alternativa), y `min_confidence` / `min_margin` opcionales (0..1) convierten decisiones más débiles en `NEEDS_AGENT`.

### Agradecimientos

Gracias a [shhivv](https://github.com/shhivv) y [arc-cua](https://github.com/shhivv/arc-cua), la inspiración de la estabilización del navegador, el soporte de selects y las protecciones `allowed_risks`, `min_confidence` y `min_margin`.
