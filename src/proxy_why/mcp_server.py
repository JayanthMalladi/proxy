"""MCP server so coding agents (Claude Code, Cursor, ...) can ask Proxy before editing code."""

from __future__ import annotations

from pathlib import Path

from . import answer
from .sync import open_store

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

INSTRUCTIONS = (
    "Proxy knows why this repository's code is the way it is, from commits, pull requests, docs and "
    "the developers' coding sessions. Before changing code whose purpose is unclear (odd constants, "
    "retries, workarounds, unusual structure), call `why` on the line or `ask` about the behaviour. "
    "Answers cite their sources. Treat answers marked unreviewed as leads to verify, not facts."
)


def format_answer(ans: answer.Answer) -> str:
    lines = [ans.text]
    if ans.citations:
        lines.append("Sources:")
        lines += [f"- {c['label']}: {c['url']}" if c.get("url") else f"- {c['label']}" for c in ans.citations]
    return "\n".join(lines)


def build_server(root: Path) -> _Server:
    server = _Server("proxy", instructions=INSTRUCTIONS)

    @server.tool()
    def why(path: str, line: int) -> str:
        """Explain why a specific line of code is the way it is: the commit, pull request and reasons
        behind its last change, plus other recorded decisions about the same file.

        path: file path relative to the repository root. line: 1-based line number."""
        store = open_store(root)
        try:
            return format_answer(answer.why(root, store, path, line, asked_by="mcp"))
        finally:
            store.close()

    @server.tool()
    def ask(question: str, path: str | None = None) -> str:
        """Ask a free-form "why" question about this codebase, for example "why do we retry the
        upload three times?". Optionally pass a file path to prefer decisions about that file.
        Returns "Not enough evidence" when the history does not answer it."""
        store = open_store(root)
        try:
            return format_answer(answer.ask(store, question, path=path, asked_by="mcp"))
        finally:
            store.close()

    @server.tool()
    def decisions(path: str, limit: int = 10) -> str:
        """List recorded decisions about a file, newest first, with their reasons and sources."""
        store = open_store(root)
        try:
            cards = answer.decisions(store, path, limit=limit)
        finally:
            store.close()
        if not cards:
            return f"No recorded decisions about {path}."
        blocks = []
        for c in cards:
            src = ", ".join(f"{e['label']} {e['url']}".strip() for e in c.evidence)
            body = f"- {c.date[:10]} {c.title}"
            if c.why:
                body += f"\n  Why: {c.why}"
            if c.alternatives:
                body += f"\n  Alternatives: {c.alternatives}"
            body += f"\n  Source: {src}"
            blocks.append(body)
        return "\n".join(blocks)

    return server


def run(root: Path) -> None:
    build_server(root).run("stdio")
