from pathlib import Path

import pytest

from reviewlane.examples import EXAMPLES, EXAMPLES_BY_KEY
from reviewlane.models import ChangeKind
from reviewlane.risk import score_pr
from reviewlane import ui_view as v

DIFF = """diff --git a/src/auth/login.py b/src/auth/login.py
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -1,2 +1,3 @@
 def login(u):
-    return check(u)
+    token = make_token(u)
+    return check(u, token)
"""


# ---- examples: each one must exercise the real classifier and land where its story says

@pytest.mark.parametrize("key,level", [("needle", "medium"), ("login", "high"), ("tidy", "low"), ("feature", "medium")])
def test_example_risk_levels(key, level):
    assert score_pr(EXAMPLES_BY_KEY[key].build()).level == level


def test_needle_isolates_the_real_change():
    pr = EXAMPLES_BY_KEY["needle"].build()
    behavioral = [f for f in pr.files if f.kind is ChangeKind.BEHAVIORAL]
    assert [f.path for f in behavioral] == ["shop/billing/discounts.py"]
    assert sum(f.changed_lines for f in behavioral) == 4
    assert all(f.kind is ChangeKind.FORMATTING for f in pr.files if f not in behavioral)


def test_tidy_has_nothing_behavioral():
    pr = EXAMPLES_BY_KEY["tidy"].build()
    kinds = {f.path: f.kind for f in pr.files}
    assert kinds["app/pricing.py"] is ChangeKind.RENAME
    assert kinds["app/models.py"] is ChangeKind.DOCS
    assert v.counts(pr)["judge"] == 0


def test_every_example_has_unique_key_and_parses():
    assert len({e.key for e in EXAMPLES}) == len(EXAMPLES)
    for e in EXAMPLES:
        assert e.build().files


# ---- helpers

def test_counts_split_lines_by_kind():
    c = v.counts(EXAMPLES_BY_KEY["needle"].build())
    assert (c["judge"], c["tc"], c["skim"], c["total"]) == (4, 0, 87, 91)
    assert (c["judge_files"], c["files"]) == (1, 6)


# ---- the Streamlit app itself

def _app():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit_app.py"), default_timeout=60)


def _text(at):
    parts = [m.value for m in at.markdown] + [e.value for e in at.error] + [w.value for w in at.warning]
    parts += [s.value for s in at.success] + [m.value for m in at.metric]
    return " ".join(parts)


def test_app_renders_default_example_without_errors():
    at = _app().run()
    assert not at.exception
    assert "4 of 91" in [m.value for m in at.metric]
    assert "Medium risk" in _text(at)


def test_app_switches_examples():
    at = _app().run()
    at.selectbox[0].set_value("login").run()
    assert not at.exception
    assert "High risk" in _text(at)
    at.selectbox[0].set_value("tidy").run()
    assert "Low risk" in _text(at) and "0 of 114" in [m.value for m in at.metric]


def test_paste_diff_flow_and_friendly_error():
    at = _app().run()
    at.selectbox[0].set_value("paste-diff").run()
    at.button[0].click().run()  # empty box
    assert any("Couldn't find any changes" in e.value for e in at.error)
    at.text_area[0].set_value(DIFF)
    at.button[0].click().run()
    assert not at.exception
    assert not any("Couldn't find" in e.value for e in at.error)
    assert "risk" in _text(at).lower()


def test_bad_github_link_gives_plain_message():
    at = _app().run()
    at.selectbox[0].set_value("github-link").run()
    at.text_input[0].set_value("not a link")
    at.button[0].click().run()
    assert any("doesn't look like a pull request link" in e.value for e in at.error)


def test_results_section_matches_the_saved_report():
    at = _app().run()
    page = _text(at)
    assert "4,722 merged changes" in page and "about as good as size" in page
