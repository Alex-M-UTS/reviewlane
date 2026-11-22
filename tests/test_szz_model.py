import os
import random
import subprocess

import pytest

from reviewlane.risk import FEATURE_NAMES
from reviewlane.szz import (
    build_dataset, is_fix_message, label_commits, list_first_parent, removed_ranges,
)


def run(repo, *args, ts=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    if ts:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{ts} +0000"
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path):
    day = 86400
    t0 = 1_700_000_000
    run(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("hi\n")
    run(tmp_path, "add", "."); run(tmp_path, "commit", "-qm", "init", ts=t0)
    (tmp_path / "foo.py").write_text("def foo(x):\n    return x / len(x)\n")
    run(tmp_path, "add", "."); run(tmp_path, "commit", "-qm", "Add foo", ts=t0 + day)
    (tmp_path / "bar.py").write_text("def bar():\n    return 1\n")
    run(tmp_path, "add", "."); run(tmp_path, "commit", "-qm", "Add bar", ts=t0 + 2 * day)
    (tmp_path / "foo.py").write_text("def foo(x):\n    return x / max(len(x), 1)\n")
    run(tmp_path, "add", "."); run(tmp_path, "commit", "-qm", "Fix crash on empty input in foo", ts=t0 + 5 * day)
    (tmp_path / "baz.py").write_text("def baz():\n    return 3\n")
    run(tmp_path, "add", "."); run(tmp_path, "commit", "-qm", "Add baz", ts=t0 + 300 * day)
    return tmp_path


def test_fix_message_detection():
    assert is_fix_message("Fix crash in parser")
    assert is_fix_message("Revert \"Add thing\"")
    assert not is_fix_message("Add prefix support")  # 'fix' inside a word must not match


def test_removed_ranges():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3,2 +3,1 @@\n-x\n-y\n+z\n"
    assert removed_ranges(diff) == [("a.py", 3, 4)]


def test_szz_lite_labels_the_introducing_change(repo):
    commits = list_first_parent(repo, 100)
    buggy = label_commits(repo, commits, window_days=90)
    subjects = {c.sha: c.subject for c in commits}
    assert {subjects[s] for s in buggy} == {"Add foo"}


def test_dataset_rows_drop_right_censored_and_have_all_features(repo):
    rows = build_dataset(repo, window_days=90)
    subjects = {r["subject"]: r for r in rows}
    assert "Add baz" not in subjects  # too recent to know whether it was buggy
    assert subjects["Add foo"]["label"] == 1 and subjects["Add bar"]["label"] == 0
    assert set(subjects["Add foo"]["features"]) == set(FEATURE_NAMES)
    # the fix commit's own history features must not include itself
    assert subjects["Fix crash on empty input in foo"]["features"]["file_prior_fixes_max"] == 0


def test_model_beats_size_baseline_on_time_split():
    pytest.importorskip("sklearn")
    from reviewlane.model import RiskModel, evaluate, precision_at_top, train

    rng = random.Random(0)
    rows = []
    for i in range(800):
        sens = 1.0 if rng.random() < 0.2 else 0.0
        feats = {n: 0.0 for n in FEATURE_NAMES}
        feats.update(sensitive_files=sens, total_lines=float(rng.randint(1, 500)),
                     behavioral_lines=float(rng.randint(1, 500)), files_changed=float(rng.randint(1, 20)))
        p = 0.6 if sens else 0.05
        rows.append({"ts": i, "label": int(rng.random() < p), "features": feats})
    report = evaluate(rows)
    assert report["methods"]["gbm"]["auc"] > report["methods"]["size_only"]["auc"] + 0.1
    assert report["methods"]["gbm"]["lift"] > 1.5

    model = train(rows)
    pct = model.percentile(model.predict_proba(rows[0]["features"]))
    assert 0 <= pct <= 100 and RiskModel.level_for_percentile(95) == "high"
    assert precision_at_top([0.9, 0.8, 0.1, 0.0], [1, 0, 0, 0], 0.25)["precision"] == 1.0
