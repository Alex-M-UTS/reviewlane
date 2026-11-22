"""Minimal GitHub REST client: fetch a PR, post one comment, set a risk label."""
from __future__ import annotations

import httpx

from .classifier import classify_pr
from .models import FileChange, PullRequest
from .render import MARKER

API = "https://api.github.com"
MAX_SOURCE_FILES = 40
MAX_SOURCE_BYTES = 200_000
RISK_LABELS = ("risk:low", "risk:medium", "risk:high")


class GitHubClient:
    def __init__(self, token: str, api: str = API, client: httpx.Client | None = None):
        self.api = api.rstrip("/")
        self.http = client or httpx.Client(timeout=30.0)
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:  # public repos can be read without a token (at a lower rate limit)
            self.headers["Authorization"] = f"Bearer {token}"

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {**self.headers, **kw.pop("headers", {})}
        resp = self.http.request(method, f"{self.api}{path}", headers=headers, **kw)
        resp.raise_for_status()
        return resp

    def get_pr(self, repo: str, number: int) -> dict:
        return self._req("GET", f"/repos/{repo}/pulls/{number}").json()

    def get_pr_files(self, repo: str, number: int, max_files: int = 300) -> list[dict]:
        files: list[dict] = []
        page = 1
        while len(files) < max_files:
            batch = self._req("GET", f"/repos/{repo}/pulls/{number}/files",
                              params={"per_page": 100, "page": page}).json()
            files += batch
            if len(batch) < 100:
                break
            page += 1
        return files[:max_files]

    def get_file(self, repo: str, path: str, ref: str) -> str | None:
        try:
            resp = self._req("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref},
                             headers={"Accept": "application/vnd.github.raw+json"})
        except httpx.HTTPStatusError:
            return None
        text = resp.text
        return text if len(text) <= MAX_SOURCE_BYTES else None

    def count_prior_prs(self, repo: str, author: str) -> int:
        """Merged PRs by this author in the repo (search API; best effort)."""
        try:
            data = self._req("GET", "/search/issues",
                             params={"q": f"repo:{repo} type:pr is:merged author:{author}", "per_page": 1}).json()
            return int(data.get("total_count", 0))
        except httpx.HTTPError:
            return 0

    def upsert_comment(self, repo: str, number: int, body: str) -> None:
        """Update our previous comment in place so the PR never gets comment spam."""
        comments = self._req("GET", f"/repos/{repo}/issues/{number}/comments", params={"per_page": 100}).json()
        for c in comments:
            if MARKER in (c.get("body") or ""):
                self._req("PATCH", f"/repos/{repo}/issues/comments/{c['id']}", json={"body": body})
                return
        self._req("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})

    def set_risk_label(self, repo: str, number: int, level: str) -> None:
        wanted = f"risk:{level}"
        current = {l["name"] for l in self._req("GET", f"/repos/{repo}/issues/{number}/labels").json()}
        for stale in (set(RISK_LABELS) & current) - {wanted}:
            self._req("DELETE", f"/repos/{repo}/issues/{number}/labels/{stale}")
        if wanted not in current:
            self._req("POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": [wanted]})


def build_pull_request(gh: GitHubClient, repo: str, number: int, max_sources: int = MAX_SOURCE_FILES) -> PullRequest:
    meta = gh.get_pr(repo, number)
    base_sha, head_sha = meta["base"]["sha"], meta["head"]["sha"]
    files: list[FileChange] = []
    for raw in gh.get_pr_files(repo, number):
        status = {"removed": "removed", "added": "added", "renamed": "renamed"}.get(raw["status"], "modified")
        files.append(FileChange(
            path=raw["filename"], status=status,
            additions=raw.get("additions", 0), deletions=raw.get("deletions", 0),
            patch=raw.get("patch", "") or "",
        ))
    return _attach_sources(gh, repo, meta, files, base_sha, head_sha, max_sources)


def _attach_sources(gh, repo, meta, files, base_sha, head_sha, max_sources=MAX_SOURCE_FILES) -> PullRequest:
    fetched = 0
    for f in files:
        if f.path.endswith(".py") and fetched < max_sources:
            if f.status != "added":
                f.old_source = gh.get_file(repo, f.path, base_sha)
            if f.status != "removed":
                f.new_source = gh.get_file(repo, f.path, head_sha)
            fetched += 1
    author = meta["user"]["login"]
    pr = PullRequest(
        title=meta.get("title", ""), body=meta.get("body") or "", author=author,
        number=meta["number"], base_sha=base_sha, head_sha=head_sha, files=files,
        author_prior_prs=gh.count_prior_prs(repo, author),
    )
    return classify_pr(pr)
