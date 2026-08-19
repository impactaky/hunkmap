from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from hunkmap.store import ReviewProject


def git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


class ReviewProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        git(self.repo, "init", "--quiet")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / "tracked.txt").write_text("before\n")
        git(self.repo, "add", "tracked.txt")
        git(self.repo, "commit", "--quiet", "-m", "initial")
        (self.repo / "tracked.txt").write_text("after\n")
        (self.repo / "untracked.txt").write_text("new\n")
        self.project = ReviewProject(self.repo, self.repo / ".hunkmap")
        self.project.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_init_covers_tracked_and_untracked_changes(self) -> None:
        coverage = self.project.coverage()
        self.assertTrue(coverage.ok)
        self.assertEqual(coverage.files_total, 2)
        self.assertEqual(coverage.files_mapped, 2)
        self.assertEqual(coverage.hunks_total, 2)

    def test_duplicate_manifest_entry_fails_coverage(self) -> None:
        manifest = self.project.load_manifest()
        manifest["hunks"].append(dict(manifest["hunks"][0]))
        self.project.manifest_path.write_text(json.dumps(manifest))
        coverage = self.project.coverage()
        self.assertFalse(coverage.ok)
        self.assertEqual(len(coverage.duplicate), 1)

    def test_metadata_tampering_fails_coverage(self) -> None:
        manifest = self.project.load_manifest()
        manifest["hunks"][0]["path"] = "wrong.txt"
        self.project.manifest_path.write_text(json.dumps(manifest))
        coverage = self.project.coverage()
        self.assertFalse(coverage.ok)
        self.assertEqual(len(coverage.mismatch), 1)

    def test_live_diff_change_marks_snapshot_stale(self) -> None:
        (self.repo / "tracked.txt").write_text("changed again\n")
        coverage = self.project.coverage()
        self.assertTrue(coverage.stale)
        self.assertFalse(coverage.ok)

    def test_refresh_preserves_unchanged_annotations_and_review_state(self) -> None:
        first = self.project.load_manifest()["hunks"][0]
        self.project.annotate(first["id"], group="Purpose", why="Needed")
        self.project.set_reviewed(first["id"], True)
        (self.repo / "another.txt").write_text("another\n")
        self.project.refresh()
        retained = {entry["id"]: entry for entry in self.project.load_manifest()["hunks"]}[first["id"]]
        self.assertEqual(retained["group"], "Purpose")
        self.assertIn(first["id"], self.project.load_state()["reviewed"])
        self.assertTrue(self.project.coverage().ok)

    def test_init_supports_an_unborn_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            git(repo, "init", "--quiet")
            (repo / "first.txt").write_text("first\n")
            project = ReviewProject(repo, repo / ".git" / "hunkmap")
            project.initialize()
            coverage = project.coverage()
            self.assertTrue(coverage.ok)
            self.assertEqual(coverage.files_total, 1)


if __name__ == "__main__":
    unittest.main()
