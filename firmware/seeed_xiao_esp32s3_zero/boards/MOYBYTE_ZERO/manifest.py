# What this board freezes from MicroPython's own tree, in place of the port's
# boards/manifest.py (build.sh hands it to moybyte_frozen_manifest; the board's
# modules/ are frozen beside it).
#
# Kept: the port's modules -- the filesystem's first mount (_boot, flashbdev,
# inisetup), `machine`'s Python half, and the LED-strip helpers -- and the
# sensor drivers, for a sensor wired to the labelled pads at the REPL (see the
# I2C note in mpconfigboard.h).
#
# Left out, because nothing on this board imports them and the console it
# serves runs in the browser: the networking bundle (mip, ntptime, ssl,
# requests, urequests, webrepl -- this board's HTTP, TLS and updater are
# native/moy_net's), asyncio, umqtt, upysh (the board's shell is moy_sh) and
# aioespnow, whose `espnow` this image does not build (MICROPY_PY_ESPNOW).
freeze("$(PORT_DIR)/modules", ("_boot.py", "flashbdev.py", "inisetup.py", "machine.py",
                               "apa106.py"))
require("neopixel")
require("onewire")
require("ds18x20")
require("dht")
