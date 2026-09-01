---
paths:
  - "tests/**"
  - "tools/p4_autotest.py"
  - "tools/check_docs.py"
---

<!-- How this repo tests, and why a grep is not coverage. -->

- **Host tests execute the device tier now, they do not grep it.** The firmware
  suites once asserted device bodies as source STRINGS, which is how a meter
  printed a constant for weeks behind a green test. What legitimately stays a grep
  is ROUTING — that a board still calls a shared helper — and
  `tests/test_micropython_spike.py` keeps only those.

- **The hosted console has a CI net, and its skips have TEETH.**
  `tests/test_web_sync_e2e.py` and `tests/test_web_persist_e2e.py` are the only
  checks that drive the wasm head in real headless Chrome; both are gated on
  `MOYBYTE_WEB_E2E` and are run by `.github/workflows/web-e2e.yml` (path-filtered,
  sharing pages.yml's wasm cache key). Prerequisites resolve through
  `tests/web_e2e.py`, which warns and skips on a bench but FAILS under
  `CI`/`MOYBYTE_REQUIRE_WEB_E2E` — same doctrine as `MOYBYTE_REQUIRE_UNIX_MP`.
  A skip is the right answer to a missing emsdk build on a laptop and the wrong
  one in a job that asked for the suite, so the workflow also refuses a run in
  which nothing ran.

- **The p8 importer's cart gate lives UPSTREAM, and moybyte's copy is a
  tier-parity check.** `make -C libmoy p8-carts` in moy-spec runs the corpus
  through `run_cart` -- the real C console on the real LUA_32BITS VM, which is
  where the porter lives and where a porter bug should turn red. That VM is not
  a detail: lua_Number is a SINGLE-PRECISION float there, and a 16.16
  fixed-point implementation of p8's bitwise operators passed every test here
  and returned 0 there. `tests/test_p8_corpus.py` runs the same carts through
  moybyte's Python host instead, so what it proves is that both consoles agree
  -- "one cart, every tier". Do not justify the split by input: libmoy has a
  full input API and `run_cart --hold` uses it.

- **That gate exists because the unit tests could not have found any of this.** Every porter bug of 2026-09-01 — fifteen
  dialect rules, a 60fps cart whose update never ran, a tap that moved two menu
  slots — was found by importing a famous cart and LOOKING at it, while the
  suite stayed green throughout. A cart that never ticks never errors either,
  so "no exception" is not a measurement.
  `tests/test_p8_corpus.py` runs twelve well-known BBS carts through the real
  Player and pins what each one does in `tests/p8_corpus_expected.json`.
  - **The carts are NOT in the repo** — other people's work, some under
    licences that forbid redistribution. `tools/fetch_p8_corpus.py` caches them
    outside the tree and the suite skips without them (`MOYBYTE_P8_CORPUS`).
    The cart list is chosen to stress DIFFERENT things (a raycaster, a
    world-gen sim, a minified bytecode VM, two carts with graphics packed into
    strings); twelve similar carts would have found perhaps three of the
    fifteen bugs.
  - **Each cart runs in a CHILD PROCESS with a timeout**, because a cart can
    HANG: `terra` spins in its own world generation and the console's Lua opens
    no debug library, so nothing inside the process can interrupt it.
  - **The ratchet nags in BOTH directions.** A cart doing less fails; a cart
    doing MORE fails too, asking for the pin to be raised — a ratchet that
    tightens only when someone remembers is one that never tightens.
  - **Know what the two numbers prove.** `distinct` (frames that differ)
    catches "nothing runs" — 1 for a frozen cart, 25+ for a live one — but NOT
    "playable": a title screen animating off `time()` scores high while the
    cart's update is dead. `responds` (held-direction pixels differ from
    not-held) is the strong signal, and it is not fooled by `rnd()`. Both were
    checked against deliberately dead and deliberately live probe carts.

- **On-glass testing — all three boards have a suite** (#156). Each is gated on
  its own env var and shares one session in file order, leaving the board where
  it found it: `tests/test_p4_on_glass.py` (`MOYBYTE_P4_PORT`),
  `tests/test_tdeck_on_glass.py` (`MOYBYTE_TDECK_PORT`),
  `tests/test_guition_on_glass.py` (`MOYBYTE_GUITION_PORT`), over
  `tools/p4_autotest.py`'s `P4Board` and the shared `tests/on_glass.py` fixture.
  - **The line state at open is per-board and OPPOSITE, and it is DATA.**
    `P4Board(board_dir=…)` reads `dtr`/`rts`/`attach_only`/`chunk` from that
    board's `[serial]` block. The P4's CH343 opens with both LOW; the two S3
    boards' USB-Serial/JTAG is ON the SoC, so opening them low is a CHIP RESET
    (`rst:0x15`) after which the device re-enumerates under the open handle and
    every read returns nothing, forever — indistinguishable from a dead board.
    `attach_only` REFUSES a reset rather than recording one.
  - **Merely OPENING the P4's CH343 reboots it** (`rst:0x1`; the Linux CH34x
    driver glitches the reset circuit). A bare probe right after open is
    measuring a board mid-boot, ~17s to the desk.
  - **The dev channel is ONE class** (`runtime/dev_channel.py`) with one
    vocabulary: `state`/`tap`/`run`/`open`/`swipe`/`drag`/`diag`/`skip`/`gov`/
    `mem`/`bl`/`vol`/`power`/`web`/`py`/`quit`. A command a board cannot serve
    DECLINES. Board extras arrive as a handler dict, `py` scope extras via `env`.
    `state` is one-line JSON and assertions read console STATE, not pixels;
    `swipe` goes through the real pointer feed; `py` evals against the live
    console between frames.
  - **`quit` exits the DESKTOP to the REPL, not the running cart**
    (`REMOTE quit -> REPL`). Using it to end a cart leaves the board at `>>>`,
    after which every suite errors with "did not answer `state`" and reads like a
    dead board. Recover with a **Ctrl-D soft reset** — it re-runs `main.py` and
    does NOT re-enumerate USB, which is what makes it safe on an attach-only board.
  - **Never put a call that blocks on FLASH inside a `pyexec` snippet.** `pyexec`
    uploads in chunks while `cmd` sends one line, so a real file write stalls the
    loop long enough for a streaming PERF line to interleave into the exchange;
    the reader then parses the fragment as a COMMAND and `int()`s its argument,
    surfacing as a `PY ERR` that names nothing that is wrong. Issue those as their
    own short `cmd`.
  - **A UART board's stdin ring is ~256 bytes with NO flow control**, so
    `_write_line` paces bursts and any other writer of long lines must too (USB
    boards backpressure and never need it). `SERIAL_LINE_MAX` must fit the
    harness's `pyexec` chunk lines — at the T-Deck's original 96 every P4 upload
    was silently dropped as noise.
  - Waits and staleness: **wait for `REMOTE drag done`/`swipe done`** before the
    next command; PERF's `wmr/wmw/wms` are last-sample values that go STALE when
    their pass stops running (a repeated constant means "not running"); allow ~10s
    after a first `open picker` at a new size (cover pop-in, #155).
  - **Look system-app carts up by TITLE, never folder name** — the device seeds
    from the title slug, the host copies the source folder, and that mismatch is
    what broke `AppearanceAppLayer.is_app` on device (pinned by
    `tests/test_device_seed_parity.py`).
  - When a T-Deck sits wedged for esptool, `--before usb_reset` connects where
    `default_reset` write-times-out.
