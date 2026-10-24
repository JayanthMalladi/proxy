"""Bring the local store up to date with the repository's history."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import extract
from .ingest import docs, git_history, github, sessions
from .redact import redact
from .repo import git, head, origin
from .store import Source, Store

STORE_DIR = ".proxy"
STORE_FILE = "proxy.db"


def store_path(root: Path) -> Path:
    return root / STORE_DIR / STORE_FILE


def open_store(root: Path) -> Store:
    return Store(store_path(root))


@dataclass
class SyncReport:
    commits: int = 0
    prs: int = 0
    prs_from_merges: int = 0
    docs: int = 0
    sessions: int = 0
    cards: int = 0
    redacted: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        prs = f"{self.prs} PRs from GitHub" if self.prs or not self.prs_from_merges else \
            f"{self.prs_from_merges} PRs from merge commits"
        parts = [f"{self.commits} commits", prs, f"{self.docs} doc sections",
                 f"{self.sessions} sessions"]
        text = f"Read {', '.join(parts)}. {self.cards} decision cards written."
        if self.redacted:
            text += f" {self.redacted} secrets redacted."
        return text


def sync(root: Path, store: Store, *, github_prs: bool | None = None, include_sessions: bool | None = None,
         max_prs: int = 200, fetch: github.Fetch | None = None, claude_home: Path | None = None,
         use_llm: bool | None = None, log=lambda msg: None) -> SyncReport:
    report = SyncReport()
    remote = origin(root)
    tracked = set(git(root, "ls-files", check=False).splitlines())

    if include_sessions is not None:
        store.set_meta("sessions", "on" if include_sessions else "off")
    include_sessions = store.get_meta("sessions") == "on"

    changed: list[Source] = []

    # Commits, incrementally from the last synced HEAD when it is still an ancestor.
    last = store.get_meta("last_commit")
    if last and _is_ancestor(root, last):
        commits = git_history.read_commits(root, since=last, remote=remote)
    else:
        commits = git_history.read_commits(root, remote=remote)
    log(f"commits: {len(commits)} new")
    by_merge = git_history.link_merged_commits(root, commits)
    for c in commits:
        if not c.meta.get("pr") and c.ref in by_merge:
            c.meta["pr"] = by_merge[c.ref]
            c.meta["via_merge"] = True
        report.redacted += _save(store, c)
        changed.append(c)
    report.commits = len(commits)

    # Merge commits name their PR; use them as PR records until GitHub data replaces them.
    for c in commits:
        stand_in = git_history.pr_from_merge(c, remote)
        if stand_in is None:
            continue
        existing = store.get_source("pr", stand_in.ref)
        if existing and not existing.meta.get("from_merge"):
            continue
        report.redacted += _save(store, stand_in)
        changed.append(stand_in)
        report.prs_from_merges += 1

    # Pull requests from GitHub, when a token is available or explicitly requested.
    token = github.token_from_env()
    want_prs = github_prs if github_prs is not None else bool(token)
    if want_prs:
        if remote is None or remote.host != "github.com":
            report.notes.append("Skipped PRs: origin is not a github.com repository.")
        else:
            try:
                prs = github.read_pull_requests(remote, fetch or github.http_fetch(token), max_prs=max_prs,
                                                since=store.get_meta("last_pr_sync"))
                for p in prs:
                    report.redacted += _save(store, p)
                    changed.append(p)
                report.prs = len(prs)
                if prs:
                    store.set_meta("last_pr_sync", max(p.date for p in prs))
            except github.GitHubError as e:
                report.notes.append(f"Skipped PRs: {e}")
    elif remote and remote.host == "github.com":
        report.notes.append("PR descriptions and reviews not fetched: set GITHUB_TOKEN to include them.")

    # Docs are cheap to re-read every time.
    doc_sources = docs.read_docs(root, remote)
    for d in doc_sources:
        report.redacted += _save(store, d)
        changed.append(d)
    report.docs = len(doc_sources)

    if include_sessions:
        sess = sessions.read_sessions(root, claude_home)
        for s in sess:
            report.redacted += _save(store, s)
            changed.append(s)
        report.sessions = len(sess)

    # A source can appear twice (a PR merged twice, a re-read doc); keep the last version.
    changed = list({(s.kind, s.ref): s for s in changed}.values())

    # Link PRs to the files their commits touched, and text to the files it names.
    pr_commits = _pr_commits(store)
    for src in changed:
        if src.kind == "pr":
            linked = pr_commits.get(int(src.ref), [])
            src.files = sorted(set(src.files).union(*(c.files for c in linked)))
            if src.meta.get("from_merge") and not src.body.strip() and linked:
                # Without GitHub data, a PR's commit messages are the best record of its reasoning.
                src.body = "\n\n".join(f"{c.title}\n{c.body}".strip() for c in reversed(linked))
        if src.kind in ("pr", "doc", "session"):
            src.files = sorted(set(src.files) | mentioned_files(src.body, tracked))
        store.upsert_source(src)

    for src in changed:
        pr_url = remote.pr_url(src.meta["pr"]) if remote and src.meta.get("pr") else None
        card = extract.extract(src, pr_url=pr_url, use_llm=use_llm)
        if card:
            card.source_id = src.id
            store.upsert_card(card)
            report.cards += 1

    sha = head(root)
    if sha:
        store.set_meta("last_commit", sha)
    store.commit()
    return report


def _is_ancestor(root: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", sha, "HEAD"],
                          capture_output=True).returncode == 0


def _save(store: Store, src: Source) -> int:
    src.title, n1 = redact(src.title)
    src.body, n2 = redact(src.body)
    store.upsert_source(src)
    return n1 + n2


def _pr_commits(store: Store) -> dict[int, list[Source]]:
    out: dict[int, list[Source]] = {}
    for c in store.sources("commit"):
        n = c.meta.get("pr")
        if n and not c.meta.get("merge"):
            out.setdefault(int(n), []).append(c)
    return out


_PATHISH = re.compile(r"[`'\"(\s]((?:[\w.\-]+/)+[\w.\-]+\.\w{1,8})(?::\d+)?")


def mentioned_files(text: str, tracked: set[str]) -> set[str]:
    found = set()
    for m in _PATHISH.finditer(" " + (text or "")):
        path = m.group(1).lstrip("./")
        if path in tracked:
            found.add(path)
    return found
