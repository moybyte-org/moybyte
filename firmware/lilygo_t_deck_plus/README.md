# KidCode LilyGO T-Deck Plus Firmware Scaffold

This directory is the first firmware bring-up target for the user's LilyGO
T-Deck Plus.

It is intentionally a smoke-test scaffold, not the full KidCode runtime yet.
The first job is to compile and flash something known, prove serial output, and
then layer in display, keyboard, and project-bundle loading.

Upstream references:

```text
https://github.com/Xinyuan-LilyGO/T-Deck
https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Deck/master/platformio.ini
https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Deck/master/boards/T-Deck.json
https://raw.githubusercontent.com/Xinyuan-LilyGO/T-Deck/master/examples/UnitTest/utilities.h
```

Local build:

```bash
make firmware-build-lilygo
```

`make firmware-build-lilygo` first generates
`include/kidcode_project_bundle.h` from `examples/tiny_runner.kcproj`, then runs
PlatformIO.

Upload, once the board is attached:

```bash
make device-port
make firmware-upload-lilygo PORT=/dev/ttyACM0
make firmware-monitor-lilygo PORT=/dev/ttyACM0
```

One-step smoke run:

```bash
make firmware-smoke-lilygo PORT=/dev/ttyACM0
```

Expected serial output:

```text
KidCode firmware smoke test
Board: LilyGO T-Deck Plus
Board id: lilygo_t_deck_plus
Bundled project: tiny_runner
Bundle title: Tiny Runner
Bundle bytes: <non-zero>
Keyboard: detected
Display: KidCode native tiny_runner canvas
Runtime: native tiny_runner scaffold
KidCode heartbeat 0
Native tiny_runner player_x 62
Native tiny_runner player_y 60
Native buttons left/right/up/down 0/0/0/0
```

The display should show a centered 128x128 KidCode canvas with a white border,
a green player rectangle, and a yellow coin rectangle. The player moves
horizontally when no keyboard is detected. When the keyboard is detected, use
`WASD` or `HJKL` to move the player. `Z` maps to KidCode `a`, and `X` maps to
KidCode `b`. This is a native firmware scaffold for `tiny_runner`, not a
general Python runtime yet.

Verify a saved serial log:

```bash
make firmware-smoke-check-lilygo LOG=/tmp/kidcode_lilygo_serial.log
```

If upload does not start, the upstream notes say to enter download mode by
connecting USB-C, toggling power on, holding the center trackball, and pressing
the left reset button.

Next firmware steps:

```text
1. compile this serial smoke test
2. flash it and verify serial output
3. add display init from the official T-Deck examples
4. map the keyboard polling into canonical KidCode button names
5. replace the native tiny_runner scaffold with general .kc8 runtime loading
6. map a portable subset to MicroPython or native generated code
```
