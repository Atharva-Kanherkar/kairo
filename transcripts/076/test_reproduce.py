import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("reproduce.py")
SPEC = importlib.util.spec_from_file_location("kairo_076_reproduce", MODULE_PATH)
reproduce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduce)


class ReproduceTests(unittest.TestCase):
    def test_python_executable_symlink_is_not_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "python-real"
            target.touch()
            link = root / "python-venv"
            link.symlink_to(target)
            selected = reproduce.absolute_without_resolving(link)
            self.assertEqual(selected, link)
            self.assertTrue(selected.is_symlink())

    def test_sanitize_bytes_replaces_every_canary(self):
        canaries = {name: f"secret-{name}" for name in reproduce.MARKERS}
        raw = " ".join(canaries.values()).encode()
        sanitized = reproduce.sanitize_bytes(raw, canaries).decode()
        for name, marker in reproduce.MARKERS.items():
            self.assertIn(marker, sanitized)
            self.assertNotIn(canaries[name], sanitized)

    def test_require_no_canaries_refuses_unsanitized_value(self):
        canaries = {name: f"secret-{name}" for name in reproduce.MARKERS}
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.require_no_canaries(b"Authorization: Bearer secret-server", canaries)
        reproduce.require_no_canaries(b"Authorization: Bearer [SERVER_PROVIDER_CREDENTIAL]", canaries)

    def test_parse_http_rejects_empty_and_malformed_captures(self):
        for raw in (b"", b"POST / HTTP/1.1\n\n", b"not-http\r\n\r\n"):
            with self.subTest(raw=raw):
                with self.assertRaises((reproduce.ReproductionError, UnicodeError)):
                    reproduce.parse_http(raw, "request")

    def test_validate_search_records_checks_counts_and_credentials(self):
        canaries = {name: f"secret-{name}" for name in reproduce.MARKERS}

        def record(mode):
            store = "kairo-exploit-store" if mode == "exploit" else "kairo-control-store"
            credential = canaries["server"] if mode == "exploit" else canaries["control"]
            request = reproduce.make_request(
                "POST",
                f"/v1/vector_stores/{store}/search",
                body=reproduce.SEARCH_BODY,
                bearer=canaries["internal"],
                port=4000,
            )
            upstream = reproduce.make_request(
                "POST",
                f"/v1/vector_stores/{store}/search",
                body=reproduce.SEARCH_BODY,
                bearer=credential,
                port=5000,
            )
            response_body = json.dumps(reproduce.UPSTREAM_RESPONSE_BODY).encode()
            response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(response_body)).encode() + b"\r\n\r\n" + response_body
            return {
                "client_request_raw": request,
                "client_response_raw": response,
                "upstream_request_raw": upstream,
                "upstream_response_raw": response,
            }

        exploit = [record("exploit") for _ in range(reproduce.RUNS)]
        control = [record("control") for _ in range(reproduce.RUNS)]
        reproduce.validate_search_records(exploit, control, canaries)
        control.pop()
        with self.assertRaisesRegex(reproduce.ReproductionError, "control records"):
            reproduce.validate_search_records(exploit, control, canaries)


if __name__ == "__main__":
    unittest.main()
