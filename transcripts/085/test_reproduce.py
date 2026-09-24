import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
import unittest

MODULE_PATH = Path(__file__).with_name("reproduce.py")
SPEC = importlib.util.spec_from_file_location("kairo_085_reproduce", MODULE_PATH)
reproduce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduce)

UPSTREAM_PATH = Path(__file__).with_name("upstream_server.py")
USPEC = importlib.util.spec_from_file_location("kairo_085_upstream_server", UPSTREAM_PATH)
upstream_server = importlib.util.module_from_spec(USPEC)
USPEC.loader.exec_module(upstream_server)

ORIGINAL_INPUT = "Append the line 'run' to the ledger."


def stream(model):
    return upstream_server.provider_stream({"model": model}, itertools.count(1)).decode()


def events(body):
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


class UpstreamServerTests(unittest.TestCase):
    def test_primary_tool_delivers_then_fails_in_band(self):
        body = stream("primary-tool")
        self.assertIn("response.output_item.done", body)
        self.assertIn('"status":"completed"', body)
        self.assertIn("event: error", body)
        self.assertNotIn("response.completed", body)

    def test_primary_tool_failed_uses_the_documented_terminal_event(self):
        body = stream("primary-tool-failed")
        self.assertIn("response.output_item.done", body)
        self.assertIn("event: response.failed", body)
        self.assertIn('"code":"server_error"', body)
        self.assertNotIn("event: error", body)

    def test_primary_tool_drop_has_no_terminal_event_and_drops(self):
        body = stream("primary-tool-drop")
        self.assertIn("response.output_item.done", body)
        self.assertNotIn("event: error", body)
        self.assertNotIn("response.failed", body)
        self.assertNotIn("response.completed", body)
        self.assertTrue(upstream_server.drops_connection("primary-tool-drop"))
        self.assertFalse(upstream_server.drops_connection("primary-tool"))

    def test_primary_announced_only_announces_the_call(self):
        types = [event["type"] for event in events(stream("primary-announced"))]
        self.assertEqual(types, ["response.created", "response.output_item.added", "error"])

    def test_primary_prefail_has_no_output_item(self):
        body = stream("primary-prefail")
        self.assertNotIn("output_item", body)
        self.assertIn("event: error", body)

    def test_primary_textpartial_streams_delta_then_fails(self):
        body = stream("primary-textpartial")
        self.assertIn("partial answer", body)
        self.assertIn("event: error", body)
        self.assertNotIn("response.completed", body)

    def test_primary_nofault_completes_cleanly(self):
        body = stream("primary-nofault")
        self.assertIn("response.completed", body)
        self.assertNotIn("event: error", body)

    def test_fallbacks_complete_and_reasoning_fallback_leads_with_reasoning(self):
        tool = stream("fallback-tool")
        self.assertIn("call_fallback", tool)
        self.assertIn("response.completed", tool)
        added = [(e["output_index"], e["item"]["type"]) for e in events(stream("fallback-reasoning-tool"))
                 if e["type"] == "response.output_item.added"]
        self.assertEqual(added, [(0, "reasoning"), (1, "function_call")])

    def test_unknown_model_rejected(self):
        with self.assertRaises(ValueError):
            upstream_server.provider_stream({"model": "not-a-scenario"}, itertools.count(1))


class ReproduceTests(unittest.TestCase):
    def test_safe_text_rejects_credentials(self):
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.safe_text(b'{"value":"sk-abcdefghijklmnopqrstuvwxyz"}')
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.safe_text(reproduce.MASTER_KEY.encode())
        self.assertEqual(reproduce.safe_text(b'{"value":"PING"}'), '{"value":"PING"}')
        # A random base64url id that happens to contain "sk-" is not a key.
        embedded = b'{"id":"resp_V4gSask-y2cSKJcc120mnoHmwenPPHj8Diz"}'
        self.assertEqual(reproduce.safe_text(embedded), embedded.decode())
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.safe_text(b'{"authorization":"Bearer sk-y2cSKJcc120mnoHmwenPPHj8Diz"}')

    def test_output_directory_must_be_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            args = type(
                "Args",
                (),
                {"output_dir": directory, "python": "/missing", "expect_litellm": "1.102.1",
                 "captured_at": "2026-09-24", "sdk_python": [], "note": None},
            )()
            with self.assertRaisesRegex(reproduce.ReproductionError, "already exists"):
                reproduce.run(args)

    def test_version_tuple_orders_sdk_releases(self):
        self.assertLess(reproduce.version_tuple("3.13.0"), reproduce.SDK_INDEX_FIX)
        self.assertLess(reproduce.version_tuple("2.54.0"), reproduce.SDK_INDEX_FIX)
        self.assertGreaterEqual(reproduce.version_tuple("3.14.0"), reproduce.SDK_INDEX_FIX)
        self.assertGreaterEqual(reproduce.version_tuple("3.19.2"), reproduce.SDK_INDEX_FIX)

    @staticmethod
    def consumer(error=None, calls=(), created=2, final=True):
        return {
            "error": error,
            "final_response": {"id": "resp_x"} if final and not error else None,
            "completed_tool_call_ids": list(calls),
            "created_ids": [f"resp_{i}" for i in range(created)],
        }

    def record(self, mode, upstream_count, consumer, openai="2.54.0", replays=(), fallback_input=ORIGINAL_INPUT,
               status=200, transport="complete"):
        request = {"model": mode, "stream": True, "input": ORIGINAL_INPUT, "tool_choice": reproduce.MODES[mode][2]}
        upstream = [{"body_raw": json.dumps({"input": ORIGINAL_INPUT}), "transport": transport}]
        upstream += [{"body_raw": json.dumps({"input": fallback_input}), "transport": "complete"}] * (upstream_count - 1)
        return {
            "target": {"project": "BerriAI/litellm", "version": "1.102.1"},
            "mode": mode,
            "trial": 1,
            "client_request": {"path": "/v1/responses", "body_raw": json.dumps(request)},
            "client_response": {"status": status},
            "upstream_exchanges": upstream[:upstream_count],
            "consumer_openai": openai,
            "consumer": consumer,
            "sdk_replays": [{"openai": version, "consumer": result} for version, result in replays],
        }

    def test_duplicate_tool_requires_the_call_twice_on_every_sdk(self):
        twice = self.consumer(calls=["call_primary", "call_fallback"])
        reproduce.validate_record(self.record("trigger-duplicate-tool", 2, twice, replays=[("3.19.2", twice)]))
        once = self.consumer(calls=["call_fallback"])
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(self.record("trigger-duplicate-tool", 2, twice, replays=[("3.19.2", once)]))

    def test_duplicate_tool_requires_the_fallback_to_get_the_original_input(self):
        twice = self.consumer(calls=["call_primary", "call_fallback"])
        with self.assertRaisesRegex(reproduce.ReproductionError, "original input"):
            reproduce.validate_record(self.record("trigger-duplicate-tool", 2, twice, fallback_input=[{"role": "developer"}]))

    def test_sdk_crash_depends_on_the_sdk_version(self):
        crash = self.consumer(error={"type": "AssertionError", "message": ""}, calls=["call_primary"])
        tolerated = self.consumer(calls=["call_primary", "call_fallback"])
        reproduce.validate_record(self.record("trigger-sdk-crash-item-type", 2, crash,
                                              replays=[("3.13.0", crash), ("3.14.0", tolerated)]))
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(self.record("trigger-sdk-crash-item-type", 2, crash, replays=[("3.14.0", crash)]))
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(self.record("trigger-sdk-crash-item-type", 2, tolerated))

    def test_partial_text_trigger_requires_the_continuation_path(self):
        crash = self.consumer(error={"type": "AssertionError", "message": ""})
        continuation = [{"type": "message", "role": "developer"}]
        reproduce.validate_record(self.record("trigger-sdk-crash-partial-text", 2, crash, fallback_input=continuation))
        with self.assertRaisesRegex(reproduce.ReproductionError, "continuation"):
            reproduce.validate_record(self.record("trigger-sdk-crash-partial-text", 2, crash))

    def test_no_fault_control_requires_single_upstream_call(self):
        once = self.consumer(calls=["call_primary"], created=1)
        reproduce.validate_record(self.record("control-no-fault", 1, once))
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(self.record("control-no-fault", 2, once))

    def test_fault_before_output_control_requires_single_completed_call(self):
        reproduce.validate_record(self.record("control-fault-before-output", 2, self.consumer(calls=["call_fallback"])))
        twice = self.consumer(calls=["call_primary", "call_fallback"])
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(self.record("control-fault-before-output", 2, twice))

    def test_transport_drop_must_not_reach_the_fallback(self):
        dropped = self.consumer(error={"type": "APIError", "message": ""}, calls=["call_primary"])
        reproduce.validate_record(self.record("boundary-transport-drop", 1, dropped,
                                              transport="closed-before-declared-length"))
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(self.record("boundary-transport-drop", 2, dropped,
                                                  transport="closed-before-declared-length"))
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(self.record("boundary-transport-drop", 1, self.consumer(calls=["call_primary"]),
                                                  transport="closed-before-declared-length"))

    def test_validate_rejects_unknown_mode(self):
        record = self.record("control-no-fault", 1, self.consumer(calls=["call_primary"], created=1))
        record["mode"] = "not-a-mode"
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(record)


if __name__ == "__main__":
    unittest.main()
