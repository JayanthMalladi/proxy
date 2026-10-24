from proxy_why.ingest import docs, git_history, github, sessions
from proxy_why.repo import origin, parse_remote

from .conftest import write_session


def test_parse_remote_variants():
    for url in ("git@github.com:acme/payments.git", "https://github.com/acme/payments",
                "https://github.com/acme/payments.git", "ssh://git@github.com/acme/payments.git"):
        r = parse_remote(url)
        assert (r.host, r.owner, r.name) == ("github.com", "acme", "payments"), url
    assert parse_remote("https://github.com/acme/payments").pr_url(7) == "https://github.com/acme/payments/pull/7"


def test_read_commits_parses_messages_files_and_prs(repo):
    commits = git_history.read_commits(repo, remote=origin(repo))
    by_title = {c.title: c for c in commits}
    fix = by_title["Cut callback retries from 5 to 3"]
    assert fix.files == ["payments/callback.py"]
    assert "INC-311" in fix.body
    assert fix.author == "Ravi"
    assert fix.url.startswith("https://github.com/acme/payments/commit/")
    assert by_title["Record the storage decision (#4130)"].meta["pr"] == 4130
    merge = next(c for c in commits if c.meta.get("merge"))
    assert merge.meta["pr"] == 4127


def test_branch_commits_are_linked_to_their_merge_pr(repo):
    commits = git_history.read_commits(repo)
    mapping = git_history.link_merged_commits(repo, commits)
    fix = next(c for c in commits if c.title.startswith("Cut callback"))
    assert mapping[fix.ref] == 4127
    merge = next(c for c in commits if c.meta.get("merge"))
    pr = git_history.pr_from_merge(merge, origin(repo))
    assert pr.ref == "4127" and pr.title == "Cut UPI callback retries to stop duplicate refunds"


def test_incremental_read_only_returns_new_commits(repo):
    all_commits = git_history.read_commits(repo)
    newest_two = git_history.read_commits(repo, since=all_commits[2].ref)
    assert [c.ref for c in newest_two] == [c.ref for c in all_commits[:2]]


def test_docs_split_sections_and_detect_adrs(repo):
    found = docs.read_docs(repo, origin(repo))
    adr = next(d for d in found if d.meta.get("adr"))
    assert adr.title == "2. Use SQLite for the local store"
    assert adr.files == ["docs/adr/0002-sqlite-store.md"]
    assert docs.split_sections("# A\ntext\n```\n# not a heading\n```\n## B\nmore") == [
        ("A", "text\n```\n# not a heading\n```"), ("B", "more")]


def test_sessions_are_parsed_for_this_repo_only(repo, tmp_path):
    home = tmp_path / "claude-home"
    write_session(home, repo)
    write_session(home, tmp_path / "other-repo", name="other")
    found = sessions.read_sessions(repo, home)
    assert len(found) == 1
    s = found[0]
    assert s.title == "make the callback retry logic simpler"
    assert s.files == ["payments/callback.py"]
    assert "exponential backoff" in s.body
    assert "private" not in s.body  # thinking blocks are not stored
    assert "/clear" not in s.body  # harness messages are skipped


def fake_github(prs, extra=None):
    extra = extra or {}

    def fetch(url):
        if "/pulls?" in url:
            return prs, None
        for key, value in extra.items():
            if key in url:
                return value, None
        return [], None
    return fetch


def test_github_pull_requests_with_discussion():
    prs = [
        {"number": 4127, "title": "Cut UPI callback retries", "body": "Stops duplicate refunds.",
         "merged_at": "2026-03-12T04:30:00Z", "updated_at": "2026-03-12T04:30:00Z",
         "html_url": "https://github.com/acme/payments/pull/4127", "user": {"login": "ravi"},
         "merge_commit_sha": "abc"},
        {"number": 4128, "title": "Closed without merge", "body": "", "merged_at": None,
         "updated_at": "2026-03-12T04:00:00Z", "user": {"login": "x"}},
    ]
    extra = {
        "/pulls/4127/comments": [{"body": "Keep the 40s gap, the gateway rate-limits bursts.", "path": "payments/callback.py",
                                  "user": {"login": "maya", "type": "User"}}],
        "/issues/4127/comments": [{"body": "Deploy preview ready", "user": {"login": "netlify[bot]", "type": "Bot"}}],
    }
    out = github.read_pull_requests(parse_remote("https://github.com/acme/payments"), fake_github(prs, extra))
    assert [p.ref for p in out] == ["4127"]
    assert "maya on payments/callback.py: Keep the 40s gap" in out[0].body
    assert "netlify" not in out[0].body
