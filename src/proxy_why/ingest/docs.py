"""Read markdown docs and ADRs, split into sections."""

from __future__ import annotations

import re
from pathlib import Path

from ..repo import Remote, git
from ..store import Source

_DOC_NAMES = re.compile(r"(?i)(readme|contributing|architecture|design|decision|adr|rfc|changelog|history)")
_ADR_PATH = re.compile(r"(?i)(^|/)(adr|adrs|decisions|architecture[-_ ]decisions?)(/|$)|(^|/)\d{3,4}[-_].*\.md$")
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")

MAX_DOC_BYTES = 400_000


def is_adr(path: str) -> bool:
    return bool(_ADR_PATH.search(path))


def candidate_docs(root: Path) -> list[str]:
    files = git(root, "ls-files", "*.md", "*.markdown", "*.rst", check=False).splitlines()
    picked = []
    for f in files:
        if "node_modules/" in f or f.startswith("vendor/"):
            continue
        if is_adr(f) or _DOC_NAMES.search(Path(f).name) or f.startswith(("docs/", "doc/")):
            picked.append(f)
    return picked


def read_docs(root: Path, remote: Remote | None = None, limit: int = 500) -> list[Source]:
    sources: list[Source] = []
    for path in candidate_docs(root)[:limit]:
        full = root / path
        try:
            if full.stat().st_size > MAX_DOC_BYTES:
                continue
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        url = remote.blob_url(path) if remote else ""
        if is_adr(path):
            title = _first_heading(text) or Path(path).stem
            sources.append(Source(kind="doc", ref=path, title=title, body=text, url=url,
                                  files=[path], meta={"adr": True, "path": path}))
            continue
        for heading, body in split_sections(text):
            if len(body.strip()) < 80:
                continue
            sources.append(Source(kind="doc", ref=f"{path}#{_slug(heading)}", title=heading or path,
                                  body=body.strip(), url=url, files=[path],
                                  meta={"adr": False, "path": path}))
    return sources


def split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading, buf = "", []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            if buf:
                sections.append((heading, "\n".join(buf)))
            heading, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    if buf:
        sections.append((heading, "\n".join(buf)))
    return sections


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            return m.group(2).strip()
    return ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "top"
