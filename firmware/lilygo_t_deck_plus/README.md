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
Display: ST7789 color heartbeat
Runtime: serial-only scaffold
KidCode heartbeat 0
```

The display should alternate full-screen colors once per heartbeat. This is the
first physical proof that the firmware loop and ST7789 display path are running
before the KidCode canvas renderer is integrated.

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
4. draw the 128x128 KidCode logical canvas
5. read keyboard/trackball input into KidCode button names
6. load /data/tiny_runner.kc8 or an embedded bundle
```
