"""The four GitHub REST calls the panel needs. Stdlib only.

Token: a fine-grained PAT scoped to this one repository, with
Contents: read/write and Pull requests: read/write (Metadata: read is implied).
"""

import base64
import json
import urllib.error
import urllib.request

API = "https://api.github.com"
BRANCH_PREFIX = "kpanel/"


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, token: str, repo: str, base: str = "main"):
        if not token:
            raise GitHubError("GITHUB_TOKEN is not set (set KPANEL_GITHUB_TOKEN)")
        self.token, self.repo, self.base = token, repo, base

    def _req(self, method, path, body=None):
        req = urllib.request.Request(
            f"{API}/repos/{self.repo}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "kpanel",
                **({"Content-Type": "application/json"} if body is not None else {}),
            })
        try:
            # The URL is API (https) plus a path: never file: or another scheme.
            with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message", "")
            except Exception:
                msg = ""
            raise GitHubError(f"GitHub {method} {path}: HTTP {e.code} {msg}".strip()) from None
        except urllib.error.URLError as e:
            raise GitHubError(f"GitHub unreachable: {e.reason}") from None

    def head(self) -> str:
        """Commit SHA at the tip of the base branch."""
        return self._req("GET", f"/git/ref/heads/{self.base}")["object"]["sha"]

    def read(self, path: str, ref: str):
        """(text, blob_sha) of a file at a given commit."""
        f = self._req("GET", f"/contents/{path}?ref={ref}")
        return base64.b64decode(f["content"]).decode(), f["sha"]

    def open_pr(self, *, path, new_text, blob_sha, from_commit, branch, title, body) -> str:
        """Branch from `from_commit`, commit `new_text` to `path`, open a PR. Returns its URL.

        Branching from the exact commit the edit was computed against (not from
        whatever main is now) means the PR's diff is exactly the edit, and
        GitHub flags a conflict if main moved underneath, instead of the panel
        silently reverting someone else's change.
        """
        self._req("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": from_commit})
        self._req("PUT", f"/contents/{path}", {
            "message": title, "branch": branch, "sha": blob_sha,
            "content": base64.b64encode(new_text.encode()).decode(),
        })
        pr = self._req("POST", "/pulls", {"title": title, "head": branch, "base": self.base, "body": body})
        return pr["html_url"]

    def open_panel_prs(self):
        """Open PRs this panel created: [(number, title, url)]."""
        prs = self._req("GET", "/pulls?state=open&per_page=50") or []
        return [(p["number"], p["title"], p["html_url"]) for p in prs
                if p["head"]["ref"].startswith(BRANCH_PREFIX)]
