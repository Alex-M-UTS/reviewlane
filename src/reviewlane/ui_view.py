"""Small helpers for the demo page: plain-language labels and line counts."""
from __future__ import annotations

from .models import SKIMMABLE, ChangeKind, PullRequest

LEVEL_TITLE = {"low": "Low risk", "medium": "Medium risk", "high": "High risk"}
LEVEL_ADVICE = {
    "low": "Fast lane. One reviewer can skim this.",
    "medium": "Standard lane. One careful reviewer.",
    "high": "Deep lane. Send to a senior reviewer and ask for a second approval.",
}
KIND_LABEL = {
    ChangeKind.FORMATTING: "formatting only",
    ChangeKind.RENAME: "rename only",
    ChangeKind.GENERATED: "generated file",
    ChangeKind.DOCS: "documentation",
    ChangeKind.TEST: "tests",
    ChangeKind.CONFIG: "configuration",
    ChangeKind.BEHAVIORAL: "changes behavior",
}


def counts(pr: PullRequest) -> dict[str, int]:
    """Changed lines split into: needs judgment, tests/config, safe to skim."""
    judge = sum(f.changed_lines for f in pr.files if f.kind is ChangeKind.BEHAVIORAL)
    tc = sum(f.changed_lines for f in pr.files if f.kind in (ChangeKind.TEST, ChangeKind.CONFIG))
    skim = sum(f.changed_lines for f in pr.files if f.kind in SKIMMABLE)
    return {
        "judge": judge, "tc": tc, "skim": skim, "total": judge + tc + skim,
        "files": len(pr.files),
        "judge_files": sum(1 for f in pr.files if f.kind is ChangeKind.BEHAVIORAL),
    }
