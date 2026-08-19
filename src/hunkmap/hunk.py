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
    run(hunk, "session", "reload", session_id, "--json", "--", "patch", str(patch))
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        session = next((item for item in sessions(hunk) if item.get("sessionId") == session_id), None)
        snapshot = session.get("snapshot", {}).get("state", {}) if session else {}
        if session and len(session.get("files", [])) == 1 and int(snapshot.get("selectedHunkIndex", 0)) == 0:
            return
        time.sleep(0.05)
    raise HunkMapError("Hunk did not finish loading the selected review unit")
