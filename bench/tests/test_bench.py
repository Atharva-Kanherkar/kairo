"""Offline checks for the kairo-bench harness and task catalog (no Docker, no keys).

    python3 -m unittest discover -s bench/tests      # Python 3.11+
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH / "lib"))

from kairo_bench import agents, report, runner, tasks  # noqa: E402
from kairo_bench.egress_proxy import allowed  # noqa: E402
from kairo_bench.paths import is_test_path  # noqa: E402
import kairo_bench.egress_proxy as egress  # noqa: E402


class TaskCatalogTest(unittest.TestCase):
    def setUp(self):
        self.tasks = tasks.all_tasks()

    def test_every_task_loads(self):
        self.assertGreater(len(self.tasks), 0)

    def test_headline_split_has_thirty_bug_tasks(self):
        ids = tasks.load_split("kairo-30")
        self.assertEqual(len(ids), 30)
        self.assertEqual(len(set(ids)), 30)
        by_id = {t.id: t for t in self.tasks}
        self.assertTrue(all(by_id[i].kind == "bug" for i in ids))

    def test_negatives_split_is_no_bug(self):
        by_id = {t.id: t for t in self.tasks}
        for tid in tasks.load_split("negatives"):
            self.assertEqual(by_id[tid].kind, "no-bug", tid)

    def test_every_task_is_in_exactly_one_split(self):
        listed = tasks.load_split("kairo-30") + tasks.load_split("negatives")
        self.assertEqual(sorted(listed), sorted(t.id for t in self.tasks))

    def test_task_files_are_complete(self):
        for t in self.tasks:
            with self.subTest(task=t.id):
                self.assertTrue((t.dir / "problem.md").read_text().strip())
                run_sh = t.verifier_dir / "run.sh"
                self.assertTrue(run_sh.is_file() or "verify.py" in t.verifier_command)
                self.assertTrue((BENCH / "envs" / t.env / "Dockerfile").is_file(), t.env)
                if t.kind == "bug":
                    patch = t.gold_patch.read_text()
                    self.assertTrue(patch.startswith("diff --git"), "gold.patch must be a git diff")
                    self.assertFalse(any(is_test_path(p) for p in re.findall(r"^diff --git a/(\S+)", patch, re.M)),
                                     "gold patches exclude upstream test files")
                if t.has_reproducer:
                    mode = (t.public_dir / "reproduce.sh").stat().st_mode
                    self.assertTrue(mode & stat.S_IXUSR, "reproduce.sh must be executable")

    def test_env_dockerfiles_pin_a_full_commit(self):
        for t in self.tasks:
            with self.subTest(task=t.id):
                dockerfile = (BENCH / "envs" / t.env / "Dockerfile").read_text()
                self.assertIn(t.base_commit, dockerfile, "task base_commit must be the commit the env fetches")

    def test_verifier_is_not_shipped_to_the_agent(self):
        for t in self.tasks:
            with self.subTest(task=t.id):
                if t.public_dir.is_dir():
                    names = {p.name for p in t.public_dir.rglob("*")}
                    self.assertNotIn("verify.py", names)

    def test_problem_statements_do_not_link_the_fix(self):
        for t in self.tasks:
            with self.subTest(task=t.id):
                text = t.problem.lower()
                if t.upstream_fix:
                    self.assertNotIn(t.upstream_fix.lower(), text)
                self.assertNotIn("gold", text)

    def test_test_names_are_unique_per_task(self):
        for t in self.tasks:
            names = list(t.fail_to_pass) + list(t.pass_to_pass)
            self.assertEqual(len(names), len(set(names)), t.id)


class HarnessUnitTest(unittest.TestCase):
    def test_is_test_path(self):
        self.assertTrue(is_test_path("tests/unit/test_messages.py"))
        self.assertTrue(is_test_path("core/providers/openai/chat_test.go"))
        self.assertTrue(is_test_path("crates/x/tests/stream.rs"))
        self.assertFalse(is_test_path("litellm/router.py"))
        self.assertFalse(is_test_path("core/providers/openai/chat.go"))

    def test_egress_allowlist_matching(self):
        egress.ALLOW[:] = ["api.anthropic.com", ".openai.com"]
        self.assertTrue(allowed("api.anthropic.com"))
        self.assertTrue(allowed("api.openai.com"))
        self.assertTrue(allowed("openai.com"))
        self.assertFalse(allowed("github.com"))
        self.assertFalse(allowed("api.anthropic.com.evil.example"))
        self.assertFalse(allowed("pypi.org"))

    def test_prompt_contains_problem_and_contract(self):
        t = tasks.select(None, ["any-llm-057"])[0]
        prompt = runner.render_prompt(t, reproduction=True)
        self.assertIn(t.problem.strip().splitlines()[0], prompt)
        self.assertIn("/work/verdict.json", prompt)
        self.assertIn("reproduce.sh", prompt)
        self.assertNotIn("reproduce.sh", runner.render_prompt(t, reproduction=False))
        self.assertIn(t.base_commit[:12], prompt)

    def test_every_agent_has_an_invocation(self):
        secrets = {"ANTHROPIC_API_KEY": "sk-ant-SECRET-1", "OPENAI_API_KEY": "sk-oai-SECRET-2", "GEMINI_API_KEY": "gem-SECRET-3"}
        for name in agents.known_agents():
            spec = agents.parse_agent(name)
            inv = agents.invocation(spec, secrets)
            self.assertTrue(inv.command, name)
            for key in inv.key_names:
                self.assertNotIn(secrets[key], inv.command, "secrets must never appear in argv")

    def test_agent_selector_with_model(self):
        spec = agents.parse_agent("opencode:openrouter/qwen/qwen3-coder")
        self.assertEqual(spec.model, "openrouter/qwen/qwen3-coder")
        self.assertEqual(spec.slug, "opencode-openrouter-qwen-qwen3-coder")

    def test_usage_parsing(self):
        spec = agents.parse_agent("claude-code")
        out = "\n".join([json.dumps({"type": "system"}),
                         json.dumps({"type": "result", "total_cost_usd": 1.25, "num_turns": 7,
                                     "usage": {"input_tokens": 10, "output_tokens": 20}})])
        self.assertEqual(agents.parse_usage(spec, out)["cost_usd"], 1.25)

    def test_report_scores_bug_and_no_bug_tasks(self):
        rows = [
            {"agent_label": "a", "task": "t1", "kind": "bug", "category": "crash", "difficulty": "easy", "resolved": True},
            {"agent_label": "a", "task": "t2", "kind": "bug", "category": "crash", "difficulty": "hard", "resolved": False},
            {"agent_label": "a", "task": "n1", "kind": "no-bug", "category": "crash", "difficulty": "easy",
             "resolved": False, "hallucinated_fix": True},
        ]
        s = report.summarize(rows)
        a = s["agents"]["a"]
        self.assertEqual(a["bug_resolved_any_trial"], 1)
        self.assertAlmostEqual(a["bug_resolved_mean"], 0.5)
        self.assertEqual(a["hallucinated_fixes"], 1)
        md = report.render_markdown(s, {"run_id": "x"})
        self.assertIn("| a | 50.0% of 2 |", md)


class VerifyLibTest(unittest.TestCase):
    def test_results_contract(self):
        import kairo_verify as kv
        r = kv.Results()
        with r.test("passes"):
            pass
        with r.test("fails"):
            assert False, "nope"
        with r.test("errors"):
            raise RuntimeError("boom")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.json")
            r.write(path)
            data = json.load(open(path))
        self.assertEqual([t["status"] for t in data["tests"]], ["pass", "fail", "error"])

    def test_upstream_records_and_replies(self):
        import kairo_verify as kv
        with kv.Upstream(lambda cap: kv.Reply.json({"echo": cap.json})) as up:
            status, _, raw = kv.request("POST", up.url + "/v1/x", {"a": 1})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw), {"echo": {"a": 1}})
            self.assertEqual(up.last("/v1/x").json, {"a": 1})

    def test_sse_parsing(self):
        import kairo_verify as kv
        events = kv.sse_events(b'event: a\ndata: {"x": 1}\n\ndata: [DONE]\n\n')
        self.assertEqual(events[0]["event"], "a")
        self.assertEqual(events[0]["json"], {"x": 1})
        self.assertEqual(events[1]["data"], "[DONE]")


if __name__ == "__main__":
    unittest.main()
