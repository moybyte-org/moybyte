# Third-party components

Moybyte is licensed as described in [LICENSE.md](LICENSE.md) — FSL-1.1-MIT for
the console and firmware, MIT for the spec player's compiled artifacts. Some files in this
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
| Lua 5.4.7 (device VM) | `firmware/lilygo_t_deck_plus_micropython/native/moy_lua/lua/` | [lua.org](https://www.lua.org/) | MIT | **Yes** — documented |
| Lua 5.4.7 (measurement spike) | `experiments/lua_bridge/components/lua/` | [lua.org](https://www.lua.org/) | MIT | No |
| `esp_lcd_ek79007` panel driver | `firmware/esp32_p4_wifi6_touch_lcd_7b/native/moy_dsi/vendor/` | [espressif/esp-iot-solution](https://github.com/espressif/esp-iot-solution) | Apache-2.0 | No |
| esptool-js 0.6.0 (the site's board flasher) | `site/vendor/esptool-js/` | [espressif/esptool-js](https://github.com/espressif/esptool-js) | Apache-2.0 | No |
| `font_petme128_8x8` glyph data | `runtime/font.py`; derived webfont `site/petme128.woff2` | [MicroPython](https://github.com/micropython/micropython) | MIT | No (re-encoded) |
| PICO-8 base palette + colour names | `runtime/palette.py` (`_BASE16`, `NAMES`) | [PICO-8 / Lexaloffle](https://www.lexaloffle.com/pico-8.php) | CC-0 | No |
| Pixelarticons icon shapes | `runtime/chrome.py` (`_GLYPHS` and siblings) | [halfmage/pixelarticons](https://github.com/halfmage/pixelarticons) | MIT | **Yes** — retraced |
| T-Deck pin assignments | `docs/boards/lilygo_t_deck_plus.md` | [Xinyuan-LilyGO/T-Deck](https://github.com/Xinyuan-LilyGO/T-Deck) | facts; source cited | Transcribed |

Build-time upstreams that end up inside shipped binaries are in §5.
Development and optional dependencies that are *not* redistributed are in §6.

---

## 2. Vendored source

### 2.1 Lua 5.4 — the cart VM

`firmware/lilygo_t_deck_plus_micropython/native/moy_lua/lua/`

The `moy_lua` native module (issue #67) embeds a complete Lua interpreter so a
cart can declare `"runtime": "lua"`. The same directory is staged into the
ESP32-P4 and WebAssembly builds, so this one copy is the source for all three
targets.

- **Upstream:** Lua 5.4.7 — <https://www.lua.org/>, tarball
  <https://www.lua.org/ftp/lua-5.4.7.tar.gz> (the `src/` directory).
- **Licence:** MIT. Copyright © 1994–2024 Lua.org, PUC-Rio.
  Full text: [`.../moy_lua/lua/COPYRIGHT`](firmware/lilygo_t_deck_plus_micropython/native/moy_lua/lua/COPYRIGHT).
- **Modified: yes.** Two changes, both listed in
  [`.../moy_lua/lua/MODIFICATIONS.md`](firmware/lilygo_t_deck_plus_micropython/native/moy_lua/lua/MODIFICATIONS.md):
  a `#pragma GCC optimize("O2")` block added to 32 `.c` files, and
  `LUA_32BITS` flipped from `0` to `1` in `luaconf.h`. Nothing else differs
  from upstream; the tarball's standalone `lua.c` / `luac.c` / `lua.hpp` /
  `Makefile` are simply not vendored.
- `modmoy_lua.c` and `micropython.cmake` in the parent directory are Moybyte's
  own bridge code, not Lua's, and are under this repository's licence.

### 2.2 Lua 5.4 — the measurement spike

`experiments/lua_bridge/components/lua/`

The `#6`/`#67` benchmark that decided whether a Lua tier was worth building. It
is deliberately kept on **stock** Lua so it measures a stock VM.

- **Upstream / licence:** identical to §2.1.
  Full text: [`.../components/lua/COPYRIGHT`](experiments/lua_bridge/components/lua/COPYRIGHT).
- **Modified: no.** Every `.c`/`.h` file is byte-for-byte upstream. The only
  Moybyte file in that directory is the added `CMakeLists.txt` ESP-IDF
  component wrapper. See
  [`.../components/lua/MODIFICATIONS.md`](experiments/lua_bridge/components/lua/MODIFICATIONS.md).

### 2.3 Espressif `esp_lcd_ek79007` — the P4 panel driver

`firmware/esp32_p4_wifi6_touch_lcd_7b/native/moy_dsi/vendor/`

The EK79007 MIPI-DSI controller driver for the Waveshare 7″ board (issue #58).

- **Upstream:** `espressif/esp_lcd_ek79007` v2.0.2~1 from the
  [ESP Component Registry](https://components.espressif.com/components/espressif/esp_lcd_ek79007),
  sourced from `components/display/lcd/esp_lcd_ek79007` in
  <https://github.com/espressif/esp-iot-solution> at commit
  `12f6ca1182ec48889b17ec570fadaaf267cb336e` (recorded in the vendored
  `idf_component.yml`).
- **Licence:** Apache-2.0, © 2023–2025 Espressif Systems (Shanghai) CO LTD.
  Full text is retained at
  [`.../vendor/license.txt`](firmware/esp32_p4_wifi6_touch_lcd_7b/native/moy_dsi/vendor/license.txt);
  the per-file `SPDX-FileCopyrightText` / `SPDX-License-Identifier` headers are
  intact. Upstream ships no `NOTICE` file, so Apache-2.0 §4(d) attaches nothing
  further.
- **Modified: no.** Every file is byte-for-byte upstream, so Apache-2.0 §4(b)'s
  changed-files notice is not triggered. That determination — and how to
  re-verify it in one command — is recorded in
  [`.../vendor/MODIFICATIONS.md`](firmware/esp32_p4_wifi6_touch_lcd_7b/native/moy_dsi/vendor/MODIFICATIONS.md).
- The board bring-up that *uses* the driver (`modmoy_dsi.c`,
  `micropython.cmake`, one level up) is Moybyte's own work.

### 2.4 esptool-js — the website's board flasher

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

### 3.4 Board pin assignments — LilyGO T-Deck

`docs/boards/lilygo_t_deck_plus.md` and the constants derived from it in
`firmware/lilygo_t_deck_plus_micropython/modules/tdeck_board.py` /
`tdeck_display.py`. (The transcription originally landed in the `.moyproj`
SDK's `moybyte_cli/boards.py` as `BOARD_PROFILES`, with its own `sources` list;
that SDK was deleted on 2026-07-31 and git history has it. The board doc is the
surviving citation.)

GPIO numbers, the I²C keyboard address and the SPI pin map were transcribed
from LilyGO's own board files —
<https://github.com/Xinyuan-LilyGO/T-Deck> (`boards/T-Deck.json` and
`examples/UnitTest/utilities.h`), which `docs/boards/lilygo_t_deck_plus.md`
names directly.

These are hardware facts about a physical product, not expressive work, and no
upstream code was copied — but the source is named here because the repository
names it, and a reader deserves to know where the numbers came from.

---

## 4. Formats, protocols and behavioural parity — implemented, not copied

Listed so a reviewer does not have to wonder. Each of these reproduces a
published format, protocol or behaviour; none contains third-party code.

- **PICO-8 `.p8` / `.p8.png` cart format** (`tools/import_p8.py`) — the
  section layout (`__gfx__`, `__gff__`,
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
- **PNG decoding** (`tools/import_p8.py`) and **PNG encoding**
  (`tools/render_icons.py`) — hand-written per the PNG specification, with
  `zlib` from the standard library for DEFLATE. The Paeth predictor is the
  spec's own pseudocode.
- **WebSocket, RFC 6455** (`runtime/web_view_ws.py`,
  `firmware/lilygo_t_deck_plus_micropython/modules/moy_webserver.py`) —
  handshake and framing written from the RFC.
  `WS_GUID` is the RFC's magic constant. SHA-1 and Base64 come from the
  standard library.
- **SHA-256** (`moy_ota.py`, `tools/gen_ota_manifest.py`) — `hashlib`.
- **No third-party JavaScript.** `runtime/web_view_page.py`,
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
channels: the rolling `firmware-latest` release (both boards' images, replaced
per board as they are rebuilt) and the website itself, which serves its own copy
under `_site/firmware/` for a browser to write. §5.1 and §5.2 apply to both.

### 5.1 LilyGO T-Deck Plus, ESP32-S3 (`firmware/lilygo_t_deck_plus_micropython/build.sh`)

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

### 5.3 Web runner, MicroPython-WASM (`firmware/web_runner/build.sh`)

| Project | Upstream | Licence |
|---|---|---|
| MicroPython v1.28.0 + micropython-lib | <https://github.com/micropython/micropython> | MIT |
| Emscripten / emsdk | <https://github.com/emscripten-core/emsdk> | MIT (with NCSA for legacy components) |
| Lua 5.4.7 | vendored, §2.1 | MIT |

The published `micropython.wasm` / `micropython.mjs` bundle therefore contains
MicroPython, Emscripten runtime support and Lua — all MIT — and any page
hosting them should carry those notices.

### 6.4 `experiments/wasm_aot/build.sh` (experiment only, nothing shipped)

| Project | Upstream | Licence |
|---|---|---|
| WAMR (wasm-micro-runtime) `WAMR-2.4.5`, plus a prebuilt `wamrc` release binary | <https://github.com/bytecodealliance/wasm-micro-runtime> | Apache-2.0 WITH LLVM-exception |

`experiments/wasm_aot/core6502.c` and `spike6502.lua` are Moybyte's own
hand-written 8-opcode benchmark cores, not derived from any emulator.

### 6.5 Patches we apply to upstream sources

`firmware/lilygo_t_deck_plus_micropython/patches/*.patch` and
`firmware/esp32_p4_wifi6_touch_lcd_7b/patches/*.patch` are Moybyte-authored
diffs against MicroPython and ESP-IDF (I²C GIL release, `MICROPY_OBJ_REPR_C`
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
| pillow | GIF export in `tools/make_site_gifs.py` (`dev`) | MIT-CMU / HPND |
| pygame | the simulator window (`sim`), imported lazily | **LGPL-2.1** |
| esptool | flashing a board (`device`); `tools/esptool_no_modem.py` monkeypatches its reset strategy at runtime | **GPL-2.0-or-later** |
| pyserial | serial I/O (`device`) | BSD-3-Clause |
| lupa | host-side Lua carts (`runtime/lua_host.py`); optional, probed at import | MIT |
| fontTools | `tools/make_petme_webfont.py` only | MIT |

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
and how to regenerate it locally. `moy.py demo` downloads and converts it on
request, printing the licence notice first. It must not ship in a product
image, a seed set, or anything commercial.

Cartridges *you* author are yours; see LICENSE.md.

---
