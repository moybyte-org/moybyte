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
  issue you open, close, comment on or edit.** A living-body issue like #66 goes
  stale locally the moment it is edited on GitHub.
- **#66 keeps its current state in the BODY; comments are its changelog.**
  When new hardware numbers land, edit the body (per-cart fps, frame budgets,
  levers), then sync.
- **An open issue is not evidence the problem still exists.** Grep the code
  before planning or reporting work on one.
- Labels: one or more `area:*` and exactly one maturity rung, bumped as the
  issue moves — `docs/issue_taxonomy.md`. Sub-issues hang under their umbrella
  tracker.
- `Closes #N` in a commit message closes the issue once the commit is pushed
  to the default branch;
  a summary of what landed goes in `gh issue comment`, not in the close.
