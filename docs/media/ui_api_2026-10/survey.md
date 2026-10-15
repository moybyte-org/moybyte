# A public UI API for Moybyte: adopt a standard, or ours? (survey, 2026-10-15)

The design built on it is `docs/ui_api_2026-10.md`. The owner's decisions of 2026-10-15 kept this survey's shape and names and amended its rule on style: a call may override its colours and an app may take focus over (that doc, sections 3 and 4).

**Answer: copy a shape, don't adopt a library.** Take Dear ImGui's *semantics* (immediate mode, ids, engine-owned keyboard navigation) and microui/raygui's *call shape* (flat calls that answer a value), and give them **our names** (the design system's own: `primary`, `tonal`, `danger`, `toggle`, `current`, `focus` — which already match Material 3 and HTML/ARIA). No surveyed API can be adopted as-is: the ones with keyboard navigation are too big or the wrong model, and the small ones have no navigation and let an app push colours, which breaks engine-owned theming.

## What Moybyte has today

- `runtime/ui.py` over `native/moy_ui` (C): immediate mode, no tree, style as data. Calls take canvas, theme and an explicit rect: `ui.button(cv, th, rect, label, kind, on, …)`, `ui.row`, `ui.cell`, `ui.list`, `ui.chip`, `ui.tab_row`, rect algebra, plus `Hits`, `ScrollRegion`, `DragTap` owned by the caller.
- **Focus belongs to each app** — no focus model in the toolkit; apps draw `focus_ring` themselves. `Hits` is pointer-only.
- Compiled apps get nine `ui_*` rows in `native/moy_app/roles.json`.
- moy-spec's SPEC.md has **no UI verbs**: carts get `btn`/`btnp`, `touch()`, `key`, `textmode`, and draw menus by hand.
- Settled: no widget tree, no LVGL (`docs/surface_model_v1.md` §8; `docs/kernel_toolkit_2026-10.md` §1).

## The survey

"Theme restyles every app" = a theme restyles every app with no app code changed (our core requirement).

| API | model | theme restyles every app? | keyboard focus / nav | MCU footprint | licence | Lua/Py/wasm binding | known to kid/hobby makers |
|---|---|---|---|---|---|---|---|
| **Dear ImGui** | immediate, ids | partly: one global style, but apps `PushStyleColor` freely; no semantic kinds | **yes, the best** (keyboard+gamepad, spatial) | heavy: C++, triangle rasteriser, font atlas | MIT | overloads, `bool*` out-params | engine/tool devs, not kids |
| **raygui** | immediate, rect per call | yes: global property table, `.rgs` styles; no kinds | none | small single header | zlib | trivial flat C | raylib hobbyists |
| **microui** | immediate, ids, command list | yes: one `mu_Style`; no kinds | none | **tiny**, ~1100 lines ANSI C | MIT | trivial | niche |
| **Nuklear** | immediate, command buffer | yes: deep `nk_style`, image skins | text input only | medium | MIT/PD | C, many structs | C devs |
| **egui** | immediate | yes: global Style/Visuals | yes | megabytes as wasm | MIT/Apache | Rust only | Rust devs |
| **LVGL** | retained tree | yes: styles + `lv_theme` | yes (groups) | 64 KB+ flash | MIT | C, MicroPython | embedded devs; **in our graveyard** |
| **Picotron gui** | retained tree of Lua tables + callbacks | **no**: colours per element | none (mouse-first) | built in | proprietary | Lua-native | **our closest audience** |
| **Playdate SDK** | gridview: app draws each cell; OS system menu | no: the app draws | yes (d-pad) | small | proprietary | Lua, C | indie handheld makers |
| **SUIT** (LÖVE) | immediate | **yes**: theme is a table of draw functions | text input only | small | MIT | Lua-native | LÖVE hobbyists |
| **Slab** (LÖVE) | immediate, ImGui-like | yes: style tables | limited | medium | MIT | Lua | LÖVE hobbyists |
| **PICO-8 / TIC-80** | none: `sel` index + `btnp` | n/a — every cart looks different | hand-rolled | 0 | n/a | n/a | **everyone in our audience** |
| SwiftUI / Flutter / Compose | declarative tree | yes | yes | far beyond an MCU | various | none | vocabulary: Material 3 filled/**tonal**/outlined buttons |
| HTML / CSS | retained DOM, cascade | yes | yes (`tabindex`, `:focus-visible`) | n/a | open | n/a | **most widely known vocabulary** |

What it shows:
1. **Only three have real keyboard navigation**: Dear ImGui, LVGL, egui. LVGL is ruled out, egui is Rust; ImGui's model (immediate, no tree) fits ours.
2. **None knows our design system**: no semantic kinds, no `current` separate from focus, no focus per size class — and all let an app override colours per call. We need the opposite.
3. **Our audience knows no UI API.** PICO-8/TIC-80 makers hand-roll menus; Picotron's GUI is mouse-first and coloured per element. The vocabulary they know is HTML's and phone apps'.

## The top three shapes, as a Moybyte cart
(Same page each time: a Sound toggle, a Volume slider, a Theme list with a current item, a primary Save button, keyboard focus. Sketches.)

### 1. Dear ImGui's shape
```lua
function _draw()
  if ui.Begin("Settings") then
    _, sound = ui.Checkbox("Sound", sound)
    _, vol   = ui.SliderInt("Volume", vol, 0, 10)
    for i, name in ipairs(themes) do
      if ui.Selectable(name, i == cur) then cur = i end
    end
    ui.PushStyleColor(ui.Col_Button, 0x4c3fd6)  -- "primary"
    if ui.Button("Save") then save() end
    ui.PopStyleColor()
  end
  ui.End()
end
```
Gains: battle-tested navigation, ids, `(changed, value)`. Breaks: primary is a pushed colour (per-app styling the look forbids); no `current`; Begin/End pairing traps; C++ + triangle rasteriser; hundreds of calls to keep verbatim across Lua/Python/wasm.

### 2. raygui / microui shape (explicit rects)
```lua
function _draw()
  local y = 20
  sound = ui.toggle(10, y, 140, 20, "Sound", sound);       y += 24
  vol   = ui.slider(10, y, 140, 20, "Volume", vol, 0, 10); y += 24
  scroll, cur = ui.list(10, y, 140, 80, themes, scroll, cur); y += 84
  ui.set_style(ui.BUTTON, ui.BASE_COLOR_NORMAL, 0x4c3fd6)
  if ui.button(10, y, 140, 20, "Save") then save() end
end
```
Gains: closest to today's toolkit and the wasm rows; tiny. Breaks: no focus at all; primary is global style mutation; hard-coded coordinates keep the theme's row heights and type sizes out of the app.

### 3. Picotron's shape (retained, callbacks)
```lua
function _init()
  gui = create_gui()
  gui:attach_toggle{label="Sound", on=sound, change=function(e) sound=e.on end}
  gui:attach_slider{label="Volume", min=0, max=10, value=vol}
  gui:attach_list{items=themes, current=cur}
  gui:attach_button{label="Save", kind="primary", tap=save}
end
function _update() gui:update_all() end
function _draw() cls() gui:draw_all() end
```
Gains: the PICO-8 family's idiom. Breaks: a widget tree (settled "none, ever"); state lives twice; callbacks don't cross to wasm; no keyboard navigation; colours per element.

## Recommendation: copy the shape, our names

```lua
function _draw()
  ui.page("Settings")                        -- rows stack in content_rect()
  sound = ui.toggle("Sound", sound)          -- on = the current mark
  vol   = ui.slider("Volume", vol, 0, 10)    -- left/right while focused
  cur   = ui.list("Theme", themes, cur)      -- cur drawn as current
  if ui.button("Save", "primary") then save() end
end
```

1. **It is today's toolkit, made public** — immediate, C, style as data. New: an auto-row cursor (`ui.page`) and a focus state inside `moy_ui`. No tree.
2. **Navigation is the engine's** (keyboard is primary): up/down (and spatial left/right) over last frame's rects (the `Hits` buffer already records them); `a`/Enter activates; left/right adjusts sliders/toggles; exactly one focus per screen. A cart can `ui.focus(id)` and ask `ui.keys_taken()`; it never draws the mark.
3. **Kinds and marks are the API**: `normal|primary|tonal|danger|toggle`, `current` is a value the cart passes; no colour/radius/font arguments anywhere.
4. **Same in three runtimes**: flat calls, numbers in, a value out — Lua, Python and an i32 wasm import agree.
5. **Familiar enough**: "a button is a line that answers true when pressed"; names match web and phone apps.

### Theming stays engine-owned
- No style in any call; metrics come from the theme per size class; `ui.page` lays out at the theme's row height (so auto-layout belongs in the API).
- Focus and current drawn only by the engine.
- Escape hatch `ui.part(id, w, h)`: the engine draws fill and marks, the cart draws content (swatch, cover, Calc's display); it can read `theme()` roles, not write them.
- The shell's own apps use only this API — a ratchet test keeps it so.

### Cost
Focus/navigation state in `moy_ui`, auto-row cursor, slider/toggle/part widgets; move the shell's apps onto it (deleting their own focus code); goldens move; the nine wasm `ui_*` rows are replaced (no users, no compatibility layer).

## What goes into moy-spec
`proposals/ui.md`, aimed at **core** (§10: software any host can implement is not an extension): the verb table (`ui.page`, `label`, `button`, `toggle`, `slider`, `list`, `field`, `part`, `focus`, `keys_taken`, `ui.at(x,y,w,h)`), identical in Lua/Python/wasm; the semantics (ids, one focus, navigation by the logical buttons, activation on `a`, `b`/back stays the host's); the kind names. **Not** pixels: each host draws its own look; libmoy ships a reference look in palette indices; conformance tests behaviour, not pixels.

### Sources
Dear ImGui https://github.com/ocornut/imgui · raygui https://github.com/raysan5/raygui · microui https://github.com/rxi/microui · Nuklear https://github.com/Immediate-Mode-UI/Nuklear · egui https://github.com/emilk/egui · LVGL https://lvgl.io · Picotron manual §10.14 https://www.lexaloffle.com/dl/docs/picotron_manual.html · Inside Playdate https://sdk.play.date/inside-playdate/ · SUIT https://github.com/vrld/suit · Slab https://github.com/flamendless/Slab

*Evidence: Picotron and Playdate from their official manuals; SUIT from its theme.lua; microui and raygui from their project pages; the ImGui, Nuklear, egui, LVGL, Slab, declarative and HTML rows from memory, not re-fetched; footprints rough; the three Lua samples are sketches (Picotron ships only button, scrollbars, pulldown and text editor).*
