# linux-use

Linux sibling MCP server for mac-use. Python 3 standard library only. This implementation is deliberately **X11-only** and fail-closed; X11 command-line utilities do not offer the same non-activating, race-safe accessibility guarantees as macOS Accessibility APIs.

## MCP setup

1. Use an X11 desktop session (`echo "$XDG_SESSION_TYPE"` should report `x11`) and install `wmctrl` and `xdotool` from your distribution. Wayland is not supported.
2. Run `python3 /absolute/path/to/linux-use/server.py` as an MCP stdio server. For example, configure the client with command `python3` and argument `/absolute/path/to/linux-use/server.py`.
3. Call `initialize`, `tools/list`, and `tools/call` as usual. `doctor` reports environment support; `list_windows` uses `wmctrl -lpG` and returns PID/window ID candidates. Provide those IDs explicitly; there is no active-window fallback.

The server advertises the mac-use tool names and compatible JSON-RPC/MCP shapes. Currently only `doctor` and X11 `list_windows` are implemented as usable tools. `screenshot`, `zoom`, `cursor_position`, `get_ui_tree`, `jev_decide`, `left_click`, `type`, and `click_element` return an explicit unsupported error. In particular, state tokens are required for mutation calls but are not treated as proof of current state: the backend refuses every native mutation rather than pretending a token check is safe. UI-tree Accessibility, private-field filtering, screenshot acquisition, pointer/cursor observation, reliable human-activity arbitration, semantic Jev advice, and non-activating targeting are unsupported.

## Chrome extension / native messaging

The `extension/` directory contains the copied Manifest V3 extension. Load it unpacked from `chrome://extensions`, enable Developer mode, and copy its 32-character extension ID. Register the Linux native messaging manifest:

```sh
chmod +x /absolute/path/to/linux-use/server.py
python3 /absolute/path/to/linux-use/server.py install-chrome-host EXTENSION_ID
```

The installer writes `~/.config/google-chrome/NativeMessagingHosts/io.macuse.computer_use.json`, scoped to that extension origin. The host implements Chrome's length-prefixed native messaging transport, but rejects browser operations. The MCP server and host currently have no browser-session bridge; `browser_status` reports disconnected/unsupported, and `browser_open`, `browser_snapshot`, and `browser_act` are not functional. Do not interpret successful registration as browser-control support. `browser_close` releases no tab and never closes a tab.

## Limitations and safety

- Linux native backend supports X11 only; no Wayland emulation, window activation, guessed coordinates, or input injection.
- `wmctrl` window enumeration exposes titles and PIDs; treat output as potentially sensitive.
- `target_pid`/`target_window_id` and `expected_state_token` contracts are advertised for compatibility, but unsupported operations always fail closed.
- Chrome extension registration is provided for protocol/setup compatibility, not usable tab automation. Browser ownership/takeover guarantees cannot be claimed until the MCP-to-extension session bridge is implemented and tested.
- There is no claim of Linux-host verification from this macOS environment. Tests here exercise MCP framing/dispatch and helpers, not a real X11 desktop or Chrome installation.

## Tests

Run locally (macOS or Linux with Python 3):

```sh
cd /absolute/path/to/linux-use
python3 -m unittest -v
node --test ChromeExtensionTests.mjs
```

The Node tests cover the copied extension's tab ownership logic with a mocked Chrome API. They do not prove the Linux host/extension bridge works (it currently does not).
