---
paths:
  - "runtime/**"
---

<!-- The shell kernel and the invariants its refactors left. -->

### Host == device: the shared console (important)

The console UI is **one codebase** that both the host simulator and the
device run — they render the *same* 320×240 pixels with the *same* petme128 font.
The canonical sources live in `runtime/`; `build.sh` **stages copies into the
firmware `modules/` tree**, re-staged every build and gitignored, so the device
freezes the identical code. The shipped **2026-07 shell** (spec
`docs/shell_ux_v1.md`) is everything-is-a-process: two concerns — authoring and
playing — joined by one primitive, `run(cart)` plays until exit and returns control
to whoever called it.

- `runtime/console.py` — the shell **kernel**: `Workstation`, shrunk by the 2026-07
  refactor to a compositor/router — the Layer-stack frame/input/pointer loop, the
  shared draw toolkit (`_glyph`/`_icon`/`_btn`), store/service attach points
  (`carts_store`/`wifi`/`updater`) and the spawn/exit verbs; everything
  the user sees is an app it runs. Backend-agnostic: injected `make_api` + cart
  store. (frozen as `console`)
- **The 2026-07 shell split** — `project.py` / `player.py` / `editor_app.py` /
  `wm.py` / `wm_windowed.py`. `runtime/README.md` says what each file holds and
  `docs/shell_ux_v1.md` is the UX authority; what neither can tell you:
  - **`Player` has zero knowledge of who launched it.** `run(cart)` plays until
    exit and returns to the CALLER — launcher→launcher, Editor-PLAY→the same tab.
    A crash becomes the **crash-to-code throw**: the run exits into the Editor's
    Code tab with the caret on the crashing line. The parked OOPS panel survives
    only as the no-open-cart fallback.
  - **There is NO SAVE and no dirty star** (#111). Commits ride a typing-idle
    debounce plus every exit path; a commit also appends the undo journal and runs
    graduation detection.
  - **All seven Editor tabs are `_base`-verbatim byte-identical at 320×240/1×**, so
    the Editor reflows to any panel or window size — **only a RUNNING CART is
    fixed at 320×240**.
  - **`ws.screen` is a read-only PROJECTION of the WM back-stack top**, not state.
    The visible/draw stack is memoized (zero per-frame list churn, #66).
  - **`WindowedWM` is host/P4 only and deliberately NOT staged to the S3.**
    Windows exist ONLY above the `"desk"` stack kind, so every `not _order`
    deferral presents fullscreen.
  - **Input focus is decoupled from the back-stack**: clicking a window or its chip
    moves keyboard focus WITHOUT popping, so a playtest keeps ticking while the
    Editor beside it is typed in — its pointer feed click-stripped so the
    background cart never eats editor taps. Only an explicit exit ends a run.
- **Editor-as-an-app UX (this replaced the maker/player tap-mode):** a launcher tap
  **always RUNS the cart** — no mode, no type dispatch. The pinned **"Make ✏️"
  tile** opens the Editor **project-picker** (the same grid over every editable cart
  + a ＋New tile), which owns project management (＋New / Copy / Delete — delete is
  a two-tap confirm); the launcher home has no new/dup/del. **Wallpapers are
  backdrop-only**: excluded from the run-grid, chosen in the **Appearance app**
  (wallpaper + panel theme — the ONE appearance surface; Settings' APPEARANCE
  action row deep-links to it, the old WALLPAPER/THEME stepper rows are gone),
  still editable via the picker.
- **The zoned top bar (#46, macOS-menu-bar model):** one OS-owned 18px bar. RIGHT
  zone = OS status (clock/wifi/batt/≡ + a **context-X** that exits the active app;
  the launcher root draws no X). LEFT zone = LENT to the active app (`draw_zone`):
  the launcher shows the selected cart's name, the Editor its PROJECTS/tab-ladder/
  UNDO/REDO/PLAY icons. Icons stay 16×16 sprites from the editable `IconSheet`
  (Settings → EDIT ICONS). **The bar hides entirely while a GAME plays** (the cart
  owns all 320×240); tools/apps run WITH a minimal bar (title + status + X) so
  they're always exitable.
- **Exit model (#71's pause machinery is retired):** a fullscreen GAME exits on a
  sustained **hold-BACKSPACE (~700ms)** — raw-matrix mode streams the held key, a
  transient progress toast fills, the pop returns to the caller; a quick tap is a
  plain cart key. Taskbar tools/apps exit via the context-X, so BACKSPACE stays an
  ordinary key there (the wifi password field's delete works with zero
  special-casing). A `textmode(True)` game provides its OWN exit via the additive
  cart verb **`quit()`** (in text mode BACKSPACE arrives as a typed delete and the
  keyboard has no autorepeat, so the console's gesture can't reach it) — Letter
  Blitz models it with a tap-✕. (The plan's triple-tap alias was dropped after
  on-device testing.)
- `runtime/editors.py` — `CodeEditor` / `SpriteSheet` / `PaintEditor` cores, plus
  `IconSheet` (16×16 themeable system-bar icon tiles; Settings → EDIT ICONS repaints it). (frozen as `editors`)
- **`runtime/moy_carts.py` is the `.moy` store** (and the #108 user-files layer
  beside it, and the per-project undo journal). The file lists its verbs; the
  decisions behind it:
  - **No SAVE button, no dirty star, ever** (#111). Commits fire on a typing-idle
    debounce and on EVERY exit path.
  - **Undo is scoped to the active tab's file(s), never another tab's.** The one
    bar UNDO/REDO pair walks fine-grained in-RAM ops first, then whole commits.
  - **Blocks↔code graduation is one-way and reversible only by undo**: a diverging
    code commit stores `"graduated": true`, the Blocks tab goes read-only, and
    undoing past that commit un-graduates.
  - **Wallpaper previews keep a sidecar; cover thumbs DO NOT** (#155) — and the
    contrast is the point. A computed preview FRAME is far dearer to rebuild than
    to read, so it caches to disk; a cover's RLE decode got ~three orders of
    magnitude cheaper, so a per-size sidecar cost the same as rebuilding while
    also charging a write per cover per size. Covers cache PARSED RUNS in RAM
    instead. **Do not re-add cover thumbs.**
  - **`files/trash/` is restorable and never confirms**, and WALL/GAME/wallpaper
    are **copy-on-use** — a kid's drawing is never mutated by being used
    (#108's comments hold that design discussion).
- **Dual cart runtimes (#67): a manifest `"runtime": "lua"` routes `Player.start`
  through `ws.lua_runtime`.** `docs/moycore_direction.md` is the direction doc and
  `native/moycore/` the implementation; the decisions and traps:
  - **There is exactly ONE Lua runtime and no chooser.** The old trampoline engine
    (`LuaCartRun`, `bind_draw`, the `spr_gate` batch protocol, `moy_lua_glue`) is
    DELETED and `import moy_lua` is meant to fail. Read the deletion commit before
    proposing to bring any of it back: it was kept as a fallback long after it
    should have been, and while it was there it silently ran every layer cart.
  - **What moycore registers on top of libmoy is a DENY list, not an allow list**
    (`runtime/lua_ext.py`, ONE definition every runtime imports). An allow list
    silently drops any moybyte verb nobody remembered to add — and it did.
    Object-valued verbs (`make_layer`/`draw_layer`/`image`) can never be registry
    entries: a trampoline marshals scalars and a Layer comes back nil, so they ride
    int handles plus a Lua prelude. **If you add a runtime, import that module; if
    you add an object-valued verb, it goes there, not in a verb list.** It was two
    copies once, which is why layer carts crashed on the host and merely fell back
    on device.
  - **`LUA_32BITS` is DECIDED AND ON, on every tier including the host.** Both
    boards' FPUs are single-precision, so doubles would be soft-float; since the
    host binding builds it too, float semantics and integer wrap are identical
    everywhere. (This line claimed the decision was still open for weeks after it
    was made, and a perf hunt spent its last lead re-proposing it.)
  - **The Lua allocator is internal-SRAM-first with a headroom floor and a PSRAM
    fallback** — the all-PSRAM version measured ~2× slower on the S3's OCT bus.
    `-O2` is the AFFIRMED setting on both boards: `-O3` on the VM measured a
    regression on the P4 and null on the S3, so the in-source pragmas PIN it
    rather than merely inheriting it.
  - **Three things were missing when moycore shipped**, each of which would have
    read as "moycore made the cart slower" with nothing pointing at a cause: the
    p8 shim's masked map walk (**the shim nil-guards those names, so losing them
    is SILENT**), the SRAM-floor knob, and a **seeded `rnd`** — libmoy's xorshift
    treats a zero seed as a fixed constant, so every run of every cart drew the
    same sequence.
  - **`tests/test_semantic_traces.py` is the semantic PIN** — twin Python/Lua
    carts, scripted input, hash/log/audio-order/pmem compared through the real
    glue. Run it before crossing anything further, and extend its trace vocabulary
    FIRST. The first time it drove moycore it caught a real divergence: libmoy's
    `camera` returned nothing where every other implementation returns the PREVIOUS
    offset, so `local px, py = camera(x, y)` read nil.
  - **A brand-new project has no sheet and no map, and `moy_console` holds both by
    POINTER** — `spr(0,0,0)` in an empty cart used to segfault libmoy's binding: a
    board reset with no message.
- **pmem persistence is DEFERRED (#66, on-glass 2026-07-14):** `pmem(i, v)` is
  RAM + a dirty mark; `Pmem.flush()` persists at cart exit (`release_world`),
  the crash capture, the workspace swap, and a periodic frame-boundary save
  (`player.PMEM_FLUSH_MS`, 60s). The old per-write SD save was Letter Blitz's per-pop "word-event logic spike"
  (probe-attributed on glass; #66). The
  perf_capture-gated `PMEM save=<ms>` diag line shows the deferred cadence.
- `runtime/font.py` — petme128 8×8 font, the ONE glyph source both backends rasterize (#62): the host draws it per-pixel, the device passes its blob to the native `moy_gfx.text` kernel (staged as `moy_font` at build; framebuf.text — same glyphs, no clip rect — is the no-gfx fallback).
- **UI scrolling is kinetic + scroll-as-blit (#113, 2026-07-22 — the living plan/status issue):** `ui.ScrollRegion` owns the fling physics (all dt INJECTED from the loop — never a clock — so tests are exact-trajectory deterministic) plus a painted-frame ring; an eligible drag/fling frame SHIFTS the retained pixels via the `scroll_rect` system verb (ONE implementation on every tier since the canvas flip — `DeviceCanvas.scroll_rect` over `moy_gfx.scroll_rect`, which the host reaches through `runtime/gfx_binding.py`; the old host `canvas.py` lane is deleted) and repaints only the exposed band (`Launcher.draw_shift` — the home shelf + Editor picker pilots; Settings still row-snaps, its pixel-smooth conversion is #113 Phase 5). The learned rule: **everything inside a scrolled band must be a pure function of the offset** (the picker's dots now ride the scroll in-band). The ring pins sel/statics/`ws._cover_gen` and measures against `RETAINED_FRAMES` paints back (host/layers 1, device root ping-pong 2). Web transport: the `scr` op shifts the browser's retained buffer (never deduped to `{"same":1}` — replaying a shift double-applies), covers + the static wallpaper composite ship ONCE via `/assets` (`ws.cover_assets`, serial names), and the windowed WM's gesture-vs-window checks resolve by IDENTITY (`_wins.get(key) is win` — the shared "make" group's `win.kind` is the CONTENT kind, so `key == win.kind` never matched and silently disabled the drag content-freeze/stamp-defer everywhere).
- `runtime/host_app.py` — host glue: host `make_api`, `build_workstation()` (injects `ws.lua_runtime` when the native binding builds, #67), `ConsoleDriver` (mouse=touch, arrows=trackball). Not on device.

(The pre-unification host UI — `shell.py`/`workstation.py`/`engine.py`/`api.py`/
`cartridge.py` — was removed once the shared console replaced it; issue #17.)


### The shell carve LANDED (2026-08-27) — six collaborators behind the Workstation façade

Record: `docs/history/console_architecture_2026-08.md` (rev 3, #209). Six
collaborators — `ws.web`/`prefs`/`covers`/`carts`/`look`/`history` — came out
from behind the façade in five gated landings. `tests/test_console_facade.py`
pins every surviving forward with its caller, the 17 legacy property forwards,
and every `getattr(ws, "…")` name in runtime/device/tools. Facts not to undo:

- **`ws.system` is an ALIAS of the SystemStore dict and is never rebound** —
  `SystemStore.load()` mutates it in place. That identity is why settings_layer's
  and dev_channel's raw writes never had to migrate.
- **The achievement overlays are EVENT-PUSH**: an unlock writes the flat kernel
  deadlines from the arm site; `_animating` and both WMs read plain ints and never
  call into `ach`/`ach_ui`.
- **`theme_colors` stays a flat kernel REBIND on purpose** — the launcher's cache
  keys fold `id(ws.theme_colors)`, so the rebind IS the shelf invalidation. An
  in-place alias there would be a live bug.
- **The frame loop reaches collaborators directly, never through a forward**;
  per-card grid paths use injected BOUND methods.
- **`_icon_cache` is CoverCache's and invalidates on a rescan**, BEFORE
  `slim_carts` re-bakes — slimming is the last moment a cart's sprite art exists
  in RAM.
- **Serial vocabulary moved with the code**: `p4_conformance` speaks `ws.carts.all`,
  `p4_hitch` wraps `ws.history.idle_tick`, `p4_chrome_freeze`/`p4_scroll_ab` speak
  `ws.look`.


### The 2026-08-22 hardening pass — the rules it left

Record: #206/#207/#208's comments. The recurring shape, and the first thing to
check when adding anything here: **a mechanism was promoted into one
body and nothing executable guarded it.**

- **A board with no lever reports `None`, never `0`.** A frozen 0 is also what a
  BROKEN lever looks like, and that ambiguity is what hid `PUMP fold=` printing 0
  on every board for weeks. Apply it to every new meter.
- **A counter that reaches no consumer is not instrumentation.** The Guition's
  route is `state`, not PUMP — it stages no `device_diag` (its board.toml says
  why; the P4's absence is an unwritten omission, not a decision).
- **`sdkconfig.board` is the only copy of a board's sdkconfig facts.** The guard
  derives its list and splits "is the tree stale" (a fingerprint) from "did Kconfig
  honour it" (a grep that only means anything once the fingerprint matches).
  **The two S3 fragments are 35-of-36 identical and must NOT be merged** — each
  value carries its own per-board argument.
- **A wired service must be STARTED and POLLED, not merely built**, and `poll` must
  be per-frame: on the boot path it runs once and reads statically identical.
- **Derived values get one author.** The two InputStates stay separate at 94%
  identical — `BUTTONS` is 8 names vs 15, in different orders, and a test says
  asserting them equal would be wrong.
- **Storage READS take the SD gate too, not just writes.** `poll_webhost` runs at
  the frame tail after `kick()`, so a frame that painted leaves the feeder shipping
  bands, and an sdspi transaction there is the documented panic.
- **The browser gets carts, not their history** — the undo journal crosses the wire
  in neither direction; the receiving side writes its own (`pmem` does cross).


### The 2026-08-28 quality sweep — the rules it left

Record and gates: #206, #207, #208.

- **ONE `PERF` line, one producer, three boards.** `runtime/perf_line.py` holds
  the field table, the formatter AND the parser, measured by
  `device_boot.PerfSampler` on `FrameLoop.account`. **A field a board cannot
  measure prints `-`, never `0`.** Cart titles are slugged and compounds join with
  `/`, because both readers split on whitespace and an inner `=` reads as a field.
  `tools/p4_perf.py` requires `--board`: it used to default to the P4's dtr/rts
  LOW, which on an S3 is a chip reset that strands the handle.
- **`moy_flush`'s tail wait runs even after a queue error** — the T-Deck's SD fence
  and the Guition's `s_retrieved` reconciliation both depend on it, and skipping it
  leaves band DMA live on a bus an sdspi session is about to take.
  `tests/moy_flush_harness/` compiles the REAL C on a host with no board.
- **Adding a settings toggle is one entry in `SETTINGS_TOGGLES`**, not six sites.
  The capability gates stay expressed, and the flat mirrors stay flat attributes —
  `frame_cap_fps` reads `ws.frameskip` every loop iteration on all three boards.
- **The `colors=` hatch is 14 sites and each has a written reason** (`ui.row`/
  `ui.cell` take `kind=`, like `ui.button`). `row_menu` and `row_list` are
  deliberately two skin entries: `ink_dim` and `chrome_ink_dim` resolve differently
  on machine/dark, so collapsing them passes every golden and moves a pixel.
- **The on-glass suites share one fixture** (`tests/on_glass.py`), and
  reset-vs-attach is read from `board.toml`, never chosen in a suite.
- **A doc names a file only if it exists** — `tools/check_docs.py` resolves
  backticked paths AND bare `.py` names, and pins cross-document duplication so it
  can only shrink. Run it after any docs edit.

