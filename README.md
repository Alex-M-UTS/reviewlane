# ReviewLane

AI tools now write code faster than people can review it. ReviewLane helps reviewers spend their attention where it matters. It scores each pull request's risk, separates what can be skimmed (formatting, renames, docs, generated files) from what needs a human, and writes short notes on only the part that matters.

PR → classify every file → score risk → summarize only the behavioral changes

## Web demo

ReviewLane ships with a small web app. Pick an example pull request, or paste a GitHub link or a diff, and see on one screen what needs a human and what does not.

```bash
pip install -e ".[ui]"
reviewlane ui          # opens http://localhost:8501
```

It is one page: a short description of the problem and the solution, a Try it section (four built-in examples that run through the real classifier and scorer, plus GitHub link and paste-a-diff modes), and a short, honest summary of how well it works.

### Free AI notes with Ollama (no API key)

The app and CLI use a model running on your own computer through [Ollama](https://ollama.com) when one is available. It is free and nothing leaves your machine.

1. Install Ollama from [ollama.com/download](https://ollama.com/download).
2. Download a model once: `ollama pull llama3.2` (about 2 GB, fine on 8 GB of RAM) or `ollama pull qwen2.5-coder:7b` (about 4.7 GB, better at code).
3. Start the app as usual with `reviewlane ui`. It finds Ollama on its own.

If Ollama is not running, ReviewLane uses the Anthropic API when `ANTHROPIC_API_KEY` is set, and otherwise shows a plain summary. Small local models make more mistakes than large hosted ones, so the notes are labelled and the diff is always shown next to them.

## The problem

AI tools made writing code cheap, but reviewing it costs the same human attention. Queues grow, reviewers rubber-stamp, and bugs slip through. ReviewLane attacks two parts of that:

- **Triage:** who should look at this PR, and how closely?
- **Reading cost:** how much of the diff actually needs a human?

## What it does

| Stage | How it works |
|---|---|
| **Classifier** | For Python files, compares ASTs of the old and new version to prove a change is formatting-only, docstring-only, or a consistent identifier rename. No LLM involved. Other files are classified by path and whitespace-collapsed diff text. |
| **Risk scorer** | Transparent weighted rules (size of behavioral change, sensitive paths, migrations, dependency/CI changes, missing tests, new author, bug-fix history of touched files). Every point has a reason shown in the result. Optionally replaced by a model trained on your repo's history. |
| **Summarizer** | Only behavioral hunks go to an AI model (a local Ollama model, or the Anthropic API if a key is set), with diff content treated as untrusted data. Falls back to a deterministic offline summary if neither is available or the model fails. |
| **Publisher** | Upserts a single comment (no spam on new pushes) and swaps a `risk:low/medium/high` label. |

## Quick start

```bash
pip install -e ".[dev]"

# Try it on any local git repo, no GitHub or API key needed
reviewlane analyze-local --repo-path . --base main --head HEAD --no-llm

# Or on a raw diff
git diff main | reviewlane analyze-diff - --no-llm

# With an AI-written review focus (a running Ollama is used automatically, or set an API key)
export ANTHROPIC_API_KEY=...   # optional. In PowerShell: $env:ANTHROPIC_API_KEY = "..."
reviewlane analyze-local --repo-path . --base main --head HEAD
```

### As a GitHub Action (experimental)

The Action files (`action.yml`, `Dockerfile`) are written but have not yet been run on a live repository. The CLI and web app are the tested paths. To try it, copy `.github/workflows/reviewlane.yml.example` into a repo you control as `.github/workflows/reviewlane.yml`.

## Evaluation

Reproduce with:

```bash
git clone https://github.com/pallets/flask.git
reviewlane build-dataset --repo-path flask --out flask.jsonl --max-commits 1200 --window-days 90
# repeat for requests, httpx and rich, then:
reviewlane train --data flask.jsonl requests.jsonl httpx.jsonl rich.jsonl --report results/pooled_report.json
```

**Labels (SZZ-lite).** A merged change is "buggy" if, within 90 days, a later bug-fix commit rewrote or deleted lines it introduced (via `git blame` on the fix's parent), or it was explicitly reverted. Changes newer than 90 days are dropped because they have not had time to show bugs. Features only use information available when the change landed.

**Data.** First-parent history of Flask, Requests, HTTPX and Rich: 4,722 merged changes, 155 labeled buggy (3.3%). Time-based split: train on the oldest 70%, test on the newest 30% (1,417 changes, 28 buggy).

| Method | AUC | Precision in top 10% | Share of buggy changes caught in top 10% | Lift |
|---|---|---|---|---|
| Size only (baseline) | **0.844** | 7.0% | 35.7% | 3.6x |
| ReviewLane heuristic | 0.768 | **8.5%** | **42.9%** | **4.3x** |
| Logistic regression | 0.785 | 7.0% | 35.7% | 3.6x |
| Gradient boosting | 0.831 | 7.0% | 35.7% | 3.6x |

**Honest reading of these numbers:**

- Change size is a very strong baseline, and none of the methods clearly beat it on AUC. The heuristic is slightly ahead on top-10% precision, but that is a difference of one or two PRs out of 28 positives and is well inside the noise.
- The learned models did not help on this data. With roughly 127 positive training examples and noisy labels, that is expected.
- What the risk score does add is *explainability* and the lane suggestion, not a proven accuracy gain. Treat it as a triage aid.

**Skim compression** (same four repos, 416k changed lines): 31% of changed lines were classified as safe to skim (formatting, docstring-only, renames, docs, generated files), 46% as behavioral, and 56% of PRs contained no behavioral lines at all. Docs and generated-file classification is path-based, while formatting, docstring and rename claims for Python are AST-verified.

## Limitations

- **Labels are noisy.** SZZ-lite only traces Python lines, relies on commit-message keywords, and misses bugs fixed without a keyword or in a different file. Positives are a lower bound.
- **Small positive class.** A few dozen positives in the test split means wide confidence intervals; do not read much into 1-2 PR differences.
- **Renames are checked per file.** A renamed public function is flagged as a rename only if its callers in other files changed consistently, and ReviewLane does not verify that cross-file.
- **Python-first.** Semantic proofs exist only for Python. Other languages fall back to whitespace-collapsed diff comparison.
- **Summaries can be wrong.** The AI model sees only behavioral hunks and is told to stay grounded in them, but a reviewer should treat it as a map, not a verdict.
- **Model files are pickles.** Only load models you trained yourself.
- **Not evaluated with real reviewers yet.** Reviewer time saved is the metric that matters most, and it has not been measured.

## Next steps

- Run a small reviewer-time study: 10 PRs read with and without the notes.
- Hand-check 30 AI summaries for unsupported claims and report a faithfulness rate.
- Run the GitHub Action on a live repository.
- Add tree-sitter so the classifier's proofs extend to JavaScript/TypeScript.
- Add a review analytics dashboard (queue time, rubber-stamp rate) to measure the bottleneck itself.

## Project layout

```
src/reviewlane/
  classifier.py   AST proofs + path rules
  risk.py         features + transparent heuristic
  summarizer.py   AI summary of behavioral hunks, offline fallback
  render.py       PR comment
  github.py       REST client (comment upsert, labels)
  szz.py          SZZ-lite labeling + dataset builder
  model.py        learned model + time-split evaluation
  cli.py          analyze-diff | analyze-local | analyze | action | build-dataset | train | ui
  examples.py     built-in example PRs for the demo
  ui_view.py      labels and line counts for the demo page
streamlit_app.py  the web demo (one page)
tests/            50 tests incl. a temp-git-repo SZZ test, a mocked GitHub API and headless UI tests
```
