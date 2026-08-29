"""`zlib.decompress` over MicroPython's built-in `deflate` (#194).

MicroPython dropped the `zlib` module for `deflate` in v1.21, and the vendored
p8 converter's PNG path calls `zlib.decompress(idat)` to inflate the IDAT of a
`.p8.png`. That file is moy-spec's, hash-pinned, and editing it here is a red
test -- so the missing stdlib surface is SUPPLIED rather than the caller
rewritten. Same shape as any other platform shim: it adds nothing and decides
nothing.

STAGED, NEVER ON A HOST'S sys.path. It lives in `shims/` and `build.sh` copies
it into the frozen module set as plain `zlib.py`. A file called `zlib.py` beside
the runner's other modules would shadow CPython's real one for anything that put
that directory on sys.path (`tests/test_zero_gpio.py` does), and the CLI import
path needs the real zlib in the same process.

`wbits`/`bufsize` exist to match the CPython signature the converter is written
against; ZLIB framing is what a PNG IDAT carries, so neither changes anything
here.
"""

import deflate
import io


def decompress(data, wbits=0, bufsize=0):
    return deflate.DeflateIO(io.BytesIO(data), deflate.ZLIB).read()
