"""Parse unified diffs into FileChange objects."""
from __future__ import annotations

from unidiff import PatchSet

from .models import FileChange


def parse_diff(diff_text: str) -> list[FileChange]:
    """Parse a multi-file unified diff (e.g. `git diff` or a PR .diff)."""
    changes: list[FileChange] = []
    for pf in PatchSet(diff_text):
        if getattr(pf, "is_binary_file", False):
            continue
        if pf.is_added_file:
            status = "added"
        elif pf.is_removed_file:
            status = "removed"
        elif pf.is_rename:
            status = "renamed"
        else:
            status = "modified"
        path = pf.path
        changes.append(
            FileChange(
                path=path,
                status=status,
                additions=pf.added,
                deletions=pf.removed,
                patch=str(pf),
            )
        )
    return changes


def changed_line_texts(patch: str) -> tuple[list[str], list[str]]:
    """Return (added_lines, removed_lines) text from a single-file patch.

    Header lines before the first hunk are skipped, so a removed line that itself
    starts with "--" (e.g. a SQL comment) is not mistaken for a file header.
    """
    added: list[str] = []
    removed: list[str] = []
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed
