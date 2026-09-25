import json
import os
import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server


class MCPTests(unittest.TestCase):
    window = {"window_id": 0x1234, "pid": 4321, "desktop": "0", "geometry": [10, 20, 800, 600], "title": "Editor"}

    def setUp(self):
        server.NATIVE_VISUAL_PROPOSALS.clear()
        server.NATIVE_VISUAL_OBSERVATIONS.clear()
        self.env = patch.dict(os.environ, {"DISPLAY": ":1", "XDG_SESSION_TYPE": "x11"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_initialize_and_tools_contract(self):
        init = server.handle({"jsonrpc":"2.0", "id":1, "method":"initialize"})
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
        tools = server.handle({"jsonrpc":"2.0", "id":2, "method":"tools/list"})["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, set(server.BASE_TOOLS))
        self.assertTrue(all("inputSchema" in t for t in tools))
        listed = {tool["name"]: tool for tool in tools}
        self.assertEqual(listed["native_visual_propose"]["inputSchema"]["required"],
                         ["target_pid", "target_window_id", "expected_state_token", "action", "decision"])
        self.assertEqual(listed["native_visual_execute"]["inputSchema"]["required"], ["proposal_id"])

    def test_second_native_host_does_not_replace_or_unlink_live_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Path(directory) / "bridge.sock"
            lock_path = bridge.with_suffix(".lock")
            bridge.write_text("live bridge sentinel")
            lock_file = open(lock_path, "a")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch.object(server, "BRIDGE_PATH", bridge), patch.object(
                    server.sys, "argv", ["server.py", "chrome-native-host", "chrome-extension://" + "a" * 32 + "/"]
                ):
                    server.native_host()
                self.assertEqual(bridge.read_text(), "live bridge sentinel")
            finally:
                lock_file.close()

    def test_browser_bridge_probe_rejects_stale_socket_path(self):
        with tempfile.TemporaryDirectory() as directory:
            stale_path = Path(directory) / "bridge.sock"
            stale_path.write_text("not a socket")
            with patch.object(server, "BRIDGE_PATH", stale_path):
                self.assertFalse(server.browser_bridge_available())

    def test_browser_tools_route_to_extension_and_status_reports_disconnected(self):
        with patch.object(server, "browser_request", return_value={"connected": True, "mode": "current_chrome_profile", "hasTab": False}) as request:
            result = json.loads(server.call_tool("browser_status", {})["content"][0]["text"])
        self.assertTrue(result["connected"])
        request.assert_called_once_with("browser_status", {})
        with patch.object(server, "browser_request", return_value={"url": "https://example.com/", "loading": True}) as request:
            result = json.loads(server.call_tool("browser_open", {"url": "https://example.com/"})["content"][0]["text"])
        self.assertTrue(result["loading"])
        request.assert_called_once_with("browser_open", {"url": "https://example.com/"})

    @staticmethod
    def browser_snapshot(token, ref, label="Open", text="Page state"):
        return {"state_token": token, "url": "https://example.test/", "title": "Example", "ready": True,
                "text": text, "elements": [{"ref": ref, "role": "button", "name": label, "value": "private field value",
                                                "type": "button", "disabled": False, "readOnly": False}]}

    @staticmethod
    def jev_response(choice):
        keys = {"BLOCKED", "DONE", "WAIT", "e0"}
        probabilities = {key: (1.0 if key == choice else 0.0) for key in keys}
        return {"answers": {"next": {"choice": choice, "probabilities": probabilities, "confidence": 0.95},
                             "complete": {"noul": 0.99}, "consequential": {"noul": 0.0},
                             "authorized": {"noul": 0.0}}}

    def test_jev_returns_filtered_consultative_proposal_and_reobserves(self):
        first = self.browser_snapshot("token-1", "ref-old", text="private body text")
        second = self.browser_snapshot("token-2", "ref-current", text="private body text")
        with patch.dict(os.environ, {"JEV_API_KEY": "configured"}), patch.object(
            server, "browser_request", side_effect=[first, second]
        ) as browser, patch.object(server, "jev_evaluate", return_value=self.jev_response("e0")) as evaluate:
            result = json.loads(server.call_tool("jev_decide", {"goal": "Open settings"})["content"][0]["text"])
        self.assertEqual(result["operation"], "click_element")
        self.assertEqual(result["ref"], "ref-current")
        self.assertEqual(result["state_token"], "token-2")
        self.assertEqual([call.args[0] for call in browser.call_args_list], ["browser_snapshot", "browser_snapshot"])
        payload = evaluate.call_args.args[0]
        serialized = json.dumps(payload)
        self.assertIn("Open", serialized)
        self.assertNotIn("private body text", serialized)
        self.assertNotIn("private field value", serialized)

    def test_jev_does_not_contact_model_for_missing_credentials_or_credential_goal(self):
        with patch.object(server, "jev_route", return_value=None), patch.object(server, "browser_request") as browser, patch.object(server, "jev_evaluate") as evaluate:
            with self.assertRaisesRegex(server.Unsupported, "No Jev credential"):
                server.call_tool("jev_decide", {"goal": "Open settings"})
            with patch.object(server, "jev_route", return_value=("secret", "https://example.test", "jev")):
                for goal in ("Enter my password", "Enter my PIN 1234", "Enter code 123  456"):
                    with self.subTest(goal=goal), self.assertRaisesRegex(server.Unsupported, "credentials"):
                        server.call_tool("jev_decide", {"goal": goal})
            browser.assert_not_called()
            evaluate.assert_not_called()

    def test_jev_rejects_invalid_response_without_proposal(self):
        first = self.browser_snapshot("token-1", "ref-1")
        with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
            server, "browser_request", return_value=first
        ), patch.object(server, "jev_evaluate", return_value={"answers": {}}):
            with self.assertRaisesRegex(server.Unsupported, "invalid decision"):
                server.call_tool("jev_decide", {"goal": "Open settings"})

    def test_jev_rejects_page_change_for_each_decision_including_done(self):
        for choice in ("e0", "WAIT", "BLOCKED", "DONE"):
            with self.subTest(choice=choice):
                first = self.browser_snapshot("token-1", "ref-1", text="before")
                second = self.browser_snapshot("token-2", "ref-2", text="after")
                with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
                    server, "browser_request", side_effect=[first, second]
                ) as browser, patch.object(server, "jev_evaluate", return_value=self.jev_response(choice)):
                    with self.assertRaisesRegex(server.Unsupported, "state changed"):
                        server.call_tool("jev_decide", {"goal": "Open settings"})
                    self.assertEqual(browser.call_count, 2)

    def test_jev_rejects_change_to_noneligible_field_value_and_filters_sensitive_labels(self):
        first = self.browser_snapshot("token-1", "ref-1")
        second = self.browser_snapshot("token-2", "ref-2")
        hidden_first = {"ref": "hidden-1", "role": "input", "name": "PIN", "value": "1111",
                        "type": "password", "disabled": False, "readOnly": False}
        hidden_second = {**hidden_first, "ref": "hidden-2", "value": "2222"}
        sensitive = {"ref": "code", "role": "button", "name": "Code 123  456", "value": "",
                     "type": "button", "disabled": False, "readOnly": False}
        first["elements"].extend([hidden_first, sensitive])
        second["elements"].extend([hidden_second, sensitive])
        with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
            server, "browser_request", side_effect=[first, second]
        ), patch.object(server, "jev_evaluate", return_value=self.jev_response("e0")) as evaluate:
            with self.assertRaisesRegex(server.Unsupported, "state changed"):
                server.call_tool("jev_decide", {"goal": "Open settings"})
        serialized = json.dumps(evaluate.call_args.args[0])
        self.assertNotIn("Code 123  456", serialized)
        self.assertNotIn("1111", serialized)
        self.assertNotIn("2222", serialized)

    def test_jev_projection_detects_values_swapped_between_identical_controls(self):
        first = self.browser_snapshot("token-1", "ref-1")
        second = self.browser_snapshot("token-2", "ref-2")
        first["elements"] = [
            {"role": "input", "name": "", "type": "text", "value": "alpha"},
            {"role": "input", "name": "", "type": "text", "value": "beta"},
        ]
        second["elements"] = [
            {"role": "input", "name": "", "type": "text", "value": "beta"},
            {"role": "input", "name": "", "type": "text", "value": "alpha"},
        ]
        self.assertNotEqual(server.jev_snapshot_projection(first), server.jev_snapshot_projection(second))

    def test_jev_done_is_blocked_without_independent_completion_verification(self):
        snapshot1 = self.browser_snapshot("token-1", "ref-1")
        snapshot2 = self.browser_snapshot("token-2", "ref-2")
        with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
            server, "browser_request", side_effect=[snapshot1, snapshot2]
        ), patch.object(server, "jev_evaluate", return_value=self.jev_response("DONE")):
            result = json.loads(server.call_tool("jev_decide", {"goal": "Open settings"})["content"][0]["text"])
        self.assertEqual(result["operation"], "BLOCKED")

    def test_doctor_and_unknown_method(self):
        with patch.object(server, "x11_available", return_value=True), patch.object(
            server.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"
        ):
            result = server.handle({"jsonrpc":"2.0", "id":3, "method":"tools/call", "params":{"name":"doctor", "arguments":{}}})
        capabilities = json.loads(result["result"]["content"][0]["text"])
        self.assertIn("browser_close", capabilities["unsupported"])
        self.assertIn("browser_status", capabilities["unsupported"])
        self.assertIn("screenshot", capabilities["supported_native_tools"])
        self.assertEqual(server.handle({"jsonrpc":"2.0", "id":4, "method":"unknown"})["error"]["code"], -32601)

    def test_doctor_reports_jev_only_when_bridge_credentials_and_owned_tab_are_present(self):
        cases = [
            ({"connected": True, "hasTab": True}, ("key", "endpoint", "model"), True),
            ({"connected": True, "hasTab": False}, ("key", "endpoint", "model"), False),
            ({"connected": True, "hasTab": True}, None, False),
            ({"connected": False, "hasTab": False}, ("key", "endpoint", "model"), False),
        ]
        for status, route, expected in cases:
            with self.subTest(status=status, route=route), patch.object(server, "browser_bridge_available", return_value=True), patch.object(
                server, "browser_request", return_value=status
            ), patch.object(server, "jev_route", return_value=route):
                result = json.loads(server.call_tool("doctor", {})["content"][0]["text"])
            self.assertEqual(result["decision_tool_prerequisites_met"], expected)
            self.assertTrue(result["jev_prerequisites"]["does_not_prove_model_request_success"])
            self.assertEqual("jev_decide" in result["unsupported"], not expected)

        with patch.object(server, "browser_bridge_available", return_value=False), patch.object(
            server, "browser_request"
        ) as request, patch.object(server, "jev_route", return_value=("key", "endpoint", "model")):
            result = json.loads(server.call_tool("doctor", {})["content"][0]["text"])
            request.assert_not_called()
        self.assertFalse(result["decision_tool_prerequisites_met"])

    def test_doctor_hides_capture_dependent_tools_when_screenshot_requirements_are_missing(self):
        for missing in (("xwd",), ("convert", "magick")):
            with self.subTest(missing=missing), patch.object(server, "x11_available", return_value=True), patch.object(
                server.shutil, "which", side_effect=lambda name, missing=missing: None if name in missing else f"/usr/bin/{name}"
            ):
                result = server.call_tool("doctor", {})
            capabilities = result["content"][0]["text"]
            reported = json.loads(capabilities)["supported_native_tools"]
            self.assertNotIn("screenshot", reported)
            self.assertNotIn("restore_window", reported)
            for name in ("left_click", "type", "key", "scroll"):
                self.assertNotIn(name, reported)

    def test_doctor_reports_no_native_tools_on_wayland_even_with_display_and_utilities(self):
        with patch.dict(os.environ, {"DISPLAY": ":1", "XDG_SESSION_TYPE": "wayland"}), patch.object(
            server.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"
        ):
            self.assertFalse(server.x11_available())
            capabilities = json.loads(server.call_tool("doctor", {})["content"][0]["text"])
        self.assertEqual(capabilities["supported_native_tools"], [])

    def test_restore_window_validates_target_token_then_returns_fresh_observation(self):
        token = server.state_token(self.window, b"pixels")
        with patch.object(server, "list_windows", return_value=[self.window]) as windows, patch.object(server, "screenshot_png", return_value=b"pixels"), patch.object(server, "run_cli") as execute:
            execute.side_effect = ["", "0x1234"]
            result = json.loads(server.call_tool("restore_window", {
                "target_pid":4321, "target_window_id":0x1234, "expected_state_token":token
            })["content"][0]["text"])
        self.assertEqual(execute.call_args_list[0].args, ("wmctrl", "-ia", "0x1234"))
        self.assertEqual(execute.call_args_list[1].args, ("xdotool", "getwindowfocus"))
        self.assertEqual(result["window_id"], 0x1234)
        self.assertTrue(result["restored"])
        self.assertEqual(result["state_token"], token)
        self.assertEqual(windows.call_count, 2)

    def test_restore_window_refuses_missing_or_stale_state_before_activation(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "screenshot_png", return_value=b"pixels"), patch.object(server, "run_cli") as execute:
            with self.assertRaisesRegex(ValueError, "expected_state_token"):
                server.call_tool("restore_window", {"target_pid":4321, "target_window_id":0x1234})
            with self.assertRaisesRegex(server.Unsupported, "stale"):
                server.call_tool("restore_window", {"target_pid":4321, "target_window_id":0x1234, "expected_state_token":"old"})
            execute.assert_not_called()

    def test_restore_window_reports_failed_focus_instead_of_claiming_success(self):
        token = server.state_token(self.window, b"pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "screenshot_png", return_value=b"pixels"), patch.object(
            server, "run_cli", side_effect=["", "0x5678"]
        ) as execute:
            with self.assertRaisesRegex(server.Unsupported, "did not focus"):
                server.call_tool("restore_window", {"target_pid":4321, "target_window_id":0x1234,
                                                    "expected_state_token":token})
            self.assertEqual(execute.call_args_list[0].args, ("wmctrl", "-ia", "0x1234"))
            self.assertEqual(execute.call_args_list[1].args, ("xdotool", "getwindowfocus"))

    def test_screenshot_returns_mcp_png_image_and_fresh_token(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "screenshot_png", return_value=b"fake-png"):
            result = server.call_tool("screenshot", {"target_pid":4321, "target_window_id":0x1234})
        self.assertEqual(result["content"][0], {"type":"image", "data":"ZmFrZS1wbmc=", "mimeType":"image/png"})
        metadata = json.loads(result["content"][1]["text"])
        self.assertEqual(metadata["state_token"], server.state_token(self.window, b"fake-png"))
        self.assertEqual(metadata["target_pid"], 4321)
        self.assertEqual(metadata["target_window_id"], 0x1234)
        self.assertEqual(metadata["geometry"], self.window["geometry"])
        self.assertIn("provenance", metadata["visual_proposal_contract"])

    def test_screenshot_refuses_window_identity_change_during_capture(self):
        other = {**self.window, "window_id": 0x5678, "pid": 9999}
        with patch.object(server, "list_windows", side_effect=[[self.window], [other]]), patch.object(
            server, "screenshot_png", return_value=b"captured"
        ):
            with self.assertRaisesRegex(server.Unsupported, "changed during screenshot"):
                server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234})

    def test_screenshot_token_can_be_reused_only_for_identical_pixels(self):
        token = server.state_token(self.window, b"same-pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "screenshot_png", return_value=b"same-pixels"
        ):
            result = server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234,
                                                     "expected_state_token": token})
            self.assertEqual(json.loads(result["content"][1]["text"])["state_token"], token)
            with self.assertRaisesRegex(server.Unsupported, "token is stale"):
                server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234,
                                                 "expected_state_token": "old-token"})

    def test_native_visual_proposal_is_one_use_and_revalidates_before_input(self):
        token = server.state_token(self.window, b"pixels")
        base = {"target_pid": 4321, "target_window_id": 0x1234, "expected_state_token": token}
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "screenshot_png", return_value=b"pixels"
        ), patch.object(server, "run_cli") as execute:
            server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234})
            proposal = server.call_tool("native_visual_propose", {**base, "action": "left_click",
                "coordinate": [10, 20], "decision": "Click the visible Open button"})
            proposal = json.loads(proposal["content"][0]["text"])
            self.assertFalse(proposal["executed"])
            self.assertEqual(proposal["target_window_id"], 0x1234)
            server.call_tool("native_visual_execute", {"proposal_id": proposal["proposal_id"]})
            self.assertEqual(execute.call_args_list[-1].args,
                             ("xdotool", "click", "--window", "0x1234", "1"))
            with self.assertRaisesRegex(server.Unsupported, "expired, unknown, or already consumed"):
                server.call_tool("native_visual_execute", {"proposal_id": proposal["proposal_id"]})

    def test_proposal_keeps_oldest_unexpired_observation_when_cache_is_full(self):
        screenshots = [f"pixels-{index}".encode() for index in range(32)]
        token = server.state_token(self.window, screenshots[0])
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "screenshot_png", side_effect=[*screenshots, screenshots[0]]
        ):
            for _ in screenshots:
                server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234})
            proposal = server.call_tool("native_visual_propose", {"target_pid": 4321,
                "target_window_id": 0x1234, "expected_state_token": token, "action": "key",
                "key": "Return", "decision": "Press Return"})
        self.assertTrue(json.loads(proposal["content"][0]["text"])["proposal_id"])

    def test_native_visual_proposal_requires_string_state_token(self):
        result = server.handle({"jsonrpc": "2.0", "id": 97, "method": "tools/call", "params": {
            "name": "native_visual_propose", "arguments": {"target_pid": 4321, "target_window_id": 0x1234,
            "expected_state_token": [], "action": "key", "key": "Return", "decision": "Press Return"}}})
        self.assertEqual(result["id"], 97)
        self.assertEqual(result["error"]["code"], -32602)
        self.assertIn("non-empty string", result["error"]["message"])

    def test_native_visual_proposal_requires_screenshot_from_this_session(self):
        token = server.state_token(self.window, b"pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "screenshot_png", return_value=b"pixels"
        ):
            with self.assertRaisesRegex(server.Unsupported, "recent screenshot issued by this server session"):
                server.call_tool("native_visual_propose", {"target_pid": 4321, "target_window_id": 0x1234,
                    "expected_state_token": token, "action": "key", "key": "Return", "decision": "Press Return"})

    def test_pending_proposal_capacity_does_not_evict_other_valid_proposals(self):
        token = server.state_token(self.window, b"pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "screenshot_png", return_value=b"pixels"
        ), patch.object(server, "run_cli"):
            server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234})
            base = {"target_pid": 4321, "target_window_id": 0x1234, "expected_state_token": token,
                    "action": "key", "key": "Return", "decision": "Press Return"}
            proposal_ids = [json.loads(server.call_tool("native_visual_propose", base)["content"][0]["text"])["proposal_id"]
                            for _ in range(32)]
            with self.assertRaisesRegex(server.Unsupported, "Too many pending"):
                server.call_tool("native_visual_propose", base)
            server.call_tool("native_visual_execute", {"proposal_id": proposal_ids[-1]})
            server.call_tool("native_visual_execute", {"proposal_id": proposal_ids[0]})

    def test_native_visual_proposal_rejects_stale_pixels(self):
        token = server.state_token(self.window, b"old-pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "screenshot_png", side_effect=[b"old-pixels", b"new-pixels"]
        ), patch.object(server, "run_cli") as execute:
            server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234})
            with self.assertRaisesRegex(server.Unsupported, "stale"):
                server.call_tool("native_visual_propose", {"target_pid": 4321, "target_window_id": 0x1234,
                    "expected_state_token": token, "action": "type", "text": "hello", "decision": "Type text"})
            execute.assert_not_called()

    def test_failed_native_visual_input_never_returns_success(self):
        token = server.state_token(self.window, b"pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "screenshot_png", return_value=b"pixels"
        ), patch.object(server, "run_cli", side_effect=server.Unsupported("xdotool input failed safely")) as execute:
            server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234})
            proposal = server.call_tool("native_visual_propose", {"target_pid": 4321, "target_window_id": 0x1234,
                "expected_state_token": token, "action": "type", "text": "hello", "decision": "Enter text"})
            proposal_id = json.loads(proposal["content"][0]["text"])["proposal_id"]
            with self.assertRaisesRegex(server.Unsupported, "input failed"):
                server.call_tool("native_visual_execute", {"proposal_id": proposal_id})
            execute.assert_called_once()

    def test_screenshot_requires_png_converter(self):
        with patch.object(server, "run_cli_bytes", return_value=b"fake-xwd"), patch.object(server.shutil, "which", return_value=None):
            with self.assertRaisesRegex(server.Unsupported, "PNG conversion requires ImageMagick"):
                server.screenshot_png(0x1234)

    def test_valid_actions_dispatch_only_after_target_validation(self):
        token = server.state_token(self.window, b"pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "screenshot_png", return_value=b"pixels"), patch.object(server, "run_cli") as execute:
            for name, args in (("left_click", {"coordinate":[10, 20]}), ("type", {"text":"hello"}), ("key", {"key":"Return"}), ("scroll", {"coordinate":[10, 20], "delta_y":2})):
                server.call_tool(name, {**args, "target_pid":4321, "target_window_id":0x1234, "expected_state_token":token})
            self.assertEqual(execute.call_count, 6)
            self.assertEqual(execute.call_args_list[0].args, ("xdotool", "mousemove", "--sync", "--window", "0x1234", "10", "20"))
            self.assertEqual(execute.call_args_list[1].args, ("xdotool", "click", "--window", "0x1234", "1"))
            self.assertEqual(execute.call_args_list[2].args[-1], "hello")
            self.assertEqual(execute.call_args_list[3].args[-1], "Return")
            self.assertEqual(execute.call_args_list[4].args[:6], ("xdotool", "mousemove", "--sync", "--window", "0x1234", "10"))
            self.assertIn("--window", execute.call_args_list[5].args)

    def test_visual_change_makes_screenshot_token_stale_before_input(self):
        token = server.state_token(self.window, b"old-pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "screenshot_png", return_value=b"new-pixels"), patch.object(server, "run_cli") as execute:
            with self.assertRaisesRegex(server.Unsupported, "stale"):
                server.call_tool("left_click", {"target_pid":4321, "target_window_id":0x1234,
                                                   "expected_state_token":token, "coordinate":[10, 20]})
            execute.assert_not_called()

    def test_failed_pre_input_recapture_prevents_dispatch(self):
        token = server.state_token(self.window, b"pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "screenshot_png", side_effect=[b"pixels", server.Unsupported("capture failed")]
        ), patch.object(server, "run_cli") as execute:
            with self.assertRaisesRegex(server.Unsupported, "capture failed"):
                server.call_tool("type", {"target_pid":4321, "target_window_id":0x1234,
                                            "expected_state_token":token, "text":"hello"})
            execute.assert_not_called()

    def test_input_actions_require_non_empty_state_token(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli") as execute:
            for token in (None, "", "   "):
                args = {"target_pid":4321, "target_window_id":0x1234, "text":"x"}
                if token is not None: args["expected_state_token"] = token
                with self.assertRaisesRegex(ValueError, "non-empty expected_state_token"):
                    server.call_tool("type", args)
            execute.assert_not_called()

    def test_stale_or_missing_target_refuses_dispatch(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "screenshot_png", return_value=b"pixels"), patch.object(server, "run_cli") as execute:
            with self.assertRaisesRegex(server.Unsupported, "stale"):
                server.call_tool("type", {"target_pid":4321, "target_window_id":0x1234, "expected_state_token":"old", "text":"x"})
            with self.assertRaisesRegex(server.Unsupported, "missing or ambiguous"):
                server.call_tool("type", {"target_pid":4321, "target_window_id":9, "expected_state_token":"old", "text":"x"})
            execute.assert_not_called()

    def test_bad_coordinates_and_key_input_refuse_dispatch(self):
        token = server.state_token(self.window, b"pixels")
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "screenshot_png", return_value=b"pixels"), patch.object(server, "run_cli") as execute:
            base = {"target_pid":4321, "target_window_id":0x1234, "expected_state_token":token}
            for name, args in (("left_click", {"coordinate":[800, 1]}), ("left_click", {"coordinate":[1.5, 2]}), ("key", {"key":"bad key"}), ("type", {"text":""})):
                with self.assertRaises(ValueError): server.call_tool(name, {**base, **args})
            execute.assert_not_called()

    def test_missing_screenshot_utility_reports_precise_error(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server.shutil, "which", return_value=None):
            with self.assertRaisesRegex(server.Unsupported, "'xwd'.*not installed"):
                server.call_tool("screenshot", {"target_pid":4321, "target_window_id":0x1234})

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
        with patch.object(server, "run_cli", return_value=row):
            windows = server.list_windows()
        self.assertEqual(windows[0]["window_id"], 0x01234567)
        self.assertEqual(windows[0]["title"], "Editor - document.txt")


if __name__ == "__main__": unittest.main()
