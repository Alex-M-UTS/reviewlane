"""SZZ-lite labeling and dataset builder, working directly on a local git clone.

A merged change (first-parent commit: a squash commit or a merge commit) is labeled
"buggy" if, within `window_days`, a later bug-fix commit deleted or rewrote lines that
it introduced (found via `git blame` on the fix's parent), or if a later commit
explicitly reverted it. This is a simplified take on the SZZ algorithm from the
defect-prediction literature. Labels are noisy by construction; see the README.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from unidiff import PatchSet

from .classifier import classify_pr
from .diffparse import parse_diff
from .models import PullRequest
from .risk import extract_features

FIX_RE = re.compile(r"\b(fix(es|ed)?|bug(fix)?|hotfix|regression|crash|revert)\b", re.IGNORECASE)
REVERT_BODY_RE = re.compile(r"This reverts commit ([0-9a-f]{40})")
BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")
MAX_PY_SOURCE_BYTES = 200_000


def is_fix_message(subject: str) -> bool:
    return bool(FIX_RE.search(subject))


def git(repo: str | Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace"
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout if proc.returncode == 0 else ""


def removed_ranges(diff_text: str) -> list[tuple[str, int, int]]:
    """(path, start, end) of lines removed or rewritten in the *old* version, from a -U0 diff."""
    out: list[tuple[str, int, int]] = []
    for pf in PatchSet(diff_text):
        if pf.is_added_file or getattr(pf, "is_binary_file", False):
            continue
        path = pf.source_file[2:] if pf.source_file.startswith("a/") else pf.source_file
        for hunk in pf:
            if hunk.source_length > 0:
                out.append((path, hunk.source_start, hunk.source_start + hunk.source_length - 1))
    return out


def blame_range(repo: str | Path, rev: str, path: str, start: int, end: int) -> set[str]:
    text = git(repo, "blame", "--porcelain", "-w", "-L", f"{start},{end}", rev, "--", path, check=False)
    return {m.group(1) for line in text.splitlines() if (m := BLAME_HEADER_RE.match(line))}


@dataclass
class CommitInfo:
    sha: str
    email: str
    ts: int
    subject: str


def list_first_parent(repo: str | Path, max_commits: int, since: str | None = None) -> list[CommitInfo]:
    args = ["log", "--first-parent", "--format=%H%x1f%ae%x1f%at%x1f%s", f"-n{max_commits}"]
    if since:
        args.append(f"--since={since}")
    commits = []
    for line in git(repo, *args).splitlines():
        sha, email, ts, subject = line.split("\x1f", 3)
        commits.append(CommitInfo(sha, email, int(ts), subject))
    commits.reverse()  # oldest first
    return commits


def label_commits(
    repo: str | Path, commits: list[CommitInfo], window_days: int = 90, max_ranges: int = 50
) -> set[str]:
    """Return the set of commit SHAs (from `commits`) considered buggy."""
    by_sha = {c.sha: c for c in commits}
    pr_of: dict[str, str] = {}
    for c in commits:
        for s in git(repo, "rev-list", f"{c.sha}^1..{c.sha}", check=False).split():
            pr_of[s] = c.sha
    window = window_days * 86400
    buggy: set[str] = set()

    for fix in commits:
        if not is_fix_message(fix.subject):
            continue
        # Explicit reverts are the strongest signal.
        if fix.subject.lower().startswith("revert"):
            body = git(repo, "show", "-s", "--format=%B", fix.sha, check=False)
            for target in REVERT_BODY_RE.findall(body):
                owner = pr_of.get(target)
                if owner and owner != fix.sha and fix.ts - by_sha[owner].ts <= window:
                    buggy.add(owner)
        diff = git(repo, "diff", "-U0", "--no-color", f"{fix.sha}^1", fix.sha, check=False)
        if not diff:
            continue
        try:
            ranges = [r for r in removed_ranges(diff) if r[0].endswith(".py")][:max_ranges]
        except Exception:
            continue
        for path, start, end in ranges:
            for sha in blame_range(repo, f"{fix.sha}^1", path, start, end):
                owner = pr_of.get(sha)
                if owner and owner != fix.sha and 0 <= fix.ts - by_sha[owner].ts <= window:
                    buggy.add(owner)
    return buggy


def _read_at(repo: str | Path, rev: str, path: str) -> str | None:
    text = git(repo, "show", f"{rev}:{path}", check=False)
    return text if text and len(text) <= MAX_PY_SOURCE_BYTES else None


def build_pr_between(
    repo: str | Path, base: str, head: str, title: str = "", author: str = "",
    max_files: int = 400, max_sources: int = 40,
) -> PullRequest | None:
    """Build and classify a PullRequest from two git revisions of a local clone."""
    diff = git(repo, "diff", "--no-color", "-M", base, head, check=False)
    if not diff:
        return None
    try:
        files = parse_diff(diff)
    except Exception:
        return None
    if not files or len(files) > max_files:
        return None
    fetched = 0
    for f in files:
        if f.path.endswith(".py") and fetched < max_sources:
            if f.status != "added":
                f.old_source = _read_at(repo, base, f.path)
            if f.status != "removed":
                f.new_source = _read_at(repo, head, f.path)
            fetched += 1
    pr = PullRequest(title=title, author=author, head_sha=head, base_sha=base, files=files)
    return classify_pr(pr)


def build_pr(repo: str | Path, c: CommitInfo) -> PullRequest | None:
    return build_pr_between(repo, f"{c.sha}^1", c.sha, title=c.subject, author=c.email)


def build_dataset(
    repo: str | Path,
    max_commits: int = 3000,
    window_days: int = 90,
    since: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    commits = [c for c in list_first_parent(repo, max_commits, since)]
    commits = [c for c in commits if git(repo, "rev-parse", "--verify", "-q", f"{c.sha}^1", check=False).strip()]
    if not commits:
        return []
    buggy = label_commits(repo, commits, window_days)
    latest = max(c.ts for c in commits)
    cutoff = latest - window_days * 86400  # newer PRs haven't had time to show bugs (right-censoring)
    author_counts: Counter[str] = Counter()
    file_fixes: Counter[str] = Counter()
    rows: list[dict] = []
    for i, c in enumerate(commits):
        if progress:
            progress(i + 1, len(commits))
        pr = build_pr(repo, c)
        if pr is not None:
            # History features use only what was known before this change landed.
            pr.author_prior_prs = author_counts[c.email]
            pr.file_prior_fixes = {f.path: file_fixes[f.path] for f in pr.files}
            if c.ts <= cutoff:
                rows.append({
                    "sha": c.sha, "ts": c.ts, "subject": c.subject,
                    "label": int(c.sha in buggy),
                    "features": extract_features(pr),
                })
            if is_fix_message(c.subject):
                for f in pr.files:
                    file_fixes[f.path] += 1
        author_counts[c.email] += 1
    return rows


def write_jsonl(rows: list[dict], path: str | Path) -> None:
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
