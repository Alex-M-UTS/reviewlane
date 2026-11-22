"""Shared data models. Plain dataclasses keep the package dependency-light."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ChangeKind(str, Enum):
    FORMATTING = "formatting"
    RENAME = "rename"
    GENERATED = "generated"
    DOCS = "docs"
    TEST = "test"
    CONFIG = "config"
    BEHAVIORAL = "behavioral"


# Kinds a reviewer can safely skim: verified (or near-certain) not to change behavior.
SKIMMABLE = frozenset(
    {ChangeKind.FORMATTING, ChangeKind.RENAME, ChangeKind.GENERATED, ChangeKind.DOCS}
)


@dataclass
class FileChange:
    path: str
    status: str = "modified"  # added | removed | modified | renamed
    additions: int = 0
    deletions: int = 0
    patch: str = ""
    old_source: str | None = None
    new_source: str | None = None
    kind: ChangeKind = ChangeKind.BEHAVIORAL
    reason: str = ""
    symbols: list[str] = field(default_factory=list)

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass
class PullRequest:
    title: str = ""
    body: str = ""
    author: str = ""
    number: int = 0
    base_sha: str = ""
    head_sha: str = ""
    files: list[FileChange] = field(default_factory=list)
    # History signals. Must only use information available when the PR was opened.
    author_prior_prs: int = 0
    file_prior_fixes: dict[str, int] = field(default_factory=dict)

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass
class RiskResult:
    score: int
    level: str  # low | medium | high
    reasons: list[tuple[str, int]] = field(default_factory=list)  # (text, points)
    features: dict[str, float] = field(default_factory=dict)
    model_score: float | None = None  # optional learned probability


LEVEL_TO_LANE = {
    "low": "Fast lane: one reviewer, skim is fine",
    "medium": "Standard lane: one careful reviewer",
    "high": "Deep lane: senior reviewer plus a second approval",
}
