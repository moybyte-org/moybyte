## START HERE — what are you about to touch?

This file is a MAP. Find your row, read the authority it names, then come back
for the rules. Reading it top to bottom is the slow path.

| you are about to… | read FIRST | the thing that bites |
|---|---|---|
| change a draw verb / the raster | `docs/surface_model_v1.md` §4, then `device/device_canvas.py` | ONE canvas class runs on every tier. `tools/p4_conformance.py` is the only check that reaches the real C on real glass. |
| add or port a board | `docs/board_ports_2026-08.md` — its stage-6 "TAKE THESE" list | four shared bodies are taken, not copied; a lever a board lacks is expressed by ABSENCE, never by 0 |
| touch a panel flush | `native/moy_flush/moy_flush.c`'s header | "every clause was a race once". `tests/moy_flush_harness/` compiles it with no board attached |
| touch SD or the panel bus | that board dir's README | the two drivers share one SPI host; a per-op teardown hangs the board with no panic |
| change the shell / a WM / an app | `runtime/README.md` (per-file map), `docs/app_api_v1.md` | pixel goldens are the net, and the 320×240/1× row does NOT exercise the toolkit |
| add a cart verb | `docs/moy_cart_api.md`, and SPEC.md in the moy-spec repo | the verb table is a PUBLIC spec; Python and Lua must agree verbatim |
| change the PICO-8 importer or its Lua shim | `PICO8.md` and `p8_lua_port.py` in the moy-spec repo | `tools/p8_lua_port.py` is VENDORED (`make vendor-p8-import`); the corpus gate `make -C libmoy p8-carts` is the net, and a cart that fails only on a board is usually the frame cadence (`run_cart --dt`) |
| touch audio | `native/moy_audio/libmoy/UPSTREAM.md` | it is VENDORED — fix it upstream in moy-spec and re-vendor, never here |
| touch multiplayer | `docs/netplay_v1.md` | the payload is INPUTS, never state; a missing input STALLS, it never extrapolates |
| touch the browser build | `firmware/web_runner/`, `docs/moycore_direction.md` | two web modes, no crossover; where a page is SERVED from decides where its carts live |
| chase a performance number | **#66** (per-cart fps), **#58** (P4), `docs/perf_native_gap_v1.md` (#77) | numbers live in issues, never in this file — see the rule below |
| drive a board over serial | `tools/p4_autotest.py`, the three `tests/test_*_on_glass.py` | the boards' line-state rules are OPPOSITE; `[serial]` in board.toml is the authority |
| edit any document | — | run `tools/check_docs.py`; it resolves every path AND pins duplication downward |

**Four rules that outrank anything below.** Host and device are ONE codebase, not
a port — a drawing change lands in the one canvas class or the "one cart, every
tier" contract breaks. A number that MEASURES the system belongs in its issue,
not here. A board that lacks a lever reports `None`, never `0`. And a mechanism
promoted into one body needs an executable guard, or it rots silently — that is
the failure most of the entries below were written to prevent.

## What belongs in this file (read before adding to it)

**Decisions, not status.** A decision keeps its value forever — "the indexed
canvas was A/B'd on P4 glass and RGB565 won (2026-08-05)", "the double game
canvas was built, measured and REVERTED (`26e1f9f`)", "do NOT re-attempt in-loop
serial on the T-Deck". Those are what stop the next session re-doing dead work,
and they are historical BY NATURE; keep them, and keep their dates and commit
hashes. **A status claim rots.** "Still needs on-hardware verification",
"pending next flash", "not yet verified" are true for a week and then quietly
lie — and a doc that lies is worse than one that is silent, because the reader
acts on it. Status belongs in a GitHub issue, which has state; this file should
say what IS, with a date, or point at the issue.

That is not hypothetical. On 2026-08-15 this file said OTA "Still NEEDS
ON-HARDWARE VERIFICATION" in the paragraph directly ABOVE the one recording the
whole chain passing on glass on both boards, and an agent duly told the owner
their T-Deck might need a migration flash it had taken two weeks earlier.

**Same rule for numbers, and this file is a MAP to them, never a copy of them.**
A measurement pasted here goes stale exactly as this paragraph warns — one did,
inside the sentence forbidding it, and on 2026-08-28 a swept pass found the
T-Deck's image headroom recorded as `186KB` when the build reported eight times
that. State the DECISION and point at the number's home:

| what | where it lives |
|---|---|
| per-cart fps, frame budgets, the lever ledger | **#66** |
| P4 port state, its transitions and image cost | **#58** |
| cross-board strategy, the A/B benches, the ranked levers | `docs/perf_native_gap_v1.md` (**#77**) |
| Guition port + its A/Bs | **#202** · Lua/moycore tier **#67** · crisp composite **#204** |
| a board's image headroom | the build prints it; nothing else is current |
| how a subsystem works | that module's or board dir's README/header |

The numbers that MAY stay are the ones that are configuration rather than
status — a chunk size, a timeout, a headroom floor, a gesture's hold time —
because those are the design, not a measurement of it.

## What this repo is

Moybyte is an operating system for ESP32 boards: a console where the software is
cartridges, running as firmware on three console boards (plus one headless companion)
alongside a host simulator and a browser build.
Everything is ONE system: **`.moy` is the only cart format.** (A separate
`.moyproj` SDK was deleted 2026-07-31 because nothing depended on it but its own
tests; the block compiler was always separate and lives in `runtime/blocks.py`.
Git history has the rest — do not reintroduce the format.)

- `runtime/` — the **host reference** of the console (launcher → Player → tabbed Editor). Pure host, fast dev loop. See `runtime/README.md` for the per-file map; don't duplicate it.
- `firmware/lilygo_t_deck_plus_mainline/` · `firmware/esp32_p4_wifi6_touch_lcd_7b/`
  · `firmware/guition_jc3248w535/` — the three console board ports (MicroPython).
  `firmware/seeed_xiao_esp32s3_zero/` is the fourth build target and the odd one:
  HEADLESS (#41), the kid's cart store the browser console pairs with, promoted
  out of its stock-MicroPython/pushed-modules arrangement on 2026-08-29. Each
  dir's README is the authority on its hardware; `.claude/rules/boards.md` carries
  the constraints that hang a board.
- `system_carts/*.moy` — seed cartridges (folder = `manifest.json` + `main.py` + `config.json`).

The shipped shell is the **2026-07 shell** (everything-is-a-process: launcher / Player / Editor apps
over a fullscreen-stack WM; spec `docs/shell_ux_v1.md`).

**Versions: there is exactly one ladder, and docs are not on it.** The only
version a user ever sees is the firmware's — `moy_ota.FIRMWARE_VERSION`, a
monotonic integer cut by `make release` and tagged `vN`, carrying a human
`label` (`0.6`) that the update screen and the OTA manifest show. Betas stamp
the build epoch instead, labelled `beta <date>`. Everything else is **dated,
not numbered**: design docs, plans, and shell generations are named by the
month they describe (`moybyte_console_plan_2026-07.md`, "the 2026-07 shell"),
because a `v0.5` beside a firmware `0.6` reads as an older release and is not
one. Archived plans keep their historical `v0_N` filenames under
`docs/history/` — that is what git history and the issues call them. Two names
that are FORMAT generations and deliberately stay: the `.moy` cart format and
the indexed-canvas contract (still `v04` in `tests/test_v04_userland.py`,
`docs/audio_design_v04.md`, and code comments) — renaming those would churn the
public cart vocabulary for no gain.

## Common commands

```bash
make setup          # python -m venv + pip install -e '.[dev,sim]' (hermetic: NOT --system-site-packages)
make test           # pytest (all). The venv python is .venv/bin/python

# run a single test
.venv/bin/python -m pytest tests/test_v04_userland.py -k cards
.venv/bin/python -m pytest tests/test_micropython_spike.py::test_name
```

`.moy` console (host):
```bash
python tools/simulate_desktop.py                                  # boots the launcher (needs a display)
python tools/simulate_desktop.py --cart system_carts/star_catcher.moy
python tools/simulate_desktop.py --demo --gif demo.gif            # headless tour
```

## Conventions

- **The standing docs, and what each one settles.** `ls docs/` lists them and the
  router above routes to them; these are the ones that CLOSE a question, so
  reopening one means arguing with the doc, not with this file:
  - **`docs/surface_model_v1.md`** — the presentation CONTRACT for every backend.
    Read it before touching any rendering, compositing or invalidation code on any
    tier. **Its §8 graveyard is settled**: a new backend implements the §4
    compositor contract, it does not invent a new invalidation mechanism. §8 also
    carries the why-not-LVGL decision, so that is not re-litigated.
  - **The fine-grained damage architecture was KILLED by its own review**, and the
    reason generalises: its "content-independent chrome" finding had no power,
    because every one of those draw loops iterates the VIEWPORT, not the content,
    so identical numbers were structurally guaranteed. What survived is §14, the
    focused-window content freeze. One invalidation mechanism, not six — when
    there were six, two carried the same wrong key, cost most of a frame twice per
    gesture, and produced no signal at all.
  - **`docs/board_ports_2026-08.md`** — the standing doc for ADDING A BOARD; read
    its checklist and its stage-6 "TAKE THESE" list before starting a port. Its
    declines (no driver registry/ABI, no codegen) are recorded so they are not
    re-proposed.
  - `moybyte_console_plan_2026-07.md` is the current design doc;
    `docs/shell_ux_v1.md` is the shell's UX reference, corrected to as-built.
    Superseded plans live under `docs/history/` and are history, not direction.
- **Issue mirror (`docs/issues/`, gitignored):** a local, un-committed snapshot of
  every GitHub issue (`open/`, `closed/`, `INDEX.md`), so an issue number in a
  commit or a chat resolves with no network. GitHub is the source of truth and
  the script wipes and rewrites both folders, so a stale copy cannot survive.
  **Run `make sync-issues` at the start of any session that reasons about issues,
  and again after EVERY issue you open/close/comment/edit** — the mirror is only
  trustworthy if syncing is a reflex, and a living-body issue like #66 goes stale
  locally the moment it is edited on GitHub. Never hand-edit the files.
- **Device performance — #66 is the single source of truth** (the living ledger:
  current per-cart fps, the frame-budget model, shipped/reverted/open levers, how
  to measure). **Edit #66's BODY when new hardware numbers land; comments are the
  changelog** — then `make sync-issues`. P4 numbers are **#58**; the cross-board
  strategic analysis (why we trail native emulators, the frame-budget taxes, the
  PPA scale-only verdict, the ranked lever roadmap) is
  `docs/perf_native_gap_v1.md`, tracked by **#77**.
  - **Do NOT scatter numbers into this file.** One snapshot here quoted a cart's
    fps long after the board had passed it, inside the sentence forbidding
    scattered numbers.
  - **The engine-side lever chain is EXHAUSTED.** Every feed/dispatch/GC-cost
    lever was tried and either shipped (auto-native carts #67, live-set diet,
    pal-state variant cache #72, layer pool, `background()`) or reverted with a
    recorded verdict (Fold-2 auto map cache; the third bounce slot, which also
    retired the core-1 feeder unbuilt). What is left: per-cart render diets, the
    #67 Lua tier, and the P4 (#58).
  - **Per-board verdicts do NOT transfer.** The `-O3` `moy_gfx` pragma is
    A/B-confirmed on the S3 (compute-bound there) and measured NULL on the
    dispatch-bound P4 — one pragma line, opposite answers.
  - **Frameskip (#77) ships OFF** (Settings → FRAMESKIP, persisted, serial
    `skip 0|1`): a GAME's logic+input+audio tick every loop frame, render and
    flush every second — full-rate logic, 30Hz motion, at the cost of roughly
    double the alloc churn.
  - **Kid mode (#68): PERF DIAG is OFF by default and gates the diag frame-eaters
    — a measurement session needs it ON**, and DIAG SD LOG separately gates the
    periodic diag→SD write (keep it off for stutter-free serial measurement).
  - Three interpreter-vs-kid-idiom taxes were fixed ENGINE-SIDE with the kid API
    untouched: per-draw-call dispatch (#43/#63), call-frame heap-spill (#63), and
    **float boxing** — REPR_A allocated per float result, and the resulting
    heap-wrap collect was the long-standing micro-stutter; the REPR_C build patch
    (unboxed 30-bit floats) fixed it. Banding is structurally gone (the SRAM-bounce
    flush: panel DMA reads only internal SRAM).
  - Diagnostics, all gated behind `perf_capture`: `PERF`/`DRAWBRK`/`DRAW2`/
    `BATCH`/`FLUSHBRK`/`CHROMEBRK`/`PUMP`/`I2CSTAT`/`CALIB`/`HITCH`.
  - Open defects: #74 touch stalls, the launcher live-wallpaper cost, and #69's
    keyboard+touch I2C stalls (sized via I2CSTAT).
- **Branches and releases: `dev` is where work lands; `master` is what users get.**
  Commit to `dev` by default — a change is not on master until a human has tested
  it on the boards it touches, and **never push straight to master anything a
  board can run**.
  - **The two branches ARE the two OTA channels.** A push to `dev` builds a beta
    (channel `unstable` → the `firmware-beta` release); a push to `master` builds
    a stable (→ `firmware-latest`, which the site's flasher writes and the stable
    OTA offers). Host CI runs on both; the site republishes only off master.
  - **The merge into master IS the release**, and `make release NAME=0.7` is how:
    clean-tree checks → `make test` → `merge --no-ff dev` → bump
    `FIRMWARE_VERSION` AND set `FIRMWARE_NAME` → commit + tag → **stop**, printing
    the push command. Pushing master is the moment a device somewhere is offered
    the build, so it stays a separate deliberate keystroke.
  - **`NAME` is the release** (`MAJOR.MINOR`; a third component only for a pure fix
    release). It becomes the tag, the manifest `label` and the string on the kid's
    update screen. `FIRMWARE_VERSION` stays an opaque counter nobody reads — it is
    signed as an int and betas stamp a build epoch into it. **Do not hand-bump it.**
    Re-cutting a name already tagged is refused, which is the prompt to pick a fix
    release.
  - Firmware builds are path-filtered pushes and the workflow's per-ref
    `cancel-in-progress` collapses a burst of dev pushes into one build of the last
    commit, so "every push builds" costs one build, not ten.

## Where the rest lives

Everything else is a **path-scoped rule** under `.claude/rules/`: it loads only
when you touch the matching files, so it is in context exactly when it applies
and costs nothing when it does not. Read one directly if you want it early.

| rule | loads when you touch |
|---|---|
| `boards.md` | `firmware/**`, `device/**`, `native/**`, the board tools |
| `shell.md` | `runtime/**` — the kernel, the WMs, the carve invariants |
| `rendering.md` | the canvas, `ui`/`skin`/`chrome`, the `*_layer` surfaces |
| `carts.md` | `system_carts/**`, the cart API, the Lua tier, vendored audio |
| `web.md` | `firmware/web_runner/**`, the sync RPC, the webhost |
| `ota.md` | `device/moy_ota.py`, the release and signing tools |
| `netplay.md` | `runtime/netplay.py`, `device/moy_espnow.py` |
| `testing.md` | `tests/**` — the on-glass suites and the coverage standard |
