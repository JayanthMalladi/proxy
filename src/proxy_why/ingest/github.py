"""Fetch merged pull requests, their descriptions and discussion from GitHub."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable

from ..repo import Remote
from ..store import Source

# fetch(url) -> (parsed JSON, next page URL or None)
Fetch = Callable[[str], tuple[object, str | None]]

API = "https://api.github.com"
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(RuntimeError):
    pass


def http_fetch(token: str | None) -> Fetch:
    def fetch(url: str) -> tuple[object, str | None]:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "proxy-why",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                m = _NEXT.search(resp.headers.get("Link", "") or "")
                return data, (m.group(1) if m else None)
        except urllib.error.HTTPError as e:
            raise GitHubError(f"GitHub API {e.code} for {url}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise GitHubError(f"Could not reach GitHub: {e.reason}") from e
    return fetch


def token_from_env() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _pages(fetch: Fetch, url: str, max_items: int) -> list[dict]:
    items: list[dict] = []
    next_url: str | None = url
    while next_url and len(items) < max_items:
        data, next_url = fetch(next_url)
        if not isinstance(data, list):
            raise GitHubError(f"Unexpected response from {url}")
        items.extend(data)
    return items[:max_items]


def read_pull_requests(remote: Remote, fetch: Fetch, max_prs: int = 200,
                       since: str | None = None, with_discussion: bool = True) -> list[Source]:
    """Return one Source per merged PR. `since` is an ISO date: older PRs are skipped."""
    base = f"{API}/repos/{remote.owner}/{remote.name}"
    url = f"{base}/pulls?state=closed&sort=updated&direction=desc&per_page=100"
    sources: list[Source] = []
    for pr in _pages(fetch, url, max_prs):
        if not pr.get("merged_at"):
            continue
        if since and (pr.get("updated_at") or "") < since:
            break  # sorted by updated desc, so everything after is older
        number = pr["number"]
        discussion: list[str] = []
        if with_discussion:
            discussion = _discussion(fetch, base, number)
        body = (pr.get("body") or "").strip()
        if discussion:
            body = (body + "\n\n## Discussion\n" + "\n\n".join(discussion)).strip()
        sources.append(Source(
            kind="pr",
            ref=str(number),
            title=(pr.get("title") or f"PR #{number}").strip(),
            body=body,
            author=(pr.get("user") or {}).get("login", ""),
            date=pr.get("merged_at") or "",
            url=pr.get("html_url") or remote.pr_url(number),
            meta={"pr": number, "merge_sha": pr.get("merge_commit_sha"),
                  "comments": len(discussion)},
        ))
    return sources


def _discussion(fetch: Fetch, base: str, number: int) -> list[str]:
    out: list[str] = []
    for kind, url in (
        ("review", f"{base}/pulls/{number}/reviews?per_page=100"),
        ("comment", f"{base}/issues/{number}/comments?per_page=100"),
        ("inline", f"{base}/pulls/{number}/comments?per_page=100"),
    ):
        for item in _pages(fetch, url, 300):
            text = (item.get("body") or "").strip()
            if not text or _is_bot(item):
                continue
            who = (item.get("user") or {}).get("login", "someone")
            where = f" on {item['path']}" if kind == "inline" and item.get("path") else ""
            out.append(f"{who}{where}: {text}")
    return out


def _is_bot(item: dict) -> bool:
    user = item.get("user") or {}
    return user.get("type") == "Bot" or user.get("login", "").endswith("[bot]")
