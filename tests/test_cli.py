from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hunkmap.cli import main


def git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


class CliTest(unittest.TestCase):
    def test_apply_map_is_complete_ordered_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "--quiet")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            for name in ("first.txt", "second.txt"):
                (repo / name).write_text("before\n")
            git(repo, "add", ".")
            git(repo, "commit", "--quiet", "-m", "initial")
            for name in ("first.txt", "second.txt"):
                (repo / name).write_text("after\n")

            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--repo", str(repo), "sync"]), 0)
            state_dir = repo / ".git" / "hunkmap"
            initial = json.loads((state_dir / "manifest.json").read_text())
            first, second = initial["hunks"]

            drafted = io.StringIO()
            with redirect_stdout(drafted):
                self.assertEqual(main(["--repo", str(repo), "draft-map"]), 0)
            draft_path = Path(drafted.getvalue().strip())
            self.addCleanup(draft_path.unlink, missing_ok=True)
            draft = json.loads(draft_path.read_text())
            self.assertEqual(draft["goal"], "")
            self.assertEqual([unit["id"] for unit in draft["chapters"][0]["units"]], [
                first["id"], second["id"],
            ])
            self.assertEqual(draft["chapters"][0]["name"], "Unassigned")

            not_ready = io.StringIO()
            with redirect_stdout(not_ready):
                self.assertEqual(main(["--repo", str(repo), "check", "--ready"]), 1)
            self.assertIn("SEMANTIC    ", not_ready.getvalue())
            self.assertIn("goal is empty", not_ready.getvalue())
            self.assertIn("label is unset", not_ready.getvalue())
            with patch("hunkmap.herdr.launch") as launch:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["--repo", str(repo), "open"]), 1)
                launch.assert_not_called()

            document = {
                "goal": "Ship one coherent result",
                "chapters": [
                    {
                        "name": "Second purpose first",
                        "units": [{
                            "id": second["id"],
                            "label": "Change second",
                            "why": "Second drives the review order",
                            "safety": "Only second.txt changes",
                            "evidence": "Fixture covers second.txt",
                        }],
                    },
                    {
                        "name": "First purpose second",
                        "units": [{
                            "id": first["id"],
                            "label": "Change first",
                            "why": "First follows the semantic dependency",
                            "safety": "Only first.txt changes",
                            "evidence": "Fixture covers first.txt",
                        }],
                    },
                ],
            }
            map_path = root / "review-map.json"
            map_path.write_text(json.dumps(document))

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--repo", str(repo), "apply-map", str(map_path)]), 0)
            self.assertIn("Applied 2 chapters to 2 review units", output.getvalue())

            applied = json.loads((state_dir / "manifest.json").read_text())
            self.assertEqual(applied["goal"], document["goal"])
            self.assertEqual([item["id"] for item in applied["hunks"]], [second["id"], first["id"]])
            self.assertEqual([item["group"] for item in applied["hunks"]], [
                "Second purpose first", "First purpose second",
            ])

            redrafted = io.StringIO()
            with redirect_stdout(redrafted):
                self.assertEqual(main(["--repo", str(repo), "draft-map"]), 0)
            redraft_path = Path(redrafted.getvalue().strip())
            self.addCleanup(redraft_path.unlink, missing_ok=True)
            self.assertEqual(json.loads(redraft_path.read_text()), document)

            context = io.StringIO()
            with redirect_stdout(context):
                self.assertEqual(main(["--repo", str(repo), "context", "--patches"]), 0)
            rendered_context = context.getvalue()
            self.assertIn("Why: Second drives the review order", rendered_context)
            self.assertIn("Safety: Only second.txt changes", rendered_context)
            self.assertIn("Evidence: Fixture covers second.txt", rendered_context)
            self.assertIn("```diff", rendered_context)

            ready = io.StringIO()
            with redirect_stdout(ready):
                self.assertEqual(main(["--repo", str(repo), "check", "--ready"]), 0)
            self.assertIn("SEMANTIC    ready", ready.getvalue())
            with patch("hunkmap.herdr.launch") as launch:
                opened = io.StringIO()
                with redirect_stdout(opened):
                    self.assertEqual(main(["--repo", str(repo), "open"]), 0)
                launch.assert_called_once()
                self.assertIn("SEMANTIC    ready", opened.getvalue())

            before_invalid = (state_dir / "manifest.json").read_bytes()
            invalid_maps: list[tuple[dict, str]] = []

            missing = deepcopy(document)
            missing["chapters"].pop()
            invalid_maps.append((missing, "missing review units"))

            duplicate = deepcopy(document)
            duplicate["chapters"][1]["units"][0] = deepcopy(duplicate["chapters"][0]["units"][0])
            invalid_maps.append((duplicate, "duplicate review unit"))

            unknown = deepcopy(document)
            unknown["chapters"][0]["units"][0]["id"] = "hm-unknown"
            invalid_maps.append((unknown, "unknown review unit"))

            empty = deepcopy(document)
            empty["chapters"][0]["units"][0]["why"] = " "
            invalid_maps.append((empty, "must be a non-empty string"))

            for invalid, expected_error in invalid_maps:
                with self.subTest(expected_error=expected_error):
                    map_path.write_text(json.dumps(invalid))
                    errors = io.StringIO()
                    with redirect_stderr(errors):
                        self.assertEqual(main(["--repo", str(repo), "apply-map", str(map_path)]), 2)
                    self.assertIn(expected_error, errors.getvalue())
                    self.assertEqual((state_dir / "manifest.json").read_bytes(), before_invalid)

            (repo / "first.txt").write_text("changed again\n")
            map_path.write_text(json.dumps(document))
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(main(["--repo", str(repo), "apply-map", str(map_path)]), 2)
            self.assertIn("diff is stale", errors.getvalue())
            self.assertEqual((state_dir / "manifest.json").read_bytes(), before_invalid)

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
