"""Regression tests for transcripts/072/reproduce.py.

These tests exercise reproduction helper functions and validation logic directly
without requiring a running Bifrost process.

Run with:
    python3 -m unittest transcripts/072/test_reproduce.py
    python3 -O -m unittest transcripts/072/test_reproduce.py
"""

import importlib.util
import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

MODULE_PATH = Path(__file__).resolve().parent / "reproduce.py"
_spec = importlib.util.spec_from_file_location("kairo_072_reproduce", MODULE_PATH)
reproduce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reproduce)


class PortAvailabilityTests(unittest.TestCase):
    def test_detects_occupied_port(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        _, port = srv.getsockname()
        try:
            with self.assertRaises(reproduce.ReproductionError) as ctx:
                reproduce.ensure_port_available("127.0.0.1", port)
            self.assertIn(f"port {port}", str(ctx.exception))
        finally:
            srv.close()

    def test_passes_available_port(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        _, port = srv.getsockname()
        srv.close()
        reproduce.ensure_port_available("127.0.0.1", port)


class WaitForReadyTests(unittest.TestCase):
    def test_fails_fast_on_process_exit(self):
        fake_proc = Mock()
        fake_proc.poll.return_value = 1
        fake_proc.returncode = 1
        with self.assertRaises(reproduce.ReproductionError) as ctx:
            reproduce.wait_for_ready("http://127.0.0.1:9999", fake_proc, timeout=1.0)
        self.assertIn("exited early with code 1", str(ctx.exception))


class ValidationTests(unittest.TestCase):
    def test_reproduction_error_type(self):
        err = reproduce.ReproductionError("something failed")
        self.assertIsInstance(err, Exception)
        self.assertEqual(str(err), "something failed")

    def records(self, name="live/responses-any.jsonl"):
        return [
            json.loads(line)
            for line in (MODULE_PATH.parent / name).read_text().splitlines()
        ]

    def test_all_saved_exchanges(self):
        for mode in ("local", "live"):
            files = sorted((MODULE_PATH.parent / mode).glob("*.jsonl"))
            self.assertEqual(len(files), 10 if mode == "local" else 8)
            metadata = json.loads(
                (MODULE_PATH.parent / mode / "metadata.json").read_text()
            )
            self.assertTrue(metadata["complete"])
            self.assertEqual(metadata["target"]["revision"], reproduce.REVISION)
            for path in files:
                with self.subTest(path=path.name, mode=mode):
                    reproduce.validate_records(
                        self.records(f"{mode}/{path.name}"), mode == "live"
                    )

    def test_bad_later_trial_is_not_hidden(self):
        records = self.records()
        records[-1]["body"]["tool_choice"] = "required"
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_records(records, True)

    def test_missing_or_duplicate_trials_fail(self):
        for records in (self.records()[:1], [self.records()[0]] * 5):
            with self.assertRaises(reproduce.ReproductionError):
                reproduce.validate_records(records, True)

    def test_wrong_rejection_cannot_prove_bug(self):
        record = self.records()[0]
        error = json.loads(record["upstream_response"]["body_raw"])
        error["error"]["param"] = "model"
        record["upstream_response"]["body_raw"] = json.dumps(error)
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(record, True)

    def test_wrong_route_is_rejected(self):
        record = self.records()[0]
        record["path"] = "/v1/chat/completions"
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(record, True)

    def test_sdk_must_not_dispatch_after_rejection(self):
        record = self.records()[0]
        record["consumer"]["tool_dispatches"] = 1
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_record(record, True)

    def test_direct_control_changes_only_choice(self):
        for mode in ("local", "live"):
            for profile in ("responses", "chat"):
                trigger = self.records(f"{mode}/{profile}-any.jsonl")[-1]["body"]
                expected = copy.deepcopy(trigger)
                expected["tool_choice"] = "required"
                for record in self.records(f"{mode}/{profile}-direct-required.jsonl"):
                    self.assertEqual(record["body"], expected)

    def test_named_control_changes_only_choice(self):
        for mode in ("local", "live"):
            for profile in ("responses", "chat"):
                for trigger, control in zip(
                    self.records(f"{mode}/{profile}-any.jsonl"),
                    self.records(f"{mode}/{profile}-named.jsonl"),
                ):
                    expected = json.loads(trigger["client_request"]["body_raw"])
                    expected["tool_choice"] = {"type": "tool", "name": "get_weather"}
                    self.assertEqual(
                        json.loads(control["client_request"]["body_raw"]), expected
                    )

    def test_sensitive_body_refuses_capture(self):
        for raw, secret in (
            (b'{"echo":"not-a-real-key"}', "not-a-real-key"),
            (b'{"echo":"sk-example-not-a-key"}', ""),
        ):
            with self.assertRaises(reproduce.ReproductionError):
                reproduce.safe_raw(raw, secret)
        raw = b'{ "test": "unchanged" }\n'
        self.assertEqual(reproduce.safe_raw(raw).encode(), raw)

    def test_only_safe_headers_are_retained(self):
        self.assertEqual(
            reproduce.safe_headers(
                [
                    ("Content-Type", "application/json"),
                    ("Authorization", "do-not-capture"),
                    ("OpenAI-Organization", "private"),
                    ("Set-Cookie", "private"),
                ]
            ),
            {"content-type": "application/json"},
        )

    def test_binary_revision_and_dirty_build_are_rejected(self):
        for text in (
            "vcs.revision=wrong",
            f"vcs.revision={reproduce.REVISION}\nvcs.modified=true",
        ):
            with patch.object(reproduce.subprocess, "check_output", return_value=text):
                with self.assertRaises(reproduce.ReproductionError):
                    reproduce.verify_binary("unused")

    def test_dotenv_is_parsed_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("OPENAI_API_KEY='$(not-executed)'\n")
            with patch.dict(reproduce.os.environ, {}, clear=True):
                self.assertEqual(reproduce.load_key(path), "$(not-executed)")

    def test_authenticated_upstream_cannot_change_destination(self):
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.upstream(
                "https://untrusted.invalid/", b"{}", True, "not-a-real-key"
            )

    def test_sweep_rejects_malformed_tool_choices(self):
        probe_path = MODULE_PATH.parents[2] / "tools/sweep/probes.py"
        spec = importlib.util.spec_from_file_location("issue072_probes", probe_path)
        probes = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probes)
        probe = next(p for p in probes.PROBES if p.id == "req.tool_choice.any")
        self.assertEqual(
            probe.expect({"tool_choice": "required"}, {}, {}), probes.PRESERVED
        )
        for choice in (
            None,
            False,
            "any",
            "auto",
            {"mode": "required"},
            {"type": "required"},
            {"type": "any", "mode": "required"},
        ):
            with self.subTest(choice=choice):
                self.assertEqual(
                    probe.expect({"tool_choice": choice}, {}, {}), probes.MANGLED
                )


if __name__ == "__main__":
    unittest.main()
