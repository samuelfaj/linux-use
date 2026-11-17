import contextlib
import json
import os
import fcntl
import io
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
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
                with patch.object(server, "BRIDGE_PATH", bridge), patch.object(server, "installed_extension_id", return_value="a" * 32), patch.object(
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
        with patch.object(server, "browser_request", return_value={"connected": True, "mode": "current_chrome_profile", "hasTab": False, "version": server.MINIMUM_EXTENSION_VERSION}) as request:
            result = json.loads(server.call_tool("browser_status", {})["content"][0]["text"])
        self.assertTrue(result["connected"])
        self.assertEqual(result["state"], "connected")
        # A status question must not wait behind a slow page the way an action may.
        request.assert_called_once_with("browser_status", {}, timeout=2)
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
        # The proposal names the tool that executes it on the owned tab.
        self.assertEqual((result["operation"], result["action"]), ("browser_act", "click"))
        self.assertEqual(result["ref"], "ref-current")
        self.assertEqual(result["state_token"], "token-2")
        self.assertEqual([call.args[0] for call in browser.call_args_list], ["browser_snapshot", "browser_snapshot"])
        payload = evaluate.call_args.args[0]
        serialized = json.dumps(payload)
        self.assertIn("Open", serialized)
        self.assertNotIn("private body text", serialized)
        self.assertNotIn("private field value", serialized)

    def test_jev_without_key_hands_filtered_candidates_to_the_session_llm(self):
        snapshot = self.browser_snapshot("token-1", "ref-1")
        snapshot["elements"].append({"ref": "ref-pw", "role": "input", "name": "Login", "type": "password", "value": "hunter2"})
        snapshot["elements"].append({"ref": "ref-del", "role": "button", "name": "Delete draft", "type": "button"})
        with patch.object(server, "jev_route", return_value=None), patch.object(
            server, "browser_request", return_value=snapshot
        ), patch.object(server, "jev_evaluate") as evaluate:
            result = json.loads(server.call_tool("jev_decide", {"goal": "Open settings"})["content"][0]["text"])
        evaluate.assert_not_called()
        self.assertEqual((result["mode"], result["operation"]), ("llm", "DEFER_TO_LLM"))
        # Only safe candidates: no password field, no risky control the goal did not allow.
        self.assertEqual(result["candidates"], [{"operation": "browser_act", "action": "click", "ref": "ref-1",
                                                  "role": "button", "label": "Open"}])
        self.assertEqual(result["state_token"], "token-1")
        self.assertNotIn("hunter2", json.dumps(result))

    def test_jev_does_not_contact_model_for_credential_goal(self):
        with patch.object(server, "browser_request") as browser, patch.object(server, "jev_evaluate") as evaluate:
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

    def test_jev_done_needs_independent_completion_evidence(self):
        for completion, expected in ((0.99, "DONE"), (0.5, "BLOCKED")):
            with self.subTest(completion=completion):
                response = self.jev_response("DONE")
                response["answers"]["complete"]["noul"] = completion
                with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
                    server, "browser_request", side_effect=[self.browser_snapshot("token-1", "ref-1"), self.browser_snapshot("token-2", "ref-2")]
                ), patch.object(server, "jev_evaluate", return_value=response):
                    result = json.loads(server.call_tool("jev_decide", {"goal": "Open settings"})["content"][0]["text"])
                self.assertEqual(result["operation"], expected)
                if expected == "BLOCKED":
                    self.assertEqual(result["reason"], "Completion evidence below threshold")

    def test_jev_advice_carries_alternatives_signals_engine_and_block_reason(self):
        response = self.jev_response("e0")
        response["answers"]["next"]["probabilities"] = {"e0": 0.70, "WAIT": 0.30, "BLOCKED": 0.0, "DONE": 0.0}
        response["answers"]["consequential"]["noul"] = 0.9
        response["answers"]["authorized"]["noul"] = 0.99
        response.update(engine="typesafe/jev-1.14", fallback_reason="liquid/d1.2-ultra HTTP 503")
        with patch.object(server, "jev_route", return_value=("key", "https://example.test", "jev")), patch.object(
            server, "browser_request", side_effect=[self.browser_snapshot("token-1", "ref-1"), self.browser_snapshot("token-2", "ref-2")]
        ), patch.object(server, "jev_evaluate", return_value=response):
            result = json.loads(server.call_tool("jev_decide", {"goal": "Open settings"})["content"][0]["text"])
        # 0.70 is below the material threshold; the reason lets the session LLM ask the user instead of guessing.
        self.assertEqual((result["operation"], result["reason"]), ("BLOCKED", "Choice below material confidence threshold"))
        self.assertEqual(result["signals"], {"complete": 0.99, "consequential": 0.9, "authorized": 0.99})
        self.assertEqual(result["alternatives"], [{"operation": "WAIT", "probability": 0.30}])
        self.assertEqual((result["engine"], result["fallback_reason"]), ("typesafe/jev-1.14", "liquid/d1.2-ultra HTTP 503"))

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
        self.assertEqual(result["operation"], "browser_act")
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
        self.assertEqual(result["operation"], "browser_act")
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

    def test_atspi_set_value_verifies_delayed_write_once_and_refuses_unverified(self):
        from unittest.mock import Mock
        node, app, desktop, gi, atspi = [Mock() for _ in range(5)]
        node.get_name.return_value = self.window["title"]
        node.get_role_name.return_value = "entry"
        node.get_child_count.return_value = 0
        app.get_process_id.return_value = self.window["pid"]
        app.get_child_count.return_value = 1
        app.get_child_at_index.return_value = node
        desktop.get_child_count.return_value = 1
        desktop.get_child_at_index.return_value = app
        atspi.get_desktop.return_value = desktop
        editable = node.get_editable_text_iface.return_value
        editable.set_text_contents.return_value = True
        # Readback goes through Atspi.Text.get_text(node, ...): the interface object's own get_text is the
        # deprecated one-argument Accessible.get_text and failed every readback on real AT-SPI.
        for observed, succeeds in [(["", "hello"], True), ([""] * 15, False)]:
            atspi.Text.get_text.side_effect = observed
            editable.set_text_contents.reset_mock()
            with patch.object(server.cua_native.importlib, "import_module", side_effect=[gi, atspi]), patch.object(
                server, "list_windows", return_value=[self.window]
            ), patch.object(server.cua_native.time, "sleep"), patch.object(server, "run_cli") as cli:
                if succeeds:
                    self.assertTrue(self.value("set_value", {"window_id": self.window["window_id"], "role": "entry", "value": "hello"})["ok"])
                else:
                    with self.assertRaisesRegex(server.Unsupported, "accepted but unverified"):
                        self.value("set_value", {"window_id": self.window["window_id"], "role": "entry", "value": "hello"})
                editable.set_text_contents.assert_called_once_with("hello")
                self.assertFalse(any(c.args and c.args[0] == "xdotool" for c in cli.call_args_list))
        value = node.get_value_iface.return_value
        value.set_current_value.return_value = True
        for observed, succeeds in [([0, 42], True), ([0] * 15, False)]:
            value.get_current_value.side_effect = observed
            value.set_current_value.reset_mock()
            with patch.object(server.cua_native.importlib, "import_module", side_effect=[gi, atspi]), patch.object(
                server, "list_windows", return_value=[self.window]
            ), patch.object(server.cua_native.time, "sleep"):
                if succeeds:
                    self.assertTrue(self.value("set_value", {"window_id": self.window["window_id"], "role": "entry", "value": 42})["ok"])
                else:
                    with self.assertRaisesRegex(server.Unsupported, "numeric write accepted but unverified"):
                        self.value("set_value", {"window_id": self.window["window_id"], "role": "entry", "value": 42})
                value.set_current_value.assert_called_once_with(42)

    def test_human_activity_between_click_commands_stops_before_click(self):
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server.cua_native, "human_guard", side_effect=[None, None, server.Unsupported("human_activity")]
        ), patch.object(server, "run_cli") as cli:
            with self.assertRaisesRegex(server.Unsupported, "human_activity"):
                self.value("click", {"window_id": self.window["window_id"], "x": 10, "y": 20})
            self.assertFalse(any(c.args[:2] == ("xdotool", "click") for c in cli.call_args_list))


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

    def test_every_page_tool_lets_the_caller_escalate_and_the_flag_reaches_the_extension(self):
        # The extension refuses a tab the user selected unless the call carries allow_while_user_active.
        # The schemas forbid unknown properties, so a tool without it could never escalate; and only
        # the extension can tell whether the user is on the tab, so the server must pass it through.
        tools = {tool["name"]: tool["inputSchema"] for tool in server.schemas()}
        for name in server.BROWSER_PAGE_TOOLS:
            with self.subTest(name=name):
                self.assertEqual(tools[name]["properties"]["allow_while_user_active"]["type"], "boolean")
                self.assertFalse(tools[name]["additionalProperties"])
        # A dialog in a tab the user selected is theirs; open, close and status act on no page.
        others = {"browser_dialog", "browser_open", "browser_close", "browser_status"}
        for name in others:
            self.assertNotIn("allow_while_user_active", tools[name]["properties"])
        # A new browser tool has to be classified one way or the other.
        self.assertEqual(set(server.BROWSER_PAGE_TOOLS) | others,
                         {name for name in server.BASE_TOOLS if name.startswith("browser_") or name == "page"})
        with patch.object(server, "browser_request", return_value={"url": "https://example.com/"}) as request:
            server.call_tool("browser_click", {"x": 1, "y": 2, "allow_while_user_active": True})
        request.assert_called_once_with("browser_click", {"x": 1, "y": 2, "allow_while_user_active": True})

    def test_snapshot_query_is_accepted_and_forwarded(self):
        # A long page buries the one control a task needs past the 200-element cap; query is how the
        # caller reaches it, so the schema must admit it and the server must forward it unchanged.
        schema = {tool["name"]: tool["inputSchema"] for tool in server.schemas()}["browser_snapshot"]
        self.assertEqual(schema["properties"]["query"]["type"], "string")
        with patch.object(server, "browser_request", return_value={"elements": []}) as request:
            server.call_tool("browser_snapshot", {"query": "delete app"})
        request.assert_called_once_with("browser_snapshot", {"query": "delete app"})

    def test_extension_status_names_every_state_and_never_raises(self):
        # browser_status is the agent's first browser call. Each state means a different next step
        # (use the tools, retry, reload, reconnect, install), so none may be mistaken for another,
        # and a status question must get a status answer rather than an error.
        current = {"connected": True, "mode": "current_chrome_profile", "hasTab": False, "version": "1.5.0"}
        cases = [
            ({"return_value": current}, "a" * 32, "connected"),
            # Versions compare by number: 1.10.0 is newer than 1.5.0 although it sorts first as text.
            ({"return_value": {**current, "version": "1.10.0"}}, "a" * 32, "connected"),
            ({"return_value": {**current, "version": "1.3.1"}}, "a" * 32, "outdated"),
            ({"return_value": {key: value for key, value in current.items() if key != "version"}}, "a" * 32, "outdated"),
            ({"side_effect": server.BridgeTimeout("timed out")}, "a" * 32, "busy"),
            ({"side_effect": server.Unsupported("not connected")}, "a" * 32, "not_connected"),
            ({"side_effect": server.Unsupported("not connected")}, None, "not_installed"),
            ({"side_effect": RuntimeError("unexpected")}, "a" * 32, "not_connected"),
        ]
        for request, registered, state in cases:
            with self.subTest(state=state, request=request), patch.object(server, "browser_request", **request), patch.object(
                server, "installed_extension_id", return_value=registered
            ):
                status = server.extension_status()
            self.assertEqual(status["state"], state)
            self.assertEqual(status["connected"], state in ("connected", "outdated", "busy"))
            self.assertEqual("hint" in status, state != "connected")

    def test_browser_status_answers_with_a_state_when_the_bridge_is_down(self):
        # With Chrome closed there is no bridge socket; the tool must still answer, not fail, and say why.
        with tempfile.TemporaryDirectory() as home, patch.object(server, "BRIDGE_PATH", Path(home) / "bridge.sock"), patch.object(
            server, "chrome_running", return_value=False
        ):
            server.install_host("a" * 32, home)
            with patch.object(server.Path, "home", return_value=Path(home)):
                result = server.call_tool("browser_status", {})
        status = json.loads(result["content"][0]["text"])
        self.assertFalse(result["isError"])
        self.assertEqual(status["state"], "not_connected")
        self.assertTrue(status["hint"])

    def test_bridge_waits_out_an_extension_reconnect_but_not_a_closed_chrome(self):
        # The extension reconnects about a second after a drop, so a call made in that gap should land
        # instead of failing. With Chrome closed nothing will reconnect, so fail at once and say why.
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "BRIDGE_PATH", Path(directory) / "bridge.sock"):
            with patch.object(server, "chrome_running", return_value=False):
                start = time.monotonic()
                with self.assertRaisesRegex(server.Unsupported, "Chrome is not running"):
                    server.browser_request("browser_snapshot", {})
                self.assertLess(time.monotonic() - start, 0.5)

            def reconnect_then_answer():
                time.sleep(0.3)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(server.BRIDGE_PATH))
                    listener.listen(1)
                    client, _ = listener.accept()
                    with client:
                        request = server._read_frame(client.makefile("rb"))
                        server._write_frame(client.makefile("wb"), {"id": request["id"], "ok": True, "url": "https://example.com/"})
                server.BRIDGE_PATH.unlink()

            extension = threading.Thread(target=reconnect_then_answer)
            extension.start()
            with patch.object(server, "chrome_running", return_value=True):
                self.assertEqual(server.browser_request("browser_snapshot", {}), {"url": "https://example.com/"})
                extension.join()
                # Chrome is up but the extension never comes back: give up after the wait, with the reconnect hint.
                start = time.monotonic()
                with self.assertRaisesRegex(server.Unsupported, "extension is not connected. It reconnects"):
                    server.browser_request("browser_snapshot", {}, timeout=1)
                self.assertGreaterEqual(time.monotonic() - start, 0.5)

    def test_bridge_failures_say_which_failure_happened(self):
        # A closed bridge means "check browser_status and retry"; a timeout means the action may have
        # run, so "check the tab before retrying". The caller can only choose if the messages differ.
        def bridge():
            server.BRIDGE_PATH.unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(server.BRIDGE_PATH))
            listener.listen(1)
            return listener

        with tempfile.TemporaryDirectory() as directory, patch.object(server, "BRIDGE_PATH", Path(directory) / "bridge.sock"):
            with bridge():
                with self.assertRaisesRegex(server.BridgeTimeout, "timed out. Check the tab"):
                    server.browser_request("browser_snapshot", {}, timeout=0.3)
            with bridge() as listener:
                closing = threading.Thread(target=lambda: listener.accept()[0].close())
                closing.start()
                with self.assertRaisesRegex(server.Unsupported, "stopped answering") as caught:
                    server.browser_request("browser_snapshot", {}, timeout=2)
                closing.join()
                self.assertNotIsInstance(caught.exception, server.BridgeTimeout)

    def test_native_host_tells_the_caller_when_the_extension_never_answers(self):
        # A page Chrome froze kept the extension from answering; the host waited forever and every
        # later call hung behind it. Now the caller hears why, and the host exits so Chrome starts a
        # clean one when the extension reconnects.
        origin = "chrome-extension://" + "a" * 32 + "/"
        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()
        with contextlib.ExitStack() as stack:
            for fd in (stdin_r, stdin_w, stdout_r, stdout_w):
                stack.callback(os.close, fd)
            bridge = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "bridge.sock"
            chrome_reads = stack.enter_context(open(stdout_r, "rb", closefd=False))
            host_stdin = stack.enter_context(open(stdin_r, "rb", closefd=False))
            host_stdout = stack.enter_context(open(stdout_w, "wb", closefd=False))
            for target, name, value in ((server, "BRIDGE_PATH", bridge), (server, "installed_extension_id", lambda: "a" * 32),
                                        (server.sys, "argv", ["server.py", "chrome-native-host", origin]),
                                        (server.sys, "stdin", host_stdin), (server.sys, "stdout", SimpleNamespace(buffer=host_stdout))):
                stack.enter_context(patch.object(target, name, value))
            host = threading.Thread(target=server.native_host, kwargs={"response_timeout": 0.3}, daemon=True)
            host.start()
            deadline = time.monotonic() + 2
            while not bridge.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as caller:
                caller.settimeout(5)  # a host without its deadline fails this test instead of hanging it
                caller.connect(str(bridge))
                with caller.makefile("wb") as writer:
                    server._write_frame(writer, {"id": "r1", "operation": "browser_snapshot"})
                with caller.makefile("rb") as reader:
                    answer = server._read_frame(reader)
            host.join(2)
            self.assertEqual(server._read_frame(chrome_reads)["id"], "r1")
        self.assertEqual(answer["id"], "r1")
        self.assertIs(answer["ok"], False)
        self.assertIn("did not answer", answer["error"])
        self.assertFalse(host.is_alive())
        self.assertFalse(bridge.exists())

    def test_native_host_serves_only_the_registered_extension(self):
        # The host is a plain script anyone can start with any origin argument; only the extension
        # install_host registered may open the bridge into the user's Chrome profile.
        for registered, origin in (("a" * 32, "chrome-extension://" + "b" * 32 + "/"), (None, "chrome-extension://" + "a" * 32 + "/")):
            with self.subTest(registered=registered), tempfile.TemporaryDirectory() as directory, open(os.devnull, "rb") as stdin, patch.object(
                server, "BRIDGE_PATH", Path(directory) / "bridge.sock"
            ), patch.object(server, "installed_extension_id", return_value=registered), patch.object(
                server.sys, "argv", ["server.py", "chrome-native-host", origin]
            ), patch.object(server.sys, "stdin", stdin):
                server.native_host()
                self.assertFalse(server.BRIDGE_PATH.with_suffix(".lock").exists())

    def test_client_exit_closes_the_agents_tab_only_when_the_bridge_is_up(self):
        # An agent that never reached browser_close would leave its tab, and Chrome's debugging bar,
        # open for good. With no bridge there is no tab to close, and exit must not wait on a reconnect.
        for available in (True, False):
            with self.subTest(available=available), patch.object(server, "browser_bridge_available", return_value=available), patch.object(
                server, "browser_request", side_effect=server.Unsupported("tab already gone")
            ) as request, patch.object(server.sys, "argv", ["server.py"]), patch.object(server.sys, "stdin", io.StringIO("")):
                server.main()  # a failed close must not turn a clean exit into a crash
            if available:
                request.assert_called_once_with("browser_close", {})
            else:
                request.assert_not_called()

    def test_screenshot_of_chrome_points_at_the_browser_tools(self):
        # Web content answers DOM refs in a background tab far better than pixel clicks in the user's
        # Chrome window, so a caller looking at Chrome should learn the extension is there to use.
        for name, available, expected in (("chrome", True, True), ("chrome", False, False), ("gedit", True, False)):
            with self.subTest(name=name, available=available), patch.object(server, "process_name", return_value=name), patch.object(
                server, "browser_bridge_available", return_value=available
            ):
                self.assertEqual(server.browser_tools_hint(4321) is not None, expected)
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server, "screenshot_png", return_value=b"png"), patch.object(
            server, "process_name", return_value="chrome"
        ), patch.object(server, "browser_bridge_available", return_value=True):
            content = server.call_tool("screenshot", {"target_pid": 4321, "target_window_id": 0x1234})["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertIn("browser_open", content[-1]["text"])

    def test_installed_extension_id_reads_what_install_host_registered(self):
        # The native host trusts only this origin and browser_status says not_installed without it,
        # so it must read exactly what install_host writes and reject anything else.
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(server.installed_extension_id(home))
            server.install_host("b" * 32, home)
            self.assertEqual(server.installed_extension_id(home), "b" * 32)
            origin = "chrome-extension://{}/".format
            for broken in ("not json", "[]", json.dumps({"allowed_origins": [origin("b" * 32), origin("c" * 32)]}),
                           json.dumps({"allowed_origins": [origin("z" * 32)]})):
                server.host_registration(home).write_text(broken)
                self.assertIsNone(server.installed_extension_id(home))

    def test_doctor_and_health_report_include_the_extension_state(self):
        # Diagnostics are where a user looks when browser tools are missing; they must say which state.
        with patch.object(server, "browser_bridge_available", return_value=False), patch.object(
            server, "installed_extension_id", return_value=None
        ):
            doctor = json.loads(server.call_tool("doctor", {})["content"][0]["text"])
        self.assertEqual(doctor["chrome_extension"]["state"], "not_installed")
        self.assertEqual(doctor["supported_browser_tools"], [])
        with patch.object(server, "extension_status", return_value={"connected": True, "state": "busy", "hint": "retry"}):
            health = json.loads(server.call_tool("health_report", {})["content"][0]["text"])
        self.assertEqual(health["chrome_extension"]["state"], "busy")

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
        ), patch.object(server.atspi_tree, "snapshot", side_effect=server.Unsupported("no AT-SPI tree")), patch.object(
            server, "decision_image", return_value=(None, "capture_unavailable")
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


class DecisionRoutingTests(unittest.TestCase):
    """d1.2-ultra answers every decision; Jev answers only when d1.2-ultra cannot be reached."""

    request = {"state": {"goal": "Click Save"}, "questions": {"q": {"type": "noul", "instructions": "?"}}}
    answered = {"answers": {"q": {"type": "noul", "noul": 0.9}}}

    def scripted(self, replies):
        calls = []

        class Response:
            def __init__(self, body):
                self.body = body

            def read(self, limit):
                return json.dumps(self.body).encode()[:limit]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout):
            body = json.loads(request.data)
            calls.append({"model": body["model"], "body": body, "timeout": timeout,
                          "auth": request.get_header("Authorization"), "url": request.full_url})
            reply = replies.get(body["model"], 500)
            if isinstance(reply, BaseException):
                raise reply
            if isinstance(reply, int):
                raise server.urllib.error.HTTPError(request.full_url, reply, "status", {}, None)
            return Response(reply)

        return calls, patch.object(server.urllib.request, "urlopen", side_effect=urlopen)

    def test_openrouter_key_always_routes_to_d12_ultra_with_jev_fallback(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key", "TYPESAFE_API_KEY": "ts-key"}, clear=True):
            # The owner chose OpenRouter for every decision, so a Jev key beside it must not win.
            self.assertEqual(server.jev_route(), ("or-key", "https://openrouter.ai/api/alpha/decisions",
                                                  "liquid/d1.2-ultra", "typesafe/jev-1.14"))
        with patch.dict(os.environ, {"JEV_API_KEY": "jev-key", "TYPESAFE_API_KEY": "ts-key"}, clear=True):
            self.assertEqual(server.jev_route(), ("jev-key", "https://api.typesafe.ai/v1/systemone", "jev-latest", None))
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(server.jev_route())

    def test_d12_ultra_answers_and_names_the_model_that_served_it(self):
        calls, urlopen = self.scripted({"liquid/d1.2-ultra": dict(self.answered, model="liquid/d1.2-ultra-20261116")})
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=True), urlopen:
            response = server.jev_evaluate(dict(self.request))
        self.assertEqual(response["engine"], "liquid/d1.2-ultra-20261116")
        self.assertNotIn("fallback_reason", response)
        self.assertEqual([call["model"] for call in calls], ["liquid/d1.2-ultra"])
        self.assertEqual(calls[0]["auth"], "Bearer or-key")
        # A short primary timeout leaves time for Jev when d1.2-ultra hangs.
        self.assertEqual(calls[0]["timeout"], 5)

    def test_unreachable_d12_ultra_falls_back_to_jev_and_says_why(self):
        for failure in (503, 429, 404, TimeoutError("slow"), server.URLError(ConnectionRefusedError())):
            with self.subTest(failure=failure):
                calls, urlopen = self.scripted({"liquid/d1.2-ultra": failure, "typesafe/jev-1.14": self.answered})
                with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=True), urlopen:
                    response = server.jev_evaluate(dict(self.request))
                self.assertEqual([call["model"] for call in calls], ["liquid/d1.2-ultra", "typesafe/jev-1.14"])
                self.assertEqual(response["engine"], "typesafe/jev-1.14")
                self.assertTrue(response["fallback_reason"].startswith("liquid/d1.2-ultra "), response["fallback_reason"])
                self.assertEqual(response["answers"]["q"]["noul"], 0.9)

    def test_rejected_request_fails_loud_instead_of_hiding_the_bug_behind_jev(self):
        for status in (400, 401, 402, 422):
            with self.subTest(status=status):
                calls, urlopen = self.scripted({"liquid/d1.2-ultra": status, "typesafe/jev-1.14": self.answered})
                with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=True), urlopen:
                    with self.assertRaisesRegex(server.Unsupported, f"HTTP {status}"):
                        server.jev_evaluate(dict(self.request))
                self.assertEqual([call["model"] for call in calls], ["liquid/d1.2-ultra"])

    def test_both_models_down_is_an_error_naming_both(self):
        calls, urlopen = self.scripted({"liquid/d1.2-ultra": 503, "typesafe/jev-1.14": 502})
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=True), urlopen:
            with self.assertRaises(server.Unsupported) as raised:
                server.jev_evaluate(dict(self.request))
        self.assertIn("liquid/d1.2-ultra HTTP 503", str(raised.exception))
        self.assertIn("typesafe/jev-1.14 HTTP 502", str(raised.exception))

    def test_direct_jev_route_has_no_fallback(self):
        calls, urlopen = self.scripted({"jev-latest": 503})
        with patch.dict(os.environ, {"JEV_API_KEY": "jev-key"}, clear=True), urlopen:
            with self.assertRaises(server.Unsupported):
                server.jev_evaluate(dict(self.request))
        self.assertEqual([(call["model"], call["timeout"]) for call in calls], [("jev-latest", 12)])

    def test_image_request_keeps_its_image_on_fallback_and_gets_a_longer_timeout(self):
        calls, urlopen = self.scripted({"liquid/d1.2-ultra": 503, "typesafe/jev-1.14": self.answered})
        state = server.state_with_image({"goal": "Click Save"}, "data:image/png;base64,AAAA")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=True), urlopen:
            server.jev_evaluate({"state": state, "questions": self.request["questions"]})
        self.assertEqual(calls[0]["timeout"], 10)
        # Jev on OpenRouter reads images up to 1024 px, so the fallback decides from the same evidence.
        for call in calls:
            image, text = call["body"]["state"]
            self.assertEqual(image, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}})
            self.assertIn("Click Save", text)


class AtspiTreeTests(unittest.TestCase):
    """The AT-SPI layer: ids that keep meaning their element, secure fields never read, decisions over it."""

    window = {"window_id": 4194311, "pid": 77, "desktop": "0", "geometry": [0, 0, 640, 360], "title": "Form"}

    def setUp(self):
        server.atspi_tree.STORE.clear()

    @staticmethod
    def item(index, role, name="", parent=None, states=(), actions=(), value=None, depth=0):
        accessible = type("Accessible", (), {})()
        accessible.get_role_name = lambda: role
        accessible.get_name = lambda: name
        accessible.get_description = lambda: ""
        return {"id": f"ax_{index}", "role": role, "name": name, "description": "", "value": value,
                "states": list(states), "actions": list(actions), "depth": depth, "parent": parent, "accessible": accessible}

    def form(self):
        return [self.item(0, "frame", "Form"), self.item(1, "filler", parent="ax_0"),
                self.item(2, "label", "Status: Ready", parent="ax_1"), self.item(3, "filler", parent="ax_1"),
                self.item(4, "label", "Wi-Fi", parent="ax_3"), self.item(5, "toggle button", parent="ax_3", states=["checked"], actions=["toggle"]),
                self.item(6, "button", "Save", parent="ax_1", actions=["click"]),
                self.item(7, "button", "Delete draft", parent="ax_1", actions=["click"])]

    def test_rows_fold_unnamed_wrappers_and_a_query_keeps_ancestors(self):
        rows = server.atspi_tree.render_rows(self.form())
        # A single-child wrapper says nothing; folding it keeps the tree short for the model reading it.
        self.assertEqual(rows.splitlines()[:3], ['- [ax_0] frame "Form"', "  - [ax_1] filler", '    - [ax_2] label "Status: Ready"'])
        self.assertIn('- [ax_5] toggle button {checked} [toggle]', rows)
        query = server.atspi_tree.render_rows(self.form(), query="save|status")
        self.assertEqual(query.splitlines(), ['- [ax_0] frame "Form"', "  - [ax_1] filler",
                                              '    - [ax_2] label "Status: Ready"', '    - [ax_6] button "Save" [click]'])
        capped = server.atspi_tree.render_rows(self.form(), max_elements=2)
        self.assertIn("more rows", capped.splitlines()[-1])

    def test_an_id_keeps_meaning_its_element_until_it_changes(self):
        nodes = self.form()
        token = server.atspi_tree.remember(self.window, nodes)
        self.assertIs(server.atspi_tree.resolve(server, self.window, "ax_6", token), nodes[6]["accessible"])
        nodes[6]["accessible"].get_name = lambda: "Send"  # Same slot, different control: never press it.
        with self.assertRaisesRegex(server.Unsupported, "stale_state_token"):
            server.atspi_tree.resolve(server, self.window, "ax_6", token)
        with self.assertRaisesRegex(server.Unsupported, "not in that read"):
            server.atspi_tree.resolve(server, self.window, "ax_99", token)

    def test_only_the_last_eight_reads_are_kept(self):
        tokens = [server.atspi_tree.remember(self.window, self.form()) for _ in range(9)]
        with self.assertRaisesRegex(server.Unsupported, "stale_state_token"):
            server.atspi_tree.resolve(server, self.window, "ax_6", tokens[0])
        server.atspi_tree.resolve(server, self.window, "ax_6", tokens[-1])

    def test_a_secure_field_is_never_read(self):
        atspi = type("Atspi", (), {})()
        node = type("Node", (), {"get_text_iface": lambda self: self})()
        atspi.Text = type("Text", (), {"get_character_count": staticmethod(lambda node: 7),
                                         "get_text": staticmethod(lambda node, start, end: "hunter2")})
        self.assertIsNone(server.atspi_tree._value(atspi, node, "password text"))
        self.assertEqual(server.atspi_tree._value(atspi, node, "text"), "hunter2")

    def test_switch_without_a_name_is_labelled_by_the_text_beside_it(self):
        labels = {item["id"]: label for item, label in server.atspi_tree.actionable(self.form())}
        self.assertEqual(labels, {"ax_5": "Wi-Fi", "ax_6": "Save", "ax_7": "Delete draft"})

    def decide(self, nodes, *, route=("key", "endpoint", "model", None), image=("data:image/png;base64,AAAA", None)):
        args = {"goal": "Save the form", "target_pid": 77, "target_window_id": 4194311}
        answer = {"answers": {"next": {"choice": "e1", "probabilities": {"e0": 0.0, "e1": 1.0, "WAIT": 0.0, "DONE": 0.0, "BLOCKED": 0.0},
                                       "confidence": 0.95},
                              "complete": {"noul": 0.0}, "consequential": {"noul": 0.0}, "authorized": {"noul": 0.0}},
                  "engine": "liquid/d1.2-ultra-20261116"}
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(server.cua_native, "human_guard"), patch.object(
            server.atspi_tree, "snapshot", return_value=("ax-token", nodes)
        ), patch.object(server, "jev_route", return_value=route), patch.object(
            server, "decision_image", return_value=image
        ) as capture, patch.object(server, "jev_evaluate", return_value=answer) as evaluate:
            result = json.loads(server.call_tool("jev_decide", args)["content"][0]["text"])
        return result, capture, evaluate

    def test_native_decision_reads_the_window_image_with_its_controls(self):
        result, capture, evaluate = self.decide(self.form())
        self.assertEqual((result["operation"], result["label"], result["element_id"]), ("click_element", "Save", "ax_6"))
        self.assertEqual((result["expected_state_token"], result["image_attached"]), ("ax-token", True))
        image, text = evaluate.call_args.args[0]["state"]
        self.assertEqual(image["image_url"]["url"], "data:image/png;base64,AAAA")
        # The risky control is withheld from the model unless the goal allowed its category.
        self.assertIn("Wi-Fi", text)
        self.assertNotIn("Delete draft", text)

    def test_native_decision_never_captures_a_window_with_a_secure_field(self):
        nodes = self.form() + [self.item(8, "password text", "Password", parent="ax_1")]
        result, capture, evaluate = self.decide(nodes)
        capture.assert_not_called()
        self.assertEqual((result["image_attached"], result["image_skip_reason"]), (False, "secure_field"))
        self.assertIsInstance(evaluate.call_args.args[0]["state"], dict)

    def test_native_decision_without_a_key_hands_element_ids_to_the_session_llm(self):
        result, capture, evaluate = self.decide(self.form(), route=None)
        evaluate.assert_not_called()
        capture.assert_not_called()
        self.assertEqual((result["mode"], result["operation"], result["expected_state_token"]), ("llm", "DEFER_TO_LLM", "ax-token"))
        self.assertIn({"operation": "click_element", "element_id": "ax_6", "role": "button", "label": "Save"}, result["candidates"])
        self.assertNotIn("Delete draft", json.dumps(result))

    def test_verify_sends_the_window_image_and_a_model_error_is_unknown_not_a_failed_call(self):
        def verify(evaluate_effect):
            with patch.dict(os.environ, {"DISPLAY": ":99", "XDG_SESSION_TYPE": "x11"}), patch.object(
                server, "list_windows", return_value=[self.window]
            ), patch.object(
                server.cua_native, "jev_window_labels", return_value=[{"role": "button", "label": "Save"}]
            ), patch.object(server.atspi_tree, "snapshot", return_value=("ax-token", self.form())), patch.object(
                server, "decision_image", return_value=("data:image/png;base64,AAAA", None)
            ), patch.object(server, "jev_route", return_value=("key", "endpoint", "model", None)), patch.object(
                server, "jev_evaluate", side_effect=evaluate_effect
            ) as evaluate:
                result = server.call_tool("verify_state", {"window_id": self.window["window_id"], "expect": {"criteria": ["Wi-Fi is on"]}})
            return json.loads(result["content"][0]["text"]), evaluate
        result, evaluate = verify([{"answers": {"criterion_0": {"noul": 0.97}}, "engine": "liquid/d1.2-ultra-20261116"}])
        criterion = result["criteria"][0]
        self.assertEqual((criterion["status"], criterion["probability"], criterion["image_attached"]), ("pass", 0.97, True))
        self.assertEqual(criterion["engine"], "liquid/d1.2-ultra-20261116")
        self.assertIsInstance(evaluate.call_args.args[0]["state"], list)
        result, _ = verify(server.Unsupported("Decision request failed (unreachable)"))
        self.assertEqual(result["criteria"][0]["status"], "unknown")
        self.assertIn("unreachable", result["criteria"][0]["reason"])


class SubtaskTests(unittest.TestCase):
    """run_subtask: the model picks among offered ids; text only from inputs; secrets never leave."""

    window = {"window_id": 4194311, "pid": 77, "desktop": "0", "geometry": [0, 0, 640, 360], "title": "Form"}

    def setUp(self):
        server.atspi_tree.STORE.clear()

    def nodes(self, *, field_value="", extra=()):
        item = AtspiTreeTests.item
        return [item(0, "frame", "Form"), item(1, "label", "Status: Ready", parent="ax_0"),
                item(2, "text", "Password", parent="ax_0", value=field_value, actions=["activate"]),
                item(3, "button", "Save", parent="ax_0", actions=["click"]), *extra]

    @staticmethod
    def answer(request, operation, target=None, input_id=None, verify=0.97, constraint=0.97, submit="NONE"):
        questions = request["questions"]

        def choice(name, selected):
            criteria = questions[name]["criteria"]
            others = [key for key in criteria if key != selected]
            probabilities = {key: 0.05 / len(others) for key in others} if others else {}
            probabilities[selected] = 0.95 if others else 1.0
            return {"choice": selected, "probabilities": probabilities, "confidence": 0.95}
        answers = {"operation": choice("operation", operation), "constraint_ok": {"noul": constraint}}
        if target:
            answers["target"] = choice("target", target)
        if input_id:
            answers["input"] = choice("input", input_id)
            answers["submit"] = choice("submit", submit)
        for name in questions:
            if name.startswith("verify_"):
                answers[name] = {"noul": verify}
        return {"answers": answers, "engine": "liquid/d1.2-ultra-20261116"}

    def run_task(self, task, trees, script, *, click=None, set_value=None):
        requests = []

        def evaluate(request):
            requests.append(request)
            return script(request, len(requests) - 1)
        args = {"target_pid": 77, "target_window_id": 4194311, **task}
        with patch.object(server, "list_windows", return_value=[self.window]), patch.object(
            server, "jev_route", return_value=("key", "endpoint", "model", None)
        ), patch.object(server.atspi_tree, "snapshot", side_effect=[("ax-t%d" % index, tree) for index, tree in enumerate(trees)]
        ), patch.object(server, "jev_evaluate", side_effect=evaluate), patch.object(
            server.atspi_tree, "click_element", side_effect=click or (lambda *a: {"ok": True, "effect": "ui_changed"})
        ) as clicked, patch.object(server.cua_native, "atspi_action", side_effect=set_value or (lambda *a: {"ok": True})) as written, patch.object(
            server.subtask.time, "sleep"
        ):
            result = json.loads(server.call_tool("run_subtask", args)["content"][0]["text"])
        return result, requests, clicked, written

    def test_types_a_secret_saves_and_never_leaks_the_secret(self):
        trees = [self.nodes(), self.nodes(field_value="hunter2"), self.nodes(field_value="hunter2")]
        script = lambda request, index: (self.answer(request, "SET_VALUE", "ax_2", "input_0") if index == 0
                                         else self.answer(request, "CLICK", "ax_3") if index == 1 else self.answer(request, "DONE"))
        task = {"goal": "Sign in", "inputs": {"password": "hunter2"}, "secret_inputs": ["password"], "verification": ["Signed in"]}
        result, requests, clicked, written = self.run_task(task, trees, script)
        self.assertEqual((result["status"], result["actions_taken"]), ("SUBTASK_COMPLETE", 2))
        self.assertEqual(written.call_args.args[2]["value"], "hunter2")
        self.assertEqual(written.call_args.args[2]["expected_state_token"], "ax-t0")
        self.assertEqual(clicked.call_args.args[1]["element_id"], "ax_3")
        # The value went into the field, yet neither the model nor the caller ever saw it.
        self.assertNotIn("hunter2", json.dumps(requests))
        self.assertNotIn("hunter2", json.dumps(result))
        self.assertEqual(result["history"][0]["engine"], "liquid/d1.2-ultra-20261116")
        self.assertIn("Status: Ready", json.dumps(requests[0]["state"]["texts"]))

    def test_risky_control_is_withheld_and_a_forged_choice_is_not_executed(self):
        item = AtspiTreeTests.item
        tree = self.nodes(extra=[item(4, "button", "Delete account", parent="ax_0", actions=["click"])])
        forged = lambda request, index: self.answer(request, "CLICK", "ax_3") | {"answers": {
            **self.answer(request, "CLICK", "ax_3")["answers"],
            "target": {"choice": "ax_4", "probabilities": {"ax_4": 1.0}, "confidence": 1.0}}}
        result, requests, clicked, _ = self.run_task({"goal": "Clean up", "verification": ["Done"]}, [tree], forged)
        self.assertNotIn("Delete account", json.dumps(requests[0]))
        self.assertEqual((result["status"], result["reason"]), ("NEEDS_AGENT", "invalid decision"))
        clicked.assert_not_called()

    def test_a_single_candidate_gets_none_and_choosing_it_stops_without_acting(self):
        tree = [AtspiTreeTests.item(0, "frame", "Form"), AtspiTreeTests.item(1, "button", "Save", parent="ax_0", actions=["click"])]
        result, requests, clicked, _ = self.run_task({"goal": "Save", "verification": ["Saved"]}, [tree],
                                                     lambda request, index: self.answer(request, "CLICK", "NONE"))
        # d1.2-ultra rejects a one-option choice with HTTP 422; NONE also lets the model refuse the only control.
        self.assertEqual(sorted(requests[0]["questions"]["target"]["criteria"]), ["NONE", "ax_1"])
        self.assertEqual((result["status"], result["reason"]), ("NEEDS_AGENT", "model chose none of the offered elements"))
        clicked.assert_not_called()

    def test_done_needs_every_verification_criterion(self):
        result, _, _, _ = self.run_task({"goal": "Save", "verification": ["Saved"]}, [self.nodes()],
                                        lambda request, index: self.answer(request, "DONE", verify=0.6))
        self.assertEqual((result["status"], result["reason"]), ("NEEDS_AGENT", "verification criterion not met: Saved"))

    def test_user_activity_stops_the_loop(self):
        def busy(*args):
            raise server.Unsupported("human_activity: recent pointer/keyboard activity on the target window")
        result, _, _, _ = self.run_task({"goal": "Save", "verification": ["Saved"]}, [self.nodes()],
                                        lambda request, index: self.answer(request, "CLICK", "ax_3"), click=busy)
        self.assertEqual((result["status"], result["reason"]), ("NEEDS_AGENT", "user took control"))

    def test_dry_run_plans_without_acting(self):
        result, _, clicked, written = self.run_task({"goal": "Save", "verification": ["Saved"], "dry_run": True}, [self.nodes()],
                                                    lambda request, index: self.answer(request, "CLICK", "ax_3"))
        self.assertEqual((result["status"], result["planned_action"]["target"]), ("DRY_RUN", "ax_3"))
        clicked.assert_not_called()
        written.assert_not_called()

    def test_invalid_subtasks_are_refused_without_echoing_inputs(self):
        for task, field in (({"goal": "x"}, "verification"), ({"goal": "x", "verification": ["v"], "shortcuts": {"MOD+S": "Save"}}, "shortcuts"),
                            ({"goal": "x", "verification": ["v"], "inputs": {"pin": "9921"}, "secret_inputs": ["code"]}, "secret_inputs")):
            with self.subTest(field=field), patch.object(server, "list_windows", return_value=[self.window]), patch.object(
                server, "jev_route", return_value=("key", "endpoint", "model", None)
            ), self.assertRaises(ValueError) as raised:
                server.call_tool("run_subtask", {"target_pid": 77, "target_window_id": 4194311, **task})
            self.assertIn(field, str(raised.exception))
            self.assertNotIn("9921", str(raised.exception))


class ElementCheckTests(unittest.TestCase):
    window = {"window_id": 4194311, "pid": 77, "desktop": "0", "geometry": [0, 0, 640, 360], "title": "Form"}

    def check(self, expect, nodes):
        with patch.dict(os.environ, {"DISPLAY": ":99", "XDG_SESSION_TYPE": "x11"}), patch.object(
            server, "list_windows", return_value=[self.window]
        ), patch.object(server.atspi_tree, "snapshot", return_value=("ax-t", nodes)):
            return json.loads(server.call_tool("verify_state", {"window_id": 4194311, "expect": expect})["content"][0]["text"])

    def test_element_checks_answer_from_a_fresh_accessibility_read(self):
        item = AtspiTreeTests.item
        nodes = [item(0, "frame", "Form"), item(1, "text", "Name", parent="ax_0", value="Ana", states=["focused"]),
                 item(2, "button", "Save", parent="ax_0", actions=["click"])]
        result = self.check({"element_exists": {"role": "button", "label": "Save"},
                             "element_value": {"role": "text", "label": "Name", "equals": "Ana"},
                             "focused_element": {"element_id": "ax_1"}}, nodes)
        self.assertEqual(result["status"], "pass")
        result = self.check({"element_exists": {"role": "button", "label": "Send"}}, nodes)
        self.assertEqual(result["status"], "fail")
        # A read cut at the node limit cannot prove absence, so a missing element is unknown, not fail.
        big = nodes + [item(index, "label", f"row {index}", parent="ax_0") for index in range(3, server.atspi_tree.MAX_NODES)]
        result = self.check({"element_exists": {"role": "button", "label": "Send"}}, big)
        self.assertEqual(result["status"], "unknown")
