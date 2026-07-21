# Moybyte v0.4 userland runtime (host reference)

This is the **"other end" of the stack** from the native graphics core
(`firmware/.../native/moy_gfx`, `moy_compositor`): the **fantasy-workstation
userland** a cartridge runs on. It runs entirely on the host (no device), so it's
the fast Codex/dev loop for the v0.4 product, and it realizes v0.4 plan **Task
Group A (PC simulator first)** + **Task Group B (cartridge format)** + the first
**Living Desktop** content.

## What's here

**The shared console (host == device).** The launcher/desktop/cards/code/paint UI is
one codebase that both the host *and* the T-Deck run — the host renders the same
320×240 pixels with the same petme128 font. Since the **v0.5 shell** refactor
(`docs/shell_ux_v1.md`) the console is everything-is-a-process: `console.py` is the
**kernel** (compositor/router — the `frame`/`handle_input`/`handle_pointer` loop over
a z-ordered Layer stack + the shared draw toolkit), the apps it runs live in
`project.py`/`player.py`/`editor_app.py`, the back-stack window manager in `wm.py`,
and each **surface is its own `*_layer.py` module** (the `Layer` protocol + the
extracted editors below). The files split into **shared** (canonical here,
build-staged into the firmware `modules/` tree so the device freezes the identical
code) and **host glue**.

| file | role |
|---|---|
| `palette.py` | **(shared-ish)** `MOY64` 64-color palette (PICO-8 base 16 + ramp), name↔index |
| `font.py` | **(host)** petme128 8×8 font extracted byte-for-byte from framebuf, so host text is pixel-identical to the device |
| `canvas.py` | **(host)** `Canvas` — indexed surface (320×240 in the console), TIC-80 API (`cls/pix/line/rect/rectb/circ/circb/spr/map/print` — `rect`/`circ` filled, `rectb`/`circb` outlines; `map` blits a tilemap region, native one-call `moy_gfx.blit_map` on device), `print` uses `font.py`, `to_rgb888()`; `Image` sprites |
| `editors.py` (+ `editors_base/_code/_sheet/_paint_map/_block/_music/_scene.py`) | **(shared, staged to device)** the editor cores, split per editor with `editors.py` as the re-exporting umbrella (`from editors import X` unchanged): `editors_base` = `UndoStack`/`KeyEdge`/`UndoRedoMixin`; `CodeEditor`; `_SheetSprite`/`SpriteSheet` (8×8 tiles + `__gfx__` hex)/`IconSheet`/`TileMap` (`w×h` tile-id grid over a sheet + `map.moymap` hex, `mget`/`mset`, #32); `PaintEditor`+`MapEditor`; `BlockRow`+`BlockEditor`; `MusicEditor`; `SceneEditor` (#85 Stage 2: placed-actor rows + place/select/move/z-order/props over full-snapshot undo) |
| `audio.py` | **(shared, staged to device)** sound data model (`SFX`/`MusicTrack`/`AudioBank`) + `AudioEngine` pure-Python synth/mixer (`render()` → PCM). Backends (host `FakeAudio`/SDL, device I2S) consume `render()`. See `docs/audio_design_v04.md` (#16) |
| `console.py` | **(shared, staged to device)** `Workstation` — the v0.5 shell **kernel**: the compositor/router (the `frame`/`handle_input`/`handle_pointer` stack loop, delegating the memoized stack + composite to `wm.py`), the shared draw toolkit (`_glyph`/`_icon`/`_btn`/`_mini_btn`), the spawn/exit + navigation verbs (`open`/`run`/`go_home`/`open_picker`/`launch_selected`), the pinned handles (cart `config`/`apply`, `wallpaper_id` + picker API, `nav`), `Layout`/`CodeLayout` (responsive geometry), `NAMES`/`CURSOR`. Backend-agnostic: injected `make_api` + `make_audio` + cart store + `wifi`. The device's `moy_runtime` imports it; `host_app` runs it on the host |
| `project.py` | **(shared, staged)** `Project` — the open cart's live workspace (cart/config/sheet/tilemap/images/pmem + the `commit_*` persistence verbs); a commit also appends the undo journal and runs blocks↔code graduation detection |
| `player.py` | **(shared, staged)** `Player` — the `run(cart) → plays → returns` black box: starts a cart under the frozen `make_api`, ticks it, turns crashes into the error panel, owns the hold-BACKSPACE exit gesture + its transient toast; exit pops to the run caller |
| `editor_app.py` | **(shared, staged)** `EditorApp` — the ONE authoring app, opened on a `Project` from the launcher's Make tile → project-picker: the tab ladder Config→Blocks→Code→Sprites→Map→Music (+ PROJECTS/PLAY/SAVE in its lent bar zone); the tabs are the `*_layer.py`/`*_ui.py` surfaces |
| `wm.py` | **(shared, staged)** `FullscreenStackWM` — the small-screen tier's WM: the process back-stack (`screen` is a projection of its top), the **memoized** visible/draw stack (zero per-frame list churn, #66), and the #39 game↔system viewport composite |
| `wm_windowed.py` | **(shared, host/P4 only — NOT staged to the S3)** `WindowedWM` — the Picotron-style windowed desktop (#73/#58, spec §3's big-screen tier): the Library is the full-screen launch surface; PLAY/CHANGE leaves it for the wallpaper desktop, where every pushed process is a window with a WM title strip (min/max/close), draggable + grip-resizable and mirrored as a taskbar chip in the desktop's OS bar (restore/minimize/raise). `ws.windowed_chrome` strips OS chrome (right zone, dock) from in-window bars so the desktop bar is the one taskbar; a running cart composites integer-scaled + centered, so the editor stays visible beside a playtest. Per-window layout contexts re-run the #39 responsive layouts at each window's size. Works over the web transport too (window buffers become RecordingLayers; the game/wallpaper composite ships as one spr). `simulate_desktop.py --size 1024x600 --windowed`, or in the browser: `web_console.py --size 1024x600 --windowed` |
| `layers.py` | **(shared, staged)** the `Layer` protocol + `_LegacyLayer` shim + the thin object-surface adapters (blocks/map/music/update/sysmenu/about/achievements/perf) |
| `bar_layer.py` | **(shared, staged)** `BarLayer` — the unified 18px top bar + bottom dock (#46): draw + strip cache + clock cache + dock/bar tap slices + the bar/dock geometry constants |
| `launcher_layer.py` | **(shared, staged)** the `Launcher` grid class (two instances: `ws.launcher`, the home RUN-grid with the pinned Make tile and no wallpapers, and `ws.picker`) + `LauncherHomeLayer` — the home desktop composition (wallpaper → grid → bar; a tap always RUNS) + `EditorPickerLayer` — the Editor's project-picker (every editable cart + ＋New; owns New/Copy/two-tap-Delete) (#28) |
| `cards_layer.py` | **(shared, staged)** `CardsLayer` — the "Make it mine" config-card editor (#3/#15): card draw + layout + scroll (msel/mtop) + taps; cart `config`/`apply`/`adjust` stay on `ws`. A malformed `edit` field def (#94) degrades to one inline "!" card via `_validate_field` instead of taking the whole tab down. The header **INFO button** opens a **CART INFO modal** (#94) that edits the manifest's title/author through `ws.project.commit_manifest` (`moy_carts.save_manifest_meta`); `permissions` stays read-only |
| `paint_layer.py` | **(shared, staged)** `PaintLayer` (the sprite/icon paint editor #4/#30) + `ThemeLayer` (EDIT ICONS over the system icon sheet) — one renderer keyed on `ws._editing_icons` |
| `settings_layer.py` | **(shared, staged)** `SettingsLayer` — the Settings aggregator (#28/#39/#53): rows + scroll + draw; owns no config (dispatches every mutation to `ws` setters). Includes the **WIFI panel** (#38, spec §10: scan/pick/password/connect over the injected `ws.wifi` — Settings is an app, so wifi setup coexists with a running cart), the capability-gated P4 **BLUETOOTH KEYBOARD** panel (visual-identity-v1 widgets; enable/scan/pick/forget over `ws.keyboard`), and the **APPEARANCE action row** — wallpaper + panel-theme picking consolidated into the Appearance app (`appearance_app.py`), this row deep-links to it |
| `code_layer.py` | **(shared, staged)** `CodeLayer` — the full-screen code editor (#24/#39): draw + touch/keyboard editing + the MicroPython-safe syntax highlighter; `ws.editor`/`save_code`/`run_code` stay on `ws` |
| `wallpaper.py` | **(shared, staged)** `Wallpaper` — the desktop backdrop component (#28) the launcher home + Settings both draw; owns the rendering + compiled-cart cache, `wallpaper_id` + picker API stay on `ws`. On a distinct big system canvas the cart frame COVER-crops full-bleed (and ships as one b64 img on a recording canvas); the solid fallback fills the SYSTEM canvas |
| `widgets.py` | **(shared, staged)** self-contained support classes: `Pointer` (cursor), `Achievements` (#21 tracker + catalog), `Popup` (dropdown #52), `Pmem` (cart RAM), `Actor`/`Scenes` (#85 placed-actor scenes — the `scene()`/`load_scene()` data model), `_SilentAudio`, `_Blit` |
| `perf_hud.py` / `update_ui.py` / `system_menu_ui.py` / `achievements_ui.py` | **(shared, staged)** the FPS/frame-time HUD (#43), the OTA update screen (#53), the ≡ system-menu drawing (#52), the achievement/Easter-egg drawing (#21) |
| `block_editor_ui.py` / `map_editor_ui.py` / `scene_editor_ui.py` / `music_editor_ui.py` | **(shared, staged)** the block editor (#29), tilemap editor (#32), scene placement editor (#85 Stage 2: WYSIWYG placed actors over the pannable world — tap place/select, drag move/pan, snap, tag/flip props, front/back z-order; live-syncs each gesture into `ws.scenes` so PLAY re-starts on the freshest placement), and music/sound editor (#50) UIs |
| `moy_carts.py` | **(shared, staged to device)** the `.moy` store — scan/load/save_config/save_code/save_sprites/save_sounds/save_map/save_scene/save_manifest_meta (#94: title/author)/create/duplicate/delete + the known-WiFi credential store (load_wifi/remember_wifi/forget_wifi → `wifi.json`, #38) (dict carts; `map.moymap` tilemap blob #32; `scenes/*.moyscene` actor tables #85; `tables/*.moysheet` + `docs/*.moytext` Desk Lab interop #78; only `json`+`os`). Re-exports the three extracted leaves below under their pre-split names |
| `moy_fs.py` | **(shared, staged)** crash-safe file primitives (`_write_atomic`'s tmp/.bak dance + `_read_recover`) shared by the store and the journal |
| `moy_image.py` | **(shared, staged)** the portable `moyimg-v1` codec (Paint's RLE) + the #66/#86 cover-thumb sidecar cache |
| `moy_journal.py` | **(shared, staged)** the per-project undo/redo journal (#7 Stage 7: `journal.jsonl` append-only + full-file snapshots + rotation; the Stage 8 graduation rider). #111: the cursor is a **per-file map** (tolerant migration from the old single seq) so the bar's journal walk is scoped to the active tab's file(s); commits may carry an additive `"ops"` batch (`journal_entry_ops`) |
| `op_history.py` | **(shared, staged)** the #111 universal op-history core: `History`/`OpCodec` (invert-preferred, replay-from-keyframe fallback, segment cap, optional `max_undo` ring) + the shared `TextEditCodec`/`text_diff_op` typing-burst codec. Every Editor tab + Writer/Sheets record fine-grained ops into one of these; the bar UNDO/REDO walks it before the journal |
| `players.py` | **(shared, staged)** the #65 transport-neutral multiplayer layer: `PlayerRouter` (input sources → player slots behind `btn(name, player)`/`players()`; slot 0 = the local console) + the `NetService` seam and `LoopbackNet` host fake behind the permission-gated `net.send`/`on_net` cart verbs |
| `ui.py` | **(shared, staged)** the immediate-mode widget toolkit + rect algebra + Hits + ScrollRegion + the `is_light` gate (visual identity v1 Phase 3; see `docs/app_api_v1.md`) |
| `calc_app.py` | **(shared, staged)** Calc -- the reference SYSTEM APP for the app API (`ws.register_app`, docs/app_api_v1.md): identity cart `system_carts/calc.moy`, geometry via the ui rect algebra, taps via ui.Hits |
| `app_shell.py` | **(shared, staged)** the Desk Lab apps' shared list shell (#78): `ListShellLayout` (chrome-inset frame + notebook-list geometry/row_rect) + `ListShellApp` (is_app identity gate, store readiness, guarded blob load, persist status contract, list scroll/nav) — the base Sheets/Writer/Storybook derive from |
| `sheets_app.py` / `formula.py` | **(shared, staged)** Sheets (#78) — the kid spreadsheet app (identity cart `system_carts/sheets.moy`): workbook list + responsive grid + formula-entry row, autosaving `sheets.json`; `formula.py` is its hand-rolled tokenizer/recursive-descent formula engine + `Sheet` model (the #48 block-operator vocabulary + `sum`/`avg`; cycles → `#LOOP`, malformed → `#ERR`, never raises). A sheet attached to a cart (`tables/<name>.moysheet`) is read back via the `table()` cart verb |
| `host_app.py` | **(host glue)** host `make_api` (incl. audio + the capability-gated `wifi`), `FakeAudio` + `FakeWifi` backends, `build_workstation()` (320×240 Canvas + `moy_carts` + seeded system carts), and `ConsoleDriver` (mouse/keyboard → the shared console) |
| `input.py` | **(host)** `InputState` — held/pressed/released + `last_key` (same contract as firmware `moybyte`) |

The pre-unification host UI (`shell.py`, `workstation.py`, `engine.py`, `api.py`,
`cartridge.py`) was **removed** once the shared console replaced it (issue #17); the
older `.moyproj` SDK lives separately under `moybyte/` / `moybyte_cli/`.

Content + tooling:
- `system_carts/` — `moy_night.moy` (the default: a STATIC brand-colorway night
  scene — free under the redraw gate, incl. the web view),
  `open_machine.moy` (the STATIC MOY64 construction-grid backdrop from the visual
  identity — also free under the redraw gate),
  `wallpaper_space.moy` (Living Desktop: starfield + pet),
  `ocean.moy` (bubbles + fish), `star_catcher.moy` (a **game**: catch falling
  stars), and `paint.moy` (the full-canvas Paint app; saves a shared `.moyimg`,
  publishes it through the `my_art.moy` wallpaper, and can attach it to a game as
  `images/bg.moyimg`). Paint's high-level store actions live behind the narrow
  shell-owned `ArtworkService`; they are not part of the kid cart API.
  `theme_picker.moy` (title: Appearance) opens the ONE appearance surface (`runtime/appearance_app.py`,
  deep-linked from Settings' APPEARANCE row): a Display-Properties-style side-by-side picker —
  catalog column left, preview right. IMAGES/CARTS preview in a little MONITOR whose screen
  shows the FULL wallpaper (a second compile of the cart on an offscreen canvas, blitted as one
  spr so the web tiers render it; fills/My Art draw direct); THEMES previews mock WM windows in
  the selected token set. A cart's preview is a COMPUTED STILL, identical on every tier (no
  host/device policy fork — the owner's anti-drift call): one render per cart source on the
  pure-Python `canvas.py` (staged to both boards), persisted as a `thumbs/wp<w>x<h>.mct`
  sidecar stamped with `cover_sig(src)` (the thumbnail model — an edit recomputes, a re-seed
  regenerates; no prebaked assets), so the appearance screen closes the redraw gate like any
  static UI.
  `writer.moy` (title: Writer) opens the kid notebook (`runtime/writer_app.py`):
  a notes list + ruled text page over the shared `CodeEditor` core, autosaving
  one crash-safe `notes.json` beside `artwork.moyimg`.
  `storybook.moy` (title: Storybook, #78) opens `runtime/storybook_app.py`:
  decks of art+words pages that COMPILE to real story carts (`deck.json` + a
  generated, readable `main.py`; Paint art attaches per page). Hand-editing the
  code past the deck's vocabulary GRADUATES the story through the exact same
  manifest `graduated` flag + undo-journal `grad` rider the block editor uses
  (`project.py`'s `_journal_code`/`_journal_code_toward` treat a deck.json as
  an origin just like blocks.json) -- not a local hash-compare guess: it
  persists, survives a reload, and undoing past the graduating commit
  un-graduates it, same as a block cart.
  `sheets.moy` (title: Sheets, #78) opens `runtime/sheets_app.py`: the kid
  spreadsheet (see the file table above); sheets become game data via `table()`.
  An open sheet's ATTACH button opens a third mode listing every GAME/story
  cart (the same row-list widget the workbook list itself uses) and writes the
  sheet's current cells into the picked cart's folder as
  `tables/<name>.moysheet` (`moy_carts.save_table`), so its next open reads it
  back via `table(name)`.
  Each seed carries its own `config` defaults and edit schema where applicable.
- `tools/simulate_desktop.py` — run the workstation (or a single cart) on the host.
- `tests/test_v04_userland.py` — canvas, cartridge, desktop, launcher, cards tests.

## Run it

```bash
# interactive workstation (needs a display): boots to the launcher
#   arrows move, RUN=Enter, MENU=M, CODE=C, SAVE=S, HOME=H, quit=Esc
python tools/simulate_desktop.py

# headless demo tour -> animated GIF (launcher -> carts -> Make-it-mine)
python tools/simulate_desktop.py --demo --gif demo.gif

# launch a single cartridge directly (skip the launcher)
python tools/simulate_desktop.py --cart system_carts/star_catcher.moy

# headless custom script
python tools/simulate_desktop.py --gif out.gif \
    --script "wait:20 right run wait:40 home wait:8 right run wait:40"
```

Script tokens: a button name (`menu/up/down/left/right/run/save/code/home`)
presses once; `name:N` presses then observes N frames; `wait:N` observes N frames.

## Why indexed + language-neutral

The canvas works in **palette indices** and the drawing API is plain
functions over a buffer — no dependency on `framebuf`, LVGL, or even Python in the
contract. That's deliberate: the same surface maps onto the device's native
`moy_compositor` RGB565 framebuffer (indices → RGB565 via the palette), and onto a
future Lua VM. Cartridges are portable; only the backend changes.

## v0.4 MVP status (plan §14.3)

Done here: boot to a cartridge **launcher** (Task A); the `.moy` model with
duplicate/save and system-vs-user protection (Task B); multiple cartridge types
running on one runtime — wallpaper + **game** (Task A/G seed); the interactive
**Make it mine** / **cards editor** with a **See the code** view (Task E);
on-screen **Run** / **Home** / **Save**; and a friendly error screen.

Next: load the saved user wallpaper on boot; richer cards (add/remove, not just
adjust); local **share** of a cartridge (Task H); and the big one — port the
runtime's canvas backend onto the device `moy_compositor` so the *same* `.moy`
runs on the T-Deck.
