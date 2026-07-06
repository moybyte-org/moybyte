# Moybyte shell UX — the technical implementation plan (v1)

**North star above this doc:** `docs/shell_ux_v1.md` — the LOCKED top-of-stack UX spec.
This doc is the "HOW": the staged, bisectable migration from today's code to that UX.
Its §11 (the ~7-verb OS↔app contract) and §12 (perf guardrails) are binding on every
stage below; where this plan and that spec could be read to disagree, the spec wins.

**Status:** PLAN / STAGED — nothing here is implemented yet. Each stage is one (or a
few) bisectable commits, each shippable and green on all three nets (the 777-test host
suite, the golden-frame pixel harness, the pyflakes undefined-name gate). This doc
contains NO product code; it names what moves, from where (file:line, measured on
branch `refactor/console-layers`), to where, and in what order.

**Companion docs (context, beneath the spec):**
`docs/shell_os_architecture_v1.md` (the syscall/capability boundary + the measured
93-member `ws.*` god-API this migration shrinks), `docs/shell_layers_refactor_v1.md`
(the just-completed Layer decomposition this plan builds on — its surfaces are the
Editor's tabs), `docs/shell_architecture_v1.md` (privileged system carts + layered
compositor). The kid-facing cart contract (`docs/moy_cart_api.md`, realized as
`make_api`) is **frozen** — not one name changes at any stage.

**Issues:** #29 (blocks tab + graduation), #46 (bar → zoned OS shell), #71 (exit model
— this plan RETIRES its pause machinery), #55 (Editor/Settings as privileged apps),
#22/#41 (webview as a WM surface), #58 (P4 — seam only, out of v1), #66 (the perf
regression bar every stage is measured against).

**One-line thesis:** extract the Player first — the `run(cart) → plays → returns`
black box is the single cut that decouples playing from authoring; everything after
(Editor-as-app, zoned bar, exit model, WM, journal, graduation) is a consumer of that
cut, and each lands as its own green, revertible stage so the migration can pause at
any stage without leaving a dual state behind.

---

## 1. The end-state, in code terms

### 1.1 The module map

| Concept (spec §) | Module / object | Built from (today) |
|---|---|---|
| **Kernel** (§3) | `runtime/console.py` — `Workstation`, shrunk to: the compositor/router (`frame` stack loop `console.py:2950`, `_visible_stack` `:1440`), process spawn/kill (the back-stack, replacing the `screen`/`menu_view` strings `:1127`/`:1144`), input routing (`handle_input` `:2536`, `handle_pointer` `:2605`), the Player invocation + exit guarantee (§2 below), the shared draw toolkit (`_glyph` `:3308`, `_icon` `:3331`, `_btn` `:3246`), and store/service attach points (`carts_store`, `wifi`, `updater`, `web_hook`) | `Workstation` today (`console.py:1053`, ~2,295 lines) minus everything the rows below take. The class name stays `Workstation` throughout — the device (`moy_runtime.py:626`) and host (`host_app.py:687`) both construct it and 777 tests reach it; a rename is cosmetic churn and deliberately NOT a stage. |
| **Project workspace** (§6, §7) | `runtime/project.py` — `Project`: the open cart's live data + its persistence verbs | the flat `ws.` attributes `cart`/`config`/`sheet`/`tilemap`/`images`/`pmem` (`console.py:1129-1150`), the builders `_build_sheet`/`_build_pmem`/`_build_tilemap`/`_build_audio` (`:1949-2011`), and the save verbs `_save_config`/`save_sprites`/`save_map`/`save_sounds` + the write half of `save_code` (`:2181-2445`) |
| **Player** (§2) | `runtime/player.py` — `Player`: run a cart under the frozen `make_api`, feed input, present pixels, guarantee exit | `_start` (`console.py:1876`), the cart tick inside `_draw_content_desktop` (`:2863-2937`), the cart key-edge derivation (`:2881-2884`), `_sync_cart_text_mode` (`:2077`), the crash capture (`:2899-2912`), and — until Stage 5 retires them — the pause state (`cart_paused` `:1134`, `_desktop_input` `:2559`, `_desktop_pointer` `:2624`, `_draw_pause_dim`/`_draw_pause_buttons` `:3130`/`:3139`) |
| **Editor app** (§6) | `runtime/editor_app.py` — `EditorApp`: ONE app, opened on a `Project`; its tabs ARE the extracted layers | tab ladder = `CardsLayer` (`cards_layer.py:38`, Config), `BlockEditorUI` (`block_editor_ui.py:230`, Blocks), `CodeLayer` (`code_layer.py:130`, Code), `PaintLayer` (`paint_layer.py:80`, Sprites), `MapEditorUI` (`map_editor_ui.py:112`, Map), `MusicEditorUI` (`music_editor_ui.py:101`, Music); plus the `menu_view` machine it replaces: `set_menu_view` (`console.py:2015`), `_open_menu/_open_paint/_open_map/_open_blocks/_open_music` (`:2094-2136`), `_leave_menu` (`:2158`), `_leave_or_home` (`:2524`) |
| **Launcher app** (§2, §4) | `runtime/launcher_app.py` — `LauncherApp` (the back-stack root; never exits) | `LauncherHomeLayer` + the `Launcher` grid (`launcher_layer.py:179`/`:36`), the cart-management verbs `new_cart`/`dup_cart`/`del_cart` (`console.py:2474-2504`), plus the §4 tap-mode dispatch (new) |
| **Settings app** (§10) | `runtime/settings_app.py` — `SettingsApp` (privileged) | `SettingsLayer` (`settings_layer.py:36`), `ThemeLayer` (`paint_layer.py:273`), `UpdateUI` (`update_ui.py`), `open_settings`/`_exit_settings` (`console.py:1812`/`:1822`) |
| **Window manager** (§3) | `runtime/wm.py` — the ONLY tier-specific layer. S3/host v1: `FullscreenStackWM` (top-of-stack draws fullscreen; owns the game↔system composite `_composite_game` `console.py:2718` + `_viewport`/`_game_xy` `:2696`/`:2711` and the bar-visibility rule). Webview: the draw-command protocol (`runtime/web_view.py` — `DrawRecorder:183`, `RecordingLayer:526`, `TeeCanvas:602`) grows per-app surfaces (Stage 9). P4 windowed WM: seam only, out of v1. | the composite + viewport code named at left, plus the implicit "which content layer is active" rule (`_content_layer` `console.py:1401`) |
| **Zoned bar** (§5) | `runtime/bar_layer.py` — `BarLayer` re-scoped: OS shell (right zone: clock/wifi/batt/context-X/gear) + a LENT left zone the active app fills; hidden while a Player is on top | today's three hardcoded variants in `_draw_status_strip(where)` (`bar_layer.py:121-166`) + the cached cart strip (`_draw_top_bar_cart` `:168`, the #43 strip cache — kept) |
| **Undo journal** (§7) | `runtime/moy_carts.py` — `journal_append`/`journal_undo`/`journal_redo`/`journal_compact`, per-project on SD | new; lives beside `_write_atomic` (`moy_carts.py:78`) and the save verbs (`save_code:422` …), the same `json`+`os`-only discipline |
| **Blocks graduation** (§8) | `EditorApp` + `runtime/blocks.py` + `moy_carts` | today's re-derived guard: `blk_protect` (`block_editor_ui.py:295`), `_cart_has_handwritten_code` (`console.py:2138`), `BLOCK_MARKER`/`is_block_authored_source` (`runtime/blocks.py:644`/`:647`), `graduate_to_code` (`block_editor_ui.py:807`) — becomes a STORED project fact (Stage 8) |

Every new `runtime/*.py` module is (a) added to the firmware staging list
(`firmware/lilygo_t_deck_plus_micropython/build.sh:166-188`) and (b) added to the
pyflakes gate's target list (`tests/test_micropython_spike.py:2777`,
`test_no_undefined_names_in_extracted_modules`) **in the same commit that creates
it** — host == device stays structural, not aspirational.

### 1.2 The ~7-verb contract, mapped

| Verb (spec §11) | Realization |
|---|---|
| OS→app `open(project)` | app construction / `app.open(project)` at spawn — the kernel builds the `Project` (via `moy_carts.load`, `moy_carts.py:299`) and hands it over; the app never touches the store directly |
| OS→app `present(left_zone, canvas)` | the existing Layer protocol's `draw(dt)` (`runtime/layers.py:26`) for the content region, plus a new `draw_zone(cv, rect)` the zoned bar calls for the lent left zone (Stage 4) |
| OS→app `route(input)` | the existing `handle_input(i)` / `handle_pointer(px, py, click)` — already the Layer protocol; unchanged shape |
| OS→app `teardown()` | the existing `on_enter`/`on_leave` lifecycle, extended so an app drops its editors/preview state (today's `reset()` methods: `cards_layer.py:62`, `map_editor_ui.py:143`, `block_editor_ui.py:303`) |
| app→OS `run(project)` | `ws.run(project, caller=app)` — the kernel pushes a Player process; on Player exit the kernel pops back to `caller` (§2 below) |
| app→OS `commit(project)` | `project.commit_*()` — writes through `moy_carts` inside `ws._with_sd` (the SD-session discipline, `console.py:1226`, device wrapper `moy_runtime.py:644`), and (Stage 7) appends a journal entry |
| app→OS `exit` | `ws.exit(app)` — pop the back-stack (the launcher, as root, never pops; spec §9 gives it no X) |

**Where the two API constructors attach — unchanged and new:**

- `make_api` (kid cart ↔ Player, **frozen**): injected exactly as today —
  `host_app.py:689` / `moy_runtime.py:627` set `ws.make_api`; the **Player** becomes
  its only consumer (today it's `Workstation._start`, `console.py:1893`). The Player
  binds the namespace ONCE per run, closing over the raw canvas/input/sheet objects —
  the §12 guardrail's mechanism (see §5).
- `make_system_api` (system apps): constructed by the kernel at spawn and passed to
  the app constructor, per-surface grants transcribed from the measured footprints
  (`shell_os_architecture_v1.md` §5.1). **This plan does not gate on it**: during
  Stages 2-6 the apps keep their `self.ws` back-reference (today's seam,
  `layers.py:44`), and the capability APIs land per-surface on the OS-arch doc's own
  track. The one place this plan forces the issue is the Editor's tabs, which switch
  their *data* access from `ws.sheet`/`ws.cart`/… to the injected `Project` (Stage 3)
  — the single biggest chunk of the 93-member surface, removed by construction.

---

## 2. `run(cart) → returns` on a single-threaded frame loop — the honest shape

The spec's primitive is "plays until exit and returns control to the caller." One
blunt mechanical fact shapes the whole implementation: **the console is one
cooperative frame loop** — `moy_runtime.run_desktop`'s `while True`
(`moy_runtime.py:761`) on device, `ConsoleDriver.frame` (`host_app.py:781`) on host —
so `run()` **cannot literally block**. The primitive is therefore a **stack
discipline, not a blocking call**:

- `ws.run(project, caller)` pushes a `Player` process onto the back-stack and records
  `caller`.
- Each frame the kernel routes `frame`/`handle_input`/`handle_pointer` to the top of
  the stack exactly as the Layer router already does — the Player's `tick(dt)` is
  today's cart branch of `_draw_content_desktop` (`console.py:2869-2921`).
- When the Player exits (Stage 5's hold-BACKSPACE; until then, the existing pause-QUIT
  path `console.py:2641-2646`), the kernel pops it and the **caller is top again, in
  exactly the state it was left** — the Editor on the same tab, the launcher on the
  same page. That pop IS the "return."

The exit guarantee is the Player's one job and is already mostly written: the crash
capture that turns any cart exception into the error panel instead of a hang
(`console.py:2899-2912`), and the error-panel-with-EDIT-reachable policy
(`open()`'s comment, `console.py:1939-1942`), move into the Player verbatim. What is
NEW is that "where do we go on exit" stops being five scattered `self.screen = ...`
writes (`open:1943`, `apply:2431`, `run_code:2263,2267`, `_leave_menu:2179`,
`_exit_settings:1826`) and becomes the single pop-to-caller.

---

## 3. The stages

The heart of the plan. Ordering rule: **the Player cut first** (it decouples the two
halves of the console), then the stages that consume it, cheapest-net-risk first.
Every stage: green on pytest (777) + golden (`/tmp/moy_golden/check_golden.sh`, 9
chrome screens) + pyflakes; one revert restores the previous stage; **the commit that
introduces a replacement deletes what it replaces** — no parallel mechanisms held
open (see §6).

| # | Stage | New/changed modules | Net + rollback |
|---|---|---|---|
| 0 | Baseline measurement | none (procedural) | #66 harness numbers recorded on hardware; golden re-captured at the branch tip |
| 1 | Project workspace extraction | `runtime/project.py` | golden byte-identical; `ws.*` forwards keep every test green; revert = 2 commits |
| 2 | **Player extraction (the pivotal cut)** | `runtime/player.py` | golden byte-identical; #66 re-measured on hardware BEFORE proceeding; revert = 2-3 commits |
| 3 | Editor as an app; tabs = the extracted layers; tap-mode setting | `runtime/editor_app.py` | golden identical for unchanged screens; navigation tests edited in-commit (deliberate semantics change, called out) |
| 4 | Zoned bar (OS shell + lent left zone) | `runtime/bar_layer.py` reshaped | golden re-captured for bar rows only; strip cache proven by `test_top_bar` redraw counts |
| 5 | Exit model (X + hold-BACKSPACE); pause machinery retired | `player.py`, `bar_layer.py`, `console.py` deletions | pause goldens deleted (spec §9); #66 re-check (input path touched) |
| 6 | WM formalization (S3 fullscreen back-stack) | `runtime/wm.py`; `screen`/`menu_view` strings deleted | golden byte-identical (pure mechanism flip); the largest test-churn stage |
| 7 | Undo/redo journal | `runtime/moy_carts.py` | new unit tests (journal walk, torn-tail recovery, cap); golden untouched |
| 8 | Blocks↔code graduation | `editor_app.py`, `blocks.py`, `moy_carts.py`, manifest | new unit tests over the graduation matrix; #29 flows re-tested |
| 9 | Webview per-app WM surface (+ the P4 seam, named only) | `web_view.py`, `moy_webserver.py` | `test_moy_webserver` round-trip; device fps ceiling re-measured with WEB VIEW on |

### Stage 0 — baseline (no code)

Record the #66 ledger numbers on hardware at the branch tip (Sakura / Hop Quest /
Sky Run / Battle City, PERF DIAG ON), re-capture the golden set, confirm 777 green.
These are the regression bars for Stages 2, 5, and 9. Update #66's body if the
snapshot moved, then `make sync-issues`.

### Stage 1 — the Project workspace (the Player's enabling commit)

**What "the project workspace" is as an object:** `Project` = the open cart's live
state — the `cart` dict (src/cfg/sprites/map/sounds/blocks/path, as loaded by
`moy_carts.load`), the live `config` dict, the built `SpriteSheet`, `TileMap`,
`AudioBank`, `Pmem`, and `images` — plus the persistence verbs (`commit_config`,
`commit_code`, `commit_sprites`, `commit_map`, `commit_sounds`, `commit_blocks`) that
write through the injected store + `_with_sd`. It is the one object a tab edits and
the one object the Player runs. It is NOT a copy: the editors and a re-run share the
same live `sheet`/`tilemap`/bank exactly as today (edits are visible to the running
cart via `gen` bumps — `editors.py:224` — that behavior is load-bearing and kept).

**Moves (two commits):**
1. The builders `_build_sheet`/`_build_pmem`/`_build_tilemap`/`_build_audio`
   (`console.py:1949-2011`) and the attributes `cart`/`config`/`sheet`/`tilemap`/
   `images`/`pmem` (`:1129-1150`) become `Project` fields; `Workstation.open()`
   (`:1918`) constructs `self.project`; `ws.sheet` etc. become one-line **forwarding
   properties** so all eleven surface files and every test keep working unmodified.
2. The save verbs (`_save_config:2436`, `save_sprites:2269`, `save_map:2320`,
   `save_sounds:2340`, and the store-write half of `save_code:2181` — the
   compile-check/UI half stays with the code surface) move to `Project.commit_*`;
   the `ws.save_*` names stay as forwards (they are tested surface).

**Net:** golden byte-identical (no draw change); pytest green with zero test edits
(the forwards guarantee it). **Rollback:** revert both commits; nothing else moved.

### Stage 2 — extract the Player (the pivotal first move)

**What comes out of `Workstation` into `runtime/player.py`:**

- `_start` (`console.py:1876-1916`) → `Player.start(project)`: reset canvas state,
  stamp the cart clock (`_cart_start_ms`), gate `wifi` by the manifest permission
  (`_cart_has_perm`, `:1464`), call the injected `make_api`, `exec` the source,
  capture `_update`/`_draw`, convert any exception into `cart_error`/`crash_line`.
- The cart tick inside `_draw_content_desktop` (`:2869-2921`) → `Player.tick(dt)`:
  key-edge derivation (`cart_key`/`cart_keyp`), `_update(dt)`/`_draw()`/`audio.tick`,
  the per-frame perf split fills (`_pf_upd`/`_pf_cart`/`_pf_audio` — the DRAWBRK
  contract stays on `ws`, the Player writes into it as the layer does today),
  crash capture, `_sync_cart_text_mode` (`:2077`), `_reset_canvas_state`.
- The desktop input/pointer slices `_desktop_input` (`:2559`) and `_desktop_pointer`
  (`:2624`) → `Player.handle_input`/`handle_pointer` — **including, temporarily, the
  whole #71 pause machinery** (`cart_paused`, `_bks_prev`, `_draw_pause_dim`,
  `_draw_pause_buttons`, the `_PAUSE_QUIT_BTN` hit test). Pause is retired in Stage 5;
  moving it wholesale first keeps Stage 2 pixel-identical.
- The error panel `_draw_error_panel` (`:3280`) draws under the Player's ownership
  (crash chrome is the Player's UX, per spec §2's "guarantees the cart will exit").

**What replaces the in-Workstation mode switch:** the `"desktop"` entry of
`_content_layers` (`console.py:1376`) becomes a thin adapter over `ws.player`;
`ws.run(project, caller)` sets it active and records the caller; the five scattered
`self.screen = "desktop"` writes become `ws.run(...)` calls; QUIT's `go_home()`
(`:2646`) becomes the pop-to-caller. Because the only caller today is
launcher-shaped, behavior is unchanged — the Editor becomes the second caller in
Stage 3, *proving* the decoupling (Editor→PLAY→exit→same tab with zero Player
knowledge of editors).

**The bundle the Player receives:** the `Project` (Stage 1), the raw canvas, the
input, the audio factory. It does NOT receive the store, the bar, the launcher, the
layouts — grep-enforceable by the pyflakes gate plus a new spike-test grep asserting
`player.py` never names `menu_view`/`launcher`/`bar_layer`/`carts_store`.

**Net:** golden byte-identical (pause frame, crash panel, FPS chip all draw the same
pixels from new homes). **Then stop and measure:** the #66 harness on hardware, all
four ledger carts, compared to Stage 0. The hot path moved files; §5's analysis says
the cost is zero, but the ledger — not the analysis — is the gate. **Rollback:**
revert the stage's commits; Stage 1's `Project` stands alone and stays.

### Stage 3 — the Editor as an app whose tabs ARE the extracted layers

`EditorApp` owns: the tab ladder `Config → Blocks → Code → Sprites → Map → Music`
(+ PLAY), the current-tab state (replacing `menu_view`, `console.py:1144`), the lazy
tab builders (the bodies of `set_menu_view`, `:2015-2055` — CodeEditor/PaintEditor/
map/blocks/music builds move in unchanged), the text-mode flip (`_set_text_mode(view
== "code")`, `:2052`), and the leave semantics (`_leave_menu`, `:2158`).

**How a tab gets the workspace:** the existing layer/UI instances are constructed
with `(project, api)` instead of reaching `ws.sheet`/`ws.cart`/`ws.config` — Stage 1
made those reads forwards, so this rewiring is mechanical per tab (one tab per
commit: cards → code → paint → map → music → blocks, mirroring the layers refactor's
one-surface-per-commit discipline). Shared UI plumbing (`_glyph`, layouts, pointer)
stays reached through the seam the OS-arch doc will later scope.

**Config-first landing (spec §4/§6):** `EditorApp.open(project)` lands on Config —
today's `_open_menu` rule (`console.py:2094-2098`: cards if the cart has an `edit`
schema, else code) is kept as the fallback for schema-less carts.

**PLAY = `commit(); run(current)`:** the PLAY affordance calls the dirty tabs'
`commit_*` then `ws.run(project, caller=self)`; on pop, the Editor is on the tab it
left. This REPLACES today's implicit run-on-close (`_leave_menu`'s re-`_start`,
`:2168-2178`, and `run_code`'s screen flip, `:2253`) — a deliberate, spec'd
navigation-semantics change; the tests that assert `screen == "menu"`/`"desktop"`
transitions are edited in the same commits, and that edit list is the commit
message's honesty section.

**The §4 tap-mode setting** rides this stage (it needs the Editor to exist): a
`system.json` key (`tap_mode: "maker" | "player"`, default maker), one Settings row
(`settings_layer.py:80` `_settings_rows`), and the launcher tap dispatch: maker →
spawn `EditorApp(project)`; player → `ws.run(project, caller=launcher)`. Both verbs
exist on every cart's long-press/menu regardless of mode.

### Stage 4 — the zoned bar

`BarLayer` splits `_draw_status_strip(where)`'s three hardcoded variants
(`bar_layer.py:121-166`) into: **right zone** (OS-owned: clock/wifi/batt + gear +
the Stage-5 context X), drawn by the bar itself; **left zone**, a rect LENT to the
active app via `draw_zone(cv, rect)` + a tap slice routed to the app. The launcher
lends new/dup/del (today's `where == "home"` branch); the Editor lends the tab
ladder + PLAY (replacing the pause-only tool switcher, `_draw_top_bar_cart`
`:168`); Settings lends its sections. **The #43 strip cache is kept and generalized:**
the cached strip's key (`_cart_bar_key`, `:209`) grows the active app + its zone
generation, so the bar remains a one-blit chrome cost; `test_top_bar`'s redraw-count
assertions are the net. **The bar hides while a Player is on top** — the rule that is
today implicit in "bar only draws in pause/crash" (`console.py:2924-2935`) becomes
the explicit WM visibility rule, same pixels.

### Stage 5 — the exit model; #71's machinery retires

- **Taskbar apps exit by tap:** the X in the right zone pops the stack (`ws.exit`);
  the launcher draws no X (root). BACKSPACE becomes a plain key in every taskbar app
  — the text-mode carve-out ("a TOOL keeps backspace as delete") and the
  `_bks_prev` edge-detect (`console.py:1138`, `:2580-2587`) are **deleted**.
- **The fullscreen Player exits on hold-BACKSPACE:** quick tap reaches the cart as a
  key; a sustained hold (~700ms, with a small on-screen hold-progress affordance so
  it is discoverable and never accidental) pops to the caller. Wiring: raw-matrix
  mode already streams held keys (`d4&0x08` — the reason game mode exists), so
  `input.held("home")` + a threshold is the whole device mechanism; the host maps a
  held BACKSPACE identically; the web page's ☰ maps server-side to a synthesized
  hold-exit event.
- **Retired in this stage's commits:** `cart_paused` + the pause toggle
  (`_desktop_input`, `console.py:2559-2601`), `_draw_pause_dim`/`_draw_pause_buttons`
  (`:3130-3153`), `_PAUSE_QUIT_BTN`/`_PAUSE_CONTINUE_BTN` (`:428`), the pause branch
  of `_desktop_pointer` (`:2634-2654`), and the pause-gated `_animating` rule
  (`:2820-2825`). The pause goldens are deleted, not re-captured — the spec removed
  the surface.
- **The known edge, on the record (spec §9 accepted it):** on old keyboard firmware
  (`_raw_unsupported` sessions) the ASCII latch fakes a hold for only
  `KEY_HOLD_MS = 260ms` (`modules/moybyte/input.py:59`) — a 700ms hold is
  undetectable. Fallback for those sessions: the latch window is extended for the
  console key specifically, or a triple-tap-within-1s alias; which one is an open
  question (§7) to settle on hardware, and old-fw devices are the dev units only.

### Stage 6 — WM formalization (S3 first)

The flip from strings to the stack: `runtime/wm.py`'s `FullscreenStackWM` owns the
back-stack (launcher root → spawned app → Player), the game↔system composite
(`_composite_game` moves in), viewport mapping (`_game_xy`), and bar visibility. The
`screen`/`menu_view` strings and `_content_layer`'s registry lookup
(`console.py:1401`) are **deleted in the same commit** — after Stages 2-5 every
screen is already an app/Player behind an adapter, so this stage is a mechanism swap
with byte-identical goldens, but it is the largest test-churn commit (everything that
asserts `ws.screen` — grep says dozens of call sites across `tests/`) and is
sequenced late deliberately, when the strings have the fewest remaining readers.
Webview note: `web.install()` swaps `ws.canvas` (`moy_runtime.py:690-693`) — the WM
owns that rebinding hook so the Tee keeps intercepting whichever process draws.

### Stage 7 — the undo/redo journal (spec §7: Save is invisible, Undo is durable)

**On-disk shape** (per project, beside its files — `<cart>.moy/journal/`):
- `journal.jsonl` — append-only, one JSON line per commit event:
  `{"seq": N, "ts": ..., "file": "main.py", "snap": "s/000N-main.py"}` — the entry
  points at a **full-file snapshot** stored under `journal/s/`. Full snapshots, not
  diffs: MicroPython-safe (no difflib), corruption-isolated (one bad snapshot loses
  one step), and the files are small (measured: `main.py` 5.3KB,
  `sprites.moygfx` 16.5KB for the biggest seed cart, `system_carts/star_catcher.moy`).
- `cursor.json` — `{"seq": N}`, the undo position, written via `_write_atomic`
  (`moy_carts.py:78`).

**The walk:** undo = copy snapshot `seq-1` of that file over the live file (through
the same atomic writer + `_with_sd` session as every save) and step the cursor; redo
= step forward. A NEW commit while the cursor is rewound truncates the redo tail
(the Google-Docs rule). **Reboot survival is free:** journal + cursor are SD files.
**Torn-write recovery:** JSONL's virtue — a torn last line fails `json.loads` and is
dropped at load; the cursor is atomic.

**Granularity, stated honestly:** a durable step = one `commit` — a tab-leave, PLAY,
or the invisible autosave debounce — NOT one keystroke. Finer in-session undo stays
in-RAM where editors already have it (paint's stroke revert `map_editor_ui.py:293`
pattern; CodeEditor may grow an in-RAM edit stack later, out of this plan). This
satisfies §7's "walks back a mistake one change at a time, including after
power-off" at commit granularity; the spec paragraph owns the guarantee, this plan
owns the cost call.

**Size/rotation policy:** per-project cap of 64 entries or 512KB, whichever first;
compaction drops the oldest entries + their snapshots. Worst-case sprite-heavy
projects: 64 × 16.5KB ≈ 1MB per project — trivial on a multi-GB SD. Write cost: one
extra file write per commit, same size class as the save itself, in the same
between-frames `_with_sd` session (the #56/#40 SD discipline is untouched); a commit
roughly doubles its SD time and commits happen at human cadence, not frame cadence.

### Stage 8 — blocks↔code graduation (spec §8, the MakeCode model)

Today the "can't blockify" state is **re-derived** every open from a heuristic
(`blk_protect`: no blocks.json AND `_cart_has_handwritten_code`,
`block_editor_ui.py:295`). The spec's graduation is a **stored, one-way project
fact**:

- `manifest.json` grows `"graduated": true`, set at the moment a code commit's source
  stops round-tripping. Detection is **content-based, not marker-based** (a kid can
  edit code while leaving the `BLOCK_MARKER` line intact): on each code commit of a
  block-authored project, recompile `blocks.json` via the existing compiler
  (`runtime/blocks.py`, `compile_blocks`) and compare normalized output to the
  committed source; differ → graduated.
- On graduation: `blocks.json` is left frozen (the last-good program — the read-only
  render source), the Blocks tab renders it read-only with the celebration banner
  ("you've leveled up to code"), and SAVE/regenerate are disabled — extending the
  exact refuse-to-overwrite behavior `graduate_to_code`/`blk_protect` already
  implement.
- **Un-graduation happens only through Stage 7:** the journal entry for the
  graduating commit records the flag flip, so undoing past it restores both the
  source and `"graduated": false` — the one honest back-door the spec allows.
- Edge cases in the test matrix: template-only carts (stay blockifiable —
  `_cart_has_handwritten_code`'s existing rule), marker kept + code edited
  (graduates), code edited then hand-reverted byte-identical (does NOT graduate —
  content comparison says round-trip holds), a code-only cart that never had blocks
  (never "graduates"; it simply has no block program — today's protected mode
  becomes "empty blocks tab offering to start").

### Stage 9 — webview as a WM surface (and the P4 seam, named only)

The webview already speaks a per-draw command protocol and has per-layer recording
(`RecordingLayer`, `web_view.py:526`); this stage moves it from "one flattened frame"
to "one stream per WM surface" (bar / app content / Player viewport), which is
exactly the #73 retained-layer shape the browser can composite — and the browser
page becomes the second window manager (spec §3's tier table). Off by default, the
Tee-swap discipline unchanged (`moy_webserver.py` — zero per-draw cost when OFF).
The P4 windowed WM is **not built**: this stage only proves the WM interface has two
implementations (S3 stack + browser), which is the seam the P4 work will slot into.

---

## 4. The first three commits (what an implementer types Monday)

1. **`refactor(shell): extract the Project workspace (Stage 1a)`** — create
   `runtime/project.py` (`Project` + the four builders moved verbatim from
   `console.py:1949-2011`); `Workstation.open()` builds `self.project`; add
   forwarding properties for `cart`/`config`/`sheet`/`tilemap`/`images`/`pmem`; stage
   the module in `build.sh`; add it to the pyflakes list. Nets: 777 green untouched,
   golden identical.
2. **`refactor(shell): move the save verbs onto Project (Stage 1b)`** — `commit_*`
   verbs on `Project` (bodies from `console.py:2269-2445` + the write half of
   `save_code`); `ws.save_*` become forwards. Nets: same bar.
3. **`refactor(shell): extract the Player -- start + tick (Stage 2a)`** — create
   `runtime/player.py`; move `_start` and the cart-tick body of
   `_draw_content_desktop`; the `"desktop"` content layer delegates to `ws.player`;
   `ws._start` stays a forward (tested surface). Nets: 777 + golden + pyflakes, and
   the new no-forbidden-names grep for `player.py`.

---

## 5. Perf guardrails, made concrete (binding — spec §12)

1. **The hot per-frame draw path stays injected-direct.** The hot calls are: the cart
   namespace's draw verbs — closures built ONCE per run by `make_api`
   (`host_app.py:362` / `moy_runtime.py:57`) over the **raw canvas object**
   (`DeviceCanvas`, `device_canvas.py:141`) — specifically the per-tile/per-sprite
   loops (`spr` → `canvas.spr_tile` auto-batch, `map_` → one native `blit_map`,
   `spr_batch` → one native call) and the per-glyph text kernel. The Player extraction
   moves WHO calls `make_api`, never WHAT it closes over: the Player hands `make_api`
   the same raw canvas reference `Workstation._start` hands it today. **Forbidden by
   this plan:** wrapping the canvas or the cart namespace in any per-call
   boundary/validating object — that re-adds the #43/#63 dispatch tax. The one
   sanctioned canvas indirection remains the webview's opt-in Tee swap (OFF by
   default, `moy_runtime.py:685-693`). Chrome-side: the shared toolkit's per-glyph
   loops (`_glyph` `console.py:3308`) and the bar keep their caches (the #43 strip,
   the #66 clock cache); the Stage-1 forwarding properties are UI-cadence
   conveniences and the standing code style — hoist to a local before any loop —
   keeps them out of inner loops (they never appear in the cart path at all, which
   closes over the objects at start).
2. **The bar hides during play** — preserved at every stage: today's rule
   (`console.py:2924-2935`), Stage 4's explicit WM visibility rule after. A playing
   cart owns 320×240 and the whole frame budget; the Player draws no chrome except
   the crash panel and (until Stage 5) the pause frame — both non-play states.
3. **No per-frame events.** The stages above introduce NO event bus; the kernel's
   notifications (app spawned/exited, theme changed) are direct verbs at transition
   time. If the OS-arch doc's §5.2 bus lands during this migration, its vocabulary
   stays lifecycle/theme/navigation — nothing per-frame or per-draw.
4. **The failure bar is #66, measured, not argued:** the ledger's four carts on
   hardware at Stage 0 (baseline), after Stage 2 (the Player cut — the mandatory
   gate before Stage 3 starts), after Stage 5 (input path), and after Stage 9
   (webview ON). Any regression vs the ledger snapshot fails the stage regardless of
   how clean the boundary is; #66's body is updated (then `make sync-issues`) each
   time.

---

## 6. Avoiding the half-migrated stall (the arch review's warned failure mode)

The review's nightmare: a long-lived dual state — two routers, two notions of "the
active screen," surfaces half on `ws` and half on APIs — that the team lives inside
for months. The staging above is shaped specifically against it:

- **One router at all times.** From Stage 2 through Stage 5 the `Workstation`
  string-keyed router remains THE router and the new objects (Player, EditorApp) are
  its delegates behind the existing content-layer seam. The back-stack becomes the
  router only in Stage 6, in the single commit that also deletes the strings. There
  is never a frame where both mechanisms route.
- **Replace-and-delete in the same commit.** Every stage's new mechanism lands with
  the deletion of what it replaced (Stage 2 deletes the cart branch from
  `_draw_content_desktop`; Stage 5 deletes the pause machinery; Stage 6 deletes the
  strings). Forward shims (`ws.sheet`, `ws.save_*`, `ws._start`) are one-liners kept
  deliberately as tested surface, not parallel implementations — and they are listed,
  so the day the OS-arch capability APIs land, the shim list is the removal list.
- **Each stage stands alone.** If the effort pauses after Stage 2, the codebase is
  strictly better (a testable Player, a Project object) with zero dead scaffolding;
  same for every later stage. No stage depends on a future stage to make sense —
  that is the same property that let the layers refactor land nine extractions
  without a stall.

---

## 7. Risks and open questions, blunt

- **The Player cut touches the hottest code in the repo.** `_draw_content_desktop`'s
  cart tick is the #66-critical path. Mitigations: golden identity + the mandatory
  hardware measurement gate after Stage 2 (§5.4) BEFORE any further stage; the
  analysis says zero added dispatch (same closures, same objects, one extra attribute
  hop at frame level, not per draw), but the ledger decides.
- **Hold-BACKSPACE on old keyboard firmware** can't be sensed past 260ms (the ASCII
  latch, `input.py:59`). Open question: extend the latch for the console key vs a
  triple-tap alias on `_raw_unsupported` sessions. Settle on hardware in Stage 5;
  affected devices are dev units (raw mode ships since fw 2025-06-12).
- **Losing pause.** Retiring #71's pause screen (per the LOCKED spec) removes the
  kid's "freeze the game" affordance — exit-and-relaunch restarts the cart. `pmem`
  covers saves, and a cart can implement its own pause; still, this is a real UX
  subtraction the spec chose and the plan implements. If it stings on hardware, the
  fix is a spec conversation, not a plan hack.
- **The undo journal's SD bill.** ~2×-per-commit write time and up to ~1MB/project.
  Bounded by the cap policy; the real risk is write latency between frames on a slow
  card inside `with_sd_live` — measure a worst-case (16.5KB sprite commit + journal
  append) on hardware in Stage 7 before declaring the debounce cadence.
- **Graduation false-positives/negatives.** Content comparison needs a normalization
  (whitespace/comments) that is honest on MicroPython; a too-eager graduate locks a
  kid out of blocks over a stray newline. Mitigation: the Stage 8 matrix + the
  journal back-door; ship conservative (graduate only on clearly-divergent code).
- **Test churn concentrated in Stages 3 and 6.** Deliberate semantics changes
  (run-on-close → explicit PLAY; `ws.screen` → the stack) rewrite navigation
  assertions. The rule: test edits land in the same commit as the behavior change,
  enumerated in the commit message; golden screens only change where the spec
  changed the pixels (pause deleted, bar re-zoned) — every other screen stays
  byte-identical through all nine stages.
- **Webview/DRAM coexistence stays unverified on hardware** (#38/#40) — Stage 9
  inherits that standing risk; it is not made worse by this plan, and Stage 9 is
  last partly for that reason.
- **What stays on the S3 vs moves out:** everything in Stages 0-8 ships on the S3
  (and the host, same code). The webview gets the per-surface stream (Stage 9); the
  P4 gets a named seam and nothing else.

## 8. Explicitly OUT of scope for v1

- **True multi-window on the S3 — never** (spec §3: the fullscreen back-stack IS the
  perf gate). **The P4 windowed WM** — a later consumer of Stage 6's interface.
- **Per-keystroke durable undo** — durable steps are commits (§3 Stage 7).
- **The full `make_system_api` migration + event bus** — the OS-arch doc's own
  track; this plan only removes the tabs' data reach-through (Stage 3) and leaves
  per-surface grants to land surface-by-surface behind the same nets.
- **Any kid-facing API change** — `make_api` / `docs/moy_cart_api.md` frozen, every
  stage.
- **Concurrent multi-cart execution** — one Player at a time on the stack
  (`shell_architecture_v1.md` §3.4's ceiling stands).

## 9. Relationship to the other docs

The stack, top to bottom: `shell_ux_v1.md` (the locked WHAT) → **this doc (the
committed HOW/WHEN)** → `shell_os_architecture_v1.md` (the boundary mechanism this
plan's shim-removal converges toward), `shell_layers_refactor_v1.md` (the completed
predecessor whose Layer objects are this plan's raw material), and
`shell_architecture_v1.md` (system carts + layered compositor — the endgame Stages 6
and 9 make reachable). The nets, the one-surface-per-commit discipline, and the
replace-and-delete rule are inherited from the layers refactor on purpose: they are
the house method that has already landed a core-loop reshape without a stall.
