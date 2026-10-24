"""Thin wrappers around the git command line."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def find_root(start: Path | str = ".") -> Path:
    try:
        out = git(Path(start), "rev-parse", "--show-toplevel")
    except GitError as e:
        raise GitError(f"{Path(start).resolve()} is not inside a git repository") from e
    return Path(out.strip())


@dataclass
class Remote:
    host: str
    owner: str
    name: str

    @property
    def web(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.name}"

    def commit_url(self, sha: str) -> str:
        return f"{self.web}/commit/{sha}"

    def pr_url(self, number: int | str) -> str:
        return f"{self.web}/pull/{number}"

    def blob_url(self, path: str) -> str:
        return f"{self.web}/blob/HEAD/{path}"


_REMOTE_RE = re.compile(r"(?:https?://|ssh://)?(?:[^@/]+@)?([^/:]+)[:/]([^/]+)/([^/]+?)(?:\.git)?/?$")


def parse_remote(url: str) -> Remote | None:
    m = _REMOTE_RE.match(url.strip())
    if not m:
        return None
    return Remote(host=m.group(1), owner=m.group(2), name=m.group(3))


def origin(root: Path) -> Remote | None:
    url = git(root, "remote", "get-url", "origin", check=False).strip()
    return parse_remote(url) if url else None


def head(root: Path) -> str | None:
    out = git(root, "rev-parse", "HEAD", check=False).strip()
    return out or None


def blame_line(root: Path, path: str, line: int) -> dict | None:
    """Return the commit that last changed one line, or None if git can't tell."""
    out = git(root, "blame", "--porcelain", "-L", f"{line},{line}", "--", path, check=False)
    if not out:
        return None
    lines = out.splitlines()
    sha = lines[0].split()[0]
    info: dict = {"sha": sha, "code": ""}
    for ln in lines[1:]:
        if ln.startswith("\t"):
            info["code"] = ln[1:]
        elif ln.startswith("author "):
            info["author"] = ln[len("author "):]
        elif ln.startswith("author-time "):
            info["time"] = int(ln.split()[1])
        elif ln.startswith("summary "):
            info["summary"] = ln[len("summary "):]
        elif ln.startswith("filename "):
            info["filename"] = ln[len("filename "):]
    if set(sha) == {"0"}:
        info["uncommitted"] = True
    return info
