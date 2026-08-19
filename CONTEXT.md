# Semantic Diff Review

hunkmap turns a changing Git diff into a complete, purpose-oriented review that
can be checked for omissions and resumed without trusting file order.

## Review structure

**Review Goal**:
The single outcome the complete change is intended to achieve.
_Avoid_: Summary, title

**Review Map**:
The complete organization of Review Units into Chapters, together with the
reasoning needed to review them.
_Avoid_: File map, diff outline

**Chapter**:
A purpose-oriented collection of Review Units within a Review Map. Chapters
describe why changes belong together, independently of their files or order.
_Avoid_: Group, file section

**Review Unit**:
The smallest change that can be selected, explained, and marked reviewed on its
own. A text hunk is one Review Unit; a changed file without text hunks is one
file-level Review Unit.
_Avoid_: Item, entry, hunk

**Annotation**:
The semantic explanation attached to a Review Unit: its short label, why it is
needed, how risk is bounded, and what evidence supports it.
_Avoid_: Comment, note

## Integrity

**Live Diff**:
The repository's current tracked and untracked changes relative to the chosen
base.
_Avoid_: Current patch

**Snapshot**:
The fixed copy of a Live Diff against which a Review Map and its progress are
interpreted.
_Avoid_: Cache, saved diff

**Unit Identity**:
A content-derived identity for one Review Unit. Editing the unit changes its
identity; moving through review without changing it does not.
_Avoid_: Index, row number

**Exact Coverage**:
The condition in which every Review Unit in the Snapshot occurs exactly once in
the Review Map, its recorded identity agrees with its content, and the Live Diff
still agrees with the Snapshot.
_Avoid_: Coverage percentage, file coverage

**Stale Review**:
A review whose Live Diff no longer agrees with its Snapshot. Its existing map
may be inspected, but review progress must not advance until it is refreshed.
_Avoid_: Dirty review, invalid review

## Review progress

**Review State**:
The mutable record of which Unit Identities have been reviewed. It survives a
refresh only for Review Units whose identities remain unchanged.
_Avoid_: Completion cache, checklist file

**Selected Unit**:
The one Review Unit currently in focus and shown for detailed inspection.
_Avoid_: Current file, current Chapter

**Review Rail**:
The interactive presentation of a Review Map that exposes Chapters, the
Selected Unit, progress, and integrity status while keeping detailed diff
inspection scoped to that unit.
_Avoid_: Sidebar, file tree
