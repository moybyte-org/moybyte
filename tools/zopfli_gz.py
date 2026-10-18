#!/usr/bin/env python3
"""Write `<file>.gz` beside each file given: zopfli's gzip, byte-reproducible.

`firmware/web_runner/build.sh` runs this over the browser console's assets. The
boards' webhost serves each `<name>.gz` with `Content-Encoding: gzip` and the
image bakes it, so every byte here is image headroom (the Zero's is the
tightest). Zopfli emits a standard gzip stream -- the header carries no mtime
and no name -- so the same input is the same bytes and nothing on the serving
side knows the difference. The baked-blob check (tools/gen_web_blob.py) compares
an asset's gz by what it INFLATES to, never by recompressing, so it agrees with
any compressor.

The `zopfli` package is a hard requirement (pinned in pyproject.toml's `dev`
extra). A missing one is an error, never a fallback to plain gzip: a build that
quietly used another compressor would ship a bigger image on that machine alone.
"""
import gzip
import os
import sys

# 30 passes: 15 -> 30 -> 60 moved the five assets by well under 0.1% in total,
# while the compress time of micropython.wasm (95% of the work) went 4s -> 6s -> 10s.
ITERATIONS = 30


def gz(data):
    """The gzip of `data` under zopfli, header-reproducible."""
    try:
        import zopfli.gzip
    except ImportError:
        sys.exit("zopfli_gz: the `zopfli` package is missing; "
                 "`pip install 'zopfli==0.4.3'` (or `make setup`)")
    out = zopfli.gzip.compress(data, numiterations=ITERATIONS)
    # The whole contract is "a gzip that inflates to the input"; check it.
    assert gzip.decompress(out) == data, "zopfli produced a stream that does not inflate to its input"
    return out


def main(paths):
    for path in paths:
        with open(path, "rb") as f:
            raw = f.read()
        out = gz(raw)
        tmp = path + ".gz.tmp"
        with open(tmp, "wb") as f:
            f.write(out)
        os.replace(tmp, path + ".gz")
        print("zopfli_gz: %s %d -> %d" % (os.path.basename(path), len(raw), len(out)))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: zopfli_gz.py FILE...")
    main(sys.argv[1:])
