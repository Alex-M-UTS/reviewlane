from reviewlane.diffparse import parse_diff
from reviewlane.models import ChangeKind, FileChange, PullRequest
from reviewlane.render import MARKER, render_comment
from reviewlane.risk import extract_features, heuristic_score, score_pr
from reviewlane.summarizer import build_prompt, offline_summary, summarize

DIFF = """diff --git a/src/auth/login.py b/src/auth/login.py
index 111..222 100644
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -1,2 +1,3 @@
 def login(u):
-    return check(u)
+    token = make_token(u)
+    return check(u, token)
"""


def beh(path, n, **kw):
    return FileChange(path=path, additions=n, kind=ChangeKind.BEHAVIORAL, **kw)


def test_parse_diff():
    files = parse_diff(DIFF)
    assert len(files) == 1 and files[0].path == "src/auth/login.py"
    assert files[0].additions == 2 and files[0].deletions == 1


def test_small_safe_pr_is_low():
    pr = PullRequest(files=[beh("src/util.py", 10), FileChange(path="tests/test_util.py", additions=10, kind=ChangeKind.TEST)],
                     author_prior_prs=50)
    assert score_pr(pr).level == "low"


def test_large_sensitive_untested_pr_is_high():
    pr = PullRequest(files=[beh("src/auth/session.py", 300), beh("src/payments/charge.py", 200), beh("migrations/001.sql", 40)],
                     author_prior_prs=0, file_prior_fixes={"src/auth/session.py": 4})
    r = score_pr(pr)
    assert r.level == "high" and r.score >= 50
    assert any("sensitive" in t.lower() for t, _ in r.reasons)
    assert sum(p for _, p in r.reasons) >= r.score


def test_noise_only_pr_is_capped_low():
    pr = PullRequest(files=[FileChange(path="a.py", additions=900, deletions=900, kind=ChangeKind.FORMATTING)], author_prior_prs=0)
    assert score_pr(pr).level == "low"


def test_features_have_no_future_info_and_are_numeric():
    f = extract_features(PullRequest(files=[beh("a.py", 25)]))
    assert f["untested_change"] == 1.0
    assert all(isinstance(v, float) for v in f.values())
    assert heuristic_score(f)[0] > 0


class FakeBlock:
    type = "text"
    def __init__(self, text): self.text = text


class FakeClient:
    def __init__(self, text="**What changed**\n- x\n\n**What to check**\n- y", fail=False):
        self.calls = []; self.text = text; self.fail = fail
        self.messages = self
    def create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("boom")
        class R: content = [FakeBlock(self.text)]
        return R()


def test_llm_only_sees_behavioral_hunks():
    pr = PullRequest(title="t", files=[
        FileChange(path="fmt.py", patch="@@\n-a\n+b", kind=ChangeKind.FORMATTING),
        FileChange(path="core.py", patch="@@\n-a\n+SECRET_BEHAVIOR", kind=ChangeKind.BEHAVIORAL, additions=1),
    ])
    client = FakeClient()
    text, src = summarize(pr, client=client)
    prompt = client.calls[0]["messages"][0]["content"]
    assert src == "llm" and "SECRET_BEHAVIOR" in prompt and "fmt.py" not in prompt
    assert "untrusted" in client.calls[0]["system"]


def test_llm_failure_falls_back_offline():
    pr = PullRequest(files=[beh("core.py", 5, patch="@@\n+x")])
    text, src = summarize(pr, client=FakeClient(fail=True))
    assert src == "offline" and "core.py" in text


def test_prompt_is_truncated():
    pr = PullRequest(files=[beh("big.py", 1, patch="@@\n" + "+x\n" * 50_000)])
    assert len(build_prompt(pr, max_chars=5_000)) < 6_500


def test_no_behavioral_changes_skips_llm():
    pr = PullRequest(files=[FileChange(path="a.md", kind=ChangeKind.DOCS)])
    client = FakeClient()
    _, src = summarize(pr, client=client)
    assert src == "offline" and not client.calls


def test_render_comment_structure():
    pr = PullRequest(files=[
        beh("src/auth/login.py", 3, symbols=["login (modified)"]),
        FileChange(path="fmt.py", additions=5, deletions=5, kind=ChangeKind.FORMATTING, reason="AST identical"),
    ])
    pr.files[0].deletions = 1
    risk = score_pr(pr)
    body = render_comment(pr, risk, offline_summary(pr))
    assert body.startswith(MARKER)
    assert "Needs human judgment" in body and "login (modified)" in body
    assert "Safe to skim: 10 of 14" in body
