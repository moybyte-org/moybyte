# KidCode v0.4 userland runtime (host reference)

This is the **"other end" of the stack** from the native graphics core
(`firmware/.../native/kc_gfx`, `kc_compositor`): the **fantasy-workstation
userland** a cartridge runs on. It runs entirely on the host (no device), so it's
the fast Codex/dev loop for the v0.4 product, and it realizes v0.4 plan **Task
Group A (PC simulator first)** + **Task Group B (cartridge format)** + the first
**Living Desktop** content.

## What's here

**The shared console (host == device).** The launcher/desktop/cards/code/paint UI
is now ONE module (`console.py`) that both the host *and* the T-Deck run — the host
renders the same 320×240 pixels with the same petme128 font. The files below split
into **shared** (canonical here, build-staged into the firmware `modules/` tree so
the device freezes the identical code) and **host glue**.

| file | role |
|---|---|
| `palette.py` | **(shared-ish)** `KID64` 64-color palette (PICO-8 base 16 + ramp), name↔index |
| `font.py` | **(host)** petme128 8×8 font extracted byte-for-byte from framebuf, so host text is pixel-identical to the device |
| `canvas.py` | **(host)** `Canvas` — indexed surface (320×240 in the console), TIC-80 API (`cls/pix/line/rect/rectb/circ/circb/spr/print` — `rect`/`circ` filled, `rectb`/`circb` outlines), `print` uses `font.py`, `to_rgb888()`; `Image` sprites |
| `editors.py` | **(shared, staged to device)** `CodeEditor` / `SpriteSheet` (8×8 tiles + `__gfx__` hex) / `PaintEditor` |
| `console.py` | **(shared, staged to device)** `Launcher` + `Pointer` + `Workstation` + cards/code/paint UI + layout/`NAMES`/`CURSOR`. Backend-agnostic: injected `make_api` + cart store. The device's `kid_runtime` imports it; `host_app` runs it on the host |
| `kid_carts.py` | **(shared, staged to device)** the `.kcart` store — scan/load/save_config/save_code/save_sprites/create/duplicate/delete (dict carts; only `json`+`os`) |
| `host_app.py` | **(host glue)** host `make_api`, `build_workstation()` (320×240 Canvas + `kid_carts` + seeded system carts), and `ConsoleDriver` (mouse/keyboard → the shared console) |
| `input.py` | **(host)** `InputState` — held/pressed/released + `last_key` (same contract as firmware `kidcode`) |

The pre-unification host UI (`shell.py`, `workstation.py`, `engine.py`, `api.py`,
`cartridge.py`) was **removed** once the shared console replaced it (issue #17); the
older `.kcproj` SDK lives separately under `kidcode/` / `kidcode_cli/`.

Content + tooling:
- `system_carts/` — `wallpaper_space.kcart` (Living Desktop: starfield + pet),
  `ocean.kcart` (bubbles + fish), `star_catcher.kcart` (a **game**: catch falling
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
python tools/simulate_desktop.py --cart system_carts/star_catcher.kcart

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
`kc_compositor` RGB565 framebuffer (indices → RGB565 via the palette), and onto a
future Lua VM. Cartridges are portable; only the backend changes.

## v0.4 MVP status (plan §14.3)

Done here: boot to a cartridge **launcher** (Task A); the `.kcart` model with
duplicate/save and system-vs-user protection (Task B); multiple cartridge types
running on one runtime — wallpaper + **game** (Task A/G seed); the interactive
**Make it mine** / **cards editor** with a **See the code** view (Task E);
on-screen **Run** / **Home** / **Save**; and a friendly error screen.

Next: load the saved user wallpaper on boot; richer cards (add/remove, not just
adjust); local **share** of a cartridge (Task H); and the big one — port the
runtime's canvas backend onto the device `kc_compositor` so the *same* `.kcart`
runs on the T-Deck.
