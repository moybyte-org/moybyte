# Refactorability audit, 2026-11-11: shared brief

The owner wants a refactor sprint after the 2026-11 OS sprint (v0.18). Three
read-only auditors each cover one part of the OS. Findings feed one plan.

## Tree

Read a worktree at origin/dev's tip (5465b571c). A board pass is building in
it: read only, never build, never write there.

## Already measured (do not redo; build on it)

- About 380k lines of product code: C 204k, Python 155k, board glue 21k.
  Plus 245k lines of tests and 208k vendored (WAMR, mbedTLS, Dropbear, Lua,
  dash).
- One call table, `native/os_calls.json` (289 rows). `tools/gen_os_table.py`
  generates the ELF, WASI, wasm and Lua tables, the permission table, the SDK
  and doc tables. Every MicroPython module (`native/*/modmoy_*.c`, ~36k
  lines) and its CPython ctypes face (`runtime/host/*binding*.py`, ~11k
  lines) are still written by hand. A UI feature is ~25% logic and ~75%
  per-tier glue.
- Three timer schedulers:
  - Python `Wakes`, `runtime/system/moybyte.py:340`;
  - C `moy_evq`, `native/moy_kernel/moy_evq.c:812`;
  - Lua services, `native/moycore/moycore_svc.c:352`.
- Notifications post through two paths.
- Lua's `moybyte` table is built twice: `runtime/run/lua_ext.py:726` and
  `moycore_svc.c:632`.
- Devices have two faces (Python objects, handles).
- Input policy has no owner:
  - text mode is set in ~30 places, and carts write the flag directly;
  - cancel is a hand-built chain (`runtime/shell/console_text.py:80`);
  - back is ~20 per-layer methods.

  Today's T-Deck typing bug came from this.
- No test walks every call-table row against a live Python `moybyte` and a
  live Lua run.

## Rules of the repo that a refactor must keep

Read `CLAUDE.md` first:
- the four rules;
- one canvas class on every tier;
- a lever a board lacks is None;
- a promoted mechanism needs an executable guard.

Also: `docs/surface_model_v1.md` §8 is closed, and the kernel is C while apps
stay Python. The ratchets in `tests/test_console_facade.py` record the last
refactor program (2026-08). Grep, then read ranges. Never read a file over
~20 KB whole.

## What to find

Refactorability, not style.
- Duplicated logic and policy: the same rule in two or more places.
- Glue that could be generated.
- Modules that are too big or tangled: a file doing several jobs, or cyclic
  imports.
- Dead code: check `inspect.getsource` users and build scripts before calling
  anything dead.
- Leaky boundaries: Python reaching into C state, shell layers poking each
  other's privates.
- Inconsistent patterns: two ways of doing one thing.
- Missing guards that let drift happen.
- Docs that contradict code.

## Output

Write one file per area: `native.md`, `shell.md`, `tiers.md` (this folder holds them).

1. A one-paragraph health verdict for the area.
2. Findings, ranked by payoff. Each finding has:
   - id;
   - title;
   - evidence (file:line);
   - what to do;
   - lines removed or simplified (estimate);
   - risk (low/med/high, and why);
   - the guard test that would keep it fixed;
   - dependencies on other findings;
   - whether it needs a board to verify.
3. A suggested order, as batches that land independently.
4. What NOT to touch, and why: settled decisions, or things that look
   duplicated but are deliberate.

Keep it to what a worker could act on. No edits to the repo, no sub-agents,
no boards. Hand back a 10-line summary and the file path.
