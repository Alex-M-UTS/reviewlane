"""Command line entrypoint: analyze PRs, run as a GitHub Action, build datasets, train models."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .classifier import classify_pr
from .diffparse import parse_diff
from .models import PullRequest
from .render import render_comment
from .risk import score_pr
from .summarizer import summarize


def _load_model(path: str | None):
    if not path:
        return None
    from .model import RiskModel
    return RiskModel.load(path)


def _analyze(pr: PullRequest, model_path: str | None, use_llm: bool) -> tuple[str, dict]:
    risk = score_pr(classify_pr(pr), _load_model(model_path))
    if use_llm:
        summary, source = summarize(pr)
    else:
        from .summarizer import offline_summary
        summary, source = offline_summary(pr), "offline"
    comment = render_comment(pr, risk, summary, source)
    return comment, {
        "score": risk.score, "level": risk.level,
        "reasons": [{"text": t, "points": p} for t, p in risk.reasons],
        "features": risk.features,
    }


def cmd_analyze_diff(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.diff == "-" else Path(args.diff).read_text()
    pr = PullRequest(title=args.title or "", files=parse_diff(text))
    comment, data = _analyze(pr, args.model_path, not args.no_llm)
    print(json.dumps(data, indent=2) if args.json else comment)
    return 0


def cmd_analyze_local(args: argparse.Namespace) -> int:
    from .szz import build_pr_between
    pr = build_pr_between(args.repo_path, args.base, args.head, title=args.title or "")
    if pr is None:
        print("No changes between the given revisions.", file=sys.stderr)
        return 1
    comment, data = _analyze(pr, args.model_path, not args.no_llm)
    print(json.dumps(data, indent=2) if args.json else comment)
    return 0


def _run_github(repo: str, number: int, post: bool, label: bool, model_path: str | None, use_llm: bool) -> int:
    from .github import GitHubClient, build_pull_request
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("Set GITHUB_TOKEN (or GH_TOKEN).", file=sys.stderr)
        return 2
    gh = GitHubClient(token)
    pr = build_pull_request(gh, repo, number)
    risk = score_pr(pr, _load_model(model_path))
    if use_llm:
        summary, source = summarize(pr)
    else:
        from .summarizer import offline_summary
        summary, source = offline_summary(pr), "offline"
    comment = render_comment(pr, risk, summary, source)
    if post:
        gh.upsert_comment(repo, number, comment)
        if label:
            gh.set_risk_label(repo, number, risk.level)
    else:
        print(comment)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    return _run_github(args.repo, args.pr, args.post, not args.no_label, args.model_path, not args.no_llm)


def cmd_action(args: argparse.Namespace) -> int:
    """Entrypoint used by the GitHub Action: read the pull_request event and comment."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not event_path or not repo:
        print("Not running inside GitHub Actions.", file=sys.stderr)
        return 2
    event = json.loads(Path(event_path).read_text())
    number = (event.get("pull_request") or {}).get("number")
    if not number:
        print("Event has no pull_request; skipping.")
        return 0
    label = os.environ.get("REVIEWLANE_LABEL", "true").lower() != "false"
    model_path = os.environ.get("REVIEWLANE_MODEL_PATH") or None
    return _run_github(repo, int(number), True, label, model_path, True)


def cmd_build_dataset(args: argparse.Namespace) -> int:
    from .szz import build_dataset, write_jsonl

    def progress(i: int, n: int) -> None:
        if i % 100 == 0 or i == n:
            print(f"  processed {i}/{n}", file=sys.stderr)

    rows = build_dataset(args.repo_path, args.max_commits, args.window_days, args.since, progress)
    write_jsonl(rows, args.out)
    pos = sum(r["label"] for r in rows)
    print(f"Wrote {len(rows)} rows to {args.out} ({pos} buggy, {pos / max(len(rows), 1):.1%})")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    from .model import evaluate, format_report, load_rows, train

    rows = []
    for path in args.data:
        rows += load_rows(path)
    report = evaluate(rows, args.train_frac, args.top_frac)
    print(format_report(report))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2))
    if args.save:
        train(rows, args.kind).save(args.save)
        print(f"Saved {args.kind} model trained on all {len(rows)} rows to {args.save}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Launch the web demo."""
    import importlib.util
    import subprocess

    app = Path(__file__).resolve().parents[2] / "streamlit_app.py"
    if not app.exists():
        print("The demo app ships with the source checkout. Run this from a clone of the repo.", file=sys.stderr)
        return 2
    if importlib.util.find_spec("streamlit") is None:
        print('Streamlit is not installed. Run: pip install -e ".[ui]"', file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reviewlane", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--model-path", help="Trained model (.pkl) to use instead of the heuristic")
        sp.add_argument("--no-llm", action="store_true", help="Skip the LLM summary")
        sp.add_argument("--json", action="store_true", help="Print machine-readable risk output")

    sp = sub.add_parser("analyze-diff", help="Analyze a unified diff file (or - for stdin)")
    sp.add_argument("diff"); sp.add_argument("--title"); common(sp)
    sp.set_defaults(func=cmd_analyze_diff)

    sp = sub.add_parser("analyze-local", help="Analyze two revisions of a local git clone")
    sp.add_argument("--repo-path", default="."); sp.add_argument("--base", required=True)
    sp.add_argument("--head", default="HEAD"); sp.add_argument("--title"); common(sp)
    sp.set_defaults(func=cmd_analyze_local)

    sp = sub.add_parser("analyze", help="Analyze a GitHub PR (needs GITHUB_TOKEN)")
    sp.add_argument("--repo", required=True, help="owner/name"); sp.add_argument("--pr", type=int, required=True)
    sp.add_argument("--post", action="store_true", help="Post/update the comment instead of printing")
    sp.add_argument("--no-label", action="store_true"); common(sp)
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("action", help="GitHub Action entrypoint")
    sp.set_defaults(func=cmd_action)

    sp = sub.add_parser("build-dataset", help="Label history of a local clone with SZZ-lite")
    sp.add_argument("--repo-path", required=True); sp.add_argument("--out", required=True)
    sp.add_argument("--max-commits", type=int, default=3000)
    sp.add_argument("--window-days", type=int, default=90); sp.add_argument("--since")
    sp.set_defaults(func=cmd_build_dataset)

    sp = sub.add_parser("train", help="Evaluate baselines vs models on a time split, optionally save one")
    sp.add_argument("--data", nargs="+", required=True, help="One or more dataset .jsonl files")
    sp.add_argument("--train-frac", type=float, default=0.7); sp.add_argument("--top-frac", type=float, default=0.1)
    sp.add_argument("--kind", choices=["gbm", "logreg"], default="gbm")
    sp.add_argument("--save"); sp.add_argument("--report")
    sp.set_defaults(func=cmd_train)

    sp = sub.add_parser("ui", help="Open the web demo in your browser")
    sp.add_argument("--port", type=int, default=8501)
    sp.set_defaults(func=cmd_ui)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
