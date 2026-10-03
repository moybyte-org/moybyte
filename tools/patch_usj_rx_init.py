#!/usr/bin/env python3
"""The USB-Serial/JTAG console takes, when it starts, the bytes a host sent
before it did.

    python3 tools/patch_usj_rx_init.py <micropython checkout>

The esp32 port's `usb_serial_jtag_init` clears the peripheral's
"packet received" interrupt and then enables it. A packet that landed before
that -- a host writing in the first half second after a reset, while the
bootloader runs -- stays in the 64-byte RX FIFO with its interrupt cleared,
so nothing reads it until the first read of stdin: on a console board the dev
channel's first poll, a whole boot later. Until then the endpoint refuses the
host's next packet, and a host that closes and reopens the port meanwhile
gets its line-state requests delivered as serial data -- SET_CONTROL_LINE_STATE
as `21 22 03 00 ...`, whose 0x03 is Ctrl-C to a boot still running
`main.py`: "desktop interrupted -> REPL". Measured on the T-Deck 2026-10-02:
a line sent 0.6 s after a reset, then a close and a reopen, put the board at
the REPL; the same line sent at 3.2 s, after the console was up, did not, and
neither did the close and reopen alone. Held open instead, the port took the
early line as `te` -- the FIFO's stall costs bytes even without the reopen.

The patch reads the FIFO once more, right after the interrupt is enabled, so
whatever the host sent early goes into stdin like any later byte and the
endpoint is free again. The interrupt character is not set yet at that point
(pyexec sets it around each script), so an early 0x03 is a plain byte.

Applied by `moybyte_patch_usj_rx_init` in tools/esp32_build_lib.sh, to
`ports/esp32/usb_serial_jtag.c`. All-or-nothing: the hunk must match exactly
once or nothing is written and the exit is non-zero. A file already carrying
the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: take what arrived before the ISR"
FILE = "ports/esp32/usb_serial_jtag.c"

STOCK = ("    ESP_ERROR_CHECK(esp_intr_alloc(ETS_USB_SERIAL_JTAG_INTR_SOURCE, "
         "ESP_INTR_FLAG_LEVEL1,\n"
         "        usb_serial_jtag_isr_handler, NULL, NULL));\n"
         "}\n")
PATCHED = ("    ESP_ERROR_CHECK(esp_intr_alloc(ETS_USB_SERIAL_JTAG_INTR_SOURCE, "
           "ESP_INTR_FLAG_LEVEL1,\n"
           "        usb_serial_jtag_isr_handler, NULL, NULL));\n"
           "    // %s: a packet that landed before\n"
           "    // its interrupt was cleared above is still in the FIFO, and the\n"
           "    // endpoint refuses the host until it is read\n"
           "    // (tools/patch_usj_rx_init.py).\n"
           "    usb_serial_jtag_handle_rx();\n"
           "}\n" % MARKER)


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit when the stock text does not match exactly once."""
    path = os.path.join(mpy_dir, FILE)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        return "already"
    n = text.count(STOCK)
    if n != 1:
        sys.exit("!! USB-Serial/JTAG RX init patch did not apply -- %s matches "
                 "%d times (the stock init changed shape); nothing written"
                 % (FILE, n))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(STOCK, PATCHED))
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch "
                                    "(its ports/esp32 is what changes)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched %s: the console takes what arrived before its ISR"
              % FILE)


if __name__ == "__main__":
    main()
