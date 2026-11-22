"""ReviewLane demo, one page.  Run locally:  streamlit run streamlit_app.py"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))  # lets the app run without `pip install` (e.g. Streamlit Community Cloud)

from reviewlane import ui_view as v  # noqa: E402
from reviewlane.classifier import classify_pr  # noqa: E402
from reviewlane.diffparse import parse_diff  # noqa: E402
from reviewlane.examples import EXAMPLES, EXAMPLES_BY_KEY  # noqa: E402
from reviewlane.models import ChangeKind, PullRequest  # noqa: E402
from reviewlane.risk import score_pr  # noqa: E402
from reviewlane.summarizer import llm_backend, offline_summary, summarize  # noqa: E402

PR_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
MAX_LLM_CALLS_PER_SESSION = 5
DIFF_CHOICE, GITHUB_CHOICE = "paste-diff", "github-link"

st.set_page_config(page_title="ReviewLane", page_icon=":material/fact_check:", layout="centered")
st.markdown(
    "<style>#MainMenu, footer, [data-testid='stToolbar'] {display: none !important;}"
    ".block-container {max-width: 760px;}</style>",
    unsafe_allow_html=True,
)


class UserError(Exception):
    """A problem the visitor can fix; the message is shown as-is."""


# ------------------------------------------------------------------ AI notes

def ai_backend() -> str | None:
    """'ollama' (free, local), 'anthropic' (API key) or None. Also reads a key from Streamlit secrets."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        try:
            key = st.secrets.get("ANTHROPIC_API_KEY")
        except Exception:
            key = None
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
    return llm_backend()


@st.cache_data(show_spinner="Writing review notes. A local model can take up to a minute the first time.")
def example_summary(key: str) -> tuple[str, str]:
    return summarize(EXAMPLES_BY_KEY[key].build())


def custom_summary(pr: PullRequest) -> tuple[str, str]:
    backend = ai_backend()
    if backend == "ollama":  # free and local, so no cap
        with st.spinner("Writing review notes with the local model…"):
            return summarize(pr)
    used = st.session_state.get("llm_calls", 0)
    if backend == "anthropic" and used < MAX_LLM_CALLS_PER_SESSION:  # paid, so cap each visitor
        st.session_state["llm_calls"] = used + 1
        return summarize(pr)
    return offline_summary(pr), "offline"


NOTE_CAPTION = {
    "llm": "Written by an AI model that only saw the lines that change behavior.",
    "ollama": "Written by a model running on this computer (Ollama) that only saw the lines that change "
              "behavior. Small local models can get details wrong, so check the diff.",
    "offline": "Plain summary. AI-written notes appear here when Ollama is running locally or the app has an API key.",
}


# ------------------------------------------------------------------ result

def _display_patch(patch: str, max_lines: int = 150) -> str:
    """The diff body without the file-header lines."""
    body = patch.splitlines()
    while body and body[0].startswith(("---", "+++", "diff ", "index ")):
        body = body[1:]
    return "\n".join(body[:max_lines]) or "(no diff available)"


def render_result(pr: PullRequest, summary: tuple[str, str]) -> None:
    risk = score_pr(pr)
    c = v.counts(pr)

    banner = {"low": st.success, "medium": st.warning, "high": st.error}[risk.level]
    banner(f"**{v.LEVEL_TITLE[risk.level]}** ({risk.score}/100). {v.LEVEL_ADVICE[risk.level]}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Lines to read closely", f"{c['judge']} of {c['total']}")
    col2.metric("Files that change behavior", f"{c['judge_files']} of {c['files']}")
    col3.metric("Safe to skim", f"{round(100 * c['skim'] / c['total']) if c['total'] else 0}%")

    st.subheader("Why this score")
    if risk.reasons:
        st.markdown("\n".join(f"- {text} (+{pts})" for text, pts in risk.reasons))
    else:
        st.write("No risk signals found.")

    notes, source = summary
    st.subheader("Review notes")
    st.markdown(notes)
    st.caption(NOTE_CAPTION.get(source, NOTE_CAPTION["offline"]))

    needs = sorted((f for f in pr.files if f.kind is ChangeKind.BEHAVIORAL), key=lambda f: -f.changed_lines)
    if needs:
        st.subheader("Look at these")
        for i, f in enumerate(needs):
            with st.expander(f"{f.path}  (+{f.additions} −{f.deletions})", expanded=(i == 0)):
                if f.symbols:
                    st.caption("Changed: " + ", ".join(f.symbols[:6]))
                st.code(_display_patch(f.patch), language="diff")

    other = [f for f in pr.files if f.kind in (ChangeKind.TEST, ChangeKind.CONFIG)]
    if other:
        st.subheader("Tests and config")
        st.markdown("\n".join(f"- `{f.path}` ({v.KIND_LABEL[f.kind]})" for f in other))

    skim = [f for f in pr.files if f.kind not in (ChangeKind.BEHAVIORAL, ChangeKind.TEST, ChangeKind.CONFIG)]
    st.subheader("Safe to skip")
    if skim:
        st.markdown("\n".join(f"- `{f.path}`: {f.reason}" for f in skim))
    else:
        st.write("Nothing in this change could be safely skipped.")


# ------------------------------------------------------------------ custom inputs

def load_github(url: str, token: str) -> PullRequest:
    import httpx
    from reviewlane.github import GitHubClient, build_pull_request

    m = PR_URL.search(url or "")
    if not m:
        raise UserError("That doesn't look like a pull request link. It should look like "
                        "https://github.com/owner/repo/pull/123")
    owner, repo, number = m.group(1), m.group(2), int(m.group(3))
    try:
        gh = GitHubClient(token.strip())
        return build_pull_request(gh, f"{owner}/{repo}", number, max_sources=30 if token.strip() else 8)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code == 404:
            raise UserError("GitHub couldn't find that pull request. If the repository is private, "
                            "add a GitHub token.")
        if code in (401, 403, 429):
            raise UserError("GitHub refused the request, usually because anonymous visitors are "
                            "rate-limited. Add a GitHub token, or try again in a few minutes.")
        raise UserError(f"GitHub returned an error ({code}). Try again in a moment.")
    except httpx.HTTPError:
        raise UserError("Couldn't reach GitHub. Check the connection and try again.")


def load_diff(text: str) -> PullRequest:
    try:
        files = parse_diff(text or "")
    except Exception:
        files = []
    if not files:
        raise UserError("Couldn't find any changes in that text. Paste the full output of `git diff`, or add "
                        ".diff to the end of a GitHub pull request link and copy what the page shows.")
    return classify_pr(PullRequest(title="Pasted diff", files=files))


def show_custom(mode: str) -> None:
    held = st.session_state.get("custom")
    if held and held[0] == mode:
        render_result(held[1], custom_summary(held[1]))


# ------------------------------------------------------------------ page

st.title("ReviewLane")
st.markdown(
    "**The problem.** AI tools now write code faster than people can review it. Reviewers get long pull requests, "
    "skim them, and approve. Bugs slip through, or the review queue backs up.\n\n"
    "**The solution.** ReviewLane looks at each pull request and sorts every changed line into *safe to skim* "
    "(formatting, renames, docs, generated files) or *needs a human*. For Python it proves this by comparing the "
    "code before and after, so it isn't guessing. It scores the risk with plain rules that explain themselves, "
    "and writes short notes about only the part that matters."
)

st.header("Try it")
labels = {e.key: e.title for e in EXAMPLES}
labels[DIFF_CHOICE] = "Paste my own diff"
labels[GITHUB_CHOICE] = "Load a GitHub pull request by link"
choice = st.selectbox("Choose a pull request", list(labels), format_func=labels.get)

if choice in EXAMPLES_BY_KEY:
    st.caption(EXAMPLES_BY_KEY[choice].blurb)
    pr = EXAMPLES_BY_KEY[choice].build()
    render_result(pr, example_summary(choice) if ai_backend() else (offline_summary(pr), "offline"))

elif choice == GITHUB_CHOICE:
    url = st.text_input("Pull request link", placeholder="https://github.com/pallets/flask/pull/5000")
    token = st.text_input("GitHub token (optional, for private repositories or rate limits)", type="password",
                          help="Used for this request only. It is never stored.")
    if st.button("Analyze", type="primary"):
        try:
            with st.spinner("Fetching the pull request from GitHub…"):
                st.session_state["custom"] = (choice, load_github(url, token))
        except UserError as e:
            st.session_state.pop("custom", None)
            st.error(str(e))
    show_custom(choice)

else:
    text = st.text_area("Diff", height=200, placeholder="diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ ...")
    st.caption("Tip: add .diff to the end of any GitHub pull request link and copy the page. A pasted diff has "
               "no full files, so Python formatting and rename changes can't be proven the way they are for "
               "the examples.")
    if st.button("Analyze", type="primary"):
        try:
            st.session_state["custom"] = (choice, load_diff(text))
        except UserError as e:
            st.session_state.pop("custom", None)
            st.error(str(e))
    show_custom(choice)

st.header("How well does it work?")
report = json.loads((ROOT / "results" / "pooled_report.json").read_text())
skim_stats = json.loads((ROOT / "results" / "skim_stats.json").read_text())
positives = round(report["test_base_rate"] * report["n_test"])
ours = round(report["methods"]["heuristic"]["recall"] * positives)
size = round(report["methods"]["size_only"]["recall"] * positives)
st.markdown(
    f"I replayed the history of four open-source Python projects (Flask, Requests, HTTPX, Rich): "
    f"{skim_stats['changes']:,} merged changes. A change counted as buggy if a later fix rewrote lines it "
    f"introduced. On the newest {report['n_test']:,} changes, flagging the top 10% by ReviewLane's score caught "
    f"{ours} of {positives} buggy ones. Sorting by size alone caught {size}. That gap is too small to mean anything, "
    f"so the honest summary is that **the score is about as good as size**. What ReviewLane adds is a reason for "
    f"each score and the skip list.\n\n"
    f"It also found that {skim_stats['safe_to_skim_pct']:.0f}% of changed lines were safe to skim, and "
    f"{skim_stats['prs_with_no_behavioral_pct']:.0f}% of changes did not alter how the code behaves.\n\n"
    f"**Not yet measured:** whether reviewers actually finish faster, and how often the AI notes are wrong. "
    f"Proofs only exist for Python, and the risk score is a triage aid, not a verdict."
)
