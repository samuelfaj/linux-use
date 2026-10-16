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
        which = server.shutil.which
        idle_check = patch.object(server.shutil, "which", side_effect=lambda name: None if name == "xprintidle" else which(name))
        idle_check.start()
        self.addCleanup(idle_check.stop)
        config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(config_dir.cleanup)
        config_path = Path(config_dir.name) / "config.json"
        config_path.write_text(json.dumps({"allow_without_activity_check": True}))
        config_patch = patch.object(server.cua_native, "CONFIG_PATH", config_path)
        config_patch.start()
        self.addCleanup(config_patch.stop)

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

    def test_risk_classify_uses_whole_words_and_phrases(self):
        self.assertEqual(server.risk_classify("Empty   Trash"), "delete")
        self.assertIsNone(server.risk_classify("Closet"))
        self.assertEqual(server.risk_classify("Sign out"), "close")
        self.assertEqual(server.risk_categories("Remove and send"), {"delete", "send"})

    def test_jev_withholds_risky_controls_unless_allowed(self):
        def decide(allowed):
            first = self.browser_snapshot("token-1", "ref-1", label="Delete draft")
            second = self.browser_snapshot("token-2", "ref-2", label="Delete draft")
            args = {"goal": "Delete the draft"} | ({"allowed_risks": allowed} if allowed is not None else {})
            response = self.jev_response("e0")
            response["answers"]["consequential"]["noul"] = 0.9
            response["answers"]["authorized"]["noul"] = 0.95
            with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
                server, "browser_request", side_effect=[first, second]
            ), patch.object(server, "jev_evaluate", return_value=response) as evaluate:
                return json.loads(server.call_tool("jev_decide", args)["content"][0]["text"]), evaluate
        for allowed in (None, ["send"]):
            result, evaluate = decide(allowed)
            self.assertEqual(result["operation"], "BLOCKED")
            evaluate.assert_not_called()
        result, evaluate = decide(["delete"])
        self.assertEqual(result["operation"], "click_element")
        self.assertEqual(result["ref"], "ref-2")

    def test_jev_risky_target_is_material_even_when_model_says_routine(self):
        first = self.browser_snapshot("token-1", "ref-1", label="Sign out")
        second = self.browser_snapshot("token-2", "ref-2", label="Sign out")
        with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
            server, "browser_request", side_effect=[first, second]
        ), patch.object(server, "jev_evaluate", return_value=self.jev_response("e0")):
            result = json.loads(server.call_tool("jev_decide", {"goal": "Sign out", "allowed_risks": ["close"]})["content"][0]["text"])
        self.assertEqual(result["operation"], "BLOCKED")

    def test_jev_margin_and_confidence_gates_return_needs_agent(self):
        def decide(**gates):
            first = self.browser_snapshot("token-1", "ref-1")
            second = self.browser_snapshot("token-2", "ref-2")
            response = self.jev_response("e0")
            response["answers"]["next"]["probabilities"] = {"e0": 0.6, "WAIT": 0.4, "BLOCKED": 0.0, "DONE": 0.0}
            with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
                server, "browser_request", side_effect=[first, second]
            ), patch.object(server, "jev_evaluate", return_value=response):
                return json.loads(server.call_tool("jev_decide", {"goal": "Open settings", **gates})["content"][0]["text"])
        result = decide()
        self.assertEqual(result["operation"], "click_element")
        self.assertAlmostEqual(result["margin"], 0.2)
        result = decide(min_margin=0.5)
        self.assertEqual(result["operation"], "NEEDS_AGENT")
        self.assertIn("min_margin", result["reason"])
        result = decide(min_confidence=0.99)
        self.assertEqual(result["operation"], "NEEDS_AGENT")
        self.assertIn("min_confidence", result["reason"])

    def test_jev_rejects_invalid_risk_and_threshold_arguments(self):
        with patch.object(server, "browser_request") as browser:
            for field, value in (("allowed_risks", ["nuke"]), ("allowed_risks", "delete"), ("min_confidence", True),
                                 ("min_margin", 1.5), ("min_margin", "0.5")):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    server.call_tool("jev_decide", {"goal": "Open settings", field: value})
            browser.assert_not_called()

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


class NativePortTests(unittest.TestCase):
    window = MCPTests.window

    def setUp(self):
        self.native = server.cua_native
        self.native.SESSION.update(session=None, capture_scope="desktop")
        self.native.RECORDING.clear()
        self.native.RECORDING.update(active=False, output_dir=None, turn=0, video=False)
        self.native.CURSOR.update(enabled=False, x=0, y=0, theme="default", motion="instant")
        self.native.DISPATCH_DEPTH = 0
        self.native.VIDEO = None
        environment = patch.dict(os.environ, {"DISPLAY": ":1", "XDG_SESSION_TYPE": "x11"})
        environment.start()
        self.addCleanup(environment.stop)
        which = server.shutil.which
        utilities = patch.object(server.shutil, "which", side_effect=lambda name: None if name == "xprintidle" else which(name))
        utilities.start()
        self.addCleanup(utilities.stop)
        config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(config_dir.cleanup)
        config_path = Path(config_dir.name) / "config.json"
        config_path.write_text(json.dumps({"allow_without_activity_check": True}))
        config_patch = patch.object(server.cua_native, "CONFIG_PATH", config_path)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.addCleanup(self.native.RECORDING.update, active=False)

    def value(self, name, args=None):
        return json.loads(server.call_tool(name, args or {})["content"][0]["text"])

    def test_every_native_tool_has_schema_and_description(self):
        names = set("list_apps launch_app kill_app bring_to_front get_accessibility_tree get_desktop_state get_screen_size get_cursor_position move_cursor click double_click right_click drag type_text press_key hotkey set_window_frame verify_state clipboard_read clipboard_write check_permissions health_report get_config set_config start_session end_session get_session_state set_agent_cursor_enabled set_agent_cursor_theme set_agent_cursor_motion get_agent_cursor_state start_recording stop_recording get_recording_state replay_trajectory invoke_menu set_value".split())
        catalog = {tool["name"]: tool for tool in server.schemas()}
        self.assertEqual(names, set(self.native.PROPERTIES))
        self.assertEqual(len(server.BASE_TOOLS), len(set(server.BASE_TOOLS)))
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(catalog[name]["inputSchema"], self.native.schema(name))
                self.assertFalse(catalog[name]["inputSchema"]["additionalProperties"])
                self.assertIn("session", catalog[name]["inputSchema"]["properties"])
                self.assertNotIn("Not implemented", catalog[name]["description"])
        self.assertEqual(server.handle({"id": 1, "method": "initialize"})["result"]["serverInfo"]["version"], "1.3.2")

    def test_schema_validation_rejects_missing_unknown_and_wrong_types(self):
        for name in self.native.PROPERTIES:
            with self.subTest(name=name), self.assertRaises(ValueError):
                server.call_tool(name, {"unrecognized": True})
        for name, args in (("click", {"x": True, "y": 1}),
                           ("click", {"x": 1, "y": 1, "delivery_mode": "guess"}),
                           ("click", {"x": 1, "y": 1, "button": "unknown"}),
                           ("click", {"x": 1, "y": 1, "count": 0}),
                           ("click", {"x": 1, "y": 1, "modifier": [1]}),
                           ("set_config", {"max_image_dimension": 0}),
                           ("set_config", {"max_image_dimension": 16385}),
                           ("type_text", {"text": "bad\x00text"}),
                           ("launch_app", {"urls": "not-array"}),
                           ("verify_state", {"expect": {"unknown": True}})):
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                server.call_tool(name, args)

    def test_agent_cursor_is_on_by_default_so_users_see_where_the_agent_clicks(self):
        import importlib
        fresh = importlib.reload(importlib.import_module("cua_native"))
        self.assertTrue(fresh.CURSOR["enabled"])
        importlib.reload(fresh)
        server.cua_native = self.native

    def test_click_background_uses_window_send_event_without_focus(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", return_value="99") as run:
            result = self.value("click", {"window_id": 0x1234, "x": 10, "y": 20})
        self.assertTrue(result["ok"])
        self.assertEqual([call.args for call in run.call_args_list], [
            ("xdotool", "mousemove", "--sync", "--window", "0x1234", "10", "20"),
            ("xdotool", "click", "--window", "0x1234", "--repeat", "1", "--delay", "100", "1")])

    def test_click_foreground_activates_clicks_and_restores(self):
        def cli(*args):
            if args[1] == "getactivewindow":
                return "4660" if any(call.args == ("wmctrl", "-ia", "0x1234") for call in run.call_args_list) else "99"
            return ""
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", side_effect=cli) as run:
            self.value("double_click", {"window_id": 0x1234, "x": 10, "y": 20, "delivery_mode": "foreground"})
        calls = [call.args for call in run.call_args_list]
        self.assertEqual(calls[0], ("xdotool", "getactivewindow"))
        self.assertEqual(calls[1], ("wmctrl", "-ia", "0x1234"))
        self.assertIn(("xdotool", "click", "--repeat", "2", "--delay", "100", "1"), calls)
        self.assertEqual(calls[-1], ("wmctrl", "-ia", "0x63"))

    def test_foreground_ignores_windowactivate_failure_when_wmctrl_activates(self):
        def cli(*args):
            if args[1] == "getactivewindow":
                return "4660" if any(call.args == ("wmctrl", "-ia", "0x1234") for call in run.call_args_list) else "99"
            if args[1] == "windowactivate":
                raise server.Unsupported("windowactivate failed")
            return ""
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", side_effect=cli) as run:
            self.value("click", {"window_id": 0x1234, "x": 10, "y": 20, "delivery_mode": "foreground"})
        self.assertNotIn(("xdotool", "windowactivate", "0x1234"), [call.args for call in run.call_args_list])

    def test_foreground_activation_failure_restores_focus(self):
        def cli(*args):
            if args[1] == "getactivewindow":
                return "99"
            if args[1] == "windowactivate":
                raise server.Unsupported("windowactivate failed")
            return ""
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", side_effect=cli) as run:
            with self.assertRaisesRegex(server.Unsupported, "foreground activation failed: target window did not become active"):
                self.value("click", {"window_id": 0x1234, "x": 10, "y": 20, "delivery_mode": "foreground"})
        self.assertEqual(run.call_args.args, ("wmctrl", "-ia", "0x63"))

    def test_foreground_restores_focus_on_input_failure(self):
        def cli(*args):
            if args[1] == "getactivewindow":
                return "4660" if any(call.args == ("wmctrl", "-ia", "0x1234") for call in run.call_args_list) else "99"
            if args[1] == "click":
                raise server.Unsupported("input failed")
            return ""
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", side_effect=cli) as run:
            with self.assertRaisesRegex(server.Unsupported, "input failed"):
                self.value("click", {"window_id": 0x1234, "x": 10, "y": 20, "delivery_mode": "foreground"})
            self.assertEqual(run.call_args.args, ("wmctrl", "-ia", "0x63"))

    def test_click_bounds_and_empty_key_validation_prevent_input(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli") as run:
            for name, args in (("click", {"x": 800, "y": 1}), ("drag", {"from_x": 0, "from_y": 0, "to_x": -1, "to_y": 0}),
                               ("hotkey", {"keys": []}), ("press_key", {"key": "--window"}), ("type_text", {"text": ""})):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.value(name, {"window_id": 0x1234, **args})
            run.assert_not_called()

    def test_keyboard_background_and_foreground_arguments(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", return_value="99") as run:
            self.value("type_text", {"window_id": 0x1234, "text": "hello"})
            self.assertEqual(run.call_args.args, ("xdotool", "type", "--window", "0x1234", "--clearmodifiers", "--", "hello"))
            self.value("hotkey", {"window_id": 0x1234, "keys": ["ctrl", "c"]})
            self.assertEqual(run.call_args.args, ("xdotool", "key", "--window", "0x1234", "ctrl+c"))
        def cli(*args):
            if args[1] == "getactivewindow":
                return "4660" if any(call.args == ("wmctrl", "-ia", "0x1234") for call in run.call_args_list) else "99"
            return ""
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", side_effect=cli) as run:
            self.value("type_text", {"window_id": 0x1234, "text": "hello", "delivery_mode": "foreground"})
            self.assertIn(("xdotool", "type", "--clearmodifiers", "--", "hello"), [call.args for call in run.call_args_list])
            self.assertEqual(run.call_args.args, ("wmctrl", "-ia", "0x63"))

    def test_right_click_and_modifier_cleanup(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli") as run:
            self.value("right_click", {"window_id": 0x1234, "x": 0, "y": 0, "modifier": ["shift"]})
        calls = [call.args for call in run.call_args_list]
        self.assertEqual(calls[0], ("xdotool", "keydown", "--window", "0x1234", "shift"))
        self.assertEqual(calls[-2][-1], "3")
        self.assertEqual(calls[-1], ("xdotool", "keyup", "--window", "0x1234", "shift"))

    def test_recording_creates_evidence_before_and_after_each_mutation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(self.native, "root_png", side_effect=[b"before", b"after"]), patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli"):
            self.value("start_recording", {"output_dir": directory})
            self.value("click", {"window_id": 0x1234, "x": 1, "y": 2})
            turn = Path(directory) / "turn-00001"
            self.assertEqual((turn / "before.png").read_bytes(), b"before")
            self.assertEqual((turn / "after.png").read_bytes(), b"after")
            self.assertEqual(json.loads((turn / "action.json").read_text())["name"], "click")
            self.assertTrue(json.loads((turn / "evidence.json").read_text())["ok"])
            self.assertFalse(self.value("stop_recording")["active"])
            with self.assertRaisesRegex(ValueError, "already contains"):
                self.value("start_recording", {"output_dir": directory})

    def test_recording_recursively_redacts_action_arguments(self):
        arguments = {"text": "private", "nested": [{key: "private" for key in
                     ("value", "secret_inputs", "prompt_text", "javascript")}], "safe": "keep"}
        with tempfile.TemporaryDirectory() as directory, patch.object(self.native, "root_png", return_value=b"png"):
            self.value("start_recording", {"output_dir": directory})
            self.native.call(server, lambda name, args: server.text_result({"ok": True}), "browser_act", arguments)
            action = json.loads((Path(directory) / "turn-00001/action.json").read_text())
            self.assertEqual(action["arguments"], {"text": "[REDACTED]", "nested": [{key: "[REDACTED]" for key in
                             ("value", "secret_inputs", "prompt_text", "javascript")}], "safe": "keep"})
            self.assertEqual(arguments["text"], "private")

    def test_window_recording_never_falls_back_to_desktop(self):
        for arguments in ({}, {"window_id": 999}):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory, patch.object(
                self.native, "root_png"
            ) as root, patch.object(self.native, "target", side_effect=server.Unsupported("missing window")):
                self.native.SESSION["capture_scope"] = "window"
                self.value("start_recording", {"output_dir": directory})
                self.native.call(server, lambda name, args: server.text_result({"ok": True}), "browser_act", arguments)
                turn = Path(directory) / "turn-00001"
                self.assertEqual(json.loads((turn / "evidence.json").read_text())["skipped"], "window_scope_without_target")
                self.assertFalse((turn / "before.png").exists())
                self.assertFalse((turn / "after.png").exists())
                root.assert_not_called()
                self.value("stop_recording")
        with patch.object(server, "screenshot_png", return_value=b"window") as screenshot, patch.object(
            self.native, "target", return_value=self.window
        ), patch.object(self.native, "root_png") as root:
            self.assertEqual(self.native.recording_png(server, {"window_id": self.window["window_id"]}), b"window")
            screenshot.assert_called_once_with(self.window["window_id"])
            root.assert_not_called()

    def test_missing_activity_check_fails_closed_unless_config_allows(self):
        self.native.CONFIG_PATH.write_text("{}")
        self.assertFalse(self.value("get_config")["allow_without_activity_check"])
        with patch.object(server, "run_cli") as run:
            with self.assertRaisesRegex(server.Unsupported, "human_activity_check_unavailable: install xprintidle"):
                self.value("clipboard_write", {"text": "private"})
            run.assert_not_called()
        self.native.CONFIG_PATH.write_text(json.dumps({"allow_without_activity_check": True}))
        self.assertTrue(self.value("set_config", {"allow_without_activity_check": True})["allow_without_activity_check"])
        self.native.human_guard(server, {})
        with self.assertRaises(ValueError):
            self.value("set_config", {"allow_without_activity_check": "true"})

    def test_recording_failed_action_has_failure_evidence(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(self.native, "root_png", return_value=b"png"), patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", side_effect=server.Unsupported("input failed")):
            self.value("start_recording", {"output_dir": directory})
            with self.assertRaisesRegex(server.Unsupported, "input failed"):
                self.value("click", {"window_id": 0x1234, "x": 1, "y": 2})
            evidence = json.loads((Path(directory) / "turn-00001/evidence.json").read_text())
            self.assertFalse(evidence["ok"])
            self.assertIn("input failed", evidence["error"])

    def test_recording_keeps_human_refusal_evidence_without_input(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(self.native, "root_png", return_value=b"png"), patch.object(server, "run_cli") as run:
            self.value("start_recording", {"output_dir": directory})
            with patch.object(self.native, "human_guard", side_effect=server.Unsupported("human_activity")):
                with self.assertRaisesRegex(server.Unsupported, "human_activity"):
                    self.value("click", {"window_id": 0x1234, "x": 1, "y": 2})
            evidence = json.loads((Path(directory) / "turn-00001/evidence.json").read_text())
            self.assertFalse(evidence["ok"])
            self.assertIn("human_activity", evidence["error"])
            run.assert_not_called()

    def test_replay_records_replayed_actions_and_stops_on_errors(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as destination:
            turn = Path(source) / "turn-00001"
            turn.mkdir()
            (turn / "action.json").write_text(json.dumps({"name": "click", "arguments": {"window_id": 0x1234, "x": 1, "y": 2}}))
            with patch.object(self.native, "root_png", return_value=b"png"), patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli"):
                self.value("start_recording", {"output_dir": destination})
                self.assertTrue(self.value("replay_trajectory", {"dir": source, "delay_ms": 0})["ok"])
                self.assertTrue((Path(destination) / "turn-00001/action.json").exists())
            self.native.RECORDING["active"] = False
            with patch.object(server, "list_windows", return_value=[]):
                result = self.value("replay_trajectory", {"dir": source})
                self.assertFalse(result["ok"])
                self.assertTrue(result["stopped_on_error"])

    def test_config_round_trip_and_invalid_values_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(self.native, "CONFIG_PATH", Path(directory) / "config.json"), patch.object(self.native, "human_guard"):
            self.assertEqual(self.value("get_config")["max_image_dimension"], 1920)
            self.value("set_config", {"max_image_dimension": 1024})
            self.assertEqual(self.value("get_config"), {"max_image_dimension": 1024, "allow_without_activity_check": False})
            with self.assertRaises(ValueError):
                self.value("set_config", {"max_image_dimension": -1})
            self.assertEqual(json.loads((Path(directory) / "config.json").read_text()), {"max_image_dimension": 1024, "allow_without_activity_check": False})
            self.assertEqual((Path(directory) / "config.json").stat().st_mode & 0o777, 0o600)

    def test_human_activity_refuses_new_and_existing_mutations(self):
        def cli(*args):
            if args[0] == "xprintidle":
                return "1999"
            if args[1] == "getmouselocation":
                return "X=10\nY=20\nWINDOW=4660\n"
            if args[1] == "getactivewindow":
                return "4660"
            self.fail(f"Mutation dispatched: {args}")
        with patch.object(server.shutil, "which", return_value="/usr/bin/tool"), patch.object(server, "run_cli", side_effect=cli), patch.object(server, "list_windows", return_value=[self.window]):
            for name, args in (("click", {"window_id": 0x1234, "x": 1, "y": 2}),
                               ("kill_app", {"pid": 4321}),
                               ("type", {"target_window_id": 0x1234, "target_pid": 4321, "text": "hello", "expected_state_token": "unused"})):
                with self.subTest(name=name), self.assertRaisesRegex(server.Unsupported, "human_activity"):
                    server.call_tool(name, args)

    def test_human_idle_boundary_and_other_window_allow_action(self):
        with patch.object(server.shutil, "which", return_value="/usr/bin/tool"), patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", return_value="2000") as run:
            self.native.human_guard(server, {"window_id": 0x1234})
            run.assert_called_once_with("xprintidle")
        outputs = ["100", "X=10\nY=20\nWINDOW=99\n", "99"]
        with patch.object(server.shutil, "which", return_value="/usr/bin/tool"), patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", side_effect=outputs):
            self.native.human_guard(server, {"window_id": 0x1234})

    def test_health_reports_missing_idle_check(self):
        with patch.object(server.shutil, "which", return_value=None):
            result = self.value("health_report")
        self.assertEqual(result["human_activity_check"], "skipped: xprintidle is not installed")
        self.assertTrue(result["x11"])

    def test_window_cursor_is_overlay_only_even_without_tkinter(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli") as run, patch.object(self.native.importlib, "import_module", side_effect=ImportError):
            self.value("start_session", {"session": "agent"})
            state = self.value("set_agent_cursor_enabled", {"session": "agent", "enabled": True})
            self.assertEqual(state["status"], "overlay_unavailable")
            state = self.value("move_cursor", {"session": "agent", "window_id": 0x1234, "x": 5, "y": 7})
            self.assertFalse(state["physical_pointer_moved"])
            self.assertEqual((state["x"], state["y"]), (15, 27))
            run.assert_not_called()
            self.assertTrue(self.value("get_session_state", {"session": "agent"})["cursor"]["enabled"])
            self.value("end_session", {"session": "agent"})
            self.assertIsNone(self.native.SESSION["session"])

    def test_launch_uses_detached_argv_without_shell_and_reports_pid_windows(self):
        with patch.object(self.native, "desktop_entries", return_value=[]), patch.object(server, "list_windows", side_effect=[[], [self.window]]), patch.object(server.subprocess, "Popen") as popen:
            popen.return_value.pid = 4321
            popen.return_value.poll.return_value = None
            result = self.value("launch_app", {"command": "editor --new", "urls": ["https://example.com"], "additional_arguments": ["--flag"]})
        self.assertEqual(result["pid"], 4321)
        self.assertEqual(result["windows"], [self.window])
        self.assertEqual(popen.call_args.args[0], ["editor", "--new", "https://example.com", "--flag"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertNotIn("shell", popen.call_args.kwargs)
        with patch.object(self.native, "desktop_entries", return_value=[]):
            result = self.value("launch_app", {"command": "editor", "name": "Editor"})
            self.assertFalse(result["ok"])

    def test_desktop_exec_expands_urls_without_shell(self):
        entry = {"desktop_id": "editor.desktop", "name": "Editor", "exec": "editor %U", "path": "/usr/share/applications/editor.desktop"}
        self.assertEqual(self.native.application_command({"desktop_id": "editor.desktop", "urls": ["file:///tmp/a%20b"]}, [entry]), ["editor", "file:///tmp/a%20b"])

    def test_window_frame_readback_and_verify_missing_window(self):
        changed = {**self.window, "geometry": [1, 2, 300, 200]}
        with patch.object(server, "list_windows", side_effect=[[self.window], [changed]]), patch.object(server, "run_cli") as run:
            result = self.value("set_window_frame", {"window_id": 0x1234, "x": 1, "y": 2, "w": 300, "h": 200})
            self.assertTrue(result["ok"])
            run.assert_called_once_with("wmctrl", "-ir", "0x1234", "-e", "0,1,2,300,200")
        with patch.object(server, "list_windows", return_value=[]):
            self.assertEqual(self.value("verify_state", {"window_id": 0x1234, "expect": {"window_exists": False}})["status"], "pass")

    def test_desktop_state_uses_root_xwd_and_configured_resize(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(self.native, "CONFIG_PATH", Path(directory) / "config.json"), patch.object(server.shutil, "which", side_effect=lambda name: "/usr/bin/convert" if name == "convert" else None), patch.object(server, "run_cli_bytes", side_effect=[b"xwd", b"png"]) as run:
            result = server.call_tool("get_desktop_state", {})
            self.assertEqual(result["content"][0]["type"], "image")
            self.assertEqual(run.call_args_list[0].args, ("xwd", "-silent", "-root"))
            self.assertIn("1920x1920>", run.call_args_list[1].args)

    def test_clipboard_binary_requires_png_and_xclip(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server.shutil, "which", side_effect=lambda name: "/usr/bin/xsel" if name == "xsel" else None):
            path = Path(directory) / "image.png"
            path.write_bytes(b"not png")
            with self.assertRaisesRegex(ValueError, "PNG"):
                self.value("clipboard_write", {"image_path": str(path)})
            path.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
            with self.assertRaisesRegex(server.Unsupported, "require xclip"):
                self.value("clipboard_write", {"image_path": str(path)})

    def test_kill_app_sends_term_then_kill_and_refuses_server_pid(self):
        with patch.object(self.native.os, "kill") as kill, patch.object(self.native.time, "sleep"):
            result = self.value("kill_app", {"pid": 4321})
            self.assertEqual(kill.call_args_list[0].args, (4321, self.native.signal.SIGTERM))
            self.assertEqual(kill.call_args.args, (4321, self.native.signal.SIGKILL))
            self.assertFalse(result["termination_verified"])
            with self.assertRaises(ValueError):
                self.value("kill_app", {"pid": os.getpid()})
        with patch.object(self.native.os, "kill", side_effect=[None, ProcessLookupError]), patch.object(self.native.time, "sleep"):
            self.assertEqual(self.value("kill_app", {"pid": 4321})["signal"], "SIGTERM")

    def test_screen_size_cursor_and_desktop_tree_parse_utilities(self):
        with patch.object(server.shutil, "which", side_effect=lambda name: "/usr/bin/xdpyinfo" if name == "xdpyinfo" else None), patch.object(server, "run_cli", return_value="  dimensions: 1920x1080 pixels"):
            self.assertEqual(self.value("get_screen_size"), {"width": 1920, "height": 1080, "scale_factor": 1})
        with patch.object(server.shutil, "which", return_value=None), patch.object(server, "run_cli", return_value="Screen 0: minimum 8 x 8, current 1280 x 720, maximum 32767 x 32767") as run:
            self.assertEqual(self.value("get_screen_size")["width"], 1280)
            run.assert_called_once_with("xrandr", "--current")
        with patch.object(server, "run_cli", return_value="X=7\nY=8\nWINDOW=4660\n"):
            self.assertEqual(self.value("get_cursor_position"), {"x": 7, "y": 8, "window_id": 4660})
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "run_cli", side_effect=["_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x99, 0x1234", "4660"]):
            result = self.value("get_accessibility_tree")
            self.assertEqual(result["windows"][0]["stacking_order"], 1)
            self.assertTrue(result["windows"][0]["active"])

    def test_clipboard_uses_exact_text_and_uri_targets(self):
        with patch.object(server.shutil, "which", side_effect=lambda name: "/usr/bin/xclip" if name == "xclip" else None), patch.object(server.subprocess, "Popen") as popen:
            popen.return_value.returncode = 0
            self.assertEqual(self.value("clipboard_write", {"text": "hello"})["mime_type"], "text/plain;charset=utf-8")
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/xclip", "-selection", "clipboard", "-t", "text/plain;charset=utf-8", "-i"])
            self.assertEqual(popen.return_value.communicate.call_args.args, (b"hello",))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "a b.txt"
                path.write_text("file")
                self.assertEqual(self.value("clipboard_write", {"file_path": str(path)})["mime_type"], "text/uri-list")
                self.assertEqual(popen.return_value.communicate.call_args.args, ((path.resolve().as_uri() + "\r\n").encode(),))

    def test_optional_video_uses_x11grab_and_stops_owned_process(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server.shutil, "which", side_effect=lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None), patch.object(server.subprocess, "Popen") as popen:
            popen.return_value.poll.return_value = None
            popen.return_value.returncode = 0
            result = self.value("start_recording", {"output_dir": directory, "record_video": True})
            self.assertTrue(result["video"])
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/ffmpeg", "-nostdin", "-n", "-f", "x11grab", "-i", ":1", str(Path(directory).resolve() / "recording.mp4")])
            self.value("stop_recording")
            popen.return_value.terminate.assert_called_once()
            popen.return_value.wait.assert_called_once_with(timeout=5)

    def test_atspi_missing_fails_closed(self):
        with patch.object(self.native.importlib, "import_module", side_effect=ImportError):
            for name, args in (("invoke_menu", {"path": ["File", "Save"]}), ("set_value", {"value": "hello"})):
                with self.subTest(name=name), self.assertRaisesRegex(server.Unsupported, "python gi Atspi"):
                    self.value(name, args)


class BrowserPortTests(unittest.TestCase):
    window = MCPTests.window
    @staticmethod
    def browser_snapshot(text="private page text"):
        return MCPTests.browser_snapshot("current-token", "current-ref", label="Save", text=text)

    setUp = MCPTests.setUp
    def test_new_browser_operations_route_exact_names(self):
        tools = {tool["name"]: tool for tool in server.schemas()}
        for name, arguments in {
            "browser_navigate": {"url": "https://example.com"},
            "browser_click": {"ref": "current"},
            "browser_type": {"text": "hello", "mode": "keystrokes"},
            "browser_pointer": {"action": "hover", "x": 1, "y": 2},
            "browser_dialog": {"action": "inspect"},
            "browser_set_input_files": {"ref": "current", "paths": ["/tmp/a"]},
            "page": {"action": "get_text"},
        }.items():
            with self.subTest(name=name), patch.object(server, "browser_request", return_value={"routed": True}) as request:
                self.assertEqual(json.loads(server.call_tool(name, arguments)["content"][0]["text"]), {"routed": True})
                request.assert_called_once_with(name, arguments)
                self.assertTrue(tools[name]["inputSchema"]["properties"])
                self.assertNotIn("separately registered", tools[name]["description"])

    def test_download_moves_completed_file_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server.Path, "home", return_value=Path(folder)):
            downloads = Path(folder) / "Downloads" / "linux-use"
            downloads.mkdir(parents=True)
            destination = Path(folder) / "output"
            destination.mkdir()
            source = downloads / "download.txt"
            source.write_text("download content")
            args = {"ref": "current", "directory": str(destination)}
            with patch.object(server, "browser_request", side_effect=lambda operation, arguments: {"download_path": str(source), "elements": []}) as request:
                result = json.loads(server.call_tool("browser_download", args)["content"][0]["text"])
                request.assert_called_once_with("browser_download", args)
                self.assertEqual(result["path"], str((destination / source.name).resolve()))
                self.assertNotIn("download_path", result)
                self.assertFalse(source.exists())
                self.assertEqual(Path(result["path"]).read_text(), "download content")
                source.write_text("keep source")
                with self.assertRaises(server.Unsupported):
                    server.call_tool("browser_download", args)
                self.assertEqual(source.read_text(), "keep source")
                self.assertEqual(Path(result["path"]).read_text(), "download content")
                Path(result["path"]).unlink()
                with patch.object(server.shutil, "copyfileobj", side_effect=OSError("disk full")), self.assertRaises(server.Unsupported):
                    server.call_tool("browser_download", args)
                self.assertEqual(source.read_text(), "keep source")
                self.assertFalse(Path(result["path"]).exists())
            with patch.object(server, "browser_request") as request:
                for directory in ["relative", str(destination / "missing")]:
                    with self.assertRaises(ValueError):
                        server.call_tool("browser_download", {"ref": "current", "directory": directory})
                request.assert_not_called()
            (destination / "download.txt").write_text("borrowed file")
            with patch.object(server, "browser_request", return_value={"download_path": str(destination / "download.txt")}):
                with self.assertRaises(server.Unsupported):
                    server.call_tool("browser_download", args)

    def verify_browser_criteria(self, criteria):
        return json.loads(server.call_tool("verify_state", {"browser": True, "expect": {"criteria": criteria}})["content"][0]["text"])

    def test_criteria_maps_thresholds_in_one_text_only_request(self):
        snapshot = self.browser_snapshot()
        snapshot["elements"] += [{"role": "input", "name": "Password", "ref": "secret", "value": "super-secret"}]
        criteria = ["Save available", "State absent", "Uncertain", "Clearly present", "Clearly absent"]
        answers = {f"criterion_{index}": {"noul": probability} for index, probability in enumerate([0.85, 0.15, 0.5, 1, 0])}
        with patch.object(server, "jev_route", return_value=("key", "endpoint", "model")), patch.object(
            server, "browser_request", return_value=snapshot
        ) as browser, patch.object(server, "jev_evaluate", return_value={"answers": answers}) as evaluate:
            result = self.verify_browser_criteria(criteria)
            self.assertEqual([item["status"] for item in result["criteria"]], ["pass", "fail", "unknown", "pass", "fail"])
            self.assertEqual(result["status"], "fail")
            evaluate.assert_called_once()
            payload = evaluate.call_args.args[0]
            self.assertEqual(len(payload["questions"]), len(criteria))
            self.assertTrue(all(question["type"] == "noul" for question in payload["questions"].values()))
            for private in ["private field value", "super-secret", "Password", "private page text", "https://", "state_token", "image", "value"]:
                self.assertNotIn(private, json.dumps(payload))
            self.assertEqual(payload["state"]["evidence"], {"labels": [{"role": "button", "label": "Save"}]})
            self.assertEqual(browser.call_count, 2)

    def test_criteria_no_key_returns_filtered_evidence_without_jev(self):
        with patch.object(server, "jev_route", return_value=None), patch.object(
            server, "browser_request", return_value=self.browser_snapshot()
        ), patch.object(server, "jev_evaluate") as evaluate:
            result = self.verify_browser_criteria(["Save available"])
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["evidence"], {"labels": [{"role": "button", "label": "Save"}]})
            evaluate.assert_not_called()

    def test_criteria_invalid_bounds_rejected_before_observation(self):
        with patch.object(server, "browser_request") as browser:
            for criteria in [[], ["a"] * 9, ["x" * 201], [" "], [1], "not an array"]:
                with self.subTest(criteria=criteria), self.assertRaises(ValueError):
                    self.verify_browser_criteria(criteria)
            browser.assert_not_called()

    def test_criteria_boundary_and_unknown_or_stale_answers(self):
        for answers in [{}, {"criterion_0": {"noul": True}}, {"criterion_0": {"noul": 2}}]:
            with patch.object(server, "jev_route", return_value=True), patch.object(
                server, "browser_request", return_value=self.browser_snapshot()
            ), patch.object(server, "jev_evaluate", return_value={"answers": answers}):
                self.assertEqual(self.verify_browser_criteria(["x" * 200] * 8)["status"], "unknown")
        with patch.object(server, "jev_route", return_value=True), patch.object(
            server, "browser_request", side_effect=[self.browser_snapshot(), self.browser_snapshot(text="changed")]
        ), patch.object(server, "jev_evaluate", return_value={"answers": {"criterion_0": {"noul": 1}}}):
            self.assertEqual(self.verify_browser_criteria(["Save available"])["status"], "unknown")

    def test_native_labels_never_read_field_values_and_scrub_names(self):
        from unittest.mock import Mock
        def node(role, label, children=()):
            item = Mock()
            item.get_role_name.return_value = role
            item.get_name.return_value = label
            item.get_child_count.return_value = len(children)
            item.get_child_at_index.side_effect = lambda index: children[index]
            return item
        field = node("entry", "private field value")
        frame = node("frame", "Editor", [node("button", "Save"), field, node("status", "token=private-secret"),
                                                   node("label", "Verification code: 123456"), node("label", "123456"),
                                                   node("label", "API key"), node("label", "token")])
        app = node("application", "Editor", [frame])
        app.get_process_id.return_value = self.window["pid"]
        desktop = node("desktop", "Desktop", [app])
        atspi, gi = Mock(), Mock()
        atspi.get_desktop.return_value = desktop
        with patch.object(server.cua_native.importlib, "import_module", side_effect=[gi, atspi]):
            evidence = server.cua_native.jev_window_labels(server, self.window)
        encoded = json.dumps(evidence)
        self.assertIn("Save", encoded)
        self.assertNotIn("private field value", encoded)
        self.assertNotIn("private-secret", encoded)
        field.get_text.assert_not_called()
        with patch.object(server.cua_native.importlib, "import_module", side_effect=[gi, atspi]), patch.object(
            server, "list_windows", return_value=[self.window]
        ), patch.object(server, "jev_route", return_value=True), patch.object(
            server, "jev_evaluate", return_value={"answers": {"criterion_0": {"noul": 0.9}}}
        ) as evaluate:
            server.call_tool("verify_state", {"window_id": self.window["window_id"], "expect": {"criteria": ["Save available"]}})
            payload = json.dumps(evaluate.call_args.args[0])
            self.assertIn("Save", payload)
            for secret in ("Verification code", "123456", "API key", '"token"'):
                self.assertNotIn(secret, payload)
        self.assertEqual(server.scrub_jev_text("abc123456 123 1234"), "abc[redacted] 123 [redacted]")
        with patch.object(server.cua_native.importlib, "import_module", side_effect=ImportError):
            self.assertEqual(server.cua_native.jev_window_labels(server, self.window), [])

    def test_native_criteria_scrubs_title_and_uses_optional_labels(self):
        window = {**self.window, "title": "Editor token=private-key person@example.com"}
        with patch.object(server, "list_windows", return_value=[window]), patch.object(
            server.cua_native, "jev_window_labels", return_value=[{"role": "button", "label": "Save"}]
        ), patch.object(server, "jev_route", return_value=True), patch.object(
            server, "jev_evaluate", return_value={"answers": {"criterion_0": {"noul": 0.9}}}
        ) as evaluate:
            result = json.loads(server.call_tool("verify_state", {"window_id": window["window_id"], "expect": {"criteria": ["Save available"]}})["content"][0]["text"])
            self.assertEqual(result["status"], "pass")
            payload = json.dumps(evaluate.call_args.args[0])
            self.assertNotIn("private-key", payload)
            self.assertNotIn("person@example.com", payload)
            self.assertIn("Save", payload)


if __name__ == "__main__": unittest.main()
