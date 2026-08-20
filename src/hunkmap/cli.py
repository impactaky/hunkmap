"""Command-line interface for hunkmap."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from hunkmap.store import HunkMapError, ReviewProject


def project_from(args: argparse.Namespace) -> ReviewProject:
    return ReviewProject.discover(args.repo, args.state_dir)


def coverage_text(project: ReviewProject, coverage=None) -> str:
    coverage = coverage or project.coverage()
    return "\n".join((
        f"FILES       {coverage.files_mapped}/{coverage.files_total}",
        f"HUNKS       {coverage.hunks_mapped}/{coverage.hunks_total}",
        f"MISSING     {len(coverage.missing)}",
        f"DUPLICATE   {len(coverage.duplicate)}",
        f"EXTRA       {len(coverage.extra)}",
        f"MISMATCH    {len(coverage.mismatch)}",
        f"DIFF        {'STALE' if coverage.stale else 'clean'}",
    ))


def report_check(project: ReviewProject, require_ready: bool) -> bool:
    coverage = project.coverage()
    print(coverage_text(project, coverage))
    issues = project.semantic_issues() if require_ready else ()
    if require_ready:
        print(f"SEMANTIC    {'ready' if not issues else f'{len(issues)} issue(s)'}")
        for issue in issues:
            print(f"  - {issue}")
    return coverage.ok and not issues


def context_markdown(project: ReviewProject, include_patches: bool) -> str:
    manifest = project.load_manifest()
    state = project.load_state()
    reviewed = set(state.get("reviewed", []))
    units = {unit.id: unit for unit in project.units()}
    lines = [f"# {manifest.get('goal') or 'Diff review'}", "", "## Coverage", "", "```text", coverage_text(project), "```"]
    groups: dict[str, list[dict]] = {}
    for entry in manifest["hunks"]:
        groups.setdefault(entry.get("group") or "Unassigned", []).append(entry)
    for group, entries in groups.items():
        lines.extend(("", f"## {group}", ""))
        for entry in entries:
            mark = "x" if entry["id"] in reviewed else " "
            lines.append(f"- [{mark}] `{entry['id']}` {entry['label']} — {entry.get('why') or 'WHY未設定'}")
            if include_patches:
                lines.extend(("", "```diff", units[entry["id"]].patch.decode("utf-8", "replace").rstrip(), "```", ""))
    return "\n".join(lines) + "\n"


def review_map_document(project: ReviewProject) -> dict:
    manifest = project.load_manifest()
    chapters: list[dict] = []
    positions: dict[str, int] = {}
    for entry in manifest["hunks"]:
        name = entry.get("group") or "Unassigned"
        if name not in positions:
            positions[name] = len(chapters)
            chapters.append({"name": name, "units": []})
        chapters[positions[name]]["units"].append({
            "id": entry["id"],
            "label": entry.get("label") or "",
            "why": entry.get("why") or "",
            "safety": entry.get("safety") or "",
            "evidence": entry.get("evidence") or "",
        })
    return {"goal": manifest.get("goal") or "", "chapters": chapters}


def write_draft_map(project: ReviewProject) -> Path:
    if not project.coverage().ok:
        raise HunkMapError("coverage is incomplete or the diff is stale; run sync")
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f"hunkmap-{project.repo.name}-",
        suffix=".json",
        delete=False,
    ) as handle:
        json.dump(review_map_document(project), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        return Path(handle.name)


def read_json(path: str) -> object:
    try:
        if path == "-":
            return json.load(sys.stdin)
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise HunkMapError(f"could not read review map: {error}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunkmap", description="Coverage-checked semantic Git diff review")
    parser.add_argument("--repo", type=Path, help="path inside the Git repository")
    parser.add_argument("--state-dir", type=Path, help="review state directory (default: Git metadata directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="snapshot the current diff")
    init.add_argument("--base", default="HEAD")

    sync = sub.add_parser("sync", help="initialize or refresh the current diff snapshot")
    sync.add_argument("--base", default="HEAD", help="base used only when initializing a new review map")

    sub.add_parser("refresh", help="refresh the snapshot and preserve unchanged annotations")
    check = sub.add_parser("check", help="fail unless coverage is exact and the diff is current")
    check.add_argument("--ready", action="store_true", help="also require complete semantic metadata")
    sub.add_parser("status", help="show coverage and review progress")
    sub.add_parser("list", help="list review units")

    show = sub.add_parser("show", help="print one review unit as a patch")
    show.add_argument("selector")

    annotate = sub.add_parser("annotate", help="set semantic metadata for one unit")
    annotate.add_argument("selector")
    annotate.add_argument("--group")
    annotate.add_argument("--label")
    annotate.add_argument("--why")
    annotate.add_argument("--safety")
    annotate.add_argument("--evidence")

    goal = sub.add_parser("goal", help="set the review goal")
    goal.add_argument("text")

    apply_map = sub.add_parser("apply-map", help="atomically apply a complete semantic review map")
    apply_map.add_argument("path", nargs="?", default="-", help="JSON file (default: stdin)")

    sub.add_parser("draft-map", help="write an editable review map JSON to a temporary file")

    review = sub.add_parser("review", help="mark a unit reviewed")
    review.add_argument("selector")
    unreview = sub.add_parser("unreview", help="clear reviewed state")
    unreview.add_argument("selector")

    context = sub.add_parser("context", help="emit organized review context")
    context.add_argument("--patches", action="store_true")

    sub.add_parser("open", help="check semantic readiness and open the Review Rail in Herdr")
    sub.add_parser("herdr", help="open the interactive Review Rail in Herdr")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        project = project_from(args)
        if args.command == "init":
            project.initialize(args.base)
            print(f"Initialized {project.state_dir}")
            print(coverage_text(project))
        elif args.command == "sync":
            if project.exists():
                project.refresh()
                action = "Refreshed"
            else:
                project.initialize(args.base)
                action = "Initialized"
            print(f"{action} {project.state_dir}")
            print(coverage_text(project))
            print(f"UNITS       {len(project.load_manifest()['hunks'])}")
        elif args.command == "refresh":
            project.refresh()
            print(f"Refreshed {project.state_dir}")
            print(coverage_text(project))
        elif args.command == "check":
            return 0 if report_check(project, args.ready) else 1
        elif args.command == "status":
            state = project.load_state()
            print(coverage_text(project))
            print(f"REVIEWED    {len(state.get('reviewed', []))}/{len(project.load_manifest()['hunks'])}")
        elif args.command == "list":
            reviewed = set(project.load_state().get("reviewed", []))
            for index, entry in enumerate(project.load_manifest()["hunks"], 1):
                mark = "x" if entry["id"] in reviewed else " "
                print(f"{index:>3} [{mark}] {entry['id']}  {entry['group']}  {entry['label']}")
        elif args.command == "show":
            sys.stdout.buffer.write(project.unit_for(args.selector).patch)
        elif args.command == "annotate":
            entry = project.annotate(
                args.selector, group=args.group, label=args.label, why=args.why,
                safety=args.safety, evidence=args.evidence,
            )
            print(f"Updated {entry['id']}")
        elif args.command == "goal":
            project.set_goal(args.text)
            print("Goal updated")
        elif args.command == "apply-map":
            chapters, units = project.apply_map(read_json(args.path))
            print(f"Applied {chapters} chapters to {units} review units")
        elif args.command == "draft-map":
            print(write_draft_map(project))
        elif args.command == "review":
            entry = project.set_reviewed(args.selector, True)
            print(f"Reviewed {entry['id']}")
        elif args.command == "unreview":
            entry = project.set_reviewed(args.selector, False)
            print(f"Unreviewed {entry['id']}")
        elif args.command == "context":
            print(context_markdown(project, args.patches), end="")
        elif args.command == "open":
            if not report_check(project, True):
                return 1
            from hunkmap.herdr import launch
            launch(project)
        elif args.command == "herdr":
            from hunkmap.herdr import launch
            launch(project)
        return 0
    except HunkMapError as error:
        print(f"hunkmap: {error}", file=sys.stderr)
        return 2
