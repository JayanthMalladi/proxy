"""Turn sources (commits, PRs, docs, sessions) into decision cards.

The default extractor is rule based, so Proxy works with no model at all. When
an OpenAI-compatible endpoint is configured (see llm.py) it is used instead and
the rules remain the fallback.
"""

from __future__ import annotations

import re

from . import llm
from .store import Card, Source

WHY = re.compile(
    r"(?i)\b(because|since(?!\s+(?:then|v?\d))|so that|in order to|to (?:avoid|prevent|fix|reduce|keep|make sure|ensure|support|allow)|"
    r"due to|caus(?:e|ed|es|ing)|otherwise|the reason|reasons?:|motivation|rationale|"
    r"(?:problem|issue) (?:is|was)|(?:(?:this|that|it) )?(?:fixes|prevents|avoids|ensures)|(?:this|that|it) (?:fixed|allows|makes|means)|"
    r"which (?:caused|led|broke|meant)|was (?:causing|breaking|failing)|fails? (?:when|if)|breaks? (?:when|if))\b"
)
ALTERNATIVE = re.compile(
    r"(?i)\b(instead of|rather than|alternatives?|considered|we tried|i tried|tried (?:to|using)|"
    r"did(?:n't| not) work|doesn't work|does not work|fall ?back|replac(?:e|ed|es|ing) \S+ with|"
    r"switch(?:ed|ing)? (?:from|to)|(?:options?|approaches?) (?:were|are|considered))\b"
)
DECISION = re.compile(
    r"(?i)\b(decided|decide|we will|we'll|chose|choose|going with|switch(?:ed|ing)? to|"
    r"use \S+ instead|deprecat\w*|drop(?:ped)? support|revert\w*|we should|let's|settled on|agreed)\b"
)

TRIVIAL_SUBJECT = re.compile(
    r"(?i)^(merge (branch|remote|main|master|\S+ into)|bump |release |version |v?\d+\.\d+(\.\d+)?$|update changelog|"
    r"typo|fix typo|format|lint|style:|chore\(deps|build\(deps|\[pre-commit|pre-commit autoupdate|wip$)"
)
_PR_SUFFIX = re.compile(r"\s*\(#\d+\)\s*$")
_TEMPLATE_LINE = re.compile(r"(?i)^\s*(- \[[ x]\]|<!--|#+\s*(checklist|checks|summary)\b|fixes #|closes #|resolves #|signed-off-by:|co-authored-by:)")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(])|\n+")
_ADR_SECTION = re.compile(r"^#{1,3}\s*(.+?)\s*$", re.M)

MAX_SENTENCE = 400


def sentences(text: str) -> list[str]:
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    out = []
    for raw in _SENT_SPLIT.split(text):
        s = raw.strip()
        if not s or _TEMPLATE_LINE.match(s):
            continue
        s = re.sub(r"^\s*(?:[-*+]\s*|\d+[.)]\s+)", "", s)  # list markers
        s = re.sub(r"^#+\s*", "", s)
        s = " ".join(s.split())
        if len(s) < 12:
            continue
        out.append(s[:MAX_SENTENCE])
    return out


def _pick(sents: list[str], pattern: re.Pattern[str], limit: int, exclude: set[str] | None = None) -> list[str]:
    picked = []
    for s in sents:
        if exclude and s in exclude:
            continue
        if pattern.search(s) and s not in picked:
            picked.append(s)
        if len(picked) >= limit:
            break
    return picked


def is_trivial(source: Source) -> bool:
    if source.kind == "pr":
        return bool(TRIVIAL_SUBJECT.search(source.title)) or source.author.endswith("[bot]")
    if source.kind != "commit":
        return False
    if source.meta.get("merge"):
        return True  # the PR record carries the merge's content
    if TRIVIAL_SUBJECT.search(source.title):
        return True
    return source.author.endswith("[bot]") or "dependabot" in source.author.lower()


def evidence_for(source: Source, pr_url: str | None = None) -> list[dict]:
    label = {
        "commit": f"commit {source.ref[:8]}",
        "pr": f"PR #{source.ref}",
        "doc": source.ref,
        "session": f"Claude Code session {source.ref[:8]}",
    }[source.kind]
    ev = [{"label": label, "url": source.url, "date": source.date}]
    if pr_url and source.kind == "commit" and source.meta.get("pr"):
        ev.append({"label": f"PR #{source.meta['pr']}", "url": pr_url, "date": ""})
    return ev


def extract(source: Source, pr_url: str | None = None, use_llm: bool | None = None) -> Card | None:
    if is_trivial(source):
        return None
    if use_llm is None:
        use_llm = llm.configured()
    card = None
    if use_llm:
        card = _extract_llm(source)
    if card is None:
        card = _extract_rules(source)
    if card is None:
        return None
    card.evidence = evidence_for(source, pr_url)
    card.files = card.files or list(source.files)
    card.date = card.date or source.date
    card.author = card.author or source.author
    return card


def _extract_rules(source: Source) -> Card | None:
    if source.kind == "doc" and source.meta.get("adr"):
        return _from_adr(source)

    description, _, discussion = source.body.partition("\n## Discussion\n")
    desc_sents = sentences(description)
    disc_sents = sentences(discussion)
    title = _PR_SUFFIX.sub("", source.title).strip()

    why = _pick(desc_sents, WHY, 3)
    why += _pick(disc_sents, WHY, max(0, 4 - len(why)), exclude=set(why))
    alts = _pick(desc_sents + disc_sents, ALTERNATIVE, 2, exclude=set(why))
    decisions = _pick(desc_sents + disc_sents, DECISION, 1)

    if source.kind == "commit":
        if not (why or alts) and len(description.strip()) < 120:
            return None
        decision = title
        if not why:
            # No stated reason: keep what the message says as detail, not as a "why".
            detail = [x for x in desc_sents if x not in alts][:2]
            if detail:
                decision = f"{title}. {' '.join(detail)}"
    elif source.kind == "pr":
        decision = title
        first = desc_sents[0] if desc_sents else ""
        if first and first not in why and not _same(first, title):
            decision = f"{title}. {first}"
    elif source.kind == "doc":
        if not (decisions and why):
            return None
        decision = decisions[0]
    elif source.kind == "session":
        turns = re.split(r"\n\n(?=Developer: |Agent: )", source.body)
        agent_text = "\n".join(t[len("Agent: "):] for t in turns if t.startswith("Agent: "))
        agent_sents = sentences(agent_text)
        why = _pick(agent_sents, WHY, 4)
        alts = _pick(agent_sents, ALTERNATIVE, 2, exclude=set(why))
        if not (why or alts) and not source.files:
            return None
        decision = title
    else:
        return None

    cues = sum(bool(x) for x in (why, alts, decisions))
    base = {"commit": 0.45, "pr": 0.55, "doc": 0.5, "session": 0.4}[source.kind]
    return Card(
        source_id=source.id or 0,
        title=title[:200],
        decision=decision[:600],
        why=" ".join(why)[:1500],
        alternatives=" ".join(alts)[:800],
        confidence=min(0.9, base + 0.12 * cues),
        method="rules",
    )


def _same(a: str, b: str) -> bool:
    norm = lambda x: re.sub(r"[^a-z0-9]+", " ", x.lower()).strip()  # noqa: E731
    na, nb = norm(a), norm(b)
    return na == nb or na.startswith(nb) or nb.startswith(na)


def _from_adr(source: Source) -> Card:
    sections: dict[str, str] = {}
    matches = list(_ADR_SECTION.finditer(source.body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source.body)
        sections[m.group(1).lower()] = source.body[m.end():end].strip()

    def find(*names: str) -> str:
        for key, text in sections.items():
            if any(n in key for n in names):
                return text
        return ""

    decision = " ".join(sentences(find("decision", "outcome"))[:2])
    why = " ".join(sentences(find("context", "problem", "motivation", "drivers"))[:3])
    alts = " ".join(sentences(find("alternative", "option", "considered"))[:3])
    if not decision:
        decision = " ".join(sentences(source.body)[:2])
    return Card(
        source_id=source.id or 0, title=source.title[:200], decision=decision[:600],
        why=why[:1500], alternatives=alts[:800], confidence=0.85, method="rules",
    )


def _extract_llm(source: Source) -> Card | None:
    data = llm.extract_decision(source)
    if not data or not data.get("decision"):
        return None
    if data.get("is_decision") is False:
        return None
    return Card(
        source_id=source.id or 0,
        title=str(data.get("title") or source.title)[:200],
        decision=str(data["decision"])[:600],
        why=str(data.get("why") or "")[:1500],
        alternatives=str(data.get("alternatives") or "")[:800],
        confidence=float(data.get("confidence") or 0.6),
        method=f"llm:{llm.model_name()}",
    )
