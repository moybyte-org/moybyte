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

from moy_webserver import WebServer, http_response, FileResponse

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
    _listdir = listdir or os.listdir
    _isdir = isdir or _is_dir
    _read = read or _read_text
    out = {}
    for cart in sorted(_listdir(carts_root)):
        cdir = carts_root + "/" + cart
        if not _isdir(cdir):
            continue
        _pack_dir(out, cdir, cart, _listdir, _isdir, _read)
    return out


def _pack_dir(out, path, prefix, _listdir, _isdir, _read):
    for name in sorted(_listdir(path)):
        if name in SKIP_DIRS:
            continue
        full = path + "/" + name
        rel = prefix + "/" + name
        if _isdir(full):
            _pack_dir(out, full, rel, _listdir, _isdir, _read)
            continue
        text = _read(full)
        if text is not None:             # binary/unreadable: skip, never crash
            out[rel] = text


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
        try:
            bundle = self._with_sd(lambda: pack_store(self.carts_root))
        except Exception as exc:  # noqa: BLE001
            return http_response(500, '{"error":"%s"}' % exc)
        import json
        return http_response(200, json.dumps(bundle))
