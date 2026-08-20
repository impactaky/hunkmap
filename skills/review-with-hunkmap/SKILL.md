---
name: review-with-hunkmap
description: Turn a live Git diff into a complete, purpose-oriented hunkmap Review Map with a Review Goal, Chapters, and evidence-based annotations for every Review Unit. Use when asked to organize a change for review, prepare a semantic diff map, verify that no diff hunk was omitted, or present a structured review rail. Do not use for an ordinary code review or a plain diff display unless semantic mapping is requested.
---

# Prepare a Review Map with hunkmap

Treat hunkmap as the source of truth for the diff, unit identities, coverage, and review state. Supply the semantic organization: Review Goal, Chapters, and Annotations.

Use the current repository unless the user specifies another one. Prefix commands with `hunkmap --repo <path>` when operating elsewhere. Use `HEAD` as the base for a new map unless another base is requested. Preserve the recorded base when a map already exists.

## 1. Synchronize the snapshot

```bash
hunkmap sync --base <base>
```

`sync` initializes a missing map or refreshes an existing map against its recorded base. It preserves annotations and Review State only for unchanged Unit Identities.

Check the reported coverage and unit count. If there is no reviewable diff, report that result instead of inventing an empty map.

## 2. Organize every Review Unit by purpose

Read all Review Units and their patches in one pass:

```bash
hunkmap context --patches
```

Inspect surrounding implementation, tests, and project documentation only as needed to understand the change. Do not infer purpose solely from filenames or diff order.

Define one Review Goal for the outcome of the complete change. Group every Review Unit into purpose-oriented Chapters, not file-oriented sections. Order Chapters and Units in the sequence a reviewer should follow.

Annotate each Unit ID exactly once:

- `label`: identify the change concisely.
- `why`: explain why this unit is needed for its Chapter or Review Goal; do not restate the label or patch.
- `safety`: state the affected boundary, preserved invariant, or failure behavior.
- `evidence`: cite tests, commands, contracts, or code relationships actually checked. State `Not verified` when appropriate; never invent successful validation.

Generate and edit a complete map document outside the repository:

```bash
hunkmap draft-map
hunkmap apply-map <draft-path>
```

Use every Unit ID emitted by the latest `context --patches` exactly once. If `apply-map` reports missing, duplicate, unknown, stale, or empty fields, correct the document and apply it again. A rejected document leaves the existing map unchanged.

## 3. Validate and present the map

For an interactive review environment with `herdr` and `hunk` installed, run:

```bash
hunkmap open
```

Otherwise validate without opening the Review Rail:

```bash
hunkmap check --ready
```

Both paths require Exact Coverage, a current Snapshot, and complete semantic fields. Judge the quality and truth of the prose yourself; mechanical readiness cannot do that.

If the Live Diff changed, run `sync` again, reread `context --patches`, reinterpret added or identity-changed units, and reapply the complete map. Never reuse stale Unit IDs.

Report the Review Goal, Chapter count, Review Unit count, readiness result, and whether the Review Rail opened.

Do not mark Units reviewed automatically. Review State records a human review decision, or the decision of a reviewer explicitly authorized to perform that review; it is not a completion marker for map preparation.

## Reviewer handoff

When the user explicitly requests a separate reviewer, pass the single `context --patches` document containing the Goal, Chapters, all Annotations, Review State, and every patch. Ask the reviewer to find defects, contract violations, and unverified boundaries. Do not split the document mechanically or change Review State merely because findings were produced.

## Integrity boundaries

- Keep raw patches authoritative; annotations must not replace, alter, or omit diff facts.
- Use hunkmap's Exact Coverage instead of maintaining a separate hunk inventory.
- Mark uncertain Safety or Evidence as unknown or unverified.
- Keep implementation work, ordinary code review, and plain diff display outside this workflow unless the user also requests a semantic Review Map.
