"""Learned risk model plus the evaluation harness (time-based split, top-k precision)."""
from __future__ import annotations

import bisect
import json
import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .risk import FEATURE_NAMES, heuristic_score


def _vec(feats: dict[str, float]) -> list[float]:
    return [float(feats.get(n, 0.0)) for n in FEATURE_NAMES]


@dataclass
class RiskModel:
    clf: Any
    train_scores: list[float]  # sorted training-set probabilities, for percentile mapping
    kind: str = "gbm"

    def predict_proba(self, feats: dict[str, float]) -> float:
        return float(self.clf.predict_proba([_vec(feats)])[0][1])

    def percentile(self, p: float) -> float:
        """Where this probability sits within the training distribution (0-100)."""
        if not self.train_scores:
            return 0.0
        return 100.0 * bisect.bisect_right(self.train_scores, p) / len(self.train_scores)

    @staticmethod
    def level_for_percentile(pct: float) -> str:
        return "low" if pct < 70 else "medium" if pct < 90 else "high"

    def save(self, path: str | Path) -> None:
        with open(path, "wb") as fh:
            pickle.dump(self, fh)

    @staticmethod
    def load(path: str | Path) -> "RiskModel":
        # Only load model files you trained yourself: pickle can execute code.
        with open(path, "rb") as fh:
            return pickle.load(fh)


def _make_clf(kind: str):
    if kind == "logreg":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced"))
    from sklearn.ensemble import GradientBoostingClassifier
    return GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.05, random_state=0)


def train(rows: list[dict], kind: str = "gbm") -> RiskModel:
    X = [_vec(r["features"]) for r in rows]
    y = [int(r["label"]) for r in rows]
    if len(set(y)) < 2:
        raise ValueError("Training data needs both positive and negative examples.")
    clf = _make_clf(kind).fit(X, y)
    scores = sorted(float(p[1]) for p in clf.predict_proba(X))
    return RiskModel(clf=clf, train_scores=scores, kind=kind)


def time_split(rows: list[dict], train_frac: float = 0.7) -> tuple[list[dict], list[dict]]:
    """Train on older PRs, test on newer ones, so the future never leaks into training."""
    ordered = sorted(rows, key=lambda r: r["ts"])
    cut = int(len(ordered) * train_frac)
    return ordered[:cut], ordered[cut:]


def precision_at_top(scores: list[float], labels: list[int], frac: float = 0.1) -> dict[str, float]:
    n = len(scores)
    k = max(1, math.ceil(n * frac))
    order = sorted(range(n), key=lambda i: -scores[i])[:k]
    hits = sum(labels[i] for i in order)
    total_pos = sum(labels)
    base = total_pos / n if n else 0.0
    prec = hits / k
    return {
        "k": k,
        "precision": prec,
        "recall": (hits / total_pos) if total_pos else 0.0,
        "lift": (prec / base) if base else 0.0,
    }


def evaluate(rows: list[dict], train_frac: float = 0.7, top_frac: float = 0.1) -> dict:
    """Compare size-only baseline, transparent heuristic, and learned models on held-out PRs."""
    from sklearn.metrics import roc_auc_score

    train_rows, test_rows = time_split(rows, train_frac)
    y_test = [int(r["label"]) for r in test_rows]
    if len(set(y_test)) < 2:
        raise ValueError("Test split has a single class; need more data.")
    candidates: dict[str, list[float]] = {
        "size_only": [r["features"]["total_lines"] for r in test_rows],
        "heuristic": [float(heuristic_score(r["features"])[0]) for r in test_rows],
    }
    for kind in ("logreg", "gbm"):
        m = train(train_rows, kind)
        candidates[kind] = [m.predict_proba(r["features"]) for r in test_rows]
    report = {
        "n_train": len(train_rows),
        "n_test": len(test_rows),
        "test_base_rate": sum(y_test) / len(y_test),
        "top_frac": top_frac,
        "methods": {},
    }
    for name, scores in candidates.items():
        report["methods"][name] = {
            "auc": roc_auc_score(y_test, scores),
            **precision_at_top(scores, y_test, top_frac),
        }
    return report


def format_report(report: dict) -> str:
    lines = [
        f"train={report['n_train']}  test={report['n_test']}  test base rate={report['test_base_rate']:.1%}",
        f"{'method':<10} {'AUC':>6} {'P@top' + str(int(report['top_frac'] * 100)) + '%':>9} {'recall':>8} {'lift':>6}",
    ]
    for name, m in report["methods"].items():
        lines.append(f"{name:<10} {m['auc']:>6.3f} {m['precision']:>9.1%} {m['recall']:>8.1%} {m['lift']:>6.2f}")
    return "\n".join(lines)


def load_rows(path: str | Path) -> list[dict]:
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]
