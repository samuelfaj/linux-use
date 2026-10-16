# linux-use (Español)

Read this in: [English](README.md) · [Português](README.pt-BR.md)

### Qué funciona actualmente

`linux-use` ofrece un servidor MCP mediante la entrada y salida estándar. En una sesión X11, `doctor` informa sobre el entorno y `list_windows` devuelve identificadores de ventana, PID y títulos. También admite capturas PNG y acciones de clic izquierdo, escritura, teclas y desplazamiento en una ventana identificada explícitamente y con un token de estado vigente. Wayland no es compatible.

Las capturas devuelven la imagen al modelo visual del cliente MCP, no a Jev. El modelo puede registrar una propuesta nativa de un solo uso con `native_visual_propose`; `native_visual_execute` vuelve a validar la ventana y los píxeles antes de enviar la entrada. Las propuestas vencen en dos minutos, hay un máximo de 32 y cada una se consume una sola vez. El token vincula el estado de la captura, pero no demuestra quién tomó la decisión ni que exista autorización. No hay un árbol completo de elementos de Accesibilidad nativa; las acciones opcionales de menú/valor AT-SPI se describen abajo. Jev asesora sobre controles de la pestaña propia de Chrome y evalúa criterios de `verify_state` a partir de evidencia textual disponible; no recibe capturas nativas.

### Herramientas de paridad con cua-driver

Inspirados en [cua-driver de trycua/cua](https://github.com/trycua/cua/tree/main/libs/cua-driver), estos grupos amplían las herramientas existentes:

- **Aplicaciones:** `list_apps`, `launch_app`, `kill_app`, `bring_to_front`. Inicia en segundo plano; fuerza la terminación solo de aplicaciones que hayas iniciado. `bring_to_front` activa de forma explícita y persistente; no es una alternativa automática para enviar entrada.
- **Escritorio:** `list_windows`, `get_accessibility_tree` (resumen de aplicaciones y ventanas), `get_desktop_state` (captura de toda la pantalla), `get_screen_size`, `get_cursor_position`, `move_cursor`. El movimiento con ámbito de ventana solo actualiza la superposición del agente; `scope: "desktop"` mueve el puntero real.
- **Entrada:** `click`, `double_click`, `right_click`, `drag`, `scroll`, `type_text`, `press_key`, `hotkey` y las herramientas de entrada existentes. El segundo plano es el modo predeterminado. Las herramientas que exponen `delivery_mode` aceptan `"background"` o `"foreground"`; solo un `delivery_mode: "foreground"` explícito activa temporalmente el objetivo y restaura después la aplicación anterior. Las mutaciones se rechazan con `human_activity` cuando la protección detecta al usuario en el objetivo; no intentes eludir el rechazo.
- **Menús y geometría de ventana:** `invoke_menu` recorre una ruta exacta de menú y rechaza elementos ausentes, ambiguos o deshabilitados; `set_value` modifica un control compatible; `set_window_frame` cambia posición y tamaño y los verifica mediante lectura posterior.
- **Verificación:** `verify_state` devuelve `pass`, `fail` o `unknown` para expectativas estructuradas y comprobaciones en lenguaje natural mediante `expect.criteria`, evaluadas por Jev (de 1 a 8 cadenas no vacías, de hasta 200 caracteres cada una; por ejemplo, `expect: {"criteria": ["El botón Guardar está visible"]}`). Un resultado desconocido no demuestra el éxito.
- **Portapapeles:** `clipboard_read` (texto solo con `include_text: true`) y `clipboard_write` (texto, imagen o archivo). Registra el contenido anterior antes de sobrescribirlo y restáuralo solo si el usuario no lo ha cambiado desde entonces.
- **Configuración, permisos y diagnóstico:** `get_config`, `set_config`, `check_permissions`, `health_report` y `doctor`. La configuración se guarda en `~/.linux-use/config.json`; usa el diagnóstico para comprobar los requisitos de la plataforma en vez de dar por disponibles permisos o utilidades.
- **Sesiones y cursor del agente:** `start_session`, `end_session`, `get_session_state`; `set_agent_cursor_enabled`, `set_agent_cursor_theme`, `set_agent_cursor_motion`, `get_agent_cursor_state`. La superposición visual deja pasar los clics, no toma el foco y nunca mueve el puntero real. Registra la identidad de la sesión y termina las sesiones que inicies.
- **Grabación y reproducción de trayectorias:** `start_recording`, `stop_recording`, `get_recording_state`, `replay_trajectory`. Cada `turn-0000N/` guarda `action.json`, `before.png` y `after.png`. El vídeo opcional usa captura X11 mediante `ffmpeg` si está instalado. Detén las grabaciones que inicies. Pueden contener argumentos de acciones e imágenes sensibles; protege los archivos. La reproducción repite acciones reales y exige la misma autorización y las mismas comprobaciones que la ejecución en directo.
- **Pestaña propia de Chrome:** las herramientas existentes `browser_open`, `browser_snapshot`, `browser_act`, `browser_close`, `browser_status`, más `browser_navigate`, `browser_click`, `browser_type` (`insert_text` o `keystrokes`), `browser_pointer` (pasar el puntero, clic derecho, doble clic, desplazamiento, arrastre), `browser_dialog` (inspeccionar, aceptar, descartar, texto de un diálogo de entrada), `browser_set_input_files`, `browser_download` y `page` (`execute_javascript`, `get_text`, `query_dom`, `click_element`, `insert_text`, `type_keystrokes`). Solo actúan sobre la pestaña de la sesión, nunca sobre pestañas arbitrarias del usuario. Usa referencias nuevas tras las mutaciones y cede el control cuando el usuario lo tome.

La extensión de Chrome añade los permisos `"debugger"` y `"downloads"` para entrada de confianza, diálogos, selección de archivos y descargas. Recarga la extensión actualizada y acepta los nuevos permisos si se solicitan. Chrome puede mostrar su barra amarilla de **depuración** mientras `chrome.debugger` está conectado durante las acciones; un diálogo abierto puede mantener la conexión hasta que se gestione. Esto no concede control sobre otras pestañas.

Linux sigue limitado a X11. `get_accessibility_tree` resume ventanas, no es un árbol de elementos AT-SPI; `invoke_menu` y `set_value` requieren un entorno Python `gi`/AT-SPI ya instalado. El portapapeles necesita `xclip` o `xsel` (imágenes/archivos requieren `xclip`); la superposición depende de un entorno gráfico disponible. Comprueba `health_report` y `overlay_available`. La detección de actividad necesita `xprintidle`; el diagnóstico indica que se omite si falta, así que no supongas que la entrada en un escritorio compartido está protegida sin él. Algunas acciones de puntero X11 mueven el puntero real incluso en segundo plano; usa un escritorio aislado si pueden interferir. Las herramientas anteriores `get_ui_tree`, `click_element`, `zoom` y `cursor_position` siguen sin ser compatibles; usa `get_cursor_position` para la nueva consulta del cursor.

### Jev primero

Jev es la principal ventaja: prefiere decisiones basadas solo en texto en vez de pedir al LLM de la sesión que lea capturas. Usa `jev_decide` para un paso, un bucle de observación/decisión/acción gestionado por el cliente (`run_subtask` está disponible en mac-use, no en este catálogo de Linux), y `verify_state` con `expect.criteria` en lenguaje natural para verificar el resultado. Para Chrome, usa `jev_decide` con `browser: true` en la pestaña propia. Jev recibe etiquetas, roles y texto de objetivo/criterios filtrados, nunca capturas, valores de campos ni secretos. Es más rápido y barato que razonar sobre capturas; las etiquetas y los objetivos aún pueden contener información privada. Usa capturas solo cuando falte evidencia estructurada o no sea suficiente.

Configura `JEV_API_KEY`, `TYPESAFE_API_KEY` u `OPENROUTER_API_KEY`. Sin clave, usa los candidatos o la evidencia local devueltos con el LLM de la sesión; no afirmes que una comprobación de Jev pasó. Un error de Jev configurado no implica éxito ni permiso para actuar. Jev asesora, nunca autoriza acciones con consecuencias.

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

Funcionan `doctor`, `list_windows`, `restore_window`, `screenshot`, `left_click`, `type`, `key`, `scroll`, `native_visual_propose` y `native_visual_execute` cuando están instaladas las utilidades requeridas. La imagen se entrega al modelo visual del cliente MCP, no a Jev. La propuesta se vincula al objetivo y al token; la ejecución la revalida y consume una sola vez. Prioriza Jev para controles de la pestaña propia de Chrome y criterios de `verify_state` basados en evidencia textual; las propuestas visuales nativas quedan como alternativa con capturas. `zoom`, `cursor_position`, `get_ui_tree` y `click_element` siguen sin estar disponibles.

### Estado de la extensión de Chrome

La carpeta `extension/` contiene la extensión de Chrome. Registra el host con `python3 server.py install-chrome-host EXTENSION_ID`; la extensión se conecta sola y se reconecta automáticamente cada 30 segundos si se cae (un clic en el icono reintenta al instante). El servidor MCP reenvía las solicitudes mediante un socket Unix local con permisos restringidos. La automatización del navegador es independiente de los controles de escritorio X11. Las pestañas propias se crean en segundo plano; la extensión deja de actuar cuando detecta que el usuario seleccionó una pestaña. Chrome no puede hacer atómicas la comprobación de actividad y la eliminación de la pestaña, así que una selección que coincida con la eliminación todavía podría cerrarse.

### Pruebas

Ejecuta las pruebas disponibles desde la carpeta `linux-use`:

```sh
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

Estas pruebas cubren el comportamiento MCP y las funciones auxiliares, además de la lógica de propiedad de pestañas de la extensión con Chrome simulado. No verifican el funcionamiento en un escritorio Linux X11 real ni la integración completa con Chrome. Aún hace falta verificarlo en Linux.

### Estabilización del navegador, selects y protecciones de Jev

`browser_act` ahora espera a que la página se estabilice (fetch/XHR iniciados por la acción más 150 ms sin cambios en el DOM, con límite de 2 s, o 10 s mientras haya solicitudes iniciadas por la acción pendientes) y devuelve una **instantánea nueva** con `settle_ms` y `settled`; las referencias de la instantánea anterior quedan obsoletas. Las instantáneas listan las opciones de `<select>` (hasta 50, con el valor seleccionado) y `fill` en un select acepta el valor o el texto de una opción. Los elementos cubiertos se marcan con `covered: true` y `browser_act` los rechaza (`element is covered`). Las regiones activas (`role=status`/`alert`, `aria-live`) permanecen en la instantánea con `offscreen: true` si están fuera de la vista.

`jev_decide` retiene los controles que nombran efectos de eliminar, enviar, comprar o cerrar, salvo que figuren en el parámetro opcional `allowed_risks` (solo para categorías que el objetivo autoriza). Un destino riesgoso elegido cuenta como material. Devuelve `margin` (probabilidad elegida menos la mejor alternativa), y `min_confidence` / `min_margin` opcionales (0..1) convierten decisiones más débiles en `NEEDS_AGENT`.

### Agradecimientos

Gracias a [shhivv](https://github.com/shhivv) y [arc-cua](https://github.com/shhivv/arc-cua), la inspiración de la estabilización del navegador, el soporte de selects y las protecciones `allowed_risks`, `min_confidence` y `min_margin`.
