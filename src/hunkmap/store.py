"""Persistent snapshot, manifest, review state, and coverage validation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from hunkmap.diff import DiffUnit, parse_patch, sha256


class HunkMapError(RuntimeError):
    pass


@dataclass(frozen=True)
class Coverage:
    files_total: int
    files_mapped: int
    hunks_total: int
    hunks_mapped: int
    missing: tuple[str, ...]
    duplicate: tuple[str, ...]
    extra: tuple[str, ...]
    mismatch: tuple[str, ...]
    stale: bool

    @property
    def ok(self) -> bool:
        return not self.missing and not self.duplicate and not self.extra and not self.mismatch and not self.stale


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(("git", *args), cwd=repo, capture_output=True)
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise HunkMapError(message or f"git {' '.join(args)} failed")
    return result


def find_repo(path: Path) -> Path:
    result = run_git(path, "rev-parse", "--show-toplevel")
    return Path(result.stdout.decode().strip()).resolve()


def _state_relative(repo: Path, state_dir: Path) -> str | None:
    try:
        return state_dir.resolve().relative_to(repo).as_posix()
    except ValueError:
        return None


def capture_diff(repo: Path, base: str, state_dir: Path) -> bytes:
    resolved = run_git(repo, "rev-parse", "--verify", base, check=False)
    if resolved.returncode == 0:
        diff_base = resolved.stdout.decode().strip()
    elif base == "HEAD":
        empty_tree = run_git(repo, "hash-object", "-t", "tree", "/dev/null")
        diff_base = empty_tree.stdout.decode().strip()
    else:
        raise HunkMapError(resolved.stderr.decode("utf-8", "replace").strip() or f"unknown base: {base}")
    relative = _state_relative(repo, state_dir)
    pathspec = ["--", "."]
    if relative:
        pathspec.append(f":(exclude){relative}/**")
    tracked = run_git(repo, "diff", "--binary", "--no-ext-diff", "--no-color", diff_base, *pathspec).stdout

    others = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout.split(b"\0")
    untracked_patches: list[bytes] = []
    for raw_path in others:
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", "surrogateescape")
        if relative and (path == relative or path.startswith(relative + "/")):
            continue
        result = run_git(
            repo, "diff", "--binary", "--no-ext-diff", "--no-color", "--no-index",
            "--", "/dev/null", path, check=False,
        )
        if result.returncode not in (0, 1):
            raise HunkMapError(result.stderr.decode("utf-8", "replace").strip())
        untracked_patches.append(result.stdout)
    return tracked + b"".join(untracked_patches)


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HunkMapError(f"{label} must be a non-empty string")
    return value.strip()


def _entry(unit: DiffUnit, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    prior = previous or {}
    return {
        "id": unit.id,
        "path": unit.path,
        "ordinal": unit.ordinal,
        "kind": unit.kind,
        "header": unit.header,
        "digest": unit.digest,
        "group": prior.get("group", "Unassigned"),
        "label": prior.get("label", f"{Path(unit.path).name} h{unit.ordinal + 1}"),
        "why": prior.get("why", ""),
        "safety": prior.get("safety", ""),
        "evidence": prior.get("evidence", ""),
    }


class ReviewProject:
    def __init__(self, repo: Path, state_dir: Path):
        self.repo = repo.resolve()
        self.state_dir = state_dir if state_dir.is_absolute() else self.repo / state_dir
        self.snapshot_path = self.state_dir / "snapshot.patch"
        self.manifest_path = self.state_dir / "manifest.json"
        self.state_path = self.state_dir / "state.json"

    @classmethod
    def discover(cls, repo: Path | None = None, state_dir: Path | None = None) -> "ReviewProject":
        root = find_repo((repo or Path.cwd()).resolve())
        if state_dir is None:
            raw = run_git(root, "rev-parse", "--git-path", "hunkmap").stdout.decode().strip()
            state_dir = Path(raw)
        return cls(root, state_dir)

    def exists(self) -> bool:
        return self.snapshot_path.is_file() and self.manifest_path.is_file() and self.state_path.is_file()

    def require(self) -> None:
        if not self.exists():
            raise HunkMapError(f"review project is not initialized: {self.state_dir}")

    def load_manifest(self) -> dict[str, Any]:
        self.require()
        return json.loads(self.manifest_path.read_text())

    def load_state(self) -> dict[str, Any]:
        self.require()
        return json.loads(self.state_path.read_text())

    def units(self) -> list[DiffUnit]:
        self.require()
        return parse_patch(self.snapshot_path.read_bytes())

    def initialize(self, base: str = "HEAD", force: bool = False) -> None:
        if self.exists() and not force:
            raise HunkMapError(f"already initialized: {self.state_dir}; use refresh")
        patch = capture_diff(self.repo, base, self.state_dir)
        units = parse_patch(patch)
        if not units:
            raise HunkMapError("Git diff contains no reviewable changes")
        manifest = {
            "version": 1,
            "base": base,
            "repo": str(self.repo),
            "snapshot_sha256": sha256(patch),
            "goal": "",
            "hunks": [_entry(unit) for unit in units],
        }
        state = {"version": 1, "reviewed": [], "notes": {}}
        _atomic_bytes(self.snapshot_path, patch)
        _atomic_json(self.manifest_path, manifest)
        _atomic_json(self.state_path, state)

    def refresh(self) -> None:
        self.require()
        old_manifest = self.load_manifest()
        old_state = self.load_state()
        base = old_manifest["base"]
        patch = capture_diff(self.repo, base, self.state_dir)
        units = parse_patch(patch)
        if not units:
            raise HunkMapError("Git diff contains no reviewable changes")
        previous = {entry["id"]: entry for entry in old_manifest["hunks"]}
        current_ids = {unit.id for unit in units}
        manifest = {
            **old_manifest,
            "repo": str(self.repo),
            "snapshot_sha256": sha256(patch),
            "hunks": [_entry(unit, previous.get(unit.id)) for unit in units],
        }
        state = {
            **old_state,
            "reviewed": [item for item in old_state.get("reviewed", []) if item in current_ids],
            "notes": {key: value for key, value in old_state.get("notes", {}).items() if key in current_ids},
        }
        _atomic_bytes(self.snapshot_path, patch)
        _atomic_json(self.manifest_path, manifest)
        _atomic_json(self.state_path, state)

    def coverage(self, check_current: bool = True) -> Coverage:
        manifest = self.load_manifest()
        snapshot = self.snapshot_path.read_bytes()
        units = parse_patch(snapshot)
        expected = Counter(unit.id for unit in units)
        expected_by_id = {unit.id: unit for unit in units}
        mapped = Counter(entry.get("id", "") for entry in manifest.get("hunks", []))
        missing = tuple(sorted((expected - mapped).elements()))
        extra = tuple(sorted((mapped - expected).elements()))
        duplicate = tuple(sorted(key for key, count in mapped.items() if count > 1))
        mismatch = tuple(sorted(
            entry.get("id", "")
            for entry in manifest.get("hunks", [])
            if entry.get("id") in expected_by_id and any((
                entry.get("path") != expected_by_id[entry["id"]].path,
                entry.get("ordinal") != expected_by_id[entry["id"]].ordinal,
                entry.get("kind") != expected_by_id[entry["id"]].kind,
                entry.get("digest") != expected_by_id[entry["id"]].digest,
            ))
        ))
        expected_files = {unit.path for unit in units}
        mapped_files = {entry.get("path", "") for entry in manifest.get("hunks", []) if entry.get("id") in expected}
        stale = manifest.get("snapshot_sha256") != sha256(snapshot)
        if check_current:
            current = capture_diff(self.repo, manifest["base"], self.state_dir)
            stale = stale or sha256(current) != sha256(snapshot)
        return Coverage(
            files_total=len(expected_files),
            files_mapped=len(mapped_files & expected_files),
            hunks_total=sum(expected.values()),
            hunks_mapped=sum(min(mapped[key], count) for key, count in expected.items()),
            missing=missing,
            duplicate=duplicate,
            extra=extra,
            mismatch=mismatch,
            stale=stale,
        )

    def semantic_issues(self) -> tuple[str, ...]:
        manifest = self.load_manifest()
        issues: list[str] = []
        if not isinstance(manifest.get("goal"), str) or not manifest["goal"].strip():
            issues.append("goal is empty")
        for entry in manifest.get("hunks", []):
            unit_id = entry.get("id") or "unknown"
            group = entry.get("group")
            if not isinstance(group, str) or not group.strip() or group == "Unassigned":
                issues.append(f"{unit_id}: chapter is unassigned")
            label = entry.get("label")
            path = entry.get("path")
            ordinal = entry.get("ordinal")
            default_label = (
                f"{Path(path).name} h{ordinal + 1}"
                if isinstance(path, str) and isinstance(ordinal, int)
                else None
            )
            if not isinstance(label, str) or not label.strip() or label == default_label:
                issues.append(f"{unit_id}: label is unset")
            for field in ("why", "safety", "evidence"):
                value = entry.get(field)
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"{unit_id}: {field} is empty")
        return tuple(issues)

    def resolve(self, selector: str) -> dict[str, Any]:
        entries = self.load_manifest()["hunks"]
        if selector.isdigit():
            index = int(selector) - 1
            if 0 <= index < len(entries):
                return entries[index]
        matches = [entry for entry in entries if entry["id"] == selector or entry["id"].startswith(selector)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise HunkMapError(f"unknown hunk selector: {selector}")
        raise HunkMapError(f"ambiguous hunk selector: {selector}")

    def unit_for(self, selector: str) -> DiffUnit:
        entry = self.resolve(selector)
        return next(unit for unit in self.units() if unit.id == entry["id"])

    def annotate(self, selector: str, **updates: str | None) -> dict[str, Any]:
        manifest = self.load_manifest()
        target = self.resolve(selector)
        entry = next(item for item in manifest["hunks"] if item["id"] == target["id"])
        for key, value in updates.items():
            if value is not None:
                entry[key] = value
        _atomic_json(self.manifest_path, manifest)
        return entry

    def set_reviewed(self, selector: str, reviewed: bool) -> dict[str, Any]:
        if not self.coverage().ok:
            raise HunkMapError("coverage is incomplete or the diff is stale; run sync")
        entry = self.resolve(selector)
        state = self.load_state()
        values = set(state.get("reviewed", []))
        if reviewed:
            values.add(entry["id"])
        else:
            values.discard(entry["id"])
        state["reviewed"] = sorted(values)
        _atomic_json(self.state_path, state)
        return entry

    def set_goal(self, goal: str) -> None:
        manifest = self.load_manifest()
        manifest["goal"] = goal
        _atomic_json(self.manifest_path, manifest)

    def apply_map(self, document: Any) -> tuple[int, int]:
        if not self.coverage().ok:
            raise HunkMapError("coverage is incomplete or the diff is stale; run sync")
        manifest = self.load_manifest()
        if not isinstance(document, dict):
            raise HunkMapError("review map must be a JSON object")
        if set(document) != {"goal", "chapters"}:
            raise HunkMapError("review map must contain only goal and chapters")

        goal = _required_text(document["goal"], "goal")
        chapters = document["chapters"]
        if not isinstance(chapters, list) or not chapters:
            raise HunkMapError("chapters must be a non-empty array")

        existing = {entry["id"]: entry for entry in manifest["hunks"]}
        seen_ids: set[str] = set()
        seen_chapters: set[str] = set()
        mapped: list[dict[str, Any]] = []
        unit_fields = {"id", "label", "why", "safety", "evidence"}

        for chapter_index, chapter in enumerate(chapters, 1):
            prefix = f"chapters[{chapter_index}]"
            if not isinstance(chapter, dict) or set(chapter) != {"name", "units"}:
                raise HunkMapError(f"{prefix} must contain only name and units")
            name = _required_text(chapter["name"], f"{prefix}.name")
            if name in seen_chapters:
                raise HunkMapError(f"duplicate chapter: {name}")
            seen_chapters.add(name)
            units = chapter["units"]
            if not isinstance(units, list) or not units:
                raise HunkMapError(f"{prefix}.units must be a non-empty array")

            for unit_index, unit in enumerate(units, 1):
                unit_prefix = f"{prefix}.units[{unit_index}]"
                if not isinstance(unit, dict) or set(unit) != unit_fields:
                    raise HunkMapError(
                        f"{unit_prefix} must contain only id, label, why, safety, and evidence"
                    )
                unit_id = _required_text(unit["id"], f"{unit_prefix}.id")
                if unit_id not in existing:
                    raise HunkMapError(f"unknown review unit: {unit_id}")
                if unit_id in seen_ids:
                    raise HunkMapError(f"duplicate review unit: {unit_id}")
                seen_ids.add(unit_id)
                mapped.append({
                    **existing[unit_id],
                    "group": name,
                    "label": _required_text(unit["label"], f"{unit_prefix}.label"),
                    "why": _required_text(unit["why"], f"{unit_prefix}.why"),
                    "safety": _required_text(unit["safety"], f"{unit_prefix}.safety"),
                    "evidence": _required_text(unit["evidence"], f"{unit_prefix}.evidence"),
                })

        missing = set(existing) - seen_ids
        if missing:
            raise HunkMapError("missing review units: " + ", ".join(sorted(missing)))

        _atomic_json(self.manifest_path, {**manifest, "goal": goal, "hunks": mapped})
        return len(chapters), len(mapped)

    def write_state(self, state: dict[str, Any]) -> None:
        _atomic_json(self.state_path, state)
