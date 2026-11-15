---
name: issues
description: Read, open, close, comment on or edit Moybyte's GitHub issues, and keep the local issue mirror (docs/issues/) true. Use at the start of work that reasons about issue numbers, and around every gh issue command.
---

# Issues

GitHub is the source of truth. `docs/issues/` (gitignored) is a local snapshot
of every issue — `open/`, `closed/`, `INDEX.md`, and the `STATUS.md` dashboard —
so an issue number in a commit or a chat resolves with no network.
`make sync-issues` wipes and rewrites it, so a stale copy cannot survive; never
hand-edit it.

- **Sync at the start of any session that reasons about issues, and after EVERY
  issue you open, close, comment on or edit.** An issue goes stale locally the
  moment it is edited on GitHub.
- **#66 keeps no current state in its body (2026-11-15).** The per-cart numbers
  are `docs/perf/ledger.md`, generated from the board pass's records, and the
  levers are `docs/perf/levers.md`; the body is pointers and an index of the
  sections moved out of it. A measurement's story (a bisect, an A/B, a deep
  dive) is a new comment on #66, never a body edit.
- **An open issue is not evidence the problem still exists.** Grep the code
  before planning or reporting work on one.
- Labels: one or more `area:*` and exactly one maturity rung, bumped as the
  issue moves — `docs/issue_taxonomy.md`. Sub-issues hang under their umbrella
  tracker.
- `Closes #N` in a commit message closes the issue once the commit is pushed
  to the default branch;
  a summary of what landed goes in `gh issue comment`, not in the close.
