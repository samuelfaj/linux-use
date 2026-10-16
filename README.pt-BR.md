# linux-use (Português do Brasil)

Read this in: [English](README.md) · [Español](README.es.md)

### O que funciona atualmente

O `linux-use` oferece um servidor MCP por entrada e saída padrão. Em uma sessão X11, `doctor` informa o estado do ambiente e `list_windows` retorna IDs de janela, PID e títulos. Também são suportadas capturas PNG e ações de clique esquerdo, digitação, teclas e rolagem em uma janela identificada explicitamente e com token de estado atual. Wayland não é compatível.

As ações de entrada revalidam a identidade e o estado visual da janela antes do envio. As capturas exigem `xwd` e ImageMagick; as ações de entrada exigem `xdotool`. Não há uma árvore completa de elementos de Acessibilidade nativa; as ações opcionais de menu/valor AT-SPI estão descritas abaixo. O Jev avalia controles filtrados da aba própria do Chrome e critérios de `verify_state` com evidência textual disponível; ele não recebe capturas nativas. A automação do navegador funciona pela extensão registrada separadamente.

### Ferramentas de paridade com cua-driver

Inspirados no [cua-driver do trycua/cua](https://github.com/trycua/cua/tree/main/libs/cua-driver), estes grupos ampliam as ferramentas existentes:

- **Aplicativos:** `list_apps`, `launch_app`, `kill_app`, `bring_to_front`. Inicie em segundo plano; force o encerramento somente de aplicativos que você iniciou. `bring_to_front` ativa de forma explícita e persistente; não é uma alternativa automática para enviar entrada.
- **Desktop:** `list_windows`, `get_accessibility_tree` (resumo de aplicativos e janelas), `get_desktop_state` (captura da tela inteira), `get_screen_size`, `get_cursor_position`, `move_cursor`. O movimento com escopo de janela só atualiza a sobreposição do agente; `scope: "desktop"` move o ponteiro real.
- **Entrada:** `click`, `double_click`, `right_click`, `drag`, `scroll`, `type_text`, `press_key`, `hotkey` e as ferramentas de entrada existentes. Segundo plano é o padrão. As ferramentas que expõem `delivery_mode` aceitam `"background"` ou `"foreground"`; somente um `delivery_mode: "foreground"` explícito ativa temporariamente o alvo e restaura o aplicativo anterior depois. As mutações são recusadas com `human_activity` quando a proteção detecta o usuário no alvo; não tente contornar a recusa.
- **Menus e geometria da janela:** `invoke_menu` percorre um caminho exato de menu e recusa itens ausentes, ambíguos ou desabilitados; `set_value` altera um controle compatível; `set_window_frame` muda posição e tamanho e verifica por leitura posterior.
- **Verificação:** `verify_state` retorna `pass`, `fail` ou `unknown` para expectativas estruturadas e verificações em linguagem natural com `expect.criteria`, avaliadas pelo Jev (de 1 a 8 strings não vazias, com até 200 caracteres cada; por exemplo, `expect: {"criteria": ["O botão Salvar está visível"]}`). Um resultado desconhecido não comprova sucesso.
- **Área de transferência:** `clipboard_read` (texto somente com `include_text: true`) e `clipboard_write` (texto, imagem ou arquivo). Registre o conteúdo anterior antes de sobrescrever e restaure somente se o usuário não tiver alterado o conteúdo desde então.
- **Configuração, permissões e diagnóstico:** `get_config`, `set_config`, `check_permissions`, `health_report` e `doctor`. A configuração fica em `~/.linux-use/config.json`; use o diagnóstico para verificar os requisitos da plataforma em vez de presumir que permissões ou utilitários estão disponíveis.
- **Sessões e cursor do agente:** `start_session`, `end_session`, `get_session_state`; `set_agent_cursor_enabled`, `set_agent_cursor_theme`, `set_agent_cursor_motion`, `get_agent_cursor_state`. A sobreposição visual deixa os cliques passarem, não toma o foco e nunca move o ponteiro real. Registre a identidade da sessão e encerre as sessões que iniciar.
- **Gravação e reprodução de trajetórias:** `start_recording`, `stop_recording`, `get_recording_state`, `replay_trajectory`. Cada `turn-0000N/` guarda `action.json`, `before.png` e `after.png`. O vídeo opcional usa captura X11 com `ffmpeg` quando instalado. Pare as gravações que iniciar. Elas podem conter argumentos de ações e imagens sensíveis; proteja os arquivos. A reprodução repete ações reais e exige a mesma autorização e as mesmas verificações da execução ao vivo.
- **Aba própria do Chrome:** as ferramentas existentes `browser_open`, `browser_snapshot`, `browser_act`, `browser_close`, `browser_status`, mais `browser_navigate`, `browser_click`, `browser_type` (`insert_text` ou `keystrokes`), `browser_pointer` (passar o ponteiro, clique direito, clique duplo, rolagem, arraste), `browser_dialog` (inspecionar, aceitar, dispensar, texto de um diálogo de entrada), `browser_set_input_files`, `browser_download` e `page` (`execute_javascript`, `get_text`, `query_dom`, `click_element`, `insert_text`, `type_keystrokes`). Atuam somente na aba da sessão, nunca em abas arbitrárias do usuário. Use referências novas após mutações e ceda o controle quando o usuário assumir.

A extensão do Chrome adiciona as permissões `"debugger"` e `"downloads"` para entrada confiável, diálogos, seleção de arquivos e downloads. Recarregue a extensão atualizada e aprove os novos pedidos de permissão. O Chrome pode mostrar sua barra amarela de **depuração** enquanto `chrome.debugger` estiver conectado durante as ações; um diálogo aberto pode manter a conexão até ser tratado. Isso não concede controle sobre outras abas.

O Linux continua limitado ao X11. `get_accessibility_tree` resume janelas, não é uma árvore de elementos AT-SPI; `invoke_menu` e `set_value` exigem um ambiente Python `gi`/AT-SPI já instalado. A área de transferência precisa de `xclip` ou `xsel` (imagens/arquivos exigem `xclip`); a sobreposição depende de um ambiente gráfico disponível. Confira `health_report` e `overlay_available`. A detecção de atividade precisa de `xprintidle`; o diagnóstico informa que ela é omitida se estiver ausente, então não presuma entrada protegida em desktop compartilhado sem ele. Algumas ações de ponteiro X11 movem o ponteiro real mesmo em segundo plano; use um desktop isolado se houver risco de interferência. As ferramentas antigas `get_ui_tree`, `click_element`, `zoom` e `cursor_position` continuam sem suporte; use `get_cursor_position` para a nova consulta do cursor.

### Jev primeiro

O Jev é a principal vantagem: prefira decisões só com texto em vez de pedir ao LLM da sessão que leia capturas. Use `jev_decide` para um passo, um ciclo de observação/decisão/ação gerenciado pelo cliente (`run_subtask` está disponível no mac-use, não neste catálogo do Linux), e `verify_state` com `expect.criteria` em linguagem natural para verificar o resultado. Para o Chrome, use `jev_decide` com `browser: true` na aba própria. O Jev recebe rótulos, papéis e texto de objetivo/critérios filtrados, nunca capturas, valores de campos ou segredos. Isso é mais rápido e barato que raciocinar sobre capturas; rótulos e objetivos ainda podem conter informações privadas. Use capturas somente quando faltar evidência estruturada ou ela for insuficiente.

Configure `JEV_API_KEY`, `TYPESAFE_API_KEY` ou `OPENROUTER_API_KEY`. Sem chave, use os candidatos ou as evidências locais retornados com o LLM da sessão; não afirme que uma verificação do Jev passou. Um erro do Jev configurado não significa sucesso nem permissão para agir. O Jev aconselha, nunca autoriza ações com consequências.

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

Funcionam `doctor`, `list_windows`, `restore_window`, `screenshot`, `left_click`, `type`, `key`, `scroll`, `native_visual_propose` e `native_visual_execute` quando os utilitários necessários estão instalados. A imagem é entregue ao modelo visual do cliente MCP, não ao Jev. A proposta fica vinculada ao alvo e ao token; a execução revalida o estado e consome a proposta uma única vez. Priorize o Jev para controles da aba própria do Chrome e critérios de `verify_state` com evidência textual; as propostas visuais nativas ficam como alternativa com capturas. `zoom`, `cursor_position`, `get_ui_tree` e `click_element` continuam indisponíveis.

### Estado da extensão do Chrome

A pasta `extension/` contém a extensão do Chrome. Registre o host com `python3 server.py install-chrome-host EXTENSION_ID`; a extensão se conecta sozinha e reconecta automaticamente a cada 30 segundos se cair (um clique no ícone tenta de novo na hora). O servidor MCP encaminha as solicitações por um socket Unix local com permissões restritas. A automação do navegador é independente dos controles de desktop X11. As abas próprias são abertas em segundo plano; a extensão para de agir quando detecta que o usuário selecionou uma aba. O Chrome não torna atômicas a verificação de atividade e a remoção da aba, então uma seleção simultânea à remoção ainda pode resultar no fechamento.

### Testes

Execute os testes disponíveis dentro da pasta `linux-use`:

```sh
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

Esses testes cobrem o comportamento MCP e as funções auxiliares, além da lógica de posse de abas da extensão com uma API Chrome simulada. Eles não verificam o funcionamento em um ambiente Linux X11 real nem a integração completa com o Chrome. Ainda é necessária uma verificação em Linux.

### Estabilização do navegador, selects e proteções do Jev

O `browser_act` agora espera a página estabilizar (fetch/XHR iniciados pela ação mais 150 ms sem mudanças no DOM, com limite de 2 s, ou 10 s enquanto houver requisições iniciadas pela ação pendentes) e retorna um **snapshot novo** com `settle_ms` e `settled`; as referências do snapshot anterior ficam obsoletas. Os snapshots listam as opções de `<select>` (até 50, com o valor selecionado) e `fill` em um select aceita o valor ou o texto de uma opção. Elementos cobertos são marcados com `covered: true` e o `browser_act` os recusa (`element is covered`). Regiões ao vivo (`role=status`/`alert`, `aria-live`) permanecem no snapshot com `offscreen: true` quando estão fora da área visível.

O `jev_decide` retém controles que nomeiam efeitos de excluir, enviar, comprar ou fechar, a menos que estejam no parâmetro opcional `allowed_risks` (somente para categorias que o objetivo autoriza). Um alvo arriscado escolhido conta como material. Ele retorna `margin` (probabilidade escolhida menos a melhor alternativa), e `min_confidence` / `min_margin` opcionais (0..1) transformam decisões mais fracas em `NEEDS_AGENT`.

### Agradecimentos

Obrigado a [shhivv](https://github.com/shhivv) e [arc-cua](https://github.com/shhivv/arc-cua), a inspiração para a estabilização do navegador, o suporte a selects e as proteções `allowed_risks`, `min_confidence` e `min_margin`.
