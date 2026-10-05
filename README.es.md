# linux-use (Español)

Read this in: [English](README.md) · [Português](README.pt-BR.md)

### Qué funciona actualmente

`linux-use` ofrece un servidor MCP mediante la entrada y salida estándar. En una sesión X11, `doctor` informa sobre el entorno y `list_windows` devuelve identificadores de ventana, PID y títulos. También admite capturas PNG y acciones de clic izquierdo, escritura, teclas y desplazamiento en una ventana identificada explícitamente y con un token de estado vigente. Wayland no es compatible.

Las capturas devuelven la imagen al modelo visual del cliente MCP, no a Jev. El modelo puede registrar una propuesta nativa de un solo uso con `native_visual_propose`; `native_visual_execute` vuelve a validar la ventana y los píxeles antes de enviar la entrada. Las propuestas vencen en dos minutos, hay un máximo de 32 y cada una se consume una sola vez. El token vincula el estado de la captura, pero no demuestra quién tomó la decisión ni que exista autorización. La Accesibilidad nativa sigue sin estar disponible. Jev asesora sobre controles estructurados de una pestaña Chrome propia; no decide acciones nativas de Linux.

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

Funcionan `doctor`, `list_windows`, `restore_window`, `screenshot`, `left_click`, `type`, `key`, `scroll`, `native_visual_propose` y `native_visual_execute` cuando están instaladas las utilidades requeridas. La imagen se entrega al modelo visual del cliente MCP, no a Jev. La propuesta se vincula al objetivo y al token; la ejecución la revalida y consume una sola vez. Jev decide sobre controles estructurados de una pestaña Chrome propia, no sobre ventanas Linux nativas. `zoom`, `cursor_position`, `get_ui_tree` y `click_element` siguen sin estar disponibles.

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
