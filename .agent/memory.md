# Memory — local-gaze

Durable cross-session learnings, ONLY where they beat docs/code/git (project facts live in `docs/`;
decisions in git). Keep lean; prune on drift.

## Tooling / environment
- Subagent dispatch: `Agent` with `model: opus` (or the inherited default) resolves to `opus[1m]`, which
  this env rejects ("may not exist or you may not have access"). Use `subagent_type: "fork"` (forks run on
  the parent model verbatim) or pass an available model (`sonnet`/`haiku`). Observed failing twice 2026-06-19.
- Headroom reads: a `[N items compressed … hash=…]` marker means the Read was lossy. `headroom_retrieve` is
  unreliable here (often "Content not found"); recover exact text by re-Reading the file in narrow
  `offset`/`limit` ranges instead.
