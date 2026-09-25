#!/usr/bin/env python3
"""Unit tests for the finding 087 runner and fail-closed evidence checker."""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reproduce = load_module("kairo_087_reproduce_tests", ROOT / "reproduce.py")
checker = load_module("kairo_087_checker_tests", ROOT / "check_evidence.py")


class ReproduceTests(unittest.TestCase):
    def test_sanitizer_marks_fixed_keys_and_rejects_other_credentials(self):
        self.assertEqual(reproduce.sanitize_text(reproduce.MASTER_KEY), "<SYNTHETIC_MASTER_KEY>")
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.sanitize_text("Bearer " + "sk-" + "abcdefghijklmnopqrstuvwxyz")

    def test_ledger_requires_valid_consecutive_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            path.write_text('{"seq":4,"entry":"alpha"}\n{"seq":5,"entry":"alpha"}\n', encoding="utf-8")
            self.assertEqual(len(reproduce.ledger_records(path)), 2)
            path.write_text('{"seq":4,"entry":"alpha"}\n{"seq":6,"entry":"alpha"}\n', encoding="utf-8")
            with self.assertRaises(reproduce.ReproductionError):
                reproduce.ledger_records(path)

    def test_pinned_and_current_main_evidence_pass(self):
        checker.check_matrix(ROOT / "results.json", ROOT)
        main = ROOT / "matrix" / "main-2701e200"
        checker.check_matrix(main / "results.json", main)

    def test_checker_rejects_vacuous_and_inconsistent_summaries(self):
        summary = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
        empty = copy.deepcopy(summary)
        empty["cells"]["violation_default_retries"]["runs"] = []
        inconsistent = copy.deepcopy(summary)
        inconsistent["cells"]["violation_num_retries_2"]["runs"][4]["tool_executions"] = 1
        for mutated in (empty, inconsistent):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "results.json"
                path.write_text(json.dumps(mutated), encoding="utf-8")
                with self.assertRaises(checker.reproduce.ReproductionError):
                    checker.check_matrix(path, ROOT)

    def test_checker_rejects_missing_raw_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "cells", root / "cells")
            results = root / "results.json"
            shutil.copy2(ROOT / "results.json", results)
            missing = root / "cells" / "control_fault_off" / "runs" / "run-05" / "client-response.http"
            missing.unlink()
            with self.assertRaises(checker.reproduce.ReproductionError):
                checker.check_matrix(results, root)


if __name__ == "__main__":
    unittest.main()
