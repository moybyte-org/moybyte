# Surface model v1 — the presentation contract for every backend

**Status: v1.2 LOCKED (2026-08-12) — v1.1 plus the STAGE-4 AMENDMENT (§13),
which retires the web annex.** Written, then put through the parallel
adversarial architecture + perf review passes (verdicts: **LOCK AFTER FIXES**
/ **PERF CASE STANDS WITH FIXES**); all twenty findings are folded into this
revision — **§12 is the finding-by-finding traceability ledger.**
**Tracks:** #73/#105 (windowed WM), #58 (P4 port), #175/#176 (web runner), #113
(scroll), #163 (grid kernels), #76/#153 (web deltas).
**Relation to prior docs:** this is the successor that `docs/ui_damage_model_v1.md`'s
§0 review outcome asks for — it builds the §2.1 consolidation that survived that
review (one invalidation mechanism instead of six, at *surface* granularity) and
refuses everything that review killed (fine-grained damage, inferred static
content). It does not supersede `shell_ux_v1.md` (what the UI does) or
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

The verdict, from the evidence accumulated across four targets:

| layer | model | why |
|---|---|---|
| widget logic | **immediate mode** — every surface repaint re-runs its layout/draw code | one codebase reflows from 320×240 to any window size (#39); `draw(dt)` stays a pure function, which the whole golden/deterministic test harness depends on; the shell stays small |
| surfaces | **retained** — every WM surface has identity, local content, and placement, with an explicit change signal | drags/resizes change *placement*, not *content*; every target independently converged on this (P4 backdrop+stamp, web deflayer+delta, wasm view bracket) |
| dirty tracking | **explicit signals owned by producers** — generation counters + a declared `animating` class + the pointer leg (§3: today's gate has THREE legs, and all three survive) | inference of "static" lost twice here (Fold-2 revert; scroll-as-blit over web); booleans cleared by consumers is where the six ad-hoc mechanisms diverged |
| compositing | **per-backend strategy** behind one contract | the browser compositor is delegated-to on the GPU (validated per-device, §5.4 — not assumed free); the P4 must stamp in place (a 1:1 full-screen copy is ~26ms, PSRAM-bandwidth-bound, MEASURED #58); the S3 full-repaints 320×240 and must pay ~nothing for this model (§5.1 shows the arithmetic) |
| widget tree | **none, ever** | the `ui_damage_model_v1` §0 review killed the perf case; LVGL left the draw path at 47→90fps and has no P4 port; half our surfaces are free-form cart output no tree can model |

## 2. The Surface object

Lives in a **new leaf module** (`runtime/surface.py`), imported by
`wm_windowed.py` and the web serve glue only — **never by `wm.py` or
`console.py`**, which the S3 build freezes verbatim (`build.sh` stages
`runtime/` wholesale; the only denylisted shell file is `wm_windowed.py`).
This is what makes §5.1's "the S3 executes no new code" true by construction.

```python
class Surface:
    sid          # stable identity, minted from the WM REGISTRY key
    domain       # "system" | "game" (the existing begin_surface domain)
    w, h         # CONTENT size — the surface's local coordinate space
    x, y, scale, z   # PLACEMENT — WM-owned; content code never reads or writes it
    content_gen  # stamped from the WM's ONE monotonic counter when content changes
    place_gen    # stamped from the same counter on move/resize/restack/show/hide
    animating    # this surface repaints without any bump while true (§3 class B)
```

**sid rules (the make-group lesson).** sids are minted from the WM's registry
keys, **never from content kinds** — the shipped `key == win.kind` comparison
silently never matched for the shared "make" group (window key `"make"`,
content kind `"picker"`), which disabled its drag content-freeze everywhere
(`wm_windowed.py`, the identity-resolution fix). sids are world-qualified: the
fullscreen Library layer and the windowed desk backdrop both carry the layer
id `"launcher"` today (`launcher_layer.py` vs `wm_windowed.py`) — one sid,
two contents across a world flip is exactly the aliasing §3 forbids.

**Gen minting (no per-object counters, ever).** Both gens are stamped from
**one monotonic per-WM counter** — the `atlas_gen` pattern (`web_view.py`:
increments, never restarts), which §2's L3 cites as prior art and which this
rule generalizes. Rationale: surfaces are destroyed wholesale in the real code
(`on_relayout` does `_wins.clear()`; every world flip rebuilds all windows)
while per-client caches (`SurfaceDelta._last`) persist per connection. A
per-object counter restarting at 0 can collide with a client's stored gen and
alias into a wrong `{"same":1}` — the browser would replay a **dead window's**
content. A monotonic mint makes recreation safe for free: a reborn surface
carries a fresh, higher gen, so every consumer sees it as changed. Comparisons
are always `!=`, never `>`.

**This is a re-partition, not a promotion.** Today's Stage 9 marks are
draw-stack **layer** ids: ONE `"windows"` surface covers all windows + chips,
one `"launcher"` the whole desk (`console.py` `_surf` forwarding;
`wm_windowed.py` `_WindowStackLayer`). Per-window sids mean new
`begin_surface` call sites inside the window draw path, and the web payload
*shape* changes. Phase A's golden gate is therefore scoped to **pixels**;
payload-shape tests are re-baselined knowingly (§9).

### The laws

- **L1 — content/placement separation.** A move or restack touches
  `place_gen` only. **Resize is the stated exception:** it reflows content
  (`_resize_window` rebuilds the buffer and layout context), so resize =
  `place_gen` + a WM-sanctioned `content_gen` bump — the one place the WM
  writes a content gen, and it is doing so as the owner of the reflow.
- **L2 — dirty is explicit, in three producer classes (§3).** Only producers
  signal change: gen bumps, the declared `animating` class, and the pointer
  leg. Inferring "static" from draw streams is FORBIDDEN (§8: Fold-2, web
  scroll-as-blit).
- **L3 — gens come from one monotonic per-WM mint** (§2 above), compared `!=`
  per consumer against its own last-seen. Never a boolean a consumer clears:
  N consumers (P4 compositor, each web client's `WsClientState`, the L8
  harness) must not coordinate. Prior art: `atlas_gen`, `ws._cover_gen`,
  `sheet.gen`.
- **L4 — surfaces record and draw in LOCAL space.** The WM owns the transform
  to the root canvas / the wire (#175's `view` bracket, generalized). Content
  code positioned in desktop coordinates is a bug. The wire-compat
  consequence of this law is owned honestly in §6 (it is a protocol break,
  versioned — not silently compatible).
- **L5 — compositing strategy is backend-owned.** The shared layer decides
  *whether* a surface changed; the backend decides *how* the glass catches up.
- **L6 — the degenerate tier pays ~nothing, and here is the arithmetic.** On
  the S3: no Surface objects exist (§2 leaf module is not staged there); the
  only executed shape is the already-shipped no-op probe pattern
  (`begin_surface` is a `getattr(..., None)` probe per stack layer, live
  since Stage 9), and producer signals ride the **existing** `ws._dirty`
  writes unmodified (§3). ESTIMATED added cost: zero new per-draw work; ≤10
  event-driven probe/no-op calls per frame ≈ single-digit µs.
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
  **on frames it renders** — under frameskip (#77) logic ticks at full rate
  while render runs at half, and the gen moves only with renders, or web
  clients would re-ship at 60 while glass draws 30.
- **L10 — new backends implement §4; they do not add invalidation
  mechanisms.** This is the "we don't redesign this again" law.

## 3. The dirty protocol — three producer classes, all owned

**Today's #44 gate has three legs**, and the model absorbs all three —
`_needs_redraw` is `_dirty OR _animating(dt) OR ptr_state != _last_ptr`
(`console.py`), and the windowed content-freeze (`_content_static`,
`wm_windowed.py`) re-derives the same three conditions per window. A model
that absorbed only the `_dirty` leg would freeze live wallpapers, the music
playhead, OTA progress, and every paint stroke — the review's top finding.

**Class A — explicit gen bumps.** The signal rides the **existing**
`ws._dirty = True` writes, *observed by the surface layer, not rewritten*: an
un-attributed dirty is a **global epoch bump** (all visible surfaces count as
changed — safe, never wrong, merely unprofitable). Per-surface attribution is
**opt-in per site**, added only where audited, via a no-op-probed verb (the
`begin_surface` pattern) so shared `*_layer.py` code stays S3-safe. The audit
is a named Phase A deliverable — there are **~179 raw `_dirty = True` sites
across 22 files** (grep-verified; ui_damage §0.2.7 counted the same), several
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
(§7). An animating surface needs no bumps and is never skipped.

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
| wallpaper (static, e.g. `moy_night.moy`) | nothing — idles free *by construction* |
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
`_content_static` freeze promoted from one special case to the contract.
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
+ full repaint under the gate. **No Surface objects exist here** (§2 leaf
module is not staged); producer signals ride the unmodified `_dirty` writes;
L6 shows the ≤single-digit-µs arithmetic. Hardware constraints bounding any
future change: single `tx_color` full-screen flush; SD/display shared SPI
host; the I2C poller thread (#69). This annex exists mostly to say: **do
nothing here.** Phase A's gate for this tier is "the per-frame executed path
is provably unchanged" — grep-tests pin the no-op shapes; note the frozen
*source* of shared files may still drift textually (comments, unrelated
edits), so the gate is on executed shape, not file bytes.

### 5.2 P4 — windowed on-glass

`WindowedWM`. Surfaces: wallpaper/desk, each `_Win` + its chrome band, bar,
cursor, overlays, the game viewport. Strategy: **in-place stamps, never full
recomposite** — a 1024×600 1:1 copy is ~26ms PSRAM-bandwidth-bound (MEASURED
#58), which is why this backend's placement-dirty handler is the shipped drag
machinery: `_BackdropLayer` retained cache, dirty-union restore
(`blit_strip_rect`), body-subtract, and the async-PPA stamp-defer expressed
through §4's `end_frame()`. Its last-seen state is per-buffer N-deep (§4) —
the streak/sig fields are absorbed and deleted (§7). **Buffer ownership:**
`moy_alloc` has no `free()`, and resize/world-flip/relayout rebuild window
buffers today — Surface buffers are minted with an owner, reused per slot,
never evict-and-reallocate. Phase C claims **no perf change** — the gate is
"drag fps not regressed" on the #156 on-glass suite, **naming the make-group
window's drag explicitly** (the last silent-disable bug lived exactly there).
Games: fullscreen in the play world; windowed via the PPA upscale composite
(PPA is upscale-only — §8).

### 5.3 Host sim — reference implementation

pygame; buffers are cheap, so this backend implements the contract in its
plainest form and serves as the test bed: golden pixel-parity before/after
Phase A **including windowed-size goldens** (the 320×240 `_base` goldens
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
`{"id", "domain", "cmds"}` or `{"id", "domain", "same": 1}` (`web_view.py`
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
| scroll-as-blit `RETAINED_FRAMES` (#113) | **per-backend**: device root path unchanged; web root stays 0; per-surface in D under §6's invariants | — |
| `_cover_runs` / `_cover_gen`, `atlas_gen`, `sheet.gen` | **unchanged** — the L3 prior art | never |
| `assets_json` memo key (wasm) | **unchanged** — serialization concern | never |

## 8. The graveyard — do not re-try without new evidence

- **LVGL / any third-party retained UI.** Left the draw path at 47→90fps on
  the S3; no P4 port. Recorded with numbers in `ui_damage_model_v1.md`.
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

- **Phase A — name it (refactor + audit).** `runtime/surface.py` (leaf, not
  staged to S3); gens from the per-WM mint; Class A = observed `_dirty`
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
The tracker is #192; the reasoning is `docs/moycore_plan_2026-08.md` §3.2/§6.

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

**One thing this amendment deliberately does NOT do: delete `runtime/surface.py`.**
The registry (`Surface`/`SurfaceSet`, the monotonic mint, the epoch, the
prefix-scoped sync) was reached only through `wm_windowed`'s
`if not self._recording: return` guards, and nothing sets `_recording` any
more — so on every shipping tier that code is now inert. It stays because it is
Phase A/C groundwork for the raster tiers, which this stage does not touch;
what died is its recording DRIVER, not the model. Stated here so the next
reader finds the answer instead of discovering an unreachable branch and having
to guess whether it was an oversight. Wiring it on raster, or deleting it, is
Phase C's call to make with evidence.

**What replaced the L8 stream-hash gate.** §5.4's accounting proved "every
surface stream that changed was covered by a moved gen" — a pixel-less
substitute for L8 that only the recording tier could run. The wasm head has
pixels now, so it takes the same gate every other raster tier takes, and its
screenshots are directly comparable to the host's (`pageshot.mjs`, which no
longer reconstructs a replayer to produce them).
