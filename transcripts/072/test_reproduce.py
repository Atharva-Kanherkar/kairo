"""Regression tests for transcripts/072/reproduce.py.

These tests exercise reproduction helper functions and validation logic directly
without requiring a running Bifrost process.

Run with:
    python3 -m unittest transcripts/072/test_reproduce.py
    python3 -O -m unittest transcripts/072/test_reproduce.py
"""

import importlib.util
import os
import socket
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
            self.assertIn(f"port {port} on 127.0.0.1 is in use", str(ctx.exception))
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


if __name__ == "__main__":
    unittest.main()
