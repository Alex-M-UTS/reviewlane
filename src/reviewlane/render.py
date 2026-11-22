"""Render the PR comment."""
from __future__ import annotations

from collections import defaultdict

from .classifier import skim_stats
from .models import LEVEL_TO_LANE, SKIMMABLE, ChangeKind, PullRequest, RiskResult

MARKER = "<!-- reviewlane -->"
_ICON = {"low": "🟢", "medium": "🟡", "high": "🔴"}


def render_comment(pr: PullRequest, risk: RiskResult, summary: str, summary_source: str = "offline") -> str:
    skim, total = skim_stats(pr.files)
    pct = round(100 * skim / total) if total else 0
    out = [
        MARKER,
        f"## {_ICON[risk.level]} ReviewLane: {risk.level.upper()} risk ({risk.score}/100)",
        f"**Suggested lane:** {LEVEL_TO_LANE[risk.level]}",
        "",
    ]
    if risk.reasons:
        out.append("**Why this score**")
        out += [f"- {text} (+{pts})" for text, pts in risk.reasons[:3]]
        out.append("")

    out += ["### Review focus", summary, ""]

    needs = [f for f in pr.files if f.kind is ChangeKind.BEHAVIORAL]
    if needs:
        noun = "file" if len(needs) == 1 else "files"
        out.append(f"### Needs human judgment ({len(needs)} {noun})")
        for f in sorted(needs, key=lambda x: -x.changed_lines)[:10]:
            sym = f" — {', '.join(f.symbols[:3])}" if f.symbols else ""
            out.append(f"- `{f.path}` (+{f.additions}/-{f.deletions}){sym}")
        if len(needs) > 10:
            out.append(f"- …and {len(needs) - 10} more")
        out.append("")

    skimmable = [f for f in pr.files if f.kind in SKIMMABLE]
    other = [f for f in pr.files if f.kind in (ChangeKind.TEST, ChangeKind.CONFIG)]
    if skimmable or other:
        out.append(f"### Safe to skim: {skim} of {total} changed lines ({pct}%)")
        groups: dict[str, list[str]] = defaultdict(list)
        for f in skimmable:
            groups[f"{f.kind.value}: {f.reason}"].append(f.path)
        out.append("<details><summary>Show files</summary>\n")
        for label, paths in groups.items():
            out.append(f"- **{label}**: " + ", ".join(f"`{p}`" for p in paths[:8]) + (" …" if len(paths) > 8 else ""))
        if other:
            out.append("- **tests/config** (not skimmable, but lower-risk): " + ", ".join(f"`{f.path}`" for f in other[:8]))
        out.append("\n</details>\n")

    src = "LLM summary" if summary_source == "llm" else "offline summary"
    out.append(f"<sub>ReviewLane · {src} · risk is a triage aid, not a verdict. Skim claims for Python are AST-verified.</sub>")
    return "\n".join(out)
