# doom/ — Doom as a WASM cart on the T-Deck (ESP32-S3), #158

The S3 half of the #158 spike, measured 2026-09-24. doomgeneric compiled to
wasm32 with wasi-sdk, AOT-compiled for Xtensa with the wamrc that
`../toolchain/build_wamrc_xtensa.sh` builds, run by WAMR on the LilyGO T-Deck
with the panel, keyboard and trackball driven natively. The numbers and the
constraints they were measured under are in `../README.md`.

## The pieces

| file | what |
|---|---|
| `dg_moy.c` | doomgeneric's platform half: six imports (`moy_ticks_ms`, `moy_sleep_ms`, `moy_get_key`, `moy_draw`, `moy_wad_size`, `moy_wad_read`) and the WAD class that replaces `w_file.c`. `dg_start(zone_mb)` / `dg_tick()` are the exports the host calls. |
| `build_wasm.sh` | stages upstream `../doomgeneric`, patches one seam (`M_FileExists` answers for `doom1.wad`), compiles `doom.wasm` (5 MB fixed linear memory, 256 KB shadow stack), then `wamrc --target=xtensa --cpu=esp32s3 --size-level=0` into `doom_xtensa_xip.aot` (executes in place from flash) and `doom_xtensa_plain.aot` (relocated into PSRAM). |
| `wasm_imports.py` | lists a module's imports; the host must provide every one. |
| `doom_host.py` | the reference run: the same module under wasmtime on the host with the same six imports, `--png` to dump a frame. If it traps here the module is wrong; if only the board traps, the port is. |
| `doom_spike/` | the ESP-IDF app: WAMR + the natives, the WASI stubs wasi-libc drags in, the ST7789 blit (`lcd.c`, the console's own bring-up sequence, 20-row bands through two internal-SRAM DMA bounce buffers), the C3 keyboard and trackball (`input.c`). Partitions: 2 MB app, 3 MB `wasmaot` (16-byte `MOYAOT` header + module), 5 MB `wad`. |
| `flash_doom.sh PORT [module]` | flashes app + module (+ `../doom1.wad`, skip with `SKIP_WAD=1`). `CHIP=p4` flashes `doom_spike/build_p4` to the P4 (bootloader at 0x2000, 32 MB). REPLACES the console firmware; restore with `make firmware-flash-tdeck-mainline PORT=...` / `make firmware-flash-p4 PORT=...`. |
| `read_doom.py PORT [secs] [--pulse]` | serial reader printing the `DOOM` lines: attach-only for the T-Deck (its `[serial]` rule), `--pulse` resets the P4's CH343 first. |
| `sdkconfig.defaults.esp32p4` | the P4 build (`idf.py -B build_p4 -D SDKCONFIG=sdkconfig.p4 set-target esp32p4 build`): console clock, L2 cache, PSRAM, UART console; headless — `lcd.c` and `input.c` compile to stubs off the S3. |

Controls on the T-Deck follow the console's scheme: `w a s d` steer, `l` and
`space` fire, `k` uses, `z`/`x` strafe, `enter` confirms, `backspace` is the
menu key; the trackball turns and walks, its click fires. Held keys keep
firing: the keyboard is switched to its raw matrix mode (command 0x03, five
level bytes, the console's `RAW_KEYS` table), and key edges come from diffing
reads. Keyboard firmware older than 2025-06-12 ignores that command and reports
presses only; the driver detects it and falls back to timed holds.

## Reproducing

```bash
../toolchain/build_wamrc_xtensa.sh        # once: an Xtensa-capable wamrc (~45 min)
python3 ../toolchain/patch_wamr_s3.py     # once per fresh WAMR clone
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
