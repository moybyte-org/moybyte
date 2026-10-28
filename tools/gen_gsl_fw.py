#!/usr/bin/env python3
"""Transcribe a Silead GSLX680 firmware table into the board's C array.

    python tools/gen_gsl_fw.py <esp_lcd_gsl3680.h> <out.c>

The GSL3680 has no flash: the host uploads its firmware over I2C after every
reset, and panel vendors publish that firmware as a C array of
`{offset, value}` pairs (`struct fw_data GSLX680_FW[]`). This emits the same
pairs as 5-byte `<BI` records in one `const uint8_t` array, so the kernel's
driver (native/moy_input/moy_touchdev.c) streams it from flash without parsing
anything at boot. Run once per panel; the output is checked in beside the
board, which names it in mpconfigboard.h (MOY_INPUT_TOUCH_FW).
"""

import re
import struct
import sys
from pathlib import Path

DOC = """// The Silead GSL3680 firmware for %(panel)s.
//
// The GSL3680 is a RAM-loaded touch controller: it has no flash of its own, so
// the kernel's driver (native/moy_input/moy_touchdev.c) uploads this over I2C
// after every reset. The table is the panel vendor's, transcribed by
// tools/gen_gsl_fw.py from the GSLX680_FW[] array in the factory demo Guition
// publishes for this exact glass (esp_lcd_gsl3680.h; provenance in
// THIRD_PARTY.md). It is PANEL-specific -- the sensor geometry is in here -- so
// it lives in the board tree, not beside the driver.
//
// Format: %(n)d records of 5 bytes, <BI -- the register offset, then the 32-bit
// value little-endian. An offset of 0xF0 is a page select and is written as ONE
// byte (the low byte of the value), every other offset as all four; the driver
// knows that rule, this file does not.

#include <stddef.h>
#include <stdint.h>

const uint8_t %(name)s[] = {
%(lines)s
};
const size_t %(name)s_LEN = sizeof(%(name)s);
"""


def transcribe(header_text):
    i = header_text.index("GSLX680_FW[] = {")
    entries = re.findall(r"\{\s*0x([0-9a-fA-F]+)\s*,\s*0x([0-9a-fA-F]+)\s*\}",
                         header_text[i:])
    return b"".join(struct.pack("<BI", int(o, 16), int(v, 16))
                    for o, v in entries)


def render(blob, panel, name="moy_gsl_fw"):
    lines = []
    for k in range(0, len(blob), 15):
        chunk = blob[k:k + 15]
        lines.append("    " + " ".join("0x%02x," % c for c in chunk))
    return DOC % {"panel": panel, "n": len(blob) // 5, "lines": "\n".join(lines),
                  "name": name}


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    blob = transcribe(Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
    Path(argv[2]).write_text(
        render(blob, 'the Guition JC8012P4A1C\'s 10.1" glass'), encoding="utf-8")
    print("%s: %d entries, %d bytes" % (argv[2], len(blob) // 5, len(blob)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
