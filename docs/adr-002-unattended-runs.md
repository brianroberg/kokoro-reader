# ADR-002: Unattended, Scheduled Agent Runs

**Status:** Proposed (pending Brian's approval of the design spec)
**Date:** 2026-10-14
**Amends:** ADR-001's consequence that "new articles require an interactive session with the agent"

## Decision

The agent-driven workflow of ADR-001 stays as it is: one agent session per article, following the skill, making the judgment calls about which flagged issues to fix. What changes is how a session starts. A scheduled runner starts it, with nobody at the keyboard, for each new article from a configured source, and publishes the result to Audiobookshelf.

Design: `docs/superpowers/specs/2026-10-14-unattended-podcast-pipeline-design.md`.

## Why

Brian wants new articles to appear in his podcast feed without starting a session for each one. ADR-001's reasons for an agent rather than a script still hold: assessing Gemini's report and choosing between a phonetic override, a substitution and acceptance all need judgment. So the agent stays, and the scheduling and bookkeeping around it become code.

## Boundaries that make unattended runs acceptable

Unattended means an agent reads untrusted web text with no one watching. Four boundaries make that acceptable:
- **The agent's only tools are one MCP server's** (`narration`): get and edit the prepared text, record, check, look up phonemes, and finish. Its arguments are typed, and no tool takes a path. Bash, file and web tools are off, and the agent holds no publishing or push credentials.
- **The agent runs off the laptop,** in a container on the home server with restricted egress, away from Brian's files. The Mac runs only the MCP server, which is deterministic code.
- **The runner's code fetches and publishes.** The agent never does.
- **A deterministic gate runs before publishing.** It holds any recording whose text differs from the article in an undeclared way, or whose final check doesn't support the agent's claim.

## Consequences

- Batch processing now exists, with a nightly cap that's tuned from per-article cost records.
- The skill gains a second caller, the runner. Changes to the skill now affect unattended runs too, so the result contract (`result.json`) is versioned with it.
- The agent reaches the toolkit only through the MCP server, so whether the agent runs on the Mac or elsewhere comes down to the server's transport (stdio or HTTP over the tailnet). The skill and the contract stay the same.
