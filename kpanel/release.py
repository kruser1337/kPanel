"""Which kPanel this is, and whether GitHub has a newer release.

VERSION is bumped by hand in the commit that gets tagged v<VERSION>, so a
clone always knows what it is without git or network. The check asks GitHub's
public API for the latest release (drafts and pre-releases excluded by GitHub)
from a background thread, so a page never waits on it; until it has an answer,
or when it fails, the footer simply says nothing about updates.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request

VERSION = "0.3.0"
REPO = "kruser1337/kPanel"
CHECK_SECONDS = 12 * 3600  # unauthenticated API: 60 requests/h per IP, this uses 2/day

# The whole tag, not a prefix: it ends up in the page and in a link.
_TAG = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})")

LATEST = {"tag": None}  # set by the checker; read by the footer


def parse(tag):
    """'v1.2.3' -> (1, 2, 3); None for anything else."""
    m = _TAG.fullmatch(tag or "")
    return tuple(int(x) for x in m.groups()) if m else None


def fetch_latest(repo=REPO, timeout=5):
    """The latest release's tag, or None (no releases yet, offline, rate-limited)."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "kpanel"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        return data.get("tag_name") if isinstance(data, dict) else None
    except (OSError, ValueError):  # URLError/HTTPError are OSErrors; ValueError: bad JSON
        return None


def check(fetch=fetch_latest):
    tag = fetch()
    LATEST["tag"] = tag if parse(tag) else None


def checker():
    while True:
        check()
        time.sleep(CHECK_SECONDS)


def start():
    threading.Thread(target=checker, daemon=True, name="release-check").start()


def status(current=VERSION):
    """(state, tag): ("update", "v0.2.0"), ("current", "v0.1.0") or (None, None) if unknown."""
    latest = LATEST["tag"]
    if not latest:
        return None, None
    return ("update" if parse(latest) > parse(current) else "current"), latest


def release_url(tag):
    return f"https://github.com/{REPO}/releases/tag/{tag}"
