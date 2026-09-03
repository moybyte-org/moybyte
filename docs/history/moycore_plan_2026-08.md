# Moycore (2026-08): one engine under two languages — the C play path, the Python shell, and the sunset ledger

**Status: v13 (2026-08-13) — THE LADDER IS WALKED, AND THEN TWO THINGS
v12 CLAIMED TURNED OUT TO BE HALF TRUE.** Both are fixed; both are recorded
here rather than quietly amended, because the shape of the mistake is the
lesson.

*One: the browser was never on moycore.* v12 said "all five rungs" and meant
the boards and the host. `web_runner/build.sh` staged `moy_lua` and not
`moycore`, and `web_boot` injected the old factory, so the tier most likely to
be somebody's first Moybyte ran a different engine from the two that had been
audited. It stages the usermod now and wires the boards' chooser verbatim
(+25KB wasm; sakura_lua/brick_siege_lua/ray_lua all report `moycore.active()`
True, screenshot correctly, and the shipped page boots in real Chrome).

*Two: on every tier, moycore was running almost no real cart.* Every Lua seed
calls `make_layer` or `image()` in `_init`. Those are OBJECT-valued, and no
runtime here marshals objects — so the registered trampoline handed the cart
`nil`, `_init` raised, the load failed, and `_make_lua` fell back. On device
that printed a decline nobody was reading; on the host it was silent. The
per-cart evidence that had been collected was real and was collected on the
one cart that happens not to do this. The fix is the glue moy_lua has always
had, promoted to `runtime/lua_ext.py` and imported by all three: one prelude,
one int-handle registry. moycore grew `exec()` for the chunk (the prelude must
run after `register` and before the cart — the window `load()` closes), and the
host binding's dispatch went 4 → 8 int args because `__layer_spr` takes seven
and four silently dropped colorkey/scale/flip.

*What both have in common:* the gate said yes and nothing asked the carts. The
new pins do — every Lua SEED, launched through the real shell, must be a
`MoycoreHostRun`, and a fallback is a successful start, so the assertion is on
the run's TYPE. Chasing this also turned up a crash that predates all of it:
`moy_console` holds sheet and map by pointer, a brand-new project has neither,
and libmoy's binding dereferenced both — `function _draw() spr(0,0,0) end` in
an empty cart segfaulted, which on a board is a reset with no message. Fixed
upstream (degrade to empty, per SPEC.md §10's rule) and vendored.

**§9'S LAST OPEN QUESTION IS CLOSED** (layers/images stay Python-side, on a
cart census: one cart, one blit per frame, against the cost of a second
console in C). Stage
3's code landed too: the S3 stages moycore, injects the same per-cart
chooser, and links `usermod_moycore` into a full flash image, with the
allocator moved to internal-SRAM-first because the all-PSRAM version is a
measured ~2x regression on that board. moycore
runs a Lua cart's whole frame in C on P4 glass (`moycore.active()` True
under a conformance cart, on-glass suite 22/22); `canvas.py`'s verbs
delegate to libmoy with the conformance goldens unchanged; the host runs
the boards' own Lua for every cart.

**BOTH BOARDS ARE FLASHED AND MEASURED (2026-08-13, the deletion build).**
P4: on-glass suite 22/22, spec conformance 10/10 against the board, every Lua
cart a `MoycoreRun`, a 60.1s celeste window with zero hitches — and fps
UNCHANGED, because that roster was already at the 60 cap and the deletable slice
was 2–4ms of a 33ms frame. **The S3 is where the case was, and it paid:
celeste p50 30 → 43 fps, worst 27 → 39, GC 2 collects/57s at 192–195ms → 1 at
149ms.** M0(c)'s gate ("p50 ≥ 30 AND worst ≥ 28"), which last month failed on
the floor by one sample, now passes with room. Numbers and caveats in #66.

What that leaves as the S3's biggest lever is no longer the engine: chrome is
**2.2× the entire cart** (10.17ms against 4.56ms), and internal SRAM is
exhausted — 23KB free, so the Lua heap runs 48.8KB SRAM against 110–265KB
PSRAM even with the floor knob working correctly.
**Read that chrome figure with the 2026-08-14 instrument fix in mind** (#66):
CHROMEBRK's `other` was a residual of a residual over six MILLISECOND-quantized
brackets, so it carried up to ~6ms of pure rounding on top of whatever was real,
and the stack walk — the biggest genuinely-unnamed piece — was never summed into
the split at all. Both are fixed (microsecond brackets throughout, plus a `stk`
bucket), and on P4 glass the walk is now **81% of chrome** where every named
bucket used to read 0.00. Re-measure the S3 before treating 10.17ms as the
target.
Then §9's superset question, which is a product call and is what keeps lupa
in `[dev,sim]` and `spr(Image)` on the Python raster. The §6.10
batching question is CLOSED (2026-08-14): the verbs are deleted, and the P4
A/B says the plain loop is ~0.53ms FASTER than the batch it replaced, because
the cart had to pack the span buffer in Python to use it. (`LuaCartRun` is
GONE as of 2026-08-13 — the deletion that rung had been holding, taken once
moycore was actually equivalent; see §6.9's ledger.)** v9 — the ladder RE-SEQUENCES on a finding
(§6.0): libmoy already implements every remaining stage-1 verb in C, plus
the loop entry points, so those crossings are ABSORBED into stage 2 rather
than written twice. v8: RUNG 1 IS DONE — stage 4 SHIPPED and the
3.2 streaming sunset is COMPLETE. The wasm head rasterizes with the boards'
own kernel (`moy_gfx` + libmoy compiled in as a usermod, the system canvas
IS `device_canvas.DeviceCanvas`), the page blits a framebuffer, and the
recording stack — recorder, CommandCanvas, RecordingLayer, ServedState,
SurfaceDelta, WsClientState, the wire protocol, the JS replayer,
`runtime/web_view.py` and `runtime/web_view_page.py` — is deleted. The
`surface_model_v1.md` §13 amendment landed with it. Measured (see §6 stage
4). v7 (same day) was the OWNER DIRECTIVE (§3.5): zero
duplication, everything that can be moycore is moycore; stage 2 is GO on
§4.2 grounds, the host raster joins the ladder, and §3.3's blocks call
closes (Python carts stay — the one deliberate duplication). v6: the 3.2
die-now sunset wave SHIPPED, the stage-4 wasm-raster spike REPORTED
(gate closed: moy_gfx-in-wasm), M0 fully closed on glass. v5 (2026-08-11
night) was the graduation: stage 0 + the stage-1 pin shipped, the M0 cheap
levers built, the design-doc treatment run.**
v1 was the parking place for the 2026-08-11 discussion; v2 folded that
discussion's grounded review (the issue-mirror check, the seam map of
`moy_lua`/`moy_gfx_capi`/the glue) and the **owner decisions of 2026-08-11**
(§3); v3 added the follow-on call from the same day — docked mode's session
model is buried and replaced by commit-shaped automated sync (§3.4); v4 was
the graduation candidate from the night stage 0 / the trace harness / the M0
levers landed; **v5 folds the two adversarial review passes** (arch: 16
findings incl. one blocker on stage 4's sufficiency and a dead-configuration
constraint on map(); perf: 7 findings incl. the headline fps estimate
failing its own arithmetic convention). The surviving corrections are woven
into the sections they hit. It is the ONE direction document for the
engine/runtime end state — it absorbs the docked-mode doctrine and
supersedes the lines of other docs named in §2.

Claims follow the surface-model convention: **MEASURED** (with source),
**ESTIMATED** (arithmetic shown), **PREDICTED** (with the falsifiable gate).
Current fps/ms numbers live in **#66**, not here — numbers quoted below are
dated snapshots for the arithmetic's shape, and #66 wins on conflict.

## 1. The one-sentence idea, and the end state

The moment a LUA cart runs, the console hands the raster, the input feed and
the audio to **one C core built from what we already vendor** (libmoy raster +
libmoy audio + the vendored Lua 5.4 VM + a C frame loop), and takes them back
when the run ends. MicroPython remains the SHELL and stops being the ENGINE.

The end-state stack, across every head:

- **moycore (C, one lineage, golden-pinned):** raster + audio + Lua VM +
  frame loop. Hosts: T-Deck, P4, the wasm head, and the CPython sim (bound
  via cffi/ctypes — §3.1). This is the same core moy-spec already builds as
  its 297KB wasm player; moycore on the boards is the third and fourth host
  of an existing engine, not a new one.
- **Python (MicroPython on boards/wasm, CPython on host):** the shell — WM,
  editors, store, OTA, the sync RPC (§3.4) — plus **Python carts**, which keep today's
  path (their hot verbs already reach C with no Python frame via the #155
  gates). Python stays on as the GUI toolkit and a cart language; the engine
  identity is C.
- **Dead at end state:** `runtime/audio.py`'s synth twin, lupa, the entire
  draw-command recording/streaming stack (§3.2), the Lua trampoline/batch
  upcall/state-mirror seams, and the `LUA_32BITS` host-parity hole.
- **Alive, deliberately:** the Python cart/shell verb surface (`make_api` +
  the DeviceCanvas/compositor glue) — the ONE accepted duplication (§3.5),
  pinned by the semantic traces. The host's own raster is GONE: v6 and
  earlier said "two raster implementations is the end state — not one"; the
  §3.5 directive REVERSED that, and stage 5 went one step further than the
  binding it planned — see §6 stage 5. `runtime/host_canvas.py` builds
  `device_canvas.DeviceCanvas` on CPython over `runtime/gfx_binding.py`
  (vendored libmoy compiled RGB565), and runtime/canvas.py is deleted.

## 2. Document map (what this absorbs, supersedes, defers to)

- **Absorbs:** v1 of this file (the 2026-08-11 moycore sketch);
  `docked_mode_2026-08.md`'s *doctrine* — local-first, physical possession
  as consent, artifact-shaped transfer — and its browser-head premise. Its
  session/role model is NOT absorbed but buried (§3.4); the file stays as
  historical reference and MOVED to `docs/history/` with this graduation
  (v5's commit).
- **Supersedes (specific lines):** docked mode's "mirror mode stays, frozen
  scope" — the 2026-08-11 decision sunsets the streaming view entirely
  (§3.2, with the accepted costs named) — and docked mode's
  head/controller/mirror session model, replaced by §3.4's commit-shaped
  sync. `shell_architecture_v1.md` §3.2
  ("why the layered compositor matters for the web view") — that motivation
  dies with the stream; §3's compositor case now rests on the P4 desktop and
  `surface_model_v1.md` alone. `perf_native_gap_v1.md`'s ranked lever
  roadmap — closed with verdicts in #66; this doc is the successor strategic
  tier for the engine.
- **Defers to, untouched:** `surface_model_v1.md` — the LOCKED presentation
  contract; moycore is a *producer* under it, never a new compositor (§7).
  One honest exception: at stage 4 the sunset retires much of its §5.4 web
  annex, which — because that doc is LOCKED — lands as an explicit versioned
  amendment to THAT file first (§8 here records what to amend, and owns the
  precise attribution of whose rule that is). `ui_widgets_2026-08.md` and the
  §2 privilege half of `shell_architecture_v1.md` — the shell/GUI axis,
  orthogonal, and in fact *reinforced*: system-carts-in-Python assumes
  Python stays a cart language, which §3.3 decides. `docs/moy_cart_api.md`
  and SPEC.md — the kid-facing contract does not change, ever.

## 3. Decisions register (owner, 2026-08-11)

### 3.1 `runtime/audio.py`'s synth twin is dropped; the host binds the vendored C

The twin existed so `make setup` needs no compiler. That objection expired
twice: `experiments/audio_parity/audio_parity.py` already compiles the exact
vendored libmoy source with whatever `cc` is on PATH (with a graceful
no-compiler skip), and `pyproject.toml` already ships lupa — a compiled C
extension — so the pure-Python host was never a property we had. The host sim
binds vendored libmoy (cffi or a ctypes `.so` built at setup, degrading to
silence without a compiler, the same graceful-absence pattern lupa uses).

Dies: the synth twin and its whole drift class — the equal-loudness bug and
the fractional-SFX-speed bug were both twin bugs; the class has a body count.
`tests/test_audio_parity.py` is RE-POINTED, not shrunk (as built): the strict
pass pins the binding's marshalling bit-exactly against an independent
reference render, and the device-precision pass keeps measuring the
double-vs-float gap. Stays: the `SFX`/`AudioBank`/`sounds.json` data model
and the Music editor (editor-domain Python, untouched). This is **stage 0**
(§6): it ships value alone and proves the CPython-host-embedding pattern the
rest of moycore's sim story needs.

The same pattern later kills **lupa**: when the host embeds moycore's VM,
the host runs `LUA_32BITS` semantics, closing the standing hole CLAUDE.md's
#67 section records — the float-width/integer-wrap gap that limits
golden-frame parity to the host for float-heavy carts. Host-cart behavior
becomes device-identical by construction.

### 3.2 The streaming webview is sunset (supersedes docked mode's frozen-scope mirror)

The whole draw-command recording/streaming stack goes — in two waves, split
by who still stands on it (v5 correction; the v4 text bundled them).
**The die-now wave SHIPPED 2026-08-12**: `TeeCanvas`, the device webserver's
frame push (+ `device_webview.py` and the Settings WEB VIEW surface —
`moy_webserver.py` survives stripped to the socket/HTTP/WS transport core the
§3.4 RPC rides, with `handle_http`/`on_text`/`send_text` seams),
`tools/web_console.py` (+ its VM deploy recipe), and every decline-the-Tee
guard in `moy_lua_glue`/`make_spr_gate`; both boards also stopped freezing
`web_view.py`/`web_view_page.py` (flash back). Pinned by
`tests/test_streaming_sunset.py` (absence greps) and the salvaged behavior
suites (`test_web_recording.py`, the transport-core `test_moy_webserver.py`,
and a `webharness.py` standing in for the deleted console tool). *(Both of
those recording-side suites are themselves gone at stage 4 — see §6.)*
Two corrections the deletion surfaced, recorded here because the v5 text was
wrong about them: **`SurfaceDelta` and `WsClientState` are NOT in the die-now
wave** — web_boot constructs both (the wasm head's push path), so they move
to the stage-4 wave with the rest of the substrate; and the **atlas/defspr
ship-once lane + the diet `["map"]`/`["settiles"]` wire ops lost their only
PRODUCER** (the Tee) — they are orphaned code inside `web_view.py` that dies
with the stack at stage 4 rather than being worth a targeted excision now.
AT STAGE 4: `DrawRecorder`, `RecordingLayer`, `CommandCanvas` and the page
replayer — they are the wasm head's own rendering substrate until it
re-rasters, and deleting them earlier deletes the head. The browser's job
moves to the **wasm head**: same frozen runtime in the browser, synced to
the device per §3.4.

**The Zero owner call — MADE (2026-08-12): it survives, re-based on the
wasm head.** The XIAO ESP32-S3 Zero side port
(`firmware/seeed_xiao_esp32s3_zero/`) had the streaming web view as its ONLY
output path ("browser is the GPU": `TeeCanvas` + the `moy_webserver` frame
push, staged from the T-Deck tree), so this wave orphaned it. The owner's
re-framing: the wasm head does everything the Zero's stream did, better —
so the Zero stops being a console that borrows a screen and becomes **a
pocketable miniature computer the browser console pairs with**, contributing
exactly what a browser cannot: (a) the natural FIRST host of the §3.4 sync
RPC — the kid's cart store on its flash, behind the surviving
`moy_webserver` transport core, pulled/pushed by any browser running the
wasm head; (b) physical I/O — GPIO/motor verbs for carts, which is the
parked #9 pull arriving (its consumer now exists); (c) plausibly serving
the wasm bundle itself over its SoftAP (the M0 SoftAP already works;
`dist/` is ~1.2MB — a one-time ~10s load at measured S3 WiFi rates, then
browser-cached), making the Zero a self-contained console-in-your-pocket
with no infrastructure. Nothing is rebuilt yet: the port's streaming code
is dead with the stream, and the rebuild rides the §3.4 track on its own
schedule (#41 carries the direction, #9 the pins).

What survives from that neighborhood: `moy_webserver`'s socket/HTTP core (the
§3.4 sync RPC rides it) and the page's input surfaces (they become the
controller role — input up needs no frame stream down).

**Accepted costs, named:** the four mirror jobs (show-and-tell, remote
assist, capture of real-hardware footage, field diagnostics) lose their
vehicle — a docked head shows the *browser's* session, not the device's
glass, so mirror-of-glass genuinely dies. Cheap partial successor worth
carrying as an open question: a **screenshot verb on the sync RPC** (one
framebuffer still on demand — stills are artifact-shaped like everything else
docked mode ships; it is streams the ceiling forbids). `pageshot`/
`browsershot` get simpler successors that screenshot the wasm head's
framebuffer directly, retiring the "replayer sliced out of
`web_view_page.py`" sync rule.

**Sequencing consequence — and the honest size of it (review finding, v5):**
the wasm runner's system canvas IS `CommandCanvas`, so the sunset cannot
complete until the wasm head re-rasters (stage 4). But "the page becomes a
dumb blitter, exactly what moy-spec's 297KB player already is" — v4's line —
was a category error: that player has NO SHELL. The wasm head runs the whole
console, windowed desktop included, and moycore's raster per §7's own split
covers the CART canvas plus console extensions, not shell pixels — while
moy_gfx is deliberately compiled OUT of the wasm build. So completing the
sunset requires a wasm-side SHELL raster + framebuffer present path that no
stage previously named: either runtime/canvas.py (the host's indexed raster,
since deleted) interpreted at desktop
sizes (which reverses the runner's founding "the wasm never rasterizes a
pixel" premise and needs a measured perf verdict before it is believed) or
moy_gfx-in-wasm (currently excluded by build design). Stage 4 therefore
BEGINS with a feasibility spike that prices those two options; the sunset's
completion date is gated on its verdict, not assumed. Until stage 4,
`DrawRecorder`/`RecordingLayer`/`CommandCanvas`/the replayer are the wasm
head's SUBSTRATE — they die at stage 4, not now; what dies now is the
device webserver's frame push and the TeeCanvas lane (§5's split rows).

### 3.3 Python stays on as the shell and a cart language — and only those

The engine identity is C. For Python carts this is nearly the shipped state
(hot verbs are #155 C gates; what is Python is orchestration, which survives
because it IS the shell). For Lua carts, the per-frame Python surface
converges to: poll input → `moycore.tick(dt)` → present.

The permanent asymmetry this creates is named in §5 (the one discipline lane
that remains, and its required pin). The product call this surfaced is
**CLOSED (owner, 2026-08-12, with §3.5): blocks keep graduating to Python**
(the MakeCode model, #111), so Python-as-cart-language stays and `make_api`
is accepted as the ONE deliberate duplication in the zero-duplication end
state — kept honest by the semantic-trace harness, not by discipline. (The
blocks→Lua re-target remains describable if the graduation ladder is ever
redesigned, but it is a declined option now, not an open call.)

### 3.4 Docked mode's session model is buried; the wasm head gets automated, commit-shaped sync

(Follow-on decision, same day.) The headship machinery — DOCKED placard,
head/controller/mirror roles, store-snapshot ownership, session-token
lifecycle — is judged **feature creep** and dropped. Three tells, recorded so
it stays buried: its hard problem (divergence) is self-inflicted by sharing a
store — exchange artifacts and it evaporates; its own conflict rule (per-file
last-writer-wins + the undo journal) needs none of the machinery, so headship
added ceremony, not safety; and its reference board was the P4 — the board
that least needs a browser head — while the board a head would rescue (the
T-Deck) was positioned out of the feature set. The streaming half's bug
history seconds the verdict: the mirror's defect class was cache-agreement
across a lossy transport, which bandwidth never fixes.

**What the owner keeps is the automation** ("sync in wasm mode, so it's
automated"). Automation and shared live state are different axes; the sync
that ships is commit-shaped artifact delivery riding machinery that already
exists:

- **The unit of sync is the commit.** The console has no SAVE — commits
  already fire on the typing-idle debounce and every exit path (#111), each
  one atomic, whole-file-shaped, journal-appended. The push schedule and the
  reconciliation net are already built; sync adds delivery, not concepts.
  ESTIMATED: a commit is KB-sized — sub-second at either board's measured
  rates (#53's downloads); a first full-store pull is a few hundred KB,
  seconds. Artifact-shaped is the payload class the WiFi ceiling is fine at.
- **Store model:** the wasm head grows its own browser-local store (it is
  runner-only today, so the store is new work under any design). When PAIRED,
  a device cart opens by pull and every commit auto-pushes; unpaired, the
  local store stands alone. `carts_store` is an injected seam on every tier —
  the device-backed store is a second backend behind the same interface, not
  a new concept for the shell or the kid.
- **Conflict rule:** per-file last-writer-wins + the journal on both sides —
  docked mode's own proposal, kept, now the *whole* story. Optionally an
  advisory "open in browser" badge on the device; never a lock.
- **Consent:** unchanged doctrine — PIN/QR pairing shown on the glass; an
  endpoint that writes flash is never open to the classroom WiFi. Physical
  possession is consent, exactly as OTA established.
- **Survivors from docked mode:** phones-as-controllers (input-only, the
  `btn(name, p)` reservation), the transports (WiFi primary; USB where the
  board's constraints allow), `moy_webserver`'s socket core. RUN ON CONSOLE
  degenerates from a transfer verb to a plain launch verb — sync already
  delivered the cart.
- **Parked, explicitly:** GPIO verbs, ESP-NOW relay, USB data-disk. They
  wait for their own pull (#9), not this design.
- **The security dividend, named:** with the mirror dead and sessions
  dropped, the device's whole network surface is the OTA client (outbound),
  one consent-gated sync endpoint, and controller input. Token-lifetime and
  lost-token UX questions mostly evaporate instead of getting answered.

*Pins (the §4.2 rule applies to this seam too):* a CI convergence harness —
two real store instances over a fake transport, scripted edit/commit
sequences including drop-and-reattach and a deliberate two-sided collision —
asserting both stores converge and both journals stay replayable. It lands
WITH the RPC, not after it. The sync track is independent of moycore's
stages (§6) and proceeds on its own schedule.

### 3.5 The zero-duplication directive (owner, 2026-08-12 late)

"I don't want any duplication in moybyte — everything that can be moycore
should be moycore." This resolves the queue question M0 left open: **stage
2 is GO on §4.2 grounds alone** (the fps case is measured nil and that is
fine — de-duplication is the point), and the ladder runs in this execution
order, each rung deleting a parallel implementation:

1. ~~**Stage 4** (wasm head re-rasters; gate closed) — deletes the recording
   stack, completes the §3.2 sunset.~~ **DONE 2026-08-12 (§6).**
   ~~2–5~~ **all walked by 2026-08-13**, stage 3 included -- its code is
   written, staged, compiled and linked for Xtensa; only the flash is
   owner-gated (the board cannot enter the ROM loader unattended). Two gate bugs on the way,
   both recorded where they happened: a substring scan that disqualified
   every cart, and a regex lookbehind that CPython accepts and
   MicroPython's `re` raises on — the second shipped green and made
   moycore unreachable on the board while everything looked healthy.
   Whatever routes work to moycore, assert that something arrives.
2. **Stage 1 completion — RE-SCOPED (§6.0):** the verb crossings are
   absorbed into stage 2 (libmoy already implements them); what remains is
   the trace-vocabulary extension and the superset decision.
3. **Stages 2–3** (`moycore.tick`, P4 then S3) — deletes `LuaCartRun`'s
   registry and the per-frame Python engine surface.
4. **Host embeds moycore's VM** (§3.1's second half) — lupa dies, the
   `LUA_32BITS` parity hole closes. **The BINDING is built and PROVEN
   (2026-08-12 night):** `runtime/lua_binding.py` compiles libmoy's
   binding over the SAME vendored Lua 5.4 the boards use, `LUA_32BITS`
   and all, and `tests/test_host_lua_binding.py` runs a cart through it
   — load + `_init`, frames whose canvas tracks cart state, a snapshot
   input edge arriving as `sfx(3)` then `sfx(5, 2)`, pmem at 41+4 dirty,
   an error returning `cart:1: boom` (text with its line, which is what
   crash-to-code needs), and the SPEC.md 4.1 sandbox verified as a
   ceiling by probing each excluded stdlib. **The SWAP is in (same night):** `build_workstation` routes a
   spec-only Lua cart to the binding and keeps lupa only for carts using
   moybyte's superset, pinned end-to-end by
   `tests/test_host_moycore_route.py`. lupa cannot leave `[dev,sim]`
   until the superset carts have a path -- which is §9's layers/images
   question, the same one gating the device. Recorded because the gate's
   FIRST version was a silent no-op: a plain substring scan for the
   superset names disqualified every cart in the tree, so the new path
   existed and was never taken. It matches calls now, and the test
   asserts both directions.
5. **Host raster binds libmoy** (NEW scope this directive adds; reverses
   v6's "two rasters is the end state") — **SHIPPED 2026-08-15, and it
   went further than this item asked.** The plan here was for
   runtime/canvas.py to keep its viewport/layers/batching half and
   delegate its verbs to an INDEXED libmoy binding
   (runtime/raster_binding.py, whose oracle
   tests/test_host_raster_binding.py had already replayed all 10
   conformance scenes pixel-identically). What landed instead is the
   whole file's DELETION: the host builds
   `device_canvas.DeviceCanvas` — the class both boards and the browser
   run — through `runtime/host_canvas.py` over
   `runtime/gfx_binding.py` (the same vendored libmoy compiled RGB565
   instead of indexed). So the compositor/extension half is not a
   surviving Python layer either; it is the board class's. The indexed
   binding and its oracle are deleted with the raster they were built
   for; `tests/test_spec_conformance.py` now replays the traces through
   the 565 canvas and matches the same goldens. The risk was bounded by
   rather than by review.

What remains after rung 5 is exactly one duplication, accepted by the same
decision: Python carts' `make_api` verb surface (§3.3, blocks call closed).

**Perf guardrails, so the directive can't quietly cost fps:** the boards
build libmoy `MOY_PIXEL_RGB565` (the indexed-vs-565 A/B is settled and
moycore does NOT reopen it — libmoy compiles per pixel format); the
presentation layer (fold, PPA async overlap, bounce pump, per-board
composite strategies) stays per-board and outside moycore (§7); the
`lua_Alloc` SRAM-first policy and the -O2 pins carry over. Every crossing
to date measured fps-null on the S3 and held the P4 cap; any rung that
measures a regression stops the ladder at that rung until explained.

## 4. Why (two arguments, both audited)

### 4.1 Performance — the honest arithmetic

The celeste/S3 lever chain is fully measured (#66): dispatch deleted (#189,
~2ms), SRAM residency falsified (census), compiler flags null on both boards,
the composite folded (#190). Snapshot 2026-08-11: frame ~32.5ms (25–31fps) =
render ~15ms (of which ~13–15ms pure Lua interpretation) + logic ~5ms (also
Lua) + composite ~1.8ms + **~10–12ms of everything else** (flush/pump, input
consume, audio tick, chrome walk, Player/loop orchestration). Note the pair
itself is honest but lossy: 25fps implies 40ms frames, so the fps band
carries 0–7.5ms/frame of variance and GC time the ms decomposition does not.
MP-side GC collects: 119ms spikes when #189 measured them, **170–189ms by
the post-1a live-set (~790k) measurement the same night** (#191 thread) —
the fresher, worse number is the one stage 2 aims at.

Moycore does NOT make Lua faster — the ~18–20ms of VM time is untouched. And
by this doc's own scope boundary (§7: presentation unchanged), part of the
"everything else" slice *survives* moycore: the fold pump, the present, the
input poll. **The deletable share of the ~10–12ms is PREDICTED, not
estimated — no arithmetic in this doc derives it**, and the confirmed
stage-1 deletions to date (#189's ~2ms, 1a's batch upcalls) measured
fps-NULL on the S3, so the honest prior points at the LOW end. If the
attribution confirms 4–8ms deletable, naive subtraction on the measured
frame band (32.5–40ms) lands ~28–41fps against a 30fps cart; if it confirms
~2ms, the fps case is dead. (v4 of this doc claimed "high-20s to mid-30s"
as an ESTIMATE; that violated its own show-the-arithmetic convention and is
retracted — gate (a) produces the number.) The *qualitative* prize is
deleting the MP GC collects from play — the felt stutters — and it needs its
own precision: stage 2 deletes the collect *cadence* (cart churn → ~0), not
the collect *cost*, which is mark time over a shell-owned live set moycore
does not shrink. The ~1.6KB/frame residual churn has never been attributed
to an owner; if it is shell-loop garbage, stage 2 does not delete it — M0(a)
must attribute bytes as well as milliseconds. The P4 is already at cap
(62/62), so moycore's P4 case is maintainability + the spike class only.

**PREDICTED, with gates:** (a) the pre-stage-1 attribution (§6 M0) names
where the ~10–12ms actually goes, splitting it into deletable-by-moycore vs
survives-by-scope — if the deletable share is small, the fps case collapses
to §4.2 alone *before any stage is built*; (b) stage 1's zero-upcall frame
must land within the budget that attribution predicts, or the model is wrong;
(c) stage 2's GC claim is measured as: collects during a 60s celeste play
window under `moycore.tick` → ~0, and the residual spike ledger names its
owner.

### 4.2 Maintainability — the pin is the cure, the vendoring is the diet

The bug pattern this repo keeps paying for is parallel implementations kept
in agreement by discipline. But the two shipped cures (audio #97, the nine
libmoy verbs) show the mechanism precisely: the load-bearing element is an
**automated construction-level pin** (bit-for-bit parity, conformance
goldens); vendoring shrinks what the pin must cover. Two of the four bugs the
v1 draft cited are already cured by shipped vendoring; the invalidation-cache
pair is shell-side (surface model's territory); the webview re-export was a
staging-class bug that moycore is neutral toward. What moycore actually
prevents is the next tline-class drift in the seams that remain — and those
are genuinely bug-prone: the #63 batch order rule (enforced today by an
upcall convention), the camera/clip/pal Python-authoritative-C-mirror sync,
the token protocol, the decline lanes.

**And moycore *creates* one lane of the exact disease species.** Today a Lua
cart and a Python cart get *mostly* the same behavior by construction —
`LuaCartRun`'s registry is a loop over `make_api`'s dict, same closures, one
source of truth for `btnp` timing, `pmem` semantics, clip edge cases. Mostly:
the trampoline cannot marshal tables or buffers, so `spr_batch`/`rect_batch`/
`spans` and direct `Image` use are ALREADY unavailable to Lua carts — a
standing semantic asymmetry the v4 draft understated. It understated it in a
second way too, found on 2026-08-13: it cannot marshal OBJECTS either, which
is why `make_layer`/`draw_layer`/`image` have never been registry entries on
any runtime. They ride int handles and a Lua prelude
(`runtime/lua_ext.py`) — and the reason that file exists is that the glue
was reachable from the two device runtimes and invisible to the host's, so
the host registered the raw closures and every layer cart fell off moycore
without saying so. Post-moycore, Lua
carts get C implementations of input/state/audio verbs while Python carts
keep the Python ones: parallel implementations at the *semantic* layer,
where pixel conformance sees nothing. That trade is accepted — the
mechanical seams are per-frame-hot, the semantic surface changes rarely —
but only under the staging rule: **each stage ships with deletions AND
pins** (§6). The pin is BUILT (`tests/test_semantic_traces.py`, 2026-08-11):
the unix-port dual-usermod build that `tests/test_moycore_loop.py` uses
drives the REAL C against the REAL Python path — scripted input +
state-verb traces replayed down both cart paths, hash- and log-compared,
mutation-tested. Honesty about the order: stages 1a/1b crossed BEFORE it
existed, on pixel pins alone; the harness pinned the batch order rule
retroactively and it gates everything from the masked map onward. **The
rule from here: a crossing extends the trace vocabulary FIRST** — the
harness gates only what its traces exercise (§6 lists its known coverage
gaps), and a crossing outside the vocabulary is a crossing without a pin.
Enforcement caveat, accepted: both pin suites skip when the hand-built unix
dual-usermod MicroPython is absent, so they have teeth on the dev machine
and none in bare CI — keeping that build alive is part of the pin.

Dual-core, for contrast, deletes zero lanes and adds a failure dimension; if
ever wanted it composes BETTER after moycore (two C tasks and a fence, no
GIL to route around). Present-on-core-1 remains the compatible tactical
option (#190 thread).

## 5. The lane ledger

Roughly ten ways a Lua cart's `rect` can happen today. Disposition of every
lane (the maintainability case is THIS table, kept honest):

| lane | disposition | pinned by |
|---|---|---|
| upstream libmoy (moy-spec) | **the one engine** — where the core grows | spec conformance suite |
| `moy_gfx` kernels (9 verbs = libmoy calls; compositor kernels own) | **absorbed into moycore's device port** | goldens + on-glass conformance (`p4_conformance`) |
| `l_draw` direct-C hot shapes (#189) | **DELETED 2026-08-13** — libmoy's own binding draws these, so the hand-written direct family had no job left; its byte-parity suite went with it | the semantic traces (identical canvas hashes, twin carts) |
| `__moy_map_masked` + flags (the p8 shim's C map walk, #66 M0) | **already moycore-shaped** — folds into the native surface | the 12 masked-map A/B scenes (byte-exact) |
| prelude `rnd`/`flr` (pure Lua, M0) | **stays** — VM-local by design | flr: add to the trace vocabulary; rnd: unpinnable on purpose (random), the recorded exception |
| trampoline → Python closure (per-verb) | **dead for Lua on all three tiers** (the wasm tier joined 2026-08-13, later than stage 4 planned — see the status header); what survives is the SUPERSET registry, which is the point, plus the object-verb handles that never were registry entries | the §4.2 semantic traces, verb by verb as each crosses |
| `LuaCartRun` (the whole old runtime) | **stays, deliberately** — it is the fallback, and until 2026-08-13 it was the fallback that ran every real cart; delete it after both boards are flashed and observed, not before | `test_host_moycore_route`'s seed sweep asserts nothing reaches it |
| object-verb glue (`make_layer`/`draw_layer`/`image` handles + prelude) | **shared, one definition** (`runtime/lua_ext.py`) — it was never a trampoline candidate and the copy that did not exist is what broke the host | `test_moycore_loop`'s OBJ block runs the real file under MicroPython |
| `l_draw_fallback` odd shapes (pix 2-arg read, nils) | **must move C-side or the registry survives** — stage-1 decision, not a footnote | NOT yet pinned: the 16-case matrix is hot-shapes-only and the dispatch assert proves the fallback *fires*, not that it *matches* — the stage-1 decision ships WITH a real odd-form A/B |
| batch protocol (`begin_batch`/`flush_batch` upcalls, token, order rule) | **dead since 1a** — flush moved into C | pixel goldens + the trace harness's interleaved spr/primitive frames (the named "order-rule test" was only ever a source grep + a zero-pixel flush; the harness is the behavioral pin) |
| camera/clip/pal Python-authoritative mirrors | **ownership flips** to moycore during a run; shell reads back at exit | state-verb traces — which do NOT yet observe the exit-time read-back (the trace resets state per frame); extend before this crossing |
| `DeviceCanvas` Python methods + #155 gates | **survive** — Python carts and chrome | device↔host parity as today |
| runtime/canvas.py host raster | **DELETED 2026-08-15** — superseded by `device_canvas.DeviceCanvas` on CPython (`runtime/host_canvas.py`), so the shell and Python carts draw on the boards' class | `test_spec_conformance` goldens, now replayed through that canvas |
| TeeCanvas / device webserver frame push | **die now** — §3.2 sunset | grep-tests pinning absence |
| DrawRecorder / RecordingLayer / CommandCanvas / page replayer | **die at stage 4** — they are the wasm head's substrate until it re-rasters (§3.2 sequencing) | n/a until then |
| lupa host runner | **dies** when the host embeds moycore's VM (§3.1) | host==device Lua parity, now exact |
| `runtime/audio.py` synth | **DEAD** (stage 0 shipped) | parity suite re-pointed: strict marshalling pass + device-precision pass |

## 6. Staging (each stage ships with deletions AND pins, or it isn't done)

**M0 — measure first (no build).** (a) Post-fold full-frame attribution for
celeste on the S3: name the ~10–12ms non-VM slice, split deletable vs
survives-by-scope. (b) **Ship the two cheap levers — BUILT (`87164a5`,
2026-08-11):** the **flag-masked `map()`** went C as `__moy_map_masked` in
moy_lua (a masked walk over the ctx's registered map cells appending through
the SAME batch protocol l_spr stamps; the `__gff__` flags cross once as hex;
the moy-spec shim probes it and keeps its Lua loop as the lupa/wasm
fallback — deliberately NOT the console's `map()`, which keeps its Python
lane), pinned byte-exact at the time by 12 A/B scenes (that suite is gone with the lane — see the ledger); and
`rnd`/`flr` went into the glue prelude as pure Lua (`time()` stayed a
trampoline for M0 — it reads live input state in MP's ticks domain and no
cart calls it hot; it crosses at stage 2 with the loop, not before).
Staleness note on the 4.5ms: it was measured 2026-08-09 on the zoomed cart
in the pre-fold ~30ms-render regime, and the shim's local-capture pass
cheapened the same loop afterward — the live prize is ≤4.5ms, and only the
re-measure prices it. P4-verified: conformance 10/10, celeste at the 62
cap, 0 upcall-falls. (c) **The S3 re-read LANDED (2026-08-12 night, owner
play, 57.5s / 20 samples): p50 = 30, worst = 27, steady 27–32** — against
the gate ("a solid 30" = p50 ≥ 30 AND worst ≥ 28 over ≥60s uncapped, PERF
DIAG on), p50 sits exactly ON the bar and the floor fails by ONE sample,
which contains a 192ms GC collect. The levers bought ~+2fps over the 25–31
baseline (consistent with the ≤4.5ms staleness note); LUABATCH read clean
(upfall=0 every window). **Verdict: the fps case NARROWS instead of
resolving** — steady-state render now rides the 30fps cadence and the thing
breaking the floor is the GC spike class, which is stage 2's qualitative
target. So stage 2's fps case and its qualitative case are now the same
case: delete the collects from play, keep ~30 solid. (a) **The attribution
LANDED the same day** (variant carts by difference — floor / +input /
+state; the full table lives in #66): the deletable-by-moycore share is
**~2–4ms, not the predicted 4–8** — gate (a) FIRES at the review's low-end
prior. What it is made of: state-verb trampolines ~1.5–3ms (measured price;
the pal() reset pair dominates), input-verb crossings **~0.0 measured**
(twelve upcalls per frame are invisible — #107 already paid that bill, and
moycore's input snapshot region buys nothing at cart rates), wrapper
slivers ≤1ms. And the churn split answers the spike-ownership question:
the FLOOR cart — zero per-frame upcalls — still collects (~115ms every
~33s), so 15–30% of celeste's churn is the shell's own and stage 2
stretches the collect cadence ~3–6× rather than deleting collects. **M0 is
now FULLY CLOSED (2026-08-12 evening):** the 2× floor run landed (56fps /
18.9ms — the whole 1×→2× present premium is ~1–2ms, the #190 fold's doing),
and the state-verb slice was CUT IN PLACE the same day rather than waiting
for a crossing — the pal() gate-table fix (`a791c33`,
`experiments/state_verb_cost/`: the trampoline crossing is 0.07µs, ~1% — 
the cost was always the Python body) collapsed yesterday's +3.3ms
state-vs-input delta into the noise floor on glass. Consequence for the
ledger above: the deletable-by-moycore share shrinks again, to **≲1ms +
wrapper slivers** — stage 1's remaining fps case is effectively nil, and
§4.2 carries the whole case alone. Numbers live in #66, as always. Be honest about what the
gates can kill: no S3 number kills the *project* (both outcomes re-queue
it); what a number kills is a CLAIM — (c) sharpened the fps claim, (a) then
shrank it to its floor: **the whole engine-side prize is ~+2–3fps steady
(low-30s) plus spikes 3–6× rarer but not gone.** ~~Stage 2's go/no-go is
now an owner call~~ — the call was MADE the next evening (§3.5): GO, on
§4.2 grounds alone, with the fps case honestly written off.

**Stage 0 — host-embedded audio. SHIPPED (`ff69071`, 2026-08-11).** §3.1:
bind vendored libmoy on the host, delete the synth twin. As built:
`runtime/audio_binding.py` compiles the DOUBLE-WIDENED vendored source (the
parity harness's own recipe — under which the strict suite had proven the
twin bit-identical, so the swap moved no sample the host plays) plus a small
shim (`runtime/moyhost_audio.c`, malloc'd per-engine handles, the
modmoy_audio pattern minus I2S) into a hash-cached `.so`; `make setup`
pre-builds it. `AudioEngine` survives everywhere as the bank/model holder
(the §3.1 recon found `project.py` constructs it on the device); its verbs
drive the C on the host and no-op on the boards. **No fallback — KISS**
(owner): a build without moy_audio (and a host without a compiler) plays
SILENCE; `DeviceAudio`'s Python-engine lane is deleted. The parity suite
re-pointed: the strict pass now pins the BINDING's marshalling bit-exactly
against an independently-driven reference render; twin-internal voice tests
re-expressed through an `active_channels()` mask or retired to the parity
scenarios. The CPython-embedding pattern the sim's moycore story needs is
proven.

### 6.0 The re-sequencing (2026-08-12 night) — stage 1's remainder is stage 2

**The finding.** `moy-spec/libmoy/src/moy_lua.c` (550 lines) is a complete
Lua binding of the spec verb table: it registers **all 38 verbs** as C
functions — including every one stage 1 has left (`cls`, `map`, `camera`,
`clip`, `pal`, `palt`, `btn`, `btnp`, `pmem`, `time`, `sfx`, `music`,
`mget`/`mset`, `touch`, `key`/`keyp`, `textmode`, `quit`, `cfg`, `rnd`,
`flr`) — with the same argument shapes moybyte uses, because SPEC.md is
where both got them (`l_map(mx,my,w,h,sx,sy,colorkey,scale)`,
`l_spr(n,x,y,colorkey,scale,flip)`). Beside it, `moy.h` exports the loop:

    int moy_lua_open  (lua_State *L, moy_console *con);
    int moy_lua_init  (lua_State *L, char *err, size_t errlen);
    int moy_lua_update(lua_State *L, float dt, char *err, size_t errlen);
    int moy_lua_draw  (lua_State *L, char *err, size_t errlen);

That is `moycore.tick`, already written, with the error text crossing as a
buffer exactly as stage 2 wants it for crash-to-code. And it is host-agnostic
by construction: `moy_console` is `{canvas, sheet, map, host, rng}`, the
canvas's `pix` is **caller-owned** ("YOURS, never allocated here") with a
`MOY_PIXEL_RGB565` mode and a `wire[]` table, so a `DeviceCanvas`'s existing
framebuffer can BE the canvas with no copy; and `moy_host` is a callback
table (`btn`/`btnp`/`time_ms`/`pmem_get`/`pmem_set`/`sfx`/`music`/`touch`/
`key`/`textmode`/`quit`/`cfg`) whose NULL entries are defined as conforming
no-ops. libmoy deliberately does not embed a VM — you hand it a `lua_State`,
and moybyte already vendors Lua 5.4 under `native/moy_lua/lua/`.

**Therefore the remaining stage-1 items are re-scoped, not dropped.**
Crossing `cls`, `map`, the camera/clip/pal ownership flip, the input
snapshot and the audio queue into moybyte's own `moy_lua` module would be
writing a **second C implementation of code that already exists upstream**,
and then deleting it at stage 2 — the exact duplication §3.5 exists to end,
committed twice over. So they are absorbed: stage 2 acquires them by
vendoring `moy_lua.c`, and stage 1 closes with what does NOT duplicate
anything.

**What stage 1 still owes, because stage 2 needs it either way:**
1. **The trace-vocabulary extension** (§4.2's rule). The harness gates only
   what its traces exercise, and stage 2 moves EVERY verb at once — so the
   vocabulary has to cover cls/map/state/input/audio *before* the switch,
   including the non-draw-lane liveness guards, or the biggest crossing in
   the plan lands against the thinnest pin.
2. **The odd-shape decision**, which the finding sharpens into its real
   question: not "what about odd arities" but **what happens to moybyte's
   SUPERSET** — `make_layer`/`draw_layer`/`image`, scenes/tables/texts,
   `view()`, wifi, achievements. libmoy binds the spec; those are moybyte's.
   They can be registered as extra C functions beside libmoy's table, or
   left trampolining into Python for the rare ones. That is §9's
   layers/images question, now load-bearing, and it decides at stage 2 with
   a real cart census rather than in the abstract.

**What this does NOT change:** the crossings already SHIPPED (1a's batch,
1b's sspr/tline, #189's solid draw family) stay exactly as they are until
stage 2 replaces the whole path — they are working, pinned and measured, and
ripping them out early would buy nothing but risk. The `moy_gfx` C API they
ride is also what the compositor uses, so it survives regardless.

**Stage 1 — C-only cart ABI. RE-SCOPED by §6.0 above** (its remaining verb
crossings are absorbed into stage 2; what it still owes is the trace
vocabulary and the superset decision). Original scope, kept for the record:
extend #189 until a celeste frame makes ONE Python round trip (the tick). The work, sized by the seam map: move the
batch flush + sheet/atlas access C-side (the big one); export
`blit_batch`/`blit_map`/`sspr`/`tline`/`cls` through `moy_gfx_capi` (kernels
exist, Python-bound only); flip camera/clip/pal ownership; the input
snapshot region; the sfx/music command queue; decide the odd-shape fallback
(absorb C-side, or keep the registry alive and say so in §5). `moy_lua_call`
already has the right shape — it needs return values (`lua_pcall(L, n, 0, 0)`
discards today) and exists as the single entry. *Deletes:* the per-verb
trampolines for Lua hot paths, the batch upcall protocol, the #107
marshalling fast paths (nothing left to marshal). *Pins:* the semantic trace
harness (§4.2) lands HERE, not later — it is stage-1 work (PAID, see the
PROGRESS block below).

*PROGRESS (2026-08-11, the night this doc went to v3):* **the batch move is
BUILT and P4-verified** — `DrawCtx.set_batch_src` + `moy_gfx_capi_flush_batch`
(the blit_batch walk, pure C, token-guarded) + C-stamped run breaks in
`l_spr`/`l_draw`, with `DeviceCanvas.flush_batch`'s `_lua_batch_sheet`
fallback keeping Python-side flushes of C-stamped runs correct. Byte-parity
pinned on the unix build (zero-upcall asserted); on glass: celeste flushes
C-side with 0 upcall-falls and flat Python batch counters, sakura exercises
the fallback lane, fps unchanged at the P4 cap. **Stage 1b landed the same
night:** sspr + tline are lua_CFunctions over new capi exports against the
registered sheet (+ `set_map_src` for the tilemap) — default-arg forms
handled, odd forms and unregistered sources fall back to the trampolines.
On glass: conformance 10/10 with the provisional scenes through the direct
lanes; `provisional` 3,388 direct shape-draws / 0 fallbacks,
`provisional_tline` 15,000 / 0. **The semantic trace harness is PAID
(`6fd3d29`, same night): `tests/test_semantic_traces.py`** — one scripted
trace (input edges + a line-faithful twin cart) down BOTH device cart paths
under the unix dual-usermod build (real moy_lua VM + LuaCartRun glue +
direct lanes vs the same `device_api.make_api` closures as a Python cart),
comparing per-frame canvas hashes, the btn/btnp/pmem observation log, the
audio command order and the final pmem image; mutation-tested (camera drift,
dropped pal() reset, btn-for-btnp, pmem wrap slip, audio order swap — all
caught). Every further crossing runs against it — **after extending its
vocabulary to cover that crossing** (§4.2's rule). Known vocabulary gaps,
recorded so nobody mistakes the pin for wider than it is: `key`/`keyp`,
`touch`/`mouse`, `textmode`/`quit`/`view`, player-slot `btn(name, p)`, the
exit-time state read-back, map's colorkey/scale forms, odd verb FORMS
(pix 2-arg read, nils), scenes/tables/texts, and non-draw-lane liveness
guards (the draw/batch lanes assert they ran direct; an input/audio crossing
needs the analogous proof or it can pass vacuously). Remaining stage-1
items: cls; `map()` for MOY carts — the v3/v4 constraint "keep the Fold-2
cache" was DEAD CONFIGURATION (the review caught it): `MAP_AUTO_CACHE`
ships False with a losing hardware verdict (T-Deck 2026-07-07, 4.3–5.7ms
direct vs 13.4ms cached), so the shipped device lane is direct `blit_map`
and the crossing carries no cache constraint (the HOST's gen-keyed map
cache lives in `DeviceCanvas.map`, which survives regardless); state-verb
ownership; input snapshot; audio queue; the odd-shape decision — which
ships WITH a real odd-form A/B, since today's matrix covers hot shapes only.

**Stage 2 — the core loop, P4 first. THE CORE IS BUILT AND VERIFIED
(2026-08-12 night); the wiring is not.** `native/moycore` exists: libmoy's
Lua binding vendored, `modmoycore.c` supplying the host half (a canvas over
the DeviceCanvas framebuffer with no copy, an input SNAPSHOT array, an audio
command QUEUE, C-side pmem with a dirty flag), built into the unix
dual-usermod binary. `tests/test_moycore_loop.py` pins it: a cart loads,
`_init` runs, four frames tick clean, the canvas changes with cart state
(122→176→226→276 non-zero bytes), a `btnp` edge written into the snapshot
comes back as `sfx(3)` in the queue and a held button as `sfx(5, 2)`, pmem
lands at 41+4 dirty, and a raising `_update` returns `bad:1: boom` — text
with the line number, which is what crash-to-code needs.
`device/moycore_glue.py` is the Python half (snapshot refresh, queue drain
through the SAME `make_api` closures so sfx semantics stay in one place,
pmem write-back, a `supports()` source scan that routes superset carts to
`LuaCartRun`). **What is NOT done: nothing calls it.** `Player` still takes
the `LuaCartRun` path on every board, and no glass has run a moycore frame.
Original scope, still owed: Exit/crash returns a status the Player maps to
crash-to-code (`cart:LINE:` chunknames carry the line; the error text crosses
in the status — the Player's `self.ns` error-panel dependency ends).
*Deletes:* `LuaCartRun`'s registry for the moycore path. *Pins:* the trace
harness runs against the C loop.

**Stage 3 — S3.** Same core over the fold/bounce presentation. The fold
(#190) keeps working — moycore renders the cart canvas; the bounce pump
synthesizes bands from it exactly as today.

**Stage 4 — the wasm head. SHIPPED 2026-08-12.** It opened with the
feasibility spike §3.2 required: price the wasm shell raster
(runtime/canvas.py at desktop sizes vs moy_gfx-in-wasm) with real frame
numbers, since the cart canvas was moycore-covered and the SHELL was the
unpriced half. **The spike REPORTED** (`experiments/wasm_shell_raster/` —
MEASURED, real dist wasm VM + emcc-built vendored libmoy, node, 1024×600):
option (a), runtime/canvas.py interpreted in the shipped wasm MicroPython,
priced MARGINAL — desk repaint 37ms median / editor repaint 49ms (vs 6.5/8.4ms
under CPython); option (b), the vendored libmoy kernels compiled to wasm,
priced TRIVIAL — 0.04–0.1ms per full repaint + 0.42ms indexed→RGBA present,
~500–1,000× (a). The §10 gate ("both options unplayable → no completion path")
closed in the sunset's favor and the raster choice was made: **moy_gfx-in-wasm**.

**What shipped, and the one structural surprise: there is no new canvas
class.** The browser runs `device_canvas.DeviceCanvas` — the boards' own file,
staged verbatim — over ~100 lines of presentation glue
(`firmware/web_runner/web_canvas.py`: a `WebCompositor` answering
`size`/`framebuffer`/`back_buffer`/`gfx`, and a `WebSystemCanvas` that is
`P4SystemCanvas` minus the hardware). That is possible because DeviceCanvas
talks to a compositor INTERFACE rather than to a panel, and every genuinely
board-specific lever (the SRAM-bounce pump, the DPI ping-pong, GDMA async
copies, the PPA, PSRAM layer pooling) already sits behind a `getattr` probe
that finds nothing in a browser. So the directive's rung-1 deletion is larger
than planned: not just the recording stack, but the entire *possibility* of a
third raster. Three architectures, one raster, one set of goldens.

**MEASURED in the shipped VM (2026-08-12, node, 1024×600 desktop tier), with
paints verified rather than assumed:** full desk repaint **0.18ms** median
(p90 0.29) against the spike's 37ms interpreted — the console half is now
nearly free; a Lua game frame 0.39ms; the worker's framebuffer copy out of the
wasm heap 0.31ms; the page's 565→RGBA expansion 1.2ms. So the PRESENT
dominates the frame, which is the inversion the spike predicted ("dispatch,
not pixels"). End-to-end in real headless Chrome (`browsershot.mjs`, the
shipped page over CDP): **a locked 60fps** with worker step 0.5ms mean,
sustained, with the transferable ping-pong holding. And Lua carts got the
native batch lane for the first time in a browser — the old CommandCanvas had
no `_batch_arr`, so LuaCartRun fell back to a Python spr closure per sprite;
`brick_siege_lua` now reports 180 C-flushes / 643 quads with 0 upcall-falls.

*Deleted:* `CommandCanvas`, `DrawRecorder`, `RecordingLayer`, `ServedState`,
`SurfaceDelta`, `WsClientState`, the wire protocol, the page's JS replayer,
`runtime/web_view.py`, `runtime/web_view_page.py`, `tests/webharness.py`,
`tests/test_web_recording.py`, the resync/assets-hint node harnesses, and the
whole `/assets` PIXEL payload (palette, font, sheet, tilemap, images, covers —
with the 360–560ms serialisation, the incremental diff and the re-request latch
that existed to manage it). The Zero's streaming backend went too (un-runnable
since the die-now wave). *Survived on their own terms:* `runtime/web_input.py`
(the browser event decode — transport-shaped, not raster-shaped, and the §3.4
RPC speaks it) and `web_view_ws.py`.

*Pins:* `tests/test_streaming_sunset.py` gained stage 4's absence greps (both
modules gone; no recorder class anywhere in `runtime/`) plus, for the first
time, the POSITIVE claim — the runner build compiles `native/moy_gfx`, stages
the boards' `device_canvas.py`, and pixels leave the VM by address.
`test_web_worker_protocol.py` pins the framebuffer transport end to end
(`fb_addr`/`HEAPU8`/the transfer/`fbret`) and that the resync protocol is gone.
The head's shell pixels ride the host goldens, and `pageshot.mjs` decodes the
same framebuffer the browser blits instead of reconstructing a replayer.

*Landed with it:* the `surface_model_v1.md` §13 amendment (that doc is LOCKED;
§5.4 and §6 are retired, Phases B/D close, §1–§4 and §5.1–§5.3 unchanged).

**One survivor is deliberate and belongs to the other doc:** the WM's surface
registry is now unreachable (nothing sets `_recording`) and was kept anyway.
`surface_model_v1.md` §13 owns that decision and its reasoning; do not
re-litigate it here.

**Four build traps the rebuild surfaced**, all fixed at the source: a
counter declared inside the IDF guard but incremented by the Python render
entry (latent since the tempo hunt — it breaks every non-IDF build); dead
`static inline` code that only clang diagnoses; and two flag-ordering hazards
in the webassembly port's Makefile. CLAUDE.md's web-runner section carries the
operational detail, since that is where somebody hits them again.

### 6.9 The three remaining swaps, with the seams already surveyed

Rungs 1, 2 and 4 are complete. Rungs 3 and 5 have their cores built and
pinned and their PRODUCTION SWAPS outstanding. Each is now mechanical rather
than exploratory, and what follows is what a session needs so it does not
re-derive it. Execute in this order; the reasons are dependency, not taste.

**(a) `Player` -> moycore on the P4.** The board is REPL-alive and the
harness is proven (`tools/p4_autotest.py`, `tests/test_p4_on_glass.py`), so
this one needs no owner at the bench. Stage `native/moycore` into the P4
build beside `moy_gfx`/`moy_lua` (it requires both siblings -- it compiles
neither a raster nor a VM), inject `moycore_glue.make_moycore_runtime(ws)`
as `ws.lua_runtime` with `LuaCartRun` kept for carts `supports()` rejects,
and measure `brick_siege_lua` and celeste against #66's numbers. The claim
to test is stage 2's, not a speed one: collects during a 60s play window go
to ~0 cadence, and the residual spike ledger names its owner.

**(b) `Player` -> moycore on the S3.** Same change, but the T-Deck cannot be
flashed unattended (no BOOT button -- the trackball IS GPIO0, held at power
on) and its USB-CDC RX is dead under the desktop, so it wants the owner
present. Do it after (a) so only the presentation differs (the #190 fold
keeps working: moycore renders the cart canvas, the bounce pump synthesizes
bands from it exactly as today).

**(c) `canvas.py`'s verbs delegate to `raster_binding`.** The pixel oracle
is already passing (10/10 conformance scenes), so what remains is the seam,
and the seam is NOT uniform -- surveyed 2026-08-12:

  * **Shapes that already line up**, and therefore the place to start:
    `cls`, `pix` (write), `line`, `rect`, `rectb`, `circ`, `circb`, `tri`,
    `trib`, `sspr`, `tline`, `map`, `spr_tile`. For the sheet/tilemap-taking
    three, libmoy's console HOLDS its assets where canvas.py takes them per
    call -- so register them on the binding when they change rather than
    per verb.
  * **`spr(img, ...)` does NOT line up**: canvas.py's takes an `Image`
    (moybyte's paint images and layers), libmoy's takes a sheet tile index.
    Leave it Python-side until the superset question below is answered.
  * **`SystemCanvas.print` at `font_scale > 1`** draws each glyph pixel as
    an `fs x fs` rect block; libmoy's `moy_print` is scale-1. Delegate the
    1x path only.
  * **State stays PYTHON-authoritative and pushes downstream.** 28 sites
    outside the verbs read `_cam_x`/`_clip_*`/`_pal_map` -- layers,
    `blit_strip`, `scroll_rect`, `fill_rects`, the sprite variant caches.
    Push camera/clip/pal/palt into the C canvas from their setters (and
    from `set_viewport`, whose offset rides them). One authority with a
    downstream copy is the device's shipped `_gate_state` shape; TWO
    authorities is the disease.
  * The batch (`spr_tile` queues) exists to cut PYTHON dispatch. With the
    body in C its case weakens, but it also fixes draw ORDER -- so delete it
    only with the order rule re-pinned, not as a side effect.

**And the one question that gates all three, which is a product call rather
than an implementation detail (§9):** libmoy binds the SPEC table; moybyte's
cart API is a superset (`make_layer`/`draw_layer`/`image`, scenes, tables,
texts, `view`). Today every path routes superset carts to the old runtime by
source scan -- `moycore_glue.supports()` on device, `lua_host.moycore_supports()`
on host. That works and it is why lupa cannot leave `[dev,sim]` yet. The
options are C layers inside moycore, a handle shim, or accepting the split
permanently. The cart census says the pressure is low (one Lua cart uses
layers, at one blit per frame), so this can be decided on design grounds
rather than under a perf deadline.

**A warning worth carrying into all three, learned the hard way tonight:**
the host gate's first version was a plain substring scan and disqualified
EVERY cart in the tree, so the new path existed, the suite was green, and
nothing took it. Whatever routes work to moycore, assert that something
actually arrives -- per cart, by name. An untaken path is the failure mode
these swaps are most likely to have.

### 6.10 The batching rung — ANSWERED ON GLASS (2026-08-14): the verbs can go

**Settled by the Bench twins' new phase split, on the S3.** Python vs Lua,
line-faithful twins, PERF DIAG on:

    phase                  Python    Lua
    FLOOR (console frame)    18.0    19.0    within the 1ms clock
    LOGIC (+3000 iters)     +11.0    +5.0    Lua 2.2x FASTER
    DRAW  (+300 rects)       +0.0    +1.0    equal within the clock

**The draw paths are indistinguishable at 300 calls/frame**, so the per-call
overhead difference is <=3us/call and the batch verbs buy <=1ms at realistic
call counts. **`spr_batch`/`rect_batch`/`spans` can be deleted from the cart
API** — same verbs in both languages, which is what makes a kid's cart
spec-portable, and nothing measurable is lost. The x86 kernel A/B below DID
transfer, contrary to the (correct, and worth having had) worry that unix
microbenches usually do not.

Also settled: **Lua's arithmetic is 2.2x faster than Python's on this board**,
which explains ray_lua's ~2x (a DDA march is almost pure arithmetic) and
retires "Lua logic is slower on the S3" — that reading came from brick_siege vs
brick_siege_lua, which is not a twin. Numbers and the one open check (whether
the Python bench cart actually got auto-native) are in #66.

**What is left of this rung is a deletion, not a build:** remove the three verbs
from `make_api` on both boards and the host, from `docs/moy_cart_api.md`, and
from the carts that use them (`ray_test`, `brick_siege`) — which also makes
those pairs comparable by construction. No libmoy change, no new kernel, no
gate work.

**DONE 2026-08-14, and the deletion turned out to be a WIN, not a cost.** The
verbs are gone from both `make_api`s, the docs, the blocks reserved list and the
`moy-api.lua` stubs (which had declared `rect_batch`/`spans` to Lua carts that
could never call them). Ray Test and Ray Lua now render **0 differing pixels
over 45 frames** — the pair is one program in two languages.

The number this rung had been arguing about, measured properly on P4 glass with
the real console running (`ws.canvas`, 160 spans, best-of-9, the two paths
INTERLEAVED so drift hits both):

    160 x rect()                          1,135 us   <- what the cart writes now
    pack the span buffer + 1 fill_rects   1,659 us   <- what rect_batch needed
      of which: Python packing            1,325 us
                the kernel                  364 us

**The loop is ~0.53ms FASTER** (two independent runs: 524us, 551us). The batch
kernel really is cheap — 364us for 160 spans — but a cart that must refill its
span buffer every frame pays 800 array stores in Python to reach it, and that
costs more than the calls it saves. So the verbs were not merely worth ≤1ms:
for the raycaster that motivated them, they were a **net loss**, and the "kid
writes the obvious loop" answer is also the fast one.

Two caveats on reading this. It measures the RECT pair; `spr_batch` was already
redundant with the auto-batch gate, which coalesces a plain `spr()` run into the
same native `blit_batch` with no packing at all. And 9.16us/`rect()` is the P4's
dispatch cost, which the S3's differs from — but the S3's Bench twins already
answered the same question from the other direction (DRAW +0.0 Python vs +1.0
Lua at 300 calls), and the two agree.

### 6.10a The kernel A/B that got there (2026-08-13, superseded conclusion kept)

**The premise below was falsified within the hour, by the measurement it asked
for.** Kept in full rather than rewritten, because the reasoning was sound and
only the number was missing, and because the shape of the error is the useful
part: every step was a plausible inference from real data, and the one control
nobody had run turned a 2.9x into a 1.0x.

The batch-kernel A/B script (deleted with the experiment; git history has it)
ran on the unix build, 200 sprites, same
tiles, same positions, same canvas, both paths verified to draw an IDENTICAL
12,656 pixels:

    A  one moy_gfx.blit_batch of 200          10 us
    B  200 x libmoy moy_spr, via moycore/Lua  29 us   B/A = 2.90x
    C  the same Lua loop, sprites clipped     19 us   (VM loop + call overhead)
    B - C  the kernel alone                   10 us   (B-C)/A = 1.00x

**The kernels are identical.** `blit_batch` is not a better kernel than N
`moy_spr` calls; 65% of the apparent gap was the Lua loop and its per-call
overhead. Three consequences, each retiring something asserted above:

  1. **libmoy needs no batch kernel.** `moy_spr_batch` upstream would buy
     nothing. That half of this rung does not exist.
  2. **"moycore broke auto-batching for Lua" is wrong in its consequence.** The
     old `l_spr`'s value was avoiding a PYTHON call; moycore avoids Python by
     construction. Restoring gate-feeding would save the kernel difference,
     which is zero.
  3. **The gate cannot help Lua at all.** It coalesces N calls into one kernel
     pass; the cart still makes N calls. With no kernel difference there is
     nothing left for it to save.

**What survives, and it is the real question.** Batching still helps by
amortising the CALLS, and the only thing that amortises N calls is one call --
i.e. exactly the explicit verb the owner wants deleted. There is no free lunch
here, so the decision is not "can auto-batching replace the verbs" but **"is the
per-call cost small enough to delete them?"** On x86 the loop+call is ~95ns per
sprite. On the S3 it will be several times that, and THAT is the number that
decides: 200 sprites x 1us is 0.2ms and nobody cares; x 5us is 1ms and it
matters. Measure it on glass (the Bench pair's new DRAW phase is the
instrument) before touching the cart API.

**PRIOR ART, and it argues the S3 may disagree.** This was measured on x86, and
#66 already records a case of exactly this shape going the other way on glass:
"a pal() dispatcher split measured 5x on unix but is NOT shipped -- an extra
call layer costs ~5us on-glass (#63's empty-method number) and per-board
verdicts don't transfer", plus the lab hazard that "unix-MP microbenches of
LARGE functions carry a ~4us/call cliff". The S3's own per-call number is
known and large: `CALIB call4=635 us/100` = **6.35us per 4-arg call**, which is
precisely the 14.5 vs 9.5us gap the Bench micro phase shows between Python's
individual and batched spr.

So the S3 may not agree that the kernels are equal. The hint is in the same
table: Lua's spr measures 15.5us against Python's batched 9.5. If the kernels
were equal there, that whole 6us would have to be one `lua_CFunction` call --
possible, but large. The alternative is that `blit_batch` genuinely wins on a
board where hoisting the sheet and palette pointers across 200 sprites matters
more than it does on a machine with a real cache hierarchy. **Run the same
experiment on glass before concluding anything about the boards** -- the
harness is `experiments/batch_kernel_ab/`, and porting it is a small edit.

Two methodology notes, both of which cost a wrong answer first:

  * The first run reported 4.83x with the two paths drawing 6,589 and 12,456
    pixels -- `blit_batch` takes its stride in PIXELS (`self._stride = self.w`)
    and it was handed bytes, so it wrapped every row and lost half its writes
    off the end. **Compare pixel counts before comparing times**; a path that
    draws less is not faster.
  * Without control C the 2.9x reads as a kernel result and this rung gets
    built. One clipped-sprite run is the whole difference between a measurement
    and a reconstruction.

### 6.10 (superseded premise, kept for the reasoning) — auto-batch belongs in libmoy, not in the verb table

Opened 2026-08-13 from an owner question with a better answer than the one it
was asked about. The chain: the Lua and Python cart APIs have diverged
(`spr_batch`/`rect_batch`/`spans` marshal ARRAYS, so the bridge cannot carry
them and a Lua cart cannot call them); the owner asked to DROP those verbs
rather than port them, on the grounds that they are complicated to teach; and
then asked whether automated batching could replace them.

**It already does, and moycore broke it.** `DeviceCanvas` has carried the
`spr_gate` auto-batch since #43/#63 — a plain `spr()` loop coalesces into ONE
native `blit_batch`, which is why a kid never needed the explicit verb.
moycore's `spr` is libmoy's `l_spr`, which draws each sprite immediately and
never touches the gate. Two independent signs, both already on glass:

  * celeste under moycore logs `BATCH flushes=0 sprites=0 maxrun=0` — the gate
    sits idle for the whole run.
  * Bench: Lua `spr` **15.5µs/sprite** against Python's batched **9.5µs**.

`modmoy_lua.c`'s `l_spr` appended int16 quads straight into `_batch_arr` via the
spr_gate protocol; that is what made a Lua sprite loop coalesce, and it was
deleted with `LuaCartRun` on 2026-08-13 on the reasoning that libmoy's `spr`
superseded it. It superseded the DRAWING, not the BATCHING. Recoverable from
git rather than needing reinvention — but see the premise below before
recovering it, because the right home may not be moycore at all.

**THE PREMISE, WHICH IS NOT YET MEASURED.** Everything below rests on where the
6µs goes, and the honest reading of the bench is a reconstruction, not a
measurement:

    Python individual   14.5µs  = ~5µs crossing + ~9.5µs batched pixels
    Python batched       9.5µs  = pixels only
    Lua under moycore   15.5µs  = no crossing at all

If the batch win were only amortising the host crossing, Lua — which has none —
would already sit at ~9.5. It sits at 15.5, which says the win is in the KERNEL
(`blit_batch` hoists colorkey resolution, palette state and clip across a run;
`moy_spr` redoes them per call). **That inference comes from two rows of a table
taken on different carts and different runs.** Step one is therefore a direct
A/B in one place: time N × `moy_spr` against one batched call of the same N on
the unix build. It is twenty minutes and it decides whether this rung exists —
if the gap is the crossing after all, libmoy has no problem to solve and Lua
under moycore needs nothing.

**If the premise holds, the shape is a KERNEL, not a VERB.** `moy_spr_batch(c,
sheet, quads, n)` beside `moy_spr` in libmoy, with the host coalescing runs into
it. Nothing enters SPEC.md's verb table: batching stays an implementation
detail, so the API a kid learns does not grow by one word, and a cart that says
`spr()` in a loop is fast in both languages by construction.

Why upstream rather than in `moy_gfx`: the win is in the raster, and the raster
is libmoy's. Building it here would be a parallel implementation of a libmoy
concern — §3.5's disease, committed knowingly. Upstream it also lands in the
wasm player and the ESP-IDF reference for free, and `moy_gfx`'s own
`blit_batch` can then be deleted rather than maintained beside it.

**What it deletes, which is the point.** `spr_batch`, `rect_batch` and `spans`
leave the cart API entirely — from the docs, from `make_api`, from both boards.
The two Python-only verbs that forced the twin carts apart go with them, so
`ray_test`/`ray_lua` and `brick_siege`/`brick_siege_lua` become comparable BY
CONSTRUCTION instead of by a cfg switch (the twin audit is in the bench commit;
only the bench pair is an instrument today).

`rect`'s gate is the genuinely new half — `spr` has one and `rect` does not, and
`ray_test`'s whole thesis is that one `rect_batch` per frame is what makes
software 3D affordable (its own header prices the wall pixels at ~0.03ms;
per-column `rect` calls would be ~4.1ms). A span gate feeding `fill_spans` is
the same shape as the sprite gate and is what lets that verb go.

**And it settles the tier question §9 has been carrying.** Same verbs in both
languages means a kid's cart is spec-portable — which is the thing actually
being bought, and the reason to do this at all rather than document the
divergence and move on. Today: blocks compile to `main.py`, `moy_carts.create`
defaults to `runtime="python"`, so a kid never meets Lua and never meets the
divergence either. That is why this is a rung and not a fire.

## 7. Architecture sketch (what changed from v1)

Enter/exit as v1: `Player.start` (lua branch) calls `moycore.run_begin(...)`
with cart source, `sounds.json` text (already crosses once), sheet/tilemap
blobs, config/pmem snapshot, the game canvas buffer, an input snapshot region
the loop refreshes. Per MP frame: `moycore.tick(dt)`, one upcall. State that
stays Python: pmem persistence (dirty flags at tick boundaries), scenes/
tables/texts decoded at load and crossed as data, achievements/bookkeeping,
`textmode()`/`quit()`/`view()` flags.

**Presentation: unchanged, and now stated as contract compliance.** Moycore
renders the cart canvas and is, in `surface_model_v1.md` terms, the game
surface's *producer*: Class B (`animating`) while running, content gen moving
on frames it renders (L9 — frameskip semantics preserved), composited by
each backend's shipped strategy (fold on S3, PPA on P4, framebuffer blit on
the wasm head, plain blit on host). Moycore does not own the glass and adds
no invalidation mechanism (L10). Live-edit beside a windowed P4 run:
**decided** — moycore watches `sheet.gen` at the tick boundary and re-pulls
the blob on change; snapshot semantics, the same generation-counter idiom
the contract already blesses.

**Where the core lives: decided — split.** The spec-surface core (raster,
audio, VM, loop) grows UPSTREAM in moy-spec's libmoy — the wasm player
already forces host-agnosticism, and the vendoring rule says fixes belong
there. A moybyte-side `native/moycore` vendors it and owns the console
extensions that are NOT spec: layers/images, scenes/tables/texts glue, pmem,
net, textmode/quit/view. That is exactly audio's shipped shape (thin
`modmoy_audio.c` binding + vendored libmoy).

## 8. Interactions with the shell/UX doc ladder

- **`surface_model_v1.md` (LOCKED):** moycore complies (§7). At stage 4 the
  sunset retires most of its §5.4 web annex (per-surface streams,
  `SurfaceDelta`, the keyframe verb, the wire-protocol §6) — its §11
  open-question record already demoted per-surface canvases to "the device
  transport's bytes (#153)", and the device transport is what §3.2 kills, so
  Phases B/D effectively close. Because that doc is LOCKED, the retirement
  lands as an explicit versioned amendment to THAT file at stage 4 (the lock
  discipline implies it; the doc's own "versioned" language governs the wire
  protocol, so this is this plan's commitment, not a quote of that one);
  Phases A/C (host/P4) are untouched.
- **`shell_architecture_v1.md`:** §2 (privileged system carts) composes with
  this plan and leans on §3.3's decision that Python remains a cart
  language; §3.2's webview-as-window-manager motivation is superseded.
- **`ui_widgets_2026-08.md`:** orthogonal (the GUI half of "Python stays as
  the shell"); no interaction beyond both assuming the shell stays Python.
- **`docked_mode_2026-08.md`:** superseded (§2, §3.4). Its store-RPC phase
  re-scopes to the §3.4 sync RPC and proceeds on its own track, independent
  of moycore's stages; controllers survive as designed; the session model is
  buried with its reasoning in §3.4. Only the §3.2 sunset's *completion*
  waits on stage 4.
- **`docs/history/ui_damage_model_v1.md` / `visual_identity_v1.md` / `shell_ux_v1.md`:**
  untouched; the why-not-LVGL record and the UX spec are not in play.

## 9. Open questions (pruned — v1 had five; the LADDER's are all closed)

*Scope note, 2026-08-13: nothing below gates the §3.5 ladder any more. The
layers/images call was its last one and is decided just below. What survives
belongs to the **§3.4 sync RPC**, which this plan already scopes as its own
track, independent of the moycore stages — plus one measurement that wants S3
glass and is therefore waiting on the same flash stage 3 is.*

- ~~**Layers/images from Lua carts**~~ **DECIDED 2026-08-13: the compat
  shim. They stay Python-side.** The plan asked for this call to be made
  "with the trace harness in hand", and the evidence is a census rather
  than a preference:

  * **Pressure is one cart.** Across every Lua cart in the tree
    (`bench_lua`, `brick_siege_lua`, `ray_lua`, `sakura_lua`, celeste),
    exactly ONE calls `make_layer`/`draw_layer`/`image` — `sakura_lua`,
    which exists as the line-faithful A/B twin of the Python cart. The
    other superset users are `view()` (a flag, two carts) and
    `background()`.
  * **The shape is per-FRAME, not per-sprite.** A layer costs one
    allocation at `_init` and one window-copy per frame. The crossings
    moycore deletes are the per-sprite and per-primitive ones, hundreds
    per frame; a `draw_layer` upcall is one. So the thing C layers would
    buy is the smallest crossing on the board.
  * **The cost is a second console.** moybyte's layers are not just index
    buffers: each is a full canvas with the whole verb table bound to it
    (`_Layer._VERBS`). Implementing them inside moycore means a second
    console in C — which trades the duplication this project deletes for
    a smaller one, in service of one cart.

  **So: a cart using the superset runs on the trampoline registry, and the
  split is a source gate** (`moycore_glue.supports()` on the boards,
  `lua_host.moycore_supports()` on the host — hand scans, deliberately,
  after a regex version raised on MicroPython and made moycore unreachable
  on glass while looking healthy).

  **Consequences, stated so they are not read as loose ends:** lupa stays
  in `[dev,sim]` as the host's superset runtime, and `spr(Image)` stays on
  the Python raster for the same reason. Both are now deliberate, not
  pending.

  **What would reopen it** (the falsifiable form): a Lua cart that makes
  layers per-frame-HOT — `draw_layer` inside a sprite loop, or many layers
  composited per frame — would move this from one upcall to many, and the
  measurement, not the design, should decide it then.
- ~~**The screenshot verb** on the sync RPC (§3.2's partial mirror
  successor): worth its ~page of code, or does the capture job just die?~~
  **DROPPED (owner, 2026-08-25):** the wasm head covers the show-and-tell
  jobs — the browser IS the console, so sharing happens there; the capture
  job dies with the mirror as §3.2 priced in.
- **Sync mechanics** (§3.4) — two of three CLOSED 2026-08-25 with the RPC's
  landing: the browser-local substrate is **OPFS** (the ops are file writes
  at paths, so OPFS applies them 1:1 with no invented keyspace; measured 89
  files seeded in 184ms, a commit applied in 9ms — `moy_store.mjs`, #193),
  and pairing is **no token at all**: a pin in the QR-encoded url, checked
  per batch, re-pair by re-scanning the connection screen (#197's switch).
  Still open: how a two-sided collision *presents* to a kid — the rule is
  LWW + journal; the UX of "your device copy moved" is undesigned. (The
  switch UX shrinks the window — a parked glass does not author — but PLAY
  ON DEVICE still writes pmem under a live browser copy. **Shrunk again
  2026-08-25:** the journal now lives with the store of record, so a push
  the browser wins lands as a real commit on the board and the overwritten
  version is one UNDO away on the glass it was made on. That turns the
  worst case from silent loss into a recoverable surprise — which is not
  the same as designing the surprise away, so this stays open.)
- **Whether stage 1 alone captures most of the S3 win** — M0's attribution
  plus stage 1's measurement settle this before stage 2 is built (v1's
  question, now with the instrument named).
- ~~Blocks→Lua someday~~ **closed with §3.5** (§3.3): blocks keep
  graduating to Python; Python carts stay as the one deliberate
  duplication.

## 10. What would kill it

(Named for what they actually kill: the review's finding stands that no S3
number kills the PROJECT — both M0 outcomes re-queue it. These gates kill
CLAIMS, and a killed claim reorders the queue.)

- **M0 — RESOLVED (2026-08-12), both halves fired partway.** (c): celeste
  straddled the gate (p50 exactly 30, floor broken by a GC collect) — the
  fps case narrowed to spike deletion. (a): the deletable share measured
  ~2–4ms against the predicted 4–8 — the fps case shrank to its floor
  (~+2–3fps steady). The queue call was made the next evening (§3.5):
  moycore proceeds as the zero-duplication project, §4.2 carrying the
  whole case.
- **Stage 1 measured:** a zero-upcall celeste frame spending its ms in the
  same places → the MP engine slice was mis-attributed; same collapse.
- **Stage 2 measured:** the (a) churn split already fired half of this
  gate: shell-owned churn (~15–30%, measured on the zero-upcall floor
  cart) means collects do NOT go to ~0 — the honest stage-2 claim is
  cadence ~3–6× longer with the ~190ms cost per collect surviving. What
  remains falsifiable at stage 2: the cadence stretch itself, and whether
  a run-start collect keeps a 60s window clean.
- **Stage 4's spike — RESOLVED (2026-08-12), gate closed in the sunset's
  favor:** interpreted canvas.py priced marginal, moy_gfx-in-wasm priced
  trivially playable (numbers in §6 stage 4); the sunset has a completion
  path and its raster choice is made.
- **The pin surface not getting built:** if the §4.2 semantic trace harness
  lags the C implementations — or a crossing lands outside its vocabulary —
  moycore *recreates* the parallel-implementation disease at the semantic
  layer while deleting it at the mechanical one — a net loss this doc's
  staging rule exists to forbid. No stage lands without its pins; that is
  the line.
