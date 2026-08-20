from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hunkmap.diff import parse_patch
from hunkmap.hunk import create_review_source, prepare_review_source, reload_patch, run_git
from hunkmap.store import ReviewProject


def git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


class ReviewSourceTest(unittest.TestCase):
    def test_source_switches_between_individual_hunks_from_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            original = [f"line {index}\n" for index in range(20)]
            (repo / "file.txt").write_text("".join(original))
            git(repo, "add", "file.txt")
            git(repo, "commit", "--quiet", "-m", "initial")
            changed = original.copy()
            changed[1] = "first change\n"
            changed[18] = "second change\n"
            (repo / "file.txt").write_text("".join(changed))

            project = ReviewProject.discover(repo, root / "state")
            project.initialize()
            units = project.units()
            self.assertEqual(len(units), 2)

            runtime = root / "runtime"
            runtime.mkdir()
            source = create_review_source(repo, "HEAD", runtime / "source")
            selected = runtime / "selected.patch"
            expected_changes = (b"+first change", b"+second change")
            for unit, expected_change in zip(units, expected_changes, strict=True):
                selected.write_bytes(unit.patch)
                prepare_review_source(source, selected)
                rendered = run_git(
                    source, "diff", "--binary", "--no-ext-diff", "--no-color", "HEAD"
                ).stdout
                selected_units = parse_patch(rendered)
                self.assertEqual(len(selected_units), 1)
                self.assertIn(expected_change, rendered)
                self.assertTrue(all(
                    change not in rendered
                    for change in expected_changes
                    if change != expected_change
                ))

    def test_source_exposes_an_untracked_file_from_an_unborn_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            (repo / "new.txt").write_text("new\n")

            project = ReviewProject.discover(repo, root / "state")
            project.initialize()
            unit = project.units()[0]

            runtime = root / "runtime"
            runtime.mkdir()
            source = create_review_source(repo, "HEAD", runtime / "source")
            selected = runtime / "selected.patch"
            selected.write_bytes(unit.patch)
            prepare_review_source(source, selected)
            rendered = run_git(
                source, "diff", "--binary", "--no-ext-diff", "--no-color", "HEAD"
            ).stdout
            self.assertEqual([item.id for item in parse_patch(rendered)], [unit.id])

            session = {
                "sessionId": "session-1",
                "files": [{"path": "new.txt"}],
                "snapshot": {"state": {"selectedHunkIndex": 0}},
            }
            with (
                patch("hunkmap.hunk.run") as hunk_run,
                patch("hunkmap.hunk.sessions", return_value=[session]),
            ):
                reload_patch("hunk", "session-1", selected)
            hunk_run.assert_called_once_with(
                "hunk", "session", "reload", "session-1",
                "--source", str(source), "--json", "--", "diff", "HEAD",
            )


if __name__ == "__main__":
    unittest.main()
