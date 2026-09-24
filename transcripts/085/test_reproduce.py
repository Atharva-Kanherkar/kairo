import importlib.util
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


class UpstreamServerTests(unittest.TestCase):
    def test_primary_tool_delivers_then_fails(self):
        import itertools

        body = upstream_server.provider_stream({"model": "primary-tool"}, itertools.count(1)).decode()
        self.assertIn("response.output_item.done", body)
        self.assertIn('"status":"completed"', body)
        self.assertIn("event: error", body)
        self.assertNotIn("response.completed", body)

    def test_primary_nofault_completes_cleanly(self):
        import itertools

        body = upstream_server.provider_stream({"model": "primary-nofault"}, itertools.count(1)).decode()
        self.assertIn("response.completed", body)
        self.assertNotIn("event: error", body)

    def test_primary_prefail_has_no_output_item(self):
        import itertools

        body = upstream_server.provider_stream({"model": "primary-prefail"}, itertools.count(1)).decode()
        self.assertNotIn("output_item", body)
        self.assertIn("event: error", body)

    def test_primary_textpartial_streams_delta_then_fails(self):
        import itertools

        body = upstream_server.provider_stream({"model": "primary-textpartial"}, itertools.count(1)).decode()
        self.assertIn("partial answer", body)
        self.assertIn("event: error", body)
        self.assertNotIn("response.completed", body)

    def test_fallback_tool_and_text_both_complete(self):
        import itertools

        tool = upstream_server.provider_stream({"model": "fallback-tool"}, itertools.count(1)).decode()
        text = upstream_server.provider_stream({"model": "fallback-text"}, itertools.count(1)).decode()
        self.assertIn("call_fallback", tool)
        self.assertIn("response.completed", tool)
        self.assertIn('"type":"message"', text)
        self.assertIn("response.completed", text)

    def test_unknown_model_rejected(self):
        import itertools

        with self.assertRaises(ValueError):
            upstream_server.provider_stream({"model": "not-a-scenario"}, itertools.count(1))


class ReproduceTests(unittest.TestCase):
    def test_safe_text_rejects_credentials(self):
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.safe_text(b'{"value":"sk-abcdefghijklmnopqrstuvwxyz"}')
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.safe_text(reproduce.MASTER_KEY.encode())
        self.assertEqual(reproduce.safe_text(b'{"value":"PING"}'), '{"value":"PING"}')

    def test_output_directory_must_be_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            args = type(
                "Args",
                (),
                {"output_dir": directory, "python": "/missing", "expect_litellm": "1.102.1", "captured_at": "2026-09-24"},
            )()
            with self.assertRaisesRegex(reproduce.ReproductionError, "already exists"):
                reproduce.run(args)

    def _record(self, mode, upstream_exchanges, error, tool_call_ids, extra_request=None, created_ids=None):
        request = {"model": mode, "stream": True}
        if extra_request:
            request.update(extra_request)
        if created_ids is None:
            created_ids = [f"resp_{i}" for i in range(upstream_exchanges)]
        return {
            "target": {"project": "BerriAI/litellm", "version": "1.102.1"},
            "mode": mode,
            "trial": 1,
            "client_request": {"path": "/v1/responses", "body_raw": json.dumps(request)},
            "client_response": {"status": 200},
            "upstream_exchanges": [{}] * upstream_exchanges,
            "consumer": {
                "error": error,
                "final_response": None if error else {"id": "resp_x"},
                "completed_tool_call_ids": tool_call_ids,
                "created_ids": created_ids,
            },
        }

    def test_validate_duplicate_tool_requires_two_upstream_calls_and_two_tool_calls(self):
        good = self._record("trigger-duplicate-tool", 2, None, ["call_primary", "call_fallback"])
        reproduce.validate_record(good)  # does not raise
        bad = self._record("trigger-duplicate-tool", 2, None, ["call_primary"])
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(bad)

    def test_validate_sdk_crash_requires_assertion_error(self):
        good = self._record(
            "trigger-sdk-crash-item-type", 2, {"type": "AssertionError", "message": ""}, []
        )
        reproduce.validate_record(good)
        bad = self._record("trigger-sdk-crash-item-type", 2, None, ["call_primary"])
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(bad)

    def test_validate_no_fault_control_requires_single_upstream_call(self):
        good = self._record("control-no-fault", 1, None, ["call_primary"])
        reproduce.validate_record(good)
        bad = self._record("control-no-fault", 2, None, ["call_primary"])
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(bad)

    def test_validate_pre_first_chunk_control_requires_single_completed_call(self):
        good = self._record("control-pre-first-chunk", 2, None, ["call_fallback"])
        reproduce.validate_record(good)
        bad = self._record("control-pre-first-chunk", 2, None, ["call_primary", "call_fallback"])
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(bad)

    def test_validate_rejects_unknown_mode(self):
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(self._record("not-a-mode", 1, None, []))


if __name__ == "__main__":
    unittest.main()
