"""Offline checks for the issue 077 runner and committed wire fixtures."""

import importlib.util
import json
import os
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("kairo_077_reproduce", HERE / "reproduce.py")
reproduce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduce)


def split_exchange(raw):
    response_at = raw.find(b"HTTP/1.1 ", 1)
    if response_at < 0:
        raise AssertionError("fixture has no HTTP response")
    return raw[:response_at], raw[response_at:]


class FixtureTests(unittest.TestCase):
    def test_observed_discloses_only_the_custom_secret_control(self):
        raw = (HERE / "observed.http").read_bytes()
        request, response = split_exchange(raw)
        self.assertIn(b"POST /v1/responses HTTP/1.1", request)
        status, headers, body = reproduce.parse_http_response(response)
        self.assertEqual(status, 200)
        parsed = reproduce.validate_authenticated(response, status, headers, body)
        provider_headers = parsed["extra_fields"]["provider_response_headers"]
        self.assertEqual(provider_headers["X-Provider-Secret"], reproduce.SECRET)
        self.assertNotIn(reproduce.AUTHORIZATION_CONTROL.encode(), response)

    def test_upstream_proves_authorization_and_secret_were_emitted(self):
        raw = (HERE / "upstream.http").read_bytes()
        request, response = split_exchange(raw)
        self.assertIn(b"Authorization: Bearer <BIFROST_PROVIDER_AUTH>", request)
        self.assertIn(reproduce.SECRET.encode(), response)
        self.assertIn(reproduce.AUTHORIZATION_CONTROL.encode(), response)

    def test_expected_and_unauthenticated_controls_are_clean(self):
        expected = (HERE / "expected.http").read_bytes()
        unauthenticated = (HERE / "unauthenticated-control.http").read_bytes()
        self.assertNotIn(reproduce.SECRET.encode(), expected)
        self.assertNotIn(reproduce.SECRET.encode(), unauthenticated)
        _, response = split_exchange(unauthenticated)
        status, _, _ = reproduce.parse_http_response(response)
        self.assertEqual(status, 401)

    def test_summary_retains_all_trials_and_provenance(self):
        summary = json.loads((HERE / "results.json").read_text())
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["runs"], reproduce.RUNS)
        self.assertEqual(summary["target"]["commit"], reproduce.PINNED_COMMIT)
        self.assertEqual(
            summary["target"]["binary_vcs_revision"], reproduce.PINNED_COMMIT
        )
        self.assertEqual(summary["authenticated"]["secret_in_http_header"], reproduce.RUNS)
        self.assertEqual(summary["authenticated"]["secret_in_json_metadata"], reproduce.RUNS)
        self.assertEqual(summary["unauthenticated_control"]["http_401"], reproduce.RUNS)

    def test_sanitizers_redact_only_request_credentials(self):
        client = b"x-bf-vk: secret-value\r\nX-Test: keep\r\n"
        provider = b"Authorization: Bearer provider-value\r\nX-Test: keep\r\n"
        self.assertNotIn(b"secret-value", reproduce.sanitize_client_request(client))
        self.assertNotIn(b"provider-value", reproduce.sanitize_upstream_request(provider))
        self.assertIn(b"X-Test: keep", reproduce.sanitize_client_request(client))

    def test_current_binary_provenance_when_available(self):
        binary = os.environ.get("BIFROST_BIN")
        if not binary:
            self.skipTest("BIFROST_BIN is not set")
        provenance = reproduce.check_binary(Path(binary))
        self.assertEqual(provenance["vcs_revision"], reproduce.PINNED_COMMIT)


if __name__ == "__main__":
    unittest.main()
