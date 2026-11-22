import httpx

from reviewlane.github import GitHubClient, build_pull_request
from reviewlane.models import ChangeKind
from reviewlane.render import MARKER

OLD = "def f(a,b):\n  return a+b\n"
NEW = "def f(a, b):\n    return a + b\n"


def make_client(existing_comments, log):
    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        log.append((method, path))
        if path == "/repos/o/r/pulls/7":
            return httpx.Response(200, json={"number": 7, "title": "Tidy", "body": "", "user": {"login": "al"},
                                             "base": {"sha": "b" * 40}, "head": {"sha": "h" * 40}})
        if path == "/repos/o/r/pulls/7/files":
            return httpx.Response(200, json=[{"filename": "m.py", "status": "modified", "additions": 2, "deletions": 2,
                                              "patch": "@@ -1,2 +1,2 @@\n-def f(a,b):\n-  return a+b\n+def f(a, b):\n+    return a + b"}])
        if path == "/repos/o/r/contents/m.py":
            return httpx.Response(200, text=OLD if request.url.params["ref"] == "b" * 40 else NEW)
        if path == "/search/issues":
            return httpx.Response(200, json={"total_count": 12})
        if path == "/repos/o/r/issues/7/comments" and method == "GET":
            return httpx.Response(200, json=existing_comments)
        if path.startswith("/repos/o/r/issues/") and method in ("POST", "PATCH"):
            return httpx.Response(201, json={})
        if path == "/repos/o/r/issues/7/labels" and method == "GET":
            return httpx.Response(200, json=[{"name": "risk:high"}, {"name": "bug"}])
        return httpx.Response(204)
    return GitHubClient("tok", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_build_pull_request_verifies_formatting_with_ast():
    gh = make_client([], [])
    pr = build_pull_request(gh, "o/r", 7)
    assert pr.author == "al" and pr.author_prior_prs == 12
    assert pr.files[0].kind is ChangeKind.FORMATTING


def test_comment_is_posted_once_then_updated():
    log = []
    make_client([], log).upsert_comment("o/r", 7, MARKER + "x")
    assert ("POST", "/repos/o/r/issues/7/comments") in log

    log = []
    make_client([{"id": 99, "body": MARKER + "old"}], log).upsert_comment("o/r", 7, MARKER + "new")
    assert ("PATCH", "/repos/o/r/issues/comments/99") in log
    assert not any(m == "POST" for m, _ in log)


def test_label_swap_removes_stale_risk_label_only():
    log = []
    make_client([], log).set_risk_label("o/r", 7, "low")
    assert ("DELETE", "/repos/o/r/issues/7/labels/risk:high") in log
    assert not any("bug" in p for _, p in log)
    assert ("POST", "/repos/o/r/issues/7/labels") in log
