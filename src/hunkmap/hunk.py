"""Small adapter around Hunk's session CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

from hunkmap.store import HunkMapError


def run(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        raise HunkMapError(error.stderr.strip() or "command failed: " + " ".join(args)) from error


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(("git", *args), cwd=repo, capture_output=True)
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise HunkMapError(message or f"git {' '.join(args)} failed")
    return result


def create_review_source(repo: Path, base: str, source: Path) -> Path:
    resolved = run_git(repo, "rev-parse", "--verify", base, check=False)
    if resolved.returncode == 0:
        commit = resolved.stdout.decode().strip()
        try:
            subprocess.run(
                ("git", "clone", "--quiet", "--shared", "--no-checkout", str(repo), str(source)),
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as error:
            message = error.stderr.decode("utf-8", "replace").strip()
            raise HunkMapError(message or "could not create the temporary review source") from error
        run_git(source, "checkout", "--quiet", "--detach", commit)
    elif base == "HEAD":
        run_git(source.parent, "init", "--quiet", str(source))
        run_git(
            source,
            "-c", "user.name=hunkmap",
            "-c", "user.email=hunkmap@localhost",
            "commit", "--quiet", "--allow-empty", "-m", "hunkmap empty baseline",
        )
    else:
        message = resolved.stderr.decode("utf-8", "replace").strip()
        raise HunkMapError(message or f"unknown base: {base}")
    return source


def prepare_review_source(source: Path, patch: Path) -> None:
    run_git(source, "reset", "--hard", "--quiet", "HEAD")
    run_git(source, "clean", "-fdq")
    run_git(source, "apply", "--binary", "--whitespace=nowarn", str(patch))
    run_git(source, "add", "--intent-to-add", "--all")


def sessions(hunk: str) -> list[dict]:
    try:
        return json.loads(run(hunk, "session", "list", "--json").stdout).get("sessions", [])
    except (json.JSONDecodeError, HunkMapError):
        return []


def bind_session(hunk: str, pid: int, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = next((item for item in sessions(hunk) if item.get("pid") == pid), None)
        if match:
            return match
        time.sleep(0.05)
    raise HunkMapError("Hunk session did not become available")


def reload_patch(hunk: str, session_id: str, patch: Path) -> None:
    source = patch.parent / "source"
    prepare_review_source(source, patch)
    run(
        hunk, "session", "reload", session_id,
        "--source", str(source), "--json", "--", "diff", "HEAD",
    )
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        session = next((item for item in sessions(hunk) if item.get("sessionId") == session_id), None)
        snapshot = session.get("snapshot", {}).get("state", {}) if session else {}
        if session and len(session.get("files", [])) == 1 and int(snapshot.get("selectedHunkIndex", 0)) == 0:
            return
        time.sleep(0.05)
    raise HunkMapError("Hunk did not finish loading the selected review unit")
