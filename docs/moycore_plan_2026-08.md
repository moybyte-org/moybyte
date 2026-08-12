# Moycore (2026-08): one engine under two languages — the C play path, the Python shell, and the sunset ledger

**Status: v6 (2026-08-12) — the 3.2 die-now sunset wave SHIPPED, the stage-4
wasm-raster spike REPORTED (gate closed: moy_gfx-in-wasm), and M0's verdicts
stand. v5 (2026-08-11 night) was the graduation: stage 0 + the stage-1 pin
shipped, the M0 cheap levers built, the design-doc treatment run.**
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
- **Alive, deliberately:** `runtime/canvas.py` — the host shell rasterizes
  through it, so it survives as the golden-gated shell/Python-cart raster.
  Two raster implementations, both gated, is the end state — not one.

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
`tests/webharness.py` for the console-over-recording-canvas suites).
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
stage previously named: either `runtime/canvas.py` interpreted at desktop
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
that remains, and its required pin). The hidden product call it surfaces:
**blocks graduate to Python today** (the MakeCode model, #111). If the kid
ladder ever re-targets blocks→Lua, Python-as-cart-language could sunset
entirely and MicroPython becomes purely the GUI toolkit. That is a decision
about what language kids meet at graduation — recorded here as an **explicit
open product call, not a plan**; nothing in moycore depends on it either way.

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
standing semantic asymmetry the v4 draft understated. Post-moycore, Lua
carts get C implementations of input/state/audio verbs while Python carts
keep the Python ones: parallel implementations at the *semantic* layer,
where pixel conformance sees nothing. That trade is accepted — the
mechanical seams are per-frame-hot, the semantic surface changes rarely —
but only under the staging rule: **each stage ships with deletions AND
pins** (§6). The pin is BUILT (`tests/test_semantic_traces.py`, 2026-08-11):
the unix-port dual-usermod build that `tests/test_lua_draw_direct.py` uses
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
| `l_draw` direct-C hot shapes (#189) | **becomes moycore's native surface** | `test_lua_draw_direct` byte-parity |
| `__moy_map_masked` + flags (the p8 shim's C map walk, #66 M0) | **already moycore-shaped** — folds into the native surface | the 12 masked-map A/B scenes (byte-exact) |
| prelude `rnd`/`flr` (pure Lua, M0) | **stays** — VM-local by design | flr: add to the trace vocabulary; rnd: unpinnable on purpose (random), the recorded exception |
| trampoline → Python closure (per-verb) | **dies for Lua on the boards** at stages 1–2 (the registry survives on the wasm tier until stage 4); note tables/buffers never crossed it, so `spr_batch`/`rect_batch`/`spans`/`Image` are already Python-cart-only | the §4.2 semantic traces, verb by verb as each crosses |
| `l_draw_fallback` odd shapes (pix 2-arg read, nils) | **must move C-side or the registry survives** — stage-1 decision, not a footnote | NOT yet pinned: the 16-case matrix is hot-shapes-only and the dispatch assert proves the fallback *fires*, not that it *matches* — the stage-1 decision ships WITH a real odd-form A/B |
| batch protocol (`begin_batch`/`flush_batch` upcalls, token, order rule) | **dead since 1a** — flush moved into C | pixel goldens + the trace harness's interleaved spr/primitive frames (the named "order-rule test" was only ever a source grep + a zero-pixel flush; the harness is the behavioral pin) |
| camera/clip/pal Python-authoritative mirrors | **ownership flips** to moycore during a run; shell reads back at exit | state-verb traces — which do NOT yet observe the exit-time read-back (the trace resets state per frame); extend before this crossing |
| `DeviceCanvas` Python methods + #155 gates | **survive** — Python carts and chrome | device↔host parity as today |
| `runtime/canvas.py` host raster | **survives** — host shell + Python carts | `test_spec_conformance` goldens |
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
lane), pinned byte-exact by 12 A/B scenes in `test_lua_draw_direct`; and
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
stretches the collect cadence ~3–6× rather than deleting collects. M0 is
CLOSED but for one cell: the floor's absolute presentation slice was
measured at 1× (the variants lacked the `view` hint — deltas are
presentation-invariant and stand; the absolute is PREDICTED from celeste's
own composite/flush lines until a 2× floor run lands; the patched carts
are staged). Numbers live in #66, as always. Be honest about what the
gates can kill: no S3 number kills the *project* (both outcomes re-queue
it); what a number kills is a CLAIM — (c) sharpened the fps claim, (a) then
shrank it to its floor: **the whole engine-side prize is ~+2–3fps steady
(low-30s) plus spikes 3–6× rarer but not gone.** Stage 2's go/no-go is now
an owner call weighing that against §4.2's maintainability case.

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

**Stage 1 — C-only cart ABI.** Extend #189 until a celeste frame makes ONE
Python round trip (the tick). The work, sized by the seam map: move the
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
cache lives in `runtime/canvas.py`, which survives regardless); state-verb
ownership; input snapshot; audio queue; the odd-shape decision — which
ships WITH a real odd-form A/B, since today's matrix covers hot shapes only.

**Stage 2 — the core loop, P4 first.** `moycore.tick` owning
`_update`/`_draw` end-to-end on the REPL-alive board (testable without the
owner, #156 harness). Exit/crash returns a status the Player maps to
crash-to-code (`cart:LINE:` chunknames carry the line; the error text crosses
in the status — the Player's `self.ns` error-panel dependency ends).
*Deletes:* `LuaCartRun`'s registry for the moycore path. *Pins:* the trace
harness runs against the C loop.

**Stage 3 — S3.** Same core over the fold/bounce presentation. The fold
(#190) keeps working — moycore renders the cart canvas; the bounce pump
synthesizes bands from it exactly as today.

**Stage 4 — the wasm head.** OPENS with the feasibility spike §3.2 now
requires: price the wasm shell raster (`runtime/canvas.py` at desktop sizes
vs moy_gfx-in-wasm) with real frame numbers — the cart canvas is
moycore-covered, the SHELL is the unpriced half, and no sunset date exists
until this spike reports. **The spike REPORTED (2026-08-12,
`experiments/wasm_shell_raster/` — MEASURED, real dist wasm VM + emcc-built
vendored libmoy, node, 1024×600):** option (a), `runtime/canvas.py`
interpreted in the shipped wasm MicroPython, prices MARGINAL — desk repaint
37ms median / editor repaint 49ms (vs 6.5/8.4ms under CPython), playable
only if the shell's whole partial-repaint discipline holds, with no headroom
for slower client machines; option (b), the vendored libmoy kernels compiled
to wasm, prices TRIVIAL — 0.04–0.1ms per full repaint + 0.42ms indexed→RGBA
present, ~500–1,000× (a), so a 10× slower client still holds 60fps. The §10
gate ("both options unplayable → no completion path") is CLOSED in the
sunset's favor; stage 4's raster is **moy_gfx-in-wasm** (a usermod of the
same vendored sources — the honest cost note: the shell's ~100–150 verb
calls/frame make DISPATCH, not pixels, the binding's real budget, like the
P4). Then: the runner adopts moycore (raster compiled in, page blits the
framebuffer); the recording stack's last consumer dies,
which *completes* the §3.2 sunset. Requires the `surface_model_v1.md` §5.4
amendment (§8; an amendment NOTE naming the two dead transports landed with
the die-now wave). *Deletes:* `CommandCanvas`, the replayer, the wire
protocol, `DrawRecorder`/`RecordingLayer`. *Pins:* the spec player's
conformance already covers the core; the head's shell pixels ride the host
goldens.

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
- **`docs/ui_damage_model_v1.md` / `visual_identity_v1.md` / `shell_ux_v1.md`:**
  untouched; the why-not-LVGL record and the UX spec are not in play.

## 9. Open questions (pruned — v1 had five, three are now decided)

- **Layers/images from Lua carts** (`make_layer`/`draw_layer`/`image` via the
  handle glue): C layers inside moycore (they are index buffers +
  window-copy — libmoy-shaped) vs a compat shim that keeps them Python-side
  with per-layer crossings. Decide at stage 1 with the trace harness in hand.
- **The screenshot verb** on the sync RPC (§3.2's partial mirror
  successor): worth its ~page of code, or does the capture job just die?
- **Sync mechanics** (§3.4): the browser-local store substrate (OPFS vs
  IndexedDB), pairing-token lifetime vs plain re-pair-by-QR, and how a
  two-sided collision *presents* to a kid — the rule is LWW + journal; the
  UX of "your device copy moved" is undesigned.
- **Whether stage 1 alone captures most of the S3 win** — M0's attribution
  plus stage 1's measurement settle this before stage 2 is built (v1's
  question, now with the instrument named).
- **Blocks→Lua someday** (§3.3): the recorded product call, no engine
  dependency, no deadline.

## 10. What would kill it

(Named for what they actually kill: the review's finding stands that no S3
number kills the PROJECT — both M0 outcomes re-queue it. These gates kill
CLAIMS, and a killed claim reorders the queue.)

- **M0 — RESOLVED (2026-08-12), both halves fired partway.** (c): celeste
  straddled the gate (p50 exactly 30, floor broken by a GC collect) — the
  fps case narrowed to spike deletion. (a): the deletable share measured
  ~2–4ms against the predicted 4–8 — the fps case shrank to its floor
  (~+2–3fps steady). Moycore's queue position is now an owner call: the
  engine-side prize is small and precisely known, and §4.2 carries the
  rest of the case.
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
