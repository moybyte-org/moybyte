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

- **The p8 importer is tested UPSTREAM, and this repo does not download carts.**
  `make -C libmoy p8-carts` in moy-spec imports a corpus of real BBS carts and
  runs each through `run_cart` — the real C console — ratcheted against
  `conformance/p8_carts_expected.json`, fetched from links in
  `conformance/p8_corpus.json` and cached by CI. That is where the porter lives
  and where a porter bug should turn red.
  - **Owner call 2026-09-01: moybyte owns none of that.** Real PICO-8 carts are
    other people's work, and a suite here that downloads twelve of them to test
    a VENDORED converter is coverage in the wrong repository. What stays here is
    the ordinary tests over the vendored code — `tests/test_import_p8.py` and
    `tests/test_p8_micropython.py`, which build their carts inline.
  - **That loses nothing that caught a bug.** Every p8 bug pinned here — the
    60fps cart whose update never ran, the tap that made two menu edges, the
    glyph that is both a value and a name — is pinned by a test that writes a
    few lines of p8 source and runs them on the real Player. The corpus found
    them; the inline tests hold them.
  - **What the Player still adds over `run_cart` is TIME**: run_cart calls
    update once per frame at a fixed 1/30, while the Player runs a cart's own
    rate against the host's, with catch-up and a btnp latch spanning console
    frames. That is why the pacing tests live here and are worth keeping. See
    #217, which folds all of it into one scheduler.

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
