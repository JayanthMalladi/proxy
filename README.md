# Proxy

**Ask a codebase's memory why the code is the way it is.**

Proxy reads a repository's history (commits, pull requests, review comments, design docs and, if you opt in, your own Claude Code sessions) and turns it into **decision cards**: what was decided, why, and what was rejected, with links to the evidence. You, your teammates and your coding agents can then ask "why is this line like this?" instead of interrupting the person who wrote it.

Every answer cites its sources. The code owner can approve or edit answers, and approved answers are reused the next time anyone asks.

```
$ proxy why payments/callback.py:2
Line 2 of payments/callback.py (`RETRY_MAX = 3`) was last changed in commit 497dd488 by Ravi on 2026-03-11: "Cut callback retries from 5 to 3".

It came from PR #4127, "Cut UPI callback retries to stop duplicate refunds".

Decision: Cut UPI callback retries to stop duplicate refunds. Cut callback retries from 5 to 3
Why: A fourth retry caused duplicate refunds in incident INC-311 because NPCI callbacks can arrive up to 90s late.
Alternatives mentioned: We tried exponential backoff instead of a fixed gap, but the gateway rate-limits bursts.

(Unreviewed: the owner has not checked this answer yet.)

Sources:
  - commit 497dd488  https://github.com/acme/payments/commit/497dd488f830…
  - PR #4127  https://github.com/acme/payments/pull/4127
```

(Output from the sample repository the test suite builds.)

Everything runs locally. There is no server and no paid API: the default extractor is rule based, and when an agent asks through MCP, the agent you already use does the reasoning.

## Install

```bash
pipx install git+https://github.com/JayanthMalladi/proxy
# or, from a clone:
pip install -e ".[dev]"
```

Requires Python 3.10+ and git.

## Quick start

```bash
cd your-repo
proxy init                 # reads history into .proxy/proxy.db (kept out of git)
proxy why src/app.py:42    # why is this line the way it is?
proxy ask "why do we retry uploads three times?"
proxy decisions src/app.py # every recorded decision about a file
proxy review               # answers waiting for your review
proxy inbox                # the same, as a web page on localhost:7377
proxy sync                 # pick up new commits, PRs and docs
```

`proxy init` on [pallets/click](https://github.com/pallets/click) (3,379 commits) takes about 3 seconds on a laptop.

### Pull request descriptions and reviews

Most of the "why" in a project lives in PR descriptions and review threads. Set a GitHub token to include them:

```bash
export GITHUB_TOKEN=ghp_...   # read access to the repo is enough
proxy sync --github --max-prs 300
```

Without a token, Proxy still links every commit to its PR through merge commits and squash-merge titles, and uses the PR's commit messages as its record.

### Your Claude Code sessions (opt-in)

When an agent writes code with you, the reasoning ("I tried X, it broke Y, so we did Z") is in your session log, not in the commit. Proxy can read your local Claude Code sessions for this repository:

```bash
proxy sync --sessions      # turn on; --no-sessions turns it off again
```

Only your user prompts and the agent's visible replies are read, plus which files it edited. Thinking blocks, tool output and harness messages are skipped. Nothing leaves your machine.

## Use it from coding agents (MCP)

Proxy ships an MCP server with three tools: `why(path, line)`, `ask(question, path?)` and `decisions(path)`. Its instructions tell the agent to call Proxy before changing code whose purpose is unclear.

**Claude Code**

```bash
claude mcp add proxy -- proxy --repo /absolute/path/to/your-repo mcp
```

**Cursor** (`.cursor/mcp.json` in the repo)

```json
{ "mcpServers": { "proxy": { "command": "proxy", "args": ["--repo", ".", "mcp"] } } }
```

Run `proxy sync` (or add it to a git hook) so the agent sees recent history.

## Review loop

Every answer is stored as pending. The owner can:

```bash
proxy review                          # list pending answers
proxy review approve 12               # keep it as is
proxy review edit 12 "Because ..."    # keep your wording instead
proxy review reject 12
```

or click through `proxy inbox`. Approved answers are returned for the same question (or the same line at the same commit) from then on, marked as reviewed. Unreviewed answers are labelled as such so humans and agents treat them as leads, not facts.

## Better cards with a model (optional)

Point Proxy at any OpenAI-compatible endpoint, such as a local Ollama or a free OpenRouter model:

```bash
export PROXY_LLM_BASE_URL=http://localhost:11434/v1
export PROXY_LLM_MODEL=qwen2.5:7b
# export PROXY_LLM_API_KEY=...   # for hosted endpoints
proxy sync
```

The model is only used to extract cards. If a call fails, Proxy falls back to the rules.

## Privacy

- The store is a single SQLite file at `.proxy/proxy.db`, added to `.git/info/exclude` so it is never committed.
- Secrets (cloud keys, GitHub and Slack tokens, API keys, private keys, JWTs, `password=` style assignments) are redacted before anything is stored or sent to a model.
- Session reading is off until you turn it on.

## How it works

1. **Ingest**: `git log` with files per commit; merge commits and `(#123)` titles map commits to PRs; GitHub REST for PR bodies, reviews and comments; markdown docs split into sections, with ADRs parsed by section; Claude Code JSONL sessions.
2. **Extract**: each source becomes at most one decision card. Rules look for stated reasons ("because", "to avoid", "caused") and alternatives ("instead of", "we tried"). Trivial commits (version bumps, merges, bots) are skipped.
3. **Link**: cards are linked to the files their commits touched and to repo paths mentioned in the text.
4. **Answer**: `why` goes from `git blame` to the commit, its PR, and their cards. `ask` uses SQLite full-text search (BM25) and declines with "Not enough evidence" when the best match covers less than half of the question's key terms.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Roadmap

- WhyBench: 120 "why" questions across 5 open-source repos, to measure accuracy, citation precision and abstention against a code-only baseline
- Staleness flags when code drifts from the decision behind it
- Team sharing of approved cards through a committed `.proxy/cards/` folder
- A chat-room agent (Slack, Discord, Mattermost) that answers and records decisions

## License

MIT
