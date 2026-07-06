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
320×240 pixels with the same petme128 font. `console.py` is the **compositor/router**
(`frame`/`handle_input`/`handle_pointer` loop over a z-ordered Layer stack + the shared
draw toolkit + cart lifecycle); each **surface is its own `*_layer.py` module** (the
`Layer` protocol + the extracted editors below). The files split into **shared**
(canonical here, build-staged into the firmware `modules/` tree so the device freezes
the identical code) and **host glue**.

| file | role |
|---|---|
| `palette.py` | **(shared-ish)** `MOY64` 64-color palette (PICO-8 base 16 + ramp), name↔index |
| `font.py` | **(host)** petme128 8×8 font extracted byte-for-byte from framebuf, so host text is pixel-identical to the device |
| `canvas.py` | **(host)** `Canvas` — indexed surface (320×240 in the console), TIC-80 API (`cls/pix/line/rect/rectb/circ/circb/spr/map/print` — `rect`/`circ` filled, `rectb`/`circb` outlines; `map` blits a tilemap region, native one-call `moy_gfx.blit_map` on device), `print` uses `font.py`, `to_rgb888()`; `Image` sprites |
| `editors.py` | **(shared, staged to device)** `CodeEditor` / `SpriteSheet` (8×8 tiles + `__gfx__` hex) / `TileMap` (`w×h` tile-id grid over a sheet + `map.moymap` hex, `mget`/`mset`, #32) / `PaintEditor` |
| `audio.py` | **(shared, staged to device)** sound data model (`SFX`/`MusicTrack`/`AudioBank`) + `AudioEngine` pure-Python synth/mixer (`render()` → PCM). Backends (host `FakeAudio`/SDL, device I2S) consume `render()`. See `docs/audio_design_v04.md` (#16) |
| `console.py` | **(shared, staged to device)** `Workstation` — the compositor/router (the `frame`/`handle_input`/`handle_pointer` stack loop + `_visible_stack`/`_build_layers` + the #39 game↔system composite), the shared draw toolkit (`_glyph`/`_icon`/`_btn`/`_mini_btn`), cart lifecycle (`open`/`_start`/`go_home`/`set_menu_view`), the pinned handles (`ws.editor`/`ws.paint`, cart `config`/`apply`, `wallpaper_id` + picker API, `nav`), `Layout`/`CodeLayout` (responsive geometry), `NAMES`/`CURSOR`. Backend-agnostic: injected `make_api` + `make_audio` + cart store + `wifi`. The device's `moy_runtime` imports it; `host_app` runs it on the host |
| `layers.py` | **(shared, staged)** the `Layer` protocol + `_LegacyLayer` shim + the thin object-surface adapters (blocks/map/music/update/sysmenu/about/achievements/perf) |
| `bar_layer.py` | **(shared, staged)** `BarLayer` — the unified 18px top bar + bottom dock (#46): draw + strip cache + clock cache + dock/bar tap slices + the bar/dock geometry constants |
| `launcher_layer.py` | **(shared, staged)** the `Launcher` grid class (its instance is `ws.launcher`) + `LauncherHomeLayer` — the home desktop composition (wallpaper → grid → bar) + grid nav (#28) |
| `cards_layer.py` | **(shared, staged)** `CardsLayer` — the "Make it mine" config-card editor (#3/#15): card draw + layout + scroll (msel/mtop) + taps; cart `config`/`apply`/`adjust` stay on `ws` |
| `paint_layer.py` | **(shared, staged)** `PaintLayer` (the sprite/icon paint editor #4/#30) + `ThemeLayer` (EDIT ICONS over the system icon sheet) — one renderer keyed on `ws._editing_icons` |
| `settings_layer.py` | **(shared, staged)** `SettingsLayer` — the Settings aggregator (#28/#39/#53): rows + scroll + draw; owns no config (dispatches every mutation to `ws` setters) |
| `code_layer.py` | **(shared, staged)** `CodeLayer` — the full-screen code editor (#24/#39): draw + touch/keyboard editing + the MicroPython-safe syntax highlighter; `ws.editor`/`save_code`/`run_code` stay on `ws` |
| `wallpaper.py` | **(shared, staged)** `Wallpaper` — the desktop backdrop component (#28) the launcher home + Settings both draw; owns the rendering + compiled-cart cache, `wallpaper_id` + picker API stay on `ws` |
| `widgets.py` | **(shared, staged)** self-contained support classes: `Pointer` (cursor), `Achievements` (#21 tracker + catalog), `Popup` (dropdown #52), `Pmem` (cart RAM), `_SilentAudio`, `_Blit` |
| `perf_hud.py` / `update_ui.py` / `system_menu_ui.py` / `achievements_ui.py` | **(shared, staged)** the FPS/frame-time HUD (#43), the OTA update screen (#53), the ≡ system-menu drawing (#52), the achievement/Easter-egg drawing (#21) |
| `block_editor_ui.py` / `map_editor_ui.py` / `music_editor_ui.py` | **(shared, staged)** the block editor (#29), tilemap editor (#32), and music/sound editor (#50) UIs |
| `moy_carts.py` | **(shared, staged to device)** the `.moy` store — scan/load/save_config/save_code/save_sprites/save_sounds/save_map/create/duplicate/delete + the known-WiFi credential store (load_wifi/remember_wifi/forget_wifi → `wifi.json`, #38) (dict carts; `map.moymap` tilemap blob, #32; only `json`+`os`) |
| `host_app.py` | **(host glue)** host `make_api` (incl. audio + the capability-gated `wifi`), `FakeAudio` + `FakeWifi` backends, `build_workstation()` (320×240 Canvas + `moy_carts` + seeded system carts), and `ConsoleDriver` (mouse/keyboard → the shared console) |
| `input.py` | **(host)** `InputState` — held/pressed/released + `last_key` (same contract as firmware `moybyte`) |

The pre-unification host UI (`shell.py`, `workstation.py`, `engine.py`, `api.py`,
`cartridge.py`) was **removed** once the shared console replaced it (issue #17); the
older `.moyproj` SDK lives separately under `moybyte/` / `moybyte_cli/`.

Content + tooling:
- `system_carts/` — `wallpaper_space.moy` (Living Desktop: starfield + pet),
  `ocean.moy` (bubbles + fish), `star_catcher.moy` (a **game**: catch falling
  stars). Each carries `config` defaults, an `edit` schema, and card templates.
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
