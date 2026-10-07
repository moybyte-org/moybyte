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
  a BUILD FAILURE on every board. For that reason the two worker modules ship
  without their comments (build.sh re-prints them with emsdk's terser, nothing
  else changed): read `worker.js` and `moy_store.mjs` in the tree, not in
  `dist/`.
- **The emscripten is PINNED: `EMSDK_VERSION` in build.sh, held by
  `tools/emsdk_tree.sh` on every full build** (a tree at the pin is a silent
  no-op, another version is moved to it, one that cannot be had fails the
  build). emcc's output is not byte-reproducible across versions and this
  bundle rides every board image, so an emsdk cloned under `latest` made two
  machines' bundles differ. The pin is the version moy-spec's preflight builds
  its player in; bump the two together. A move shares its emsdk with every
  worktree (`tools/worktree.py` links it).
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
  after three gives up ONCE and says that too. A returning browser's shelf is
  OPFS's, never the bundle's -- except a SYSTEM cart the store has never had
  (a new app, a new seed game), which is seeded into it the way a board seeds a
  built-in it lacks (`moy_store.missingSystemCarts`). Without that, nobody who
  had visited before a system cart shipped would ever see it.
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
- **Get Carts runs in a page that keeps its own carts (#124), and in no other.**
  In site mode `carts_link.py` gives the console the page's fetch
  (`ws.cart_net`), an OPFS keeper (`ws.cart_keep`) and the page's file picker
  (`ws.cart_pick`); a board-served page gets `ws.cart_home` instead and the app
  says the board gets its own carts -- the two-writer rule again, and the sync
  wire could not carry a module anyway. What bites:
  - **Nothing may wait for the network inside a step.** The VM has no
    ASYNCIFY and the worker delivers bytes only between frames, so every fetch
    is a `cart_index` job that gives the frame back; a Python loop waiting for
    `ready` is a hung tab. A body goes into a spool file in the VFS -- the
    page's memory -- and never through the 16 MB heap.
  - **A page cannot read a release download or Debian's archive** (neither
    sends a CORS header). It reads an asset's `mirror`, and an external
    file's, on the carts repository's Pages site first (moy-spec's
    cartindex.py). An external file it can read from neither is the player's
    own copy, chosen through a REAL page control (the card under the canvas):
    a browser opens its file dialog only from a click on one, never from a tap
    the console relays a frame later.
  - **The sweep cannot persist an install**: the wire carries text and covers,
    never a module or a WAD. The keeper writes the folder and the record into
    OPFS behind one marker file (`moy_store.commitInstall`), boot rolls an
    interrupted one forward or away before the store is read
    (`recoverInstalls`), and the watcher ADOPTS the landed folder instead of
    shipping it. `store_test.mjs` interrupts an update at every change it
    makes.
  - **The shelves are the serving host's**: an `indexes.json` beside the page
    replaces the defaults, read once at boot.
  - **A page keeps no compiled module**, so a release asset is read by RANGE
    (`ranges` on the page's transport, `cart_index._Ranged`): the zip's
    directory, then the runs of members it keeps -- a cart's modules are most
    of its asset and never cross. Any first range not answered 206 (a host that
    ignores ranges, a refused preflight) reads the asset whole.
    `tests/test_web_wasm_e2e.py` installs a compiled cart this way and plays
    it; `tests/test_web_store_e2e.py` drives the Lua side.
- **A compiled cart runs on the BROWSER'S engine, behind the boards' session
  surface** (`native/moy_wasm_web`, its README). The cart's `main.wasm` is a
  sibling module the worker instantiates (`worker.js`'s cart engine, moy-spec's
  web-player adapters over libmoy's import table); moycore, `WasmRun`, the
  Player and the canvas are the boards'. What bites:
  - **Nothing on a board is reimplemented here, and nothing native applies**:
    no AOT module, key, signature or Unknown sources -- `moy_wasm.CHIP` is None,
    so WasmRun opens `main.wasm` and is never "slow" for it.
  - **A hook's catch rethrows anything that is not an `Error`**: the VM's own
    longjmp unwinds as a JavaScript exception through the cart's frames, and
    swallowing it would strand MicroPython's nlr.
  - **A page a board serves gets no `main.wasm`** (the wire carries text and
    covers), so its scan leaves the board's compiled carts off its shelf
    (`moy_carts.load`: no main, no cart); they play on the board.
  - **The site-mode sweep must pass a file it cannot carry by stat**
    (`moy_sync.StoreWatcher`, crc None): when it re-read every binary file on
    every sweep, an installed Doom drew a frame a second.
- **THE PAGE IS THE SERVING BOARD'S UPDATE SURFACE** (#41/#53, 2026-08-29), and
  what it does depends on whether that board has glass. Headless: the strip IS
  the update screen — two taps, then a polled progress read, because the board
  installs in its own loop. With glass: ONE tap hands the glass back and the
  board's own update screen takes over, so the page installs nothing anywhere.
  Both go through `GET`/`POST /update` on the shared webhost, which is plain
  HTTP: the megabytes never cross this link (the board downloads its own
  firmware), and a persistent socket's idle reaper would have dropped a client
  through a flash write — which is exactly what the old streaming port hit.
  That verdict is why the transport's WebSocket half had no consumer left and
  was deleted in 2026-09 (below). **ONE disconnect surface,
  and the REASON is its point**: an update or a hand-back is "expected" and
  nothing is at risk; a board that vanished is "lost", and only that one carries
  the unsynced-work warning, because board mode keeps no local store. First
  reason wins, so an update nobody needs warning about cannot later be
  re-reported as a loss.
- **`device/moy_webserver.py` is the bare HTTP transport** `moy_webhost`
  overrides: a non-blocking listener, `parse_request`/`http_response`, one-shot
  serving, and a `WebServer` whose one seam is `handle_http`. The streaming web
  view — the frame push, `device_webview.py`, the recording `TeeCanvas`, stream
  mode, the Settings WEB VIEW row, `ws.web_hook`, the host `tools/web_console.py`
  and its VM recipe — was DELETED in the 2026-08 sunset (owner decision,
  `docs/history/moycore_plan_2026-08.md` §3.2; `tests/test_streaming_sunset.py`
  pins the absences), and the recording stack went at stage 4 (2026-08-12)
  because the wasm head rasterizes. Mirror-of-glass is an accepted loss: a
  screenshot verb on the sync RPC was its recorded successor and was DROPPED
  (owner, 2026-08-25) — the browser IS the console. The WebSocket half went in
  2026-09, because the §3.4 sync RPC shipped as plain HTTP and nothing else
  used it. What survives of the old view is `runtime/web_input.py` (browser
  events → InputState/Pointer, which the sync RPC speaks); `wm_windowed`'s
  `if not self._recording` guards deliberately STAY, unreachable, over the
  kernel's surface table (`native/moy_glass`, which the browser's wasm links
  like every board) — `docs/surface_model_v1.md` §13 and §15 record why.
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
  For a one-off look at a page, `tools/web.py shot URL` prints the PNG's path
  and the page's errors (`shot /?dev=1` serves this tree's dist for the shot;
  `tools/web.py serve` serves it on a free port until stopped). It drives the
  SYSTEM Chrome through Playwright, the `web` extra: never `playwright install`.
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

