from proxy_why import answer
from proxy_why.sync import sync

from .conftest import write_session


def synced(repo, store, **kw):
    return sync(repo, store, github_prs=False, **kw)


def test_sync_reads_history_and_redacts_secrets(repo, store):
    report = synced(repo, store)
    assert report.commits == 6
    assert report.prs_from_merges == 1
    assert report.redacted >= 1
    leaked = store.db.execute("SELECT COUNT(*) FROM sources WHERE body LIKE '%ghp_abcdef%'").fetchone()[0]
    assert leaked == 0


def test_sync_is_incremental(repo, store):
    synced(repo, store)
    again = synced(repo, store)
    assert again.commits == 0


def test_why_traces_line_to_pr_and_reason(repo, store):
    synced(repo, store)
    ans = answer.why(repo, store, "payments/callback.py", 2)
    assert ans.status == "pending"
    assert "RETRY_MAX = 3" in ans.text
    assert "PR #4127" in ans.text
    assert "duplicate refunds" in ans.text
    assert any(c["url"] == "https://github.com/acme/payments/pull/4127" for c in ans.citations)


def test_why_handles_bad_lines_and_unsynced_commits(repo, store):
    assert answer.why(repo, store, "payments/callback.py", 999).status == "no_evidence"
    ans = answer.why(repo, store, "payments/callback.py", 2)  # store not synced yet
    assert "proxy sync" in ans.text


def test_ask_finds_the_decision_with_citations(repo, store):
    synced(repo, store)
    ans = answer.ask(store, "why do we only retry the callback 3 times?")
    assert ans.status == "pending"
    assert "duplicate refunds" in ans.text
    assert ans.citations


def test_ask_abstains_without_evidence(repo, store):
    synced(repo, store)
    ans = answer.ask(store, "why does the kubernetes autoscaler use custom metrics?")
    assert ans.status == "no_evidence"
    assert ans.text == answer.NOT_ENOUGH


def test_ask_finds_adr(repo, store):
    synced(repo, store)
    ans = answer.ask(store, "why sqlite instead of postgres for the store?")
    assert "single SQLite file" in ans.text


def test_approved_answers_are_reused_with_edits(repo, store):
    synced(repo, store)
    first = answer.ask(store, "Why only 3 callback retries?")
    store.review(first.answer_id, "approved", "Because a 4th retry duplicated refunds (INC-311). Safe to raise "
                                              "once idempotency keys ship.")
    again = answer.ask(store, "why only 3 callback retries")
    assert again.status == "approved"
    assert again.text.startswith("Because a 4th retry duplicated refunds")
    assert "idempotency keys" in again.text

    line = answer.why(repo, store, "payments/callback.py", 2)
    store.review(line.answer_id, "approved", "Ravi: keep at 3.")
    assert answer.why(repo, store, "payments/callback.py", 2).text.startswith("Ravi: keep at 3.")


def test_rejected_answers_are_not_reused(repo, store):
    synced(repo, store)
    first = answer.ask(store, "why only 3 callback retries")
    store.review(first.answer_id, "rejected")
    assert answer.ask(store, "why only 3 callback retries").status == "pending"


def test_sessions_are_opt_in(repo, store, tmp_path):
    home = tmp_path / "claude-home"
    write_session(home, repo)
    assert synced(repo, store, claude_home=home).sessions == 0
    report = synced(repo, store, include_sessions=True, claude_home=home)
    assert report.sessions == 1
    cards = answer.decisions(store, "payments/callback.py")
    assert any(c.evidence[0]["label"].startswith("Claude Code session") for c in cards)
    # the choice is remembered for later syncs
    assert synced(repo, store, claude_home=home).sessions == 1


def test_github_prs_replace_merge_stand_ins(repo, store):
    prs = [{"number": 4127, "title": "Cut UPI callback retries", "body": "We cut retries to 3 to avoid duplicate refunds.",
            "merged_at": "2026-03-12T04:30:00Z", "updated_at": "2026-03-12T04:30:00Z",
            "html_url": "https://github.com/acme/payments/pull/4127", "user": {"login": "ravi"}}]

    def fetch(url):
        return (prs, None) if "/pulls?" in url else ([], None)
    report = sync(repo, store, github_prs=True, fetch=fetch)
    assert report.prs == 1
    pr = store.get_source("pr", "4127")
    assert not pr.meta.get("from_merge")
    assert "payments/callback.py" in pr.files
    # a later sync without GitHub must not overwrite the richer record
    sync(repo, store, github_prs=False)
    assert store.get_source("pr", "4127").title == "Cut UPI callback retries"


def test_decisions_lists_cards_for_a_file(repo, store):
    synced(repo, store)
    titles = [c.title for c in answer.decisions(store, "payments/callback.py")]
    assert "Cut callback retries from 5 to 3" in titles
