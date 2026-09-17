#!/usr/bin/env python3
"""Transcribe a Silead GSLX680 firmware table into a frozen `bytes` module.

    python tools/gen_gsl_fw.py <esp_lcd_gsl3680.h> <out.py>

The GSL3680 has no flash: the host uploads its firmware over I2C after every
reset, and panel vendors publish that firmware as a C array of
`{offset, value}` pairs (`struct fw_data GSLX680_FW[]`). This emits the same
pairs as 5-byte `<BI` records in one `bytes` literal so the board can freeze
it into flash and `device/gsl3680.py` can stream it without parsing anything
at boot. Run once per panel; the output is checked in beside the board.
"""

import re
import struct
import sys
from pathlib import Path

DOC = '''"""The Silead GSL3680 firmware for %(panel)s.

The GSL3680 is a RAM-loaded touch controller: it has no flash of its own, so
the host uploads its firmware over I2C after every reset (`device/gsl3680.py`
does the uploading; this file is only the bytes). The table is the panel
vendor's, transcribed by `tools/gen_gsl_fw.py` from the `GSLX680_FW[]` array
in the factory demo Guition publishes for this exact glass
(`esp_lcd_gsl3680.h`; provenance in THIRD_PARTY.md). It is PANEL-specific --
the sensor geometry is in here -- so it lives in the board tree, not beside
the driver.

Format: %(n)d entries of 5 bytes, `<BI` -- the register offset, then the 32-bit
value little-endian. An offset of 0xF0 is a page select and is written as ONE
byte (the low byte of the value), every other offset as all four; the driver
knows that rule, this file does not.
"""

ENTRY = 5
FW = (
%(lines)s
)
'''


def transcribe(header_text):
    i = header_text.index("GSLX680_FW[] = {")
    entries = re.findall(r"\{\s*0x([0-9a-fA-F]+)\s*,\s*0x([0-9a-fA-F]+)\s*\}",
                         header_text[i:])
    return b"".join(struct.pack("<BI", int(o, 16), int(v, 16))
                    for o, v in entries)


def render(blob, panel):
    lines = []
    for k in range(0, len(blob), 32):
        chunk = blob[k:k + 32]
        lines.append('    b"' + "".join("\\x%02x" % c for c in chunk) + '"')
    return DOC % {"panel": panel, "n": len(blob) // 5, "lines": "\n".join(lines)}


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
