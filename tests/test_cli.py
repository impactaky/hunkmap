from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from hunkmap.cli import main


def git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


class CliTest(unittest.TestCase):
    def test_sync_initializes_then_refreshes_and_preserves_review_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            git(repo, "init", "--quiet")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            (repo / "file.txt").write_text("before\n")
            git(repo, "add", "file.txt")
            git(repo, "commit", "--quiet", "-m", "initial")
            (repo / "file.txt").write_text("after\n")

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--repo", str(repo), "sync", "--base", "HEAD"]), 0)
                self.assertEqual(
                    main(["--repo", str(repo), "annotate", "1", "--group", "Goal", "--why", "Needed"]),
                    0,
                )
                self.assertEqual(main(["--repo", str(repo), "review", "1"]), 0)
                self.assertEqual(main(["--repo", str(repo), "sync", "--base", "not-a-revision"]), 0)

            rendered = output.getvalue()
            self.assertIn("Initialized", rendered)
            self.assertIn("Refreshed", rendered)
            self.assertEqual(rendered.count("UNITS       1"), 2)
            self.assertEqual(rendered.count("DIFF        clean"), 2)

            state_dir = repo / ".git" / "hunkmap"
            manifest = json.loads((state_dir / "manifest.json").read_text())
            state = json.loads((state_dir / "state.json").read_text())
            self.assertEqual(manifest["base"], "HEAD")
            self.assertEqual(manifest["hunks"][0]["group"], "Goal")
            self.assertEqual(manifest["hunks"][0]["why"], "Needed")
            self.assertEqual(state["reviewed"], [manifest["hunks"][0]["id"]])

    def test_init_annotate_review_and_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            git(repo, "init", "--quiet")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            (repo / "file.txt").write_text("before\n")
            git(repo, "add", "file.txt")
            git(repo, "commit", "--quiet", "-m", "initial")
            (repo / "file.txt").write_text("after\n")

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--repo", str(repo), "init"]), 0)
                self.assertTrue((repo / ".git" / "hunkmap" / "manifest.json").is_file())
                self.assertFalse((repo / ".hunkmap").exists())
                self.assertEqual(main(["--repo", str(repo), "annotate", "1", "--group", "Goal", "--why", "Needed"]), 0)
                self.assertEqual(main(["--repo", str(repo), "review", "1"]), 0)
                self.assertEqual(main(["--repo", str(repo), "check"]), 0)
                self.assertEqual(main(["--repo", str(repo), "status"]), 0)
            rendered = output.getvalue()
            self.assertIn("HUNKS       1/1", rendered)
            self.assertIn("REVIEWED    1/1", rendered)


if __name__ == "__main__":
    unittest.main()
