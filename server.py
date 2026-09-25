#!/usr/bin/env python3
"""Linux X11-only mac-use sibling MCP stdio server; intentionally fail-closed."""
import json
import os
import re
import shutil
import shlex
import struct
import subprocess
import sys
from pathlib import Path

HOST = "io.macuse.computer_use"
BASE_TOOLS = ["screenshot", "zoom", "cursor_position", "list_windows", "get_ui_tree", "jev_decide", "doctor", "left_click", "type", "click_element", "browser_open", "browser_snapshot", "browser_act", "browser_close", "browser_status"]

class Unsupported(RuntimeError):
    pass


def run_cli(*args):
    """Run a system utility without a shell and return stdout; errors fail closed."""
    executable = shutil.which(args[0])
    if not executable:
        raise Unsupported(f"Unsupported/unavailable: required system utility {args[0]!r} is not installed.")
    try:
        return subprocess.run([executable, *args[1:]], check=True, text=True, capture_output=True, timeout=4).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise Unsupported(f"X11 utility failed safely: {exc}") from exc


def x11_available():
    return bool(os.environ.get("DISPLAY")) and shutil.which("wmctrl") is not None and shutil.which("xdotool") is not None


def list_windows():
    if not os.environ.get("DISPLAY"):
        raise Unsupported("Linux native controls require an X11 DISPLAY; Wayland is unsupported.")
    lines = run_cli("wmctrl", "-lpG").splitlines()
    windows = []
    for line in lines:
        fields = line.split(None, 8)
        if len(fields) < 9:
            continue
        wid, desktop, pid, x, y, width, height, _host, title = fields
        windows.append({"window_id": int(wid, 16), "pid": int(pid), "desktop": desktop,
                        "geometry": [int(x), int(y), int(width), int(height)], "title": title})
    return windows


def schemas():
    targeting = {"target_app": {"type": "string"}, "target_pid": {"type": "integer"},
                 "target_window_id": {"type": "integer"}, "expected_state_token": {"type": "string"}}
    props = {
        "screenshot": targeting, "zoom": {**targeting, "region": {"type": "array"}},
        "cursor_position": targeting, "list_windows": {"bundle_id": {"type": "string"}},
        "get_ui_tree": targeting, "jev_decide": {**targeting, "goal": {"type": "string"}},
        "doctor": targeting, "left_click": {**targeting, "coordinate": {"type": "array"}},
        "type": {**targeting, "text": {"type": "string"}},
        "click_element": {**targeting, "role": {"type": "string"}, "label": {"type": "string"}},
        "browser_open": {"url": {"type": "string"}},
        "browser_act": {"action": {"type": "string", "enum": ["click", "fill", "type", "scroll"]}, "ref": {"type": "string"}, "text": {"type": "string"}, "delta_x": {"type": "number"}, "delta_y": {"type": "number"}},
    }
    required = {"screenshot": ["target_pid", "target_window_id"], "zoom": ["target_pid", "target_window_id", "region"],
        "cursor_position": ["target_pid", "target_window_id"], "get_ui_tree": ["target_pid", "target_window_id"],
        "jev_decide": ["target_pid", "target_window_id", "goal"], "doctor": [],
        "left_click": ["target_pid", "target_window_id", "expected_state_token", "coordinate"],
        "type": ["target_pid", "target_window_id", "expected_state_token", "text"],
        "click_element": ["target_pid", "target_window_id", "expected_state_token", "role", "label"],
        "browser_open": ["url"], "browser_act": ["action"]}
    out = []
    for name in BASE_TOOLS:
        out.append({"name": name, "description": descriptions(name), "inputSchema": {
            "type": "object", "properties": props.get(name, {}), "required": required.get(name, []), "additionalProperties": False}})
    return out


def descriptions(name):
    if name == "list_windows": return "List X11 windows using wmctrl. Linux session must be X11, not Wayland."
    if name == "doctor": return "Report X11 and system utility availability; no permissions are changed."
    if name.startswith("browser_"): return "Chrome automation requires the separately registered extension; unsupported actions fail closed on this Linux server."
    if name == "jev_decide": return "Semantic advice is unsupported; no model request or input is made."
    return "Not implemented safely on Linux/X11; reports unsupported rather than activating, guessing, or posting input."


def text_result(value, error=False):
    return {"content": [{"type": "text", "text": value if isinstance(value, str) else json.dumps(value, sort_keys=True)}], "isError": error}


def call_tool(name, args):
    if name == "doctor":
        return text_result({"platform": sys.platform, "display": bool(os.environ.get("DISPLAY")), "session_type": os.environ.get("XDG_SESSION_TYPE"),
                            "x11_only": True, "wmctrl": shutil.which("wmctrl"), "xdotool": shutil.which("xdotool"),
                            "supported_native_tools": ["list_windows"] if x11_available() else [],
                            "unsupported": [x for x in BASE_TOOLS if x not in ("doctor", "list_windows")]})
    if name == "list_windows":
        if args.get("bundle_id"):
            raise Unsupported("bundle_id filtering is not available on Linux; use target PID/window ID from the returned list.")
        return text_result(list_windows())
    if name == "browser_status":
        return text_result({"connected": False, "mode": "unsupported", "reason": "The Linux Chrome extension/native-host bridge is not implemented; no browser control is attempted."})
    if name == "browser_close":
        return text_result({"closed": False, "released": False, "reason": "No Linux browser session is supported."})
    if name in BASE_TOOLS:
        # Validate caller-supplied identity, but never use X11 focus switching or guessed coordinates.
        if name in ("left_click", "type", "click_element", "screenshot", "zoom", "get_ui_tree", "jev_decide", "cursor_position"):
            if not isinstance(args.get("target_pid"), int) or not isinstance(args.get("target_window_id"), int):
                raise ValueError("target_pid and target_window_id are required; no implicit active-window targeting is allowed.")
            if name in ("left_click", "type", "click_element") and not args.get("expected_state_token"):
                raise ValueError("expected_state_token is required; mutation refused.")
        raise Unsupported(f"{name} is explicitly unsupported on this backend. X11 CLI tools cannot provide the required non-activating, race-safe target/state-token guarantees; no action was performed.")
    raise ValueError(f"unknown tool: {name}")


def handle(message):
    ident = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32600, "message": "invalid request"}}
    if method.startswith("notifications/") or ident is None:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "linux-use", "version": "1.0.0"}, "instructions": "Linux backend is X11-only. Only list_windows and doctor are implemented; native UI actions and Chrome automation fail closed as unsupported. No window is activated and no input is posted."}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": ident, "result": {"tools": schemas()}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = call_tool(params.get("name", ""), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": ident, "result": result}
        except ValueError as exc:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": str(exc)}}
        except Unsupported as exc:
            return {"jsonrpc": "2.0", "id": ident, "result": text_result(str(exc), True)}
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"method not found: {method}"}}


def install_host(extension_id, home=None):
    if not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("Expected a 32-character Chrome extension ID containing letters a-p.")
    home = Path(home or Path.home())
    manifest_dir = home / ".config/google-chrome/NativeMessagingHosts"
    host_dir = home / ".local/lib/linux-use"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    host_dir.mkdir(parents=True, exist_ok=True)
    host = host_dir / "chrome-native-host"
    script = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -u " + shlex.quote(str(Path(__file__).resolve())) + " chrome-native-host \"$@\"\n"
    host.write_text(script, encoding="utf8")
    host.chmod(0o700)
    manifest = {"name": HOST, "description": "linux-use Chrome native messaging host", "path": str(host), "type": "stdio", "allowed_origins": [f"chrome-extension://{extension_id}/"]}
    path = manifest_dir / f"{HOST}.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf8")
    path.chmod(0o600)
    return str(path)


def native_host():
    """Chrome native messaging framed protocol; browser requests are rejected safely."""
    while True:
        head = sys.stdin.buffer.read(4)
        if not head: return
        if len(head) != 4: return
        size = struct.unpack("<I", head)[0]
        if size < 1 or size > 1_048_576: return
        data = sys.stdin.buffer.read(size)
        if len(data) != size: return
        try:
            req = json.loads(data)
            response = {"id": req.get("id"), "ok": False, "error": "Linux browser automation backend is unsupported; no tab was accessed."}
        except Exception:
            response = {"id": None, "ok": False, "error": "Invalid native messaging request."}
        encoded = json.dumps(response, separators=(",", ":")).encode()
        sys.stdout.buffer.write(struct.pack("<I", len(encoded)) + encoded)
        sys.stdout.buffer.flush()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "install-chrome-host":
        if len(sys.argv) != 3:
            print("Usage: server.py install-chrome-host EXTENSION_ID", file=sys.stderr); return 2
        try: print(install_host(sys.argv[2])); return 0
        except (ValueError, OSError) as exc: print(str(exc), file=sys.stderr); return 2
    if len(sys.argv) > 1 and sys.argv[1] == "chrome-native-host":
        native_host(); return 0
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = handle(message)
        except Exception:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n"); sys.stdout.flush()

if __name__ == "__main__":
    main()
