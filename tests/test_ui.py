from __future__ import annotations

import unittest
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from hunkmap.ui import ReviewRailApp


@dataclass
class FakeUnit:
    patch: bytes


@dataclass
class FakeCoverage:
    ok: bool


class FakeProject:
    def __init__(self) -> None:
        self.manifest = {
            "goal": "Review the complete change",
            "hunks": [
                {
                    "id": "hm-one",
                    "path": "src/one.py",
                    "ordinal": 0,
                    "group": "Core",
                    "label": "parse diff",
                    "why": "Build the review units",
                    "safety": "Unit tested",
                    "evidence": "test_diff",
                },
                {
                    "id": "hm-two",
                    "path": "src/two.py",
                    "ordinal": 0,
                    "group": "Core",
                    "label": "persist state",
                    "why": "Remember review progress",
                    "safety": "Atomic write",
                    "evidence": "test_store",
                },
                {
                    "id": "hm-three",
                    "path": "src/three.py",
                    "ordinal": 0,
                    "group": "Adapter",
                    "label": "reload Hunk",
                    "why": "Show only the selected unit",
                    "safety": "Adapter boundary",
                    "evidence": "smoke test",
                },
            ],
        }
        self.state = {"reviewed": []}
        self.coverage_ok = True

    def load_manifest(self) -> dict:
        return deepcopy(self.manifest)

    def load_state(self) -> dict:
        return deepcopy(self.state)

    def unit_for(self, identifier: str) -> FakeUnit:
        return FakeUnit(f"patch for {identifier}\n".encode())

    def write_state(self, state: dict) -> None:
        self.state = deepcopy(state)

    def coverage(self) -> FakeCoverage:
        return FakeCoverage(self.coverage_ok)


class ReviewRailAppTest(unittest.IsolatedAsyncioTestCase):
    async def test_keyboard_navigation_and_review_are_persisted(self) -> None:
        project = FakeProject()
        reloads: list[bytes] = []
        with TemporaryDirectory() as directory:
            app = ReviewRailApp(
                project,  # type: ignore[arg-type]
                Path(directory),
                "hunk",
                "session",
                patch_reloader=lambda _hunk, _session, path: reloads.append(
                    path.read_bytes()
                ),
            )
            async with app.run_test(size=(72, 24)) as pilot:
                await pilot.pause()
                self.assertEqual(app.current, 0)
                await pilot.press("j")
                await pilot.pause()
                self.assertEqual(app.current, 1)
                self.assertEqual(reloads[-1], b"patch for hm-two\n")

                await pilot.press("r")
                await pilot.pause()
                self.assertEqual(project.state["reviewed"], ["hm-two"])

                await pilot.press("a")
                await pilot.pause()
                self.assertEqual(app.current, 2)
                self.assertEqual(project.state["reviewed"], ["hm-two"])

    async def test_mouse_selects_and_right_click_reviews_then_advances(self) -> None:
        project = FakeProject()
        with TemporaryDirectory() as directory:
            app = ReviewRailApp(
                project,  # type: ignore[arg-type]
                Path(directory),
                "hunk",
                "session",
                patch_reloader=lambda _hunk, _session, _path: None,
            )
            async with app.run_test(size=(72, 24)) as pilot:
                await pilot.pause()
                self.assertTrue(await pilot.click("#unit-1"))
                await pilot.pause()
                self.assertEqual(app.current, 1)

                self.assertTrue(await pilot.click("#unit-1", button=3))
                await pilot.pause()
                self.assertEqual(project.state["reviewed"], ["hm-two"])
                self.assertEqual(app.current, 2)

    async def test_stale_diff_blocks_review_state_changes(self) -> None:
        project = FakeProject()
        with TemporaryDirectory() as directory:
            app = ReviewRailApp(
                project,  # type: ignore[arg-type]
                Path(directory),
                "hunk",
                "session",
                patch_reloader=lambda _hunk, _session, _path: None,
            )
            async with app.run_test(size=(72, 24)) as pilot:
                await pilot.pause()
                project.coverage_ok = False
                app._check_coverage()
                await pilot.press("r")
                await pilot.pause()
                self.assertEqual(project.state["reviewed"], [])
                self.assertIn("DIFF STALE", app.message)


if __name__ == "__main__":
    unittest.main()
