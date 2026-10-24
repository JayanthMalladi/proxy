"""Strip secrets from text before it is stored or sent to a model."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Each pattern matches a whole secret. Keep them specific: a false positive only
# hides a few characters, but a missed key can leak to a model provider.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}\b"),  # OpenAI / Anthropic style keys
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),  # Slack tokens
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),  # Google API keys
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWTs
]

# key = value assignments whose name says they hold a secret. Only the value is replaced.
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:password|passwd|secret|api_key|apikey|token|access_key)[A-Z0-9_]*)"
    r"(\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})(\3)"
)


def redact(text: str) -> tuple[str, int]:
    """Return the text with secrets replaced, and how many were replaced."""
    if not text:
        return text, 0
    count = 0
    for pattern in _PATTERNS:
        text, n = pattern.subn(REDACTED, text)
        count += n

    def _mask(m: re.Match[str]) -> str:
        if m.group(4) == REDACTED:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(5)}"

    text, n = _ASSIGNMENT.subn(_mask, text)
    count += n
    return text, count
