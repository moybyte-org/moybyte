# Surface model v1 — the presentation contract for every backend

**Status: v1.3 LOCKED (2026-10-07) — v1.1 plus the STAGE-4 AMENDMENT (§13),
which retires the web annex, the 2026-08-27 FOLD (§14), which absorbs the
archived predecessor and changes no part of the model, and the 2026-10-07
KERNEL AMENDMENT (§15), which moves the Surface's home and its mint into the
native kernel and changes no law, the 2026-10-27 REMOVAL (§16) of the
surface table that nothing read, and the 2026-10-27 SHIFT FRAME (§14.7), the
change record a content's frame may be, which adds no invalidation mechanism.** Written, then put
through the parallel adversarial architecture + perf review passes
(verdicts: **LOCK AFTER FIXES** / **PERF CASE STANDS WITH FIXES**); all twenty
findings are folded into this revision — **§12 is the finding-by-finding
traceability ledger.**
**Tracks:** #73/#105 (windowed WM), #58 (P4 port), #175/#176 (web runner), #113
(scroll), #163 (grid kernels), #76/#153 (web deltas).
**Relation to prior docs:** this is the successor that
`docs/history/ui_damage_model_v1.md`'s §0 review outcome asks for — it builds the
§2.1 consolidation that survived that review (one invalidation mechanism instead
of six, at *surface* granularity) and refuses everything that review killed
(fine-grained damage, inferred static content). That doc was archived 2026-08-27
and its still-binding kernel folded in as **§14**, so live code cites this file.
It does not supersede `shell_ux_v1.md` (what the UI does) or
`visual_identity_v1.md` (how it looks); it governs **how pixels reach glass**.

**How to use this doc:** this is the contract. A new backend implements §4 and its
annex in §5; it does not invent a new invalidation mechanism (Law L10). A change
to the model is a change to THIS file first, reviewed the same way. **Read §13
before §5.4 or §6** — the web transport those sections describe no longer
exists, and the amendment says what replaced it. §8 is the
graveyard — approaches already tried and buried with evidence; re-proposing one
requires new evidence, not new enthusiasm. Performance statements in this doc are
labeled **MEASURED** (with the source), **ESTIMATED** (arithmetic shown), or
**PREDICTED** (with the falsifiable gate that settles them) — an unlabeled claim
is a doc bug.

---

## 1. The model in one page

The shell's rendering has three separable costs:

1. **Deciding what to draw** — walking the UI, issuing draw verbs. Python
   dispatch. Attacked by kernels/batching (#163, `spr_batch`, `fill_rects`) —
   NOT by this spec.
2. **Rasterizing the commands** — attacked by the native verbs, the PPA, and
   record-only elision. NOT by this spec.
3. **Re-doing 1+2 when nothing relevant changed** — THIS spec.

The verdict, from the evidence accumulated across every target:

| layer | model | why |
|---|---|---|
| widget logic | **immediate mode** — every surface repaint re-runs its layout/draw code | one codebase reflows from 320×240 to any window size (#39); `draw(dt)` stays a pure function, which the whole golden/deterministic test harness depends on; the shell stays small |
| surfaces | **retained** — every WM surface has identity, local content, and placement, with an explicit change signal | drags/resizes change *placement*, not *content*; every target independently converged on this (P4 backdrop+stamp, web deflayer+delta, wasm view bracket) |
| dirty tracking | **explicit signals owned by producers** — generation counters + a declared `animating` class + the pointer leg (§3: today's gate has THREE legs, and all three survive) | inference of "static" lost twice here (Fold-2 revert; scroll-as-blit over web); booleans cleared by consumers is where the six ad-hoc mechanisms diverged |
| compositing | **per-backend strategy** behind one contract | the browser compositor is delegated-to on the GPU (validated per-device, §5.4 — not assumed free); the P4 must stamp in place (a 1:1 full-screen copy is ~26ms, PSRAM-bandwidth-bound, MEASURED #58); the S3 full-repaints 320×240 and must pay ~nothing for this model (§5.1 shows the arithmetic) |
| widget tree | **none, ever** | the `ui_damage_model_v1` §0 review killed the perf case; LVGL left the draw path at 47→90fps and has no P4 port; half our surfaces are free-form cart output no tree can model |

## 2. The Surface object

**Removed (§16, 2026-10-27).** No code holds this object: the Python leaf
(`runtime/surface.py`) went with the kernel's glass pass and the kernel's SURF
table with §16, because nothing ever read a gen. The record below is the
model's vocabulary, which §14's mechanisms and the backends' per-buffer state
still follow in spirit.

    sid          # stable identity, minted from the WM REGISTRY key; the row's key
    domain       # "system" | "game"
    w, h         # CONTENT size — the surface's local coordinate space
    x, y, scale, z   # PLACEMENT — WM-owned; content code never reads or writes it
    content_gen  # stamped from ONE monotonic counter when content changes
    place_gen    # stamped from the same counter on move/resize/restack/show/hide
    animating    # this surface repaints without any bump while true (§3 class B)


**sid rules (the make-group lesson).** sids are minted from the WM's registry
keys, **never from content kinds** — the shipped `key == win.kind` comparison
silently never matched for the shared "make" group (window key `"make"`,
content kind `"picker"`), which disabled its drag content-freeze everywhere
(`wm_windowed.py`, the identity-resolution fix). sids are world-qualified: the
fullscreen Library layer and the windowed desk backdrop both carry the layer
id `"launcher"` today (`launcher_layer.py` vs `wm_windowed.py`) — one sid,
two contents across a mode switch is exactly the aliasing §3 forbids.

**Gen minting (no per-object counters, ever).** Both gens are stamped from
**one monotonic counter** — the `atlas_gen` pattern (from the since-deleted `web_view.py`:
increments, never restarts), which §2's L3 cites as prior art and which this
rule generalizes; one counter per console, whichever WM is up. Rationale: surfaces are destroyed wholesale in the real code
(`on_relayout` does `_wins.clear()`; console mode drops every window's surface while the windows wait parked)
while per-client caches (`SurfaceDelta._last`) persist per connection. A
per-object counter restarting at 0 can collide with a client's stored gen and
alias into a wrong `{"same":1}` — the browser would replay a **dead window's**
content. A monotonic mint makes recreation safe for free: a reborn surface
carries a fresh, higher gen, so every consumer sees it as changed. Comparisons
are always `!=`, never `>`.

### The laws

- **L1 — content/placement separation.** A move or restack touches
  `place_gen` only. **Resize is the stated exception:** it reflows content
  (`_resize_window` rebuilds the buffer and layout context), so resize =
  `place_gen` + a WM-sanctioned `content_gen` bump — the one place the WM
  writes a content gen, and it is doing so as the owner of the reflow.
- **L2 — dirty is explicit, in three producer classes (§3).** Only producers
  signal change: gen bumps, the declared `animating` class, and the pointer
  leg. Inferring "static" from draw streams is FORBIDDEN (§8: Fold-2, web
  scroll-as-blit). A draw-stream comparison may ASSERT, never license: the
  shift frame's record audit (§14.7) is a test's net, not the frame's rule.
- **L3 — gens come from one monotonic mint** (§2 above),
  compared `!=` per consumer against its own last-seen. Never a boolean a
  consumer clears: N consumers (the kernel's compositors, the L8 harness) must
  not coordinate. Prior art: `atlas_gen`, `ws._cover_gen`, `sheet.gen`.
- **L4 — surfaces record and draw in LOCAL space.** The WM owns the transform
  to the root canvas / the wire (#175's `view` bracket, generalized). Content
  code positioned in desktop coordinates is a bug. The wire-compat
  consequence of this law is owned honestly in §6 (it is a protocol break,
  versioned — not silently compatible).
- **L5 — compositing strategy is backend-owned.** The shared layer decides
  *whether* a surface changed; the backend decides *how* the glass catches up.
- **L6 — the degenerate tier pays ~nothing, and here is the arithmetic.** On
  the S3 the **existing** `ws._dirty` writes stay as they are (§3), and the
  frame gate reads the kernel's own epoch once a frame (§15's leg): one C
  call per frame, ≈ single-digit µs, and no new per-draw work.
- **L7 — no retained widget tree, no per-widget damage.** Litigated; §8.
- **L8 — staleness AND silent disablement must both be testable.** Two
  directions, per tier:
  - *Correctness (hash ⇒ gen):* content changed without a bump is a loud
    failure. Host: pixel-hash per surface, **harness mode only, excluded from
    default CI** (the golden suites are already fragile — #166; ESTIMATED
    ~1.5-3ms/frame). Recording tiers (web/wasm) have **no pixels**
    (`ViewCanvas` has no `buf`; layers are `RECORD_ONLY`): the assertion is
    over the **command stream** — "surface cmds changed ⇒ `content_gen`
    moved" — run in the web test suites. Residual the stream-hash cannot
    see: replay-state leaks in the page (the `dfl` save/restore class),
    covered by the page-side replayer tests.
  - *Perf (the silent-disable direction):* on a placement-only gesture frame
    the harness asserts **zero content gens moved and zero surface streams
    shipped**; every backend exposes ship/skip counters via the existing
    `ws.note_cost` convention. This is the direction the recurring real
    defects lived in — the 72ms bar-strip rebuild that "produced no signal"
    (ui_damage §2.1) and the make-group freeze that never engaged — and a
    hash assertion cannot catch either.
- **L9 — cart output is opaque.** A game surface's content is free-form; the
  shell never parses or diffs it. A running cart's surface is content-dirty
  **on frames it renders** — under the tick model (#217) logic ticks at the
  cart's rate while render runs on a divisor of it, and the gen moves only
  with renders, or web clients would re-ship at 60 while glass draws 30.
- **L10 — new backends implement §4; they do not add invalidation
  mechanisms.** This is the "we don't redesign this again" law.

## 3. The dirty protocol — three producer classes, all owned

**The #44 gate has three legs**, and the model absorbs all three —
`_needs_redraw` is `_dirty OR _animating(dt) OR ptr_state != _last_ptr`
(`console.py`) plus the kernel-frame epoch (§15) and the WM's owed-repaint
legs, and the windowed content-freeze (`_content_static`, `wm_windowed.py`)
re-derives the three conditions per window. A model
that absorbed only the `_dirty` leg would freeze live wallpapers, the music
playhead, OTA progress, and every paint stroke — the review's top finding.

**Class A — explicit gen bumps.** The signal rides the **existing**
`ws._dirty = True` writes, *observed, not rewritten*: an un-attributed dirty
is a **global** change — the whole frame repaints — safe, never wrong, merely
unprofitable. Per-surface attribution is **opt-in
per site**, added only where audited, so shared `*_layer.py` code stays S3-safe. The audit
is a named Phase A deliverable — there are **about 135 raw `_dirty = True`
sites across 31 files** (2026-10-07; the 2026-07 review counted ~179 across
22, as ui_damage §0.2.7 had), several
firing from async contexts (wifi scan, bluetooth, OTA `download_step`) where
"the active surface" would mis-attribute. "Mechanical migration" is hereby
retracted; un-audited sites stay global-epoch forever and are still correct.

**Class B — declared animating surfaces.** Sources that repaint although
nothing marks dirty, today enumerated by the `_animating` predicate and
excluded **by name** in `_content_static`: the live wallpaper, the music
preview's playhead, the bluetooth panel's async scan/pair states, the update
screen's progress, splash/confetti/toast/egg overlays, and the running cart.
Each declares `Surface.animating = True` while its animation is live — the
by-name lists become the seed of the registration and are then **deleted**
(§7). An animating surface needs no bumps and is never skipped. A verbs page
moving on its own -- a fling's coast, a glide to a step's target -- declares
itself through the engine (`moy_ui_api_moving`, `ws.ui_moving` for the gate).

**Class C — pointer-armed mutators (the pre-#44 debt).** Paint strokes, map
pans, block drags and kinetic scrolls mutate content **without marking
dirty** — they predate the gate and rely on the pointer-state leg arming the
repaint (`_content_static`'s own docstring). **The pointer leg survives the
consolidation**: any frame with a pointer DOWN or CLICK (this frame or last)
renders live, exactly as shipped. Migrating these handlers to Class A bumps
is a stated obligation with a bounded audit, verified by L8 — and until a
handler is migrated, the DOWN-guard caps the model's win on gesture frames
(owned honestly in §5.4's predictions). The moving cursor itself is
position-only and inert to content (audited 2026-07-27 in the code) — it is
a tiny surface whose motion is pure `place_gen`, i.e. this model's cheapest
advertisement.

| owner | signal |
|---|---|
| running game (Player) | Class B while running; gen moves on frames it renders (L9) |
| app windows (Editor tabs, Settings, Desk-Lab apps) | Class A via the existing `_dirty` writes (global epoch until attributed); Class C during gestures |
| self-animating panels (music preview, bluetooth, OTA, live wallpaper, overlays) | Class B declaration |
| wallpaper (static, e.g. `moybyte.moy_night.moy`) | nothing — idles free *by construction* |
| bar | Class A: clock tick (1/s), status change, lent-zone redraw request from the app |
| desk icon column | Class A on selection/label change (own surface — not the wallpaper's, §11) |
| window chrome | its own WM-owned chrome band per window (§5 preamble), bumped by the WM on focus/title change |

The #44 frame gate survives as the aggregate: "any Class A/B/C signal since
this backend's last **presented buffer**" — see §4 for why "last frame" is
not enough on multi-buffer hardware.

## 4. The compositor contract

```python
def composite(surfaces):
    """surfaces: z-ordered. The backend compares each surface's gens against
    its own last-seen state and brings glass/wire to agreement. Strategy is
    the backend's (L5)."""

def end_frame():
    """The frame-tail hook. Runs AFTER every surface write; the one legal
    place for deferred/async work that must be the frame's LAST write."""
```

**Last-seen is per PHYSICAL BUFFER, N-deep.** A backend that presents from N
rotating buffers (host 1; P4 2-3, `RETAINED_FRAMES`-shaped) keeps last-seen
gens **per buffer**: a surface may only be skipped when no signal moved
within the last N *presented* frames — a gen that moved one frame ago still
owes a paint into the other buffer. This is the ping-pong-flicker rule the P4
learned on glass; the shipped streak counters (`_stamp_streak`,
`_chrome_streak`, `_chip_streak`, `_desk_streak`, `_bezel_paints` and their
sig twins) ARE hand-rolled per-buffer last-seens, and §7 retires them by name.
`end_frame()` exists because the P4's async-PPA stamp-defer has a hard
ordering constraint (an async PPA op must be the frame's LAST write, with dst
cache-writeback before submit — #58): that rule needs a verb to hang on, or
the contract cannot express the shipped fast path.

| backend | content changed | placement changed | nothing changed (N-deep) |
|---|---|---|---|
| S3 / T-Deck | full repaint under the #44 gate (today's `FullscreenStackWM.draw_stack`) | n/a (fullscreen stack) | skip frame |
| P4 | in-place repaint of that surface (today's `WindowedWM.draw_stack` full/partial) | backdrop dirty-union restore + retained-content stamp — **today's drag path, renamed** | skip |
| host sim | re-run the surface's immediate draw into its buffer; blit by z | blit by z | skip |
| web page | re-ship that surface's stream; page replays into **that surface's canvas** | ship placement metadata; page moves the canvas — zero replay | ship `{"same":1}` / nothing; page doesn't touch it |

The "content changed → re-run the immediate draw" cells are the
**skip-draw generalization**: a backend may skip *calling* a gen-clean,
non-animating, pointer-quiet surface's draw entirely — the shipped
`_content_static` freeze promoted from one special case to the contract
(**§14.1** is that slice as built, and what its code cites).
It inherits Class C's caps until the producer audit lands (§3).

## 5. Backend annexes (normative)

**Window chrome, everywhere:** chrome (title strip, border, shadow, grip) is
a **WM-owned chrome band per window** with its own gen — NOT part of the
content surface. The shadow draws *outside* `content_rect` (so
chrome-in-surface would break §2's bounds), a focus flip recolors chrome on
two windows (chrome-in-surface would re-ship two full contents per click on
the web), and the resize rubber-band outline is compositor-drawn. Surfaces
remain opaque rects; chrome bands may exceed them.

### 5.1 S3 / T-Deck — fullscreen tier

`FullscreenStackWM` only. Surfaces degenerate to: one app surface + bar + the
game composite (`composite_game`/`viewport`). Strategy: memoized stack (#66)
+ full repaint under the gate. **No Python Surface object exists here**: the
rows are the kernel's (§15), producer signals ride the unmodified `_dirty`
writes folded once per painted frame, and L6 shows the ≤single-digit-µs
arithmetic. Hardware constraints bounding any future change: the banded
flush fed from core 0 (`native/moy_flush/moy_flush.h`); the SD/display shared
SPI host on the T-Deck; the I2C poller (#69). This annex exists mostly to
say: **do nothing here.** The per-frame executed path gains the kernel's
epoch read (L6) and nothing else, and the gate for this tier is each S3's
on-glass suite and the perf roster within noise, not file bytes — the frozen
*source* of shared files may still drift textually (comments, unrelated
edits). This contract is the presentation's, not the shell's pixels: the
shell's own draws on this tier change when a restyle changes them, once and
named, gated by the suite, the roster and `tools/p4_bench.py --board tdeck
--repaint` within its thresholds (amended 2026-10-11 for theming's phase 2b,
which moved the T-Deck editors' frozen dark literals onto the theme's tokens;
`docs/theming_2026-09.md` §9).

**A compiled cart's frame reaches the flush from the cart's own memory and
adds nothing to the contract.** The game composite on a banded board is a
data path, not a dirty signal: a running cart is still content-dirty on the
frames it draws (L9), and the composite at the game→system boundary hands the
flush the cart's frame to fold (`native/moy_flush/moy_fold.h`) where it would
have handed it the game canvas to snapshot — the #190 fold, fed from a second
source. Nothing decides what changed. An overlay above disarms the fold as it
does over any game canvas; a painter that draws on the game canvas over the
frame has the frame written there first; and the two opaque rects the shell
draws over every play frame, the FPS chip and the perf HUD line, are DECLARED
by the painter that draws them (`patch_cart_frame`) — a producer's statement,
never inferred from the draw stream (L2, §8).

### 5.2 P4 — windowed on-glass

`WindowedWM`. Surfaces: wallpaper/desk, each `_Win` + its chrome band, bar,
cursor, overlays, the game viewport. Strategy: **in-place stamps, never full
recomposite** — a 1024×600 1:1 copy is ~26ms PSRAM-bandwidth-bound (MEASURED
#58), which is why this backend's placement-dirty handler is the shipped drag
machinery: `_BackdropLayer` retained cache, dirty-union restore
(`blit_strip_rect`), body-subtract, and the async-PPA stamp-defer expressed
through §4's `end_frame()`. Its last-seen state is per-buffer N-deep (§4) —
the streak/sig fields are absorbed and deleted (§7). **Buffer ownership:**
`moy_alloc` has no `free()`, and resize/relayout rebuild window
buffers today — Surface buffers are minted with an owner, reused per slot,
never evict-and-reallocate. Phase C claims **no perf change** — the gate is
"drag fps not regressed" on the #156 on-glass suite, **naming the make-group
window's drag explicitly** (the last silent-disable bug lived exactly there).
Games: fullscreen in console mode; windowed via the PPA upscale composite
(PPA is upscale-only — §8).

**The rotated P4 (Guition 10.1″, 2026-09-08) adds nothing to the contract
and one verb to the backend.** Its paint target is ONE persistent landscape
buffer, presented by rotating it onto portrait scan buffers, so the
placement-changed cell above is *the same* backdrop restore followed by a
rect rotate instead of a full one — provided the compositor knows the rect.
`WindowedWM._hand_damage` hands it the gesture union and the extents of the
windows it rendered live (`note_damage` on the root canvas, absent on every
other backend), the same set the union restore already trusts; the
fullscreen home's shelf drag hands its band the same way
(`hand_content_damage`), and the bar says when it painted new pixels into
one of the compositor's strips (`strip_moved`), so a still bar is not
rotated; a frame nobody describes is a full rotate. This is not a second
invalidation mechanism: no new dirty state, no new producer class — the
backdrop layer, the window loop, the shelf and the bar say what they
painted, once, on the frame they painted it. `end_frame()`'s ordering rule holds there too: the quiet game frame's
rotate is queued at flush and fenced at the next present, before the cart's
tick can write the canvas it was copied from.

**A compiled cart's direct-colour frame adds nothing here either**
(`device/p4_canvas.py`'s `present_frame`): the composite at the game
boundary -- console mode's and the player window's -- hands the PPA the
cart's frame, or the DMA snapshot of it, where it would have handed it the
game canvas, and the opaque rects the shell draws over it are declared as in
§5.1. Nothing decides what changed.

### 5.3 Host sim — reference implementation

pygame; buffers are cheap, so this backend implements the contract in its
plainest form and serves as the test bed: golden pixel-parity before/after
Phase A **including windowed-size goldens** (the 320×240 goldens
structurally cannot catch windowed-tier staleness — ui_damage §0.2.4), and
both L8 directions live here (pixel-hash harness mode; the
placement-only-frame zero-ship assertion).

### 5.4 Web — one page protocol, three transports

*(**RETIRED — see §13.** All three transports named below are gone: the device
webserver's frame push and the host web console died in the 2026-08 streaming
sunset (moycore plan §3.2), and the wasm runner stopped serving a command
stream at stage 4 when it started rasterizing. The page protocol, the
per-surface streams, the delta and the keyframe verb went with them. Text below
kept as written, because the reasoning it records — especially the GC cliff
measurement — outlived the transport.)*

The device webserver (`moy_webserver.py`, WS push), the host web console
(the deleted `web_console` tool), and the wasm runner (`firmware/web_runner/`,
worker-owned VM per #176) serve the same page protocol (the page generator that
used to live beside `web_view` + `page_tail.js`).

- **Content** = the surface's command stream, recorded in local space (L4),
  shipped when its gen moves. Deflayer (#54/#43) is the content half for
  window surfaces: ship-once, referenced after (its cart-level uses are NOT
  absorbed — §7). `SurfaceDelta`'s deep byte-compare is **kept as the
  shipping safety net** until the L8 stream assertion has caught at least one
  real bug in CI, then demoted; the gen-compare's win is ESTIMATED, not
  "free" — only the deep `==` is removed, `_slice_surfaces` and the gen
  plumbing remain (netting per the perf review F10).
- **Placement** = metadata per surface entry (§6), applied as a canvas
  position/transform. The page gives each surface **its own canvas**;
  compositing is **delegated to the browser compositor** — not assumed free.
  ESTIMATED page memory: ~5.4MB per full-screen surface (idx array +
  ImageData + canvas backing at 1024×600), ~15-25MB for a populated desk +
  a GPU texture upload per changed-surface `putImageData`; fine on desktop,
  **validated on one phone-class device as part of the Phase B gate** (the
  page explicitly engineers for iPhone-class clients). Banked win the flat
  page cannot have: the changed-surface blit shrinks ~8× (today every frame
  replays the cache into one buffer and blits all 614K pixels; per-surface,
  a play frame touches only the 76.8K-pixel game canvas).
- **PREDICTED, with gates (the review's F1):** a *window-move* drag ships
  N×(30-60)B of stubs + placement per frame and replays nothing. NOTE the
  only measured wasm drag is **25.8ms record-only for a content scroll**
  (`RecordingLayer.RECORD_ONLY`) — a different frame class that placement
  metadata cannot help *by design* (its content gen legitimately moves every
  frame; its lever is §4's skip-draw for the *other* surfaces + Class C
  migration). Phase B therefore **measures the wasm window-move baseline
  first**; the 60fps gate is falsified if that baseline is already
  worker-bound. The page-side win ceiling without skip-draw is the ~2ms
  replay+blit half (the worker split already absorbed the ~7ms console half).
- **GC: the recorded contradiction, RESOLVED (Phase B gate 0, 2026-07-30).**
  `web_boot.py` justified the delta by "less garbage → fewer threshold-driven
  sweeps"; #176 said the sweep is **heap-size-bound**, "reducing per-frame
  allocation is not a lever." Measurement found both half-right, at different
  scales: the port hardcodes `gc_alloc_threshold` to **16KB**
  (`ports/webassembly/main.c`), so any frame allocating past it schedules a
  **full mark+sweep at the next JS→Python boundary**, whose cost scales with
  the LIVE SET — ~2ms on the handheld tier (#176's number), **~67ms on the
  desktop tier** (bigger live set: backdrop caches, cover runs). Under-16KB
  frames skip it entirely — the whole 0.2ms-vs-70ms idle/drag cliff.
  Allocation is a lever only ACROSS the 16KB cliff, not gradually — which is
  exactly what §4 skip-draw provides (a drag frame ships only the moving
  window). Shipped policy: `web_boot` raises the threshold to 6MB and the
  worker lands collects on idle (3 quiet frames) with a bounded periodic
  guard. Measured on the wasm desktop: window drag 70.15 → **0.54ms/frame**
  (p95 0.63, no collects in 120 drag frames); content drag 137 → 8ms mean.
- **Drop-recovery (the review's A7/F2):** the push transports drop frames by
  design (bandwidth cap on device; the page keeps only the newest frame on
  the wasm). Once unchanged surfaces stop being re-recorded, a change that
  rode a dropped push has no stream anywhere to ship late. The transport
  therefore gets a **keyframe-production verb** — force-record all surfaces
  for one frame — generalizing the shipped `WsClientState` first-frame
  keyframe latch; invoked on connect, reconnect, and detected drop.
- **Device webserver (rewritten for the tier it actually ships on):** the
  device transport runs on the **fullscreen** tier — no windows, so the
  placement lever does not exist there. Its win is the delta's recorded
  case — a live wallpaper or running cart over a static grid+bar — plus the
  bar stub; PREDICTED drag saving ≤10% of frame bytes (the app surface
  re-ships in full during a scroll). The device-side CPU of
  `begin_surface` marks + `_slice_surfaces` + per-surface compare is
  **UNMEASURED** (the code's "negligible on-device" is asserted, and the
  deep-== is most expensive on exactly the unchanged surfaces it elides).
  Gate before flipping `surfaces_on` on device: an S3 on-glass A/B measuring
  **bytes/frame AND ms/frame**, on the live-wallpaper launcher and on a
  scroll drag.
- Root `RETAINED_FRAMES` stays **0** on web transports (§8). Per-surface
  scroll retention is Phase D, under §6's two `scr` invariants.
- The wasm runner keeps `RECORD_ONLY` (orthogonal: raster-half elision) and
  the worker split (#176). The `view` bracket (#175) remains the wire
  encoding for a cart span inside a window until placement metadata carries
  it (it is L4 for the game surface, invented first).

## 6. Wire protocol delta (web)

*(**RETIRED — see §13.** There is no wire protocol: the wasm head blits a
framebuffer. The `scr` invariants below were the sharpest thinking in this
section and are preserved as reasoning about retained surfaces generally, not
as a shipped wire.)*

**Current shape (corrected):** `frame_payload.surfaces` entries are dicts —
`{"id", "domain", "cmds"}` or `{"id", "domain", "same": 1}` (the deleted `web_view.py`
serve path; the `[sid, domain, cmds]` triple is the recorder-internal
`frame_surfaces()` shape only).

**Added at Phase B:** per entry `place: [x, y, scale, z]`, `gen`, `pgen`; and
a top-level protocol **version field**. **This is a versioned break, not a
silent extension** (the review's A4): L4 makes streams position-free, so a
page that ignored `place` would stack every window at the origin — the old
"ignore-and-it-works" claim is retracted. The real compat mechanism is that
page and server ship from the same build on every transport and the page is
served per-GET (never long-cached); the version field exists so a stale page
fails loudly instead of rendering garbage. ESTIMATED wire cost of the new
fields: N×~30-60B/frame ≈ 2-5% of the device's ~72KB/s budget at 30fps × 5
surfaces — accounted against §5.4's device arithmetic.

**`scr` invariants:** the root-level `scr` op remains forbidden
(RETAINED_FRAMES=0). Inside a Phase D retained surface it is allowed only
under BOTH: (a) a `scr`-carrying surface entry is **never** collapsed to
`{"same":1}` (already true in code — promoted to spec), and (b) a frame
carrying `scr` for a retained surface is **undroppable, or a detected drop
forces that surface's next ship to be a §5.4 keyframe** — without (b), frame
drops reproduce the marching-into-black failure §8 buries, through a
different hole than the quiet-gate one.

**Delivery is NOT guaranteed — the client must detect drops and ask
(normative, added 2026-07-31 from a shipped defect).** The clause above scoped
drop-handling to `scr` under Phase D. That was too narrow: `{"same":1}` means
"replay the stream I sent you earlier", so **every** delta-encoded frame is
already undroppable, and one lost frame strands every surface it carried in
full — permanently, because the server keeps saying "same" and the client keeps
replaying stale pixels. A transport that drops frames is legitimate (the web
runner's rAF loop deliberately keeps only the newest frame so a slow main
thread never queues up); what is NOT legitimate is dropping one silently. So:

- a client that discards a frame it did not replay MUST report it, and
- the server MUST answer with a §5.4 keyframe: forget the per-client cache
  (`SurfaceDelta.reset`), re-arm the WM's full draw, and re-open the redraw
  gate so the frame is actually produced.

Recovery is exactly one full frame; delta encoding resumes immediately after.
**How this presented** (owner, 2026-07-31): on a *tablet* — never the desktop —
tapping PLAY appeared to do nothing, and a later drag brought the Library up
with the desktop still visible around it. The desktop was masking it by
accident: a moving mouse dirties the console constantly, so a stranded surface
gets re-shipped within a few frames. A touch device has no hover, so nothing
repairs it. **The lesson is a general one for this spec: any invariant that
depends on the client's cache matching the server's belief needs a repair
path, not just a correct steady state.**

## 7. The consolidation ledger

What exists today → its disposition, **and the phase in which the old field
is deleted** (an "absorbed" row with nothing deleted would make this model a
seventh mechanism — the review's A9). Each deletion lands with a grep-test
pinning the field's absence.

| mechanism | disposition | deleted |
|---|---|---|
| #44 `ws._dirty` frame gate (three legs: dirty / `_animating` / pointer) | **absorbed as §3's three producer classes** — the `_dirty` writes stay and become the Class A epoch signal | predicate re-expression in C; writes never |
| the bar strip cache keyed on canvas identity (ui_damage §2.1's flagship bug) | **absorbed** by the bar Surface's gen — the identity key that caused the 72ms silent rebuild is deleted; the model's trophy case | B (web), C (device) |
| `_content_static` by-name freeze + its exclusion lists | **absorbed** by §4 skip-draw + Class B declarations | C |
| streak/sig family (`_win_sig`/`_stamp_streak`, `_chrome_sig/_streak`, `_chip_sig/_streak`, `_desk_sig/_streak`, `_bezel_key/_paints`) | **absorbed** by §4's per-buffer N-deep last-seen | C |
| `FullscreenStackWM.draw_stack` memoized stack (#66) | **unchanged** — it IS the S3 compositor | never |
| P4 `_BackdropLayer` + dirty-union + stamp-defer (#58) | **absorbed/renamed** — the P4 placement handler, via `end_frame()` | fields fold in C |
| deflayer ship-once + `blit_layer_*` (#54/#43) | **split**: window-content use absorbed into surface content; **cart-level layers (scroll layers, terrain, bar strip) kept as-is** | window path in B |
| `SurfaceDelta` deep-compare `{"same":1}` (#76) | **kept as the shipping safety net**; gen-compare added; deep-== demoted only after the L8 stream assertion catches a real bug in CI | after that, B+ |
| `WsClientState` SURF cache + keyframe latch | **kept**; latch generalized into §5.4's keyframe-production verb | never |
| `RecordingLayer.RECORD_ONLY` (#175 follow-on) | **kept, orthogonal** — raster-half elision, not invalidation | never |
| `ViewCanvas` + `view` bracket (#175) | **generalized** into L4; wire op converges in B+ | B+ |
| scroll-as-blit `RETAINED_FRAMES` (#113) | **per-backend**, and its ring is one body (§14.7): a content's frames are vouched for by ONE ring per region -- a window's by the windowed WM's shown ring (`_shown`, §14.3), a full-screen content's by its own `PaintRing` (`ui_draw`) -- never both | — |
| `_cover_runs` / `_cover_gen`, `atlas_gen`, `sheet.gen` | **unchanged** — the L3 prior art | never |
| `assets_json` memo key (wasm) | **unchanged** — serialization concern | never |

## 8. The graveyard — do not re-try without new evidence

- **LVGL / any third-party retained UI.** We shipped it and we left it, and
  the numbers are here so this is not re-litigated. On the T-Deck LVGL only
  ever brought up the panel and the SPI bus — nothing drew the UI with it —
  and replacing the drawing path with our own native blitter measured
  **47 → 90 FPS**, its CPU-bound rotation being the wall. The fork went with
  it on **2026-08-17**: the T-Deck ships mainline + `native/moy_lcd` and no
  LVGL exists anywhere in the tree. The P4 never had any, and no DSI port
  exists — so on the one board where these costs hurt, "just use the library"
  is first a port of LVGL. It also reaches only the device targets of the
  set we render the same pixels on: it is C with bindings and cannot run the
  host sim, so adopting it forks the shell into two UI implementations. What we took is the
  ALGORITHM — an invalid-area list, a merge, a clip discipline — not the
  dependency. This flips only if the P4 becomes the only target, with no host
  simulator; full analysis in `docs/history/ui_damage_model_v1.md` §4.
- **Per-widget / fine-grained damage.** The `ui_damage_model_v1` §0 review
  killed the perf case; the maintainability case is served at surface
  granularity by this spec.
- **Inferred static-content caching.** Fold-2 auto map cache: built, measured,
  reverted (`perf_native_gap_v1.md`) — inferring "static" under a free-form
  draw API cost more than it saved.
- **A retained ROOT framebuffer across web frames.** The #44 gate ships
  nothing on quiet frames while the frame counter advances — and frame DROPS
  (designed into both push transports) lose shifts the same way — so a shift
  can target a buffer that never received the paint it is relative to.
  Root `RETAINED_FRAMES=0` is the law; retention is per-surface (Phase D,
  §6's two invariants) or nothing.
- **Pure-Python cart rasterization in wasm.** ~85ms/f + ~102KB/f for one
  320×240 frame (#175). Commands ship; the page draws.
- **Tee-rasterizing for pixel-less consumers.** 246.7ms → 25.8ms on a windowed
  picker drag frame when the raster half was skipped (`RECORD_ONLY`).
- **PPA for 1:1 copies; PPA sprite batching.** Upscale-only wins; a 1:1
  full-screen copy is CPU≈PPA (~26ms, bandwidth-bound); 64×16×16 queued =
  4.57ms vs 0.70ms CPU (#58). Constrains every P4 compositing strategy.
- **The frame-level damage architecture (`ui_damage_model_v1` §5 phases 1–3).**
  Reviewed and downgraded in that doc itself; the P4's real costs were
  dispatch + overdraw → kernels (#163).
- **Per-object generation counters on destructible surfaces.** Never shipped
  — designed out at review (§2): recreation aliases against persistent
  client caches into wrong `{"same":1}` replays.

## 9. Phasing and gates

- **Phase A — name it (refactor + audit).** The surface registry (built as
  the Python leaf `runtime/surface.py`, the kernel's table since §15); gens
  from the one mint; Class A = observed `_dirty`
  epoch + opt-in attribution; Class B registration seeded from the
  `_animating`/`_content_static` lists; the **producer audit** (the ~179-site
  list + the Class C handler inventory) is a named deliverable. Gates: host
  goldens pixel-identical **including windowed sizes**; payload-shape tests
  re-baselined knowingly; both L8 directions green **on the audited
  screens**; S3 executed-path grep gate (§5.1).
- **Phase B — web compositing (the payoff, gated).** FIRST: measure the wasm
  window-move drag baseline (§5.4 — the recorded 25.8ms is a different frame
  class). Then per-surface canvases + placement metadata + protocol version
  bump + the keyframe-production verb; skip-draw for gen-clean surfaces; the
  GC A/B. Gates: window-move drag at 60fps on the wasm desktop (falsified if
  the baseline is already worker-bound — then the lever is skip-draw, not
  placement); one phone-class device validates the N-canvas compositing;
  device `surfaces_on` flips only after the on-glass bytes-AND-ms A/B.
- **Phase C — P4 (consolidation, no perf claim).** Per-buffer N-deep
  last-seens replace the streak/sig family (fields deleted + grep-tests);
  chrome bands; Class B/C re-expression of `_content_static`. Gate: #156
  on-glass suite green; drag fps not regressed, **the make-group window's
  drag named explicitly**.
- **Phase D — optional.** Per-surface scroll retention on web under §6's two
  `scr` invariants; further convergence of the `view` bracket into placement
  metadata.

## 10. Non-goals

The cart-facing API (SPEC.md's verb table) — untouched, forever the spec's
domain. A retained widget tree (L7). S3 presentation changes (L6). Editor tab
*content* draw cost — that is #163's kernel; the two compose. Audio, input,
transport liveness — other specs.

## 11. Open questions (bounded)

Resolved at review: chrome is a WM-owned band, not content (§5 preamble,
was Q3); gen lifetime is the monotonic mint (§2, was Q4).

1. Bar granularity: one surface, or zones (lent-left / OS-right) so a clock
   tick doesn't re-ship the app's zone? (Lean: one surface; it's tiny.
   Revisit only if the L8 perf counters show the bar dominating ships.)
2. Desk icon column: own surface (lean — selection changes must not re-ship
   a 1024×600 backdrop) or the wallpaper's.
3. ~~The Phase B GC A/B's outcome~~ RESOLVED — see §5.4: both rationales were
   half-right (the 16KB trigger cliff vs the live-set-bound collect cost);
   neither is buried, the synthesis is the shipped policy.
4. Phone-class N-canvas compositing (§5.4) — if it fails, the fallback is
   the flat buffer with per-surface *replay* skipping only (keep the CPU
   win, forfeit the compositor win). NOTE (gate 0 outcome): the falsification
   clause effectively FIRED in reverse — skip-draw + the GC policy met the
   60fps drag gate with the flat-buffer page (~2ms replay), so per-surface
   canvases are no longer a wasm-tier lever; their remaining case is the
   DEVICE transport's bytes (#153) and they proceed on that schedule.

## 12. Review ledger (traceability)

Arch review (verdict LOCK AFTER FIXES) → folded: A1 three-legged gate → §3
rewritten as three producer classes; A2 gen lifetime/sid aliasing → §2
monotonic mint + sid rules; A3 per-buffer last-seen + `end_frame()` → §4;
A4 wire compat self-contradiction + wrong wire shape → §6 versioned break +
corrected shapes; A5 Phase A S3 gate + non-mechanical migration → §2 leaf
module, §3 Class A epoch rule, §5.1/§9 gate rewording; A6 delta safety net +
pixel-less L8 → §5.4 + L8 per-tier; A7 drop-recovery → §5.4 keyframe verb +
§6 scr invariant (b); A8 chrome/overlays/cursor → §5 preamble, §3 Class B/C
+ cursor note; A9 ledger deletions → §7 rebuilt with delete-phases +
grep-tests; A10 resize/L1, frameskip/L9, Stage 9 re-partition, windowed
goldens, P4 buffer ownership → L1, L9, §2, §5.3/§9, §5.2.

Perf review (verdict STANDS WITH FIXES) → folded: F1 drag claim/baseline →
§5.4 PREDICTED + Phase B baseline-first gate + skip-draw stated; F2 Phase D
drop hazard → §6 invariant (b); F3 device wrong-tier arithmetic → §5.4
device bullet rewritten + bytes-AND-ms gate; F4 L8 perf direction → L8 +
Phase C make-group gate; F5 producer audit / "mechanical" retracted → §3;
F6 S3 gate wording + arithmetic → L6/§5.1; F7 GC contradiction → §5.4 A/B;
F8 L8 cost/per-tier definition → L8; F9 compositor memory/"for free" →
§5.4 ESTIMATED + phone gate + banked 8× blit win; F10 number wording →
§5.4/§6 corrected.

---

## 13. Amendment: the web annex retires (2026-08-12, moycore stage 4)

This doc is LOCKED, so its web half is retired by an explicit versioned
amendment rather than by editing §5.4 and §6 into a shape the code never had.
The tracker is #192; the reasoning is `docs/history/moycore_plan_2026-08.md` §3.2/§6.

**What changed underneath.** §5.4 opened "the device webserver, the host web
console, and the wasm runner serve the same page protocol". The first two were
deleted in the 2026-08 streaming sunset. The third stopped needing a protocol
at all: the wasm head now compiles the same `moy_gfx` + libmoy kernel the
boards run, draws into its own RGB565 framebuffer, and hands the finished bytes
to the page. The page expands 565 to RGBA through a lookup table and calls
`putImageData`. There is no command stream, no replayer, no atlas, no
per-surface slicing, no `{"same":1}` stub, no keyframe verb, no `/assets` pixel
payload.

**Therefore, retired:** §5.4 in full; §6 in full (protocol version, `place`/
`gen`/`pgen` entries, the `scr` invariants as a *wire* rule); Phase B and
Phase D of §9; and the delete-phases in §7 that named `SurfaceDelta`'s
deep-compare and `WsClientState`'s latch as things to demote later — they are
deleted, not demoted. `RETAINED_FRAMES = 0` as a web-tier rule retires with
them: the runner's canvas holds one persistent buffer and declares 1, like any
other raster surface.

**Unchanged and still binding:** §1–§4 (the model, the laws, the compositor
contract), §5.1–§5.3 (S3, P4, host), §8 (the graveyard), and the §2/L6 leaf
discipline. A backend still implements §4 and invents no new invalidation
mechanism; the wasm head complies as an ordinary raster tier, which is the
point — it stopped being a special case.

**This amendment kept `runtime/surface.py` (2026-08-12), and §15 moves it.**
The registry (`Surface`/`SurfaceSet`, the monotonic mint, the epoch, the
prefix-scoped sync) was reached only through `wm_windowed`'s
`if not self._recording: return` guards, and nothing sets `_recording` any
more — so on every shipping tier that code is inert from this stage on. It
stayed because it was Phase A/C groundwork for the raster tiers, which this
stage did not touch; what died was its recording DRIVER, not the model. The
evidence Phase C wanted arrived as the kernel: the raster tiers' compositors
cross to C and consume the gens there, so §15 gives the registry to the
kernel and the glass pass of `docs/kernel_survival_2026-10.md` deletes the
Python leaf.

**What replaced the L8 stream-hash gate.** §5.4's accounting proved "every
surface stream that changed was covered by a moved gen" — a pixel-less
substitute for L8 that only the recording tier could run. The wasm head has
pixels now, so it takes the same gate every other raster tier takes, and its
screenshots are directly comparable to the host's (`pageshot.mjs`, which no
longer reconstructs a replayer to produce them).

## 14. Amendment: the predecessor folds in (2026-08-27)

`docs/history/ui_damage_model_v1.md` — the doc this one succeeds — is archived,
and three things in it were still load-bearing. The why-not-LVGL numbers are
inlined in §8. The other two are here: the design source the shipped content
freeze cites by section, and the evidence for having exactly one mechanism.
**Nothing in the model changes** — §14.1 is §4's skip-draw cell as BUILT, and
§14.2 is why §7 exists. What that doc's own §0 review walked or killed (the
frame-level damage architecture, its §5 phases 1–3, the retained-mode
primitives) stays there and is not carried forward; §8 keeps the verdicts.

### 14.1 The shipped first slice: the focused-window content freeze

`wm_windowed._content_static` is §4's skip-draw generalization as built: on a
PAINTED frame that provably did not change the focused window's content, the
content draw is skipped and the retained `win.buf` stamp presents it instead.
"Provably" is five checkable facts, all of them required.

- **`ws._dirty` is false, or the WM's own mode-switch mark.** It is still
  readable during the draw — `frame()` clears it after — so false means no
  input or state handler reported a visible change this frame. The home's X
  bringing the desk back marks `MODE_MARK`, which no window's content moved
  with; any handler's mark after it is `True`.
- **The buffer holds the focus mark this frame draws** (`win._marked`, set
  where the buffer is drawn, against `_marks_for`): one a screen, so a
  window under a menu that holds the keys draws without it.
- **No pointer DOWN or CLICK, this frame or last — unless the press is the
  WM's.** Class C (§3) mutates content without marking dirty, so any button
  activity renders live. The check reads BOTH frames because handlers consume
  `p.click` in place — a tap that opens a prompt zeroes it before the draw sees
  it, and the previous frame's edge is still in `ws._last_ptr`. A press that
  lands on chrome — a strip, its buttons, a resize zone, a full-screen
  window's row head — is the WM's (`_grab`): no content is handed it, its
  drag or its release, so it waives the check. A position/visibility-only change is inert:
  no content surface draws hover feedback (audited 2026-07-27), and anything
  that appears without input is a Class B source or it could never paint under
  the #44 gate at all.
- **The content is not a Class B animating surface.** The `_animating` sources
  that draw INSIDE an app window — the music preview's playhead, the bluetooth
  panel's async scan/pair, the update screen's progress — are excluded by name
  until §3's registration replaces the list. Sources outside app windows draw
  on other layers and need no exclusion.
- **`win.buf` is not stale.** A rebuilt buffer, or `_direct_render` painting
  past the buffer straight into the framebuffer, leaves it behind the truth.
  Where the shown ring (§14.3) says the last frame's buffer held the window's
  page where it lies now, it is refilled from that buffer
  (`copy_last_into`, §14.7: `WindowedWM._refill`), on a frame with nothing
  to paint and before any other reader stamps it (a focus loss, a move, a
  resize, this freeze); else the next paint renders live to refill it (only
  its lag, where the content speaks §14.3's protocol).

Pinned by `tests/test_wm_content_freeze.py` — a frozen-vs-live pixel-equality
check plus a case per leg of the gate.

**The caret invariant this gate is designed around.** The one way a
surface-granularity freeze breaks is a surface that animates without
signalling: it silently stops moving. The 2026-07-27 audit found the feared
case does not exist here — **both carets, the code editor's and the editor
handle's, are deliberately SOLID** (`runtime/editor_handle.py` paints its caret
unconditionally). A blinking caret added later without a Class B declaration is
exactly how this freeze breaks.

**MEASURED on P4 glass, 2026-07-27** (before the #159 L2-cache flag, which
moved every surface median; #58 carries the post-flag sweep):

| the focused editor window, per painted frame | cost |
|---|---|
| re-running the content draw (map tab) | 70.7ms |
| stamping the retained `win.buf` 1:1 | 13.7ms |
| stamping nothing, because nothing moved | ~0 |

**The on-glass verdict is humbler than that table, and it is the honest one.**
Most of the frame classes it promises were already covered on the P4: a game
beside a focused editor takes `draw_stack`'s quiet partial stack, which
bypasses every window draw (A/B: 32.2 vs 32.4ms — no change, correctly), and
an open Editor freezes a live wallpaper anyway. Where animation-armed full
paints DO happen — Settings over a live-wallpaper desk — the freeze measured
**123.1 → 109.2ms per frame**, with `tools/p4_clicks.py`'s transitions
unchanged within noise, all dirty-armed by design. The 70ms class is real on
tap-settle frames on the P4 and on every cursor-move frame on the HOST tier,
where a visible cursor defeats the quiet paths.

**The WM's press waives the DOWN-guard (2026-10-22).** It is the widening
this slice named: a press the WM captured — a drag or resize of any window, a
strip button, a full-screen row's head — reaches no content, so the focused
content stays frozen under it. A further widening goes one trigger source at
a time, behind the two gates this slice used — `tools/p4_clicks.py`
transitions must not regress, `tools/p4_surface_sweep.py` drag medians must
move.

### 14.2 Why one mechanism — the evidence, kept

§7's ledger says what becomes of the six hand-rolled invalidation mechanisms;
this is why there are six and why that is a defect rather than an aesthetic
complaint. Two of them produced the SAME bug and it cost a day to find
(2026-07-26):

- the **bar strip cache** keyed on canvas IDENTITY while the windowed WM
  alternates destinations for the same bar (root canvas through a viewport on a
  quiet frame, window buffer otherwise), so every switch read as a canvas swap
  and rebuilt: **72ms of an 86ms Settings frame, twice per gesture**;
- **`new_layer`'s pre-collect**, sized for a cart's ~384KB world, was charged
  to the bar's 36KB strip. A collect on the P4 desk is **55ms**, 113ms with the
  picker open (mark scales with the live set) — the sole source of GC pauses
  during a gesture.

**Neither produced any signal.** Nothing broke; two frames in thirty-one were
five times slower, which shows up only if you happen to measure the exact
frame, and four wrong models died before the real one (`tools/p4_alloc.py`'s
header). A cache whose key silently stops describing reality is the recurring
defect of this architecture, not an accident — which is why L3 forbids
consumer-cleared booleans, L8 demands the silent-disable direction be
testable, and §7 deletes each absorbed field with a grep-test instead of
leaving it beside the model as a seventh mechanism.

### 14.3 The shown ring: what each buffer holds of the focused window (2026-10-23)

§14.1 skips a window's content draw when nothing changed; it says nothing
about a content that changes a small part of itself every frame. Paint's
stroke is that content, and on a desk it paid twice: a held-key stroke
stamped the whole window buffer every frame, and a pointer stroke, which
direct render draws into the root, began and ended on whole frames — the
content's own record of its buffers saw a new canvas at the press, and the
window buffer it went back to at the release was the stroke's whole length
behind. #66 has the numbers.

**The WM keeps §4's per-buffer last-seen for the focused window's content
rect** (`WindowedWM._shown`, one entry a painted frame). An entry says the
frame left that buffer's content rect holding the window's page with
nothing over it — the window focused and on top, the chrome quiet, no
overlay, popover, scrim or cursor, no window under it drawing into it — and
the part of the page the frame changed: what the content says its draw
changed, nothing on a frozen frame, unknown otherwise. When the
RETAINED_FRAMES frames before this one all have entries, the buffer this
frame paints holds the page but for the changes of all but the oldest, and:

- **the stamp copies only those and this frame's**, nothing on a frame that
  changed nothing;
- **a direct render tells the content what lags** before it draws, and the
  content repaints that and its own change. A buffer the ring cannot vouch
  for is seeded by stamping the window buffer into it, when that buffer's
  own lag is known;
- **the window buffer direct render left behind keeps the content's
  changes as its lag**, which the first frame after the release repaints
  instead of the whole page;
- **a frame whose next buffer the ring cannot vouch for asks for it** while
  the board is idle, as Paint's own `settle` does for its canvas, so the
  next press finds every buffer known. A frame asked for that changed
  nothing known stops asking.

**The protocol is three names on the content's Layer**: `HELD = True`;
`retained(known, x0, y0, x1, y1)`, before each draw the WM makes of it in
its window — whether the target holds its page as of its last draw, and the
part that lags; `changed()`, after it — the part of the page the draw
changed, or None. A content counts the WM's word only when its previous draw
was one the WM spoke of too. A content without the names draws whole, as
every content did before, and its stamps stay whole. Paint is the first to
speak it.

**This is not a second invalidation mechanism.** Nothing here decides
whether a surface draws: the gate, the gens and §14.1's freeze still do. It
is §4's last-seen per physical buffer, kept below surface granularity for
one rect, from what the content says it painted on the frame it painted it —
the trust `note_damage` already places in the WM (§5.2). On the rotated
compositor the window's damage is that change rect, not the window.

**An app on the verbs draws every frame it draws straight into the glass**
(`WindowedWM._speaks`: its pages' frames say what they change, docs/scroll_and_text_2026-10.md §8)
when its window is focused and on top with nothing over it: its press,
drag, coast, key and glide frames, each with no desk restore and no
chrome, since a page moving on its own (`ws.ui_moving`) changes its
window's content alone (`_frame_is_quiet`). Its entry in the ring is its
page's rect, changed or not (`_still_shown`: a frame whose page did not
change and whose buffer holds it draws nothing), and the ring is the
voucher its pages shift by (§14.7). Each entry also says where the page
lay and whether the frame changed anything on the glass outside it (no
other window, clock, tile or chip drew), which is what a buffer's debt is
made of (§14.6).

Pinned by `tests/test_wm_shown.py`: pointer and held-key strokes on one, two
and three buffers presented in turn, every presented frame byte-equal to
full repaints, every frame from the press through the release a stroke
frame, and the stamps' bytes bounded; and by `tests/test_wm_records.py`
for the apps on the verbs (below and §14.7).

### 14.4 The desk home's still backdrop (2026-10-26)

The desk's console home is a still backdrop under three parts that change:
the hero with its details pane (the selection), the shelf's band and the
bar. A full paint draws the backdrop whole; on the P4 that is a third of the
frame, and a key press on the shelf, a tab switch or a fling's last frame
paid it because the selection or the tab sat in the statics key the drag
partial gates on. #66 has the numbers.

**The home keeps §4's per-buffer last-seen for its parts**
(`LauncherHomeLayer._still_ring`, a `PaintRing` slot a painted frame): the backdrop's
key, the selection's and the band's, recorded only for a frame whose pixels
were the home's alone (`content_alone`: no overlay, splash, cursor or owed
pill over it, the home on top, nothing animating). When the RETAINED_FRAMES
painted frames before this one all have entries under this frame's backdrop
key, every buffer holds the backdrop, and a part whose key differs from any
of theirs lags in the buffer this frame paints: it alone is put back over the
backdrop and drawn; the bar's strip, outside every such rect, is drawn
where the buffer's was drawn under another key (the ring keeps the bar's
`shown_key` beside the parts) or a part put back meets it.
Where it can be had, the backdrop's pixels come from the wallpaper itself
(`Wallpaper.restore_key`: the picture, the theme's baked backdrop or the
plain fill); a cart wallpaper keeps the drag partial and its band copy.

**A change leaves the other buffers lagging, and the home asks for the frames
that bring them to it** while nothing moves and no press waits (as the
windowed WM's `debt_owed` does). The retained re-entry frame is captured once
every buffer holds the frame, and copies only the parts that moved since the
frame it holds (`_moved_rects`).

**This is not a second invalidation mechanism.** Whether the home paints is
still the gate's; the ring only says which of its parts the buffer lacks.
Pinned by `tests/test_home_still.py`: a session of key presses, tab switches,
a drag and a fling, and the buttons' focus, on one, two and three buffers
presented in turn, the glass's every picture byte-equal to full repaints'.

### 14.5 The page held under a dialog (2026-10-26)

A dialog takes the keys and the pointer, so the page under it does not
change while it stays up; a frame that types into it or moves its focus
paid the page and its wash anyway, and on the T-Deck the two were most of
the frame. #66 has the numbers.

**A modal keeps the page under it washed** (`ui_draw.HeldPage`): the first
frame draws the page and washes it (`HeldPage.wash` over
`ui_draw.wash_page`, the toolkit's own rule: the surface less the page's
hint band at its foot), the wash storing each pixel to the held layer as
it lands (`moy_shape_shadow_io`) and the rest of the page's rect copied
there after it; each frame after puts the copy back and draws the dialog
over it with `{"wash": False}`, the override that tells a dialog its page
is washed already. The copy is held under a key of what the page is a
function of while the dialog is up — the dialog itself, the canvas, the
page's rect, the font scale and the look — and only from a frame whose page
was still (its context owed no frame), with nothing animating, on a canvas
that retains its pixels and draws into its whole buffer: a pane's viewport
(the desk Editor's) draws the page every frame. One modal is up at a time,
so the holds share one layer of the canvas's size, kept across dialogs
and freed by the Player's fit check after the home's retained frame
(`docs/shell_layout_2026-10.md` §3). The Editor's Cart info
dialog and Blocks' entry prompts hold their page; the pause and stop cards
hold the run's frame washed beside the frame `_keep_under` keeps for the
resumed game, whose layer is kept across cards and freed by the fit check
the same way; where the game's frame is the system canvas (the handheld)
an idle frame mints both, so no card's first frame does.

**A dialog's first frame takes the page from the last frame where it can**
(`HeldPage.shown`, `opened`, `last`): where the frame before drew the page
with no dialog up, still and alone on the glass (no banner, overlay,
cursor or pill over it, nothing animating), and the input that opened the
dialog changed nothing else in the page -- A on a Blocks slot with no move
among its keys, A on Cart info's door -- with no change reported since
that frame (the console's `_dirty_in`, taken before the input's own), the
first frame washes that page out of the buffer the frame drew into
(`last_buffer`, the compositor's face of §14.6's buffer; `copy_last` where
a compositor names no such buffer; one persistent buffer holds it already)
instead of drawing it, and holds it as above. A dialog a page's own draw
opens (a tap on its link) draws the page, which drew this frame anyway. The system menu
holds the handheld home the same way (`SystemMenuUI.put_home`): where the
fullscreen WM draws the home and the menu's card alone, the whole frame
under the card -- the bars, the washed content and the hint strip naming
the menu's keys -- is held under the home's retained key and the scrim's
rects, the scrim storing to the held layer as it lands, and kept across
opens while that key holds. The home captures no retained frame under the
hold, so the menu's close stamps the one from before it.

**A frame that only types into the dialog or moves its focus puts nothing
back** (a dialog's rows frame, §14.7): the Editor's dialogs begin their
frame ahead of the page (`ui_draw.DialogRows`), and where the engine
licenses one and every buffer holds the page as the frames before left
it -- each of the RETAINED_FRAMES painted frames before drew the same
dialog over the hold, alone on the glass (`HeldPage.note`, `rows`) -- the
dialog draws its changed rows and the page stays as it is. On the desk the
window's buffer is the hold: while a pane's tab has a prompt up the desk
Editor speaks §14.3's protocol (`EditorDesk.HELD`, `retained`, `changed`),
and a frame whose buffer holds the desk as its last draw left it, with no
input the desk took itself (a press, a chord) and no look, size, layout or
bar key moved, draws the prompt's rows alone; the stamp copies them, and
the full-screen row over the band stays as the buffer holds it
(`WindowedWM._row_over`).

**This is not a second invalidation mechanism.** Whether a frame paints is
still the gate's; the copy only says what the page already is. Pinned by
`tests/test_held_page.py`: the dialogs typed into and moved, on the handheld
and the desk, the dialogs the keys open (after a move no frame painted, with
a move among their keys, under the trackball's cursor), and the cards over
a run, on one and two buffers presented in turn, every presented frame
byte-equal to frames that draw the page and let the dialog wash it, the
hold put back on most of them and the last frame's page taken on the first
frames that may.

### 14.6 A frame the last one painted: the copy out of its buffer (2026-10-26)

§14.3, §14.4 and the windowed WM's debts (`_full_debt`, the popover's and
the scrim's owed paints) bring a lagging buffer to the glass by drawing
again what it lacks: a change is painted once per buffer. Where nothing
changed since the last painted frame, the buffer that frame presented holds
this frame's picture, and copying it out is cheaper than drawing it again.
#66 has the numbers.

**The backend verb is `copy_last(x, y, w, h)` on the root canvas**, bound
from its compositor where that compositor names the buffer the last
presented frame drew into (the P4s' DSI and rotated compositors, the S3s'
banded one), absent elsewhere (the web's canvas, one persistent buffer,
has none). It copies the rect, in buffer coordinates, from that buffer
into the one this frame draws into: on a P4 after fencing any queued op
still writing it (`moy_dsi_last`), whole rows through the AXI GDMA
(`moy_ppa.copy`) and any other rect a row copy on the CPU; on an S3 a row
copy on the CPU, the panel's feeder reading the same buffer alongside
(`moy_banded_last`). One persistent buffer copies nothing; no frame yet
answers False and the caller draws. `last_buffer()`, bound the same way where the
compositor has it (the S3s' banded one, the host's), answers that buffer
itself where it is not the drawn-into one, None otherwise: for a pass that
reads it instead of copying it out (a wash over the last frame, §14.5). It is the frame's first
write to what it copies. The host compositor's `last` is a test's ring of
buffers presented in turn.

**A ring that knows the last frame held this one copies instead of
drawing**, the lag it copies being the ring's own:

- **the windowed WM's debt frame of a still desk** with no popover and no
  playtest (`_RepeatLayer`): the rows the frames since its buffer was
  painted changed, where each changed nothing outside the focused window's
  page (`_debt_rows`, from the shown ring's entries, §14.3), else the whole
  glass. It copies whatever the focused window's buffer holds, so a buffer
  direct render left behind never holds a debt up (`debt_owed`). Its shown
  ring entry is the last frame's, the page unchanged;
- **a playtest's debt frame** -- the quiet one after a whole frame, and
  the frame the system menu's scrim comes over the game on, which washes
  the copy once: the whole glass
  (whole rows are the GDMA's; round the game's rect they would not be), then
  the quiet stack over it (`_lead_quiet_stack`,
  `_lead_pop_stack`), the game's fresh frame composited (and washed) and the
  popover's debts paid out of the copy;
- **a popover's ride** while the popover is as the last frame left it
  (`_pop_seq` counts its changes, `_repeating` is the frame's verdict): the
  scrim's rect, the card's, the pause menu's over its paused playtest
  and a stopped run's card over its stopped one;
- **the desk home's still frame** whose ring's last entry holds this
  frame's parts and bar: the rows its lagging parts span, whole. Its
  retained re-entry frame, which every buffer then holds, is captured on
  the next idle frame or before the next painted one draws
  (`capture_due`), so a held key's glide does not pay it.

"The last frame held this one" is checkable: the last painted frame is the
one before (`_painted_f`, the ring's entry for it), and nothing reported a
change since -- the desk settled and calm (§14.1's terms), no popover
change, no playtest window but a held one (paused, or stopped under its
card), nothing animating; or, beside a running playtest, nothing changed
but its game (the quiet frame's terms).

**This is not a second invalidation mechanism.** Whether a surface paints
and what a buffer lacks are still the gate's and the rings'; the copy only
replaces the drawing of what they name. Pinned by
`tests/test_p4_display.py` (the verb: the buffer it names, its fence, the
GDMA and CPU halves), `tests/test_wm_windowed.py` (debt frames and the
scrim's ride copying, every buffer then byte-equal to whole repaints) and
`tests/test_home_still.py`.

### 14.7 A frame that moves the last one: the shift frame (2026-10-27)

§14.3 to §14.6 bring a lagging buffer to the glass by drawing what it lacks
or copying what the last frame painted. A scrolling page moves: frame n
draws the same rows as frame n-1, moved, plus a band. The shift frame is that
change spoken as a record (`docs/scroll_and_text_2026-10.md` §3 is the
design; this is the contract): what a frame changed is ROWS (rects the draw
repainted), or one region R TRANSLATED by Δ -- the pixels moved, the |Δ|
rows the move uncovered drawn, and rows beside them.

**The two Δ rules.** Inside R a shift is always n-1 → n by this frame's Δ,
out of the buffer frame n-1 drew, licensed only when frame n-1 painted R
whole with nothing over it, whatever the ring's depth: translations never
compose. Outside R, what a buffer lacks is every frame's rows since that
buffer last took a paint, and those are drawn. Where a canvas has a single
persistent buffer (the host's and the browser's root, window buffers,
layers), that buffer held the frame before, and the move happens in place.

**The backend verbs.** A canvas's `shift_last` moves the rect of the buffer the last presented frame drew into, by (dx, dy), into
the buffer this frame draws into, and answers False where it cannot (the
caller draws); `shift_fence()` makes the rows it wrote the CPU's to draw
over. `copy_last_into(layer, ...)` copies a rect of that buffer into a
layer. Per canvas: the P4s' 2D-DMA, the S3s' row copy beside the feeder, in
place on one buffer (`device/device_canvas.py`, the compositors in
`native/moy_glass`). A canvas whose copy costs about what the raster it
saves does declares it (`SHIFT_COST`, per mille of a row's raster a row it
copies costs: the T-Deck's root), and a frame whose floor leaves too
little of R undrawn to pay for the copy draws whole (`ui_draw._pays`);
None, unmeasured, shifts.

**One voucher per region.** Who says frame n-1 painted R whole: under the
windowed WM the shown ring (`_shown`) for a window's content, never the
content (`WindowedWM._vouches`, handed to the page's host for the draw:
`ui_draw.window_frame`); on the full-screen tier the content's own `PaintRing`
(`ui_draw.PaintRing`, the slots `ScrollRegion`'s #113 ring also keeps),
because the stack draws everything around R every frame. The WM says which
(`CONTENT_RING`), and the console tells each painted frame whether the
content is alone on the glass (`ui_draw.paint_frame`: no overlay, splash,
cursor or owed pill over it, `content_alone`).

**The verbs engine's floor.** What licenses a shift frame of a verbs page is
known at the engine's begin: the input it applied (a drag, a coast's or a
glide's step, a focus move, a key typed into a field, a placed table's row
step) and the generation every page, placed table and picture inside R
carries (`gen` in the override block, the app's word that what it draws
there is a function of it). A page with no gen, a stop `ui.at` placed
inside R, a picture with no gen, a press, an activation, a stepped value, a
hover, a dialog, a popover, a tooltip: the frame draws whole; so does the
frame after a pass whose answer changed what it had already drawn
(`AppHost.again`), whose page is not the app's. The pass then draws the FLOOR -- the band, the fades,
the rows whose marks moved -- and every draw goes once through each piece
of the frame's region it reaches (outside R, and the floor's rects), so a
floor pixel takes every draw over it in a whole frame's order; the floor only
widens during the pass (a stop unlike the last frame's takes R from its top
down; a picture whose gen moved, its box). What a stop outside R draws
into R -- a box reaching in, a mark's reach -- is floor where R moved or
its marks changed, and a stop drawn before R opens makes the floor the
pass's from its start, so its part of R draws through it (Get Carts' Check
on the desk reaches four rows into its shelf). A key typed into a field joins
the floor as the field's row, and a field whose text changed with no key
typed (the app's own) takes its row during the pass: its stop keeps its
text's hash. A dialog over a held page (`wash` false) whose app gives it a
`gen` has rows frames of its own: R is its card and the drop under it as
the last frame drew them, the floor the rows of the stops whose marks move
and of the field typed into, all inside the card's rows its rounded
corners leave whole (a corner drawn again would blend over itself), and
the dialog draws through its floor alone -- what lies outside R is the
page under it, its host's to keep. A dialog that comes up otherwise than
planned (its card, title, buttons or gen) draws nothing that frame and
owes the next. `moy_ui_api.h` is the authority.

**This is not a second invalidation mechanism.** Whether a surface paints is
still the gate's, the gens', §14.1's freeze's and the Class B declaration's
(a moving page is `api_moving`); the record only says, for a frame that IS
painting, what its buffer lacks, and `shift_last` replaces the drawing of
what it moved, as `copy_last` does a whole frame. The one ring body is a
grep. Pinned by `tests/test_shift_frames.py` (the engine: drags, flings,
glides, every move size, the clamps, a gen moving mid-fling, a cover landing,
overlays, a theme change, a gap, a deferred copy landing at the fence),
`tests/test_shift_apps.py` (Settings and its pages, My Art, Get Carts,
Files, Storybook and Appearance on the full-screen console, a declared copy
cost, and a change to what a page shows made with no mark of the app's),
`tests/test_wm_records.py` (every app on the verbs in a desk window, the
Waveshare P4's desk, windows overlapping, a focus change and a move
mid-coast, a popover and the system menu's scrim over a glide, and each
change Files, Storybook and Appearance must move their gens for),
`tests/test_home_band.py` (the home's band: grid, icons and carousel,
handheld and desk, on plain, backdrop and picture themes) and
`tests/test_dialog_rows.py` (the Editor's dialogs typed into and moved
through, on the handheld and the desk, and Settings' Name field), each
presented frame, with up to three buffers taking
turns, byte-equal to whole repaints, the share of shift frames held above a floor,
and the engine's record audit failing any shift frame whose stops moved
outside its floor or reached into R outside it.

## 15. Amendment: the Surface's home is the kernel (2026-10-07)

§16 removes the table this amendment moved; the kernel epoch it adds stays.

`docs/native_kernel_2026-09.md` §8 asked for this before its sprint 3, and
`docs/kernel_survival_2026-10.md` §3.3 is the crossing that executes it. The
model does not change; what moves is where the §2 object lives and through
what a producer signals.

**What moves.** The surface record, the registry, the one monotonic mint, the
set-level epoch and the prefix-scoped sync are the kernel's: a handle table of
kind SURF (the kernel doc's §4.3), rows allocated in PSRAM, reached from
Python, Lua and wasm through the kernel's bindings. A producer signals through
the row's handle — `touch` for an attributed Class A change, `move` for
placement, `animating` for Class B, `epoch` for the un-attributed case — and a
consumer reads `content_gen` folded with the epoch and compares it `!=`
against its own last-seen. The Python leaf `runtime/surface.py` is deleted by
the glass pass; the WM mints sids from its registry keys as before and holds
handles instead of objects.

**Why now, and at what scope.** The registry is inert on every shipping tier
today: no canvas defines `begin_surface`, `wm_windowed`'s `_recording` is
never set, and nothing reads a gen — the P4 desk's retained caches and streaks
and the launcher's are the Python window managers' and stay theirs: §9's
Phase C was the kernel plan's sprint 7 (the window managers), which does not
run (`docs/native_kernel_2026-09.md` §10 question 8).
So the glass pass ports the registry as it is, inert, plus the epoch fold, and
no kernel compositor reads a gen in sprint 3; §7's ledger rows that fold
fields "in C" mean Phase C, as they always did, and with it the per-buffer
N-deep last-seen and the frame gate's predicate stay Python. Two things make the move worth doing now rather than then. From
sprint 4 a Lua or wasm cart runs with no VM on the S3 boards: L9 still holds
for it — content-dirty exactly on the frames it renders — and there is no
Python left to say so, so the producer signal has to be a kernel call. And
from sprint 3 the kernel draws frames of its own under a Python window
manager (the idle wake, the screensaver, the parked screen, the floor's
text), which needs a signal in the other direction:

    uint32_t moy_glass_kernel_epoch(void);   // moved by every frame the kernel drew itself

The frame gate reads it as one more leg — the kernel epoch moved since this
console's last painted frame counts as dirty for `RETAINED_FRAMES` frames, so
every retained buffer is repainted — which keeps the idle wake's rule (the
panel never holds a stale frame after a wake) with no change to the WMs'
caches.

**What does not change.** §1's model and §2's laws, L1 to L10; the three
producer classes of §3; the compositor contract of §4, `end_frame` included;
the annexes; and §8's graveyard, LVGL included — the kernel is code of our
own, built for the host as much as for the boards, and carries no widget tree
(L7); a backend still implements §4 and invents no invalidation mechanism
(L10). The §8 ruling against per-object
generation counters stands and is not touched by handles: a handle's
generation is the table slot's and refuses a stale handle; a content gen is the
mint's and is compared, never validated. L6's arithmetic holds on the S3
because the `_dirty` writes are untouched and the frame gate folds them into
one kernel call per painted frame. L8's two directions read the kernel's gens
and counters on every tier.

`runtime/console.py`'s frame gate and `wm_windowed`'s content freeze (§14.1)
are Python over the kernel's table: the window managers stay Python (sprint 7
does not run, `docs/kernel_toolkit_2026-10.md`), so the predicate in C, the
row §7 reserved for it, waits for whatever moves the window managers.

## 16. Amendment: the surface table is removed (2026-10-27)

§13 kept the registry for groundwork and §15 moved it into the kernel, and in
both states nothing read a gen: no canvas defined `begin_surface` or
`skip_surface`, `WindowedWM._recording` was never set, and the retained
caches, streaks, §14's freeze and ring are the window manager's own
per-buffer state. The registry is deleted with everything that fed or probed
it: the WM's gesture and window signals, the console's and the launcher's
`begin_surface` probes, the kernel's SURF table (`moy_surface_*`; the id
`MOY_KIND_SURF` stays reserved) and the glass bindings that reached it. The
sid, gen and skip-draw vocabulary of §2 to §4 names a model with no code
behind it; a backend that wants one needs the evidence L10 asks for.

What stays is the KERNEL EPOCH (`moy_glass_kernel_epoch` / `_bump`, the
bindings' `kernel_epoch` / `kernel_bump`): the frame gate's leg for the
kernel's own draws (§15).
