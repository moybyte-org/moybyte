# What this board freezes from MicroPython's own tree, in place of the port's
# boards/manifest.py (build.sh hands it to moybyte_frozen_manifest; the board's
# modules/ are frozen beside it). The XIAO's set, for the XIAO's reasons
# (firmware/seeed_xiao_esp32s3_zero/boards/MOYBYTE_ZERO/manifest.py): the
# filesystem's first mount, `machine`'s Python half and the LED-strip helpers,
# and the sensor drivers for a sensor wired to the allowlisted pads.
freeze("$(PORT_DIR)/modules", ("_boot.py", "flashbdev.py", "inisetup.py", "machine.py",
                               "apa106.py"))
require("neopixel")
require("onewire")
require("ds18x20")
require("dht")
