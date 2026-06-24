# KidCode v0.4 userland runtime (host reference)

This is the **"other end" of the stack** from the native graphics core
(`firmware/.../native/kc_gfx`, `kc_compositor`): the **fantasy-workstation
userland** a cartridge runs on. It runs entirely on the host (no device), so it's
the fast Codex/dev loop for the v0.4 product, and it realizes v0.4 plan **Task
Group A (PC simulator first)** + **Task Group B (cartridge format)** + the first
**Living Desktop** content.

## What's here

| file | role |
|---|---|
| `palette.py` | `KID64` 64-color palette (PICO-8 base 16 + ramp), name↔index |
| `canvas.py` | `Canvas` — 480×270 **indexed** surface, PICO-8-style API (`cls/pset/line/rect/rectfill/circ/circfill/spr/print`), 3×5 font, `to_rgb888()`; `Image` sprites |
| `input.py` | `InputState` — held/pressed/released buttons (same contract as firmware `kidcode`) |
| `cartridge.py` | `Cartridge` — `.kcart` folder (manifest + main + `config.json`), load/validate, **duplicate**, **save_config**, system-vs-user |
| `api.py` | the cartridge global namespace (`cls/spr/text/btn/cfg/col/rnd/image/...`) bound to a runtime |
| `engine.py` | `DesktopRuntime` — load a cart, run `_init/_update/_draw`, recover bad carts into a friendly on-canvas error |
| `shell.py` | `DesktopShell` — the interactive desktop + **cards editor**: a **Make it mine** panel that shows each editable field as a natural-language card (from the manifest `edit` schema / `card` templates), a **See the code** view, **Run** to apply, **Save** to a user cartridge. `press(button)` events, live or scripted. |
| `workstation.py` | `Catalog` / `Launcher` / `Workstation` — boot to a **cartridge gallery**, open any `.kcart` (wallpaper / game) into the shell, **Home** back, rescan for saved user carts. "Everything is a cartridge." |

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
