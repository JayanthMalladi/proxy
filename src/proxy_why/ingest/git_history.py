"""Read commits from git log, including which PR each one came from."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ..repo import Remote, git
from ..store import Source

# Fields are separated by unit separators and records by record separators so
# commit messages containing newlines or pipes can't break parsing.
_US, _RS = "\x1f", "\x1e"
_FORMAT = _RS + _US.join(["%H", "%P", "%an", "%aI", "%s", "%b"]) + _US

_PR_PATTERNS = [
    re.compile(r"\(#(\d+)\)\s*$"),  # squash merge: "Fix retry (#123)"
    re.compile(r"^Merge pull request #(\d+)"),  # merge commit
    re.compile(r"^Merged in .*\(pull request #(\d+)\)"),  # Bitbucket
    re.compile(r"See merge request .*!(\d+)"),  # GitLab
]

MAX_FILES = 200


def pr_number(subject: str, body: str = "") -> int | None:
    for pat in _PR_PATTERNS:
        m = pat.search(subject) or pat.search(body)
        if m:
            return int(m.group(1))
    return None


def read_commits(root: Path, since: str | None = None, limit: int | None = None,
                 remote: Remote | None = None) -> list[Source]:
    args = ["log", f"--format={_FORMAT}", "--name-only", "--no-renames"]
    if limit:
        args.append(f"-n{limit}")
    if since:
        args.append(f"{since}..HEAD")
    out = git(root, *args, check=False)
    commits: list[Source] = []
    for record in out.split(_RS):
        if not record.strip():
            continue
        parts = record.split(_US)
        if len(parts) < 7:
            continue
        sha, parents, author, date, subject, body, rest = parts[:7]
        files = [f for f in rest.strip().splitlines() if f.strip()][:MAX_FILES]
        number = pr_number(subject, body)
        meta: dict = {}
        if number:
            meta["pr"] = number
        if len(parents.split()) > 1:
            meta["merge"] = True
            meta["parents"] = parents.split()
        commits.append(Source(
            kind="commit",
            ref=sha,
            title=subject.strip(),
            body=body.strip(),
            author=author,
            date=_utc(date),
            url=remote.commit_url(sha) if remote else "",
            files=files,
            meta=meta,
        ))
    return commits


def link_merged_commits(root: Path, commits: list[Source]) -> dict[str, int]:
    """For merge-based repos: map each commit brought in by a PR merge commit to that PR.

    Squash-merged commits already carry "(#123)" in their subject; commits inside a
    merged branch don't, so we walk each PR merge commit's second parent.
    """
    mapping: dict[str, int] = {}
    for c in commits:
        if not (c.meta.get("merge") and c.meta.get("pr")):
            continue
        p1, p2 = c.meta["parents"][0], c.meta["parents"][1]
        for sha in git(root, "rev-list", f"{p1}..{p2}", check=False).split():
            mapping.setdefault(sha, c.meta["pr"])
    return mapping


def pr_from_merge(commit: Source, remote: Remote | None) -> Source | None:
    """Build a stand-in PR source from a merge commit when GitHub data isn't available."""
    number = commit.meta.get("pr")
    if not (commit.meta.get("merge") and number):
        return None
    title = merge_body_title(commit.body) or commit.title
    rest = commit.body.split("\n", 1)[1].strip() if "\n" in commit.body else ""
    return Source(
        kind="pr", ref=str(number), title=title, body=rest, author=commit.author, date=commit.date,
        url=remote.pr_url(number) if remote else "", files=list(commit.files),
        meta={"pr": number, "from_merge": True, "merge_sha": commit.ref},
    )


def merge_body_title(body: str) -> str:
    """Merge commits put the PR title on the first body line."""
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _utc(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return iso
