from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import unittest

from hunkmap.cli import main


def git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


class CliTest(unittest.TestCase):
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
