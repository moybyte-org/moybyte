"""Serve the moybyte web console FROM the console, over the device's own WiFi.

This is what replaces the streaming web view (#100), and it is the opposite
trade. The stream shipped PIXELS: every frame crossed the wire, so the browser
ran at the WiFi's speed and the defect class was cache agreement across a lossy
transport. This ships the CONSOLE: the board hands the browser the wasm head
once, the page then runs the whole shell locally at full speed, and the only
thing that crosses the wire afterwards is cart data.

    GET  /               -> index.html          (the wasm console)
    GET  /<asset>        -> micropython.wasm, worker.js, ...   [streamed]
    GET  /carts.json     -> THIS BOARD's store, packed live
    POST /sync           -> apply a commit-shaped batch INTO the store

The assets come from the FIRMWARE IMAGE (native/moy_web, gzipped, ~573KB of
the app slot), and a copy pushed onto the board's storage overrides them. That
way round because the drift went the other way for months: the bundle was only
ever a hand-placed copy, so a board could serve a console older than its own
firmware -- and when this was decided the T-Deck could not even be pushed to
(its serial RX was dead under the desktop until #201, 2026-08-16), so its copy
went on by card reader or not at all. The image is the guarantee; a push is
still the faster dev loop.

The last line is why this needs almost nothing new on the page. `worker.js`
already boots by doing `fetch("carts.json")` and writing the result into the
wasm VFS -- a RELATIVE url, so a page served from the board fetches the board's
carts and the unmodified web build comes up holding your things. The pull half
of moycore plan 3.4 is that one endpoint.

THE PUSH HALF is POST /sync (moy_sync, one body with the browser's watcher and
the CI convergence harness): the page sweeps its own VFS for changed files and
ships them back as commit-shaped batches, so an edit made in the browser lands
in this board's store within about a second of its #111 commit. Per-file
last-writer-wins; each side keeps its own journal. A batch that touches the
SHELF (a manifest, a cover sheet, a cart appearing or dying) triggers the
injected `on_sync` -- the console re-scans, so the kid's launcher shows the
browser's work without a reboot.

SECURITY: the read half stays open by the standing owner call (any device on
the network that can reach the port gets the store); the WRITE half accepts an
optional `pin` -- when the host is built with one, a batch not carrying it is
refused 403. The boards pass none TODAY, which keeps the LAN dev loop and the
owner's own desk working, but an open port that writes flash is explicitly
NOT classroom-safe (the 3.4 doctrine: physical possession is consent, an open
TCP port is not) -- wiring the pin into the Settings row's displayed URL is
the named follow-up before this feature travels.
"""

try:
    import os
except ImportError:                      # pragma: no cover
    os = None

import json as _json

from moy_webserver import (WebServer, http_response, FileResponse,
                           ChunkedResponse, BlobResponse)

# The console BAKED INTO THIS IMAGE (native/moy_web + tools/gen_web_blob.py).
# Optional by construction: a build without it (a host test, an older image, a
# firmware built with no web bundle available) simply falls back to storage,
# which is what the whole feature was before 2026-08-15.
try:
    import moy_web as _moy_web
except ImportError:                      # pragma: no cover -- host/no-bundle
    _moy_web = None

# Where a PUSHED copy of `firmware/web_runner/dist` lives on this board -- the
# override, not the source of truth: since 2026-08-15 the bundle is baked into
# the firmware image (native/moy_web) and this directory is what a human puts
# there to iterate faster than a reflash. The caller passes the directory
# rather than this module guessing; these are the two conventional homes.
#
# NAMED FOR THE STORAGE, NOT FOR A BOARD. They were TDECK_/P4_ when there were
# two boards and each used a different one, which read as a per-board constant
# and is not: it is a storage choice, and the third board picked the internal
# one while carrying an SD slot of its own. A board name here would have to be
# re-decided every time the roster grows; the storage fact does not move.
SD_WEB_DIR = "/sd/web"
INTERNAL_WEB_DIR = "/moy/web"

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

# The skip predicate and the walking primitives are moy_sync's now -- ONE body
# for the pull walkers here, the browser's push sweep, and the receiving
# apply, so what never crosses the wire cannot diverge by direction. journal/
# stays home (each side keeps its own undo history -- the 2026-08-22 pull
# decision and its push mirror), thumbs/ is a regenerable cache, .bak/.tmp are
# crash-safety artifacts. `pmem.json` crosses: it is the kid's saves.
try:
    import moy_sync
except ImportError:                      # host / CPython: the runtime package
    from runtime import moy_sync

_skip = moy_sync._skip
_entries = moy_sync._entries
_is_dir = moy_sync._is_dir
_read_text = moy_sync._read_text


def pack_store(carts_root, listdir=None, read=None, isdir=None):
    """The whole cart store as the page's bundle shape: {"<cart>/<rel>": text}.

    The shape is `worker.js`'s, not a new one -- it is what the dev twin
    (`firmware/web_runner/serve.py --carts`, which calls THIS function) serves
    and what `writeCarts` already consumes, so the page needs no change to
    read a board instead of a build.

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
        if _skip(name):
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
    # The exclusions go through `_skip`, not a second copy of the test: these
    # two walkers are independent bodies whose output must agree, and a skip
    # added to one only makes packed and streamed diverge.
    for name, isdir_ in sorted(_entries(path, _listdir, _isdir)):
        if _skip(name):
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


def _file_size(path):
    try:
        return os.stat(path)[6]
    except OSError:
        return None


def _pushed_copy(full):
    """(path, size, encoding) for a PUSHED copy of `full`, or None.

    Both probes in ONE call so `_asset` pays ONE storage session for them.
    The pre-gzipped copy wins; raw is the fallback (a plain static host sets no
    Content-Encoding, so `dist/` keeps both).
    """
    size = _file_size(full + ".gz")
    if size is not None:
        return (full + ".gz", size, "gzip")
    size = _file_size(full)
    if size is not None:
        return (full, size, None)
    return None


def _baked(name):
    """The named asset out of the firmware image, or None.

    Guarded rather than trusted: an image built before moy_web existed has no
    module, and one built with no bundle available has a module reporting zero
    assets. Neither is a request-time error -- both mean "fall through".
    """
    if _moy_web is None:
        return None
    try:
        return _moy_web.asset(name)
    except Exception:                    # noqa: BLE001 -- never fail a request
        return None


def baked_stamp():
    """The image's own bundle as "<count> <bytes> <digest>", or None.

    The answer to the question that started this: WHICH console is this board
    serving? Reachable from a REPL (`py moy_webhost.baked_stamp()`) and from
    the P4's serial `py`, so the check costs one line and no reflash.
    """
    if _moy_web is None:
        return None
    try:
        return _moy_web.stamp()
    except Exception:                    # noqa: BLE001
        return None


class WebHost(WebServer):
    """The transport, with the console's own pages and store wired to it."""

    def __init__(self, carts_root, web_dir, port=None, with_sd=None,
                 ensure_online=None, pin=None, on_sync=None):
        if port is None:
            WebServer.__init__(self)
        else:
            WebServer.__init__(self, port=port)
        self.carts_root = carts_root
        self.web_dir = web_dir
        # The push half's consent gate + the console's shelf-refresh hook (see
        # the module docstring). pin=None means the write endpoint is as open
        # as the read one -- the standing owner call for a desk, not a
        # classroom. on_sync fires only for batches that changed the SHELF.
        self.pin = pin
        self.on_sync = on_sync
        # Settings contract (console.Workstation.toggle_webhost): `.serving`,
        # `.start()`, `.stop()`, `.url()`, and `.error` for a failure the row can
        # show. Constructed but NOT started -- __init__ binds no socket, so
        # injecting this costs nothing until a kid turns it on.
        self.serving = False
        self.error = None
        # Brought up by start(), because a Settings toggle cannot be asked to
        # connect the WiFi first: the row is the whole UI this feature has.
        self._ensure_online = ensure_online
        # The T-Deck's store lives on a shared-SPI SD card that must be touched
        # through moybyte_sd.with_sd_live, never directly (see that module and
        # the hard-constraints section of CLAUDE.md). The P4 has no SD and
        # passes None, which makes this a plain call-through.
        self._with_sd = with_sd or (lambda fn: fn())
        # ...and the SAME gate guards asset STREAMING, which is where the bytes
        # actually are: /sd/web/micropython.wasm is ~1MB off that shared bus,
        # against a carts.json walk that is comparatively tiny. Gating only the
        # store walk would have left the megabyte ungated. None on the P4, whose
        # bundle is on internal flash and races nothing.
        self.stream_gate = with_sd

    def start(self, ip=None):
        """Bring the link up, then listen. Sets `serving` only if BOTH worked.

        The order matters and is not cosmetic: binding first would give a row
        reading "0.0.0.0:8080" on a board with no network, which looks like
        success and is not.

        `ip` is the address to ADVERTISE, not to bind -- the socket still
        listens on every interface. The base transport reports whatever it was
        given, which is 0.0.0.0 by default, and 0.0.0.0 is exactly the one
        address a kid cannot type into a browser.
        """
        if self._ensure_online is not None and ip is None:
            ip = self._ensure_online()
        # The base start() RETURNS False on a bind failure rather than raising
        # (it is guarded so a busy port cannot take the loop down). Checking it
        # is what stops the row saying ON over a server that is serving nothing.
        if not WebServer.start(self, ip):
            raise OSError("port %d busy" % self.port)
        self.serving = True
        try:
            print("WEBHOST", self.source_note())
        except Exception:                # noqa: BLE001 -- a log is never fatal
            pass

    def source_note(self):
        """One line naming WHICH bundle this board is about to serve.

        Storage overriding the image is the right precedence and also the exact
        shape of the old bug -- a stale pushed copy shadowing a good one, with
        nothing anywhere saying so. It cost a day the last time. So the override
        announces itself, with the image's own stamp beside it: two strings on
        one serial line, and the question stops needing an investigation.

        The probe goes through the storage gate, because on the T-Deck a stat
        of /sd/web touches a card that shares the panel's SPI host.
        """
        pushed = self._with_sd(lambda: [
            n for n in ASSETS
            if _file_size(self.web_dir + "/" + n + ".gz") is not None
            or _file_size(self.web_dir + "/" + n) is not None])
        stamp = baked_stamp()
        if not pushed:
            return "serving the bundle baked into this firmware (%s)" % (
                stamp or "NONE -- this image has no web console")
        if len(pushed) == len(ASSETS):
            return "serving the PUSHED copy at %s, not the image's (%s)" % (
                self.web_dir, stamp or "none")
        return "MIXED: %d of %d assets pushed to %s, the rest from the " \
               "image (%s) -- push all four or delete them" % (
                   len(pushed), len(ASSETS), self.web_dir, stamp or "none")

    def stop(self):
        WebServer.stop(self)
        self.serving = False

    def handle_http(self, method, path, body):
        if method == "POST" and path.split("?", 1)[0] == "/sync":
            return self._sync(body)
        if method != "GET":
            return http_response(405, '{"error":"GET only (writes ride POST /sync)"}')
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
        # A PRE-GZIPPED copy wins when it is there. The board does no
        # compressing -- it serves `<name>.gz` verbatim and lets the browser
        # inflate it, so the only cost is picking the file. Worth it because
        # the wire is the expensive part here: the four assets are 1,155,953 B
        # raw and 572,747 B gzipped, and on the T-Deck they stream off SD
        # inside the DMA gate, so halving the bytes halves the window in which
        # the console is handing its storage to the socket.
        # Raw stays the fallback: `dist/` keeps both, because a plain static
        # host (moybyte.com, tools/serve.py) sets no Content-Encoding, and a
        # browser handed gzip bytes without that header sees garbage.
        #
        # STORAGE WINS OVER THE IMAGE, and that order is the decision. A copy
        # on the board is an explicit human action -- tools/p4_push_web.py, a
        # card reader -- and if the baked copy took precedence that dev loop
        # would die and a baked bundle would be a bundle nobody could iterate
        # on. The image's job is the GUARANTEE: a board that has never been
        # pushed to still serves a console, and it is the one its firmware was
        # built from. (Which also means a HALF-pushed bundle is a mixed one --
        # push all four or none; p4_push_web does.)
        #
        # The PROBE must go through `_with_sd` too, not just the body: on the
        # T-Deck `web_dir` is on the card sharing the panel's SPI host, and this
        # runs at the frame tail where a painted frame has just kicked a flush
        # the core-0 feeder is still shipping. A bare `os.stat` there is an
        # sdspi transaction concurrent with band queueing from the other core --
        # the Cache/MMU panic modmoy_lcd.c's SD SESSION GUARD exists for. A miss
        # costs the same directory read as a hit, so a board serving the BAKED
        # bundle is not exempt either.
        full = self.web_dir + "/" + name
        pushed = self._with_sd(lambda: _pushed_copy(full))
        if pushed is not None:
            path, size, encoding = pushed
            return FileResponse(path, size, ASSETS[name], encoding=encoding)
        blob = _baked(name + ".gz")
        if blob is not None:
            return BlobResponse(blob, ASSETS[name], encoding="gzip")
        blob = _baked(name)
        if blob is not None:
            return BlobResponse(blob, ASSETS[name])
        # Nothing on storage and nothing in the image -- a firmware built
        # without a web bundle (build.sh says so loudly at build time). Name
        # the directory AND the reason, because the failure is a setup step and
        # not a bug, and a bare 404 in a browser tells the owner nothing.
        return http_response(
            404, "no web bundle in this firmware and none at %s -- build "
                 "firmware/web_runner/dist and reflash, or copy it there"
                 % self.web_dir, "text/plain; charset=utf-8")

    def _sync(self, body):
        """Apply one push batch into the store (moy_sync.apply_ops -- the same
        function the CI convergence harness and the dev server run).

        The whole apply runs INSIDE one storage-gate entry: a batch is bounded
        (the transport refuses requests past 64KB), and on the T-Deck every
        one of these writes lands on the card that shares the panel's SPI
        host, at the frame tail where the feeder may still be shipping bands
        -- the same reason the asset probe takes the gate. Boards without
        shared storage pass a call-through and pay nothing.

        A bad op SKIPS (reported in `err`), it never aborts the batch: the
        client clears an answered batch either way, so aborting would just
        make one poison op eat its innocent neighbours forever.
        """
        ops, pin = moy_sync.parse_batch(body)
        if ops is None:
            return http_response(400, '{"error":"bad batch"}')
        if self.pin and pin != self.pin:
            return http_response(403, '{"error":"pin"}')
        applied, errors, shelf_dirty = self._with_sd(
            lambda: moy_sync.apply_ops(self.carts_root, ops))
        if errors:
            try:
                print("SYNC %d applied, %d refused: %s"
                      % (applied, len(errors), errors[0][1]))
            except Exception:            # noqa: BLE001 -- a log is never fatal
                pass
        if shelf_dirty and self.on_sync is not None:
            try:
                self.on_sync()
            except Exception as exc:     # noqa: BLE001 -- never fail the request
                print("SYNC rescan failed:", exc)
        return http_response(200, _json.dumps(
            {"ok": applied, "err": [list(e) for e in errors[:8]]}))

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


def ensure_online(wifi, autoconnect=None, wait_ms=12000, step_ms=250):
    """Connect if needed, WAIT for the link, then report the STA IP.

    The wait is not optional and the reason is recorded in moy_ota's own
    ensure_online: `connect()` polls for 4s and gives up, and on the P4 a saved
    network measured 1.5s SLOWER than that (its radio is a separate C6 over
    SDIO, so cold association is slow). Without the wait a perfectly good
    network reads as "no wifi" -- which is exactly what the WEB CONSOLE row did
    on its first try.

    This lives here, and not in either board's run_desktop, because it is the
    same 25 lines on both. That is not a hypothetical: the web console shipped
    on the P4 with every SHARED piece already in place -- moy_webhost itself,
    the Settings row, the console verbs, all staged from one source -- and the
    T-Deck still did not have the feature, because the one per-board injection
    was never written for it. The row is capability-gated on `ws.webhost`, so
    the whole thing failed by being invisible rather than by breaking.
    """
    if wifi is None:
        raise OSError("no wifi service")
    if not wifi.status()[0]:
        if autoconnect is not None:
            try:
                autoconnect(wifi)
            except Exception:          # noqa: BLE001 -- the wait below decides
                pass
        import time
        _sleep_ms = getattr(time, "sleep_ms", None)
        for _ in range(max(1, wait_ms // step_ms)):
            if wifi.status()[0]:
                break
            if _sleep_ms is not None:
                _sleep_ms(step_ms)
            else:                      # host/CPython: no sleep_ms
                time.sleep(step_ms / 1000.0)
    st = wifi.status()
    if not st[0]:
        raise OSError("no wifi")
    return st[2]                       # the STA IP: what the row displays


def make_webhost(ws, carts_root, web_dir, autoconnect=None, with_sd=None,
                 port=None, pin=None):
    """The WebHost every board injects -- built once, here.

    Takes `ws` rather than `ws.wifi` because the wifi service is attached by
    wire_workstation_core, which has not necessarily run when a board builds
    this; the closure reads it at TOGGLE time, which is the only moment it
    matters. The sync push's shelf refresh is wired HERE for the same reason
    the wait lives in ensure_online: it is the same line on every board, and
    a per-board injection is exactly what left the T-Deck without the web
    console the first time.

    Constructed, NOT started: __init__ binds no socket, so injecting this costs
    nothing until a kid turns the row on. The two things that genuinely differ
    per board are the arguments -- the web directory (/sd/web vs /moy/web) and
    the SD gate (the T-Deck's store is on a shared-SPI card; the P4 has none).
    """
    return WebHost(carts_root, web_dir, port=port, with_sd=with_sd,
                   ensure_online=lambda: ensure_online(
                       getattr(ws, "wifi", None), autoconnect),
                   pin=pin, on_sync=lambda: ws.rescan_carts())
