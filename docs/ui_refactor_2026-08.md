# The UI refactor 2026-08 — one widget vocabulary, apps as data, user apps possible

**Status: PLAN (2026-08-19).** This doc supersedes the *plans* in
`docs/ui_widgets_2026-08.md` (DRAFT v0.2) and `docs/shell_decoupling_2026-08.md`
(PLAN/OPEN) by folding both, plus four parallel adversarial reviews run against
them on 2026-08-19 (architecture / performance / app-authoring / execution).
Those two docs stay as the **evidence and analysis** they are — their
measurements, their inventories and their review ledgers are cited from here and
are not re-derived. What changes is the SEQUENCE and the SCOPE: roughly half of
the combined program is cut, and three things neither doc contained are added.

**The goal, in the owner's words (2026-08-19):** *"the whole UI surface [should]
become unified, make system apps easy to write and adding new user apps as well
… do a performance test on the p4 first so we are certain that we don't lose
performance."*

**The P4 baseline is MEASURED and recorded (§7).** It was taken before any of
this landed, on a board flashed from `dev` @ `b388cd3`, and the tooling's
run-to-run variance was established at the same time so that "no performance
lost" is a falsifiable claim rather than an assurance.

---

## 1. What is cut, and why (read this before proposing any of it again)

Four substantial pieces of the two source plans are **deliberately not built**.
Each was killed by evidence, not by budget; the evidence is recorded here so the
next session does not re-derive it.

### 1.1 The hover overlay — `ui_widgets` §4, W1, W3: CUT

The mechanism is written against a machine that no longer exists. §4's
per-backend table describes the web recording transport — cached command
streams, `{"same":1}` window entries, "no new wire semantics", the drop-report
keyframe law — and `docs/surface_model_v1.md` §13 retired all of it in the
2026-08-12 stage-4 sunset. The wasm head rasterizes now; a hover restore there
costs what it costs everywhere else, not zero.

§4's own postscript already concedes the perf case: *"The W1 gate is therefore
RE-SCOPED, not met … on the web the numbers no longer make the case."* The
alarming row that justified the whole mechanism (map tab, 14.55 ms/flip) was
mostly JSON serialization, and deleting the serializer collected the win — the
same flip now measures 2.63 ms.

On the P4 it is worse than unjustified, it is unsafe. The board's
`README.md` records that *the IDF PPA driver invalidates the whole out-picture
buffer at submit*, so an async op must be the frame's LAST framebuffer write —
and `_draw_app_window` returns early after registering the deferred stamp. A cue
drawn "after the frame's surfaces" lands after submit and before the kick, and
is silently discarded. The un-hover restore, if it routed through an async
strip blit, would break the invariant outright.

And the premise fails independently: with an HID mouse the cursor moves every
frame, `_ptr_state()` carries x/y, so `_needs_redraw` is True every frame and
`draw_stack`'s quiet gate (which requires `not pointer.visible`) is disabled
entirely. Cursor motion alone would cost a full surface repaint, which the
overlay does not address.

**Hover returns as a proposal** only when (a) USB-HID mouse lands on the P4
(#83), and (b) a hover-flip cost is re-measured on a tier that still pays for
one. Not before. **`pressed` is kept** (§4 below) — it is verified to add zero
extra paints and it is the state touch users actually need.

### 1.2 `ws._dirty` → `SurfaceSet` — `shell_decoupling` rows 7–8: CUT

`runtime/surface.py` is **denied on two of the three boards** —
`firmware/lilygo_t_deck_plus_mainline/board.toml:195` and
`firmware/guition_jc3248w535/board.toml:152`. So `SurfaceSet` cannot be "the API
that 157 `ws._dirty` references want"; a universal `ctx.damage` over it is not
buildable. Any damage role must be a new shared leaf with an epoch-only body on
the fullscreen tiers.

Row 7's claim to be *"behaviour-identical by construction … No perf change, no
risk"* is refuted. The raster early-return in `_surface_signals` is exactly what
makes the model free today; and `SurfaceSet` has no clear — `content_gen()` is
monotonic and each consumer compares its own stored gen, so a window that did
not draw this frame never refreshes its gen and reads dirty again next frame,
where one globally-cleared boolean had already been cleared. That is **more
repaints, not fewer**.

Row 8's ratchet ("no `ws._dirty` outside the damage owner", 188 write sites)
also contradicts `surface_model_v1.md` §3, which explicitly **retracts**
mechanical migration: un-audited sites stay global-epoch forever, attribution is
opt-in per site. The locked doc wins.

### 1.3 Row 9's `DrawKit`: CUT (dead premise)

`ws._btn`, `ws._icon_btn` and `ws._mini_btn` are **already one-line delegates**
onto `ui.game_btn` / `ui.game_icon_btn` / `ui.mini_btn` — that consolidation
shipped with the visual-identity work. There is no draw toolkit left to extract.
What remains is `ws._icon`, which holds real state (the IconSheet + its bar image
cache) and becomes `ctx.icons`; and `ws._glyph`, a `chrome._blit_glyph` shim.
Delete the delegates instead of extracting them.

### 1.4 Rows 10 and 11: OUT OF SCOPE

Row 10 (retiring hand-rolled invalidation on glass) is pure risk with no
unification payoff, and `shell_decoupling` §7 itself warns those mechanisms are
tuned on glass. Row 11 (one cart-verb registry) is unrelated to this goal.
Both remain legitimate future work; neither is on this critical path.

**Net: roughly half the combined program is cut.** What is left is the half that
actually serves the three product asks.

---

## 2. What is added (neither source doc had these)

### 2.1 There is no shell pixel golden. This is the blocker.

Both plans lean on a byte-identical golden set — `ui_widgets` §8.1/W0 names it
as W0's gate, and `shell_decoupling` §5 rules that its pure move-and-delete rows
must change no pixel. **That harness does not exist.** `tests/spec_conformance/hashes.json` covers the *cart* raster
only; `tests/test_responsive_editors.py` asserts `lay._base` structurally, not
pixels. The riskiest edit in the program — transcribing every hand-rolled widget
into the toolkit — would land unguarded.

Phase 0 builds it, and nothing else starts until it is green.

### 2.2 The cost of a new app is six hand-maintained lists, and no plan row touched them

`app_api_v1.md` advertises a 5-step checklist. The real cost is **8 files**, and
the per-app tax is concentrated in registration lists that are *code pretending
to be data*: the `console.py` import + construction + `register_app` block, the
`host_app.py` alias table, `tools/gen_device_carts.py`'s `CART_ORDER`, the
title→folder map in `tests/test_device_seed_parity.py`, and the web runner's
roster in `build.sh`. Four of the five fail **silently, on device only** —
forgetting `CART_ORDER` means the identity cart never seeds, so `is_app` never
claims, so the app is simply unreachable on hardware while working perfectly on
the host.

Every `system_carts/*/manifest.json` already carries `"system": true` and
**nothing reads it**. The one field shaped like an app declaration is inert.

This is the #161 move again — board staging became `board.toml` DATA and stopped
being a hand-edited list in a build script. The same move is available here, and
it removes 5 of the 8 files an app touches. **It must land before the apps are
migrated, or they get migrated twice.**

### 2.3 User apps are much closer than either doc admits

`type: "app"` carts already exist — eight of them ship — and already run with a
minimal exitable bar, the crash panel, and **permission-gated API injection**
(`wifi`/`net` are gated on manifest permissions in `player.py` today). `ui.py`
is already a pure leaf taking `(cv, th, rect)` with no shell imports, so it can
be injected into a cart namespace without a port.

What is genuinely missing is three things, not a new subsystem: a manifest
declaration, a binding to the **system** canvas instead of the fixed 320×240
game canvas, and `make_system_api` as a **filter over `AppContext`** keyed on
declared permissions. Plus a crash breadcrumb so a kid's broken app cannot
brick the console (#160-shaped, ~80 lines, needed for wallpapers anyway).

### 2.4 Three perf guard-rails the plans did not state

Measured on the repo's own unix MicroPython build and scaled by the P4 factor
the damage-model doc establishes:

- **Ban `property` forwards on the context roles.** A plain extra attribute hop
  is +0.5 µs; the same forward as a `@property` is +5.1 µs. Across Settings'
  137 per-frame accesses that is +69 µs versus **+700 µs**.
- **Mandate role hoisting.** The tree already hoists `ws = self.ws` 220 times,
  carrying 1,750 of the reach-ins. `surf = ctx.surface` at the top of `draw()`
  makes the indirection free. Neither doc mandated it.
- **Grids must NOT register per-cell rects.** `Hits` is used by exactly two
  surfaces today. Making it "the ONE state holder per surface" would have the
  map tab register ~395 rects per full draw where it registers zero — ~9.3 ms
  on the tab the ledger already calls the device's worst steady-state number.
  Scope `Hits` to bounded widget sets; grids keep arithmetic hit-testing.

---

## 3. The sequence

Each phase is one landed outcome, independently shippable and revertable, with a
ratchet that makes the old road impossible and a gate that proves it.

**Phase 0 — the shell golden harness.** SERIAL; blocks everything.
`tests/test_shell_goldens.py` + `tests/shell_goldens/hashes.json`: a hash of the
rendered system canvas for the launcher, the picker, Settings, each Editor tab,
each app, and the desk with one window. *Ratchet:* re-baselining requires an
explicit `--update-goldens` flag, so a pixel change is never silent. *Gate:*
green twice on an unmodified tree.

**Parametrize it over the axes that are currently unrendered anywhere**, because
each is a live blind spot verified 2026-08-19:
- **light variant** — `variant="light"` appears in five places in the suite and
  is *never followed by a draw*. A light panel could render black-on-black and
  the suite would stay green.
- **480×320** (Guition) — that tier's `sys_canvas ≠ canvas` arrangement has zero
  host coverage; it is asserted only in an on-glass test that skips without the
  board.
- **font_scale ≥ 2** — fs=2 renders but is never the pinned reference; fs=3 never
  draws a shell frame at all. This is `ui_widgets`' A-m6 finding, still open.
- plus 320×240/1× (T-Deck) and 1024×600 windowed (P4/host).

The existing checks it must not be confused with: `test_responsive_editors.py`
compares two workstations built *now* and asserts a layout attribute against the
constant in the file you just edited; `test_wm_windowed.py` compares two code
paths in the same commit. Those are live-vs-live A/Bs — a refactor that moves
both arms together passes green. **That is the exact failure mode this phase
exists to close**, and it is the family the damage-model doc already names twice
as the repo's recurring silent-cache bug.

**Phase 1 — enablers: DEFERRED, and the source doc's row 2 was already done.**
Verified 2026-08-19: `Layout`, `NAMES`, `color` and `_clamp_scroll` already live
in the leaf `runtime/chrome.py`; `Pointer`, `_in` and `_err_text` already live in
the leaf `runtime/widgets.py`. **Row 2 needs no new module** — what remains of it
is "consumers import from those leaves instead of taking them as constructor
arguments", which is cosmetic. Creating a new leaf instead would trip
`tests/test_staging_closure.py`, which pins the T-Deck↔P4 tier delta at exactly
`{wm_windowed.py, surface.py}`.

Row 1 (the meta-path import bootstrap) is **deferred off this critical path**.
Its value is enabling future extractions, not this goal — the one new module this
plan adds (`app_context.py`) costs two lines under today's convention. It is also
the single highest-collision sweep in the program (50 `runtime/` + 12 `device/`
files), so it would serialize every other phase behind it for no gain here. If it
is done later, its ratchet must be **narrowed to "no `from runtime.` inside an
`except ImportError`"** — ~21 of the 150 ladders are genuine optional-import
guards (zlib/deflate) and the source doc's "→ 0" would delete correct code — and
it must include a **clean-interpreter boot test**, because `tests/conftest.py`
installs the same finder at `sys.meta_path[0]` and every one of the 2,322 tests
therefore passes whether the production finder works or not.

**Phase 2 — the bar contract becomes a host guarantee** (`shell_decoupling`
row 5). The router draws the strip after `draw()` and routes the tap before
`handle_pointer()`, deleting the "forget it and your app is unexitable" bug
class.

**Scope it precisely — the strip is not one thing.** There are **seven** strip
kinds in the tree (`tool` ×7 apps, `menu` ×8 editor surfaces, `settings`,
`home`, `picker`, `desk`, `desktop`), and a hoist that collapses them picks one
and silently breaks the context-X on the others. The router owns **`"tool"` for
registered apps only**; every other kind stays exactly where it is.
*Ratchet:* **behavioural, not a call-site count** — register a stub app that
never draws a strip, drive a frame, assert the strip drew and that a tap on the
X exits; run it per registered app kind.

**Phase 3 — the widget vocabulary.** This is the unification.
- **3a (SERIAL, `runtime/ui.py` only):** add the `row` and `cell` kinds, the
  `disabled` state, and the precedence `disabled > pressed > hot > on > rest`.
  Absorb the three live private button copies (`writer_app._hist_btn`,
  `sheets_app._icon_btn`, `code_layer._panel_btn`).
- **3b/3c/3d (PARALLEL, disjoint file groups):** convert the ~13 hand-rolled row
  draws and ~16 grid/cell draws — including three independent tile pickers — onto
  the kinds. *Ratchet:* a grep test that the hand-rolled selection idiom
  (`cv.rect(...)` + `cv.print(...)` as a row/cell) exists nowhere outside
  `ui.py`. *Gate:* goldens byte-identical + the P4 bench within noise (§7).

**Phase 4 — skins as data.** A new leaf `+runtime/skin.py` (NOT in `chrome.py`,
which would create the cycle `ui → chrome → settings_layer → ui`), with
**nested** pre-flattened tables `SKIN[kind][state]` — nested, because a single
flattened `kind + ":" + state` key allocates a string per draw. Default skin is
the current pixels, pinned by Phase 0. Then a second skin plus an Appearance row
as the falsifiable restyle proof. *Gate:* P4 editor-tab frame time within noise
— the tabs are dispatch-bound, so a per-draw dict get is the one place this plan
can lose P4 performance.

**Phase 5 — the app registry becomes manifest data.** An `"app"` block in the
identity cart's manifest (id, title, entry, min_size, text_mode, order,
targets); one loader replaces the five hand-maintained lists. *Ratchet:*
`CART_ORDER`, the title→folder map, the web roster and `console.py`'s app block
contain no app names.

**Phase 6 — `AppContext` with declared needs.** The corrected role set (§5),
with each app declaring `NEEDS = (...)` from day one and a test asserting it
touches only what it declares — that is what turns `make_system_api` into a
filter over an existing interface rather than a new axis invented later.
Migrate Calc first (the reference), then **Sheets second, not last** — it is the
shape-breaker with 33 private reach-ins, and finding the roles wrong on app two
is far cheaper than on app seven. *Ratchet:* no `ws.` in the migrated app
modules. *Note:* the source doc's stop-condition grep is unsatisfiable as
written — its glob catches `editor_app.py` and `host_app.py`, neither of which
is in scope.

**Phase 7 — user apps.** `make_system_api(ctx, cart)` as a permission-keyed
filter over `AppContext`; system-canvas binding for `type:"app"` carts (fixed
size by default, responsive by opt-in when the cart defines `_layout(w,h,fs)`);
`ui` and `theme()` injected as cart globals. *Gate:* a hand-written app cart
with `"permissions": ["files"]` opens, draws with `ui`, saves a document — and a
copy of it *without* the permission finds no `files` name at all.

**Phase 8 — crash isolation.** The #160-shaped breadcrumb: mark the app id in a
sidecar before `open()`, clear it after N successful painted frames, three
strikes disables it and shows it as broken in the picker. *Gate:* a cart that
raises on every open is disabled after three boots.

**Phases 0–4 make the UI one surface. Phases 2, 5, 6 make system apps easy.
Phases 7–8 make user apps possible.**

---

## 4. What is kept from `ui_widgets_2026-08.md`

Kept: §3.1's `Hits` growth **scoped to bounded widget sets**; §3.2's six-state
model and its precedence; §3.3 skins-as-data with the pre-flatten mandate
(corrected to nested tables and moved out of `chrome.py`); §7's design
principles (no layout shift, token-derived, quiet by default); §3.3's
transcription honesty (the frozen per-kind code branches stay code and are
documented as such).

Kept and verified: **`pressed` adds zero extra paints** — `_ptr_state()` carries
`down`/`click`, so any button activity already fails `_content_static`. This is
the one perf claim in that doc that survived checking, and it is the state that
matters for touch, where hover is meaningless by nature.

Dropped: everything in §1.1 above, plus the un-hover byte-equality oracle, the
per-tier restore hooks, and open questions 1/3/4 (all downstream of the cut
mechanism).

---

## 5. The corrected `AppContext` roles

The source doc's six roles cover ~83% of the seven apps' real surface, not
">90%". Corrections, derived from the actual consumers rather than from Calc:

- **`ctx.store` splits.** It conflates the cart store with the user-files store.
  Apps use the second almost exclusively; only Storybook uses the first.
  → `ctx.files` (list/load/save/rename/duplicate/delete/trash/restore/new_name
  + history) and `ctx.carts` (all/by_id/open_editor).
- **`ctx.chrome` dissolves.** Its only 14 uses are the bar contract, which
  Phase 2 makes a host guarantee. A role whose entire content is "the host does
  this for you" is not an interface.
- **`ctx.surface` must carry pointer state.** `handle_pointer(px,py,click)`
  exposes no `down`/`held`, so apps reach `ws.pointer` directly today — a
  drag-based app *cannot* be written on the declared API.
- **Missing entirely:** `ctx.prefs` (namespaced per app id), `ctx.history` (the
  shared bar UNDO/REDO needs the app to expose it; convention only today),
  `ctx.notify` (achievements, notices, status toasts), and **lifecycle** —
  there is no `close()`/`on_exit()` hook at all, so an app that persists on an
  idle debounce loses unsaved work on exit and nothing enforces otherwise.
- **`ctx.nav` must include app-to-app.** `files_app` already calls
  `ws.writer_app.open_named(...)` across five sites — an explicit `app_api_v1`
  non-goal that happened anyway, because there was no seam for a real product
  need. Give it one.
- **Deferred:** clipboard and audio roles. No app uses `ws.audio`; clipboard is
  one comment. Add each when a second consumer appears.

`ctx.files` returns `(ok, err)` rather than raising — that is what
`app_shell._persist` already hand-rolls in three apps.

---

## 6. Out of scope, deliberately

- **`wm_windowed.py` as a refactor target.** 75 distinct `ws.*` names, 34
  private, and the home of the most expensive on-glass tuning in the repo. `shell_decoupling`
  §6's "never is a legitimate answer for this file" stands. Phase 3d touches its
  *chrome draws* only.
- **#203 (the PPI chrome-tap floor).** The owner sequenced it explicitly after
  the UI refactor, and CLAUDE.md records that. Phase 4's metrics layer is the
  seam it needs; it is not a step here.
- **The facade and barrel call sites in `tests/`.**
  `shell_decoupling` row 3's freeze-don't-migrate call was correct and is
  adopted unchanged.
- **Making Paint, Sheets, Writer and Files into carts.** An app stays shell code
  when it owns memory the Player cannot account for, or when it is the recovery
  path. Files is the surface that un-breaks things; Paint holds an off-heap
  document on PSRAM-sensitive blit paths. Calc, Storybook and Appearance are the
  migration candidates.

---

## 7. The perf contract

The baseline is in §7 of this plan's companion record (scratch: `BASELINE.md`,
to be folded into #58 when the refactor lands). Two consecutive full
`tools/p4_bench.py` runs on the flashed `dev` build agreed to **≤1 ms on every
median and p90**, so on that tool:

- **≥2 ms median shift is signal; ≥3 ms is a regression to explain.**
- Every `* idle` scenario captured **zero** frames on both runs. That is the
  redraw gate working, and it is itself a regression check: if an idle scenario
  starts capturing frames after a step, that step began dirtying the shell every
  frame.
- `tools/p4_surface_sweep.py` percentiles quantize in 4 ms buckets on the low
  edge — use it for coverage, use bench medians as the sharp instrument.
- Per-cart fps (`tools/p4_perf.py`, diag OFF) is the **control**: a UI refactor
  must not move it at all. If it moves, the change was not where we thought.

CI-side, with no board, the repo's precedent is a **counter budget, never a
clock** (`test_top_bar.py` caps allocs and renders over 24 frames *with a lower
bound first*, so a deleted counter fails). Three new budgets in that shape:
`hits.add` calls per full draw per surface (capped, with a floor); zero
`property` forwards on the context roles; and idle-frame paint count unchanged.

Gates: Phases 3 and 4 require a P4 bench run against the baseline before
merging. Phases 5–8 require `tests/test_p4_on_glass.py` green.

---

## 8. How this is executed

**Waves, with exclusive file ownership.** `runtime/console.py` (5,635 lines) is
written by most phases and `runtime/ui.py` by the widget half; those are the
choke points. Work is assigned so that **no two concurrent agents write the same
file**, and any phase that must touch a choke file runs alone in its wave.

**Verification ladder** (an executing agent runs 1–4; the integrator runs 5–6),
with measured wall-clock:

1. `pytest tests/test_spec_conformance.py` — **0.6 s**. Non-negotiable; catches
   raster damage instantly.
2. `pytest tests/test_staging_closure.py tests/test_board_toml.py` — **10.4 s**.
   Mandatory for any phase that adds, deletes, renames or re-imports a
   `runtime/` module.
3. the new golden set + the touched files' own tests.
4. `make test` — **31.7 s** under xdist (2,322 pass / 46 skip).
5. `python tools/simulate_desktop.py --demo` — the only lane that uses the
   *production* import path rather than conftest's finder. **Note it currently
   exits 0 while printing frame errors**; treat any printed error as a failure
   until that is fixed.
6. a real firmware build (`make firmware-build-p4`, `…-guition-s3`) after any
   phase that changes the frozen module set — it is also the only **size** gate,
   and the P4 has ~0.5 MB of app-slot headroom.

**Revert rather than fix forward when:** a phase's stop-condition number does not
move; a golden changes and the agent cannot say in one sentence which pixel moved
and why; a fix needs a file outside the wave's ownership map; or two consecutive
fix attempts on the same phase fail. One phase is one squashed commit on `dev`,
so revert is total.

**Two staging traps to brief every agent on.** `runtime/` staging is a
**denylist over a non-recursive `glob("*.py")`**: a new file crosses to all four
targets by default, and a new `runtime/<pkg>/` **directory is silently never
staged** — so no sub-packages. And the closure test has a swallow hole: a guard
written `try: import x / except ImportError: x = None` is invisible to it, so
swallowing import guards are forbidden by ratchet.

**Do not push more than one phase per CI cycle.** The firmware workflow is
path-filtered with `cancel-in-progress`, so a burst of commits builds only the
last one and a staging break in an early phase gets attributed to a later one.
