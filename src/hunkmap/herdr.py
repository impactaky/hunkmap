"""Launch hunkmap's Review Rail beside Hunk in Herdr."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from hunkmap.hunk import run
from hunkmap.store import HunkMapError, ReviewProject


def herdr_json(*args: str) -> dict:
    try:
        return json.loads(run("herdr", *args).stdout)
    except json.JSONDecodeError as error:
        raise HunkMapError("Herdr returned invalid JSON") from error


def split(workspace: str, pane: str, direction: str, ratio: str, cwd: Path) -> str:
    before = {item["pane_id"] for item in herdr_json("pane", "list", "--workspace", workspace)["result"]["panes"]}
    herdr_json(
        "pane", "split", "--pane", pane, "--direction", direction,
        "--ratio", ratio, "--cwd", str(cwd), "--no-focus",
    )
    after = {item["pane_id"] for item in herdr_json("pane", "list", "--workspace", workspace)["result"]["panes"]}
    created = after - before
    if len(created) != 1:
        raise HunkMapError("could not identify the new Herdr pane")
    return created.pop()


def hunk_pid(pane: str) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        info = herdr_json("pane", "process-info", "--pane", pane)["result"]["process_info"]
        match = next((item for item in info.get("foreground_processes", []) if item.get("name") == "hunk"), None)
        if match:
            return int(match["pid"])
        time.sleep(0.05)
    raise HunkMapError("Hunk did not start in the Herdr pane")


def find_hunk() -> str:
    direct = shutil.which("hunk")
    if direct:
        return direct
    mise = shutil.which("mise")
    if mise:
        result = subprocess.run((mise, "which", "hunk"), text=True, capture_output=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise HunkMapError("hunk executable was not found")


def launch(project: ReviewProject) -> None:
    coverage = project.coverage()
    if not coverage.ok:
        raise HunkMapError("coverage is incomplete or the diff is stale; run `hunkmap check` and `hunkmap refresh`")
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace or not shutil.which("herdr"):
        raise HunkMapError("Herdr is not available in this terminal")

    runtime = Path(tempfile.mkdtemp(prefix="hunkmap-herdr-"))
    first_patch = runtime / "selected.patch"
    first_patch.write_bytes(project.units()[0].patch)
    subprocess.run(("git", "init", "--quiet", str(runtime)), check=True)

    created = herdr_json(
        "tab", "create", "--workspace", workspace, "--cwd", str(project.repo),
        "--label", f"hunkmap · {project.repo.name}", "--no-focus",
    )["result"]
    tab = created["tab"]["tab_id"]
    rail = created["root_pane"]["pane_id"]
    diff_pane = split(workspace, rail, "right", "0.40", runtime)
    hunk = find_hunk()
    run(
        "herdr", "pane", "run", diff_pane, hunk, "patch", str(first_patch),
        "--pager", "--hunk-headers", "--mode", "stack", "--no-wrap",
    )
    pid = hunk_pid(diff_pane)
    run(
        "herdr", "pane", "run", rail, sys.executable, "-m", "hunkmap.ui",
        "--repo", str(project.repo), "--state-dir", str(project.state_dir),
        "--runtime", str(runtime), "--hunk", hunk, "--hunk-pid", str(pid),
    )
    run("herdr", "pane", "rename", rail, "hunkmap · semantic review + coverage")
    run("herdr", "pane", "rename", diff_pane, "Hunk · selected diff only")
    run("herdr", "tab", "focus", tab)
    print(json.dumps({"tab_id": tab, "rail": rail, "hunk": diff_pane, "runtime": str(runtime)}))
