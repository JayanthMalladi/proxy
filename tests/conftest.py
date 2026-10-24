import json
import os
import subprocess
from pathlib import Path

import pytest

from proxy_why.store import Store


def run(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def commit(root: Path, files: dict[str, str], message: str, author: str = "Ravi <ravi@example.com>",
           date: str = "2026-03-11T10:00:00+05:30") -> str:
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        run(root, "add", path)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message, f"--author={author}", f"--date={date}"],
                   check=True, capture_output=True, env=_env(date))
    return run(root, "rev-parse", "HEAD").strip()


def _env(date: str) -> dict:
    return {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_COMMITTER_NAME": "ci",
            "GIT_COMMITTER_EMAIL": "ci@example.com"}


RETRY_V1 = """def handle_callback(event):
    RETRY_MAX = 5
    return process(event, retries=RETRY_MAX)
"""

RETRY_V2 = """def handle_callback(event):
    RETRY_MAX = 3
    return process(event, retries=RETRY_MAX)
"""

ADR = """# 2. Use SQLite for the local store

## Status
Accepted

## Context
The tool runs on laptops and must work offline. Running a database server would make setup harder for every user.

## Decision
We will use a single SQLite file per repository. It ships with Python and needs no server.

## Alternatives considered
Postgres was considered but rejected because it needs a running server. Plain JSON files were rejected because search would be slow.
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "payments"
    root.mkdir()
    run(root, "init", "-q", "-b", "main")
    run(root, "config", "user.name", "Test")
    run(root, "config", "user.email", "test@example.com")
    run(root, "remote", "add", "origin", "git@github.com:acme/payments.git")
    commit(root, {"payments/callback.py": RETRY_V1, "README.md": "# Payments\n"}, "Add UPI callback handler",
           date="2026-01-05T10:00:00+05:30")
    # A feature branch merged through a PR merge commit.
    run(root, "checkout", "-q", "-b", "fix-retries")
    commit(root, {"payments/callback.py": RETRY_V2},
           "Cut callback retries from 5 to 3\n\nA fourth retry caused duplicate refunds in incident INC-311 "
           "because NPCI callbacks can arrive up to 90s late. We tried exponential backoff instead of a fixed "
           "gap, but the gateway rate-limits bursts.",
           date="2026-03-11T10:00:00+05:30")
    run(root, "checkout", "-q", "main")
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "fix-retries", "-m",
                    "Merge pull request #4127 from acme/fix-retries\n\nCut UPI callback retries to stop duplicate refunds"],
                   check=True, capture_output=True,
                   env={**_env("2026-03-12T10:00:00+05:30"), "GIT_AUTHOR_DATE": "2026-03-12T10:00:00+05:30",
                        "GIT_AUTHOR_NAME": "Ravi", "GIT_AUTHOR_EMAIL": "ravi@example.com"})
    commit(root, {"docs/adr/0002-sqlite-store.md": ADR}, "Record the storage decision (#4130)",
           date="2026-03-20T10:00:00+05:30")
    commit(root, {"config.py": 'API_TOKEN = "abcd1234efgh5678"\n'},
           "Add config\n\nTemporary key ghp_abcdefghijklmnopqrstuvwxyz0123456789 for testing, to avoid a "
           "round trip to the vault.", date="2026-04-01T10:00:00+05:30")
    commit(root, {"version.txt": "1.2.0\n"}, "Bump version to 1.2.0", date="2026-04-02T10:00:00+05:30")
    return root


@pytest.fixture
def store(repo: Path) -> Store:
    s = Store(repo / ".proxy" / "proxy.db")
    yield s
    s.close()


def write_session(home: Path, repo: Path, name: str = "abc12345-session") -> Path:
    from proxy_why.ingest.sessions import encode_path
    d = home / "projects" / encode_path(repo.resolve())
    d.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "user", "timestamp": "2026-03-11T04:00:00.000Z",
         "message": {"role": "user", "content": "make the callback retry logic simpler"}},
        {"type": "assistant", "timestamp": "2026-03-11T04:01:00.000Z", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "private"},
            {"type": "text", "text": "I kept a fixed 40s gap instead of exponential backoff because the gateway "
                                     "rate-limits bursts. Changing it would break refunds."},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(repo / "payments/callback.py"),
                                                            "old_string": "5", "new_string": "3"}},
        ]}},
        {"type": "user", "timestamp": "2026-03-11T04:02:00.000Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "timestamp": "2026-03-11T04:03:00.000Z",
         "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
    ]
    path = d / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    return path
