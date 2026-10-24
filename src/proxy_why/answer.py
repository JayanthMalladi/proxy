"""Answer "why" questions from decision cards, with citations, and reuse reviewed answers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .repo import blame_line
from .store import Card, Store

NOT_ENOUGH = "Not enough evidence in this repository's history to answer that. Ask the code owner."

STOPWORDS = set("""
a an and are as at be been but by can could did do does doing done for from had has have how i if in
into is it its me my of on or our should so than that the their them then there these they this those
to was we were what when where which who whom why will with would you your about any did does
code file line here also just like get got make made still really
""".split())

MIN_COVERAGE = 0.5


@dataclass
class Answer:
    text: str
    citations: list[dict] = field(default_factory=list)
    status: str = "pending"          # pending | approved | no_evidence
    answer_id: int | None = None
    cards: list[Card] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "answer": self.text,
            "status": self.status,
            "answer_id": self.answer_id,
            "citations": self.citations,
            "cards": [c.to_dict() for c in self.cards],
        }


# why ---------------------------------------------------------------------------

def why(root: Path, store: Store, path: str, line: int, asked_by: str = "cli") -> Answer:
    rel = _relative(root, path)
    info = blame_line(root, rel, line)
    if info is None:
        return Answer(f"Could not read line {line} of {rel}. Check the path and line number.", status="no_evidence")
    if info.get("uncommitted"):
        return Answer(f"Line {line} of {rel} has uncommitted changes, so it has no history yet.", status="no_evidence")

    sha = info["sha"]
    target = f"{rel}@{sha}"
    question = f"Why is line {line} of {rel} the way it is?"
    reviewed = store.approved_answer("why", target)
    commit = store.get_source("commit", sha)
    if commit is None:
        return Answer(f"Line {line} of {rel} was last changed in commit {sha[:8]}, which is not in "
                      "Proxy's store yet. Run `proxy sync` and ask again.", status="no_evidence")

    cards: list[Card] = []
    pr_number = commit.meta.get("pr")
    pr_source = store.get_source("pr", str(pr_number)) if pr_number else None
    pr_cards = store.cards_for_source(pr_source.id) if pr_source else []
    commit_cards = store.cards_for_source(commit.id)
    cards.extend(pr_cards + commit_cards)

    code = info.get("code", "").strip()
    head = (f"Line {line} of {rel}" + (f" (`{_clip(code, 80)}`)" if code else "") +
            f" was last changed in commit {sha[:8]} by {commit.author} on {commit.date[:10]}: \"{commit.title}\".")
    parts = [head]
    candidates = pr_cards + commit_cards
    main = next((c for c in candidates if c.why), candidates[0] if candidates else None)
    if pr_number and pr_source:
        parts.append(f"It came from PR #{pr_number}, \"{pr_source.title}\".")
    elif pr_number:
        parts.append(f"It came from PR #{pr_number} (PR details not synced; set GITHUB_TOKEN and run `proxy sync`).")
    if main and _card_body(main, skip_title=True):
        parts.append(_card_body(main, skip_title=True))
    else:
        parts.append("The commit message does not record a reason.")

    related = [c for c in store.cards_for_path(rel, limit=12) if c.id not in {x.id for x in cards}][:3]
    if related:
        parts.append("Other decisions about this file:\n" + "\n".join(
            f"- {c.title} ({_label(c)}, {c.date[:10]})" for c in related))
    cards.extend(related)

    # The blamed commit and its PR always come first, even when they produced no card.
    first = [{"label": f"commit {sha[:8]}", "url": commit.url, "date": commit.date}]
    if pr_number:
        first.append({"label": f"PR #{pr_number}", "url": pr_source.url if pr_source else
                      (commit.url.split("/commit/")[0] + f"/pull/{pr_number}" if "/commit/" in commit.url else ""),
                      "date": pr_source.date if pr_source else ""})
    citations = _dedupe(first + _citations(cards))
    text = "\n\n".join(p for p in parts if p)
    return _finish(store, "why", target, question, text, citations, cards, reviewed, asked_by)


# ask ---------------------------------------------------------------------------

def ask(store: Store, question: str, path: str | None = None, asked_by: str = "cli") -> Answer:
    target = normalise(question) + (f" @{path}" if path else "")
    reviewed = store.approved_answer("ask", target)
    terms = content_terms(question)
    if not terms:
        return Answer("Ask about a specific behaviour, file or decision.", status="no_evidence")

    query = " OR ".join(f'"{t}"' for t in terms)
    hits = store.search_cards(query, limit=15)
    if path:
        on_path = {c.id for c in store.cards_for_path(path, limit=200)}
        hits.sort(key=lambda h: (h[0].id not in on_path, -h[1]))
    scored = [(c, s, coverage(terms, c)) for c, s in hits]
    good = [h for h in scored if h[2] >= MIN_COVERAGE]
    good.sort(key=lambda h: (-round(h[2], 2), not h[0].why, -h[1]))

    if not good:
        if reviewed:
            return _finish(store, "ask", target, question, "", [], [], reviewed, asked_by)
        ans = Answer(NOT_ENOUGH, status="no_evidence")
        ans.answer_id = store.add_answer("ask", target, question, NOT_ENOUGH, [], asked_by)
        return ans

    best = good[0][0]
    parts = [f"Best match: {best.title} ({_label(best)}, {best.date[:10]}).",
             _card_body(best, skip_title=True) or "No reason was recorded with this change."]
    also, seen_titles = [], {best.title.lower()}
    for c, _, _ in good[1:]:
        if c.title.lower() not in seen_titles:
            seen_titles.add(c.title.lower())
            also.append(c)
        if len(also) == 2:
            break
    if also:
        parts.append("Also relevant:\n" + "\n".join(f"- {c.title} ({_label(c)}, {c.date[:10]})" for c in also))
    cards = [best] + also
    return _finish(store, "ask", target, question, "\n\n".join(parts), _citations(cards), cards, reviewed, asked_by)


def decisions(store: Store, path: str, limit: int = 10) -> list[Card]:
    return store.cards_for_path(path, limit=limit)


# helpers -------------------------------------------------------------------------

def normalise(question: str) -> str:
    return " ".join(re.sub(r"[^\w\s/.\-]", " ", question.lower()).split())


def content_terms(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question)
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw in STOPWORDS or lw in seen:
            continue
        seen.add(lw)
        out.append(lw)
    return out[:12]


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def coverage(terms: list[str], card: Card) -> float:
    text = " ".join([card.title, card.decision, card.why, card.alternatives, " ".join(card.files)]).lower()
    words = {_stem(w) for w in re.findall(r"[a-z0-9_]{3,}", text)}
    if not terms:
        return 0.0
    return sum(_stem(t) in words for t in terms) / len(terms)


def _finish(store: Store, kind: str, target: str, question: str, text: str, citations: list[dict],
            cards: list[Card], reviewed, asked_by: str) -> Answer:
    if reviewed is not None:
        return Answer(text=reviewed["final_answer"] + "\n\n(Reviewed and approved by the code owner.)",
                      citations=json.loads(reviewed["citations"]) or citations, status="approved",
                      answer_id=reviewed["id"], cards=cards)
    answer_id = store.add_answer(kind, target, question, text, citations, asked_by)
    return Answer(text=text + "\n\n(Unreviewed: the owner has not checked this answer yet.)",
                  citations=citations, status="pending", answer_id=answer_id, cards=cards)


def _card_body(card: Card, skip_title: bool = False) -> str:
    lines = []
    if not (skip_title and card.decision.strip() == card.title.strip()):
        lines.append(f"Decision: {card.decision}")
    if card.why:
        lines.append(f"Why: {card.why}")
    if card.alternatives:
        lines.append(f"Alternatives mentioned: {card.alternatives}")
    return "\n".join(lines)


def _label(card: Card) -> str:
    return card.evidence[0]["label"] if card.evidence else "source"


def _citations(cards: list[Card]) -> list[dict]:
    return _dedupe([ev for c in cards for ev in c.evidence])


def _dedupe(evidence: list[dict]) -> list[dict]:
    seen, out = set(), []
    for ev in evidence:
        key = ev.get("url") or ev.get("label")
        if key not in seen:
            seen.add(key)
            out.append(ev)
    return out


def _relative(root: Path, path: str) -> str:
    """Accept a path relative to the current directory, the repo root, or absolute."""
    p = Path(path)
    if not p.is_absolute():
        if not (Path.cwd() / p).exists():
            return str(p)
        p = Path.cwd() / p
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return path


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"

