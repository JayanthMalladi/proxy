"""Read local Claude Code session logs for a repository (opt-in).

Claude Code keeps one JSONL file per session under
~/.claude/projects/<cwd with non-alphanumerics replaced by '-'>/<session id>.jsonl.
Each line is an event; user and assistant events carry a `message` with content.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..store import Source

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAX_BODY = 60_000


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def encode_path(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def session_files(root: Path, home: Path | None = None) -> list[Path]:
    projects = (home or claude_home()) / "projects"
    if not projects.is_dir():
        return []
    key = encode_path(root.resolve())
    files: list[Path] = []
    for d in projects.iterdir():
        if d.is_dir() and (d.name == key or d.name.startswith(key + "-")):
            files.extend(sorted(d.glob("*.jsonl")))
    return files


def read_sessions(root: Path, home: Path | None = None) -> list[Source]:
    out = []
    for f in session_files(root, home):
        src = parse_session(f, root)
        if src:
            out.append(src)
    return out


def parse_session(path: Path, root: Path) -> Source | None:
    prompts: list[str] = []
    lines: list[str] = []
    files: list[str] = []
    last_ts = ""
    root = root.resolve()
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for raw in handle:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind not in ("user", "assistant"):
                continue
            last_ts = event.get("timestamp") or last_ts
            content = (event.get("message") or {}).get("content")
            if kind == "user":
                text = _user_text(content)
                if text:
                    prompts.append(text)
                    lines.append(f"Developer: {text}")
            else:
                for block in content if isinstance(content, list) else []:
                    if block.get("type") == "text" and block.get("text", "").strip():
                        lines.append(f"Agent: {block['text'].strip()}")
                    elif block.get("type") == "tool_use" and block.get("name") in EDIT_TOOLS:
                        rel = _relative((block.get("input") or {}).get("file_path") or
                                        (block.get("input") or {}).get("notebook_path"), root)
                        if rel and rel not in files:
                            files.append(rel)
    if not prompts:
        return None
    body = "\n\n".join(lines)
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n[truncated]"
    return Source(
        kind="session",
        ref=path.stem,
        title=_one_line(prompts[0]),
        body=body,
        author="you (Claude Code session)",
        date=last_ts[:19] + "+00:00" if last_ts.endswith("Z") else last_ts,
        files=files,
        meta={"session_file": str(path), "prompts": len(prompts)},
    )


def _user_text(content) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    else:
        return ""
    text = text.strip()
    # Skip harness-injected messages (command output, reminders, context blocks).
    if not text or text.startswith("<") or text.startswith("Caveat:"):
        return ""
    return text


def _relative(file_path: str | None, root: Path) -> str | None:
    if not file_path:
        return None
    p = Path(file_path)
    try:
        return str(p.resolve().relative_to(root)) if p.is_absolute() else str(p)
    except ValueError:
        return None


def _one_line(text: str, n: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"
