# Shell decoupling — the `ws` service locator, and the order to dismantle it

**SUPERSEDED 2026-08-19.** The sequence that actually shipped is
[`docs/ui_refactor_2026-08.md`](ui_refactor_2026-08.md); read its §1 before
re-proposing anything below, because rows 7-8, 9, 10 and 11 were all declined
there with reasons. What survives here is the material that plan cites rather
than repeats: the `ws` inventory, the per-row measurements, the numbers.

**Status:** PLAN / OPEN — a sequenced refactor with a ratchet per step. Nothing here
changes what the console *does*; every row is a move, a deletion, or a narrowing.
**Measured:** 2026-08-14, against `dev` at a green baseline (1,990 passed, 22 skipped,
2m47s). Every number below is reproducible — see §8. **Re-measure before believing
this doc**; it is a snapshot of a moving tree, not a constant.
**Feeds:** #181 (system apps aren't editable — blocked on exactly this seam),
#113 (UI animation perf), #192 (moycore tracker, which owns `surface.py`'s Phase C).
**Reads:** `docs/app_api_v1.md` (shipped), `docs/surface_model_v1.md` (LOCKED),
`docs/shell_architecture_v1.md` (exploration).

---

## 1. The finding

There is **one** structural problem. `Workstation` is a service locator: every surface
in the console holds a `self.ws` back-reference and reaches through it for whatever it
needs, including private members. Nothing imports `console` to do this, so the problem
is invisible to import analysis — which is why the module graph looks healthy while the
object graph is a single node.

```
runtime/                67 modules · 146 import edges · 3 cycles · max fan-in 20
runtime/console.py      5,547 lines · 250 methods · 202 instance attributes
ws.* reach-ins          2,243 uses across 264 distinct names
  ...public             1,575 uses / 159 names
  ...PRIVATE              668 uses / 105 names
```

The heaviest private reach-ins, all from outside `console.py`:

| name | uses | what it really is |
|---|---|---|
| `ws._dirty` | 157 (147 are assignments) | the whole-screen invalidation flag |
| `ws._btn` | 47 | the shared draw toolkit |
| `ws._with_sd` | 45 | storage |
| `ws._glyph` | 44 | the shared draw toolkit |
| `ws._icon` | 26 | the shared draw toolkit |
| `ws._set_text_mode` | 26 | keyboard policy |

Per-consumer, the coupling is very unevenly distributed — and this is what determines
the order of work:

| consumer | ws.* uses | distinct names | private |
|---|---|---|---|
| the 7 system apps (union) | 391 | **42** | 13 |
| `launcher_layer.py` | 231 | 44 | 10 |
| `wm_windowed.py` | 168 | **75** | 34 |
| `editor_app.py` | 130 | 55 | 15 |
| `wm.py` | 32 | 26 | 15 |

The seven apps together touch fewer distinct names than the windowed WM does alone.
**The apps are the cheapest place in the tree to discover the right API; the WM is the
most expensive.** Work outward from the cheap end.

## 2. What is working — do not churn it

Recorded so a future pass does not "improve" these:

- **The Layer protocol and the content-layer registry landed properly.** Six
  `_LegacyLayer` shims remain out of what was once every surface; `_content_layers` is a
  registry lookup, not a branch ladder; `register_app` is a genuine one-line extension
  point (`docs/app_api_v1.md`).
- **The import graph is clean.** Three cycles, all deliberately broken with
  function-level imports, all caused by shared constants living in the wrong module (§4
  row 2 fixes all three).
- **Hygiene is not the problem.** 12 unused locals across 47,920 lines.
- **Vendoring discipline is exemplary.** `test_libmoy_vendor.py` catches both "someone
  patched the copy" and "someone patched upstream without re-vendoring". Do not weaken it.
- **The gates are real and fast.** `test_spec_conformance.py` pins every raster pixel in
  0.05s; `test_device_canvas_parity.py` pins host↔device; `tools/p4_conformance.py`
  reaches the C kernel on glass; `tests/test_p4_on_glass.py` drives the real board in ~44s.

Cross-tier probe-guarding (`getattr(cv, "RETAINED_FRAMES", 1)`,
`getattr(sc, "blit_game", None)`) is **also correct and stays.** Four render tiers
genuinely differ in capability; that is the documented pattern, not defensive noise.

## 3. Everything else is downstream of §1

Five things that read as independent messes and are not. Each dissolves when the seam
above is fixed; none is worth a dedicated cleanup pass.

1. **Self-distrust probes.** Of 339 `getattr()` probes in `runtime/`, 98 (29%) guard the
   canvas/input backend seam and are earned. **123 (36%) probe `ws.*`/`self.*` for
   attributes a correctly-constructed instance always has** — `ws.audio`, `ws.pmem`,
   `ws.wm`, `ws.paint`, `ws.perf_capture`. Nobody can know what exists when, because 202
   attributes are assembled across five `_init_*` methods and tests build partial ones.
2. **The dual-import tax.** 142 `try/except ImportError` ladders (36 in `console.py`) plus
   a hand-ordered 22-entry `sys.modules.setdefault` table in `host_app.py` — two
   mechanisms for one problem, one of them order-sensitive.
3. **The barrel.** `console.py` re-exports 185 imported-but-unused names behind a 350-line
   header, so pre-extraction `console.X` references still resolve.
4. **The `Project` facade.** 39 forwarding properties kept as a compat shim; callers now
   use *both* paths (`ws.cart` ×32 vs `ws.project.cart` ×22; `ws.tilemap` ×1 vs
   `ws.project.tilemap` ×15).
5. **Archaeology comments.** 580 hits narrating finished refactors — `Stage N` ×252,
   `Phase N` ×96, `extracted from` ×43, `the old …` ×61. Prose is 29% of `runtime/`; most
   of it is load-bearing hardware knowledge and stays. This subset is a commit log pasted
   into the source.

**The common cause of 3, 4 and 5 is the same:** every previous extraction left the old
path open and a note explaining itself, instead of closing the path. New code then used
both. That is the failure mode this plan exists to break.

## 4. The sequence

One commit per row, on `dev`, each independently revertable.

| # | Step | Removes | Risk | Ratchet |
|---|---|---|---|---|
| 1 | meta-path finder → `+runtime/_bootstrap.py` | 142 ladders, 22 `setdefault`s | low | no `except ImportError` in `runtime/` |
| 2 | Leaf module for `Layout`/`NAMES`/`Pointer`/`color`/`_in`/`_err_text`/`_clamp_scroll` | 2 of 3 cycles, 257-site injection | low | no function-level intra-runtime imports |
| 3 | **Freeze** facade + barrel (do not migrate) | — | none | no *new* `ws.cart`-style or `console.X` refs in `runtime/` |
| 4 | `AppContext` + migrate Calc | — | low | — |
| 5 | Hoist the bar contract into the router | 14 sites, 1 private reach-in | low | apps never touch `ws.bar_layer` |
| 6 | Migrate the remaining six apps | 13 private reach-ins | medium | app modules contain no `ws.` |
| 7 | Wire `ws._dirty` → `SurfaceSet.epoch()` on raster tiers | — | low | — |
| 8 | Attribute dirty sites to `touch(sid)`, one per commit | — | medium | no `ws._dirty` outside the damage owner |
| 9 | Extract `Prefs`, `CoverCache`, `DrawKit` | ~870 lines off `Workstation` | medium | per-concern |
| 10 | Retire hand-rolled invalidation **only where proven redundant on glass** | — | high | on-glass gated |
| 11 | One cart-verb registry (host / device / lua_host / moycore_glue) | — | low | four-way parity test |

### Rows 1–2 are the enablers, however dull

Today, extracting *any* new module costs a try/except ladder **and** a hand-ordered entry
in `host_app.py`. Until that is fixed, every later row is taxed and every new shared leaf
is discouraged — which is why `Layout` and `NAMES` are still in `console.py` and passed
as constructor arguments to 257 sites.

**The mechanism for row 1 already exists and is already proven**: `tests/conftest.py`'s
`_SharedRuntimeAliasFinder` is a `sys.meta_path` finder mapping bare names to
`runtime.<name>`. All 1,990 tests import through it. It is quarantined in the test
harness while production keeps 142 hand-written ladders. Move it; delete them.

### Row 3 is a freeze, not a migration — this was a corrected call

The first draft of this plan ranked deleting the `Project` facade and the barrel as
high-priority. Sizing killed that:

| | `runtime/` | `tests/` | firmware (authored) |
|---|---|---|---|
| facade call sites (`ws.cart`, `ws.sheet`, …) | 54 | **582** | 107 |
| barrel uses (`console.X`) | — | **257** | — |

The blast radius is overwhelmingly in tests, where a facade does no harm — a test reaching
a compatibility surface is fine. The damage is *new runtime code* picking the wrong path.
So: freeze both with a ratchet, migrate the 54 runtime sites opportunistically, leave the
tests alone. ~90% of the benefit for ~10% of the diff.

### Rows 4–6: the app seam, and why it goes before the god object

The whole app→shell surface is **42 distinct names / 391 uses**, which cluster into six
roles with almost no judgement required:

| role | uses | absorbs |
|---|---|---|
| `ctx.damage` | 89 | `_dirty` |
| `ctx.store` | 120 | `carts_store`, `carts_root`, `_with_sd`, `can_manage` |
| `ctx.surface` | 62 | `sys_canvas`, `windowed_chrome`, `_glyph`, `_effective_font_scale` |
| `ctx.theme` | 36 | `theme_colors`, `theme_name`, `theme_variant`, `light_chrome`, `set_theme*` |
| `ctx.chrome` | 14 | `bar_layer` — see below |
| `ctx.nav` | ~10 | `open_app`, `run`, `is_system_app`, `_set_text_mode`, `_open_workspace` |

That is >90% of the surface. The remainder: the wallpaper cluster (~10 uses, Appearance
only — a capability, not a core role), `ws.artwork` (28 — `artwork.py` reaching for its
own `PaintEditor` model, not a shell dependency), and five app→app calls in
`files_app.py` (`ws.writer_app.open_named(...)`), which `app_api_v1.md` lists as an
explicit **non-goal (v1)** and which happened anyway because there was no seam for a real
product need.

**Start with Calc** — already the designated reference app, 7 names / 12 uses. Migrating
it proves the six roles against a real consumer for the cost of an afternoon.
`sheets_app` (33 private uses) and `storybook_app` (28) will tell you the most about
where the roles are wrong.

**These six roles are five of the eight concerns the god object needs split anyway.
Building `AppContext` *is* building the role split** — validated against seven bounded
consumers before it is pointed at the launcher and the editor tabs, where a wrong
interface is expensive to undo.

### Row 5 in detail: the bar contract is a convention wearing an API's clothes

Every app hand-implements this, byte-identically, seven times:

```python
if not self.ws.windowed_chrome:
    self.ws.bar_layer._draw_status_strip("tool")     # a PRIVATE method on a sibling layer
```

…plus seven matching `handle_bar_tap("tool", ...)` calls on the input side, and a
paragraph in `app_api_v1.md` instructing authors to write both in the right order. An app
that forgets becomes **unexitable**.

The router already knows it is drawing a registered app. It can draw the strip after
`draw()` and route the tap before `handle_pointer()`. That deletes 14 call sites, one
private reach-in, one documentation section, and a whole class of bug.

**This is the shape of the entire refactor in one example: rituals every app must
remember become guarantees the host provides.**

## 5. How to execute

**Commit discipline.** One row = one commit = one landed outcome. Squash fragments before
pushing. `dev` only; `master` is what a board runs.

**Verification ladder**, cheapest first:

1. `tests/test_spec_conformance.py` — 0.05s, pins every raster pixel
2. `make test` — 1,990 tests, ~2m47s
3. one firmware build after rows 1–2 — they touch staging and import plumbing, which host
   tests structurally cannot see
4. `MOYBYTE_P4_PORT=/dev/ttyACM0 .venv/bin/python -m pytest tests/test_p4_on_glass.py`
   — ~44s, required before anything from row 4 onward reaches `master`

**Rows 1, 2 and 5 must be provably behaviour-free.** They are moves and deletions. If a
diff in those rows changes a pixel or a control-flow decision, it is wrong — a stronger
check than the suite.

**Every row ends with a ratchet.** This is the load-bearing part of the plan. The reason
this tree carries a `Stage 1…9` archaeology trail, a Phase-0 shim, a facade used both
ways and a 185-name barrel is that **every prior refactor ended without closing the old
road.** A row is not finished when the new thing works. It is finished when the old thing
is impossible, pinned by a test. The idiom already exists here —
`tests/test_streaming_sunset.py` pins absences; `test_libmoy_vendor.py` pins hashes.

**Stop conditions.** Each row moves a number. If it does not, revert rather than continue:

- after row 1: `grep -c 'except ImportError' runtime/*.py` → 0
- after row 6: `grep -c 'ws\.' runtime/*_app.py runtime/artwork.py` → 0
- after row 9: `wc -l runtime/console.py` → under ~3,500

**Where to stop entirely.** Rows 1–3 remove most of the *noise*. Rows 4–6 produce the
first real seam and unblock #181. Rows 7–8 are the architecture. **Stopping after row 8
leaves the tree substantially better and is a legitimate end state.** Rows 9–11 are
worth doing; nothing depends on them.

## 6. Deliberately out of scope

- **`wm_windowed.py`.** 75 distinct names, 34 private — the densest coupling in the repo,
  and it holds the hardest-won on-glass perf work (async-PPA ordering, dirty-union
  restore, stamp-defer, the #155 chrome freeze). Apply roles here only after seven apps
  have proven them, and only where they fit. **"Never" is a legitimate answer for this
  file** and should not be treated as unfinished work.
- **The capability / privileged-cart API** (`make_system_api`, #55's deferred half, now
  re-filed as #181's privilege boundary). Real, wanted, and *downstream*: a capability
  table cannot be designed until the surfaces' needs are known, and rows 4–6 produce
  exactly that list — as code the interpreter checks, rather than as the hand-written
  "dependency profile" docstrings `shell_architecture_v1.md` §2.2 had to derive it from.
  Once apps declare their needs through a context, `make_system_api` is a filter over an
  existing interface instead of a new axis.
- **Migrating the 582 test-side facade calls and 257 barrel references.** See row 3.
- **A comment-cleanup pass.** The 580 archaeology hits should die *with the row that
  makes them false*, not in a sweep. A sweep would also endanger the hardware-constraint
  comments, which are the most valuable prose in the repo.
- **Deleting defensive code as a category.** 29% of the `getattr` probes are earned
  cross-tier capability detection. A blanket pass would strip those alongside the 36%
  that are god-object symptoms — and the symptoms delete themselves as rows 4–9 land.

## 7. `runtime/surface.py`: a completed model with no driver

Recorded because this cost a full investigation to establish, and the code reads as an
oversight from every angle except the one document that explains it.

`surface.py` is the damage model — 114 lines: a `Surface` record and a `SurfaceSet`
registry with one monotonic gen mint and three signals (`touch` attributed, `move`
placement-only, `epoch` un-attributed), plus `content_gen()` for consumers to compare
with `!=`. It is complete, correct, covered by `tests/test_surface_model.py` (12 tests),
and carries three rules learned at review — including that sids must be minted from WM
registry keys and never content kinds, which is the #113 bug that silently disabled the
drag freeze everywhere.

**It is inert on every shipping tier.** `wm_windowed.py` sets
`self._recording = hasattr(self._root_canvas, "begin_surface")`, nothing implements
`begin_surface` any more, and every consumer opens with `if not self._recording: return`.
Its driver — the web streaming recorder — was deleted in the 2026-08 sunset.
`surface_model_v1.md` §13 states this explicitly and says the model was kept as Phase A/C
groundwork, leaving "wiring it on raster, or deleting it" to Phase C with evidence.

**It is not abandoned and not speculative. It shipped, it worked, and its consumer was
removed.** Meanwhile the raster tiers hand-rolled a partial substitute — `_drawn_gens`,
`_lowest_dirty_window`, `_sig_stable`, `_chrome_quiet`, `_frame_is_quiet`,
`_stamp_streak`, alongside `ws._dirty` and `ws._last_ptr` in `console.py`. Those are the
"six hand-rolled partial re-derivations of region invalidation" CLAUDE.md warns about,
built while a correct general model sat one file away.

**Consequence for this plan: rows 7–8 need no design.** `SurfaceSet` is already the API
that 157 `ws._dirty` references want, and its own docstring states the mapping — *"a
`ws._dirty` write nobody attributed means 'everything changed' — safe, never wrong,
merely unprofitable."* So:

- **Row 7 is behaviour-identical by construction.** `epoch()` means everything is dirty,
  which is exactly what one global boolean already means. No perf change, no risk.
- **Row 8 is incremental and measurable.** Each `ws._dirty` → `touch(sid)` conversion is
  one audited site, one on-glass win, independently revertable. §3 Class A calls this
  opt-in per site, which is correct.
- **Row 10 is guarded.** Do **not** rip out `_lowest_dirty_window` / `_chrome_quiet` /
  `_frame_is_quiet` when the model is wired. They are tuned on glass — the chrome freeze
  alone was 8.2ms of a 70ms P4 frame. Wire alongside; retire each only when a hardware
  measurement proves it redundant.

## 8. Reproducing the numbers

Every figure in this doc comes from the working tree, not from memory. Re-run before
trusting any of it.

```bash
# coupling: distinct ws.* names, split public/private
grep -rhoE '\bws\.[a-zA-Z_][a-zA-Z0-9_]*' runtime/*.py | sort | uniq -c | sort -rn

# per-consumer profile (swap the file)
grep -rhoE '\b(self\.)?ws\.[a-zA-Z_][a-zA-Z0-9_]*' runtime/wm_windowed.py | sort -u | wc -l

# the dual-import tax
grep -rho 'except ImportError' runtime/*.py | wc -l
grep -c 'sys.modules.setdefault' runtime/host_app.py

# the barrel
.venv/bin/python -m pyflakes runtime/console.py | grep -c 'imported but unused'

# facade blast radius
grep -rhoE '\bws\.(cart|config|sheet|tilemap|images|tables|texts|pmem|scenes|ns)\b' tests/*.py | wc -l

# archaeology (all ten patterns -- dropping any of them undercounts)
grep -rhoiE 'extracted from|Stage [0-9]|Phase [0-9]|used to|no longer|was removed|pre-extraction|previously|the old |moved to|renamed' runtime/*.py | wc -l

# is the surface model still inert?
grep -rn 'def begin_surface' runtime/*.py firmware/*/modules/*.py    # empty => inert
```

The `Workstation` concern breakdown in §1 comes from an AST pass grouping its 250 methods
by name prefix; it is a judgement-calibrated grouping, not a mechanical one, and should be
re-derived rather than cited if the class changes shape.
