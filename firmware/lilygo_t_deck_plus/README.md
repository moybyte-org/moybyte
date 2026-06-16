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
pio run -d firmware/lilygo_t_deck_plus
```

Upload, once the board is attached:

```bash
pio run -d firmware/lilygo_t_deck_plus -t upload --upload-port /dev/ttyACM0
pio device monitor -d firmware/lilygo_t_deck_plus -b 115200
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
