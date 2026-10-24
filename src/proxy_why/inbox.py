"""A small local web page for reviewing Proxy's answers: approve, edit or reject."""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from .sync import open_store

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Proxy review inbox</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:0 auto;padding:24px 16px;color:#1b2430;background:#f6f7f9}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#5b6573;margin:0 0 20px}}
.item{{background:#fff;border:1px solid #dde1e7;border-radius:8px;padding:16px;margin:0 0 16px}}
.q{{font-weight:600;margin:0 0 8px}} .meta{{font-size:12px;color:#5b6573;margin-bottom:8px}}
textarea{{width:100%;box-sizing:border-box;min-height:150px;font:13px/1.45 ui-monospace,monospace;padding:8px}}
.cites{{font-size:13px;margin:8px 0}} .row{{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}}
button{{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid #c5ccd6;background:#fff;cursor:pointer}}
button.ok{{background:#18794e;color:#fff;border-color:#18794e}} button.no{{color:#b42318}}
.empty{{color:#5b6573}}
</style></head><body>
<h1>Proxy review inbox</h1><p class="sub">{count} answers waiting. Approved answers are reused for the same question; edits replace the draft.</p>
{items}</body></html>"""

ITEM = """<form class="item" method="post" action="/review">
<input type="hidden" name="id" value="{id}">
<p class="q">{question}</p><div class="meta">#{id} · asked via {asked_by} · {created}</div>
<textarea name="text" aria-label="Answer for #{id}">{answer}</textarea>
<div class="cites">{cites}</div>
<div class="row"><button class="ok" name="action" value="approve">Approve</button>
<button class="no" name="action" value="reject">Reject</button></div></form>"""


def render(root: Path) -> str:
    store = open_store(root)
    try:
        rows = store.answers("pending")
    finally:
        store.close()
    items = []
    for r in rows:
        cites = json.loads(r["citations"])
        links = " · ".join(
            f'<a href="{html.escape(c["url"])}" target="_blank" rel="noopener">{html.escape(c["label"])}</a>'
            if c.get("url") else html.escape(c["label"]) for c in cites)
        items.append(ITEM.format(id=r["id"], question=html.escape(r["question"]), asked_by=html.escape(r["asked_by"]),
                                 created=html.escape(r["created"]), answer=html.escape(r["answer"]),
                                 cites=links or "No sources"))
    return PAGE.format(count=len(rows), items="\n".join(items) or '<p class="empty">Nothing to review.</p>')


def make_handler(root: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = render(root).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode())
            answer_id = int(form.get("id", ["0"])[0])
            action = form.get("action", [""])[0]
            text = form.get("text", [""])[0].replace("\r\n", "\n")
            store = open_store(root)
            try:
                if action == "approve":
                    store.review(answer_id, "approved", text)
                elif action == "reject":
                    store.review(answer_id, "rejected")
            finally:
                store.close()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Handler


def serve(root: Path, port: int = 7377) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(root))
    print(f"Review inbox at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
