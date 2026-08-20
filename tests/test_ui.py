from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread

from textual.pilot import Pilot
from textual.widgets import ListView, Static

from hunkmap.ui import ReviewRailApp, ReviewRow


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


class BlockingReloader:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.started = Event()
        self.release = Event()
        self.calls: list[bytes] = []
        self.active = 0
        self.max_active = 0
        self.fail_first = fail_first
        self._lock = Lock()

    def __call__(self, _hunk: str, _session: str, path: Path) -> None:
        patch = path.read_bytes()
        with self._lock:
            self.calls.append(patch)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            first = len(self.calls) == 1
        try:
            if first:
                self.started.set()
                self.release.wait()
        finally:
            with self._lock:
                self.active -= 1
        if first and self.fail_first:
            raise RuntimeError("obsolete reload failure")


class FailingOnceReloader:
    def __init__(self) -> None:
        self.failed = Event()
        self.calls: list[bytes] = []

    def __call__(self, _hunk: str, _session: str, path: Path) -> None:
        self.calls.append(path.read_bytes())
        if len(self.calls) == 1:
            self.failed.set()
            raise RuntimeError("test reload failure")


class BlockingCoverageProject(FakeProject):
    def __init__(self) -> None:
        super().__init__()
        self.coverage_started = Event()
        self.coverage_release = Event()
        self.coverage_calls = 0

    def coverage(self) -> FakeCoverage:
        self.coverage_calls += 1
        self.coverage_started.set()
        self.coverage_release.wait()
        return super().coverage()


class ShutdownAwareReviewRailApp(ReviewRailApp):
    shutdown_started: Event

    def on_unmount(self) -> None:
        self.shutdown_started.set()
        super().on_unmount()


class ReviewRailAppTest(unittest.IsolatedAsyncioTestCase):
    async def wait_for(self, event: Event) -> None:
        while not event.is_set():
            await asyncio.sleep(0)

    async def settle(self, app: ReviewRailApp, pilot: Pilot[None]) -> None:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

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
                await self.settle(app, pilot)
                self.assertEqual(app.current, 0)
                self.assertEqual(reloads, [b"patch for hm-one\n"])
                await pilot.press("j")
                await self.settle(app, pilot)
                self.assertEqual(app.current, 1)
                self.assertEqual(reloads[-1], b"patch for hm-two\n")

                await pilot.press("r")
                await pilot.pause()
                self.assertEqual(project.state["reviewed"], ["hm-two"])

                await pilot.press("a")
                await self.settle(app, pilot)
                self.assertEqual(app.current, 2)
                self.assertEqual(project.state["reviewed"], ["hm-two"])

    async def test_every_selection_entry_point_uses_background_reload(self) -> None:
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
                await self.settle(app, pilot)
                await pilot.press("j")
                await self.settle(app, pilot)
                await pilot.press("up")
                await self.settle(app, pilot)
                await pilot.press("down")
                await self.settle(app, pilot)
                self.assertTrue(await pilot.click("#unit-0"))
                await self.settle(app, pilot)
                app.action_last()
                await self.settle(app, pilot)
                app.action_first()
                await self.settle(app, pilot)
                app.action_next_unreviewed()
                await self.settle(app, pilot)
                await pilot.press("a")
                await self.settle(app, pilot)

                self.assertEqual(
                    reloads,
                    [
                        b"patch for hm-one\n",
                        b"patch for hm-two\n",
                        b"patch for hm-one\n",
                        b"patch for hm-two\n",
                        b"patch for hm-one\n",
                        b"patch for hm-three\n",
                        b"patch for hm-one\n",
                        b"patch for hm-two\n",
                        b"patch for hm-three\n",
                    ],
                )

    async def test_navigation_follows_chapter_order_not_raw_diff_order(self) -> None:
        project = FakeProject()
        project.manifest["hunks"][1]["group"] = "Adapter"
        project.manifest["hunks"][2]["group"] = "Core"
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
                await self.settle(app, pilot)

                await pilot.press("j")
                await self.settle(app, pilot)
                self.assertEqual(app.current, 2)
                self.assertEqual(reloads[-1], b"patch for hm-three\n")

                await pilot.press("j")
                await pilot.pause()
                self.assertEqual(app.current, 1)

                await pilot.press("k")
                await pilot.pause()
                self.assertEqual(app.current, 2)

                app.action_first()
                await self.settle(app, pilot)
                self.assertEqual(app.current, 0)
                await pilot.press("a")
                await self.settle(app, pilot)
                self.assertEqual(project.state["reviewed"], ["hm-one"])
                self.assertEqual(app.current, 2)

                app.action_last()
                await self.settle(app, pilot)
                self.assertEqual(app.current, 1)

    async def test_blocked_reload_does_not_block_map_and_coalesces_to_latest(self) -> None:
        project = FakeProject()
        reloader = BlockingReloader(fail_first=True)
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            app = ReviewRailApp(
                project,  # type: ignore[arg-type]
                runtime,
                "hunk",
                "session",
                patch_reloader=reloader,
            )
            async with app.run_test(size=(72, 24)) as pilot:
                await self.wait_for(reloader.started)
                try:
                    await pilot.press("j")
                    self.assertEqual(app.current, 1)
                    self.assertEqual(app.expanded, "Core")
                    self.assertTrue(
                        app.query_one("#unit-1", ReviewRow).has_class("current")
                    )
                    self.assertIn(
                        "Remember review progress",
                        app.query_one("#why", Static).render().plain,
                    )

                    await pilot.press("j")
                    self.assertEqual(app.current, 2)
                    self.assertEqual(app.expanded, "Adapter")
                finally:
                    reloader.release.set()

                await self.settle(app, pilot)
                self.assertEqual(
                    reloader.calls,
                    [b"patch for hm-one\n", b"patch for hm-three\n"],
                )
                self.assertEqual(reloader.max_active, 1)
                self.assertEqual(app.message, "")
                self.assertEqual(
                    (runtime / "selected.patch").read_bytes(),
                    b"patch for hm-three\n",
                )
                self.assertFalse((runtime / ".selected.patch.tmp").exists())

    async def test_reload_failure_keeps_selection_and_later_selection_retries(self) -> None:
        project = FakeProject()
        reloader = FailingOnceReloader()
        with TemporaryDirectory() as directory:
            app = ReviewRailApp(
                project,  # type: ignore[arg-type]
                Path(directory),
                "hunk",
                "session",
                patch_reloader=reloader,
            )
            async with app.run_test(size=(72, 24)) as pilot:
                await self.wait_for(reloader.failed)
                await self.settle(app, pilot)
                self.assertEqual(app.current, 0)
                self.assertIn("test reload failure", app.message)

                await pilot.press("j")
                await self.settle(app, pilot)
                self.assertEqual(app.current, 1)
                self.assertEqual(app.message, "")
                self.assertEqual(
                    reloader.calls,
                    [b"patch for hm-one\n", b"patch for hm-two\n"],
                )

    async def test_shutdown_drains_in_flight_background_work(self) -> None:
        project = BlockingCoverageProject()
        reloader = BlockingReloader(fail_first=True)
        shutdown_started = Event()
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        unobserved: list[dict[str, object]] = []
        loop.set_exception_handler(
            lambda _loop, context: unobserved.append(context)
        )
        self.addCleanup(
            loop.set_exception_handler,
            previous_exception_handler,
        )

        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            app = ShutdownAwareReviewRailApp(
                project,  # type: ignore[arg-type]
                runtime,
                "hunk",
                "session",
                patch_reloader=reloader,
            )
            app.shutdown_started = shutdown_started

            def release_after_shutdown_starts() -> None:
                shutdown_started.wait()
                reloader.release.set()
                project.coverage_release.set()

            releaser = Thread(target=release_after_shutdown_starts)
            workers = []
            releaser.start()
            try:
                async with app.run_test(size=(72, 24)):
                    await self.wait_for(reloader.started)
                    app._check_coverage()
                    await self.wait_for(project.coverage_started)
                    workers = [
                        worker
                        for worker in app.workers
                        if worker.name in {"hunk-reload", "coverage"}
                    ]
                    self.assertEqual(len(workers), 2)
                    self.assertTrue(all(worker.is_running for worker in workers))
                    self.assertFalse(reloader.release.is_set())
                    self.assertFalse(project.coverage_release.is_set())
            finally:
                reloader.release.set()
                project.coverage_release.set()
                releaser.join()

            await asyncio.sleep(0)
            self.assertTrue(shutdown_started.is_set())
            self.assertTrue(all(worker.is_finished for worker in workers))
            self.assertFalse(app._reload_running)
            self.assertFalse(app._coverage_running)
            self.assertEqual(reloader.active, 0)
            self.assertFalse(
                any(thread.is_alive() for thread in app._background._threads)
            )
            self.assertEqual(unobserved, [])
            self.assertFalse((runtime / ".selected.patch.tmp").exists())

    async def test_slow_coverage_is_non_blocking_non_overlapping_and_fail_closed(
        self,
    ) -> None:
        project = BlockingCoverageProject()
        with TemporaryDirectory() as directory:
            app = ReviewRailApp(
                project,  # type: ignore[arg-type]
                Path(directory),
                "hunk",
                "session",
                patch_reloader=lambda _hunk, _session, _path: None,
            )
            async with app.run_test(size=(72, 24)) as pilot:
                app._check_coverage()
                await self.wait_for(project.coverage_started)
                try:
                    app._check_coverage()
                    app._check_coverage()
                    self.assertEqual(project.coverage_calls, 1)

                    await pilot.press("j")
                    self.assertEqual(app.current, 1)
                    project.coverage_ok = False
                finally:
                    project.coverage_release.set()

                await self.settle(app, pilot)
                self.assertFalse(app.coverage_ok)
                self.assertIn(
                    "DIFF STALE",
                    app.query_one("#progress", Static).render().plain,
                )

                await pilot.press("r")
                await pilot.pause()
                self.assertEqual(project.state["reviewed"], [])
                self.assertIn("DIFF STALE", app.message)

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

    async def test_moving_cursor_to_closed_chapter_expands_it(self) -> None:
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
                self.assertEqual(app.expanded, "Core")

                rail = app.query_one("#rail", ListView)
                chapter = app.query_one("#group-1", ReviewRow)
                rail.index = list(rail.children).index(chapter)
                await pilot.pause()

                self.assertEqual(app.expanded, "Adapter")
                self.assertEqual(rail.highlighted_child.id, "group-1")
                self.assertIsNotNone(app.query_one("#unit-2", ReviewRow))
                self.assertEqual(app.current, 0)

    async def test_click_that_moves_to_closed_chapter_leaves_it_open(self) -> None:
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
                self.assertTrue(await pilot.click("#group-1"))
                await pilot.pause()
                self.assertEqual(app.expanded, "Adapter")

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
                await app.workers.wait_for_complete()
                await pilot.press("r")
                await pilot.pause()
                self.assertEqual(project.state["reviewed"], [])
                self.assertIn("DIFF STALE", app.message)


if __name__ == "__main__":
    unittest.main()
