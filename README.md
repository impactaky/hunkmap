# hunkmap

`hunkmap` turns a live Git diff into a semantic review map whose raw-hunk
coverage is mechanically checked. Review state is attached to content-derived
hunk IDs, so changed hunks lose their reviewed status after refresh.

## Quick start

```bash
uv run hunkmap --repo /path/to/repo init --base HEAD
uv run hunkmap --repo /path/to/repo check
uv run hunkmap --repo /path/to/repo list
uv run hunkmap --repo /path/to/repo annotate 1 \
  --group "Rollback" --label "旧treeを復旧" --why "切替失敗で成功済み環境を失わないため"
uv run hunkmap --repo /path/to/repo review 1
uv run hunkmap --repo /path/to/repo context
uv run hunkmap --repo /path/to/repo herdr
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

Use `refresh` after the diff changes. Annotations and reviewed state survive
only for unchanged hunk IDs.

## Interactive review

The CLI uses Textual for its interactive Review Rail. The optional `herdr`
command also requires the `herdr` and `hunk` executables:

```bash
hunkmap --repo /path/to/repo herdr
```

It opens a purpose-grouped Review Rail beside Hunk. Hunk receives only the
selected review unit, so scrolling a long patch cannot move the semantic map.

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
state cannot be changed until `hunkmap refresh` is run.
