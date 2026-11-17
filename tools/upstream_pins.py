"""The pinned upstream releases, read from tools/upstream_pins.env.

    from tools import upstream_pins
    upstream_pins.load()  # {"MOYBYTE_MPY_TAG": "v1.28.0", "MOYBYTE_IDF_VER": "v5.5.6"}
"""

import os
import re

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "upstream_pins.env")
_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=([A-Za-z0-9._~+-]+)$")


def load(path=PATH):
    """{KEY: value} of every assignment; a line that is neither one nor a
    comment is refused, so make, bash and this reader agree on the file."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            m = _LINE.match(line)
            if not m:
                raise ValueError("%s:%d is not KEY=value: %r" % (path, n, line))
            out[m.group(1)] = m.group(2)
    return out
