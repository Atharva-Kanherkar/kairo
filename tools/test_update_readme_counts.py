"""Regression tests for tools/update-readme-counts.py.

Guards against README.md's table, README.md's prose folder count, and
issues/SCOREBOARD.md's coverage line drifting out of sync with each other or
with the actual number of unique git-tracked `issues/NNN-*` writeups.

    python3 -m unittest tools/test_update_readme_counts.py
"""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parent / "update-readme-counts.py"
_spec = importlib.util.spec_from_file_location(
    "kairo_update_readme_counts", MODULE_PATH
)
counts_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(counts_tool)


class CollectTests(unittest.TestCase):
    def test_bugs_count_matches_unique_issue_numbers_on_disk(self):
        numbers = set()
        for path in counts_tool.git_ls("issues/*/README.md"):
            numbers.add(int(Path(path).parent.name.split("-", 1)[0]))
        counts = counts_tool.collect()
        self.assertEqual(counts["bugs"], len(numbers))


class ProseAndScoreboardConsistencyTests(unittest.TestCase):
    def test_readme_prose_matches_computed_bug_count(self):
        counts = counts_tool.collect()
        readme_text = counts_tool.README.read_text()
        m = counts_tool.README_PROSE_RE.search(readme_text)
        self.assertIsNotNone(m, "README.md prose sentence not found")
        prose_count = int(readme_text[m.end(1) : m.start(2)])
        self.assertEqual(
            prose_count,
            counts["bugs"],
            "README prose folder count has drifted from the computed count",
        )

    def test_scoreboard_coverage_matches_computed_bug_count(self):
        counts = counts_tool.collect()
        scoreboard_text = counts_tool.SCOREBOARD.read_text()
        m = counts_tool.SCOREBOARD_COVERAGE_RE.search(scoreboard_text)
        self.assertIsNotNone(m, "SCOREBOARD.md coverage line not found")
        coverage_count = int(scoreboard_text[m.end(1) : m.start(2)])
        self.assertEqual(
            coverage_count,
            counts["bugs"],
            "SCOREBOARD coverage folder count has drifted from the computed count",
        )

    def test_check_mode_is_a_noop_on_the_current_repo_state(self):
        counts = counts_tool.collect()
        block = counts_tool.render(counts)
        readme_text = counts_tool.README.read_text()
        readme_updated = counts_tool.apply_readme_prose(
            counts_tool.apply(readme_text, block), counts["bugs"]
        )
        self.assertEqual(
            readme_updated,
            readme_text,
            "README.md is stale; run `python3 tools/update-readme-counts.py`",
        )
        scoreboard_text = counts_tool.SCOREBOARD.read_text()
        scoreboard_updated = counts_tool.apply_scoreboard(
            scoreboard_text, counts["bugs"]
        )
        self.assertEqual(
            scoreboard_updated,
            scoreboard_text,
            "issues/SCOREBOARD.md is stale; run `python3 tools/update-readme-counts.py`",
        )


class DriftDetectionTests(unittest.TestCase):
    """Prove the guard actually fires on drift, not just that it is quiet
    when nothing has drifted."""

    def test_check_rejects_each_stale_count_without_writing(self):
        readme = counts_tool.README.read_text()
        scoreboard = counts_tool.SCOREBOARD.read_text()
        bugs = counts_tool.collect()["bugs"]
        cases = [
            (
                readme.replace(
                    f"| Reproduced issue folders | {bugs} |",
                    "| Reproduced issue folders | 0 |",
                ),
                scoreboard,
            ),
            (
                readme.replace(f"The {bugs} folders cover", "The 0 folders cover"),
                scoreboard,
            ),
            (
                readme,
                scoreboard.replace(
                    f"**Coverage**: {bugs} documented", "**Coverage**: 0 documented"
                ),
            ),
        ]
        for stale_readme, stale_scoreboard in cases:
            with self.subTest(), tempfile.TemporaryDirectory() as directory:
                readme_path = Path(directory) / "README.md"
                scoreboard_path = Path(directory) / "SCOREBOARD.md"
                readme_path.write_text(stale_readme)
                scoreboard_path.write_text(stale_scoreboard)
                with (
                    patch.object(counts_tool, "README", readme_path),
                    patch.object(counts_tool, "SCOREBOARD", scoreboard_path),
                    patch("sys.argv", ["update-readme-counts.py", "--check"]),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(counts_tool.main(), 1)
                self.assertEqual(readme_path.read_text(), stale_readme)
                self.assertEqual(scoreboard_path.read_text(), stale_scoreboard)

    def test_apply_readme_prose_rewrites_stale_number(self):
        stale = "Intro.\n\nThe 47 folders cover 51 distinct defects, 50 current.\n"
        updated = counts_tool.apply_readme_prose(stale, 49)
        self.assertIn("The 49 folders cover", updated)
        self.assertNotIn("The 47 folders cover", updated)
        # Only the folder count is mechanically derived; the defect counts
        # are editorial and must be left alone by this substitution.
        self.assertIn("51 distinct defects, 50 current", updated)

    def test_apply_scoreboard_rewrites_stale_number(self):
        stale = (
            "**Coverage**: 48 documented issue folders covering 52 distinct defects\n"
        )
        updated = counts_tool.apply_scoreboard(stale, 49)
        self.assertIn("**Coverage**: 49 documented issue folders", updated)
        self.assertNotIn("48 documented issue folders", updated)

    def test_apply_readme_prose_missing_sentence_raises(self):
        with self.assertRaises(SystemExit):
            counts_tool.apply_readme_prose("no matching sentence here", 49)

    def test_apply_scoreboard_missing_line_raises(self):
        with self.assertRaises(SystemExit):
            counts_tool.apply_scoreboard("no matching line here", 49)


if __name__ == "__main__":
    unittest.main()
