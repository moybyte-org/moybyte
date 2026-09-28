# doom/ — Doom as a WASM module: the #158 spike, and the cart built by recipe

Two things live here. The **cart**: `build_cart.py` builds Doom as a
`"runtime": "wasm"` cart on the console's own imports, on the developer's
machine, for the developer (below). The **spike**: the S3 half of #158,
measured 2026-09-24 -- doomgeneric compiled to wasm32 with wasi-sdk,
AOT-compiled for Xtensa with the wamrc that `../toolchain/build_wamrc_xtensa.sh`
builds, run by WAMR on the LilyGO T-Deck with the panel, keyboard and
trackball driven natively by an app that replaces the console firmware. The
spike's numbers and the constraints they were measured under are in
`../README.md`.

## The cart: `build_cart.py`

```bash
python3 experiments/wasm_aot/doom/build_cart.py        # -> out/doom.moy, zone 1 MiB
python3 tools/push_cart.py experiments/wasm_aot/doom/out/doom.moy --board p4
```

The recipe fetches doomgeneric at the commit the spike ran (GitHub's tarball,
checked by the sha256 of its tree, because GitHub does not promise the
tarball's bytes), the shareware `doom1.wad` v1.9 from Debian's archive (the
`doom-wad-shareware` source package's upstream tarball, and the WAD in it, each
checked by sha256), and wasi-sdk 24 when `../toolchain/wasi-sdk` does not have
it. It prints doomgeneric's GPL notice and id's licence -- the text Debian's
copyright file carries -- before it builds anything, stages the engine with the
glue, patches the seams below, links `main.wasm` with no import outside
`"moy"`, and compiles and signs a module per chip with `tools/wasm_module.py`
(the pinned compilers, the OTA signing key). Everything it downloads or writes
stays under this directory's gitignored `cache/`, `stage/` and `out/`.

**It is never committed, seeded or pushed to a store** (THIRD_PARTY.md, the
Celeste rule): the cart links GPL code and carries a WAD whose licence forbids
consideration and derivative works, so it exists on the machine that built it
and the boards that machine pushes it to. The cart folder carries both
licences in `LICENSES.txt`.

`dg_cart.c` is the glue, on the proposal's imports:

- **Pacing is Doom's own clock.** The manifest declares `"fps": "free"`, so
  the tick model does not pace the cart: every loop frame runs `_update`,
  which turns the console's input into key events and runs the game tics
  `time()` says are due -- `TryRunTics` returns when none is, where Doom's own
  loop would sleep -- and `_draw`, which renders and blits. Paced at the
  console's 30, a board slower than that would spend loop frames on ticks in
  which Doom has nothing to do and draw fewer frames for it, and a faster one
  would draw fewer than Doom's 35 tics a second. The screen melt takes one step a frame
  for the tics of `time()` since the last, with no game tic run until it
  ends, where Doom runs it as a loop waiting on the clock; the game then
  catches up, as Doom's own loop does after a melt. No sleep, no wait, no
  clock but `time()`.
- **The screen** is `blit` with Doom's 256-entry palette, the 320 x 200 frame
  letterboxed into the 320 x 240 canvas (Doom draws straight into the cart's
  frame). Silent: `-nosound`.
- **The WAD** is the cart's own file, through `read`. On a board every read
  runs inside the store's gate, which on the T-Deck drains the panel's flush
  first (the card shares its SPI bus).
- **No WASI.** wasi-libc's calls land in the glue: stdout and stderr keep a
  fatal `I_Error`'s line, `exit(0)` is `quit()`, any other exit traps, and
  there are no files but the WAD.
- **Input, on every board.** The buttons (SPEC.md 7.3): the d-pad walks and
  turns, `a` fires (selects in a menu, `y` at a prompt), `b` uses (backs out of
  a menu, `n` at a prompt), `run` opens the menu. A board with no buttons
  plays on its touch screen, a 3 x 3 pad over the whole canvas: the top row
  walks forward (turning at the corners), the middle row turns and, in the
  centre, fires; the bottom row uses, walks back and opens the menu. One
  pointer, so one action at a time. Where there is a keyboard, `,` and `.`
  strafe.
- **config.json** carries the zone (`zone`, Doom's `-mb` in whole MiB) and
  `args`, more of Doom's own flags (`-warp 1 3`), read at `_init`, so either
  can change without a rebuild as long as the manifest's memory holds the
  zone.
- **pmem is what a test reads back**: slot 0 the last CRC index and slots
  1..199 the CRC of the frame at every 500th gametic (rendered at that tic, so
  it is the same frame on every host whatever the host's draw cadence -- the
  spectre fuzz's phase is reset for it, since it walks across frames), 200..231
  a fatal `I_Error`'s text, 240 the zone asked for, 241 the zone's lowest free
  KB (free plus purgeable), 242 the heap's headroom after `_init` in KB, 243 the
  gametic and 244 the map. `frames.py` reads it, runs the cart on the host twin
  for the same CRCs, and names the attract loop's transitions;
  `tests/test_doom_cart.py` holds the host run (the frames do not depend on
  the draw cadence; every level of the episode loads and plays at the cart's
  zone) and `tests/on_glass.py`'s `doom_frames_match_the_host` holds a board
  to it. Both skip until the recipe has built the cart.

**Where it runs.** A cart above the tier's floor is allowed: a board that
can fit its load runs it from the launcher, and one that cannot refuses it at
launch with the fit notice (`native/moy_wasm/README.md`, "A cart too big for
the board"). The recipe's default is the smallest zone the shareware episode
loads and plays in (`-mb 1`: every level warped to and played, and the attract
loop's three demos, on the host twin); smaller is not a whole MiB. At that
zone the cart sits at the edge of the S3 boards' 3 MB cart-runtime reserve
with the shell resident: its load holds the linear memory's block, the
runtime pool and the text at once. The Guition S3's launcher leaves less
than that free, so the Player shows the notice before anything loads. The
T-Deck's leaves a few hundred KB more than the cart needs on a fresh boot,
and it runs; once the radios and a session's carts have taken their share,
it shows the notice. A larger reserve (`MOYBYTE_GC_SPLIT_RESERVE`) is the
lever that fits it on the Guition S3, at the Python heap's expense: the board
then runs Doom from its launcher with every frame CRC equal to the host's.
Text in flash
would take the text out of PSRAM, and costs a partition per board and a
full-erase reflash of every device. Neither is taken here (#158 carries the
tier's figures). The Waveshare P4 runs the cart from the launcher and its
suite holds the frames to the host's and the run to a drawn-fps floor; the
T-Deck's suite holds it to whichever the fit check says, and to a floor of
its own where it runs; the Guition S3's skips, saying why; and the
Guition P4's internal store cannot hold the cart, which `tools/push_cart.py`
says before it sends a byte.

The seams the recipe patches in the staged copy, each asserted: the IWAD
search answers for `doom1.wad`; `DG_ScreenBuffer` is the cart's frame; the
wait in `TryRunTics` is a return; each game tic calls `DG_AfterTic` (the CRC,
the zone's low-water mark); `D_Display` begins the melt and steps it; Doom's
own screen (`I_VideoBuffer`) IS the cart's frame, so `I_FinishUpdate` copies
nothing; and the column and span drawers (`R_DrawColumn`, `R_DrawSpan`) take
their texture and colormap into locals once a call instead of reloading the
globals for every pixel. The last two are the frame rate: a 64,000-byte
`memory.copy` a frame runs through the runtime's `memmove`, which the S3's
ROM does at about half `memcpy`'s speed (4.3 ms), and a store through the
frame may alias any global, so the compiler reloaded both pointers per pixel.

**The glue's `sbrk` answers from `__heap_end`**, never `memory.size` or
`memory.grow` (wasi-libc's `sbrk` was the module's only user of either). A
module with neither lets WAMR's compiler load the linear memory's base and
bound once per function rather than after every store and call, and read
Doom's globals at their constant addresses without a bounds check. The
memory's size is the manifest's, initial and maximum alike, so the heap
never grew anyway.

## The spike's pieces

| file | what |
|---|---|
| `dg_moy.c` | doomgeneric's platform half: six imports (`moy_ticks_ms`, `moy_sleep_ms`, `moy_get_key`, `moy_draw`, `moy_wad_size`, `moy_wad_read`) and the WAD class that replaces `w_file.c`. `dg_start(zone_mb)` / `dg_tick()` are the exports the host calls. |
| `build_wasm.sh` | stages upstream `../doomgeneric`, patches one seam (`M_FileExists` answers for `doom1.wad`), compiles `doom.wasm` (5 MB fixed linear memory, 256 KB shadow stack), then `wamrc --target=xtensa --cpu=esp32s3 --size-level=0` into `doom_xtensa_xip.aot` (executes in place from flash) and `doom_xtensa_plain.aot` (relocated into PSRAM). |
| `wasm_imports.py` | lists a module's imports; the host must provide every one. |
| `doom_host.py` | the reference run: the same module under wasmtime on the host with the same six imports, `--png` to dump a frame. If it traps here the module is wrong; if only the board traps, the port is. |
| `doom_spike/` | the ESP-IDF app: WAMR + the natives, the WASI stubs wasi-libc drags in, the ST7789 blit (`lcd.c`, the console's own bring-up sequence, 20-row bands through two internal-SRAM DMA bounce buffers), the C3 keyboard and trackball (`input.c`). Partitions: 2 MB app, 3 MB `wasmaot` (16-byte `MOYAOT` header + module), 5 MB `wad`. |
| `flash_doom.sh PORT [module]` | flashes app + module (+ `../doom1.wad`, skip with `SKIP_WAD=1`). `CHIP=p4` flashes `doom_spike/build_p4` to the P4 (bootloader at 0x2000, 32 MB). REPLACES the console firmware; restore with `make firmware-flash-tdeck-mainline PORT=...` / `make firmware-flash-p4 PORT=...`. |
| `read_doom.py PORT [secs] [--pulse]` | serial reader printing the `DOOM` lines: attach-only for the T-Deck (its `[serial]` rule), `--pulse` resets the P4's CH343 first. Saves the frames the board dumps (below) as PNGs under `$FRAMES_DIR` (default `/tmp/doomframes`). |
| the static | after minutes of PLAY (never in the demo) the glass turned to false-colour static in strips while the status digits stayed red. The dumps settled it: the frames Doom rendered at that moment were pixel-clean, palette intact, canaries around the palette untouched, heaps flat. Every frame rewrites the whole picture, so the only thing that can make static persist is the panel's own state — a command byte misread on the SPI, and the ST7789 no longer in the pixel format / addressing it was set up in. This board's panel answers register reads with 0xff (no SDO), so it cannot be watched, only re-told: `lcd.c` re-sends MADCTL and COLMOD before every frame and the whole register table every ~2 s, and a 40-minute play session then showed nothing. Probably EMI-class (the trackball and keys are what play adds), and the console's own panel driver may be exposed to the same thing on a long session. |
| frame diagnostics | the board prints `FRAMECRC gametic=G crc=X` for the frame at every 500th game tic and dumps a whole frame as hex every 2,500 frames; `doom_host.py` prints the same CRCs for the same demo (deterministic per tic), so "is the frame Doom rendered on the board the frame the host renders" is a diff of two logs. Measured 2026-09-24: 22 of 23 tics equal over 14 minutes, the odd one a level transition. |
| `sdkconfig.defaults.esp32p4` | the P4 build (`idf.py -B build_p4 -D SDKCONFIG=sdkconfig.p4 set-target esp32p4 build`): console clock, L2 cache, PSRAM, UART console; headless — `lcd.c` and `input.c` compile to stubs off the S3. |

Controls on the T-Deck follow the console's scheme: `w a s d` steer, `l` and
`space` fire, `k` uses, `z`/`x` strafe, `enter` confirms, `backspace` is the
menu key; the trackball turns and walks, its click fires. Held keys keep
firing: the keyboard is switched to its raw matrix mode (command 0x03, five
level bytes, the console's `RAW_KEYS` table), and key edges come from diffing
reads. Keyboard firmware older than 2025-06-12 ignores that command and reports
presses only; build with `KBD_ASCII_ONLY` for such a board and keys are timed
holds. The driver does NOT guess mid-session: a matrix frame with two keys down
in column 0 (W + Space reads 0x22) is indistinguishable from a printable ASCII
byte, and a guess that took it for one switched to single-byte reads while the
keyboard kept streaming the matrix -- every matrix byte then read as an ASCII
code, and A's byte, 0x08, is Backspace, the menu key.

The matrix has no diodes, so it GHOSTS: three held keys on three corners of a
rectangle read the fourth corner as pressed. W + A + L (forward, turn, fire) put
that phantom on Backspace, the menu key, and the menu opened by itself
mid-fight. A press that completes a rectangle with three keys already down is
ambiguous to the hardware; the driver gives it to the gameplay key (fire, use,
move) over a letter over the menu key, and the loser is never reported. Space
as fire sits in W's and A's own column and cannot ghost with them at all.

## Reproducing

```bash
../toolchain/build_wamrc_xtensa.sh        # once: an Xtensa-capable wamrc (~45 min)
[ -d ../wamr ] || ../build.sh                # once: clones the pinned WAMR fork
./build_wasm.sh                           # doom.wasm + both .aot
cd doom_spike && idf.py set-target esp32s3 && idf.py build && cd ..
./flash_doom.sh /dev/ttyACM0 doom_xtensa_xip.aot
python read_doom.py /dev/ttyACM0 90
```

`build_wasm.sh` expects `../toolchain/wasi-sdk` (wasi-sdk 24), `../doomgeneric`
(ozkl/doomgeneric) and `../doom1.wad` (the shareware IWAD, 4,196,020 bytes). For
the P4 it also emits `doom_riscv32_plain.aot` / `doom_riscv32_xip.aot` with the
prebuilt wamrc (`--cpu-features=+m,+a,+f,+c`: without `+m` the module wants
`__umodsi3`, which WAMR's RISC-V symbol table does not carry).
