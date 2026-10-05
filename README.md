# linux-use

A Linux MCP server inspired by mac-use. Native desktop controls require X11 and the corresponding utilities. Chrome tab automation is available through the separately registered extension. Jev decisions are available for owned Chrome tabs with structured controls. Native Linux windows use a screenshot-to-MCP-caller visual-model fallback; screenshots are not sent to Jev, and Linux Accessibility is not implemented.

Read this in: [Español](README.es.md) · [Português](README.pt-BR.md)

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

The extension connects to the Linux MCP server through Chrome native messaging and a mode-0600 Unix socket under `~/.local/share/linux-use`. Register it using `python3 server.py install-chrome-host EXTENSION_ID`, and the extension connects by itself, reconnecting automatically every 30 seconds if the link drops (clicking the icon retries at once). Browser automation is independent of the X11-only native desktop backend. Owned tabs are created inactive; the extension refuses reads or actions after human takeover and only best-effort closes tabs that remain inactive. Chrome cannot make the activity check and tab removal atomic, so a selection racing with removal may still be closed.

### Tests

Run the available tests from the `linux-use` folder:

```sh
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

These tests cover MCP/helper behavior and the extension's mocked tab-ownership logic. They do not verify operation on a real Linux X11 desktop or end-to-end Chrome integration. Linux desktop verification is still required.

### Browser settling, selects and Jev guards

`browser_act` now waits for the page to settle (page fetch/XHR started by the action plus 150 ms of DOM quiet, capped at 2 s, or 10 s while action-started requests are pending) and returns a **fresh snapshot** with `settle_ms` and `settled`; refs from the earlier snapshot are stale. Snapshots list `<select>` options (up to 50, with the selected value), and `fill` on a select accepts an option value or text. Covered elements are marked `covered: true` and `browser_act` refuses them (`element is covered`). Live regions (`role=status`/`alert`, `aria-live`) stay in the snapshot with `offscreen: true` when outside the viewport.

`jev_decide` withholds controls naming delete, send, purchase or close effects unless listed in the optional `allowed_risks` (only for categories the goal authorizes). A chosen risky target counts as material. It returns `margin` (chosen probability minus the best runner-up), and optional `min_confidence` / `min_margin` (0..1) turn weaker decisions into `NEEDS_AGENT`.

### Acknowledgements

Thanks to [shhivv](https://github.com/shhivv) and [arc-cua](https://github.com/shhivv/arc-cua), the inspiration for browser settling, select support, and the `allowed_risks`, `min_confidence` and `min_margin` guards.
