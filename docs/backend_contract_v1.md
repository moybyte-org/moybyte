# Backend contract v1 — how shared code asks a backend what it is

**Status: v1.1, REVIEWED (2026-08-01).** v1.0 was put through parallel
adversarial architecture + perf review passes (verdicts: **LOCK AFTER FIXES** /
**PERF CASE STANDS WITH FIXES**); the findings are folded here and **§9 is the
finding-by-finding ledger**. Written after two bugs in one session
(`b0c442a`, `9452bf9`) that were the same structural failure.
**Tracks:** #58 (P4), #105 (two worlds), #175/#176 (web runner), #151 (wasm).
**Relation to `surface_model_v1.md`:** that doc is **LOCKED** and governs *when*
pixels are recomputed (invalidation, retention, compositing strategy). This doc
governs *how shared code discovers what a backend can do*. Orthogonal halves of
one seam. This doc **must not** re-open that doc's §8 graveyard or §10
non-goals, and where they touch, `surface_model_v1` wins — including its own
rule that a change to it is a change to THAT file first.

---

## 1. The problem, in evidence

Shared code discovers backend capability by probing for an attribute and
treating **absence** as a fallback signal:

```python
if getattr(gc, "buf", None) is None:      # "must be command-only"
    return
```

### 1.1 There is no such thing as "a backend"

The first review's blocking finding, and it is right: capability is a property
of the **(game canvas, system canvas, world)** triple that an entry point
actually constructs — not of a target board. Seven pairs ship from five
"backends" (**table refreshed 2026-08-28**: the 2026-08 streaming sunset deleted
`TeeCanvas`/`CommandCanvas`/`ViewCanvas` and the host's pure-Python `Canvas` went
on 2026-08-15, so the rows below are today's; the finding got STRONGER, since two
of the three boards now disagree with each other):

| entry point | game canvas | system canvas |
|---|---|---|
| P4 `run_desktop` | `DeviceCanvas` | `P4SystemCanvas` — **distinct objects** |
| T-Deck `run_desktop` | `DeviceCanvas` | *the same object* |
| Guition `run_desktop` | `DeviceCanvas` 320×240 | `SystemCanvas` 480×320 — **distinct** |
| host sim 320×240 | `DeviceCanvas` (via `host_canvas`) | *the same object* |
| host sim windowed | `DeviceCanvas` | `HostSystemCanvas` |
| wasm handheld tier | `WebSystemCanvas` | *the same object* |
| wasm desktop tier | `WebSystemCanvas` 320×240 | `WebSystemCanvas` — **distinct** |

`b0c442a`'s own commit message states the discriminator as a pair-plus-world
fact: *"the P4's play world is the only place a raster tier has two distinct
canvases AND takes this path."* Any model with one row per board cannot express
that. **A four-row matrix is itself the lossy abstraction that caused the bug**,
and "every backend IS a Presenter" would re-encode it one level up.

### 1.2 The census (mechanical, corrected)

v1.0 published a table built by grepping each board's `moy_runtime.py`. That
missed the shared `device_canvas.py` — the file §2 praises as already-shared —
and so got the T-Deck column wrong in exactly the direction that flattered the
thesis. Regenerated over `canvas.py` + `web_view.py` + both boards'
`moy_runtime.py` **and** `device_canvas.py`:

| verb | host | tdeck | p4 | web | sites |
|---|---|---|---|---|---|
| `font_scale` | yes | – | yes | yes | 18 |
| `flush_batch` | yes | **yes** | yes | – | 10 |
| **`buf`** | **yes** | **–** | **–** | **–** | **10** |
| `begin_surface` | – | – | – | yes | 6 |
| `reclaim_layers` | – | **yes** | yes | – | 4 |
| `reset_state` | yes | yes | yes | yes | 3 |
| `view` | – | – | – | yes | 3 |
| `skip_surface` / `clip` / `blit_strip_rect` / `blit_game` | | | | | 2 each |
| `blit_cover`, `blit_strip_async`, `scroll_rect`, `fill_rects`, `palette`, … | | | | | 1 each |

**Total canvas-receiver probes in `runtime/`: 76** — not v1.0's "~30", which was
asserted rather than counted. Corrections: `flush_batch` and `reclaim_layers`
are on **both** boards (`device_canvas.py`); `begin_surface` is 6 sites, not 2.

**The headline survives the correction.** `.buf` is defined on **one** of the
four, the host, and ten shared sites branch on it. "No `.buf`" is the majority
case and conflates three different things: T-Deck (no buf; `sys is game`, so
compositing is a designed no-op), P4 (no buf; RGB565 in `_buf`; native
`blit_game`), web (genuinely command-only). One missing attribute cannot encode
three answers.

### 1.3 The bugs

**`b0c442a`** — `composite_game` asked "do you have `.buf`?" meaning "are you
command-only?", got `True` for the P4, returned. Cart ran, input worked, exit
worked; no picture.

**`9452bf9`** — the bezel retention latch keyed on geometry alone, so a relaunch
kept the previous screen. Same family: a region that stops being written while
N buffers rotate.

**A third instance already shipped**, on a seam v1.0 did not cover at all:
`moy_webserver.py`'s missing `effective_input_kinds` re-export dead-ended the
T-Deck web view from 2026-07-21 to 2026-08-01 and presented to the owner as
*"T-Deck WiFi is broken."* See §8.

### 1.4 Why the tests missed two of them

The fullscreen composite test runs against the **host** canvas — the one backend
with `.buf`. (v1.0 said "three of four backends had no coverage"; that
overstated it — the web-console suite covered the recording fallback until the
2026-08 streaming sunset retired that transport, and at moycore stage 4 the
**recording fallback itself was deleted**: the wasm head rasterizes with the
boards' own kernel, so every remaining backend has a `.buf`. The uncovered ones
are the two **device** pairs.)

---

## 2. What is already right — do not churn it

- Both boards already share **one** `device_canvas.py` (T-Deck tracked, P4
  staged, byte-identical).
- Host and P4 already use inheritance: `Canvas → SystemCanvas`,
  `DeviceCanvas → P4SystemCanvas`.
- The four backends already have correct implementations of "composite the
  game"; they are selected by `getattr` instead of by polymorphism.
- `tests/test_device_canvas_parity.py` already runs the **real** `DeviceCanvas`
  under CPython behind `framebuf` + `moy_gfx` stubs — a device adapter is a
  known technique here, not new risk.

**L1. The defect is dispatch, not layout.** A proposal that moves files without
removing a capability probe is out of scope.

---

## 3. The laws

- **L2. No optional verbs on the contract.** Contract verbs are defined on the
  base; a backend overrides, never omits.
- **L3. Inert defaults only where one exists.** The base supplies a no-op for
  `flush_batch`, `begin_surface`, `skip_surface`, `place_span`. It does **not**
  supply one for `composite_game` — that is **abstract**. *(Revised: v1.0 said
  the base always implements "the slow, correct, portable path". False here —
  host `buf` is 1-byte palette indices, device `_buf` is RGB565. An index loop
  on the P4 is fast and **wrong**, half-width garbage, not slow and correct. The
  only universally correct default is the command path, which is unusable on
  device. A law that is false exactly where it matters is worse than an
  explicit abstract verb.)*
- **L4. Capability is declared, never inferred** — and the declaration must be
  the **pixel format** (`indexed` / `rgb565` / `command`), not a `raster`
  boolean. A boolean would collapse three states into two: the doc's own
  opening complaint, relocated.
- **L5. Shared code calls the contract.** A capability probe in `runtime/` is a
  bug, pinned by §5 Phase 3.
- **L6. Wiring facts stay runtime tests.** `sc is gc` is checked at the call
  site, not baked into a subclass. *(Revised: v1.0 proposed "the T-Deck subclass
  returns early because `sys is game`". That hardcodes as a class property what
  `console.py:685` decides at runtime — and the web console transport (deleted
  2026-08, streaming sunset) used to flip that wiring live. It is `b0c442a`'s
  category error rewritten.)*
- **L7. Composed backends declare, never inherit.** The law stands; its example
  is now HISTORICAL — `TeeCanvas` went in the 2026-08 streaming sunset, and this
  is what it did: `__getattr__` forwarded unknown names to the wrapped canvas, so
  a Tee's capability set was the **union** of what it shadowed and what it
  wrapped — on a P4 with the web view on, `blit_game` would forward to the native
  RGB565 blit and the recorder would never see the frame. A wrapper names its
  Presenter explicitly; nothing in the tree composes a canvas this way today, and
  the law is what keeps it that way.
- **L8. Strategy stays the backend's.** This doc moves *dispatch* only.
- **L9. The S3 budget is measured, not assumed.** *(Replaces v1.0's "the S3 pays
  nothing", which was falsified by its own §4 — see §6.)*

---

## 4. The shape

```python
# runtime/present.py — STAGED TO EVERY TARGET, including the S3 (see §6)
class Presenter:
    pixels = "indexed"                 # L4: indexed | rgb565 | command

    def composite_game(self, gc, dst, ox, oy, scale, src=None):
        raise NotImplementedError      # L3: no portable default exists

    def flush_batch(self): pass
    def begin_surface(self, sid, domain): pass
    def skip_surface(self, sid, domain): return False
    def place_span(self, ox=None, oy=0, scale=1, w=0, h=0): pass
```

- `HostPresenter` — indexed raster, the reference implementation
- `DevicePresenter` — `moy_gfx` + PPA (`pixels = "rgb565"`)
- `RecordingPresenter` — command stream: `spr` composite + span bracket

Selected by a **factory over the (game, system, world) pair** of §1.1, not by
"which board am I".

**Deliberately NOT on the contract:**

- **`RETAINED_FRAMES`** — it is per-**draw-target** and runtime-mutated
  (`canvas.py:123` = 1; `device_canvas.py:273` = 2 but `:1758` sets it to 1 per
  *layer*; the P4 sets it per *instance* from `len(comp._fbs)`; `web_view.py`
  pins 0 on three classes specifically to shadow `__getattr__`). Hoisting it to
  a backend class makes every layer report the root's value — which is the
  ghosting bug `moy_runtime.py:157-164` already records ("shifted by ~twice the
  real delta and ghosted a second copy of every card").
- **`end_frame()` / `defer`** — `end_frame` belongs to `surface_model_v1` §4 and
  adding it here would amend a locked doc from the outside. And it is not
  sufficient anyway: the P4 has **two** deferral mechanisms with different
  orderings (the game composite fires `blit_async` immediately and defers only
  the scan-out to `present_pending()` on the *next* loop iteration; the drag
  stamp deliberately does not kick until `P4Compositor.flush`). `defer` is
  likewise a P4 strategy hint that shared code computes — an L8 violation.
  **Frame lifecycle is out of scope for v1.**
- **`view`** — renamed `place_span`. `view(w, h)` is a **cart-facing verb**
  (`host_api.py:491`), permanently out of scope per `surface_model_v1` §10, and
  the collision would be a trap.

**Scope: 76 probes (§1.2), of which two kinds must be split.** ~4 of the `.buf`
sites are *discrimination* (answered by `pixels`); ~6 are pixel **access**
inside composite loops. Converting the access sites means moving
`_composite_via_spr`, `_backdrop_blit` **and the bezel latch** into
`present.py` — code `surface_model_v1` §7 schedules for **deletion** in its
Phase C. Those sequence after that phase, not into Phase 1.

**Explicitly out of scope, by name:** `font_scale` (18 sites — a layout concern,
not a backend one), the `_batch_*` diagnostics, `lib_mult`/`files_btn`
(intra-shell), and the `make_api` cart verbs (`surface_model_v1` §10). Naming
them keeps Phase 3's allowlist from becoming a permanent exemption list.

---

## 5. Phasing and gates

- **Phase 0 — the conformance suite (do this first, alone).** One test body
  driven over a **coverage matrix: the seven pairs of §1.1 × {play, desk}**,
  with `b0c442a` and `9452bf9` as two members. Uses the real `DeviceCanvas`
  behind the existing `test_device_canvas_parity` stubs where it can.
  **It proves DISPATCH ONLY** — the P4's `blit_game` bottoms out in `moy_ppa`
  hardware, so a fake with `blit_game` tests the fake. #156 remains the on-glass
  gate. *(v1.0's gate — "it reproduces both bugs when reverted" — was already
  green at HEAD, since both commits shipped regression tests. A gate that cannot
  fail is not a gate.)*
  **Standalone value; the recommended stopping point if the rest is deferred.**
- **Phase 1 — the contract, host + web.** `+runtime/present.py`; host and
  recording presenters; the discrimination sites converted. Gates: host goldens
  pixel-identical **including windowed sizes**; web payload shapes byte-identical
  — and this must land **before** `surface_model_v1` Phase B, which versions the
  protocol and re-baselines those payloads knowingly.
- **Phase 2 — devices.** Device presenter; P4 native overrides. Gates:
  **#156 on-glass green**; a **governor-ON** (`console.FPS_GOVERNOR`) timed span
  over N fixed frames via `P4Board.pyval`, plus `ws.note_cost` counters
  asserting each converted site executes the **same number of times** before and
  after (`test_a_drag_touches_no_storage_and_rebuilds_no_cache` is the working
  template). **S3: owner-play verified only** — that board's USB-CDC RX is dead
  under the desktop and no on-glass harness exists for it.
- **Phase 3 — the grep gate.** No capability probe survives in shared
  `runtime/`, with §4's verb list as the allowlist and §4's out-of-scope list as
  the documented exemptions. Without this the pattern grows back and the
  contract is strictly worse than no contract.

**Not in this doc:** `surface_model_v1` Phase C (per-buffer N-deep last-seen
replacing the streak/sig family). That is what `9452bf9` patched by hand. It
sequences **after** Phase 0 here — the conformance suite is what makes it safe.

---

## 6. Risks, honestly

- **L9 / the S3 budget — the correction that matters.** v1.0 claimed
  `present.py` would be "a leaf module, staged per-target like `surface.py`".
  **Inverted.** `surface.py` escapes the S3 only because its sole importer is
  `wm_windowed.py`, the one file the S3 build denylists. The probe sites are not
  leaf-shaped: `wm.py`, `wallpaper.py`, `console.py`, `launcher_layer.py`,
  `canvas.py`, `player.py` and `ui.py` are **all staged to the S3** and hold the
  large majority of the sites. `present.py` **is an S3 module** and the S3 gains
  a class hierarchy, an instance and a per-frame indirection.
  **Gate on three published numbers, not a slogan:** frozen-image delta
  (reported, no arbitrary threshold — v1.0's "≤2KB" was undefended and probably
  self-failing at ~2KB for `present.py` alone), boot `free-int` unchanged, and
  launcher live set unchanged via `micropython.mem_info()` — **not** bare
  `gc.mem_free()`, which reads ~28MB against ~1.4MB real on the P4 port.
  Flash is not the scarce resource here (S3 app slots 4MB, P4 45% headroom);
  **internal SRAM and GC live set are.**
- **The dispatch tax is real but small, and v1.0 pointed at the wrong hazard.**
  None of the converted sites is per-**draw-call**; they are per-frame,
  per-gesture-frame or per-cart-lifecycle. The per-draw path is `spr`/`rect`/
  `print` through the native gates, untouched. ESTIMATED worst case ~20 sites ×
  ~9µs (#63's warm trivial-body call) ≈ **~0.18ms/frame, 0.5-1.1%** of the S3's
  16-33ms frames. This does **not** re-introduce the #43/#63 tax, which was
  50-1536µs × 400-1000 calls/frame. The real risks are the S3 object graph
  above and the measurement gap below.
- **Nothing we have can detect a 1% regression.** #66's own bands are 22-38%
  wide (Brick Siege 50-61, Letter Blitz 42-58) and P4 sweep medians quantize to
  4ms. The control: #172's **~8fps (15%)** regression sat on master and was
  caught by *owner play*, not by any gate. Hence Phase 2's counter-based gate
  rather than an fps claim that would pass unconditionally.
- **A big-bang refactor is the danger.** 76 sites across seven pairs, two of
  which need physical hardware. Phase 0 first is the mitigation.
- **This doc could become a seventh mechanism** if Phase 3 does not land.

---

## 7. Open questions

1. ~~Is `raster` one predicate or two?~~ **RESOLVED** → neither; it is the pixel
   format (L4), because a boolean re-collapses three states into two.
2. ~~Does `Presenter` merge with `surface_model_v1` §4's compositor?~~
   **RESOLVED for v1 → they sit BESIDE.** The lifetimes are incompatible:
   Presenter is per-canvas and process-long; §4's last-seen is per **physical
   buffer** on the P4 and per **connection** on the web (`WsClientState` is
   re-minted on reconnect); the gen mint is per **WM** and destroyed wholesale
   on a world flip. Merging is buildable but is a **redesign of a locked doc**,
   which its own rules say must start there. Re-open after its Phase C.
3. Do the layer/`Image` probes (`reclaim_layers`, 4 sites, both boards) belong
   on the Presenter or on a separate layer-allocator contract?

---

## 8. The staging seam (new — the cheapest fix in this doc)

**Status: RESOLVED 2026-08-15 (#161 Phase 3). Both boards stage by denylist,
and the closure test exists. What follows records what was true, what this doc
got wrong about it, and what shipped.**

Both reviews landed on a seam v1.0 ignored, and it had already produced a bug.
v1.1 stated the seam like this:

> - The S3's `build.sh` stages an **explicit `cp` allowlist**.
> - The P4's and web_runner's are **denylists** (glob + `DENY`).

**Half of that was wrong, and it is worth recording which half.** The S3's
allowlist was real (~70 hand-written `cp` lines). The P4's was **also an
allowlist** — a hand-written `for f in editors.py editors_base.py … ; do cp`
list — and had never been anything else; `git log -S'DENY' --
docs/backend_contract_v1.md` is empty, and so is the same search over that
board's `build.sh`. Only `web_runner/build.sh` ever globbed-minus-`DENY`. So
the real risk was **twice** what this section claimed: a new `runtime/*.py`
reached the web automatically and **neither board**, silently, with no test
asserting staging completeness. That is the same failure family as §1.3's bugs,
and it is how the `effective_input_kinds` re-export went missing for eleven
days — and, later, how the T-Deck went without the web console the P4 had.

A doc that describes a mechanism the code does not have is worse than one that
says nothing, because the next reader trusts it. This one was trusted: the
P4's own `surface.py` docstring says "the S3 build.sh denylists this file",
which was aspiration, not description, until Phase 3 made it true.

**What shipped (2026-08-15):**

- Each board declares its staging in **`firmware/<board>/board.toml`**
  (`tools/board_config.py` reads it; `build.sh` calls the stager and holds no
  module list). Shared modules are a **denylist over `runtime/*.py`**, one
  entry per exclusion, each carrying its `kind` and the prose reason — per
  #161, "whatever moves, the prose rationale moves with it".
- The two boards' shared sets now differ by exactly `wm_windowed.py` and its
  `surface.py` leaf — the presentation TIER — and by nothing else. That is an
  asserted invariant, not an observation.
- The stager also **prunes**: `modules/` is gitignored and the frozen manifest
  freezes the whole *directory*, so an unstaged module used to stay in the
  image forever on any tree that had built before. It was not hypothetical —
  `canvas.py`, `palette.py` and (on the P4) `moy_lua_glue.py` were all still
  there, and therefore still frozen, when this landed.
- `tests/test_staging_closure.py` is the completeness gate this section asked
  for: every module imported by a staged module must itself be staged, per
  target, deriving the frozen set from the declaration rather than from
  `modules/` on disk. `tests/test_board_toml.py` checks the declaration and its
  reader.

The one target still declaring its denylist inline in shell is
`firmware/web_runner/build.sh` (`DENY=`), which the closure test still parses.

**Also declared out of scope by name** (same "None means absent" pattern, other
objects — listed so they are decisions rather than omissions): `ws.wifi`,
`ws.updater`, `ws.web_hook`, `ws.can_manage`, `ws.lua_runtime`.

---

## 9. Review ledger (traceability)

| # | finding | disposition |
|---|---|---|
| B1 | capability is a (game, system, world) triple; 7 pairs, not 4 backends | §1.1 rewritten; factory in §4; Phase 0 gate is the pair matrix |
| B2/F1 | `present.py` is not leaf-shaped; L7 falsified by §4 | L7 → **L9**; §6 rewritten with three measured numbers |
| B3 | `RETAINED_FRAMES` is per-draw-target; hoisting recreates a ghosting bug | removed from §4 |
| M1/F4 | no portable default for `composite_game` (indexed vs RGB565) | L3 scoped; verb made abstract |
| M2 | "T-Deck subclass returns early" repeats `b0c442a`'s category error | **L6** — wiring stays a runtime test |
| M3/F2 | §1 matrix wrong on `flush_batch`, `reclaim_layers`; `begin_surface` undercounted | §1.2 regenerated mechanically |
| M4/F3 | 4 verbs missing; "~30 sites" asserted, not counted | §1.2 = **76**; out-of-scope list named in §4 |
| M5 | `TeeCanvas`/`ViewCanvas` break L2 both ways | **L7** (composed backends declare) |
| M6/F6 | L7 unfalsifiable; 2KB undefended; wrong resource; no S3 instrument | §6; Phase 2 states S3 = owner-play only |
| M7/F5 | merge premature; `end_frame`/`defer` insufficient and out of contract | Q2 resolved "beside"; both removed from §4 |
| M8 | Phase 0's gate already green at HEAD | restated as coverage matrix; dispatch-only caveat |
| F7 | risk mis-sized vs #43/#63 | §6 re-derived (~0.18ms/frame) |
| F8 | "unchanged fps" cannot gate | Phase 2 → governor-ON span + `note_cost` counters |
| F9/N4 | Phase 1 ↔ surface_model Phase B payload collision; coverage overclaim | sequenced in Phase 1; §1.4 corrected |
| N1 | `view` collides with the cart verb | renamed `place_span`, real signature |
| N2 | two latent sites unmentioned | §1.3 + Phase 1 disposition |
| N3 | `.buf` sites are two kinds; latch is surface_model Phase C's | split in §4 |
| X1 | build staging is the drifting seam; produced a third bug | **§8**, promoted to prerequisite |
| X2/X3 | cart verbs, service attach points | declared out of scope by name (§4, §8) |
| X4 | frame lifecycle | out of scope for v1 (§4) |
