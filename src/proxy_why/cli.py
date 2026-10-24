"""Command line interface: `proxy init`, `proxy why file:line`, `proxy ask "..."` and friends."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, answer
from .repo import GitError, find_root, git
from .sync import STORE_DIR, open_store, store_path, sync


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        root = find_root(args.repo)
    except GitError as e:
        print(f"proxy: {e}", file=sys.stderr)
        return 2
    return args.func(root, args) or 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="proxy", description="Ask a codebase's memory why the code is the way it is.")
    p.add_argument("--version", action="version", version=f"proxy {__version__}")
    p.add_argument("--repo", default=".", help="path inside the git repository (default: current directory)")
    sub = p.add_subparsers(metavar="command")

    s = sub.add_parser("init", help="set up Proxy in this repository and read its history")
    s.add_argument("--sessions", action="store_true", help="also read your local Claude Code sessions for this repo")
    _github_flags(s)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("sync", help="read new commits, PRs, docs and sessions")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--sessions", dest="sessions", action="store_true", default=None, help="turn session reading on")
    g.add_argument("--no-sessions", dest="sessions", action="store_false", help="turn session reading off")
    _github_flags(s)
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("why", help="explain why a line is the way it is, e.g. proxy why src/app.py:42")
    s.add_argument("target", help="FILE:LINE")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_why)

    s = sub.add_parser("ask", help='ask a why question, e.g. proxy ask "why do we retry uploads?"')
    s.add_argument("question")
    s.add_argument("--path", help="prefer decisions about this file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("decisions", help="list recorded decisions about a file")
    s.add_argument("path")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_decisions)

    s = sub.add_parser("cards", help="list recent decision cards")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_cards)

    s = sub.add_parser("review", help="review answers: list, approve, edit or reject")
    s.add_argument("action", nargs="?", choices=["list", "approve", "edit", "reject"], default="list")
    s.add_argument("id", nargs="?", type=int)
    s.add_argument("text", nargs="?", help="replacement answer text (for edit)")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("inbox", help="open the review inbox web page on localhost")
    s.add_argument("--port", type=int, default=7377)
    s.set_defaults(func=cmd_inbox)

    s = sub.add_parser("mcp", help="run the MCP server over stdio (for Claude Code, Cursor, ...)")
    s.set_defaults(func=cmd_mcp)

    s = sub.add_parser("status", help="show what Proxy has read")
    s.set_defaults(func=cmd_status)
    return p


def _github_flags(s: argparse.ArgumentParser) -> None:
    s.add_argument("--github", dest="github", action="store_true", default=None,
                   help="fetch PR descriptions and reviews (needs GITHUB_TOKEN for private repos)")
    s.add_argument("--no-github", dest="github", action="store_false", help="skip GitHub")
    s.add_argument("--max-prs", type=int, default=200, help="most recent merged PRs to read (default 200)")


# commands -------------------------------------------------------------------------

def cmd_init(root: Path, args) -> int:
    _exclude_store(root)
    print(f"Proxy store: {store_path(root)}")
    return _run_sync(root, github=args.github, sessions=True if args.sessions else None, max_prs=args.max_prs)


def cmd_sync(root: Path, args) -> int:
    return _run_sync(root, github=args.github, sessions=args.sessions, max_prs=args.max_prs)


def _run_sync(root: Path, github, sessions, max_prs) -> int:
    store = open_store(root)
    try:
        report = sync(root, store, github_prs=github, include_sessions=sessions, max_prs=max_prs,
                      log=lambda m: print(f"  {m}"))
    finally:
        store.close()
    print(report.summary())
    for note in report.notes:
        print(f"Note: {note}")
    return 0


def cmd_why(root: Path, args) -> int:
    path, _, line = args.target.rpartition(":")
    if not path or not line.isdigit():
        print("proxy: use FILE:LINE, for example src/app.py:42", file=sys.stderr)
        return 2
    store = open_store(root)
    try:
        ans = answer.why(root, store, path, int(line))
    finally:
        store.close()
    _print_answer(ans, args.json)
    return 0


def cmd_ask(root: Path, args) -> int:
    store = open_store(root)
    try:
        ans = answer.ask(store, args.question, path=args.path)
    finally:
        store.close()
    _print_answer(ans, args.json)
    return 0


def cmd_decisions(root: Path, args) -> int:
    store = open_store(root)
    try:
        cards = answer.decisions(store, args.path, limit=args.limit)
    finally:
        store.close()
    if not cards:
        print(f"No recorded decisions about {args.path}.")
    for c in cards:
        _print_card(c)
    return 0


def cmd_cards(root: Path, args) -> int:
    store = open_store(root)
    try:
        rows = store.db.execute("SELECT id FROM cards ORDER BY date DESC LIMIT ?", (args.limit,)).fetchall()
        cards = [store.card(r["id"]) for r in rows]
    finally:
        store.close()
    for c in cards:
        _print_card(c)
    return 0


def cmd_review(root: Path, args) -> int:
    store = open_store(root)
    try:
        if args.action == "list":
            rows = store.answers("pending")
            if not rows:
                print("Nothing to review.")
            for r in rows:
                print(f"#{r['id']}  [{r['asked_by']}]  {r['question']}")
                print("    " + r["answer"].replace("\n", "\n    "))
                print()
            return 0
        if args.id is None:
            print("proxy: give the answer id, for example: proxy review approve 3", file=sys.stderr)
            return 2
        if args.action == "edit" and not args.text:
            print('proxy: give the new text, for example: proxy review edit 3 "Because ..."', file=sys.stderr)
            return 2
        status = "rejected" if args.action == "reject" else "approved"
        ok = store.review(args.id, status, args.text if args.action == "edit" else None)
    finally:
        store.close()
    if not ok:
        print(f"proxy: no answer #{args.id}", file=sys.stderr)
        return 1
    print(f"Answer #{args.id} {status}" + (" with your edit." if args.action == "edit" else "."))
    return 0


def cmd_inbox(root: Path, args) -> int:
    from .inbox import serve
    serve(root, args.port)
    return 0


def cmd_mcp(root: Path, args) -> int:
    from .mcp_server import run
    run(root)
    return 0


def cmd_status(root: Path, args) -> int:
    if not store_path(root).exists():
        print("Proxy is not set up here yet. Run `proxy init`.")
        return 1
    store = open_store(root)
    try:
        kinds = dict(store.db.execute("SELECT kind, COUNT(*) FROM sources GROUP BY kind").fetchall())
        print(f"Repository: {root}")
        print(f"Sources: {kinds.get('commit', 0)} commits, {kinds.get('pr', 0)} PRs, "
              f"{kinds.get('doc', 0)} doc sections, {kinds.get('session', 0)} sessions")
        print(f"Decision cards: {store.count('cards')}")
        print(f"Answers waiting for review: {len(store.answers('pending'))}")
        print(f"Session reading: {store.get_meta('sessions', 'off')}")
        print(f"Last synced commit: {(store.get_meta('last_commit') or 'none')[:8]}")
    finally:
        store.close()
    return 0


# output ------------------------------------------------------------------------------

def _print_answer(ans: answer.Answer, as_json: bool) -> None:
    if as_json:
        print(json.dumps(ans.to_dict(), indent=2))
        return
    print(ans.text)
    if ans.citations:
        print("\nSources:")
        for c in ans.citations:
            print(f"  - {c['label']}" + (f"  {c['url']}" if c.get("url") else ""))
    if ans.answer_id and ans.status == "pending":
        print(f"\n(answer #{ans.answer_id}: `proxy review approve {ans.answer_id}` to keep it)")


def _print_card(c) -> None:
    print(f"[{c.id}] {c.date[:10]}  {c.title}")
    if c.why:
        print(f"     why: {c.why[:300]}")
    if c.alternatives:
        print(f"     alternatives: {c.alternatives[:200]}")
    print(f"     source: {', '.join(e['label'] for e in c.evidence)}  confidence {c.confidence:.2f}")


def _exclude_store(root: Path) -> None:
    """Keep the local store out of git without touching the tracked .gitignore."""
    git_dir = Path(git(root, "rev-parse", "--git-dir").strip())
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    if f"{STORE_DIR}/" not in existing.splitlines():
        exclude.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n") + f"{STORE_DIR}/\n")


if __name__ == "__main__":
    sys.exit(main())
