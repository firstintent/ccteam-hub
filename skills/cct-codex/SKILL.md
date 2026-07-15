---
name: cct-codex
description: >-
  Hand a heavy or long-running implementation/coding task to a Codex session
  through ccteam, then supervise it to completion. Use when the user says
  "用 cct-codex ...", "让 codex 做 ...", "交给 codex 实现 ...", "use codex to
  build/implement/refactor X via ccteam", or otherwise wants a background Codex
  agent to grind through work while accounted in the ccteam ledger. This is the
  right tool for long implementation, migrations, and mechanical grinds — NOT
  for quick questions (use cct-grok for those). Requires a running ccteam daemon
  and that the work lives in a registered ccteam project.
---

# cct-codex — delegate implementation to a Codex teammate via ccteam

You are the orchestrator. You do NOT write the code yourself — you hire a Codex
session through ccteam, brief it well, supervise it, and review the result. Codex
is your long-grind implementer: multi-file edits, migrations, test-fixing.

Everything routes through the ccteam daemon, so the work is sid-addressed,
cost-accounted, and visible in `session_list` / the web team view. Never shell
out to `codex exec` — a bare CLI run has no sid, no ledger, no supervision.

## Tools you'll use
`mcp__ccteam__session_spawn` · `mcp__ccteam__session_dispatch` ·
`mcp__ccteam__session_collect` · `mcp__ccteam__session_list` ·
`mcp__ccteam__session_stop` (and `mcp__ccteam__status` for a health check).

## Procedure

1. **Confirm the surface.** If unsure the daemon is up or which project you're
   in, call `mcp__ccteam__status` (or `session_list`). `session_spawn` resolves
   the target project from the current working directory; if you're outside a
   registered project, pass `project:"<slug>"` explicitly.

2. **Write a crisp brief with a REPLY CONTRACT.** The single biggest lever.
   Spell out the goal, the files/area, the acceptance check (build/tests/lint),
   and end EVERY task with a reply contract, e.g.:
   > Reply in ≤25 lines. Sections: STATUS / FILES CHANGED / DESIGN DECISIONS /
   > TEST RESULTS (pass/fail counts) / OPEN QUESTIONS. Do NOT paste code or diffs
   > — I review via `git diff` locally.
   Child answers beyond the return cap are truncated, and verbose output floods
   YOUR context. Tell it to commit or not per the user's intent (default: leave
   the tree dirty for review, don't commit).

3. **Spawn + first task in one call.**
   `mcp__ccteam__session_spawn { vendor:"codex", title:"<short label>",
   task:"<brief>", idempotency_key:"<unique>" }`
   - `idempotency_key` guards against a client-timeout double-spawn.
   - Capture the returned `sid` (e.g. `s47`).
   - **Check `caller` in the response.** `ambient:<your sid>` = you are a
     ccteam-managed session; the child is linked to you as its delegation
     parent and its completion notification will land in your chat as a normal
     turn — you may rely on that instead of polling. `admin` = you are a plain
     main session (root spawn; no notification can reach you): pass
     `notify:false` and POLL instead. If you expected a parent link and see
     `admin`, flag it in your report — your call rode an admin-authenticated
     MCP server instead of your session's own bearer.

4. **Track completion per your `caller`.** Ambient callers: the completion
   notification arrives as a normal turn (capped ~4k chars with a collect
   pointer) — react to it, and `mcp__ccteam__session_collect { sid, tail:true }`
   for the full answer. Admin callers (and any time you want progress): POLL
   with `mcp__ccteam__session_collect { sid, tail:true, n:1 }`.
   - `activity:"working"` = mid-turn → wait and poll again (pass `since:<last
     turn_id>` for incremental reads; don't re-pull the whole transcript).
   - `activity:"idle"` = turn finished → read.
   - **Codex caveat:** codex emits intermediate narration as separate turns and
     flickers to `idle` between them. Do NOT declare done on the first `idle` —
     confirm the FINAL structured answer is present (your reply-contract
     sections). When in doubt, poll once more.

5. **Review the diff LOCALLY.** Run `git diff` / `git status` yourself. Never ask
   the child to paste diffs — that's what the reply contract forbids.

6. **Iterate on the same sid.**
   `mcp__ccteam__session_dispatch { sid, task:"Fix X; re-run only the failing
   tests; same reply contract." }` — reusing the sid keeps context; one dispatch
   = one thing (each turn = one completion checkpoint). Completion discipline is
   the same as step 4: ambient → notification turn, admin → poll.

7. **Optionally review with a different model before you accept.** For a merge
   gate, spawn a Claude or Grok reviewer in the SAME project on the diff
   (cross-vendor review catches what same-model review misses).

8. **Close out.** An idle session spends no money, and under live-capacity
   pressure the daemon gracefully LRU-evicts the least-recently-active ones
   (evicted sids stay resumable) — so leaving it for follow-ups is fine. If it
   was a one-shot, `mcp__ccteam__session_stop { sid }`.

## Report back to the user
Summarize what Codex did, the sid, test results, files touched, and your own
review verdict — concisely. Surface failures honestly (paste the failing test
line, don't hand-wave). Offer the next step (accept / iterate / commit).

## Troubleshooting
- Tools missing (`No such tool available`): this session didn't load the ccteam
  MCP server. A normal vendor CLI session picks up the global registration
  (`ccteam config mcp` registers Claude, Codex, Grok and OpenCode); SDK-driven
  harnesses may not. Restart in a plain CLI session, or drive the same tools
  over `POST http://localhost:7331/mcp` with header
  `Authorization: Bearer ccteam:<hex>` (hex = `~/.ccteam/secrets/web-token`)
  and body `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":
  "session_spawn","arguments":{...}}}`. Admin-bearer spawns are root spawns.
- `missing project`: `cd` into a registered project or pass `project:`.
- Child "not moving": it's `working`, not stuck — that's it doing the job.
