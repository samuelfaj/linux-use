import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server

class MCPTests(unittest.TestCase):
    def test_initialize_and_tools_contract(self):
        init = server.handle({"jsonrpc":"2.0", "id":1, "method":"initialize"})
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
        tools = server.handle({"jsonrpc":"2.0", "id":2, "method":"tools/list"})["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, set(server.BASE_TOOLS))
        self.assertTrue(all("inputSchema" in t for t in tools))

    def test_stdio_calls_and_unknown_method(self):
        result = server.handle({"jsonrpc":"2.0", "id":3, "method":"tools/call", "params":{"name":"doctor", "arguments":{}}})
        self.assertIn('"x11_only": true', result["result"]["content"][0]["text"])
        self.assertEqual(server.handle({"jsonrpc":"2.0", "id":4, "method":"unknown"})["error"]["code"], -32601)

    def test_native_actions_fail_closed_and_require_explicit_target_token(self):
        err = server.handle({"jsonrpc":"2.0", "id":1, "method":"tools/call", "params":{"name":"type", "arguments":{"target_pid":1,"target_window_id":2,"text":"x"}}})
        self.assertEqual(err["error"]["code"], -32602)
        reply = server.handle({"jsonrpc":"2.0", "id":2, "method":"tools/call", "params":{"name":"type", "arguments":{"target_pid":1,"target_window_id":2,"expected_state_token":"old","text":"x"}}})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("no action was performed", reply["result"]["content"][0]["text"])

    def test_list_windows_refuses_wayland_or_missing_display(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(server.Unsupported, "X11 DISPLAY"):
                server.list_windows()

    def test_chrome_registration_launches_native_host_mode(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(server.install_host("a" * 32, home))
            registration = json.loads(path.read_text())
            self.assertEqual(registration["name"], server.HOST)
            self.assertEqual(registration["allowed_origins"], ["chrome-extension://" + "a" * 32 + "/"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            launcher = Path(registration["path"])
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o700)
            self.assertIn("chrome-native-host \"$@\"", launcher.read_text())
        with self.assertRaises(ValueError): server.install_host("bad")

    def test_list_windows_discards_wmctrl_hostname_column(self):
        row = "0x01234567  0  4321  10  20  800  600  workstation.example  Editor - document.txt"
        with patch.dict(os.environ, {"DISPLAY": ":1"}), patch.object(server, "run_cli", return_value=row):
            windows = server.list_windows()
        self.assertEqual(windows[0]["window_id"], 0x01234567)
        self.assertEqual(windows[0]["title"], "Editor - document.txt")

if __name__ == "__main__": unittest.main()
