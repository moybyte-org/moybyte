"""Serve the moybyte web console FROM the console, over the device's own WiFi.

This is what replaces the streaming web view (#100), and it is the opposite
trade. The stream shipped PIXELS: every frame crossed the wire, so the browser
ran at the WiFi's speed and the defect class was cache agreement across a lossy
transport. This ships the CONSOLE: the board hands the browser the wasm head
once, the page then runs the whole shell locally at full speed, and the only
thing that crosses the wire afterwards is cart data.

    GET /                -> index.html          (the wasm console)
    GET /<asset>         -> micropython.wasm, worker.js, ...   [streamed]
    GET /carts.json      -> THIS BOARD's store, packed live

The last line is why this needs almost nothing new on the page. `worker.js`
already boots by doing `fetch("carts.json")` and writing the result into the
wasm VFS -- a RELATIVE url, so a page served from the board fetches the board's
carts and the unmodified web build comes up holding your things. The pull half
of moycore plan 3.4 is that one endpoint.

WHAT THIS SLICE DOES NOT DO: write back. Commits made in the browser live in
the page's VFS and are lost on reload -- the push half is the next slice, and
until it lands this is a way to PLAY your console's carts in a browser and to
read their code, not to author into the board.

NOT SECURED, deliberately and by owner call: no pairing, no PIN, no token. Any
device on the same network that can reach the port gets the store. That is a
decision to revisit before this is ever pointed at a classroom (the OTA
doctrine -- physical possession is consent -- does not stretch to an open TCP
port), and the reason it is acceptable now is that the endpoint is read-only
and the console has to be told to start it.
"""

try:
    import os
except ImportError:                      # pragma: no cover
    os = None

import json as _json

from moy_webserver import (WebServer, http_response, FileResponse,
                           ChunkedResponse)

# Where `firmware/web_runner/dist` was copied on this board. The T-Deck has an
# SD card and the P4 does not, so the caller passes the directory rather than
# this module guessing; these are the conventional homes.
TDECK_WEB_DIR = "/sd/web"
P4_WEB_DIR = "/moy/web"

# Only these are ever served, and only from the web dir. An allowlist and not a
# path check, because "reject .." is the kind of rule that is one encoding trick
# from serving /moy/carts/../wifi.json -- and the set of files the console needs
# to hand a browser is FIXED and known at build time, so there is no reason to
# accept a path from the network at all.
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "worker.js": "text/javascript; charset=utf-8",
    "micropython.mjs": "text/javascript; charset=utf-8",
    "micropython.wasm": "application/wasm",
}

# Files inside a cart folder that must never leave the board with it.
SKIP_DIRS = ("thumbs", "__pycache__")


def pack_store(carts_root, listdir=None, read=None, isdir=None):
    """The whole cart store as the page's bundle shape: {"<cart>/<rel>": text}.

    The shape is `worker.js`'s, not a new one -- it is what the dev server
    (`firmware/web_runner/moy.py`) already emits and what `writeCarts` already
    consumes, so the page needs no change to read a board instead of a build.

    The three injected callables are for testing on the host, where there is no
    device filesystem; they default to the real ones.
    """
    _read = read or _read_text
    out = {}
    for cart, isdir_ in sorted(_entries(carts_root, listdir, isdir)):
        if not isdir_:
            continue
        _pack_dir(out, carts_root + "/" + cart, cart, listdir, isdir, _read)
    return out


def _pack_dir(out, path, prefix, _listdir, _isdir, _read):
    for name, isdir_ in sorted(_entries(path, _listdir, _isdir)):
        if name in SKIP_DIRS:
            continue
        full = path + "/" + name
        rel = prefix + "/" + name
        if isdir_:
            _pack_dir(out, full, rel, _listdir, _isdir, _read)
            continue
        text = _read(full)
        if text is not None:             # binary/unreadable: skip, never crash
            out[rel] = text


def stream_store_json(carts_root, listdir=None, read=None, isdir=None):
    """The same bundle as pack_store, yielded as JSON pieces -- never assembled.

    A generator and not a dict-then-dumps because on real hardware the dict IS
    the problem: the P4's store is 982KB of JSON, which took 61s to build and
    would not fit on the S3 at all. One file's text is the largest thing
    resident here.

    Escaping goes through `_jstr` -- see the measured note there for why that is
    json.dumps and not the hand-rolled walk it started as.
    """
    _read = read or _read_text
    yield "{"
    first = [True]
    for cart, isdir_ in sorted(_entries(carts_root, listdir, isdir)):
        if not isdir_:
            continue
        for piece in _stream_dir(carts_root + "/" + cart, cart,
                                 listdir, isdir, _read, first):
            yield piece
    yield "}"


def _stream_dir(path, prefix, _listdir, _isdir, _read, first):
    for name, isdir_ in sorted(_entries(path, _listdir, _isdir)):
        if name in SKIP_DIRS:
            continue
        full = path + "/" + name
        rel = prefix + "/" + name
        if isdir_:
            for piece in _stream_dir(full, rel, _listdir, _isdir, _read, first):
                yield piece
            continue
        text = _read(full)
        if text is None:                 # binary/unreadable: skip, never crash
            continue
        if not first[0]:
            yield ","
        first[0] = False
        yield _jstr(rel)
        yield ":"
        yield _jstr(text)


def _jstr(s):
    """A JSON string literal -- `json.dumps`, which is C.

    This was hand-rolled, on the reasoning that dumps allocates a second copy of
    a 40KB source file. MEASURED ON P4 GLASS 2026-08-14 and the reasoning was
    backwards: the escaper walked every character in a Python loop, so packing
    the 981KB store took 39.9s with no network involved at all -- ~1M
    interpreter iterations at ~40us each. The allocation it saved was the cheap
    half; the loop was the whole cost.

    The general lesson, and the reason this comment is long: on MicroPython a
    per-character Python loop is never the optimisation. Handing a whole string
    to a C builtin allocates, and allocating is what this file otherwise works
    hard to avoid -- but ONE file's copy is bounded and transient, where the
    dict-of-everything this replaced was not.
    """
    return _json.dumps(s)


def _entries(path, _listdir=None, _isdir=None):
    """(name, is_dir) for everything in `path`, in ONE directory traversal.

    `os.ilistdir` yields the TYPE alongside the name, which is the whole point:
    the obvious `listdir` + `stat`-each costs a full path resolution per entry,
    and on littlefs that is not a small constant.

    MEASURED ON P4 GLASS, 2026-08-14 -- littlefs walks from the root on every
    path operation, so the cost is linear in how many entries the parent holds:

        stat /moy                          5.3 ms   (depth 1)
        stat /moy/carts                   28.9 ms   (depth 2, 46 entries)
        stat /moy/carts/<cart>/main.py    59.4 ms   (depth 4)

    A stat cost MORE than opening and reading an 11KB file (44.0 ms). The store
    walk was doing 271 of them, ~16s of a 27s pack, to learn something ilistdir
    hands over for free.
    """
    ils = getattr(os, "ilistdir", None)
    if ils is not None and _listdir is None:
        for e in ils(path):
            yield e[0], (e[1] & 0x4000) != 0
        return
    # Host/test path: no ilistdir (CPython's os has none), or injected fakes.
    ld = _listdir or os.listdir
    isd = _isdir or _is_dir
    for name in ld(path):
        yield name, isd(path + "/" + name)


def _is_dir(path):
    try:
        return (os.stat(path)[0] & 0x4000) != 0
    except OSError:
        return False


def _read_text(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except (OSError, UnicodeError, ValueError):
        return None


def _file_size(path):
    try:
        return os.stat(path)[6]
    except OSError:
        return None


class WebHost(WebServer):
    """The transport, with the console's own pages and store wired to it."""

    def __init__(self, carts_root, web_dir, port=None, with_sd=None):
        if port is None:
            WebServer.__init__(self)
        else:
            WebServer.__init__(self, port=port)
        self.carts_root = carts_root
        self.web_dir = web_dir
        # The T-Deck's store lives on a shared-SPI SD card that must be touched
        # through moybyte_sd.with_sd_live, never directly (see that module and
        # the hard-constraints section of CLAUDE.md). The P4 has no SD and
        # passes None, which makes this a plain call-through.
        self._with_sd = with_sd or (lambda fn: fn())

    def handle_http(self, method, path, body):
        if method != "GET":
            # The push half is the next slice; say so rather than 404, because
            # a page from a NEWER build talking to an older board will try.
            return http_response(405, '{"error":"read-only build"}')
        path = path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            return self._asset("index.html")
        if path == "/carts.json":
            return self._carts_json()
        name = path[1:]
        if name in ASSETS:
            return self._asset(name)
        return None                      # -> 404 from the transport

    def _asset(self, name):
        full = self.web_dir + "/" + name
        size = _file_size(full)
        if size is None:
            # A board with no web bundle copied to it. Say WHICH directory,
            # because the failure is a setup step and not a bug, and a bare 404
            # in a browser tells the owner nothing.
            return http_response(
                404, "no web bundle at %s -- copy firmware/web_runner/dist "
                     "there" % self.web_dir, "text/plain; charset=utf-8")
        return FileResponse(full, size, ASSETS[name])

    def _carts_json(self):
        """The store, STREAMED as JSON -- never built.

        Measured on P4 glass before this was a generator: 46 carts / 225 files
        packed in 21.8s and `json.dumps`ed in 39.3s into a 982KB string, so the
        request timed out at 60s having sent nothing, with the frame loop
        blocked throughout. The dict and the dump are both gone; what is left is
        the unavoidable part, reading the files.

        The SD gate is entered ONCE, here, and the walk then runs outside it --
        which is correct only because of what `with_sd_live` is: it mounts the
        card once and KEEPS IT RESIDENT for the session (moybyte_sd; tearing it
        down per op is what corrupts the shared bus and hangs the next panel
        flush). So the gate's job is "make sure the card is up", and a generator
        that yields for a minute must not hold anything. Wrapping the whole
        iteration would mean holding the gate across every read, which is the
        opposite of that module's contract.
        """
        self._with_sd(lambda: None)          # ensure the card is mounted
        return ChunkedResponse(stream_store_json(self.carts_root))
