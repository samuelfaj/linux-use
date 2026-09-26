# linux-use

A Linux MCP server inspired by mac-use. Native desktop controls require X11 and the corresponding utilities. Chrome tab automation is available through the separately registered extension. Jev decisions are available for owned Chrome tabs with structured controls. Native Linux windows use a screenshot-to-MCP-caller visual-model fallback; screenshots are not sent to Jev, and Linux Accessibility is not implemented.

[English](#english) | [Español](#español) | [Português (Brasil)](#português-brasil)

---

## English

### What works today

`linux-use` provides an MCP server over standard input and output. On an X11 session, `doctor` reports the environment and `list_windows` returns window IDs, process IDs, and titles. `screenshot`, `left_click`, `type`, `key`, and `scroll` are available for explicitly identified windows with a fresh state token; other native actions return an explicit unsupported error. Wayland is not supported.

The project fails closed rather than activating windows or claiming safety checks it cannot provide. Screenshot payloads include the target PID/window ID, geometry, and a state token derived from the target identity and captured pixels. The MCP caller can use the image for visual reasoning and submit a bounded, one-use `native_visual_propose`; a separate `native_visual_execute` call rechecks the exact target and pixels immediately before input. Proposals expire after two minutes, are limited to 32 pending items, and are consumed once. A state token binds the screenshot state but does not prove that a model examined the image or that an action is authorized. Native Linux Accessibility and human-activity detection remain unavailable. Jev advises on filtered structured controls only for an owned Chrome tab; native screenshots are not sent to Jev.

### Requirements

- Linux with an X11 desktop session (`echo "$XDG_SESSION_TYPE"` should print `x11`)
- Python 3
- `wmctrl`
- `xdotool` for input and active-window verification, `xwd` for screenshots, and ImageMagick `convert` or `magick` to encode PNG screenshots
- An MCP client: Distill, Codex, Claude Code, or Grok Build

Wayland sessions are unsupported. Screenshot requires `xwd`; input actions require `xdotool`. If either utility is unavailable, that operation fails with a precise error.

### 1. Install and run the MCP server

Open a terminal and run:

```sh
git clone git@github.com:samuelfaj/linux-use.git
cd linux-use
python3 server.py
```

If you do not use GitHub SSH, replace the clone command with the URL you normally use. Keep this folder in place after setup; each MCP client starts the server from this folder.

### 2. Connect it to your MCP client

Run **one** command for the client you use, from inside the `linux-use` folder:

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

If the client was already open, restart it after adding the server. Keep the repository at the same path; the client starts `server.py` from there.

### Install the agent skill globally

Install the bundled [linux-use skill](skills/linux-use/SKILL.md) from this repository for Codex, Claude Code, and Distill/Grok Build (which share `~/.grok/skills`):

```sh
for root in "$HOME/.codex/skills" "$HOME/.claude/skills" "$HOME/.grok/skills"; do
  mkdir -p "$root/linux-use"
  cp "skills/linux-use/SKILL.md" "$root/linux-use/SKILL.md"
done
```

Start a new client session after installation. The skill requires agents to close resources they create, including after failures, preserve user-owned resources, and verify cleanup. The MCP also advertises a cleanup reminder. This is agent guidance, not an automatic sandbox; the shared Chrome profile retains normal history and site state.

### 3. Check the Linux session

Call `doctor` to see whether an X11 display is available and whether the required utilities are installed. Call `list_windows` to list visible X11 window candidates. Window titles and process IDs may contain private information; treat the result accordingly.

`doctor`, `list_windows`, `restore_window`, `screenshot`, `left_click`, `type`, `key`, `scroll`, `native_visual_propose`, and `native_visual_execute` are available on X11 when their required utilities are installed. For native windows, the screenshot image goes to the MCP caller's visual model; the server does not send it to Jev. Proposals are separate from execution and are bound to the exact target and screenshot token, which the server revalidates before input. Jev remains available only for an owned Chrome tab with credentials and structured controls; it does not decide native Linux actions. Browser tools route through the registered Chrome native-messaging host and fail closed when disconnected.

### Chrome extension status

The extension connects to the Linux MCP server through Chrome native messaging and a mode-0600 Unix socket under `~/.local/share/linux-use`. Register it using `python3 server.py install-chrome-host EXTENSION_ID`, then click the extension icon to connect. Browser automation is independent of the X11-only native desktop backend. Owned tabs are created inactive; the extension refuses reads or actions after human takeover and only best-effort closes tabs that remain inactive. Chrome cannot make the activity check and tab removal atomic, so a selection racing with removal may still be closed.

### Tests

Run the available tests from the `linux-use` folder:

```sh
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

These tests cover MCP/helper behavior and the extension's mocked tab-ownership logic. They do not verify operation on a real Linux X11 desktop or end-to-end Chrome integration. Linux desktop verification is still required.

---

## Español

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

La carpeta `extension/` contiene la extensión de Chrome. Registra el host con `python3 server.py install-chrome-host EXTENSION_ID` y haz clic en el icono de la extensión para conectar. El servidor MCP reenvía las solicitudes mediante un socket Unix local con permisos restringidos. La automatización del navegador es independiente de los controles de escritorio X11. Las pestañas propias se crean en segundo plano; la extensión deja de actuar cuando detecta que el usuario seleccionó una pestaña. Chrome no puede hacer atómicas la comprobación de actividad y la eliminación de la pestaña, así que una selección que coincida con la eliminación todavía podría cerrarse.

### Pruebas

Ejecuta las pruebas disponibles desde la carpeta `linux-use`:

```sh
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

Estas pruebas cubren el comportamiento MCP y las funciones auxiliares, además de la lógica de propiedad de pestañas de la extensión con Chrome simulado. No verifican el funcionamiento en un escritorio Linux X11 real ni la integración completa con Chrome. Aún hace falta verificarlo en Linux.

---

## Português (Brasil)

### O que funciona atualmente

O `linux-use` oferece um servidor MCP por entrada e saída padrão. Em uma sessão X11, `doctor` informa o estado do ambiente e `list_windows` retorna IDs de janela, PID e títulos. Também são suportadas capturas PNG e ações de clique esquerdo, digitação, teclas e rolagem em uma janela identificada explicitamente e com token de estado atual. Wayland não é compatível.

As ações de entrada revalidam a identidade e o estado visual da janela antes do envio. As capturas exigem `xwd` e ImageMagick; as ações de entrada exigem `xdotool`. Acessibilidade e ações semânticas nativas ainda não estão disponíveis. O Jev pode avaliar controles filtrados de uma aba própria do Chrome; ele não controla janelas nativas. A automação do navegador funciona pela extensão registrada separadamente.

### Requisitos

- Linux com uma sessão gráfica X11 (`echo "$XDG_SESSION_TYPE"` deve exibir `x11`)
- Python 3
- `wmctrl`
- `xdotool` para ações de entrada e verificação da janela ativa
- `xwd` e ImageMagick (`convert` ou `magick`) para capturas PNG
- Um cliente MCP: Distill, Codex, Claude Code ou Grok Build

Sessões Wayland não são compatíveis. Se uma ferramenta necessária estiver ausente, a operação correspondente informa o erro sem executá-la.

### 1. Instale e inicie o servidor MCP

Abra o terminal e execute:

```sh
git clone git@github.com:samuelfaj/linux-use.git
cd linux-use
python3 server.py
```

Se você não usa SSH do GitHub, substitua o comando de clonagem pela URL que costuma usar. Mantenha esta pasta no mesmo local após a instalação; cada cliente MCP inicia o servidor a partir dela.

### 2. Conecte ao seu cliente MCP

Execute **apenas um** comando para o cliente que você usa, dentro da pasta `linux-use`:

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

Se o cliente já estiver aberto, reinicie-o depois de adicionar o servidor. Mantenha o repositório no mesmo caminho; o cliente inicia o `server.py` a partir dele.

### Instale a skill do agente globalmente

Instale a [skill linux-use](skills/linux-use/SKILL.md) incluída neste repositório para Codex, Claude Code e Distill/Grok Build (que compartilham `~/.grok/skills`):

```sh
for root in "$HOME/.codex/skills" "$HOME/.claude/skills" "$HOME/.grok/skills"; do
  mkdir -p "$root/linux-use"
  cp "skills/linux-use/SKILL.md" "$root/linux-use/SKILL.md"
done
```

Inicie uma nova sessão do cliente após instalar. A skill exige fechar os recursos criados pelo agente inclusive em caso de erro, preservar os recursos do usuário e verificar a limpeza. O MCP também fornece um lembrete. São instruções para o agente, não um isolamento automático; o perfil compartilhado do Chrome mantém histórico e dados dos sites.

### 3. Confira a sessão Linux

Chame `doctor` para verificar se há uma tela X11 disponível e se os utilitários necessários estão instalados. Chame `list_windows` para listar as janelas X11 encontradas. Os títulos das janelas e os identificadores de processos podem conter informações privadas; trate esses dados com cuidado.

Funcionam `doctor`, `list_windows`, `restore_window`, `screenshot`, `left_click`, `type`, `key`, `scroll`, `native_visual_propose` e `native_visual_execute` quando os utilitários necessários estão instalados. A imagem é entregue ao modelo visual do cliente MCP, não ao Jev. A proposta fica vinculada ao alvo e ao token; a execução revalida o estado e consome a proposta uma única vez. O Jev decide usando controles estruturados de uma aba própria do Chrome, não janelas Linux nativas. `zoom`, `cursor_position`, `get_ui_tree` e `click_element` continuam indisponíveis.

### Estado da extensão do Chrome

A pasta `extension/` contém a extensão do Chrome. Registre o host com `python3 server.py install-chrome-host EXTENSION_ID` e clique no ícone da extensão para conectar. O servidor MCP encaminha as solicitações por um socket Unix local com permissões restritas. A automação do navegador é independente dos controles de desktop X11. As abas próprias são abertas em segundo plano; a extensão para de agir quando detecta que o usuário selecionou uma aba. O Chrome não torna atômicas a verificação de atividade e a remoção da aba, então uma seleção simultânea à remoção ainda pode resultar no fechamento.

### Testes

Execute os testes disponíveis dentro da pasta `linux-use`:

```sh
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

Esses testes cobrem o comportamento MCP e as funções auxiliares, além da lógica de posse de abas da extensão com uma API Chrome simulada. Eles não verificam o funcionamento em um ambiente Linux X11 real nem a integração completa com o Chrome. Ainda é necessária uma verificação em Linux.
