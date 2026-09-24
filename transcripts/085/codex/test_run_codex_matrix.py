import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "kairo_085_codex_matrix", Path(__file__).with_name("run_codex_matrix.py")
)
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)

TOOLS = [{"type": "function", "name": "exec_command", "parameters": {"type": "object"}}]


def events(body):
    return [json.loads(line[6:]) for line in body.decode().splitlines() if line.startswith("data: ")]


class ExternalizeTests(unittest.TestCase):
    BODY = ('{"model": "m", "instructions": "say \\"tools\\": no", "input": [{"tools": [1], "text": "x"}], '
            '"tools": [{"type": "function", "name": "exec_command", "nested": {"instructions": "keep"}}], "stream": true}')

    def test_round_trip_is_byte_exact_and_only_touches_top_level_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            externalized = matrix.externalize(self.BODY, shared)
            self.assertEqual(len(list(shared.iterdir())), 2)
            self.assertIn('"input": [{"tools": [1], "text": "x"}]', externalized)
            self.assertNotIn("exec_command", externalized)
            self.assertEqual(matrix.restore(externalized, shared), self.BODY)
            self.assertEqual(json.loads(externalized)["model"], "m")

    def test_restore_rejects_a_tampered_shared_file(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            externalized = matrix.externalize(self.BODY, shared)
            victim = next(shared.glob("tools-*.json"))
            victim.write_text("[]", encoding="utf-8")
            with self.assertRaises(matrix.RunError):
                matrix.restore(externalized, shared)

    def test_body_without_the_keys_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            body = '{"model": "m", "input": "hi"}'
            self.assertEqual(matrix.externalize(body, Path(directory)), body)

    def test_sanitize_marks_temp_paths_and_encrypted_reasoning(self):
        text = '{"cwd": "/private/var/t/kairo-085-codex-x/work", "encrypted_content": "gAAAA\\"B"}'
        clean = matrix.sanitize(text, ["/private/var/t/kairo-085-codex-x"])
        self.assertEqual(json.loads(clean), {"cwd": "[REDACTED:tmpdir]/work", "encrypted_content": "[REDACTED:encrypted_content]"})


class UpstreamTests(unittest.TestCase):
    def test_faulting_primary_completes_one_call_then_fails(self):
        body = matrix.provider_stream({"model": "codex-primary-fault", "tools": TOOLS, "input": []}, "resp_1")
        types = [event["type"] for event in events(body)]
        self.assertEqual(types[-1], "error")
        self.assertIn("response.output_item.done", types)
        self.assertNotIn("response.completed", types)
        done = [e for e in events(body) if e["type"] == "response.output_item.done"][0]["item"]
        self.assertEqual(done["name"], "exec_command")
        self.assertEqual(json.loads(done["arguments"]), {"cmd": "echo run >> ledger.txt"})

    def test_prefail_primary_has_no_output_item(self):
        body = matrix.provider_stream({"model": "codex-primary-prefail", "tools": TOOLS, "input": []}, "resp_1")
        self.assertEqual([e["type"] for e in events(body)], ["response.created", "error"])

    def test_tool_result_turn_gets_a_final_message(self):
        request = {"model": "codex-primary-fault", "tools": TOOLS,
                   "input": [{"type": "function_call_output", "call_id": "call_primary", "output": ""}]}
        types = [event["type"] for event in events(matrix.provider_stream(request, "resp_2"))]
        self.assertEqual(types[-1], "response.completed")
        self.assertNotIn("error", types)

    def test_unknown_model_and_missing_shell_tool_are_rejected(self):
        with self.assertRaises(ValueError):
            matrix.provider_stream({"model": "nope", "tools": TOOLS, "input": []}, "resp_1")
        with self.assertRaises(ValueError):
            matrix.provider_stream({"model": "codex-fallback", "tools": [], "input": []}, "resp_1")

    def test_check_clean_rejects_credentials(self):
        with self.assertRaises(matrix.RunError):
            matrix.check_clean("sk-" + "a" * 30, [])
        with self.assertRaises(matrix.RunError):
            matrix.check_clean("value secret-from-env", ["secret-from-env"])
        matrix.check_clean('{"model": "m"}', ["secret-from-env"])
        matrix.check_clean('{"id": "resp_V4gSask-y2cSKJcc120mnoHmwenPPHj8Diz"}', [])


if __name__ == "__main__":
    unittest.main()
