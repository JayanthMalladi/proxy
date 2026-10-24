from proxy_why.extract import extract, sentences
from proxy_why.store import Source


def test_commit_with_reason_becomes_a_card():
    src = Source(kind="commit", ref="a" * 40, title="Cut callback retries from 5 to 3 (#41)",
                 body="A fourth retry caused duplicate refunds because callbacks arrive late. "
                      "We tried exponential backoff instead of a fixed gap.",
                 files=["payments/callback.py"], url="https://x/commit/aaa", meta={"pr": 41})
    card = extract(src, pr_url="https://x/pull/41", use_llm=False)
    assert card.title == "Cut callback retries from 5 to 3"
    assert "duplicate refunds" in card.why
    assert "exponential backoff" in card.alternatives
    assert card.files == ["payments/callback.py"]
    assert [e["label"] for e in card.evidence] == ["commit aaaaaaaa", "PR #41"]


def test_trivial_commits_and_bots_are_skipped():
    for title, author in (("Bump version to 1.2.0", "ravi"), ("Merge branch 'main' into stable", "ravi"),
                          ("Update deps", "dependabot[bot]")):
        assert extract(Source(kind="commit", ref="b" * 40, title=title, author=author), use_llm=False) is None


def test_bare_commit_without_reason_is_skipped():
    assert extract(Source(kind="commit", ref="c" * 40, title="Add help parameter"), use_llm=False) is None


def test_adr_sections_map_to_card_fields():
    from .conftest import ADR
    src = Source(kind="doc", ref="docs/adr/0002.md", title="2. Use SQLite", body=ADR, meta={"adr": True})
    card = extract(src, use_llm=False)
    assert card.decision.startswith("We will use a single SQLite file")
    assert "work offline" in card.why
    assert "Postgres was considered" in card.alternatives
    assert card.confidence >= 0.8


def test_session_card_uses_agent_reasoning():
    body = ("Developer: make the retry simpler\n\nAgent: I kept a fixed gap instead of backoff because the "
            "gateway rate-limits bursts. Done.")
    src = Source(kind="session", ref="s1", title="make the retry simpler", body=body, files=["a.py"])
    card = extract(src, use_llm=False)
    assert "rate-limits bursts" in card.why
    assert card.evidence[0]["label"].startswith("Claude Code session")


def test_sentences_drop_templates_and_code():
    text = "## Checklist\n- [ ] tests\nFixes #12\n```py\nx = 1\n```\nThis prevents a crash on empty input."
    assert sentences(text) == ["This prevents a crash on empty input."]


def test_llm_result_is_used_when_available(monkeypatch):
    from proxy_why import llm
    monkeypatch.setattr(llm, "extract_decision", lambda s: {
        "is_decision": True, "title": "T", "decision": "Use 3 retries", "why": "Duplicate refunds",
        "alternatives": "", "confidence": 0.8})
    monkeypatch.setattr(llm, "model_name", lambda: "tiny")
    card = extract(Source(kind="commit", ref="d" * 40, title="x"), use_llm=True)
    assert card.decision == "Use 3 retries" and card.method == "llm:tiny"


def test_llm_failure_falls_back_to_rules(monkeypatch):
    from proxy_why import llm
    monkeypatch.setattr(llm, "extract_decision", lambda s: None)
    src = Source(kind="commit", ref="e" * 40, title="Pin pool size", body="This avoids exhausting connections.")
    assert extract(src, use_llm=True).method == "rules"


def test_llm_json_parsing():
    from proxy_why.llm import parse_json
    assert parse_json('Sure!\n```json\n{"decision": "x"}\n```') == {"decision": "x"}
    assert parse_json("no json here") is None
