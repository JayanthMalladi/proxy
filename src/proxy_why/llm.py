"""Optional model support through any OpenAI-compatible chat endpoint.

Set PROXY_LLM_BASE_URL (for example http://localhost:11434/v1 for Ollama or
https://openrouter.ai/api/v1) and PROXY_LLM_MODEL. PROXY_LLM_API_KEY is sent
as a bearer token when set. Without these, Proxy uses rules only.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .redact import redact

MAX_INPUT_CHARS = 12_000

_PROMPT = """You extract engineering decisions from software history.
Read the {kind} below and answer with one JSON object and nothing else:
{{"is_decision": true or false, "title": "...", "decision": "what was decided, one or two sentences",
 "why": "the reasons given, quoting the source where possible", "alternatives": "options rejected and why, or empty",
 "confidence": 0.0 to 1.0}}
Only use facts stated in the text. If no reason is given, leave "why" empty.

TITLE: {title}
TEXT:
{body}"""


def configured() -> bool:
    return bool(os.environ.get("PROXY_LLM_BASE_URL") and os.environ.get("PROXY_LLM_MODEL"))


def model_name() -> str:
    return os.environ.get("PROXY_LLM_MODEL", "")


def chat(prompt: str, timeout: int = 60) -> str | None:
    base = os.environ["PROXY_LLM_BASE_URL"].rstrip("/")
    payload = {
        "model": model_name(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if os.environ.get("PROXY_LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['PROXY_LLM_API_KEY']}"
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
        return None


def extract_decision(source) -> dict | None:
    body, _ = redact(source.body[:MAX_INPUT_CHARS])
    title, _ = redact(source.title)
    reply = chat(_PROMPT.format(kind=source.kind, title=title, body=body))
    return parse_json(reply) if reply else None


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
