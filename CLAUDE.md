## START HERE — what are you about to touch?

This file is a MAP. Find your row, read the authority it names, then come back
for the rules.

| you are about to… | read FIRST | the thing that bites |
|---|---|---|
| drive, flash, test or screenshot a board | the `on-glass` skill (`tools/board.py`) | the boards' line-state rules are OPPOSITE (`[serial]` in board.toml is the authority); a port another process holds is somebody's session |
| chase a performance number | the `perf` skill, then **#66** / **#58** / `docs/perf_native_gap_v1.md` (#77) | numbers live in issues, never in a doc; per-board verdicts do not transfer |
| change a draw verb / the raster | `docs/surface_model_v1.md` §4, then `device/device_canvas.py` | ONE canvas class runs on every tier. `tools/p4_conformance.py` is the only check that reaches the real C on real glass |
| add or port a board | `docs/board_ports_2026-08.md` — its stage-6 "TAKE THESE" list | shared bodies are taken, not copied; a lever a board lacks is ABSENCE, never 0; `git add` the board's modules BEFORE its first build or the stager prunes them |
| touch an ESP32-P4 board | that board's README, then `native/p4/` | the panel is a board DEFINE (`MOY_DSI_PANEL_*`); compositor `device/dsi_panel.py`, PPA canvas `device/p4_canvas.py`, P4 tier `device/p4_desktop.py` over `device/desktop_spine.py` — a fix lands once |
| touch a panel flush | `native/moy_flush/moy_flush.c`'s header | "every clause was a race once"; `tests/moy_flush_harness/` compiles it with no board |
| touch SD or the panel bus | that board dir's README | the two drivers share one SPI host; a per-op teardown hangs the board with no panic |
| change the shell / a WM / an app | `runtime/README.md`, `docs/app_api_v1.md` | pixel goldens are the net, and the 320×240/1× row does NOT exercise the toolkit |
| add a cart verb | `docs/moy_cart_api.md`, and SPEC.md in moy-spec | the verb table is a PUBLIC spec; Python and Lua must agree verbatim |
| touch a compiled (wasm) cart or the tier | `docs/wasm_tier_plan_2026-09.md`, then `experiments/wasm_aot/README.md` | the ABI is moy-spec's (proposals/wasm-runtime.md), the measurements are **#158**, and how a host EXECUTES a module never enters the spec |
| touch `native/moy_wasm/` | its README | VENDORED from our WAMR fork (`make vendor-wamr`, pin in `native/moy_wasm/wamr_pin.h`): fix the fork and re-vendor, never edit the copy; a board TAKES or denies it in board.toml |
| change the PICO-8 importer or its Lua shim | `PICO8.md` and `p8_lua_port.py` in moy-spec | `tools/p8_lua_port.py` is VENDORED (`make vendor-p8-import`); the net is `make -C libmoy p8-carts`; a board-only failure is usually frame cadence (`run_cart --dt`) |
| touch audio | `native/moy_audio/libmoy/UPSTREAM.md` | VENDORED — fix it in moy-spec and re-vendor |
| touch multiplayer | `docs/netplay_v1.md` | the payload is INPUTS, never state; a missing input STALLS, never extrapolates |
| touch the browser build | `firmware/web_runner/`, `docs/moycore_direction.md` | two web modes, no crossover; where a page is SERVED from decides where its carts live |
| edit any document | `.claude/rules/docs.md` (loads with any `.md`) | `tools/check_docs.py` resolves paths and pins duplication, but cannot see a sentence go FALSE |
| open, close or comment on an issue | the `issues` skill | `make sync-issues` after every issue mutation |
| cut a release / push to master | the `release` skill | the merge into master IS the release; never push to master anything a board can run |
| push anything | `tools/preflight.sh` | **`make test` is not the CI job**: preflight adds the steps that compare DERIVED artifacts (the baked web blob, moy-spec's `runner/`, `ports/p8`) to their sources, in CI's order. moy-spec has its own, whose wasm half runs in a PINNED emscripten container (emcc is not byte-reproducible across versions) |

**Four rules that outrank anything below.** Host and device are ONE codebase, not
a port — a drawing change lands in the one canvas class or the "one cart, every
tier" contract breaks. A number that MEASURES the system belongs in its issue,
not here. A board that lacks a lever reports `None`, never `0`. And a mechanism
promoted into one body needs an executable guard, or it rots silently.

**Docs say what IS.** Decisions keep their dates and commit hashes; status
("pending", "not yet verified") goes to an issue, which has state. When you
change behaviour, grep the vocabulary you moved and correct every claim it
made false, in place. The rest is `.claude/rules/docs.md`.

| numbers about… | live in |
|---|---|
| per-cart fps, frame budgets, the lever ledger | **#66** |
| the P4 port, its transitions and image cost | **#58** |
| cross-board strategy, A/B benches, ranked levers | `docs/perf_native_gap_v1.md` (**#77**) |
| the Guition ports · Lua/moycore tier · crisp composite | **#202** · **#67** · **#204** |
| the compiled tier | **#158** |
| a board's image headroom | the build prints it; nothing else is current |

## What this repo is

Moybyte is an operating system for ESP32 boards: a console whose software is
cartridges, running as firmware on the boards below, in a host simulator and in
a browser. **`.moy` is the only cart format** (the `.moyproj` SDK was deleted
2026-07-31; the block compiler is `runtime/blocks.py`; do not reintroduce it).

- `runtime/` — the host reference of the console (launcher → Player → tabbed
  Editor), staged into every board image. `runtime/README.md` is the file map.
- `firmware/` — one dir per build target, each README the authority on its
  hardware: `lilygo_t_deck_plus_mainline/` (T-Deck, `tdeck`),
  `esp32_p4_wifi6_touch_lcd_7b/` (Waveshare P4, `p4`), `guition_jc3248w535/`
  (`guition_s3`), `guition_jc8012p4a1c/` (10.1″ P4, `guition_p4`), the headless
  `seeed_xiao_esp32s3_zero/` (`xiao_zero`, #41) and `web_runner/`. The ids are
  each board.toml's `[board] ota`, the name every tool takes.
- `device/`, `native/` — the device tier and the C modules every board stages.
- `system_carts/*.moy` — the seed carts (manifest + main + config).

**Where a piece belongs (owner, 2026-09-26).** What a cart author needs to make
a game for every moy host — the verb table, libmoy, the bindings, the CLI,
conformance, starter templates — goes to moy-spec. moybyte-org's installable
carts live in git repos split by licence (GPL ones in moybyte-org/gpl-carts);
user carts go through the planned store (#122–#124). How a console runs them
stays here: boards, shell, store, OTA, per-chip compiling and signing, and the
seed set (`system_carts/`: the shell's apps and wallpapers, Python-only games,
and the fixtures the perf ledger and parity harness use).

The shipped shell is the **2026-07 shell** (everything-is-a-process; spec
`docs/shell_ux_v1.md`). **One version ladder**: the firmware's
(`moy_ota.FIRMWARE_VERSION`, cut by `make release`, shown by its `label`).
Docs, plans and shell generations are DATED, not numbered; archived plans keep
their `v0_N` names under `docs/history/`. Two format names stay on purpose: the
`.moy` format and the indexed-canvas contract (`v04`).

## Settled — reopening one means arguing with its doc

- `docs/surface_model_v1.md` is the presentation contract for every backend; its
  §8 graveyard is closed (a new backend implements §4, it does not invent an
  invalidation mechanism) and carries the why-not-LVGL decision. The
  fine-grained damage architecture was KILLED by its own review — its draw
  loops iterate the VIEWPORT, so its evidence had no power; §14's focused-window
  content freeze is what survived. One invalidation mechanism, not six.
- `docs/board_ports_2026-08.md` declines a driver registry/ABI and codegen.
- **No store migrations until there are users (2026-09-07).** A store FORMAT
  change ships as strict readers plus a seed version bump: seed content
  re-seeds, a legacy file reads as ABSENT (`None`) until wiped by hand. Three
  one-shot passes were deleted for it (the `.moytext`→`.md` rewrite, the
  `notes.json` notebook, the RLE→deflate picture job) with their sidecars and
  legacy readers. The door a migration would use is `sweep_store`'s
  `prune_retired`, and the bar is its own (generation-gated, one small read
  warm) — the picture pass missed it and cost a Guition a 196-second boot.
- `moybyte_console_plan_2026-07.md` is the current design doc; `docs/history/`
  is history, not direction.

## Working here

```bash
make setup                 # venv + pip install -e '.[dev,sim]' (hermetic)
make test                  # pytest; one file: .venv/bin/python -m pytest tests/x.py -k name
tools/preflight.sh         # what CI runs, in CI's order -- before pushing (--web adds Chrome)
python tools/simulate_desktop.py [--cart system_carts/star_catcher.moy]
tools/board.py ports       # which board is on which port, and who holds it
```

- **Attached boards are test resources, not a permission gate**: drive, flash
  and measure without asking (the `on-glass` skill). Never open a port another
  process holds — `tools/board.py` refuses and names the holder.
- `dev` is where work lands; `master` is what users get. Commit by pathspec
  (`git add <files>`, never `-A`: the checkout is often shared), one commit per
  landed outcome. No model identifiers in commits or files.
- Comments and docs state what IS; the story of a change goes in its commit
  message.
- **Every turn re-reads the whole conversation**, so a session's cost is turns ×
  context. Find code with `grep -n`, then read the range you need; never read a
  file over ~20 KB whole, and never read session transcripts under `~/.claude`.
  Wait for a build or a suite by running it in the background and taking its
  completion notice, never a `sleep`/`until` polling loop.

## Where the rest lives

**Path-scoped rules** (`.claude/rules/`) load when you touch matching files;
**skills** (`.claude/skills/`) load by name or when a task matches them.

| rule | loads with | | skill | for |
|---|---|---|---|---|
| `boards.md` | `firmware/**`, `device/**`, `native/**`, board tools | | `on-glass` | ports, driving, flashing, pushing, screenshots, suites, recovery |
| `shell.md` | `runtime/**` | | `perf` | measuring fps, the meters, the settled levers |
| `rendering.md` | canvas, `ui`/`skin`/`chrome`, `*_layer` | | `issues` | the issue mirror and the ledger bodies |
| `carts.md` | `system_carts/**`, cart API, Lua tier, audio | | `release` | branches, OTA channels, `make release` |
| `web.md` | `firmware/web_runner/**`, sync RPC, webhost | | | |
| `ota.md` | `device/moy_ota.py`, release and signing tools | | | |
| `netplay.md` | `runtime/netplay.py`, `device/moy_espnow.py` | | | |
| `testing.md` | `tests/**` | | | |
| `docs.md` | any `.md` | | | |
