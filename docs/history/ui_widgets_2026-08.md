# The widget system 2026-08 — one vocabulary, swappable skins, hover as an overlay

**SUPERSEDED 2026-08-19 by
[`docs/history/ui_refactor_2026-08.md`](ui_refactor_2026-08.md)**,
which folded this doc and cut about half of the combined program on evidence
(its §1 is the ledger of what was cut and why). This one stays for what it IS —
the measurements, the widget inventory and the review ledger the refactor cites.

**Status: DRAFT v0.2 (2026-08-04)** — v0 went through the parallel adversarial
architecture + perf review pass (verdicts: **REDESIGN (scoped)** / **PERF CASE
NOT ESTABLISHED**); this revision folds all findings — **§12 is the
traceability ledger**. The reviews upheld the state-holder, state-model, and
skin halves and killed v0's "Tier 1" descriptor-replay repaint; its
replacement (§4, the hover overlay) is the mechanism the `hover-shelf`
corpus itself validated. Nothing here is built. **Tracks:** #177 (hover —
shelved pending this), #163 (span kernels), #113 (scroll), #73/#105
(windowed WM). The **`hover-shelf` branch** (15 commits, 909 insertions
across 32 files incl. tests) is the evidence corpus.

**Relation to prior docs.** Subordinate to `docs/surface_model_v1.md` — the
compositor contract, dirty protocol, L7 AND L10 are untouched (v0 proposed an
L7 amendment; v0.2 needs none — §6). `docs/visual_identity_v1.md` keeps
governing how the console looks; this doc builds the vehicle that makes that
look (and the next one) swappable data. `docs/ui_damage_model_v1.md`'s
verdicts and §0.2 blockers are treated as settled fact. Claims are labeled
**MEASURED** / **ESTIMATED** (arithmetic shown) / **OWNER-REPORTED** /
**PREDICTED** (with the gate that settles them).

---

## 1. Why now — three product asks, one root cause

**The hover experiment (2026-08-03, owner verdict).** #177 was built end to
end on `hover-shelf`: pointer hover on desk icons, window chrome, taskbar
chips, Settings rows, file grids, app buttons, the Editor tab ladder, sprite
swatches, map/scene brush ghosts. Three findings shelved it:

1. **It was hand-wired per surface** — and four of the bugs found were the
   SAME bug rediscovered per surface (seeding a parked cursor, window-local
   vs screen coords, dirty-on-change-only, stale rects after relayout), plus
   one coupling class (the Editor-zone hover had to know to invalidate the
   bar strip cache — the silent-cache bug family).
2. **It felt slow** (OWNER-REPORTED on wasm, the fastest tier), because a
   hover flip repainted the WHOLE surface. Now MEASURED (§4): 8ms on the
   code tab, 18–25ms on the map tab per flip — the most expensive frame
   class made the most frequent one.
3. **It looked janky** (OWNER-REPORTED): per-surface ad-hoc treatments,
   because there was no designed state vocabulary — only a `hover` token
   bolted onto seven themes.

**The restyle requirement (owner, 2026-08-04): "we'd like to easily change
the current UI look/style."** The color half is in decent shape — the seven
former local `_button` copies are already one-line delegates onto `ui.chip`/
`ui.game_btn`, so tokens reach them. What is NOT restylable today: widget
METRICS (edge thickness, pads, strip heights, label centering quirks) and
SHAPES are hardcoded inside the draw functions; per-state looks don't exist;
and a real tail of hand-rolled widgets remains outside the toolkit
(`writer_app._hist_btn`, the Settings/list rows, every grid-cell family —
§8), where a restyle still means hunting code.

**Touch has no press feedback.** A finger on a button changes nothing until
release. Hover is cursor-only by nature; *pressed* is universal — and the
current widgets cannot express it (only `on`/`hot`, both caller-computed
semantics, not interaction states).

One root cause: **the chrome has widget functions but no widget system** —
no interaction-state model, no style/structure separation, no cheap way to
show a state flip. Three additions to `runtime/ui.py` close the gaps.

**Where each tier stands** (owner clarifications, 2026-08-04): the **S3 is a
full library member** — same staged `ui.py`/`chrome.py`, so every skin,
state, and GUI app lands there; it is excluded ONLY from §4's overlay-restore
machinery, because with no desktop its surfaces are fullscreen 320×240 and
the full repaint it already does is the cheap path (surface model L6). The
**P4 is touch-only today, and USB-HID mouse support is coming** — when it
lands, hover arrives on the board whose full repaints are the priciest
(~32–72ms post-#159), which is exactly why the overlay must exist first.

## 2. What exists — the base the library grows from

- **`runtime/ui.py`** — the immediate-mode toolkit (visual identity Phase 3):
  `button`/`chip`/`tab_row`/`status_row`/`panel`/`dialog`/`text_field`/
  `mini_btn`/`game_btn`/`game_icon_btn`/`toolbar`/`focus_ring`, the pure rect
  algebra, `Hits` (draw==tap), `ScrollRegion`+`DragTap`. Its four design
  rules (module docstring) all survive. The library is ui.py's next stage,
  not a replacement.
- **`chrome.THEMES`** — 6 families × dark/light, base tokens + §4.3 semantic
  roles via `_SEMANTIC_ALIAS`, flattened once per theme×variant into
  `_THEME_CACHE` (concrete ints, zero per-draw derivation). That flatten
  pattern is normative for skins (§3.3).
- **The per-surface Layout classes** — frozen `_base` branches byte-identical
  at 320×240/1×. Any change to a shipped widget's REST rendering is gated on
  those goldens.
- **The `hover-shelf` corpus** — shapes to absorb: the `Hits.hover`/
  `hover_frame` pump; the `hover` theme token with its `dim` alias fallback;
  the visible-or-hovering-and-not-down pointer gate; the WM's hover CLAIMING
  (a window above the desk claims its footprint; chrome hover resolves
  topmost); and — the load-bearing precedent for §4 — the desk-icon hover
  that draws AFTER the backdrop-cache stamp so the cache stays lift-free,
  with the restore coming from the retained pixels.
- **Staging reality:** `ui.py`/`chrome.py` are staged to BOTH boards and the
  wasm build; `wm_windowed.py` is host/P4 only. Everything added must stay
  MicroPython-safe and allocation-light; the S3's added cost is pinned by a
  **behavioral counter test** (zero overlay/record bookkeeping executes
  there), not a grep — the code is staged there, so "not reachable" must be
  proven at runtime, not by file absence.

## 3. The model — three additions

### 3.1 Widget identity and the state holder: `Hits` grows

`Hits` already gives every interactive rect a per-frame identity: the
`(verb, arg)` pair its draw registers. The library makes the registry the
ONE interaction-state holder per surface:

```python
class Hits:                      # grown in place; no rename
    hover    # id under a resting cursor, or None   (shelf-proven)
    pressed  # id under a pointer that went DOWN on it and is still inside
    def pointer_frame(px, py, pointer) -> bool  # ONE pump: hover + pressed;
        # True when any state changed -> the caller marks dirty / the WM
        # arms the overlay (§4)
    def pointer_leave() -> bool  # the WM routed the cursor elsewhere:
        # drop hover (and pressed), report if that changed anything
    def state_of(id) -> ("hover"|"pressed"|None)  # read back at draw
```

Alongside each hit rect the draw registers the widget's **extent** (rect +
skin-declared cue margin — `focus_ring`'s geometry already computes this
shape) and its **cue class** (§4). That is the whole retained record: rect,
extent, cue, id — no paint arguments, no replay descriptors (v0 had them;
review finding A-M4 made them unnecessary). Storage is the existing `_items`
list, reused via `clear()`; the additions are appended into the same tuples,
and an allocation budget test pins the per-frame churn (the S3's gesture
frames currently take zero collects and must stay that way — P-M2).

Rules, each one a shelf bug made structural:

- **Ids are the draw's `(verb, arg)` pairs.** Duplicate pairs were harmless
  for taps (topmost-at-point wins) but ARE ambiguous for `state_of` — a
  debug-mode check flags duplicate ids on registration (A-m2); last
  registered wins in release.
- **Rects live from one full draw to the next**: `clear()` at the top of
  each full draw wipes them; hover/pressed ids persist across clears and
  re-resolve against the fresh rects. A parked cursor re-seeds on the next
  pointer sample, never on first sighting.
- **Coordinates are surface-local**, matching what the draw registered.
- **Hover requires a pointing cursor**: `visible or hovering, and not down`.
  Touch never hovers; touch DOES press. Pressed clears on release or on
  leaving the rect. (The `hovering` pointer flag and its web delivery exist
  on `hover-shelf` only and re-land with W1 — they are NOT on the clean
  branch today; A-M7.)
- **Leave is the WM's job** (A-M1): the shelf's stale-hover class — a
  surface the cursor left, or that lost the routing, keeps its cue forever —
  is closed by contract: whichever component routes pointer samples
  (`wm_windowed`, the fullscreen stack, an app hosting sub-layouts) MUST
  call `pointer_leave()` on the surface it stops feeding. The WM's hover
  CLAIMING (topmost footprint wins, from the shelf) stays WM-central.
- **The pump is the only writer** of hover/pressed. Surfaces call
  `pointer_frame` from their pointer handler and read states at draw.

**What the pump does NOT own: hover-as-selection** (A-M5). The shelf's
Settings rows and file grids implement *preview-select* — hover MOVES the
selection (`set_msel`), which is app state, marks dirty, and repaints via
Tier 0 like any state change. That is a per-surface UX choice layered ON the
pump (the pump detects the target; the surface decides selection follows
it), and it never rides the §4 overlay. The launcher's trackball `_lhover`
is the same model and stays as-is. The doc's honest migration claim is
therefore: *cue-hover* is two mechanical lines per surface; *preview-select*
surfaces keep their (small) bespoke glue.

### 3.2 The state model

Six states, resolved in one place, precedence:

```
disabled > pressed > hot > on > hover > rest
```

- **rest** — the widget as the goldens know it. Byte-identical to today.
- **hover** — cursor tiers only; rendered as an ADDITIVE overlay cue (§4),
  never a field/ink swap. Skins pick the cue; §7's principles bound it.
- **pressed** — pointer down inside; all tiers including touch. Rides Tier 0
  (a down/click frame already forces full live repaints — verified against
  `console._needs_redraw` and `wm_windowed._content_static`, which fire on
  down AND click this-frame-or-last, so pressed feedback adds ZERO extra
  paints; P-m3). Latency bound stated honestly: on the P4's heavy tabs the
  cue lands one full draw (~30–70ms) after the finger.
- **on** — the caller's semantic toggle/selection (today's `on=`). Unchanged.
- **hot** — the armed-destructive look (today's `hot=`). Unchanged.
- **disabled** — dim-ink, non-registering. This is NOT hypothetical (A-M6):
  `writer_app._hist_btn`, `paint_layer._draw_tools`, and the Editor bar's
  history icons already hand-roll disabled ink; the state absorbs them.

`on`/`hot`/`disabled` remain CALLER arguments (semantics); `hover`/`pressed`
come from the registry (interaction). Keyboard focus traversal is out of
scope (§10); `focus_ring` stays the caller-driven treatment it is.

### 3.3 Style as data — skins

A **skin** is a data table in `chrome.py` beside `THEMES`, three layers:

1. **Tokens** — the existing THEMES families (unchanged; a skin resolves
   colors through the same semantic roles, so all 12 family×variant sets
   keep working under any skin).
2. **State table** — per widget kind × state → paint deltas (field token,
   ink token, edge token, edge weight, cue class for hover). A skin that
   names no hover entry derives it through the alias chain (`hover` → `dim`,
   edge cue → `focus`), so themes never need per-state hand-tuning — the
   janky-look failure was per-surface improvisation; the fix is one choice
   per skin.
3. **Metrics** — the numbers hardcoded in draw functions today: pads, edge
   widths, strip heights, scrollbar width, label alignment.

**Resolution is pre-flattened, mandatorily** (P-M4): like `_THEME_CACHE`, a
skin×theme×variant resolves ONCE into flat per-kind tuples of concrete ints;
the per-draw cost is an interned-key dict get + int indexing — no tuple-key
hashing, no `th.get` chains, zero per-draw allocation. The S3 (which
full-repaints everything, forever, and gains nothing from any of this) is
the budget: the flatten must leave its per-widget draw cost within noise of
today's, pinned by a µs-budget test beside the existing golden gates.

**Transcription honesty** (A-m1): the default skin IS the current pixels,
transcribed literal-by-literal and pinned by the goldens — but not every
quirk is data. `game_btn`'s frozen scale-2-print-center-8 branch,
`text_field`'s fixed-1× metrics, and `mini_btn`'s unscaled pads stay as
per-kind CODE branches the skin table points at, documented as such. The
restyle promise is scoped accordingly: a skin restyles within the indexed
canvas's primitive vocabulary (fills, edges, glyph text, sprite icons); a
skin that changes widget SIZES re-flows layouts and re-baselines goldens —
a deliberate versioned act, not a data tweak.

## 4. Hover is an overlay — the perf mechanism

v0 proposed per-widget descriptor replay ("Tier 1"). Both reviews broke it:
the web patch stream was protocol-incompatible (the page replays retained
command STREAMS — a patch entry would replace a window's cached stream and
erase its body on the next mixed frame), the self-bounding premise was false
for real widgets (`tab_row`'s inactive tabs paint no field at all), and
root-canvas chrome (chips, desk icons, window strips) had no buffer to
replay into. All three die together with the mechanism, because the ONLY
frame class replay served was the hover flip (pressed rides Tier 0) — and
the shelf already solved hover flips a cheaper way, on the desk icons
(A-M4). Generalized:

> **The hover cue is drawn AFTER the frame's surfaces, from the registry's
> (extent, cue) record; no retained buffer ever contains a hover pixel; the
> un-hover restore comes from each backend's EXISTING retained pixels.**

Per backend:

| backend | cue draw | un-hover restore | why it's cheap |
|---|---|---|---|
| host sim | post-composite draw pass (like the cursor) | re-stamp the vacated extent from the layer's buffer | buffers are cheap; reference implementation |
| P4 | same pass, after backdrop/chips/window stamps | `blit_strip_rect` re-stamp of the vacated extent from `win.buf` / backdrop cache / bar strip cache — all existing retained sources | a hover flip stamps a few KB instead of re-rendering a 30–70ms surface; the shelf's desk hover already ran exactly this shape ("hover draws AFTER: the cache stays lift-free") |
| web / wasm | the cue records into the WM's **overlay layer**, which is already its own Stage-9 surface entry drawn last; a hover flip changes ONLY that surface's tiny stream | free: the page replays every surface from its cached streams each frame, so the window's cached stream repaints the vacated pixels with no work from us | window content stays FROZEN (`_content_static` — a hover move is position-only pointer activity), so windows ship `{"same":1}`; **no new wire semantics** — the overlay is an ordinary surface entry |
| S3 | the SAME shared cue-draw helper, called by `FullscreenStackWM` at the top of its stack — a state flip marks dirty and rides the normal 320×240 full repaint | none needed — the full repaint redraws everything, so "restore" is what the frame does anyway | the full repaint IS the cheap path (L6); trackball cursor hover works for free through the same pump; preview-select surfaces keep their app-state model |

**One codebase, not a tier fork** (owner question, 2026-08-04): the cue
DRAW is one shared helper both WMs call at the top of their stacks; the
surfaces, widgets, pump, and skins are identical on every tier. The only
per-tier code is the un-hover RESTORE hook — retained-pixel stamps on the
windowed tiers, nothing on the S3 (its full repaint is the restore). That
is the shipped pattern, not a new split: the content freeze, backdrop
cache, and stamp-defer are already windowed-WM-only while the S3 stays
full-repaint, and surfaces already don't know which tier they run on.

**Why this is architecturally free:** the overlay is a tiny top-of-stack
draw — the same class as the cursor, which the surface model already
blesses ("a tiny surface whose motion is pure place_gen ... this model's
cheapest advertisement", §3). No per-widget damage exists; no retained
structure exists; L7 needs no amendment (§6). The restore path's validity
condition is exactly the shipped `_content_static` predicate — including
its down/CLICK-this-frame-or-last leg and the `_buf_stale` leg (A-M2): on
any frame that predicate says the retained pixels aren't trustworthy, the
un-hover restore is simply a normal dirty repaint. The doc defines no new,
weaker gate; the shipped predicate is the authority.

**The additive-cue constraint this buys.** Overlay hover means the cue must
DRAW OVER the rest-state widget: an edge ring in the skin's hover/focus
color, a corner glyph, an underline — never a field swap or ink change
(those pixels can't be un-drawn without a repaint). The shelf's shipped cues
already fit (button/chip hover was an edge swap, and the edge is the
widget's topmost paint, so over-drawing it is pixel-identical to redrawing
with hover state). Field-lift hover survives only as *preview-select*
(§3.1), which repaints via Tier 0 because it IS a state change. §7 makes
the constraint a design principle rather than a caveat.

**MEASURED baseline — SUPERSEDED 2026-08-12, both columns.** The original
(2026-08-04) measured the wasm RECORDING tier: a console half that
serialized draw commands to JSON and a page half that replayed them. Moycore
stage 4 deleted both — the wasm rasterizes and the page blits a framebuffer —
so those numbers describe a machine that no longer exists and are struck
rather than edited. They are kept here because the *argument* they support
(what a hover flip costs, and therefore whether an overlay is worth it) is
unchanged; only its arithmetic moved.

~~2026-08-04, recording tier: desk quiet 0.09ms / desk painted 1.92ms +
1.01ms replay / 380 B; code tab 5.29ms + 2.66ms / 15.1 KB; sprites 3.58ms +
2.66ms / 9.9 KB; map 14.55ms (p95 22.0) + 3.18ms / 67.4 KB.~~

**MEASURED (2026-08-12, raster tier, same `hoverperf.mjs` on the same
machine; console half = `step_frame_json`, i.e. the shell drawing the frame.
The present is now a FIXED cost — one heap copy + one 565→RGBA pass, ~1.5ms
at 1024×600 — independent of what changed, so there is no per-surface page
column and no bytes/frame at all):**

| frame class | console |
|---|---|
| desk, quiet | gate closed, no paint |
| desk, forced-dirty (hover-flip class) | **0.37ms** mean (p50 0.25, p95 0.36) |
| editor code tab, hover-flip class | **2.61ms** mean (p95 2.92) |
| editor sprites, same | **1.00ms** mean (p95 0.94) |
| editor map, same | **2.63ms** mean (p95 3.01) |

The shape of the problem changed with the numbers: the map tab was the
alarming row at 14.55ms with a p95 of 22.0 and 67.4 KB/frame, and it is now
2.63ms with a p95 of 3.01 and no wire at all. What that row was mostly
measuring was JSON serialization and command dispatch, not drawing.

*(The old paragraph here attributed the map tab's 14.55ms to ~6.4ms of JSON
serialization plus ~8ms of draw/record dispatch, and predicted that an
overlay would take a map-tab hover flip from 18–25ms to 5–6ms end-to-end.
Stage 4 collected most of that win by deleting the serialization outright:
the same flip now measures 2.63ms with a p95 of 3.01.)*

**The W1 gate is therefore RE-SCOPED, not met.** The overlay was justified
by a cost that has largely evaporated on the wasm tier, so what remains to
be shown is whether hover-flip repaint is expensive on the tiers that still
pay for it — the S3 especially, where a full editor repaint is a real frame
budget and no JSON was ever involved. Re-measure there before building the
overlay; on the web the numbers no longer make the case.

**The correctness oracle (A-B3).** Every overlay pilot ships with the
un-hover equivalence test: hover a widget, un-hover it, assert the frame is
**byte-identical** to a run that never hovered — per tier (host pixels; web
retained index buffer via the pageshot harness). `ui_damage` §0.065's
verification note (oracles that fail on known-good code) is the checklist
for building it; it runs in CI, not harness-mode-only, because overlay
streams are cheap to compare.

**Drop behavior (web).** A dropped overlay frame strands at worst a stale
cue, repaired by the next hover change or the existing drop-report keyframe
law (surface model §6) — never a stranded window body, because window
streams were `{"same":1}` all along. The device webserver keeps hover OFF
(§11 Q1): its cursor tier barely exists and its keyframe repair is
expensive at ~72–137KB/s.

## 5. Input routing

One pump: the surface's pointer handler calls
`hits.pointer_frame(px, py, ws.pointer)` before its tap logic; taps keep
resolving through `at()` exactly as today (zero behavioral change to
shipped surfaces). The WM owns leave (`pointer_leave` on route-away — A-M1)
and hover claiming (topmost footprint, from the shelf). Touch tiers get
pressed feedback through the same call. The web `hovering` pointer state
re-lands with W1 from the shelf (transport commits `f7f6b53`/`ca57346`);
until then the pump's hover gate simply never opens on those tiers.

## 6. Reconciling with the corpus — nothing to amend

v0 proposed amending surface-model L7. v0.2 does not need to:

- **No retained widget tree** — the registry retains four small fields per
  widget, rebuilt every full draw, structure-free. Unchanged from v0.
- **No per-widget damage** — GONE with descriptor replay. The overlay is a
  top-of-stack draw plus a restore-stamp from existing retained pixels;
  from the surface model's view it is the cursor pattern: a tiny overlay
  whose changes ride the normal painted-frame path. L7 stands as written.
- **No new invalidation mechanism** (L10) — the web overlay is an ordinary
  Stage-9 surface entry; the P4 restore uses the shipped stamp machinery;
  eligibility is the shipped `_content_static` predicate, not a new gate.
- **The graveyard stays buried** — no inferred static content (the freeze
  legs are the shipped, producer-owned ones); no damage clips (`clip()`/
  `cls` never enter); Fold-2 undisturbed; the ~179-site `_dirty` audit not
  required (un-attributed dirt just means a normal full repaint, which the
  restore path treats as truth arriving).
- **`moy_alloc` has no free** — no new buffers anywhere; the overlay draws
  on the root after the stamps, exactly where the cursor draws today.

## 7. The visual state vocabulary — designed once

Implementation lives here; the DESIGN authority stays `visual_identity_v1`,
whose §5.2 (focus and selection) and §6 (component contracts) gain the
state vocabulary when the default skin's state table is written — per-state
rows in the existing component contracts, not a new document. Principles
the shelf's jank taught, now with §4's mechanism behind them:

- **No layout shift, ever.** Hover/pressed change paint, not geometry.
- **Hover cues are additive** (§4): drawn over the rest widget — edge ring,
  corner glyph, underline. One cue per skin, applied to every widget kind.
  Field-lift "hover" is preview-select, a selection semantic (§3.1).
- **Token-derived, never new literals.** Every cue resolves through the
  semantic roles; the 12 theme sets keep working unmodified.
- **Quiet by default.** Pressed may reuse the accent look; `hot` stays the
  only loud state.

## 8. Inventory and migration map

Corrected against the code (A-M6): the seven local `_button`s are ALREADY
delegates onto `ui.chip`/`ui.game_btn` — that consolidation shipped with
the visual-identity work. The real remaining inventory:

| kind | today | the gap |
|---|---|---|
| chip / button / tab row / status / dialog / panel / toolbar / text field / stepper | one implementation in ui.py (+ delegates) | state model + skin routing |
| history chip | `writer_app._hist_btn` + two sibling disabled-ink sites | absorb into `chip(disabled=)` |
| list rows | hand-rolled (Settings, wifi, file lists) | a `row` widget kind; Settings keeps preview-select semantics |
| grid cells | hand-rolled (picker cards, file grids, sprite swatches, theme/wallpaper tiles) | a `cell` kind; the picker keeps its card art, gains cue-hover |
| scrollbar | `ScrollRegion.draw_bar` | skin metrics only |
| root-canvas chrome (taskbar chips, desk icons, window strips) | WM-drawn | pump lives in the WM (as on the shelf); cues ride the §4 overlay — these were §1's motivating surfaces and are first-class here (A-M3) |

Adoption order (each step independently shippable):

1. **Core + default skin** (§3): goldens byte-identical — **including
   windowed-size, light-variant, and fs≥2 baselines** (A-m6: the 320×240
   dark goldens structurally can't see a transcription bug elsewhere);
   `_hist_btn` + disabled-ink sites absorbed; S3 µs/alloc budget tests.
2. **Overlay pilot: the Editor tab ladder + Writer toolbar, host + wasm**
   (the surfaces the owner felt the lag on; Writer exercises `disabled`).
   Gates: the §4 ≤8ms p95 map-tab prediction; the un-hover byte-equality
   oracle green on both tiers; taps/goldens unchanged.
3. **Sweep the chrome surfaces** — WM chrome (chips/icons/strips, from the
   shelf), Calc, Sheets, Storybook, Files, Appearance; preview-select
   surfaces (Settings rows, file grids) keep their model, now over the
   pump. The shelf commits are the test oracle for where cues belong, not
   code to rebase.
4. **P4 overlay — sequenced behind USB-HID mouse support** (owner: coming).
   Hover on the P4 is only viable WITH the overlay (~32–72ms full repaints
   post-#159); gate: #156 on-glass suite green, hover-flip cost measured,
   drag fps not regressed.
5. **The second skin** — the restyle proof: a real alternative look shipped
   as data + an Appearance row, zero surface edits (metrics quirks per
   §3.3 excepted and named). If it needs surface edits, §3.3 failed and
   this doc gets revised.

## 9. Phasing summary with gates

- **W0 — library core** (§8.1): expanded golden set byte-identical; S3
  behavioral budget tests (zero overlay bookkeeping executes; per-widget
  draw µs within noise; zero added collects on a gesture).
- **W1 — overlay pilot** (§8.2): baseline already MEASURED (§4 table);
  gates = ≤8ms p95 map-tab hover flip + the byte-equality oracle + a
  throttled/dropped-frame run (`pageshot` `{"drop":N}`) showing no stranded
  window pixels (P-M3).
- **W2 — sweep** (§8.3): every surface lands with the two-line pump (or its
  declared preview-select glue); no bespoke cues survive review.
- **W3 — P4** (§8.4): behind HID mouse; on-glass gates as listed.
- **W4 — second skin** (§8.5): the restyle requirement's falsifiable proof.

Sequencing against the surface model's phases (A-m5): W0–W2 depend on
nothing from surface-model Phases A–D — the web overlay uses the Stage-9
surface marks that already ship, and the P4 restore uses shipped stamp
machinery. If Phase A lands first, the overlay layer becomes a named
Surface with a Class B declaration; nothing here blocks on it.

## 10. Non-goals

A retained widget tree (L7). Per-widget damage of any kind (dissolved, §4).
Content-draw cost (map cells, code text — #163's kernels; the overlay
composes with them but does not touch them). Content-level hover (brush
ghosts, swatch previews — per-surface features AFTER the library, riding
their surface's own repaint). Keyboard focus traversal. Animation (no
clocks in widgets; if ever, dt-injected like `ScrollRegion`). The cart API.
S3 presentation changes.

## 11. Open questions (bounded)

1. **Device webserver hover** — stays OFF (§4 drop behavior); revisit only
   if a measurement asks.
2. **Skin table granularity** — shared state table with per-kind overrides
   (lean, for §7's coherence) vs per-kind tables. Settle in W0.
3. **Does preview-select want a library helper** (the shelf's Settings glue
   distilled: "selection follows the pump's target"), or stay per-surface?
   Lean: distill after W2 shows the shape twice.
4. **Pressed on grid cells during scroll disambiguation** — the cue must
   not flash while `DragTap` hasn't decided tap-vs-scroll; likely the pump
   defers pressed until the slop resolves. Settle in W0 with a test.

## 12. Review ledger (traceability, v0 → v0.2)

Arch review (verdict REDESIGN, scoped) → folded: **A-B1** web patch stream
protocol-incompatible → §4 overlay-as-ordinary-surface, patch mechanism
deleted; **A-B2** self-bounding premise false (`tab_row`/`game_btn`/
`mini_btn`/`text_field` cited) → contract deleted with its consumer;
additive-cue constraint replaces it (§4/§7); **A-B3** no equivalence oracle
→ the un-hover byte-equality oracle, in CI (§4, W1 gate); **A-M1** stale
hover on leave/occlusion → `pointer_leave` + WM claiming (§3.1, §5);
**A-M2** gate weaker than shipped → the shipped `_content_static` predicate
is the authority, no new gate defined (§4); **A-M3** root-canvas chrome has
no replay buffer → overlay needs none; chrome surfaces first-class in §8;
**A-M4** the overlay alternative never evaluated → it IS v0.2's mechanism;
**A-M5** preview-select ≠ cue-hover, "two lines" overclaim → §3.1 split +
honest migration claim; **A-M6** stale delegate claim, missing
`_hist_btn`/disabled sites → §1/§3.2/§8 corrected; **A-M7** `hovering`
present-tense → §5 sequencing; **A-m1..m7** → §3.3 transcription honesty,
§3.1 duplicate-id check, §4 measured arithmetic, §3.1 allocation note,
§9 phase sequencing, §8.1 expanded goldens, §4 drop behavior.

Perf review (verdict NOT ESTABLISHED) → folded: **P-B1/B2** (= A-B2/A-B1)
as above; **P-B3** no measured beneficiary + phasing inverted → the §4
MEASURED baseline table (run 2026-08-04, before this revision), W1 carries
the gate, P4 named as the future beneficiary behind HID mouse (owner);
**P-M1** ≤2ms gate = page floor → gates restated end-to-end with the floor
named (§4); **P-M2** record churn → records shrunk to rect/extent/cue/id +
alloc budget test (§3.1); **P-M3** drop amplification → W1 throttled-run
gate + device hover OFF; **P-M4** skin lookup shape → pre-flattened tables
mandated (§3.3); **P-m1** number hygiene → §1/§2 corrected (909/32; P4
numbers cited post-#159 as ~32–72ms); **P-m2** pump scan cost → bounded
(~50–100µs device-class worst case, gate-closed on touch); **P-m3**
pressed-zero-extra-paints verified → §3.2; **P-m4** S3 grep gate impossible
→ behavioral counter test (§2, W0).
