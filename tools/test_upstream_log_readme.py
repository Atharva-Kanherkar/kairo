"""Offline tests for tools/update-upstream-log.py.

The tool needs the `gh` CLI to fetch state, so these tests cover only the pure
rendering helpers with fixed input.

    python3 -m unittest tools/test_upstream_log_readme.py
"""

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "update-upstream-log.py"
_spec = importlib.util.spec_from_file_location("kairo_upstream_log", MODULE_PATH)
log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(log)

FOLDERS = {"057": "issues/057-a", "058": "issues/058-b", "087": "issues/087-c"}


def item(number, state, title="t"):
    return {
        "number": number,
        "state": state,
        "title": title,
        "url": f"https://github.com/o/r/pull/{number}",
        "createdAt": "2026-09-25T10:00:00Z",
    }


class StateTests(unittest.TestCase):
    def test_closed_issue_uses_state_reason(self):
        self.assertEqual(
            log.normalize_state(
                {"state": "CLOSED", "stateReason": "COMPLETED"}
            ),
            "completed",
        )
        self.assertEqual(
            log.normalize_state(
                {"state": "CLOSED", "stateReason": "NOT_PLANNED"}
            ),
            "not planned",
        )

    def test_pull_request_states_pass_through(self):
        for raw, want in (("MERGED", "merged"), ("OPEN", "open"), ("CLOSED", "closed")):
            self.assertEqual(log.normalize_state({"state": raw}), want)


class TitleTests(unittest.TestCase):
    def test_bug_tag_is_stripped(self):
        self.assertEqual(log.clean_title("[Bug]: drops x"), "drops x")
        self.assertEqual(log.clean_title("(bug) drops x"), "drops x")

    def test_conventional_commit_prefix_is_kept(self):
        self.assertEqual(log.clean_title("fix(router): stop"), "fix(router): stop")

    def test_table_breaking_characters_are_escaped(self):
        self.assertEqual(log.clean_title("a | b <c>"), "a \\| b &lt;c&gt;")

    def test_no_em_dash_survives(self):
        self.assertNotIn(chr(0x2014), log.clean_title("a " + chr(0x2014) + " b"))


class FolderLinkTests(unittest.TestCase):
    def test_consecutive_findings_collapse_to_a_range(self):
        self.assertEqual(
            log.folder_links(("057", "058"), FOLDERS),
            "[057](issues/057-a)-[058](issues/058-b)",
        )

    def test_gap_stays_separate_and_missing_folder_is_plain(self):
        self.assertEqual(
            log.folder_links(("057", "087", "099"), FOLDERS),
            "[057](issues/057-a), [087](issues/087-c), 099",
        )


class RenderTests(unittest.TestCase):
    def data(self):
        empty = {"prs": [], "issues": []}
        data = {repo: dict(empty) for repo, _, _ in log.PROJECTS}
        data["BerriAI/litellm"] = {
            "prs": [item(43159, "open"), item(40121, "merged")],
            "issues": [item(43153, "open"), item(36898, "completed")],
        }
        return data

    def test_summary_counts_and_total(self):
        block = log.render(self.data(), FOLDERS, "2026-09-29")
        self.assertIn("| [LiteLLM](https://github.com/BerriAI/litellm) | 1 | 1 | 1 | 1 | 0 |", block)
        self.assertIn("| **Total** | **1** | **1** | **1** | **1** | **0** |", block)

    def test_rows_link_the_finding_and_sort_newest_first(self):
        block = log.render(self.data(), FOLDERS, "2026-09-29")
        self.assertLess(block.index("#43159"), block.index("#40121"))
        self.assertIn("[087](issues/087-c)", block)

    def test_projects_without_items_render_no_empty_tables(self):
        block = log.render(self.data(), FOLDERS, "2026-09-29")
        self.assertEqual(block.count("**Pull requests**"), 1)

    def test_every_project_keeps_a_summary_row(self):
        block = log.render(self.data(), FOLDERS, "2026-09-29")
        for _, name, _ in log.PROJECTS:
            self.assertIn(f"[{name}](", block)


class SpliceTests(unittest.TestCase):
    def test_replaces_only_the_marked_block(self):
        text = f"before\n{log.START}\nold\n{log.END}\nafter\n"
        self.assertEqual(
            log.splice(text, "new"),
            f"before\n{log.START}\nnew\n{log.END}\nafter\n",
        )

    def test_missing_markers_fail_loudly(self):
        with self.assertRaises(SystemExit):
            log.splice("no markers", "new")


if __name__ == "__main__":
    unittest.main()
