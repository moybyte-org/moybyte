---
paths:
  - "firmware/web_runner/**"
  - "runtime/moy_sync.py"
  - "runtime/web_*.py"
  - "device/moy_web*.py"
  - "tools/*web*.py"
---

<!-- The wasm console, the sync RPC, and the two web modes. -->

### Third target: the web runner + the moy-spec repo (#151/#170)

`firmware/web_runner/` is the MicroPython-WASM build of the same console. Its
build script and `docs/moycore_direction.md` say how it works;
`docs/history/moycore_plan_2026-08.md` is the walked plan. What neither the code
nor those docs will warn you about:

- **The wasm RASTERIZES — the browser is not the GPU** (moycore stage 4). No board
  lever is reimplemented here: bounce pump, DPI ping-pong, GDMA, PPA and PSRAM
  pooling are probe-guarded and simply absent. **`blit_cover` is NOT optional on a
  565 system canvas** — `wallpaper._backdrop_blit` probes for it and otherwise
  expands a palette-INDEX buffer that does not exist, drawing nothing: a black
  desk with correct chrome on top, which is exactly what the first build did.
- **The bundle rides the firmware image on every board, and that is its ONLY
  source.** There is no copy on storage and no way to change what one board
  serves without reflashing it, so "which console is this board serving?" is
  answered by its firmware version alone. Changing a web build means
  `firmware/web_runner/build.sh`, then rebuild and reflash. An oversized image is
  a BUILD FAILURE on every board.
- **`worker.js` STATICALLY imports `moy_store.mjs`**, so it must be in
  `moy_webhost.ASSETS`: a board that does not serve it serves a console that
  cannot boot.
- **TWO WEB MODES, TOTAL, NO CROSSOVER.** Where a page is SERVED from decides
  where its carts live — a board-served page edits the BOARD's store, a page on
  a static host keeps them in the browser. **The mode is decided ONCE at boot,
  BEFORE the VFS is seeded, because it decides what the VFS is seeded FROM.**
  `GET /sync` is the marker a board serves, and a GET miss falls through to an
  EMPTY-batch POST — because a board running firmware older than that marker
  still ACCEPTS the batch, and reading it as "static host" would quietly strand a
  kid's edits in a browser.
- **The substrate is OPFS, not IndexedDB**: the ops ARE file writes at paths, so a
  cart folder stays a cart folder. No OPFS (private window, blocked site data,
  `file://`) runs in memory and **the page says so**; a quota failure requeues, and
  after three gives up ONCE and says that too.
- **The journal lives with the STORE OF RECORD** (owner call) — there is one
  durable journal per cart, where the cart durably lives, so a kid gets undo on
  both ends without a byte of history on the wire. **The wire predicate itself
  never moves**: `_skip` refuses journal paths and a board-mode batch is
  byte-identical to what it always was.
- **THE PIN GATES EVERYTHING** (owner call), reversing the earlier read-half-open
  design: handing any device on the WiFi a child's whole cart store for the asking
  was the thing being fixed. Only the boot assets and `GET /sync` are open, by
  necessity. **A GET carries its pin the only place a GET can**, so
  `moy_webserver.parse_request` stopped stripping query strings — it was spending
  the credential before any handler saw it.
- **The #108 user files ride the same protocol as a SECOND root**, stamped
  `{"v": 2, "root": "files"}` — the bump is what makes a board flashed before it
  REFUSE the batch instead of writing `drawings/…` into its carts store. A files
  path must start with a `FILE_KINDS` kind, which is the one rule keeping
  `.history/` and `trash/` home in both directions.
- **THE PAGE IS THE SERVING BOARD'S UPDATE SURFACE** (#41/#53, 2026-08-29), and
  what it does depends on whether that board has glass. Headless: the strip IS
  the update screen — two taps, then a polled progress read, because the board
  installs in its own loop. With glass: ONE tap hands the glass back and the
  board's own update screen takes over, so the page installs nothing anywhere.
  Both go through `GET`/`POST /update` on the shared webhost, never the idle
  WebSocket core: the megabytes never cross this link (the board downloads its
  own firmware), and `WS_IDLE_MS` would reap a client through a flash write —
  which is exactly what the old streaming port hit. **ONE disconnect surface,
  and the REASON is its point**: an update or a hand-back is "expected" and
  nothing is at risk; a board that vanished is "lost", and only that one carries
  the unsynced-work warning, because board mode keeps no local store. First
  reason wins, so an update nobody needs warning about cannot later be
  re-reported as a loss.
- **WASM MODE IS A SWITCH, NOT A SESSION** (owner call): no heartbeat, no presence
  detection, no timeout. While WEB CONSOLE is ON the glass PARKS on a connection
  screen — which is how the two-writer collision is **designed out rather than
  detected**. The QR encoder is ours because there is no library on a board and
  the pin is not a constant anything could be baked with. **The pin is read at
  `start()`, never at construction** — boards build the webhost before
  system.json is loaded, so a pin captured then is one minted against an empty
  store.
- **Two Makefile patches are load-bearing and non-obvious.** `-Wno-unknown-pragmas`
  must be appended to the PORT's CFLAGS, not `CFLAGS_USERMOD`: py.mk folds usermod
  flags in at its include and the port adds `-Wall` afterwards, which re-enables
  the warning `-Werror` then makes fatal. And `HEAPU8` is patched INTO the port's
  `EXPORTED_RUNTIME_METHODS_EXTRA`, which is set with `+=` — a command-line
  assignment REPLACES it, dropping `getValue`/`setValue`, and the VM's JS wrapper
  dies at boot.
- **Reach for `node pageshot.mjs` FIRST on any "it looks wrong / it doesn't show
  up" report** — a screenshot is the right evidence for a placement or retention
  bug, and the misplaced FPS chip was invisible in a frame dump and obvious in a
  PNG. When a bug survives that (worker pump, transferable ping-pong, rAF),
  `browsershot.mjs` drives the shipped page in real headless Chrome. **The page
  waits behind a play-button splash unless the scenario passes `?dev=1`**, so a
  scenario that forgets it screenshots a blank canvas and looks like a raster bug.
- **The p8 import is UPSTREAM of us, BOTH halves.** SPEC.md says what a
  converted cart MEANS, so corrections are worked out in moy-spec and travel
  HERE — and once they did not: upstream fixed a pitch offset, our hand-copy
  never heard, and **every cart imported through this repo came out two octaves
  flat while `make test` stayed green**, because the tests had pinned the wrong
  model too. `make vendor-p8-import` carries `p8_import.py` (the assets) and,
  since 2026-08-29, `p8_lua_port.py` (the cart's CODE under a generated p8 shim,
  plus the `.moy` folder itself). **Editing either here is a red test.** What
  stays ours is `import_p8.py` (the CLI) and `p8_writer.py` (the input guards,
  the `os.path` shim, the compatibility report) — neither writes a byte of a
  cart. The `view(128, 120)` hint is `p8_writer.P8_CROP` now, passed on every
  tier, so `--zoom` is only the upstream CLI's spelling of it and cannot be
  forgotten here. `ports/celeste.moy` is gitignored on BOTH repos (CC BY-NC-SA)
  — never commit or ship it.
- **The BROWSER imports p8 carts too (#194), by running those same files, and
  the imported cart RUNS.** A dropped `.p8`/`.p8.png` is converted in the wasm
  VM: `build.sh` stages `tools/p8_import.py`, `tools/p8_lua_port.py` (both
  hash-pinned) and `tools/p8_writer.py` into the frozen set, plus `shims/zlib.py`,
  four lines over MicroPython's `deflate`. **The browser could skip the inflate
  entirely** (`createImageBitmap` + `getImageData`, then read the low bits in JS)
  and that is exactly what was DECLINED: it would be a second reader of one
  format, the same shape as the hand-copied converter, and it does not generalise
  to a board. Measured on a real MicroPython: 40ms to read a `.p8.png`, 13ms to
  write a small cart, ~360ms for Celeste. Rules the tests pin: the **zoom hint is
  unconditional** here (`p8_writer.P8_CROP`, canvas `128x128`) because on the web
  that footgun would fire on every import; the PNG validation is **explicit**
  because the frozen build is opt=3, which strips the converter's own asserts;
  and the browser and the CLI must write **byte-identical** carts, which is why
  the porter declares its manifest field order (MicroPython dicts are not
  insertion-ordered).
- **A file that only has to be "stdlib Python" is NOT known to run on
  MicroPython — RUN IT.** `p8_lua_port.py` was stdlib-only and did not: its
  `localization_lua` used a **lookbehind, a `(?:...)`, an inline `(?m)` and a
  lookahead**, all four of which MicroPython's `re` rejects at COMPILE time
  ("regex too complex"), so the browser's first import would have died on a line
  that reads fine. Three more followed — `str.isalnum` (absent), `json` with no
  `indent=`, and `os.makedirs`/`os.path.join`. All six were fixed UPSTREAM,
  because unlike `os.path.basename` a regex engine and a str method **cannot be
  injected from out here**. `tests/test_p8_micropython.py` is the lane that found
  them and `tests/test_p8_import_vendor.py` scans a re-vendored file for the six
  statically. **Memory is the other tier fact**: a real BBS cart needs ~8MB of
  MicroPython heap to import (the fixture fits in 2MB); the browser gives the VM
  16MB, so that is headroom here and the bar a device leg has to clear.
- **Trust zepto8 for p8 semantics, not the wiki**: the pattern-length rule is the
  first non-looping channel, and all-looping means the SLOWEST channel — the
  wiki's "all-looping loops forever" is WRONG.
- Web audio ships per-frame FINISHED PCM through ONE AudioWorklet ring
  (continuous resample, seam-free; starvation decays instead of hard-cutting),
  with the runner topping a cushion via the page-reported queue depth.

