import asyncio
import json

from proxy_why import cli, inbox, mcp_server
from proxy_why.sync import open_store


def test_cli_init_why_ask_review(repo, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert cli.main(["--repo", str(repo), "init"]) == 0
    out = capsys.readouterr().out
    assert "decision cards written" in out
    assert ".proxy/" in (repo / ".git" / "info" / "exclude").read_text()

    assert cli.main(["--repo", str(repo), "why", "payments/callback.py:2"]) == 0
    out = capsys.readouterr().out
    assert "PR #4127" in out and "proxy review approve" in out

    assert cli.main(["--repo", str(repo), "ask", "why only 3 callback retries", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pending"

    assert cli.main(["--repo", str(repo), "review", "edit", str(data["answer_id"]), "Keep it at 3."]) == 0
    assert cli.main(["--repo", str(repo), "ask", "why only 3 callback retries"]) == 0
    assert capsys.readouterr().out.count("Keep it at 3.") == 1

    assert cli.main(["--repo", str(repo), "status"]) == 0
    assert "Decision cards:" in capsys.readouterr().out


def test_cli_rejects_bad_target(repo, capsys):
    assert cli.main(["--repo", str(repo), "why", "payments/callback.py"]) == 2


def test_mcp_server_exposes_tools(repo):
    cli.main(["--repo", str(repo), "sync", "--no-github"])
    server = mcp_server.build_server(repo)
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert tools == {"why", "ask", "decisions"}
    result = asyncio.run(server.call_tool("why", {"path": "payments/callback.py", "line": 2}))
    text = json.dumps(result, default=str)
    assert "PR #4127" in text and "Sources:" in text


def test_inbox_renders_pending_answers(repo):
    cli.main(["--repo", str(repo), "sync", "--no-github"])
    cli.main(["--repo", str(repo), "ask", "why only 3 callback retries"])
    page = inbox.render(repo)
    assert "Proxy review inbox" in page and "why only 3 callback retries" in page
    store = open_store(repo)
    try:
        store.review(store.answers()[0]["id"], "approved")
    finally:
        store.close()
    assert "Nothing to review." in inbox.render(repo)


def test_inbox_approve_over_http(repo):
    import threading
    import urllib.parse
    import urllib.request
    from http.server import ThreadingHTTPServer

    cli.main(["--repo", str(repo), "sync", "--no-github"])
    cli.main(["--repo", str(repo), "ask", "why only 3 callback retries"])
    store = open_store(repo)
    answer_id = store.answers()[0]["id"]
    store.close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), inbox.make_handler(repo))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert "why only 3 callback retries" in urllib.request.urlopen(url).read().decode()
        data = urllib.parse.urlencode({"id": answer_id, "action": "approve", "text": "Edited in the inbox."}).encode()
        urllib.request.urlopen(urllib.request.Request(f"{url}/review", data=data))
    finally:
        server.shutdown()
        server.server_close()
    store = open_store(repo)
    try:
        assert store.answer(answer_id)["final_answer"] == "Edited in the inbox."
    finally:
        store.close()
