# linux-use

A Linux MCP server inspired by mac-use. Native desktop controls require X11 and the corresponding utilities. Chrome tab automation is available through the separately registered extension. Jev decisions are available for owned Chrome tabs with structured controls. Native Linux windows offer X11 window metadata, optional AT-SPI menu/value actions and a screenshot-to-MCP-caller visual fallback; screenshots are not sent to Jev.

Read this in: [Español](README.es.md) · [Português](README.pt-BR.md)

### What works today

`linux-use` provides an MCP server over standard input and output. On an X11 session, `doctor` reports the environment and `list_windows` returns window IDs, process IDs, and titles. `screenshot`, `left_click`, `type`, `key`, and `scroll` are available for explicitly identified windows with a fresh state token; the parity groups below add app, desktop, input, session and recording controls. Wayland is not supported.

Background delivery is the default; explicit foreground input restores the previous app, while `bring_to_front` intentionally leaves the target active. Screenshot payloads include the target PID/window ID, geometry, and a state token derived from the target identity and captured pixels. The MCP caller can use the image for visual reasoning and submit a bounded, one-use `native_visual_propose`; a separate `native_visual_execute` call rechecks the exact target and pixels immediately before input. Proposals expire after two minutes, are limited to 32 pending items, and are consumed once. A state token binds the screenshot state but does not prove that a model examined the image or that an action is authorized. A complete native Accessibility element tree is unavailable; optional AT-SPI menu/value actions and `xprintidle` activity checks are described below. Jev advises on filtered Chrome controls and evaluates `verify_state` criteria from available text evidence; native screenshots are not sent to Jev.

### cua-driver parity tools

Inspired by [trycua/cua's cua-driver](https://github.com/trycua/cua/tree/main/libs/cua-driver), these groups extend the existing tools:

- **Apps:** `list_apps`, `launch_app`, `kill_app`, `bring_to_front`. Launch in the background; force-terminate only apps you launched. `bring_to_front` is an explicit, persistent activation, not an automatic input fallback.
- **Desktop:** `list_windows`, `get_accessibility_tree` (desktop app/window summary), `get_desktop_state` (full-display screenshot), `get_screen_size`, `get_cursor_position`, `move_cursor`. Window-scope cursor movement updates the agent overlay only; `scope: "desktop"` moves the real pointer.
- **Input:** `click`, `double_click`, `right_click`, `drag`, `scroll`, `type_text`, `press_key`, `hotkey`, and the existing input tools. Background is the default. Input tools that expose `delivery_mode` accept `"background"` or `"foreground"`; only an explicit `delivery_mode: "foreground"` temporarily activates the target and restores the prior app afterward. Mutations refuse with `human_activity` when the activity guard detects the user on the target; never retry around that refusal.
- **Menus and window frame:** `invoke_menu` walks an exact menu path and refuses missing, ambiguous or disabled items; `set_value` edits a supported control; `set_window_frame` changes position/size and verifies by readback.
- **Verification:** `verify_state` returns `pass`, `fail` or `unknown` for structured expectations and natural-language `expect.criteria` evaluated by Jev (1–8 non-empty strings, at most 200 characters each; for example, `expect: {"criteria": ["The Save button is visible"]}`). An unknown check is not proof of success.
- **Clipboard:** `clipboard_read` (text only with `include_text: true`) and `clipboard_write` (text, image or file). Track prior contents before overwriting and restore only if no user change intervened.
- **Configuration, permissions and health:** `get_config`, `set_config`, `check_permissions`, `health_report`, plus `doctor`. Configuration persists in `~/.linux-use/config.json`; use diagnostics to check platform requirements rather than assuming permission or utility availability.
- **Sessions and agent cursor:** `start_session`, `end_session`, `get_session_state`; `set_agent_cursor_enabled`, `set_agent_cursor_theme`, `set_agent_cursor_motion`, `get_agent_cursor_state`. The visual overlay is click-through, does not take focus and never moves the real pointer. Track the session identity and end sessions you start.
- **Trajectory recording and replay:** `start_recording`, `stop_recording`, `get_recording_state`, `replay_trajectory`. Each `turn-0000N/` stores `action.json`, `before.png` and `after.png`. Optional video uses `ffmpeg` X11 capture when installed. Stop recordings you start. Recordings can contain sensitive action arguments and images; protect their output. Replay repeats real actions and requires the same authorization and safety checks as live work.
- **Owned Chrome tab:** existing `browser_open`, `browser_snapshot`, `browser_act`, `browser_close`, `browser_status`, plus `browser_navigate`, `browser_click`, `browser_type` (`insert_text` or `keystrokes`), `browser_pointer` (hover, right-click, double-click, scroll, drag), `browser_dialog` (inspect, accept, dismiss, prompt text), `browser_set_input_files`, `browser_download`, and `page` (`execute_javascript`, `get_text`, `query_dom`, `click_element`, `insert_text`, `type_keystrokes`). They act only on the session's owned tab, never arbitrary user tabs. Use fresh refs after mutations and yield on user takeover.

The Chrome extension adds the `"debugger"` and `"downloads"` permissions for trusted input, dialogs, file selection and downloads. Reload the updated extension and approve any new permission prompt. Chrome may show its yellow **debugging** bar while `chrome.debugger` is attached during actions; an open dialog can keep it attached until handled. This does not grant ownership of other tabs.

Linux remains X11-only. `get_accessibility_tree` is a window summary, not an AT-SPI element tree; `invoke_menu` and `set_value` require an already installed Python `gi`/AT-SPI runtime. Clipboard needs `xclip` or `xsel` (images/files require `xclip`); the overlay depends on an available GUI runtime. Check `health_report` and `overlay_available`. The human-activity check needs `xprintidle`; diagnostics report it as skipped if absent, so do not assume protected shared-desktop input without it. Some X11 pointer actions move the real pointer even in background delivery; use an isolated desktop when that could interfere. Legacy `get_ui_tree`, `click_element`, `zoom` and `cursor_position` remain unsupported; use `get_cursor_position` for the new cursor query.

### Jev first

Jev is the main advantage: prefer text-only decisions over asking the session LLM to read screenshots. Use `jev_decide` for one step, a caller-managed observe/decide/act loop (`run_subtask` is available in mac-use, not this Linux tool catalog), and `verify_state` with natural-language `expect.criteria` to verify the result. For Chrome, use `jev_decide` with `browser: true` on the owned tab. Jev works with filtered labels, roles and goal/criteria text, never screenshots, field values or secrets. This is faster and cheaper than screenshot reasoning; labels and goals may still contain private information. Use screenshots only when structured evidence is missing or insufficient.

Configure `JEV_API_KEY`, `TYPESAFE_API_KEY` or `OPENROUTER_API_KEY`. Without a key, use the returned local candidates/evidence with the session LLM; do not claim a Jev check passed. A configured Jev error is not success or permission to act. Jev advises, never authorizes consequential actions.

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

`doctor`, `list_windows`, `restore_window`, `screenshot`, `left_click`, `type`, `key`, `scroll`, `native_visual_propose`, and `native_visual_execute` are available on X11 when their required utilities are installed. For native windows, the screenshot image goes to the MCP caller's visual model; the server does not send it to Jev. Proposals are separate from execution and are bound to the exact target and screenshot token, which the server revalidates before input. Use Jev first for owned Chrome controls and `verify_state` criteria over available text evidence; native visual proposals remain a screenshot fallback. Browser tools route through the registered Chrome native-messaging host and fail closed when disconnected.

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
