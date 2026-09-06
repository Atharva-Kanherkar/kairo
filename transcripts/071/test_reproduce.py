"""Regression tests for transcripts/071/reproduce.py.

These tests exercise the reproduction script's helper functions directly and
do not require a running LiteLLM process. Run with:

    python3 -m unittest discover -s transcripts/071 -p 'test*.py'
    python3 -O -m unittest discover -s transcripts/071 -p 'test*.py'

The `-O` run matters: validate_results() must reject a bad result identically
with and without assertions enabled, since it uses explicit `if`/`raise`
rather than `assert`.
"""

import importlib.util
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock, patch

MODULE_PATH = Path(__file__).resolve().parent / "reproduce.py"
_spec = importlib.util.spec_from_file_location("kairo_071_reproduce", MODULE_PATH)
reproduce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reproduce)


class FakeProcess:
    """Stand-in for subprocess.Popen that reports an immediate exit."""

    def __init__(self, exit_code):
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code


class SanitizeDiagnosticTests(unittest.TestCase):
    def test_redacts_bearer_token(self):
        raw = b"connecting with Authorization: Bearer sk-super-secret-123 to upstream"
        out = reproduce.sanitize_diagnostic(raw)
        self.assertNotIn("sk-super-secret-123", out)
        self.assertIn("[REDACTED]", out)

    def test_redacts_api_key_field(self):
        raw = b'config: {"api_key": "sk-x-CANARY_DEPLOYMENT_API_KEY"}'
        out = reproduce.sanitize_diagnostic(raw)
        self.assertNotIn("CANARY_DEPLOYMENT_API_KEY", out)

    def test_redacts_query_key(self):
        raw = b"fetching http://127.0.0.1:9996/v1?key=CANARY_QUERY_KEY_IN_API_BASE now"
        out = reproduce.sanitize_diagnostic(raw)
        self.assertNotIn("CANARY_QUERY_KEY_IN_API_BASE", out)

    def test_leaves_ordinary_text_alone(self):
        raw = b"Uvicorn running on http://127.0.0.1:4010"
        self.assertEqual(reproduce.sanitize_diagnostic(raw), raw.decode())


class EnsurePortAvailableTests(unittest.TestCase):
    def test_fails_fast_on_occupied_port(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            with self.assertRaises(reproduce.ReproductionError) as ctx:
                reproduce.ensure_port_available("127.0.0.1", port)
            self.assertIn(str(port), str(ctx.exception))
        finally:
            blocker.close()

    def test_succeeds_on_free_port(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        reproduce.ensure_port_available("127.0.0.1", port)


class WaitForReadyTests(unittest.TestCase):
    def test_timeout_includes_startup_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "startup.log"
            log_path.write_bytes(b"config error: model is missing\n")
            with self.assertRaisesRegex(reproduce.ReproductionError, "config error"):
                reproduce.wait_for_ready(FakeProcess(None), str(log_path), timeout=0)

    def test_process_exit_during_ready_request_is_not_success(self):
        proc = Mock()
        proc.poll.side_effect = [None, 1, 1]
        with (
            patch.object(
                reproduce, "raw_http_request", return_value=(200, b"", b"", b"")
            ),
            self.assertRaisesRegex(
                reproduce.ReproductionError, "exited during startup"
            ),
        ):
            reproduce.wait_for_ready(proc, "/nonexistent/startup.log", timeout=2)

    def test_detects_early_process_exit_without_waiting_full_timeout(self):
        fake_proc = FakeProcess(exit_code=1)
        log_path = None
        try:
            fd, log_path = __import__("tempfile").mkstemp()
            os.write(fd, b"litellm: config error\n")
            os.close(fd)
            start = __import__("time").time()
            with self.assertRaises(reproduce.ReproductionError) as ctx:
                reproduce.wait_for_ready(fake_proc, log_path, timeout=20)
            elapsed = __import__("time").time() - start
            self.assertLess(
                elapsed, 5, "must detect early exit without waiting out the timeout"
            )
            self.assertIn("exited during startup", str(ctx.exception))
        finally:
            if log_path:
                os.unlink(log_path)


class StartLitellmTests(unittest.TestCase):
    def test_missing_executable_gives_actionable_diagnostic(self):
        original_bin = reproduce.LITELLM_BIN
        reproduce.LITELLM_BIN = "/nonexistent/path/to/litellm"
        try:
            with self.assertRaises(reproduce.ReproductionError) as ctx:
                reproduce.start_litellm("/nonexistent/work-dir")
            self.assertIn("not found", str(ctx.exception))
        finally:
            reproduce.LITELLM_BIN = original_bin

    def test_version_mismatch_never_starts_process(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(reproduce.os.path, "isfile", return_value=True),
            patch.object(reproduce, "ensure_port_available"),
            patch.object(
                reproduce.subprocess, "run", return_value=Mock(stdout="1.98.0")
            ),
            patch.object(reproduce.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(
                reproduce.ReproductionError, "expected LiteLLM 1.99.0"
            ):
                reproduce.start_litellm(directory)
            popen.assert_not_called()

    def test_child_environment_and_cwd_exclude_ambient_credentials(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"OPENAI_API_KEY": "CANARY_AMBIENT_KEY"}),
            patch.object(reproduce.os.path, "isfile", return_value=True),
            patch.object(reproduce, "ensure_port_available"),
            patch.object(
                reproduce.subprocess, "run", return_value=Mock(stdout="1.99.0")
            ),
            patch.object(reproduce.subprocess, "Popen") as popen,
        ):
            _, log_file, log_path = reproduce.start_litellm(directory)
            log_file.close()
            os.unlink(log_path)
            self.assertEqual(popen.call_args.kwargs["cwd"], directory)
            self.assertNotIn("OPENAI_API_KEY", popen.call_args.kwargs["env"])


class ValidateResultsTests(unittest.TestCase):
    GOOD: ClassVar[dict[str, int]] = {
        "model_info_canary_present": 5,
        "model_info_v1_canary_present": 5,
        "models_control_clean": 5,
        "liveliness_control_clean": 5,
        "runs": 5,
    }

    def test_passes_on_full_determinism(self):
        reproduce.validate_results(dict(self.GOOD))

    def test_rejects_any_partial_count(self):
        for key in (
            "model_info_canary_present",
            "model_info_v1_canary_present",
            "models_control_clean",
            "liveliness_control_clean",
            "runs",
        ):
            with self.subTest(key=key):
                bad = dict(self.GOOD)
                bad[key] = 4
                with self.assertRaises(reproduce.ReproductionError):
                    reproduce.validate_results(bad)

    def test_uses_if_raise_not_bare_assert(self):
        # A bare `assert` would be stripped under python3 -O and silently pass.
        # validate_results must still raise here even if this test file itself
        # is executed with -O, proving the check does not rely on assertions.
        bad = dict(self.GOOD)
        bad["model_info_canary_present"] = 0
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.validate_results(bad)


class WriteFixturesTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.output_dir = tempfile.mkdtemp(prefix="kairo-071-test-")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_all_files_written_on_success(self):
        reproduce.write_fixtures(
            self.output_dir,
            {"a.json": "{}\n", "b.http": b"raw bytes"},
        )
        with open(os.path.join(self.output_dir, "a.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{}\n")
        with open(os.path.join(self.output_dir, "b.http"), "rb") as f:
            self.assertEqual(f.read(), b"raw bytes")

    def test_partial_failure_leaves_existing_evidence_untouched(self):
        existing_path = os.path.join(self.output_dir, "existing.json")
        with open(existing_path, "w", encoding="utf-8") as f:
            f.write("ORIGINAL EVIDENCE")

        class Unwritable:
            """Neither bytes nor str: triggers a TypeError inside open().write()."""

        with self.assertRaises(TypeError):
            reproduce.write_fixtures(
                self.output_dir,
                {"new.json": "{}\n", "bad.json": Unwritable()},
            )

        # Pre-existing evidence must be exactly as it was.
        with open(existing_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "ORIGINAL EVIDENCE")
        # Nothing from the failed batch should have landed either.
        self.assertFalse(os.path.exists(os.path.join(self.output_dir, "new.json")))
        self.assertFalse(os.path.exists(os.path.join(self.output_dir, "bad.json")))
        # No leftover staging directory.
        entries = os.listdir(self.output_dir)
        self.assertEqual(entries, ["existing.json"])


class HttpParsingTests(unittest.TestCase):
    def test_truncated_content_length_is_rejected(self):
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.parse_http_response(
                b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n{}"
            )

    def test_capture_preserves_actual_request_and_response_bytes(self):
        response = (
            b"HTTP/1.1 200 OK\r\nX-Test:  untouched\r\nContent-Length: 2\r\n\r\n{}"
        )
        received = []
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(5)

            def serve():
                with server.accept()[0] as client:
                    client.settimeout(5)
                    request = b""
                    while b"\r\n\r\n" not in request:
                        request += client.recv(4096)
                    received.append(request)
                    client.sendall(response)

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                with patch.object(reproduce, "PORT", server.getsockname()[1]):
                    status, body, request, actual_response = reproduce.raw_http_request(
                        "/v1/model/info"
                    )
            finally:
                worker.join(timeout=6)
        self.assertFalse(worker.is_alive())
        self.assertEqual((status, body), (200, b"{}"))
        self.assertEqual(received, [request])
        self.assertTrue(request.startswith(b"GET /v1/model/info HTTP/1.1\r\n"))
        self.assertEqual(actual_response, response)

    def test_parse_http_response_extracts_status_and_body(self):
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"content-type: application/json\r\n"
            b"content-length: 13\r\n"
            b"\r\n"
            b'{"ok": true}\n'
        )
        status, body = reproduce.parse_http_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"ok": true}\n')

    def test_parse_http_response_dechunks(self):
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"transfer-encoding: chunked\r\n"
            b"\r\n"
            b"4\r\n"
            b"Wiki\r\n"
            b"5\r\n"
            b"pedia\r\n"
            b"0\r\n"
            b"\r\n"
        )
        status, body = reproduce.parse_http_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"Wikipedia")

    def test_malformed_response_raises_reproduction_error(self):
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.parse_http_response(b"not an http response")


class EnvelopeTests(unittest.TestCase):
    def test_make_envelope_round_trips(self):
        body = b'{"data": [{"litellm_params": {"api_base": "http://x/?key=CANARY_QUERY_KEY_IN_API_BASE"}}]}'
        envelope = reproduce.make_envelope("/model/info", 200, body)
        self.assertEqual(envelope["request_path"], "/model/info")
        self.assertEqual(envelope["status"], 200)
        self.assertTrue(reproduce.has_canary_in_api_base(envelope))

    def test_make_envelope_rejects_non_json_body(self):
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.make_envelope("/model/info", 200, b"not json")

    def test_has_canary_in_api_base_false_when_no_data(self):
        envelope = {"request_path": "/v1/models", "status": 200, "body": {"data": []}}
        self.assertFalse(reproduce.has_canary_in_api_base(envelope))

    def test_envelope_contains_scans_whole_body(self):
        envelope = {
            "request_path": "/v1/models",
            "status": 200,
            "body": {"data": [{"id": "leaked-CANARY_QUERY_KEY_IN_API_BASE"}]},
        }
        self.assertTrue(reproduce.envelope_contains(envelope, reproduce.CANARY))


class CaptureFailureTests(unittest.TestCase):
    def test_failed_probes_preserve_every_existing_fixture(self):
        # Exercise capture()'s actual ordering, not just validate_results() in isolation.
        for failure in ("partial", "status", "control"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                names = [
                    f"{key}.{ext}"
                    for _, key in reproduce.ROUTES
                    for ext in ("json", "http")
                ]
                names.append("client-results.json")
                before = {name: b"ORIGINAL EVIDENCE" for name in names}
                for name, content in before.items():
                    (Path(directory) / name).write_bytes(content)
                calls = {path: 0 for path, _ in reproduce.ROUTES}

                def request(path, failure=failure, calls=calls):
                    calls[path] += 1
                    status = (
                        404 if failure == "status" and path == "/v1/model/info" else 200
                    )
                    if path in ("/model/info", "/v1/model/info"):
                        api_base = "http://x/?key=" + reproduce.CANARY
                        if (
                            failure == "partial"
                            and path == "/model/info"
                            and calls[path] == 3
                        ):
                            api_base = "http://x/"
                        body = {"data": [{"litellm_params": {"api_base": api_base}}]}
                    else:
                        body = {"data": [{"id": "mock-query-key"}]}
                        if failure == "control" and path == "/v1/models":
                            body["data"][0]["id"] = "CANARY_OTHER_SECRET"
                    return status, json.dumps(body).encode(), b"request", b"response"

                log_path = Path(directory) / "startup.log"
                log_path.touch()
                proc = Mock()
                proc.poll.return_value = None
                with (
                    patch.object(
                        reproduce,
                        "start_litellm",
                        return_value=(proc, io.BytesIO(), str(log_path)),
                    ),
                    patch.object(reproduce, "wait_for_ready"),
                    patch.object(reproduce, "raw_http_request", side_effect=request),
                    self.assertRaises(reproduce.ReproductionError),
                ):
                    reproduce.capture(directory, directory)
                self.assertEqual(
                    {p.name: p.read_bytes() for p in Path(directory).iterdir()}, before
                )


if __name__ == "__main__":
    unittest.main()
