import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("reproduce.py")
SPEC = importlib.util.spec_from_file_location("kairo_074_reproduce", MODULE_PATH)
reproduce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduce)


class ReproduceTests(unittest.TestCase):
    def test_provider_tool_round_then_answer_round(self):
        first = reproduce.provider_stream({"tools": [{"type": "function", "name": "demo-echo"}]})
        second = reproduce.provider_stream({"input": [{"type": "function_call_output"}]})
        self.assertIn("resp_round_1", first)
        self.assertIn("function_call", first)
        self.assertIn("resp_round_2", second)
        self.assertIn(reproduce.FINAL_TEXT, second)

    def test_provider_no_tool_control(self):
        body = reproduce.provider_stream({"input": "hello"})
        self.assertIn(reproduce.CONTROL_TEXT, body)
        self.assertNotIn("function_call", body)

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
                {"output_dir": directory, "python": "/missing", "expect_litellm": "1.100.0"},
            )()
            with self.assertRaisesRegex(reproduce.ReproductionError, "already exists"):
                reproduce.run(args)

    def test_validate_trigger_requires_sdk_failure_and_two_rounds(self):
        record = {
            "mode": "trigger",
            "client_request": {"path": "/v1/responses"},
            "client_response": {"status": 200},
            "upstream_exchanges": [{}, {}],
            "mcp_calls": [{"tool": "echo", "value": "PING"}],
            "consumer": {"error": {"type": "AssertionError"}, "final_response": None},
        }
        reproduce.validate_record(record)
        record["upstream_exchanges"] = [{}]
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(record)

    def test_client_request_shapes_change_only_the_trigger(self):
        trigger = reproduce.client_kwargs("trigger")
        approval = reproduce.client_kwargs("approval")
        self.assertEqual(trigger["input"], approval["input"])
        self.assertEqual(trigger["tool_choice"], approval["tool_choice"])
        trigger_tool = trigger["tools"][0]
        approval_tool = approval["tools"][0]
        self.assertEqual(
            {key: value for key, value in trigger_tool.items() if key != "require_approval"},
            {key: value for key, value in approval_tool.items() if key != "require_approval"},
        )
        self.assertEqual(trigger_tool["require_approval"], "never")
        self.assertEqual(approval_tool["require_approval"], "always")


if __name__ == "__main__":
    unittest.main()
