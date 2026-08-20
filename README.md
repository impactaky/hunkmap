# hunkmap

`hunkmap` turns a live Git diff into a semantic review map whose raw-hunk
coverage is mechanically checked. Review state is attached to content-derived
hunk IDs, so changed hunks lose their reviewed status after refresh.

## Install

```bash
uv tool install .
```

Use `uv tool install --editable .` for a development checkout whose CLI should
follow local source changes.

## Agent skill

The repository includes an English, environment-independent agent skill at
[`skills/review-with-hunkmap`](skills/review-with-hunkmap). Install or link that
directory using your agent's normal skill-discovery mechanism, then invoke
`$review-with-hunkmap` to prepare a complete semantic Review Map. The core
workflow needs the `hunkmap` CLI; opening the interactive Review Rail also needs
`herdr` and `hunk` on `PATH`.

## Quick start

```bash
uv run hunkmap --repo /path/to/repo sync --base HEAD
uv run hunkmap --repo /path/to/repo context --patches
uv run hunkmap --repo /path/to/repo draft-map
# Edit the printed temporary JSON path.
uv run hunkmap --repo /path/to/repo apply-map /tmp/hunkmap-review-map.json
uv run hunkmap --repo /path/to/repo open
```

The default local state is kept under Git's metadata directory, so it does not
pollute the reviewed worktree. In a normal checkout this is `.git/hunkmap/`; Git
worktrees resolve to their own metadata directory automatically:

```text
.git/hunkmap/
├── snapshot.patch
├── manifest.json
└── state.json
```

`check` succeeds only when every snapshot unit appears exactly once in the
manifest and the live Git diff still matches the snapshot:

```text
FILES       3/3
HUNKS       28/28
MISSING     0
DUPLICATE   0
EXTRA       0
MISMATCH    0
DIFF        clean
```

Pass `--state-dir .hunkmap` when an editable worktree-local manifest is useful.

Text hunks are individual review units. Binary, rename-only, mode-only, and
other hunk-less file diffs become one file-level unit, so they cannot disappear
from coverage. Untracked files are included; a worktree-local state directory is
excluded from its own snapshot.

`sync` initializes a missing review map with `--base` and otherwise refreshes
the existing map with its recorded base. It is safe to use as the normal entry
point each time. Annotations and reviewed state survive only for unchanged hunk
IDs. `init` and `refresh` remain available when the explicit distinction is
useful.

`draft-map` writes the current units and annotations to a unique temporary JSON
file and prints its path. Edit that file, then apply the complete semantic map
atomically. Chapter and unit order in the document becomes the Review Rail
order:

```json
{
  "goal": "Keep the previous environment usable when replacement fails",
  "chapters": [{
    "name": "Rollback",
    "units": [{
      "id": "hm-0123456789abcdef",
      "label": "Restore the old tree",
      "why": "A failed switch must not discard the last successful environment",
      "safety": "Only the staged replacement is removed",
      "evidence": "Rollback integration test passes"
    }]
  }]
}
```

Every current review unit must occur exactly once, and all semantic fields must
be non-empty. Unknown or duplicate IDs, missing units, malformed chapters, or
empty fields reject the whole document without changing the manifest. Omit the
path to read JSON from stdin. `goal` and `annotate` remain available for focused
manual edits.

`open` runs the exact/current diff checks and additionally rejects an empty
goal, unassigned chapters, generated placeholder labels, and empty Why, Safety,
or Evidence fields. It opens the Review Rail only when all checks pass. Use
`check --ready` to run the same validation without opening anything.

`context --patches` emits the goal, chapter order, review state, Label, Why,
Safety, Evidence, and every raw patch as one reviewer-ready document.

## Interactive review

The CLI uses Textual for its interactive Review Rail. `open` and the lower-level
`herdr` command require the `herdr` and `hunk` executables:

```bash
hunkmap --repo /path/to/repo open
```

It opens a chapter-organized Review Rail beside Hunk. hunkmap materializes only
the selected review unit in a temporary Git source, so Hunk 0.19 can reload it
as a normal diff and scrolling a long patch cannot move the semantic map.

```text
j/k or wheel   previous / next unit
h/l            fold / expand the current group
r              toggle reviewed
a              review and continue to the next unreviewed unit
n/N            next / previous unreviewed unit
i              Why / Safety / Evidence
```

Textual provides the rail's keyboard bindings, mouse interaction, scrolling,
layout, and terminal restoration. The rail checks the live diff periodically.
If it changes after snapshotting, the header becomes `DIFF STALE` and reviewed
state cannot be changed until `hunkmap sync` is run.
