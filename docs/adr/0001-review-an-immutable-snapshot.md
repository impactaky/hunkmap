# Review an immutable snapshot with exact coverage

hunkmap captures the Live Diff as a Snapshot and considers its Review Map valid
only while every Review Unit occurs exactly once and the Live Diff still agrees
with that Snapshot. We chose this fail-closed boundary over reviewing a moving
diff because annotations and review progress must never silently attach to
changed content or omit an unrepresented change.

## Consequences

- Refresh preserves annotations and Review State only for unchanged Unit
  Identities.
- A changed file without text hunks still becomes a file-level Review Unit.
- Ordinary edits can make a review stale and pause progress; this interruption
  is deliberate evidence that the reviewed subject changed.
