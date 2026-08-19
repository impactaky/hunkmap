"""Parse a Git patch into independently reviewable units."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import shlex


@dataclass(frozen=True)
class DiffUnit:
    id: str
    path: str
    ordinal: int
    kind: str
    header: str
    patch: bytes
    digest: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_from_section(lines: list[bytes]) -> str:
    first = lines[0].decode("utf-8", "surrogateescape").rstrip("\n")
    try:
        parts = shlex.split(first)
    except ValueError:
        parts = first.split()
    if len(parts) >= 4:
        candidate = parts[3]
        return candidate[2:] if candidate.startswith("b/") else candidate

    for prefix in (b"+++ ", b"--- "):
        for line in lines:
            if line.startswith(prefix):
                candidate = line[len(prefix):].split(b"\t", 1)[0].rstrip(b"\n")
                if candidate != b"/dev/null":
                    text = candidate.decode("utf-8", "surrogateescape")
                    return text[2:] if text.startswith(("a/", "b/")) else text
    return "unknown"


def _unit_id(path: str, ordinal: int, body: bytes) -> str:
    identity = path.encode("utf-8", "surrogateescape") + b"\0" + str(ordinal).encode() + b"\0" + body
    return "hm-" + sha256(identity)[:16]


def parse_patch(patch: bytes) -> list[DiffUnit]:
    """Return one unit per text hunk, or one unit for a hunk-less file diff."""
    lines = patch.splitlines(keepends=True)
    section_starts = [index for index, line in enumerate(lines) if line.startswith(b"diff --git ")]
    units: list[DiffUnit] = []
    for section_index, start in enumerate(section_starts):
        end = section_starts[section_index + 1] if section_index + 1 < len(section_starts) else len(lines)
        section = lines[start:end]
        path = _path_from_section(section)
        hunk_starts = [index for index, line in enumerate(section) if line.startswith(b"@@ ")]
        if not hunk_starts:
            body = b"".join(section)
            units.append(DiffUnit(
                id=_unit_id(path, 0, body),
                path=path,
                ordinal=0,
                kind="file",
                header="file-level change",
                patch=body,
                digest=sha256(body),
            ))
            continue

        file_header = section[:hunk_starts[0]]
        for ordinal, hunk_start in enumerate(hunk_starts):
            hunk_end = hunk_starts[ordinal + 1] if ordinal + 1 < len(hunk_starts) else len(section)
            body = b"".join(section[hunk_start:hunk_end])
            selected = b"".join(file_header) + body
            header = section[hunk_start].decode("utf-8", "replace").rstrip("\n")
            units.append(DiffUnit(
                id=_unit_id(path, ordinal, body),
                path=path,
                ordinal=ordinal,
                kind="hunk",
                header=header,
                patch=selected,
                digest=sha256(body),
            ))
    return units
