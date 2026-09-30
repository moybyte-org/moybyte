#!/usr/bin/env python3
"""A 4 KB stdin ring, in TCM, for the ESP32-P4 console whose serial is a UART.

    python3 tools/patch_stdin_ring.py <micropython checkout>

The esp32 port reads its REPL UART into a 260-byte ring (`stdin_ringbuf_array`
in ports/esp32/mphalport.c). The UART's ISR fills it and only the MicroPython
task drains it, and with no flow control on the wire a byte that arrives with
the ring full is dropped without an error. So the ring has to hold everything
that arrives while the task is not reading, and the longest such stretch is a
heap collection landing in a frame: on the Waveshare P4 a forced collect
measured 72 ms at a fresh boot and 80-87 ms once Jet Teapot and Doom had run
(2026-09-30), and ~95 ms in the on-glass suite with a compiled cart's heap. At
115200 baud that is 11.5 bytes a millisecond, so a 95 ms collection plus the
frame it lands in (~45 ms under Jet) is about 1.6 KB; the stock 260 bytes are
23 ms. 4096 bytes are 356 ms of line rate.

IN TCM, not in .bss. The P4's 8 KB of tightly coupled memory is internal and
uncached (the ISR is IRAM and runs while a flash write has the cache off), and
the heap serves an allocation from it only when L2MEM cannot, so it idles
almost empty. A ring in .bss measured the same 3.8 KB of free internal SRAM
but shrank the L2MEM heap region behind it, which pushed a boot allocation
into the 256 KB region and cost the largest internal block 16 KB. In TCM the
L2MEM layout gains the stock array's 260 bytes and nothing moves.
`ringbuf_t` counts in uint16_t, so the ceiling is 65535.

Only a board whose serial is a UART takes this. The USB-Serial/JTAG boards'
ISR takes only what the ring has room for and the USB host waits for the rest,
so a stall there costs throughput and never a byte. TCM_DRAM_ATTR is the P4's:
another chip taking this fails to compile rather than placing the ring
somewhere unmeasured.

Applied by `moybyte_patch_stdin_ring` in tools/esp32_build_lib.sh, to
`ports/esp32/mphalport.c`. All-or-nothing: every hunk must match exactly once
or nothing is written and the exit is non-zero, naming the hunk. A tree
already carrying the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: stdin ring in TCM"
RING_BYTES = 4096

# (file, name, stock text, patched text). Each stock text must occur exactly
# once, or the whole patch is refused.
HUNKS = [
    ("ports/esp32/mphalport.c", "ring array",
     "static uint8_t stdin_ringbuf_array[260];\n",
     "// %s, %d bytes: a UART with no flow control drops what arrives\n"
     "// while a heap collection stalls the reader (tools/patch_stdin_ring.py).\n"
     "#include \"esp_attr.h\"\n"
     "static uint8_t TCM_DRAM_ATTR stdin_ringbuf_array[%d];\n"
     % (MARKER, RING_BYTES, RING_BYTES)),
]


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit naming the hunk that did not match exactly once."""
    paths = sorted({f for f, _, _, _ in HUNKS})
    texts = {}
    for rel in paths:
        with open(os.path.join(mpy_dir, rel), encoding="utf-8") as fh:
            texts[rel] = fh.read()
    if all(MARKER in texts[rel] for rel in paths):
        return "already"
    for rel, name, old, new in HUNKS:
        n = texts[rel].count(old)
        if n != 1:
            sys.exit("!! stdin ring patch did not apply -- %s hunk %r matches %d times "
                     "(the stock line changed shape); nothing written" % (rel, name, n))
        texts[rel] = texts[rel].replace(old, new)
    for rel in paths:
        with open(os.path.join(mpy_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(texts[rel])
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its ports/esp32 is what changes)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched ports/esp32/mphalport.c: %d-byte stdin ring in TCM" % RING_BYTES)


if __name__ == "__main__":
    main()
