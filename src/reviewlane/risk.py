"""Risk scoring: transparent heuristic baseline, optionally replaced by a learned model."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .models import SKIMMABLE, ChangeKind, PullRequest, RiskResult

if TYPE_CHECKING:  # pragma: no cover
    from .model import RiskModel

SENSITIVE_RE = re.compile(
    r"(auth|login|logout|password|passwd|token|secret|credential|crypto|payment|billing|"
    r"checkout|invoice|security|permission|oauth|session|acl|sandbox)",
    re.IGNORECASE,
)
MIGRATION_RE = re.compile(r"(migrations?/|alembic|\.sql$|schema)", re.IGNORECASE)
DEPENDENCY_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "poetry.lock", "uv.lock",
    "package.json", "package-lock.json", "yarn.lock", "go.mod", "cargo.toml",
}

FEATURE_NAMES = [
    "behavioral_lines", "total_lines", "files_changed", "skim_fraction", "test_lines",
    "test_ratio", "sensitive_files", "migration_files", "dependency_files", "ci_files",
    "new_files", "untested_change", "author_prior_prs", "file_prior_fixes_max",
    "file_prior_fixes_sum",
]


def extract_features(pr: PullRequest) -> dict[str, float]:
    """Features available at PR-open time (no label leakage)."""
    behavioral = [f for f in pr.files if f.kind is ChangeKind.BEHAVIORAL]
    behavioral_lines = sum(f.changed_lines for f in behavioral)
    test_lines = sum(f.changed_lines for f in pr.files if f.kind is ChangeKind.TEST)
    skim_lines = sum(f.changed_lines for f in pr.files if f.kind in SKIMMABLE)
    total = pr.changed_lines
    names = [f.path.rsplit("/", 1)[-1].lower() for f in pr.files]
    fixes = [pr.file_prior_fixes.get(f.path, 0) for f in behavioral] or [0]
    return {
        "behavioral_lines": float(behavioral_lines),
        "total_lines": float(total),
        "files_changed": float(len(pr.files)),
        "skim_fraction": (skim_lines / total) if total else 0.0,
        "test_lines": float(test_lines),
        "test_ratio": min(test_lines / (behavioral_lines + 1), 5.0),
        "sensitive_files": float(sum(1 for f in behavioral if SENSITIVE_RE.search(f.path))),
        "migration_files": float(sum(1 for f in pr.files if MIGRATION_RE.search(f.path))),
        "dependency_files": float(sum(1 for n in names if n in DEPENDENCY_NAMES or n.startswith("requirements"))),
        "ci_files": float(sum(1 for f in pr.files if ".github/" in f.path or f.path.startswith(".circleci"))),
        "new_files": float(sum(1 for f in pr.files if f.status == "added")),
        "untested_change": 1.0 if behavioral_lines >= 20 and test_lines == 0 else 0.0,
        "author_prior_prs": float(pr.author_prior_prs),
        "file_prior_fixes_max": float(max(fixes)),
        "file_prior_fixes_sum": float(sum(fixes)),
    }


def level_for(score: float) -> str:
    return "low" if score < 25 else "medium" if score < 50 else "high"


def heuristic_score(f: dict[str, float]) -> tuple[int, list[tuple[str, int]]]:
    """Weighted rules. Every point added has a human-readable reason."""
    reasons: list[tuple[str, int]] = []

    def add(text: str, pts: int) -> None:
        if pts:
            reasons.append((text, pts))

    b = f["behavioral_lines"]
    add(f"{int(b)} lines of behavioral change",
        0 if b <= 50 else 10 if b <= 200 else 20 if b <= 500 else 30)
    add(f"{int(f['files_changed'])} files touched",
        0 if f["files_changed"] <= 10 else 5 if f["files_changed"] <= 25 else 10)
    if f["sensitive_files"]:
        add("Touches security/payment/auth-sensitive paths",
            min(25, 15 + 5 * int(f["sensitive_files"] - 1)))
    add("Database migration or schema change", 15 if f["migration_files"] else 0)
    add("Dependency manifest changed", 10 if f["dependency_files"] else 0)
    add("CI/workflow configuration changed", 10 if f["ci_files"] else 0)
    add(f"{int(b)} changed lines with no accompanying tests", 15 if f["untested_change"] else 0)
    if f["author_prior_prs"] == 0:
        add("First PR from this author in the repo", 10)
    elif f["author_prior_prs"] < 5:
        add("Author has few prior PRs in the repo", 5)
    add(f"Touched files have {int(f['file_prior_fixes_max'])} prior bug-fix commits",
        min(15, 3 * int(f["file_prior_fixes_max"])))

    score = min(100, sum(p for _, p in reasons))
    if b == 0:  # nothing but verified-skimmable/test/docs/config noise
        score = min(score, 5 + (10 if f["ci_files"] else 0) + (5 if f["dependency_files"] else 0))
    return score, sorted(reasons, key=lambda r: -r[1])


def score_pr(pr: PullRequest, model: "RiskModel | None" = None) -> RiskResult:
    feats = extract_features(pr)
    score, reasons = heuristic_score(feats)
    result = RiskResult(score=score, level=level_for(score), reasons=reasons, features=feats)
    if model is not None:
        p = model.predict_proba(feats)
        pct = model.percentile(p)
        result.model_score = p
        result.score = round(pct)
        result.level = model.level_for_percentile(pct)
    return result
