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
import hashlib
import os
import sys
import zlib

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


def cache_key(raw):
    """What a stream is a function of: the input, the passes, the compressor."""
    try:
        from importlib.metadata import version
        ver = version("zopfli")
    except Exception:  # noqa: BLE001 -- an unknown version keys as one
        ver = "?"
    h = hashlib.sha256(raw)
    h.update(("|zopfli %s|%d" % (ver, ITERATIONS)).encode())
    return h.hexdigest()


def cached_gz(raw, cache):
    """gz(raw), from `cache` (a directory) when this input was compressed
    before. An entry that does not inflate to `raw` is recompressed."""
    path = os.path.join(cache, cache_key(raw) + ".gz")
    try:
        with open(path, "rb") as f:
            out = f.read()
        if gzip.decompress(out) == raw:
            return out, path
    except (OSError, EOFError, zlib.error, gzip.BadGzipFile):
        pass
    out = gz(raw)
    os.makedirs(cache, exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        f.write(out)
    os.replace(path + ".tmp", path)
    return out, path


def main(paths, cache=None):
    used = set()
    for path in paths:
        with open(path, "rb") as f:
            raw = f.read()
        if cache:
            out, entry = cached_gz(raw, cache)
            used.add(os.path.basename(entry))
        else:
            out = gz(raw)
        tmp = path + ".gz.tmp"
        with open(tmp, "wb") as f:
            f.write(out)
        os.replace(tmp, path + ".gz")
        print("zopfli_gz: %s %d -> %d" % (os.path.basename(path), len(raw), len(out)))
    if cache:
        # The cache holds the last run's streams and nothing older.
        for name in os.listdir(cache):
            if name not in used:
                os.remove(os.path.join(cache, name))


if __name__ == "__main__":
    args = sys.argv[1:]
    cache = None
    if args[:1] == ["--cache"] and len(args) >= 2:
        cache, args = args[1], args[2:]
    if not args or args[0] in ("-h", "--help"):
        print("usage: zopfli_gz.py [--cache DIR] FILE...   (writes FILE.gz beside each;\n"
              "       DIR keeps the last run's streams by input hash, so an unchanged\n"
              "       file is not compressed again)")
        sys.exit(0 if args else 2)
    main(args, cache)
