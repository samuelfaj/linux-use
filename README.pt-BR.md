# linux-use (Português do Brasil)

Read this in: [English](README.md) · [Español](README.es.md)

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
