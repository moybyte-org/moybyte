# Third-party components

Moybyte is licensed as described in [LICENSE.md](LICENSE.md) — FSL-1.1-MIT for
the console and firmware. Some files in this
repository did **not** originate here, and some of what the build produces
bundles code from elsewhere. Everything in that category is listed below, with
its upstream, its licence, and whether we changed it.

The FSL is *source-available*, not OSI-approved open source. That makes this
file more important, not less: nothing here is licensed to you by us, and every
upstream's own terms govern its own files. Where an upstream licence requires a
notice to travel with the code, that notice sits next to the code as well as
being recorded here.

**Scope.** This file covers files tracked by git. Build directories
(`.build/`, `dist/`, `native/.staged/`) and the two vendor reference trees
(`firmware/reference_tulipcc/`, `firmware/lilygo_t_deck_plus_reference/`) are
untracked and are not published from this repository; they are working material
only. Section 5 covers what the *distributed build outputs* bundle, which is a
separate question from what is committed.

---

## 1. Summary

| Component | Where it lives here | Upstream | Licence | Modified? |
|---|---|---|---|---|
| Lua 5.4.7 (device VM) | `native/moy_lua/lua/` | [lua.org](https://www.lua.org/) | MIT | **Yes** — documented |
| WAMR 2.4.5 AOT runtime (WebAssembly cart engine), Moybyte's fork | `native/moy_wasm/wamr/` | [moybyte-org/wasm-micro-runtime](https://github.com/moybyte-org/wasm-micro-runtime) (fork of [bytecodealliance/wasm-micro-runtime](https://github.com/bytecodealliance/wasm-micro-runtime)) | Apache-2.0 WITH LLVM-exception | **Yes** — the fork's commits |
| Jet software 3D rasteriser (the compiled Jet carts) | `ports/jet/jet/` | [CubeCoders/Jet](https://github.com/CubeCoders/Jet) | MIT | No |
| Utah teapot mesh + the scene it is lit in (the showcase cart) | `ports/jet/teapot.moy/teapot.obj`, `ports/jet/teapot.moy/src/scene.cpp` | [CubeCoders/JetExamples](https://github.com/CubeCoders/JetExamples), from freeglut's teapot data | MIT; freeglut's X11-style notice | Converted to OBJ; the scene ported |
| ESP 88, the neon city film: its code and artwork (the ESP 88 cart) | `ports/jet/examples/`, `ports/jet/esp88.moy/assets.bin` | [CubeCoders/JetExamples](https://github.com/CubeCoders/JetExamples) (`esp32-neon-film`) | MIT | One line (the film's frame size); artwork repacked, glow and credits scaled |
| `esp_lcd_ek79007` panel driver | `native/p4/moy_dsi/vendor/` | [espressif/esp-iot-solution](https://github.com/espressif/esp-iot-solution) | Apache-2.0 | No |
| `esp_lcd_jd9365` panel driver, Guition's build | `native/p4/moy_dsi/vendor_jd9365/` | Espressif's component as shipped in [Guition's JC8012P4A1C demo](https://github.com/DevinWatson/10.1-inch-ESP32P4-Xiaozhi-ESP32-C6-JC8012P4A1C_I_W_Y) | Apache-2.0 | No |
| GSL3680 touch firmware (JC8012P4A1C glass) | `firmware/guition_jc8012p4a1c/modules/gsl_fw_jc8012.py` | Silead, via the same Guition demo (`esp_lcd_gsl3680.h`) | vendor firmware, redistributed as shipped | Transcribed (`tools/gen_gsl_fw.py`) |
| ST7789 init register values (T-Deck panel) | `firmware/lilygo_t_deck_plus_mainline/native/moy_lcd/modmoy_lcd.c` | [lvgl-micropython/lvgl_micropython](https://github.com/lvgl-micropython/lvgl_micropython) | MIT | **Yes** — transcribed to C |
| AXS15231B init register values (Guition panel) | `firmware/guition_jc3248w535/native/moy_axs/modmoy_axs.c` | [esphome/esphome](https://github.com/esphome/esphome) | MIT (their Python half) | **Yes** — transcribed to C |
| esptool-js 0.6.0 (the site's board flasher) | `site/vendor/esptool-js/` | [espressif/esptool-js](https://github.com/espressif/esptool-js) | Apache-2.0 | No |
| `font_petme128_8x8` glyph data | `runtime/font.py`; derived webfont `site/petme128.woff2` | [MicroPython](https://github.com/micropython/micropython) | MIT | No (re-encoded) |
| PICO-8 base palette + colour names | `runtime/palette.py` (`_BASE16`, `NAMES`) | [PICO-8 / Lexaloffle](https://www.lexaloffle.com/pico-8.php) | CC-0 | No |
| Pixelarticons icon shapes | `runtime/chrome.py` (`_GLYPHS` and siblings) | [halfmage/pixelarticons](https://github.com/halfmage/pixelarticons) | MIT | **Yes** — retraced |
| T-Deck pin assignments | `docs/boards/lilygo_t_deck_plus.md` | [Xinyuan-LilyGO/T-Deck](https://github.com/Xinyuan-LilyGO/T-Deck) | facts; source cited | Transcribed |
| Guition JC3248W535 pin assignments | `firmware/guition_jc3248w535/board.toml` | the owner's own ESPHome definition for the board | facts; source cited | Transcribed |
| Guition JC8012P4A1C pin assignments + DSI timing | `native/p4/moy_dsi/modmoy_dsi.c`, `firmware/guition_jc8012p4a1c/` | Guition's demo `pins_config.h` / BSP + the ESPHome community's board profile | facts; source cited | Transcribed |

Build-time upstreams that end up inside shipped binaries are in §5.
Development and optional dependencies that are *not* redistributed are in §6.

---

## 2. Vendored source

### 2.1 Lua 5.4 — the cart VM

`native/moy_lua/lua/`

The `moy_lua` native module (issue #67) embeds a complete Lua interpreter so a
cart can declare `"runtime": "lua"`. The same directory is staged into the
ESP32-P4 and WebAssembly builds, so this one copy is the source for all three
targets.

- **Upstream:** Lua 5.4.7 — <https://www.lua.org/>, tarball
  <https://www.lua.org/ftp/lua-5.4.7.tar.gz> (the `src/` directory).
- **Licence:** MIT. Copyright © 1994–2024 Lua.org, PUC-Rio.
  Full text: [`.../moy_lua/lua/COPYRIGHT`](native/moy_lua/lua/COPYRIGHT).
- **Modified: yes.** Four changes, all listed in
  [`.../moy_lua/lua/MODIFICATIONS.md`](native/moy_lua/lua/MODIFICATIONS.md):
  a `#pragma GCC optimize("O2")` block added to 32 `.c` files, `LUA_32BITS`
  flipped from `0` to `1` in `luaconf.h`, and two edits to `lobject.c`'s
  number-to-string conversion (an integral float prints without `.0`, and
  integers convert without `snprintf`). Nothing else differs from upstream;
  the tarball's standalone `lua.c` / `luac.c` / `lua.hpp` / `Makefile` are
  simply not vendored.
- `modmoy_lua.c` and `micropython.cmake` in the parent directory are Moybyte's
  own bridge code, not Lua's, and are under this repository's licence.

### 2.2 WAMR — the WebAssembly cart engine

`native/moy_wasm/wamr/`

The `moy_wasm` native module (issue #158, `docs/wasm_tier_plan_2026-09.md`)
embeds the AOT half of the WebAssembly Micro Runtime, compiled into every
console board's image (the headless Zero denies it).

- **Upstream:** WAMR 2.4.5 — <https://github.com/bytecodealliance/wasm-micro-runtime>,
  taken from Moybyte's fork <https://github.com/moybyte-org/wasm-micro-runtime>,
  branch `moybyte-2.4.5`, at the commit `native/moy_wasm/wamr_vendor.json`
  records. `tools/vendor_wamr.py` copies an explicit file list from that commit:
  the AOT loader and runtime, the common layer, the esp-idf platform layer, the
  allocator and the utilities; the interpreter, the compiler, WASI and the
  builtin libc stay behind.
- **Licence:** Apache-2.0 WITH LLVM-exception. Full text:
  [`.../moy_wasm/wamr/LICENSE`](native/moy_wasm/wamr/LICENSE); upstream's own
  third-party notes travel as
  [`.../moy_wasm/wamr/ATTRIBUTIONS.md`](native/moy_wasm/wamr/ATTRIBUTIONS.md).
- **Modified: yes, in the fork, never here.** The fork's commits over the 2.4.5
  tag change the esp-idf platform layer (AOT text in PSRAM on the ESP32-S3 and
  ESP32-P4, PSRAM-only data allocations above a threshold, a range-scoped cache
  sync, a real native-stack boundary), two loader details, and in the runtime
  a memmove that copies disjoint ranges with memcpy; its history is the
  record. `tests/test_wamr_vendor.py` fails on any edit to the copy.
- `modmoy_wasm.c`, `moy_wasm_key.h`, the generated `wamr_pin.h` and
  `micropython.cmake` in the parent directory are Moybyte's own code, under this
  repository's licence.

### 2.3 Espressif `esp_lcd_ek79007` — the P4 panel driver

`native/p4/moy_dsi/vendor/` (moved from the Waveshare's board tree on
2026-09-06, when `moy_dsi` became the two P4 boards' shared panel module)

The EK79007 MIPI-DSI controller driver for the Waveshare 7″ board (issue #58).

- **Upstream:** `espressif/esp_lcd_ek79007` v2.0.2~1 from the
  [ESP Component Registry](https://components.espressif.com/components/espressif/esp_lcd_ek79007),
  sourced from `components/display/lcd/esp_lcd_ek79007` in
  <https://github.com/espressif/esp-iot-solution> at commit
  `12f6ca1182ec48889b17ec570fadaaf267cb336e` (recorded in the vendored
  `idf_component.yml`).
- **Licence:** Apache-2.0, © 2023–2025 Espressif Systems (Shanghai) CO LTD.
  Full text is retained at
  [`.../vendor/license.txt`](native/p4/moy_dsi/vendor/license.txt);
  the per-file `SPDX-FileCopyrightText` / `SPDX-License-Identifier` headers are
  intact. Upstream ships no `NOTICE` file, so Apache-2.0 §4(d) attaches nothing
  further.
- **Modified: no.** Every file is byte-for-byte upstream, so Apache-2.0 §4(b)'s
  changed-files notice is not triggered. That determination — and how to
  re-verify it in one command — is recorded in
  [`.../vendor/MODIFICATIONS.md`](native/p4/moy_dsi/vendor/MODIFICATIONS.md).
- The board bring-up that *uses* the driver (`modmoy_dsi.c`,
  `micropython.cmake`, one level up) is Moybyte's own work.

### 2.3a Espressif `esp_lcd_jd9365`, Guition's build — the Guition P4 panel driver

`native/p4/moy_dsi/vendor_jd9365/`

The JD9365 MIPI-DSI controller driver for the Guition JC8012P4A1C's 10.1″
800×1280 glass, taken from the factory demo Guition publishes for that exact
board (`1-Demo/arduino-examples/esp32p4_lvgl_v8/src/lcd/` in the vendor's zip,
mirrored at
<https://github.com/DevinWatson/10.1-inch-ESP32P4-Xiaozhi-ESP32-C6-JC8012P4A1C_I_W_Y>).

- **Upstream:** Espressif's `esp_lcd_jd9365` component (esp-iot-solution,
  `components/display/lcd/esp_lcd_jd9365`) with Guition's own edits — the
  panel's initialization table, a 1500 Mbps 2-lane bus config and the
  800×1280 60 Hz DPI timing macro. Guition publishes no change statement;
  `vendor_jd9365/MODIFICATIONS.md` is Moybyte's record of provenance.
- **Licence:** Apache-2.0 — both files carry Espressif's SPDX header intact
  (© 2024 Espressif Systems (Shanghai) CO LTD). The full licence text is the
  one retained beside the EK79007 driver (`vendor/license.txt`).
- **Modified: no.** Byte-for-byte as published; `modmoy_dsi.c` overrides
  `num_fbs` and `use_dma2d` at runtime rather than editing the macros.

### 2.3b Silead GSL3680 touch firmware — the Guition P4 glass

`firmware/guition_jc8012p4a1c/modules/gsl_fw_jc8012.py`

The GSL3680 is a RAM-loaded touch controller: the host uploads its firmware
over I²C after every reset. Silead publishes it only through panel vendors,
and this table came from the same Guition demo above (`GSLX680_FW[]` in
`src/touch/esp_lcd_gsl3680.h`), transcribed to a `bytes` literal by
`tools/gen_gsl_fw.py` (offset + 32-bit value per record, nothing else
changed). Every project driving this glass redistributes the same table
(ESPHome's `gsl3680` component included); Silead ships no licence with it.
Moybyte's driver (`device/gsl3680.py`) is its own work and does NOT carry
Silead's GPL `gsl_point_id.c` finger-tracking algorithm.

### 2.4 ST7789 init register values — the T-Deck panel on mainline

`firmware/lilygo_t_deck_plus_mainline/native/moy_lcd/modmoy_lcd.c`
(the `MOY_LCD_INIT` table and the MADCTL derivation above it)

ESP-IDF's own ST7789 driver sends only `SLPOUT` / `MADCTL` / `COLMOD` and offers
no hook to extend that, so the porch, gate/VCOM/power and gamma registers this
panel needs have to be sent by us. Rather than re-derive them, the values are
the ones already proven on this exact glass by the fork build.

- **Upstream:** `api_drivers/common_api_drivers/display/st7789/_st7789_init.py`
  in [`lvgl-micropython/lvgl_micropython`](https://github.com/lvgl-micropython/lvgl_micropython),
  at commit `14ad6ce2c5555272398debeff77b69021ca7ddda` — the commit the deleted
  fork build (removed 2026-08-17) had pinned.
- **Licence:** MIT, © 2024–2025 Kevin G. Schlosser. The same project is already
  listed in §5 as a build-time upstream of the shipping T-Deck image; this is
  the one place its source is *carried* rather than fetched.
- **Modified: yes** — this is a transcription, not a copy. The Python file is a
  method that calls `self.set_params()`; here it is a static C table sent with
  `esp_lcd_panel_io_tx_param()`. Three deliberate differences, all noted at the
  code: the entries `esp_lcd_panel_init()` already sends are dropped, the
  I80-8-lane-only `RAMCTRL` byte-swap branch is dropped (this is an SPI panel),
  and its `import lvgl` — present only for orientation constants — is resolved
  into the single MADCTL byte `0x68` via `swap_xy` + `mirror`, so nothing here
  depends on LVGL.
- The panel module around the table (SPI bus, esp_lcd wiring, the framebuffers,
  the banded SRAM-bounce flush) is Moybyte's own work.
- The Guition board's panel needs the same kind of entry for the same kind of
  reason; it is §2.6.

### 2.5 esptool-js — the website's board flasher

`site/vendor/esptool-js/bundle.js`

Espressif's JavaScript esptool. It is what the project site's "Put it on a
board" section uses to write a firmware image to a board over Web Serial.

- **Upstream:** [`espressif/esptool-js`](https://github.com/espressif/esptool-js)
  v0.6.0, the published `bundle.js` from
  [`esptool-js@0.6.0`](https://www.npmjs.com/package/esptool-js) on npm. That
  single file is the project's own rollup bundle: it already contains pako
  (MIT AND Zlib, © 2014–2017 Vitaly Puzrin and Andrey Tupitsin) and the
  per-chip flasher stubs, and it fetches nothing at runtime.
- **Licence:** Apache-2.0, © Espressif Systems (Shanghai) CO LTD. Full text is
  retained beside it at [`site/vendor/esptool-js/LICENSE`](site/vendor/esptool-js/LICENSE).
  Upstream ships no `NOTICE` file, so Apache-2.0 §4(d) attaches nothing further.
- **Modified: no** — byte-for-byte the published artifact, so §4(b)'s
  changed-files notice is not triggered. Re-verify, and update, with:
  `curl -sO https://unpkg.com/esptool-js@<version>/bundle.js`.
- **Why vendored rather than loaded from a CDN:** the page has to work as one
  self-contained thing, and this is a tool that writes to hardware — what it
  runs should be a file in this repository, reviewed at a pinned version, not
  whatever a CDN serves that day.
- The flasher around it (`site/flash.js`, the board table in `site/build.py`)
  is Moybyte's own work.

### 2.6 AXS15231B init register values — the Guition panel

`firmware/guition_jc3248w535/native/moy_axs/modmoy_axs.c`
(the `MOY_AXS_INIT` table and the INIT note above it)

The §2.4 problem again, one board over, and worse: there is no public
AXS15231B datasheet worth the name and ESP-IDF ships no driver for the part,
so the three vendor commands this panel needs before it will accept pixels
cannot be re-derived here. The only provenance available is a sequence already
proven on this exact glass — ESPHome's `AXS15231` model, which the owner's
working ESPHome build for this board runs — plus the standard DCS tail that
component generates around it (`COLMOD` 0x55, `MADCTL`, `INVOFF`, `SLPOUT`,
`DISPON`).

- **Upstream:** [`esphome/esphome`](https://github.com/esphome/esphome) —
  `esphome/components/qspi_dbi/models.py`, the `DriverChip("AXS15231")` block
  (`0xBB …5A A5` / `0xC1 0x33` / `0xBB …00 00`).
- **Licence:** MIT, © 2019 ESPHome. Their licence is a split one and the
  direction matters: the **C++/runtime** files are GPLv3 and *the Python
  codebase and everything else is MIT*. This table comes from a `.py` file.
- **Modified: yes** — a transcription, as §2.4 is. An ESPHome Python component
  that emits writes becomes a static C command table sent over raw
  `spi_master`, and the tail is spelled out rather than generated. Register
  *values* for a part with no datasheet are hardware facts; nothing expressive
  crossed.
- The panel module around the table (the QSPI bus, the whole frame under one
  CS assertion, the band/bounce/kick-pump-drain flush, the landscape
  rotate-gather) is Moybyte's own work — see the module header for why that C
  body is not shared with `moy_lcd`'s.
- **The touch half is a protocol constant, not a table.** `device/axs_touch.py`
  writes the same 11-byte read-touchpad command ESPHome's
  `axs15231_touchscreen.cpp` declares — a C++ file, so GPLv3 on their side of
  the split. What crossed is that byte string: the command a chip with no
  datasheet answers to. No code did — the poller, its no-news contract and the
  idle-filler lift detection are Moybyte's, written against the observed
  behaviour of the part (`firmware/guition_jc3248w535/README.md` records it).

### 2.7 Jet — the compiled Jet carts' rasteriser

`ports/jet/jet/`

The compiled (`"runtime": "wasm"`) tier's Jet carts, `ports/jet/teapot.moy/`
and `ports/jet/esp88.moy/` -- moybyte-org/carts' Jet carts, copied here by
`tools/vendor_jet_carts.py` for the tests and guards -- compile Jet into their
modules; nothing in any firmware image does. A module is a build product
(`tools/jet_cart.py`) and is never committed.

- **Upstream:** Jet — <https://github.com/CubeCoders/Jet>, at the commit that
  JetExamples (<https://github.com/CubeCoders/JetExamples>) carries as its
  `components/Jet` submodule at the JetExamples commit `ports/jet/jet_vendor.json`
  records. `tools/vendor_jet.py` copies the sources the carts compile and the
  headers they include, under upstream's paths.
- **Licence:** MIT. Copyright (c) 2026 CubeCoders Limited. Full text:
  [`ports/jet/jet/LICENSE`](ports/jet/jet/LICENSE), and each cart carries it in
  its own `LICENSES.txt`.
- **Modified: no.** `tests/test_jet_vendor.py` fails on any edit to the copy.
  The teapot builds Jet twice into one module, the second time with its
  namespaces renamed on the compiler's command line; no file changes for it.
- A cart's own `src/` is MIT (`LICENSE.md`), except what ports JetExamples
  code and says so, with CubeCoders' copyright beside ours: the teapot's
  `src/scene.cpp` (from `esp32-lighting-teapot/main/Teapot.hpp`), and ESP 88's
  `src/main.cpp` and `src/Assets.hpp`, which drive the film and declare its
  artwork.
- **What the built module also contains:** compiled code from wasi-sdk 24's
  wasi-libc and LLVM's libc++/libc++abi (§6.4's toolchain). A built cart is
  not published by this repository (moybyte-org/carts publishes them); before one ships
  in a product, its `LICENSES.txt` carries those libraries' notices as well
  (`ports/jet/README.md`, "Seeding").

---

## 3. Data and assets

### 3.1 The console font — `font_petme128_8x8`

`runtime/font.py`

The console's 8×8 text face is MicroPython's built-in `framebuf` font,
extracted byte-for-byte (96 glyphs, ASCII `0x20`–`0x7F`, 8 bytes per glyph,
column-major, LSB = top row). Host and device render the *same* pixels because
they read the *same* bytes; the build freezes this data into the device
`moy_font` blob, and `tools/make_petme_webfont.py` turns it into the woff2
inlined by the marketing site, which now lives in its own repository.

- **Upstream:** MicroPython, `extmod/font_petme128_8x8.h` —
  <https://github.com/micropython/micropython>
- **Licence:** MIT. **Copyright (c) 2013, 2014 Damien P. George.**
- **Modified: no.** The bytes are identical to upstream; only the container
  changed (a C array became a Python `bytes` literal). The generated webfont is
  a *derivative*: same glyph shapes, with proportional advances and a redrawn
  `'`/`"`. `site/petme128.woff2` is that webfont, inlined into the project site
  so the page's display type is the console's own font; the MIT notice above
  covers it.

The MIT permission notice, reproduced in full so it travels with the data:

> The MIT License (MIT)
>
> Copyright (c) 2013, 2014 Damien P. George
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

### 3.2 The MOY64 palette — indices 0–15

`runtime/palette.py` (`_BASE16`, and the `NAMES` map)

MOY64's first sixteen entries are PICO-8's base sixteen RGB values, reproduced
exactly, and the sixteen colour names (`black`, `dark_blue`, `dark_purple`,
`dark_green`, `brown`, `dark_grey`, `light_grey`, `white`, `red`, `orange`,
`yellow`, `green`, `blue`, `indigo`, `pink`, `peach`) are PICO-8's names.
Indices 16–63 are Moybyte's own curated extension. The reuse is deliberate:
converted PICO-8 carts keep their exact colours, and the palette is a familiar
one for the culture this console is aimed at.

- **Origin:** PICO-8, by [Lexaloffle Games](https://www.lexaloffle.com/pico-8.php).
- **Licence: CC-0.** Lexaloffle grants this explicitly. From the
  [PICO-8 FAQ](https://www.lexaloffle.com/pico-8.php?page=faq) — *"Can I use
  the PICO-8 palette and/or font for something?" → "Yes, please do. The palette
  and font are both available under a
  [CC-0](https://creativecommons.org/publicdomain/zero/1.0/) license."*
- **Modified: no** (values reproduced verbatim; extended, not altered).

Two honest notes rather than one convenient one. First, a bare list of RGB
values is widely understood not to be a copyrightable work in the first place,
so we would not owe a permission notice here in any event. Second, we do not
lean on that argument, because we do not have to: the rightsholder has released
the palette under CC-0, which asks for nothing. Credit is given here because
the origin is a true and material fact about the data, and because a project
that openly borrows a well-loved palette should say so.

PICO-8's *name and logo* are **not** covered by that grant and are not used as
Moybyte branding. References to PICO-8 in this repository are descriptive.
Moybyte is not affiliated with or endorsed by Lexaloffle Games.

### 3.3 Pixelarticons — the button icon vocabulary

`runtime/chrome.py` — the `_GLYPHS` table (12×12 1-bit bitmaps) and the two
sibling glyph blocks below it.

The pre-literate icon vocabulary (run, save, close, edit, home, gear, …) was
traced down to a 12×12 grid from the Pixelarticons set and hand-cleaned for
legibility at button size.

- **Upstream:** Pixelarticons by Gerrit Halfmann —
  <https://pixelarticons.com> / <https://github.com/halfmage/pixelarticons>
- **Licence:** MIT. Copyright (c) 2019 Gerrit Halfmann.
- **Modified: yes** — retraced at 12×12 and hand-adjusted; several glyphs
  (`map`, `blocks`, `scene`, `turtle`, `rabbit`) are Moybyte originals drawn in
  the same style.
- `runtime/chrome.py` is staged into both firmware trees at build time, so
  these shapes ship inside every firmware image. This entry is that
  distribution's notice; the source comment above `_GLYPHS` is the in-code one.

The separate 16×16 top-bar icon art (`_ICON_ART` in the same file, persisted as
`system_icons.moygfx`) is hand-authored Moybyte work.

### 3.3a The Utah teapot — the showcase cart's model

`ports/jet/teapot.moy/teapot.obj`

Martin Newell's teapot as JetExamples' `esp32-lighting-teapot` renders it:
that example's generated mesh (`main/TeapotMesh.hpp`, 822 vertices with smooth
normals, 1,560 triangles), which its generator evaluates from the teapot
control points freeglut ships (`fg_teapot_data.h`, which the example keeps
under its `assets`).

- **Upstream:** <https://github.com/CubeCoders/JetExamples> at the commit
  `ports/jet/jet_vendor.json` records; the control points are freeglut's
  (<https://github.com/freeglut/freeglut>, `src/fg_teapot_data.h`).
- **Licence:** the example is MIT (CubeCoders Limited); the teapot data carries
  freeglut's X11-style permission notice, reproduced in the cart's
  [`LICENSES.txt`](ports/jet/teapot.moy/LICENSES.txt).
- **Modified: converted.** `tools/vendor_jet.py` derives it -- the example's
  integer vertices and normals as OBJ, at the scales Jet's loader multiplies
  back by, so the mesh the cart loads is the example's, value for value -- into
  moybyte-org/carts, whose copy of the cart is vendored here, and
  `tests/test_jet_vendor.py` re-derives it and compares.

### 3.3b ESP 88 — the film's code and artwork

`ports/jet/examples/`, `ports/jet/esp88.moy/assets.bin`

JetExamples' `esp32-neon-film`, CubeCoders' film: its code
(`main/Film.hpp`, `World.hpp`, `City.hpp`, `Vehicle.hpp`, `RoadTrack.hpp`,
`firmware/JetConfig.hpp`) under upstream's paths, and its artwork. That
example's README ("Licence and assets" in JetExamples) calls the car and city
original procedural work: the code builds every mesh, and the example's own
Python generators made the textures and the closing credits with Pillow. The signs, dashboard and credits
carry text those scripts rasterised from a system font; the pixels are the
example's, and no font file is part of them or of this repository. The
example's generated concept images (`references/`) are not taken.

- **Upstream:** <https://github.com/CubeCoders/JetExamples> at the commit
  `ports/jet/jet_vendor.json` records.
- **Licence:** MIT, CubeCoders Limited; [`ports/jet/examples/LICENSE`](ports/jet/examples/LICENSE),
  and the cart's [`LICENSES.txt`](ports/jet/esp88.moy/LICENSES.txt).
- **Modified:** `World.hpp` by one line, which makes the film's frame size a
  default the build overrides (`tools/vendor_jet.py`'s `PATCHES`). The
  artwork (`main/Assets.hpp` and `main/CreditMask.hpp` upstream) is repacked
  into `assets.bin` for the cart to read; the glow's falloff and the credits
  are scaled to two thirds, the credits box-filtered from the example's finest
  mask. `tests/test_jet_vendor.py` re-derives all of it from the clones.

### 3.4 Board pin assignments — LilyGO T-Deck

`docs/boards/lilygo_t_deck_plus.md` and the constants derived from it in
`firmware/lilygo_t_deck_plus_mainline/modules/tdeck_panel.py` /
`tdeck_display.py`. (It first landed in the `.moyproj` SDK, deleted
2026-07-31; the board doc is the surviving citation.)

GPIO numbers, the I²C keyboard address and the SPI pin map were transcribed
from LilyGO's own board files —
<https://github.com/Xinyuan-LilyGO/T-Deck> (`boards/T-Deck.json` and
`examples/UnitTest/utilities.h`), which `docs/boards/lilygo_t_deck_plus.md`
names directly.

These are hardware facts about a physical product, not expressive work, and no
upstream code was copied — but the source is named here because the repository
names it, and a reader deserves to know where the numbers came from.

### 3.5 Board pin assignments — Guition JC3248W535

`firmware/guition_jc3248w535/board.toml` and the constants derived from it in
`firmware/guition_jc3248w535/native/moy_axs/` and `device/axs_touch.py`.

Guition publishes no board file worth transcribing, so the pins came from a
working **ESPHome** definition for this board that the owner already ran on
the physical unit (`~/Documents/Work/esphome/JC3248W535.yaml`, not in this
repo): QSPI clk/data/cs, the AXS15231 touch I²C pins, the backlight and
battery-ADC GPIOs. That file is the owner's own configuration, not upstream
work; ESPHome's contribution to it is the component vocabulary, covered by
§2.6.

Same reasoning as §3.4: GPIO numbers and an I²C address are hardware facts
about a physical product, and none of the *tuning* beside them in that YAML
was copied — that is deliberately re-derived on this glass, because per-board
verdicts do not transfer (`sdkconfig.board` carries the argument).

---

## 4. Formats, protocols and behavioural parity — implemented, not copied

Listed so a reviewer does not have to wonder. Each of these reproduces a
published format, protocol or behaviour; none contains third-party code.

- **PICO-8 `.p8` / `.p8.png` cart format** (`tools/p8_import.py`, vendored from
  the project's own [moy-spec](https://github.com/moybyte-org/moy-spec) — same
  authors, not third-party, so it is not listed in §2) — the section layout
  (`__gfx__`, `__gff__`,
  `__map__`, `__sfx__`, `__music__`), the pre-0.2.0 `:c:` compression lookup
  table, and the steganographic 2-bit-per-channel PNG packing are format
  constants. The implementation is stdlib-only and written from the format
  description. The PICO-8 music row-length rule was *verified against*
  [zepto8](https://github.com/samhocevar/zepto8)'s observable behaviour (the
  wiki's rule is wrong); no zepto8 or picotool code was used. Both projects are
  permissively licensed in any case (zepto8 WTFPL, picotool MIT).
- **PICO-8 audio parity** (`runtime/audio.py`) — waveform numbering and the
  per-note effect column follow PICO-8's numbering so imported carts sound
  right. The synthesis is Moybyte's own.
- **PNG decoding** (`tools/p8_import.py`) and **PNG encoding**
  (`tools/render_icons.py`) — hand-written per the PNG specification, with
  `zlib` from the standard library for DEFLATE. The Paeth predictor is the
  spec's own pseudocode.
- **SHA-256** (`moy_ota.py`, `tools/gen_ota_manifest.py`) — `hashlib`.
- **No third-party JavaScript.** `firmware/web_runner/page_core.html`,
  `firmware/web_runner/page_tail.js` and `firmware/web_runner/harness.mjs`
  contain only hand-written code, with no CDN references and no bundled
  libraries.

---

## 5. What the build pulls in — bundled into distributed binaries

None of the following is committed to this repository: the build scripts clone
each one on demand into gitignored working directories. They are listed because
the **binaries the build produces** — the firmware `.bin` images and the
WebAssembly web runner — contain compiled code from them, and those artifacts
carry the upstreams' obligations wherever they are published.

Since the project site gained its board flasher, "published" covers two more
channels: the rolling `firmware-latest` release (all three boards' images,
replaced per board as they are rebuilt) and the website itself, which serves its
own copy under `_site/firmware/` for a browser to write. The per-board tables
below — §5.2 (P4), §5.3 (T-Deck) and §5.5 (Guition) — apply to both.

### 5.1 LilyGO T-Deck Plus on the lvgl_micropython fork — HISTORICAL

The fork build, **deleted 2026-08-17**. Nothing produces these images any more:
the T-Deck ships from §5.3's mainline build, and the path this section used to
be titled with is that build's, not this one's. The table stays because images
built this way *were* published to `firmware-latest` and are on boards in the
world, and those binaries carry these obligations wherever they went.

| Project | Upstream | Licence |
|---|---|---|
| lvgl_micropython | <https://github.com/lvgl-micropython/lvgl_micropython> (pinned to commit `14ad6ce2`) | MIT, © 2024–2025 Kevin G. Schlosser |
| MicroPython | <https://github.com/micropython/micropython> | MIT, © 2013–2026 Damien P. George |
| micropython-lib | <https://github.com/micropython/micropython-lib> | MIT |
| LVGL | <https://github.com/lvgl/lvgl> | MIT |
| pycparser | <https://github.com/eliben/pycparser> | BSD-3-Clause |
| Berkeley DB 1.85 | <https://github.com/micropython/berkeley-db-1.xx> (MicroPython's `btree`) | BSD-style (4.4BSD, Regents of the University of California) |
| ESP-IDF | <https://github.com/espressif/esp-idf> | Apache-2.0 |

### 5.2 Waveshare ESP32-P4 7B (`firmware/esp32_p4_wifi6_touch_lcd_7b/build.sh`)

| Project | Upstream | Licence |
|---|---|---|
| MicroPython v1.28.0 | <https://github.com/micropython/micropython> | MIT |
| ESP-IDF v5.5.1 | <https://github.com/espressif/esp-idf> | Apache-2.0 |

### 5.3 LilyGO T-Deck Plus on mainline (`firmware/lilygo_t_deck_plus_mainline/build.sh`)

The same board as §5.1, built the way §5.2 is. Note what is *not* in this list
next to §5.1's: LVGL, its MicroPython binding, pycparser and the fork itself.

| Project | Upstream | Licence |
|---|---|---|
| MicroPython v1.28.0 + micropython-lib | <https://github.com/micropython/micropython> | MIT |
| Berkeley DB 1.85 | <https://github.com/micropython/berkeley-db-1.xx> (MicroPython's `btree`) | BSD-style (4.4BSD, Regents of the University of California) |
| ESP-IDF v5.5.1 | <https://github.com/espressif/esp-idf> | Apache-2.0 |

The ST7789 register values this build sends are carried in-tree and are covered
by §2.4, not by this table.

### 5.4 Web runner, MicroPython-WASM (`firmware/web_runner/build.sh`)

| Project | Upstream | Licence |
|---|---|---|
| MicroPython v1.28.0 + micropython-lib | <https://github.com/micropython/micropython> | MIT |
| Emscripten / emsdk | <https://github.com/emscripten-core/emsdk> | MIT (with NCSA for legacy components) |
| Lua 5.4.7 | vendored, §2.1 | MIT |

The published `micropython.wasm` / `micropython.mjs` bundle therefore contains
MicroPython, Emscripten runtime support and Lua — all MIT — and any page
hosting them should carry those notices.

### 5.5 Guition JC3248W535, ESP32-S3 (`firmware/guition_jc3248w535/build.sh`)

The third board, built the way §5.2 and §5.3 are — the same
`tools/esp32_build_lib.sh` clones the same two upstreams, stages the shared
`native/` modules and this board's own `native/moy_axs`, and freezes the
console.

| Project | Upstream | Licence |
|---|---|---|
| MicroPython v1.28.0 + micropython-lib | <https://github.com/micropython/micropython> | MIT |
| Berkeley DB 1.85 | <https://github.com/micropython/berkeley-db-1.xx> (MicroPython's `btree`) | BSD-style (4.4BSD, Regents of the University of California) |
| ESP-IDF v5.5.1 | <https://github.com/espressif/esp-idf> | Apache-2.0 |

The AXS15231B register values this build sends are carried in-tree and are
covered by §2.6, not by this table.

### 5.6 Guition JC8012P4A1C, ESP32-P4 (`firmware/guition_jc8012p4a1c/build.sh`)

The Waveshare P4's build (§5.2) on the second ESP32-P4 board: the same
mainline MicroPython v1.28.0, ESP-IDF v5.5.1, ESP-Hosted 2.12.12 and
managed components, the same patch ladder, the shared `native/p4/` modules
(§2.3, §2.3a) and this board's GSL3680 firmware (§2.3b) frozen into the
image. Nothing else is pulled in.

### 6.4 `experiments/wasm_aot/build.sh` (experiment only, nothing shipped)

| Project | Upstream | Licence |
|---|---|---|
| WAMR (wasm-micro-runtime) 2.4.5, taken from Moybyte's fork at branch `moybyte-2.4.5` (the esp-idf platform work over the upstream tag that §2.2 describes, at the same pinned commit), plus upstream's prebuilt `wamrc` release binary | <https://github.com/moybyte-org/wasm-micro-runtime> (fork of <https://github.com/wasm-micro-runtime/wasm-micro-runtime>) | Apache-2.0 WITH LLVM-exception |
| Espressif's LLVM fork, branch `xtensa_release_18.1.2`, built once by the toolchain script, with one patch of ours to its Xtensa backend (`experiments/wasm_aot/toolchain/llvm-xtensa-extui.patch`), to give `wamrc` its Xtensa and RISC-V backends; never vendored | <https://github.com/espressif/llvm-project> | Apache-2.0 WITH LLVM-exception |
| wasi-sdk 24, the clang/wasi-libc/libc++ toolchain `experiments/wasm_aot/doom/build_wasm.sh`, `build_cart.py` and the showcase cart's `tools/jet_cart.py` compile with; a gitignored download (`build_cart.py` and `jet_cart.py` fetch the release tarball by sha256 when it is absent), never vendored | <https://github.com/WebAssembly/wasi-sdk> | Apache-2.0 WITH LLVM-exception (wasi-libc: Apache-2.0 / MIT) |
| doomgeneric (id Software's DOOM, ozkl's portable fork), the engine `build_wasm.sh` stages from a gitignored checkout the developer fetches and `build_cart.py` fetches at a pinned commit into its gitignored cache, checked by the sha256 of its tree; never vendored | <https://github.com/ozkl/doomgeneric> | **GPL-2.0** |
| DOOM shareware IWAD `doom1.wad` v1.9 (1993), a gitignored file the developer obtains; `build_cart.py` fetches Debian's `doom-wad-shareware` source package (<http://deb.debian.org/debian/pool/non-free/d/doom-wad-shareware/>) into its gitignored cache and checks the tarball and the WAD by sha256; never vendored, never redistributed | id Software | id Software Limited Use licence: free unmodified copies only, no consideration, no derivative works |

**Doom lives in the carts repository, not in this one's products.** The
cart's glue and recipe are GPL-2.0-or-later in `carts/doom/` of
<https://github.com/moybyte-org/carts>, each cart there under the licence in
its own folder, which publishes built carts as a free, opt-in download with
their complete source. The shareware WAD is never in this repository or in
that one's git history or releases; that repository's Pages site gives it
away, free and unmodified, beside id's terms (owner, 2026-10-03), which allow
free copies but no consideration and no derivative works. Its installers
show those terms before fetching it, from that copy or from Debian's. Doom
and its WAD are never seeded, preloaded, sold with a console, or shipped in a
product image, exactly as §7 says of Celeste. The
spike's glue under `experiments/wasm_aot/doom/` is the same GPL derivative
and is built only locally. No `.wasm`, `.aot`, `.wad` or built cart is
tracked by this repository.

`experiments/wasm_aot/core6502.c` and `spike6502.lua` are Moybyte's own
hand-written 8-opcode benchmark cores, not derived from any emulator.

### 6.5 Patches we apply to upstream sources

`patches/*.patch` (the two `p4_*` ones were the Waveshare's own
`patches/` until 2026-09-06, when both ESP32-P4 boards started applying them)
are Moybyte-authored diffs against MicroPython and ESP-IDF (I²C GIL release, `MICROPY_OBJ_REPR_C`
floats, native-code arena reclaim, T-Deck early board init, SPI PSRAM TX DMA,
`esp_lcd` no-acquire `tx_color`, PSRAM temperature retune, DSI underrun hook,
BLE-HID notification fast path). Being diffs, each carries a few lines of
upstream context — MicroPython (MIT) and ESP-IDF (Apache-2.0) respectively.
The `firmware/web_runner/build.sh` equivalents are applied in place with `sed`
rather than stored as `.patch` files.

---

## 6. Development and optional dependencies

Installed from PyPI; never vendored, never redistributed by this repository.

| Package | Used for | Licence |
|---|---|---|
| pytest | test suite (`dev`) | MIT |
| pillow | GIF export in `tools/make_site_gifs.py` and `tools/make_feature_tiles.py` (`dev`) | MIT-CMU / HPND |
| pygame | the simulator window (`sim`), imported lazily | **LGPL-2.1** |
| esptool | flashing a board (`device`); `tools/esptool_no_modem.py` monkeypatches its reset strategy at runtime | **GPL-2.0-or-later** |
| pyserial | serial I/O (`device`) | BSD-3-Clause |
| fontTools | `tools/make_petme_webfont.py` only (install on demand; in no extra) | MIT |

`pygame` (LGPL) and `esptool` (GPL) are the only copyleft-licensed software the
project touches. Neither is copied into this repository and neither is part of
the console or firmware: `pygame` is dynamically imported by the desktop
simulator, and `esptool` is a standalone flashing tool a developer installs and
runs. No copyleft-licensed code is combined into, or distributed with, any
Moybyte binary.

---

## 7. Ported carts

`tools/import_p8.py` (and the moy-spec CLI's `moy port` / `moy demo`) can
convert a PICO-8 cart into a `.moy` cartridge. **A ported cart is a derivative
work of its original and carries the original's licence, not this
repository's.** PICO-8 BBS carts default to CC BY-NC-SA 4.0.

No ported cart is committed here. `ports/celeste.moy` — *Celeste* (PICO-8,
2016) by Maddy Thorson & Noel Berry — is used as a Lua-runtime conformance
test and is gitignored on purpose; `ports/README.md` records its attribution
and how to regenerate it locally. The moy-spec CLI (`moy demo`, or `moy port` on a
`.p8.png`) converts it from a cart the user downloads by hand, printing the
licence notice first — this repo's own `moy.py` was deleted 2026-08-25. It must not ship in a product
image, a seed set, or anything commercial.

Cartridges *you* author are yours; see LICENSE.md.

---
