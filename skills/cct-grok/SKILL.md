---
name: cct-grok
description: >-
  Fire a fast Grok probe, second opinion, or adversarial review through ccteam
  and inline-wait for the answer. Use when the user says "用 cct-grok ...", "问下
  grok ...", "让 grok 看看 ...", "ask grok / get grok's take / quick probe with
  grok", or otherwise wants a quick minute-scale answer from a Grok session
  routed and accounted through ccteam. This is for QUICK Q&A, code probes,
  bottleneck hunts, and cross-model second opinions — NOT for long implementation
  grinds (use cct-codex for those). Requires a running ccteam daemon and a
  registered ccteam project.
---

# cct-grok — fast Grok probe / second opinion via ccteam

Grok is your quick-answer teammate: probes, "where's the bottleneck", "is this
approach sane", cross-model review. Answers are minute-scale, so you inline-WAIT
rather than fire-and-forget. Everything routes through the ccteam daemon
(sid-addressed, cost-accounted) — never shell out to a bare grok CLI.

## Tools
`mcp__ccteam__session_spawn` · `mcp__ccteam__session_collect` ·
`mcp__ccteam__session_dispatch` · `mcp__ccteam__session_stop`
(and `mcp__ccteam__status` to sanity-check).

## Procedure

1. **Sanity-check the surface** if unsure: `mcp__ccteam__status`. `session_spawn`
   resolves the project from the working directory; pass `project:"<slug>"` if
   you're outside one.

2. **Spawn + wait inline.**
   `mcp__ccteam__session_spawn { vendor:"grok", title:"probe-<topic>",
   task:"<question>", wait_seconds:120, idempotency_key:"<unique>" }`
   Raise `wait_seconds` toward its 600 cap for heavier probes (repo-wide
   reading, multi-file tracing) instead of letting them false-timeout. Leave
   `notify` at its default: if the wait times out, an ambient caller still gets
   the completion turn for free (admin callers get nothing either way — see 3).
   Keep the task tight and add a terse reply contract, e.g. "Answer in ≤15 lines,
   ranked; no code dumps. Cite file:line for every claim about code." A focused
   question gets a focused answer. If the probe must read specific files or
   repos, give ABSOLUTE paths — the child's cwd is the project dir, not yours.

3. **Read the result.**
   - `status:"completed"` → use `result_text` directly (the inline answer,
     capped ~10k chars).
   - `status:"pending"` (timed out but still running) → poll
     `mcp__ccteam__session_collect { sid, tail:true, n:1 }` REPEATEDLY while
     `activity:"working"`; read when `activity:"idle"`. Ambient callers may
     instead just wait for the completion turn to arrive.
   - The spawn response's `caller` tells you who you were: `ambient:<sid>`
     (ccteam session — the probe is your delegation child) or `admin` (plain
     main session — root spawn; polling is your only completion signal).

4. **Use it, then close.** A probe is one-shot: `mcp__ccteam__session_stop
   { sid }` when you've got the answer. To keep chatting (follow-up questions
   with the probe's context intact), `mcp__ccteam__session_dispatch { sid,
   task:"<follow-up>", wait_seconds:120 }` on the same sid instead.

## When to reach for this vs cct-codex
- Quick question / triage / "which of these 3 is right" / adversarial sanity
  check → **cct-grok** (wait inline).
- Multi-file implementation, migration, test-fixing, anything measured in
  many minutes → **cct-codex** (async + poll).
- Route by strength; buy the deepest reasoning only once, on the hard part.

## Report back
Relay Grok's answer to the user concisely, attributed ("Grok's take: …"), with
the sid and cost if relevant. If you also ran codex or claude on the same
question, present them side by side so the user can pick.

## Troubleshooting
- Tools missing: a plain vendor CLI session picks up the global registration
  (`ccteam config mcp` covers Claude, Codex, Grok and OpenCode); SDK harnesses
  may not — restart in a CLI session or use `POST http://localhost:7331/mcp`
  with `Authorization: Bearer ccteam:<hex>` (hex = `~/.ccteam/secrets/web-token`).
- Grok unavailable on this host: `mcp__ccteam__status` / capabilities shows which
  vendors this machine actually has on PATH (`grok mcp doctor` checks the
  reverse direction — whether grok can reach ccteam).
