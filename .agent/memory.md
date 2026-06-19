# Memory — local-gaze

Durable cross-session learnings, ONLY where they beat docs/code/git (project facts live in `docs/`;
decisions in git). Keep lean; prune on drift.

## Tooling / environment
- Subagent dispatch: `Agent` with `model: opus` (or the inherited default) resolves to `opus[1m]`, which
  this env rejects ("may not exist or you may not have access"). Use `subagent_type: "fork"` (forks run on
  the parent model verbatim) or pass an available model (`sonnet`/`haiku`). Observed failing twice 2026-06-19.
- Headroom retrieve: a `[N items compressed … hash=…]` marker = a lossy Read. Failed `headroom_retrieve`
  traced to the shared proxy store's 300s default TTL — originals expire 5 min after compression (not a
  flaky bug). Fixed 2026-06-19 via `export HEADROOM_CCR_TTL_SECONDS=86400` in `~/.profile`; the proxy
  inherits it at launch, so it applies to sessions started AFTER the fix. The proxy is one long-lived
  shared daemon, so a session predating the fix keeps 300s until that daemon restarts. Fallback when a
  retrieve still misses (pre-fix proxy, or evicted past the 1000-entry `max_entries` cap): re-Read the
  file in narrow `offset`/`limit` ranges.
