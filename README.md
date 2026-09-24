# linux-use

An X11-only Linux MCP server inspired by mac-use. This prototype currently supports environment diagnostics and window listing; native window control and Chrome tab automation are not implemented.

[English](#english) | [Español](#español) | [Português (Brasil)](#português-brasil)

---

## English

### What works today

`linux-use` provides an MCP server over standard input and output. On an X11 session, `doctor` reports the environment and `list_windows` returns window IDs, process IDs, and titles. The server advertises the mac-use tool names for compatibility, but all other native actions return an explicit unsupported error. Wayland is not supported.

The project fails closed rather than activating windows, injecting input, or claiming safety checks it cannot provide. In particular, screenshots, Accessibility trees, state-token validation, human-activity detection, clicks, typing, semantic actions, and Jev advice are unavailable.

### Requirements

- Linux with an X11 desktop session (`echo "$XDG_SESSION_TYPE"` should print `x11`)
- Python 3
- `wmctrl`
- An MCP client: Distill, Codex, Claude Code, or Grok Build

Wayland sessions are unsupported. `xdotool` is not used to perform input; no input actions are implemented.

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

### 3. Check the Linux session

Call `doctor` to see whether an X11 display is available and whether the required utilities are installed. Call `list_windows` to list visible X11 window candidates. Window titles and process IDs may contain private information; treat the result accordingly.

Only `doctor` and `list_windows` currently work. The following tools are advertised but return an unsupported error: `screenshot`, `zoom`, `cursor_position`, `get_ui_tree`, `jev_decide`, `left_click`, `type`, `click_element`, and the browser tools.

### Chrome extension status

The `extension/` directory contains an extension based on the mac-use Chrome extension. The Linux server does not yet connect MCP browser requests to the extension, so browser automation is unavailable. Registering or loading the extension does not enable `browser_open`, `browser_snapshot`, `browser_act`, `browser_close`, or `browser_status` functionality. Do not use it expecting browser control.

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

`linux-use` ofrece un servidor MCP mediante la entrada y salida estándar. En una sesión X11, `doctor` informa sobre el entorno y `list_windows` devuelve los identificadores de las ventanas y de los procesos, además de sus títulos. El servidor anuncia los nombres de herramientas de mac-use por compatibilidad, pero las demás acciones nativas devuelven un error explícito de función no disponible. Wayland no es compatible.

El proyecto falla de forma segura en lugar de activar ventanas, inyectar entradas o afirmar que realiza comprobaciones de seguridad que no puede proporcionar. No están disponibles las capturas de pantalla, los árboles de Accesibilidad, la validación de tokens de estado, la detección de actividad humana, los clics, la escritura, las acciones semánticas ni las recomendaciones de Jev.

### Requisitos

- Linux con una sesión de escritorio X11 (`echo "$XDG_SESSION_TYPE"` debería mostrar `x11`)
- Python 3
- `wmctrl`
- Un cliente MCP: Distill, Codex, Claude Code o Grok Build

Las sesiones Wayland no son compatibles. `xdotool` no se utiliza para inyectar entradas; no hay acciones de entrada implementadas.

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

### 3. Comprobar la sesión de Linux

Llama a `doctor` para comprobar si hay una pantalla X11 disponible y si están instaladas las utilidades necesarias. Llama a `list_windows` para obtener las ventanas X11 detectadas. Los títulos y los identificadores de procesos pueden contener información privada; trata estos resultados con cuidado.

Actualmente solo funcionan `doctor` y `list_windows`. Estas herramientas se anuncian, pero devuelven un error de función no disponible: `screenshot`, `zoom`, `cursor_position`, `get_ui_tree`, `jev_decide`, `left_click`, `type`, `click_element` y las herramientas del navegador.

### Estado de la extensión de Chrome

La carpeta `extension/` contiene una extensión basada en la extensión de Chrome de mac-use. El servidor Linux todavía no conecta las solicitudes MCP del navegador con la extensión, por lo que la automatización del navegador no está disponible. Cargar o registrar la extensión no habilita las funciones `browser_open`, `browser_snapshot`, `browser_act`, `browser_close` ni `browser_status`. No la uses esperando poder controlar el navegador.

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

O `linux-use` oferece um servidor MCP por entrada e saída padrão. Em uma sessão X11, `doctor` informa o estado do ambiente e `list_windows` retorna os identificadores das janelas e dos processos, além dos títulos. O servidor anuncia os nomes das ferramentas do mac-use por compatibilidade, mas as demais ações nativas retornam um erro explícito de recurso não disponível. Wayland não é compatível.

O projeto falha de forma segura em vez de ativar janelas, injetar entradas ou alegar verificações de segurança que não consegue oferecer. Capturas de tela, árvores de Acessibilidade, validação de tokens de estado, detecção de atividade humana, cliques, digitação, ações semânticas e recomendações do Jev não estão disponíveis.

### Requisitos

- Linux com uma sessão gráfica X11 (`echo "$XDG_SESSION_TYPE"` deve exibir `x11`)
- Python 3
- `wmctrl`
- Um cliente MCP: Distill, Codex, Claude Code ou Grok Build

Sessões Wayland não são compatíveis. O `xdotool` não é usado para injetar entradas; nenhuma ação de entrada está implementada.

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

### 3. Confira a sessão Linux

Chame `doctor` para verificar se há uma tela X11 disponível e se os utilitários necessários estão instalados. Chame `list_windows` para listar as janelas X11 encontradas. Os títulos das janelas e os identificadores de processos podem conter informações privadas; trate esses dados com cuidado.

Atualmente, somente `doctor` e `list_windows` funcionam. Estas ferramentas são anunciadas, mas retornam erro de recurso não disponível: `screenshot`, `zoom`, `cursor_position`, `get_ui_tree`, `jev_decide`, `left_click`, `type`, `click_element` e as ferramentas do navegador.

### Estado da extensão do Chrome

A pasta `extension/` contém uma extensão baseada na extensão Chrome do mac-use. O servidor Linux ainda não conecta as solicitações MCP do navegador à extensão, portanto a automação do navegador não está disponível. Carregar ou registrar a extensão não habilita as funções `browser_open`, `browser_snapshot`, `browser_act`, `browser_close` ou `browser_status`. Não a utilize esperando conseguir controlar o navegador.

### Testes

Execute os testes disponíveis dentro da pasta `linux-use`:

```sh
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

Esses testes cobrem o comportamento MCP e as funções auxiliares, além da lógica de posse de abas da extensão com uma API Chrome simulada. Eles não verificam o funcionamento em um ambiente Linux X11 real nem a integração completa com o Chrome. Ainda é necessária uma verificação em Linux.
