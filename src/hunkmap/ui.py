"""Textual semantic Review Rail linked to one Hunk session."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from hunkmap.hunk import bind_session, reload_patch
from hunkmap.store import ReviewProject


def groups(entries: list[dict[str, Any]]) -> list[tuple[str, list[int]]]:
    ordered: list[tuple[str, list[int]]] = []
    positions: dict[str, int] = {}
    for index, entry in enumerate(entries):
        name = entry.get("group") or "Unassigned"
        if name not in positions:
            positions[name] = len(ordered)
            ordered.append((name, []))
        ordered[positions[name]][1].append(index)
    return ordered


def review_order(entries: list[dict[str, Any]]) -> list[int]:
    """Return manifest indices in the same order shown by the Review Map."""
    return [index for _chapter, members in groups(entries) for index in members]


def adjacent_review_unit(
    current: int,
    entries: list[dict[str, Any]],
    direction: int,
) -> int:
    order = review_order(entries)
    position = order.index(current)
    target = max(0, min(len(order) - 1, position + direction))
    return order[target]


def next_unreviewed(
    current: int,
    reviewed: set[str],
    entries: list[dict[str, Any]],
    direction: int = 1,
) -> int | None:
    order = review_order(entries)
    position = order.index(current)
    for offset in range(1, len(order) + 1):
        candidate = order[(position + direction * offset) % len(order)]
        if entries[candidate]["id"] not in reviewed:
            return candidate
    return None


def write_patch(project: ReviewProject, runtime: Path, index: int) -> Path:
    entry = project.load_manifest()["hunks"][index]
    unit = project.unit_for(entry["id"])
    path = runtime / "selected.patch"
    temporary = runtime / ".selected.patch.tmp"
    try:
        temporary.write_bytes(unit.patch)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


class ReviewRow(ListItem):
    """One purpose group or review unit in the rail."""

    class Clicked(Message):
        def __init__(self, row: ReviewRow) -> None:
            self.row = row
            super().__init__()

    class RightClicked(Message):
        def __init__(self, row: ReviewRow) -> None:
            self.row = row
            super().__init__()

    def __init__(self, text: str, *, kind: str, value: str | int, row_id: str) -> None:
        super().__init__(Label(text, markup=False), id=row_id, classes=kind)
        self.kind = kind
        self.value = value

    def _on_click(self, event: events.Click) -> None:
        if event.button == 3:
            event.stop()
            self.post_message(self.RightClicked(self))
            return
        if event.button == 1:
            event.stop()
            self.post_message(self.Clicked(self))


class ReviewOverlay(ModalScreen[None]):
    """Keyboard-dismissable help or review-detail overlay."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("question_mark", "dismiss", "Close", show=False),
        Binding("i", "dismiss", "Close", show=False),
    ]

    CSS = """
    ReviewOverlay {
        align: center middle;
        background: $background 60%;
    }
    ReviewOverlay > Container {
        width: 86%;
        max-width: 78;
        height: auto;
        max-height: 88%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    """

    def __init__(self, title: str, lines: list[tuple[str, str]]) -> None:
        super().__init__()
        self.title = title
        self.lines = lines

    def compose(self) -> ComposeResult:
        content = Text(self.title, style="bold cyan")
        for label, value in self.lines:
            content.append("\n\n" if label else "\n")
            if label:
                content.append(f"{label:<10}", style="bold")
            content.append(value)
        yield Container(Static(content))


PatchReloader = Callable[[str, str, Path], None]


class ReviewRailApp(App[None]):
    """Purpose map whose current unit is mirrored into a Hunk pane."""

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
        color: $text;
    }
    #goal {
        height: auto;
        min-height: 1;
        padding: 0 1;
        color: $text;
    }
    #progress {
        height: auto;
        max-height: 2;
        padding: 0 1;
        text-style: bold;
    }
    #rail {
        height: 1fr;
        background: $surface;
        scrollbar-size: 1 1;
    }
    ReviewRow {
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    ReviewRow.group {
        text-style: bold;
    }
    ReviewRow.unit {
        padding-left: 4;
    }
    ReviewRow.current {
        color: cyan;
        text-style: bold;
        background: $boost;
    }
    ReviewRow.-highlight {
        color: cyan;
        text-style: bold;
        background: $boost;
    }
    #why {
        height: auto;
        max-height: 5;
        padding: 0 1;
        border-top: solid $panel-lighten-2;
    }
    #message {
        height: auto;
        max-height: 2;
        padding: 0 1;
        color: yellow;
    }
    #keys {
        height: auto;
        max-height: 2;
        padding: 0 1;
        color: $text-muted;
        background: $panel;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit", show=False),
        Binding("j", "next_unit", "Next", show=False, priority=True),
        Binding("down", "next_unit", "Next", show=False, priority=True),
        Binding("k", "previous_unit", "Previous", show=False, priority=True),
        Binding("up", "previous_unit", "Previous", show=False, priority=True),
        Binding("h", "fold", "Fold", show=False, priority=True),
        Binding("left", "fold", "Fold", show=False, priority=True),
        Binding("l", "expand", "Expand", show=False, priority=True),
        Binding("right", "expand", "Expand", show=False, priority=True),
        Binding("r", "toggle_reviewed", "Review", show=False),
        Binding("a", "review_and_next", "Review + next", show=False),
        Binding("n", "next_unreviewed", "Next unreviewed", show=False),
        Binding("shift+n", "previous_unreviewed", "Previous unreviewed", show=False),
        Binding("g", "first", "First", show=False),
        Binding("shift+g", "last", "Last", show=False),
        Binding("i", "show_info", "Info", show=False),
        Binding("question_mark", "show_help", "Help", show=False),
    ]

    def __init__(
        self,
        project: ReviewProject,
        runtime: Path,
        hunk: str,
        session_id: str,
        *,
        patch_reloader: PatchReloader = reload_patch,
    ) -> None:
        super().__init__()
        self.project = project
        self.runtime = runtime
        self.hunk = hunk
        self.session_id = session_id
        self.patch_reloader = patch_reloader
        self.manifest = project.load_manifest()
        self.entries: list[dict[str, Any]] = self.manifest["hunks"]
        self.state = project.load_state()
        self.reviewed = set(self.state.get("reviewed", []))
        self.current = 0
        self.expanded: str | None = self._group_for(0)
        self.coverage_ok = True
        self.message = ""
        self._updating_rows = False
        self._reload_generation = 0
        self._reload_pending: tuple[int, int] | None = None
        self._reload_running = False
        self._coverage_running = False
        self._background = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="hunkmap",
        )

    def compose(self) -> ComposeResult:
        yield Static(id="goal")
        yield Static(id="progress")
        yield ListView(id="rail")
        yield Static(id="why")
        yield Static(id="message")
        yield Static(
            "j/k move · h/l fold · r review · a review+next · n/N unreviewed · i info · ? keys",
            id="keys",
        )

    def on_mount(self) -> None:
        self._refresh_header()
        self._rebuild_rows()
        self._queue_patch_reload(self.current)
        self.query_one("#rail", ListView).focus()
        self.set_interval(1.0, self._check_coverage)

    def on_unmount(self) -> None:
        self._reload_pending = None
        self._background.shutdown(wait=True, cancel_futures=True)

    def _group_for(self, index: int) -> str:
        return self.entries[index].get("group") or "Unassigned"

    def _group_rows(self) -> list[tuple[str, list[int]]]:
        return groups(self.entries)

    def _reload_patch(self, index: int) -> Exception | None:
        try:
            patch = write_patch(self.project, self.runtime, index)
            self.patch_reloader(self.hunk, self.session_id, patch)
        except Exception as error:
            return error
        return None

    def _coverage_ok(self) -> bool:
        try:
            return self.project.coverage().ok
        except Exception:
            return False

    def _queue_patch_reload(self, index: int) -> None:
        self._reload_generation += 1
        self._reload_pending = (self._reload_generation, index)
        if self._reload_running:
            return
        self._reload_running = True
        self.run_worker(
            self._drain_patch_reloads(),
            name="hunk-reload",
            group="hunk-reload",
            exit_on_error=False,
        )

    async def _drain_patch_reloads(self) -> None:
        try:
            while self._reload_pending is not None:
                generation, index = self._reload_pending
                self._reload_pending = None
                try:
                    error = await asyncio.get_running_loop().run_in_executor(
                        self._background,
                        self._reload_patch,
                        index,
                    )
                except Exception as scheduling_error:
                    error = scheduling_error
                if (
                    error is not None
                    and self.is_running
                    and generation == self._reload_generation
                ):
                    self.message = f"Hunk reload failed — {error}"
                    self._refresh_header()
        finally:
            self._reload_running = False

    def _check_coverage(self) -> None:
        if self._coverage_running:
            return
        self._coverage_running = True
        self.run_worker(
            self._check_coverage_in_background(),
            name="coverage",
            group="coverage",
            exit_on_error=False,
        )

    async def _check_coverage_in_background(self) -> None:
        try:
            try:
                ok = await asyncio.get_running_loop().run_in_executor(
                    self._background,
                    self._coverage_ok,
                )
            except Exception:
                ok = False
            if self.is_running and ok != self.coverage_ok:
                self.coverage_ok = ok
                self._refresh_header()
        finally:
            self._coverage_running = False

    def _refresh_header(self) -> None:
        goal = self.manifest.get("goal") or "Not set — use `hunkmap goal ...`"
        done = sum(entry["id"] in self.reviewed for entry in self.entries)
        blocks = round(done / len(self.entries) * 12)
        coverage = "COVERAGE OK" if self.coverage_ok else "DIFF STALE"
        self.query_one("#goal", Static).update(
            Text.assemble(("GOAL  ", "bold cyan"), goal)
        )
        self.query_one("#progress", Static).update(
            f"PROGRESS  {'█' * blocks}{'░' * (12 - blocks)}  {done}/{len(self.entries)} reviewed · {coverage}"
        )
        self.query_one("#why", Static).update(
            Text.assemble(
                ("WHY  ", "bold"), self.entries[self.current].get("why") or "WHY未設定"
            )
        )
        self.query_one("#message", Static).update(self.message)

    def _make_rows(
        self, highlight: tuple[str, str | int] | None = None
    ) -> tuple[list[ReviewRow], int]:
        rows: list[ReviewRow] = []
        selected_row = 0
        current_group = self._group_for(self.current)
        for group_index, (name, members) in enumerate(self._group_rows()):
            done = sum(self.entries[index]["id"] in self.reviewed for index in members)
            symbol = "✓" if done == len(members) else "◐" if done else "○"
            pointer = "▶" if name == current_group else " "
            fold = "▾" if name == self.expanded else "▸"
            group_row = ReviewRow(
                f"{pointer} {group_index + 1} {fold} {symbol} {name}  {done}/{len(members)}",
                kind="group",
                value=name,
                row_id=f"group-{group_index}",
            )
            if name == current_group:
                group_row.add_class("current-group")
            rows.append(group_row)
            if highlight == ("group", name):
                selected_row = len(rows) - 1
            if name != self.expanded:
                continue
            for index in members:
                entry = self.entries[index]
                mark = (
                    "●"
                    if index == self.current
                    else "✓"
                    if entry["id"] in self.reviewed
                    else "○"
                )
                suffix = (
                    " ✓"
                    if index == self.current and entry["id"] in self.reviewed
                    else ""
                )
                row = ReviewRow(
                    f"{mark}{suffix} {entry.get('label') or entry['id']}",
                    kind="unit",
                    value=index,
                    row_id=f"unit-{index}",
                )
                if index == self.current:
                    row.add_class("current")
                    selected_row = len(rows)
                rows.append(row)
                if highlight == ("unit", index):
                    selected_row = len(rows) - 1
        return rows, selected_row

    def _rebuild_rows(self, highlight: tuple[str, str | int] | None = None) -> None:
        rail = self.query_one("#rail", ListView)
        rows, selected_row = self._make_rows(highlight)
        self._updating_rows = True

        async def replace() -> None:
            await rail.clear()
            await rail.extend(rows)
            rail.index = selected_row
            self._updating_rows = False

        self.run_worker(replace, exclusive=True, group="rows")
        self._refresh_header()

    def _select(self, index: int) -> None:
        index = max(0, min(len(self.entries) - 1, index))
        if index == self.current:
            return
        self.current = index
        self.expanded = self._group_for(index)
        self.message = ""
        self._rebuild_rows()
        self._queue_patch_reload(index)

    def _set_reviewed(self, reviewed: bool, *, advance: bool = False) -> None:
        if not self.coverage_ok:
            self.message = "DIFF STALE — refreshするまでreview状態を変更できません"
            self._refresh_header()
            return
        identifier = self.entries[self.current]["id"]
        if reviewed:
            self.reviewed.add(identifier)
        else:
            self.reviewed.discard(identifier)
        self.state["reviewed"] = sorted(self.reviewed)
        self.project.write_state(self.state)
        if advance:
            following = next_unreviewed(self.current, self.reviewed, self.entries)
            if following is not None and following != self.current:
                self._select(following)
                return
        self._rebuild_rows()

    @on(ListView.Highlighted)
    def _highlighted(self, event: ListView.Highlighted) -> None:
        if self._updating_rows or not isinstance(event.item, ReviewRow):
            return
        if event.item.kind == "unit":
            self._select(int(event.item.value))
        elif event.item.kind == "group":
            name = str(event.item.value)
            if self.expanded != name:
                self.expanded = name
                self._rebuild_rows(highlight=("group", name))

    @on(ReviewRow.Clicked)
    def _clicked(self, event: ReviewRow.Clicked) -> None:
        rail = self.query_one("#rail", ListView)
        rail.focus()
        if event.row.kind == "group":
            name = str(event.row.value)
            self.expanded = None if self.expanded == name else name
            self._rebuild_rows(highlight=("group", name))
        else:
            rail.index = list(rail.children).index(event.row)

    @on(ListView.Selected)
    def _selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, ReviewRow):
            return
        if event.item.kind == "group":
            name = str(event.item.value)
            self.expanded = None if self.expanded == name else name
            self._rebuild_rows(highlight=("group", name))

    @on(ReviewRow.RightClicked)
    def _right_clicked(self, event: ReviewRow.RightClicked) -> None:
        if event.row.kind != "unit":
            return
        self._select(int(event.row.value))
        self._set_reviewed(True, advance=True)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.action_next_unit()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.action_previous_unit()

    def action_next_unit(self) -> None:
        self._select(adjacent_review_unit(self.current, self.entries, 1))

    def action_previous_unit(self) -> None:
        self._select(adjacent_review_unit(self.current, self.entries, -1))

    def action_first(self) -> None:
        self._select(review_order(self.entries)[0])

    def action_last(self) -> None:
        self._select(review_order(self.entries)[-1])

    def action_fold(self) -> None:
        self.expanded = None
        self._rebuild_rows()

    def action_expand(self) -> None:
        self.expanded = self._group_for(self.current)
        self._rebuild_rows()

    def action_toggle_reviewed(self) -> None:
        identifier = self.entries[self.current]["id"]
        self._set_reviewed(identifier not in self.reviewed)

    def action_review_and_next(self) -> None:
        self._set_reviewed(True, advance=True)

    def action_next_unreviewed(self) -> None:
        target = next_unreviewed(self.current, self.reviewed, self.entries)
        if target is not None:
            self._select(target)

    def action_previous_unreviewed(self) -> None:
        target = next_unreviewed(self.current, self.reviewed, self.entries, -1)
        if target is not None:
            self._select(target)

    def action_show_help(self) -> None:
        self.push_screen(
            ReviewOverlay(
                "KEYS",
                [
                    ("j / ↓", "next hunk"),
                    ("k / ↑", "previous hunk"),
                    ("h / ←", "fold group"),
                    ("l / →", "expand group"),
                    ("r", "toggle reviewed"),
                    ("a", "review + next unreviewed"),
                    ("n / N", "next / previous unreviewed"),
                    ("g / G", "first / last"),
                    ("mouse", "click item, right-click review+next, wheel move"),
                    ("close", "Esc / ?"),
                ],
            )
        )

    def action_show_info(self) -> None:
        entry = self.entries[self.current]
        self.push_screen(
            ReviewOverlay(
                f"REVIEW INFO  {entry['id']} · {entry['path']} · h{entry['ordinal'] + 1}",
                [
                    ("Why", entry.get("why") or "未設定"),
                    ("Safety", entry.get("safety") or "未設定"),
                    ("Evidence", entry.get("evidence") or "未設定"),
                    ("close", "Esc / i"),
                ],
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--hunk", required=True)
    parser.add_argument("--hunk-pid", type=int, required=True)
    args = parser.parse_args()
    project = ReviewProject(args.repo, args.state_dir)
    session_id = bind_session(args.hunk, args.hunk_pid)["sessionId"]
    ReviewRailApp(project, args.runtime, args.hunk, session_id).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
