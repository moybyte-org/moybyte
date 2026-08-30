"""Serve the moybyte web console FROM the console, over the device's own WiFi.

This is what replaces the streaming web view (#100), and it is the opposite
trade. The stream shipped PIXELS: every frame crossed the wire, so the browser
ran at the WiFi's speed and the defect class was cache agreement across a lossy
transport. This ships the CONSOLE: the board hands the browser the wasm head
once, the page then runs the whole shell locally at full speed, and the only
thing that crosses the wire afterwards is cart data.

    GET  /               -> index.html          (the wasm console)
    GET  /<asset>        -> micropython.wasm, worker.js, ...   [streamed]
    GET  /sync           -> {"sync": 1}: this host HAS the push half
    GET  /carts.json     -> THIS BOARD's cart store, packed live        [pin]
    GET  /files.json     -> its #108 user files (drawings/docs/...)     [pin]
    POST /sync           -> apply a commit-shaped batch INTO either store [pin]
    POST /run            -> play a cart on the BOARD's own glass (#197)   [pin]
    GET  /update         -> this board's FIRMWARE state (#41/#53)         [pin]
    POST /update         -> ask it to update                              [pin]

[pin] = refused 403 without the host's pairing pin (SECURITY, below). The two
lines above it are open on purpose, and each for its own reason.

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

BOTH STORES ride that one endpoint (2026-08-25): the batch names its root, and
a files batch lands under moy_sync.files_root instead. Nothing about the files
half touches the shelf -- the launcher renders no drawings, and the Files app
scans its kinds when it opens -- so `on_sync` stays a carts-only hook. Serving
GET /files.json is also what a browser reads as "this board speaks files": an
older board 404s, and the page then never builds its files watcher at all.

PLAY ON DEVICE is POST /run (#197): the browser hands back a cart NAME and the
board plays it on its own glass. That is the whole protocol -- the page needs to
know nothing else about device state, because the exit is the console's own
(a cart run this way returns to the connection screen, not to a shelf the
browser may be rewriting). The launch itself is `ws.launch_named`, the same body
the serial dev channel's `run` goes through.

SECURITY: THE PIN GATES EVERYTHING (owner call 2026-08-25, reversing the
standing "the read half stays open"). A host that has a pin serves exactly one
thing to a stranger -- the boot assets -- and refuses every request that would
reveal or change what is on the board:

    OPEN, always      index.html, worker.js, moy_store.mjs, micropython.mjs,
                      micropython.wasm      the page must LOAD in order to ask
    OPEN, always      GET /sync             the capability marker: it says "a
                      board lives here" and nothing else, and the page's mode
                      decision is made before it has a pin to offer
    PIN               GET /carts.json, GET /files.json    the kid's work
    PIN               POST /sync, POST /run, GET+POST /update
                                                        (and /gpio on the Zero)

The old split gave any device on the same WiFi the whole cart store for the
asking, on the reasoning that reading changes nothing. It reads a child's
work off their console, which is the part that reasoning left out.

A GET carries its pin the only place a GET can, `?pin=NNNN` -- which is why
`moy_webserver.parse_request` stopped stripping query strings: it was spending
the credential before any handler saw it. A gated GET without the right pin is
403 with `{"error":"pin"}`, deliberately distinguishable from the transport's
plain-text 404, because the PAGE branches on it: worker.js stops its boot and
the page prompts for a pin instead of showing a broken console.

The BOARDS PASS ONE SINCE #197: `make_webhost` reads `ws.web_pin()` at START,
the connection screen shows it as a QR of `http://<ip>/?pin=NNNN`, and the
page forwards its own `?pin=` into every request. Read that order carefully --
the pin is resolved in `start()`, not in `__init__`, because a board CONSTRUCTS
this before system.json is loaded and a pin captured then would be a pin minted
from an empty store. A host built with `pin=None` (a test, the dev server) is
open end to end, which is what keeps the LAN dev loop free of a password.

FIRMWARE UPDATES ARE TWO ROUTES AND TWO BACKENDS (2026-08-29). The ROUTES are
here, shared, because every board runs this host and every board runs the same
`moy_ota.OtaUpdater`; they lived on the Zero alone until now, which made "the
browser can update the board that serves it" true on exactly one board. What is
NOT shared is what a trigger MEANS, and it must not be:

  * a board WITH GLASS (`ConsoleUpdate`, below) HANDS THE GLASS BACK. Its own
    update screen (runtime/update_ui.py) already installs a chunk per PAINTED
    FRAME of that screen and takes its two confirms there. So the POST turns
    WASM MODE OFF -- which unparks the glass -- and opens that screen. It
    starts no install here, moves no chunk work into `poll()`, and therefore
    never puts an SD read at the frame tail, where the T-Deck's shared bus
    makes one the documented panic.
  * a HEADLESS board (`zero_host.ZeroUpdate`) DRIVES the install in its own
    poll loop, because it has no glass to hand back to -- and no card either,
    so its slices share nothing with a panel.

Same doctrine as moy_ota's two rollback-confirm gates: do NOT merge them behind
a flag. The backend is where the claim about the hardware lives, and `screen`
in the status document is that claim said out loud to the page, which needs it
to know whether it is about to become the progress display or to stop being the
console.

THE JOURNAL: this board is the STORE OF RECORD for a page it serves, so the
apply path journals (`moy_sync.apply_ops(..., journal=True)`) -- a browser-made
commit lands in the cart's own `journal/` and the console's UNDO walks back
through it. moy_sync's docstring carries the doctrine and the cost.
"""

try:
    import os
except ImportError:                      # pragma: no cover
    os = None

import json as _json

from moy_webserver import (WebServer, http_response, FileResponse,
                           ChunkedResponse, BlobResponse, query_param)

# The tree's one clock shim (runtime/ticks.py), staged to every board and
# importable on the host -- so the closing grace window below is testable
# without a board and cannot drift from the frame loop's own clock.
try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:                      # pragma: no cover -- host package lane
    from runtime.ticks import _ticks_ms, _ticks_diff

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
# The one refusal body every gated endpoint answers with, here rather than
# spelled five times: the page tests for it, and a page and a board disagreeing
# about the shape of "you need a pin" is a prompt that never appears.
PIN_REFUSED = '{"error":"pin"}'
# ...and the one a board with no updatable firmware answers /update with. A
# board that simply has no such route 404s, and the page reads the two
# differently: 404 means "not a board, or one flashed before this" and hides
# the surface for good; 503 means "a board, but nothing to update" and says so.
NO_UPDATER = '{"error":"no updater"}'


def pin_ok(pin, sent):
    """May a request carrying `sent` proceed against a host holding `pin`?
    No pin configured = open (a test, the dev server, a pre-#197 board). ONE
    body, so the device host and the dev twin cannot drift about what the gate
    means -- serve.py imports it."""
    return (not pin) or (sent == pin)


ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "worker.js": "text/javascript; charset=utf-8",
    # worker.js IMPORTS this one, so a board that does not serve it serves a
    # console that cannot boot. It is mode 1's store (#193) and the .moy zip
    # codec; a board-served page uses only its mode probe, but the import is
    # static and the module has to be here.
    "moy_store.mjs": "text/javascript; charset=utf-8",
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


def pack_store(carts_root, listdir=None, read=None, isdir=None, tops=None):
    """The whole store as the page's bundle shape: {"<top>/<rel>": text}.

    The shape is `worker.js`'s, not a new one -- it is what the dev twin
    (`firmware/web_runner/serve.py --carts`, which calls THIS function) serves
    and what `writeCarts` already consumes, so the page needs no change to
    read a board instead of a build.

    `tops` restricts the top-level directories that are walked at all: None
    (every dir) for the carts root, moy_sync.file_kinds() for the files root,
    where it is the one rule that keeps `.history` and `trash` home.

    The three injected callables are for testing on the host, where there is no
    device filesystem; they default to the real ones.
    """
    _read = read or _read_text
    out = {}
    for top in _root_dirs(carts_root, listdir, isdir, tops):
        _pack_dir(out, carts_root + "/" + top, top, listdir, isdir, _read)
    return out


def _root_dirs(root, listdir, isdir, tops):
    """The top-level directories a store walk descends into.

    ONE body for both walkers: they are independent generators whose output must
    agree, and the `tops` allowlist is the whole of what makes a files root
    different from a carts one. Missing root -> nothing: the carts root always
    exists, the files root is created the first time a kid makes something.
    """
    try:
        entries = sorted(_entries(root, listdir, isdir))
    except OSError:
        return []
    return [n for n, isdir_ in entries
            if isdir_ and (tops is None or n in tops)]


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


def stream_store_json(carts_root, listdir=None, read=None, isdir=None,
                      tops=None):
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
    for top in _root_dirs(carts_root, listdir, isdir, tops):
        for piece in _stream_dir(carts_root + "/" + top, top,
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


def _ota_board():
    """Which board this image is for. `moy_ota.BOARD` and nothing derived: an
    OTA payload is an app-partition image, so another board's is a valid image
    that cannot boot, and the page shows the name for the same reason the
    manifest signs it."""
    try:
        import moy_ota

        return moy_ota.BOARD
    except Exception:                    # noqa: BLE001 -- a host/no-OTA build
        return "?"


def update_status(ota, state, screen, error=None, offer=None, progress=None,
                  absent=False, staged=None):
    """The ONE /update document, whichever backend answered.

    Both backends build it here, so the page has one shape to read and a board
    that grows a third arrangement cannot invent a fourth. The fields:

      state      the backend's own state word. Deliberately NOT normalised
                 across the two: they are different machines, and flattening
                 "the glass took over" into "downloading" would be a page
                 drawing a progress bar for bytes nobody is moving.
      screen     does this board have glass of its own. A HARDWARE FACT, and
                 the one field the page must have before it offers anything:
                 True means triggering an update ENDS this page's job as the
                 console, False means this document is the only progress
                 report that exists. Present in both states on purpose -- the
                 "absence means no lever" rule is about MEASUREMENTS, and here
                 an omitted key would make a headless board and a board too
                 old to have this route read the same.
      running    the firmware now running -- what the update screens show.
      last       the PREVIOUS install's verdict, when the boot found a marker.
                 The headless replacement for the console's notice banner, and
                 why the marker is cleared at the CONFIRM rather than the read.
      available  what the last check found, when it found anything.
      progress   {done, total} while bytes are actually moving, and ABSENT
                 otherwise: a frozen 0/0 is what a broken transfer looks like
                 too, which is the ambiguity `fold=0` cost this repo weeks.
      absent     the channel has published nothing for this board yet -- the
                 normal state of a channel before its first release, and not
                 the same sentence as "up to date".
      staged     where a downloaded image is waiting for the second confirm.
                 NAMED by the board rather than guessed by the reader: the
                 screen prints its basename, and the boards stage in different
                 places (a card on one, internal flash on another).
      error      the last failure, in words a person can act on.
    """
    out = {
        "state": state,
        "screen": bool(screen),
        "running": {"version": ota.version(), "label": ota.version_label(),
                    "channel": ota.channel(), "slot": ota.slot(),
                    "board": _ota_board()},
    }
    if error:
        out["error"] = error
    verdict = getattr(ota, "boot_verdict", None)
    if verdict:
        out["last"] = {"result": verdict[0], "detail": verdict[1]}
    if offer:
        out["available"] = offer
    if progress:
        out["progress"] = progress
    if absent:
        out["absent"] = True
    if staged:
        out["staged"] = staged
    return out


class ConsoleUpdate:
    """The /update backend on a board WITH GLASS: a HAND-OFF, not a driver.

    The browser does not install anything here, and deliberately cannot. This
    board already owns the update flow -- `runtime/update_ui.py` advances the
    flash ONE CHUNK PER PAINTED FRAME of its update screen, and takes two
    deliberate confirms on the glass to get there. A browser-driven install
    would need a SECOND driver for the same `OtaUpdater`, running while the
    glass is parked on the WEB CONSOLE connection screen and the update screen
    is not up at all: on the T-Deck that driver's chunks would be SD reads at
    the frame tail, concurrent with a flush the feeder is still shipping, which
    is the documented Cache/MMU panic. So the browser's part is the TRIGGER.

    What a request does: turn WASM MODE OFF -- which unparks the glass -- and
    open the update screen there. Both halves go through the console's own one
    funnel (`ws.web.stop()`), because the mode and the socket must not be able
    to disagree about which of them is on.

    THE POST ANSWERS FIRST; THE HAND-OFF HAPPENS ON THE NEXT POLL. Turning wasm
    mode off closes the socket the request arrived on, so doing it inside the
    handler would race the one answer the page needs in order to say what just
    happened to it. Same queue, same reason, as ZeroUpdate's.

    NOTHING HERE IS UNATTENDED, and this is the load-bearing sentence. A
    request OPENS A SCREEN. The confirm that starts the download and the
    confirm that starts the flash are still taps on the board's own glass, by
    somebody holding the board. There is no setting that changes that (the
    `ota_auto` flag the Zero briefly had was deleted on the owner's call, and
    this is not a way to reintroduce it one board over).
    """

    def __init__(self, ws):
        self.ws = ws
        self.state = "idle"
        self.error = None
        self._want = False       # a queued hand-off, applied by the next step()

    # -- the request side ----------------------------------------------------

    def request(self, action, channel=None):
        """Queue the hand-off. Returns (ok, message) -- the message is what the
        caller shows, so it says what happened to the CALLER, not to the board.

        `check`, `download` and `install` are the SAME request here and all
        three are accepted: what this board can be asked for is "start the
        update flow", and the flow itself decides what to check, download and
        install -- on the glass, where the person is. A caller that wanted
        finer control is asking the wrong machine.

        `channel` is ACCEPTED AND IGNORED, deliberately. The screen this hands
        off to carries the console's own CHANNEL row, which the person is about
        to be looking at; taking the remote caller's choice would silently
        overwrite a setting that is visible two lines above the confirm.
        """
        if action not in ("check", "download", "install", "cancel"):
            return False, "action must be check, download, install or cancel"
        if action == "cancel":
            # Nothing here to cancel: what a request starts lives on the glass,
            # and its cancel is the X on that screen, in front of whoever is
            # looking at it.
            return False, "the console's own screen owns this update"
        if self._updater() is None:
            return False, "this console cannot update itself"
        if self.state == "glass" or self._want:
            return False, "the console already has this on its screen"
        # THE STATE MOVES HERE and the WORK on the next poll. A caller polling
        # in between must read "the glass has it", not the state the request
        # had not been applied to yet -- which it would read as a tap that did
        # nothing. A retry after a failure also starts clean: leaving the old
        # error standing would report the LAST attempt while this one is live.
        self.error = None
        self.state = "glass"
        self._want = True
        return True, "the console took this onto its own screen"

    # -- the pumped side -----------------------------------------------------

    def step(self):
        """One slice, from `WebHost.poll()`. True if it did work."""
        if not self._want:
            return False
        self._want = False
        ws = self.ws
        try:
            web = getattr(ws, "web", None)
            if web is not None:
                # THE ONE FUNNEL (web_console.WebConsole.stop): it stops the
                # host AND unparks the glass, and it asks what the user wants
                # rather than toggling -- a host that had already fallen over
                # under the parked screen would otherwise be STARTED here.
                # A BARE stop, deliberately: no closing window here.
                #
                # This path can FAIL after the stop -- `open_update_online`
                # below raises and the board is left at its desktop, not
                # updating -- and the page is where that error is readable. A
                # host that had already gone into its goodbye window would
                # answer 503 to the very request that was going to explain
                # what went wrong.
                #
                # It costs nothing, because this is the console-board branch
                # (`screen: true`): the page told this board to update on its
                # OWN screen, so it already knows what the silence means. The
                # window exists for the case that has no other signal -- a
                # person switching WEB CONSOLE off, which the page cannot
                # otherwise tell from an unplugged board.
                web.stop(why=None)
            # ...and only now the screen, because unparking routes home and
            # would pop an update screen pushed before it.
            ws.update_ui.open_update_online()
        except Exception as exc:         # noqa: BLE001 -- never break a frame
            self.state = "error"
            self.error = "%s" % exc
            print("UPDATE hand-off failed:", exc)
        return True

    # -- what the page reads -------------------------------------------------

    def status(self):
        """The status document, or None when this board cannot update itself.

        None is turned into the same 503 a board with no updater at all gives,
        so the page's probe has exactly two meanings: 404 = not a board that
        speaks this, 503 = a board with nothing to offer.
        """
        ota = self._updater()
        if ota is None:
            return None
        return update_status(ota, self.state, True, error=self.error)

    def _updater(self):
        """The board's OtaUpdater when this build can take an OTA, else None.

        Read off `ws` at every call and never captured: a board constructs the
        webhost before `wire_workstation_core` has attached anything, which is
        the same ordering that keeps `make_webhost`'s pin lazy. The capability
        question goes through the console's own cached query rather than
        `available()` -- it is asked once per poll and the answer is fixed for
        a boot.
        """
        ota = getattr(self.ws, "updater", None)
        if ota is None:
            return None
        try:
            return ota if self.ws._update_available() else None
        except Exception:                # noqa: BLE001 -- a ws without the query
            return None


class WebHost(WebServer):
    """The transport, with the console's own pages and store wired to it."""

    def __init__(self, carts_root, web_dir, port=None, with_sd=None,
                 ensure_online=None, pin=None, on_sync=None, pin_source=None,
                 on_run=None, update=None):
        if port is None:
            WebServer.__init__(self)
        else:
            WebServer.__init__(self, port=port)
        self.carts_root = carts_root
        self.web_dir = web_dir
        # The push half's consent gate + the console's shelf-refresh hook (see
        # the module docstring). pin=None means the write endpoint is as open
        # as the read one -- what a test or the dev server wants, and NOT what
        # a board passes since #197. on_sync fires only for batches that
        # changed the SHELF; on_run(name) plays a cart on the board's glass.
        self.pin = pin
        # ...and where a LIVE pin comes from. Resolved in start(), never here:
        # a board builds this before system.json is loaded, so reading the pin
        # at construction would mint one against an empty store and then serve
        # a QR nobody's system.json agrees with.
        self._pin_source = pin_source
        self.on_sync = on_sync
        self.on_run = on_run
        # The /update backend (ConsoleUpdate here, zero_host.ZeroUpdate on the
        # headless board). None = this host serves carts and no firmware route,
        # which is what the dev twin and every test that does not care about
        # OTA want -- and what an updater that would not CONSTRUCT leaves
        # behind, because a board that cannot update must still serve carts.
        self.update = update
        # Settings contract (console.Workstation.toggle_webhost): `.serving`,
        # `.start()`, `.stop()`, `.url()`, and `.error` for a failure the row can
        # show. Constructed but NOT started -- __init__ binds no socket, so
        # injecting this costs nothing until a kid turns it on.
        self.serving = False
        self.error = None
        # The goodbye (see stop()). None = serving normally; a reason string =
        # in the grace window, answering every request with it and about to
        # close. `closing_at` is meaningless while `closing` is None.
        self.closing = None
        self.closing_at = 0
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
        reading "0.0.0.0" on a board with no network, which looks like success
        and is not.

        `ip` is the address to ADVERTISE, not to bind -- the socket still
        listens on every interface. The base transport reports whatever it was
        given, which is 0.0.0.0 by default, and 0.0.0.0 is exactly the one
        address a kid cannot type into a browser.
        """
        if self._pin_source is not None:
            # BEFORE the bind: the pin is what the connection screen puts in
            # its QR, and a socket accepting writes for even one poll without
            # the gate its own screen advertises is the bug this ordering
            # exists to make impossible.
            try:
                self.pin = self._pin_source() or None
            except Exception as exc:  # noqa: BLE001
                # A store that cannot be written (no card, read-only) must not
                # cost the feature -- but it must not silently serve an OPEN
                # write endpoint either, so say so and keep whatever pin the
                # host was built with.
                print("WEBHOST pin unavailable:", exc)
        if self._ensure_online is not None and ip is None:
            ip = self._ensure_online()
        # The base start() RETURNS False on a bind failure rather than raising
        # (it is guarded so a busy port cannot take the loop down). Checking it
        # is what stops the row saying ON over a server that is serving nothing.
        if not WebServer.start(self, ip):
            raise OSError("port %d busy" % self.port)
        self.serving = True
        # A restart inside the grace window cancels the goodbye: the board is
        # not going anywhere after all, and a host that stayed "closing" would
        # answer 503 to every request while its row said ON.
        self.closing = None
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
               "image (%s) -- push them all or delete them" % (
                   len(pushed), len(ASSETS), self.web_dir, stamp or "none")

    def poll(self):
        """One transport poll, then ONE slice of the update backend.

        The slice runs AFTER the transport's and never inside a handler, and
        both backends need that: the console board's turns wasm mode off, which
        closes the socket the request arrived on, and the Zero's advances an
        install. Each is something the request must already have been ANSWERED
        before. Guarded, because on a console board this is the frame tail and
        a firmware endpoint must not be able to break a frame.
        """
        # The goodbye's countdown, before anything else: once the window is up
        # there is nothing left to serve or pump, and the socket should go.
        if self.closing is not None:
            if _ticks_diff(_ticks_ms(), self.closing_at) >= self.CLOSING_MS:
                self.stop()      # for real this time: socket closed, flag cleared
                return False
        did = WebServer.poll(self)
        u = self.update
        if u is not None:
            try:
                if u.step():
                    did = True
            except Exception as exc:     # noqa: BLE001 -- never break a frame
                print("UPDATE ERR %s: %s" % (type(exc).__name__, exc))
        return did

    # WebServer aliases `service = poll` at class scope, which binds the BASE
    # body -- so a caller reaching for that name would get the transport
    # without the pump. Re-aliased here rather than left as a trap.
    service = poll

    # How long a board that is going away KEEPS ANSWERING, to say so. The page
    # asks every 3s when idle (worker.js HEARTBEAT_MS) and every 1s while it has
    # something to push, so this has to outlast the slower of those plus a
    # request already in flight. Five seconds of a socket nobody is using, once,
    # in exchange for the difference between "your console handed the screen
    # back" and "your console vanished, your work may be lost".
    CLOSING_MS = 5000

    def stop(self, why=None):
        """Stop serving. With `why`, SAY SO first.

        A bare stop() closes the socket, and from the far end that is
        indistinguishable from the board being unplugged -- so a kid who turned
        WEB CONSOLE off themselves, or asked for an update, got the page's
        vanished-board panel with its data-loss warning. Reported from a P4
        session on 2026-08-30, and the page had carried an "expected" kind for
        this since the surface was written with NOTHING in the system able to
        produce it.

        The board is the only thing that knows the difference, and it knows it
        BEFORE it goes. So a `why` starts a short grace window in which every
        request is answered `{"error":"closing","why":...}` -- one round trip is
        all the page needs -- and the socket closes when it expires.

        `serving` GOES FALSE AT ONCE, and that separation is the whole point.
        `serving` is the console's notion of the feature being ON: the Settings
        row reads it, and `web_console.toggle` unparks the glass on it. Holding
        it true across the window would leave a kid staring at a parked screen
        for five seconds after they turned the row off. The lingering socket is
        a transport detail, so it gets its own flag and `device_boot.poll_webhost`
        polls on either.

        Without a `why` the old behaviour stands, which is what a test teardown
        and an error path want: no window, no waiting.
        """
        self.serving = False
        if why is None:
            WebServer.stop(self)
            self.closing = None
            return
        self.closing = why
        self.closing_at = _ticks_ms()


    def paired_url(self):
        """`url()` with the pin on it -- the ONE string worth handing a human.

        The page forwards its own `?pin=` into every batch it posts, so this is
        the entire pairing gesture: scan it, or type it, and the browser can
        write back. Without a pin it is just `url()`, which is what a host built
        open (a test, the dev server) should show."""
        base = self.url()
        if not self.pin:
            return base
        return base + "?pin=" + str(self.pin)

    def gate(self, target):
        """None when a gated request may proceed, else the 403 to answer with.

        `target` is the request target, so the pin comes off `?pin=`. Called
        before the endpoint does any work at all -- refusing after a store walk
        would leak its timing and, on the T-Deck, take the SD gate to do it.
        """
        if pin_ok(self.pin, query_param(target, "pin")):
            return None
        return http_response(403, PIN_REFUSED)

    def handle_http(self, method, path, body):
        # THE GOODBYE, before routing and before the pin: a board in its closing
        # window is going away on purpose and the only useful thing it can say
        # is that. Ahead of the gate deliberately -- a page that was never
        # given a pin still deserves to be told the console was switched off
        # rather than left to conclude it vanished, and this leaks nothing but
        # the fact that somebody turned it off.
        if self.closing is not None:
            return http_response(
                503, '{"error":"closing","why":"%s"}' % self.closing)
        # `path` is the request TARGET (query string included, since 2026-08-25
        # -- see the SECURITY note). Route on the bare path, gate on the target.
        target = path
        path = target.split("?", 1)[0]
        if method == "POST":
            if path == "/sync":
                return self._sync(body)
            if path == "/run":
                return self._run(body)
            if path == "/update":
                return self._update(target, body)
        if method != "GET":
            # The refusal NAMES the write paths. A page that guessed a verb
            # gets a sentence it can act on, where a bare 405 reads exactly
            # like a wrong url.
            return http_response(
                405, '{"error":"GET, or POST /sync /run /update"}')
        if path == "/sync":
            # The CAPABILITY MARKER, and the one GET that stays OPEN on a
            # pinned board. It reveals that a board lives here and nothing
            # else -- no cart, no name, no file -- and the page has to make its
            # mode decision (board store vs browser store) BEFORE it has any
            # pin to offer. A static host (moybyte.com, an export, `python -m
            # http.server` over dist/) 404s it, which is exactly how the page
            # learns not to offer PLAY ON DEVICE and not to build a sync loop
            # against a wall.
            return http_response(200, '{"sync":1}')
        if path == "/update":
            return self._update(target, None)
        if path == "/" or path == "/index.html":
            return self._asset("index.html")
        name = path[1:]
        if name in ASSETS:
            # The boot assets are open BY NECESSITY: the page is what shows the
            # pin prompt, so a board that gated its own console behind a pin
            # would have nothing left to ask the question with. They are the
            # same bytes every build of the console ships and say nothing about
            # this board.
            return self._asset(name)
        # The pull endpoints are DATA: one per registered sync root
        # (moy_sync.SYNC_ROOTS), so a new store serves its own GET with no new
        # arm here. Each is pin-gated -- it is the kid's work.
        for root in moy_sync.SYNC_ROOTS:
            if path == root.endpoint:
                return self.gate(target) or self._pack(root)
        return None                      # -> 404 from the transport

    def _asset(self, name):
        # A PRE-GZIPPED copy wins when it is there. The board does no
        # compressing -- it serves `<name>.gz` verbatim and lets the browser
        # inflate it, so the only cost is picking the file. Worth it because
        # the wire is the expensive part here: the assets are 1,230,814 B
        # raw and 609,268 B gzipped, and on the T-Deck they stream off SD
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
        # push them all or none; p4_push_web does.)
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

        AND IT JOURNALS (2026-08-25). This board is the store of record for the
        page that sent this, so every carts-root file the batch publishes gets
        a #111 commit in the shape the console's own commits use -- which is
        what makes the kid's on-glass UNDO able to walk back through an edit
        made in a browser. Inside the same gate entry as the writes, because
        the snapshot lands on the same card. The pin rides the BODY here (a
        POST has somewhere to put it); the gated GETs read `?pin=`.
        """
        ops, pin, root_id = moy_sync.parse_batch(body)
        if ops is None:
            return http_response(400, '{"error":"bad batch"}')
        if not pin_ok(self.pin, pin):
            return http_response(403, PIN_REFUSED)
        target = self._root_for(root_id)
        if target is None:
            return http_response(400, '{"error":"no such store"}')
        applied, errors, shelf_dirty = self._with_sd(
            lambda: moy_sync.apply_ops(target, ops, root_id, journal=True))
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

    def _run(self, body):
        """PLAY ON DEVICE (#197): `{"cart": "<title or folder>", "pin": ...}`.

        Pin-gated exactly like /sync -- this starts code on the kid's console,
        so it is a WRITE by any reading of the word.

        The launch runs INSIDE the storage gate for the same reason the apply
        does, and it is easy to miss why: `handle_http` is called from poll(),
        which the boards run at the FRAME TAIL, where a frame that painted has
        just kicked a flush the feeder may still be shipping. Opening a cart
        reads its manifest, code, sheet and pmem off the store -- on the T-Deck
        that is the card sharing the panel's SPI host, and an sdspi transaction
        there is the documented Cache/MMU panic.

        The answer carries the cart's TITLE, not an ack: the page sent a name it
        guessed at, and echoing what actually started is how it can say so."""
        try:
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            doc = _json.loads(body)
        except Exception:                # noqa: BLE001
            doc = None
        if not isinstance(doc, dict):
            return http_response(400, '{"error":"bad body"}')
        if not pin_ok(self.pin, doc.get("pin")):
            return http_response(403, PIN_REFUSED)
        if self.on_run is None:
            # A host with no console behind it (the dev server, a test): say so
            # rather than 404, which the page would read as "no such endpoint"
            # and stop offering the button over a board that simply has none.
            return http_response(501, '{"error":"no runner"}')
        name = doc.get("cart")
        # A named launch, not a bare one: `launch_named("")` runs the FIRST cart
        # (the serial `run` with no argument), which is wrong for a PLAY ON DEVICE
        # whose body arrived without a cart -- it would start a random game and,
        # for a titleless one, then answer 404. The page always names its cart;
        # an empty name is a malformed request, refused before anything launches.
        if not name or not str(name).strip():
            return http_response(400, '{"error":"no cart named"}')
        try:
            title = self._with_sd(lambda: self.on_run(name))
        except Exception as exc:         # noqa: BLE001 -- never fail the request
            print("RUN failed:", exc)
            return http_response(500, '{"error":"run failed"}')
        if not title:
            return http_response(404, '{"error":"no cart"}')
        return http_response(200, _json.dumps({"run": title}))

    def _update(self, target, body):
        """The firmware endpoint (#41/#53). `body` is None on the GET.

        BOTH METHODS ARE GATED, with no read-half exemption -- the same call
        `/gpio`'s GET was brought under on 2026-08-25. What a GET here reveals
        is which firmware a specific board on somebody's home network is
        running, which is a shopping list for whoever wants to hand it an
        image; and the write half is the strongest verb a board has, since it
        replaces the board.

        BOTH READ THE PIN OFF `?pin=`, and that is deliberate even though
        /sync and /run take theirs from the body. A GET has nowhere else to
        carry a credential, and ONE endpoint whose two methods spend the
        credential in two different places is a rule nobody holds -- so the
        query is the rule here and the page puts it on both. (This is the
        shape the Zero's endpoints already shipped with, and the board's
        README documents it for the cable-and-curl path.)

        No storage is touched on either method: `status()` reads a partition
        label and some constants, and a request only QUEUES. That matters on
        the T-Deck, where this runs at the frame tail with the panel feeder
        possibly still shipping bands -- the one place an sdspi transaction is
        the documented panic.
        """
        refused = self.gate(target)
        if refused is not None:
            return refused
        if self.update is None:
            return http_response(503, NO_UPDATER)
        if body is None:
            doc = self.update.status()
            if doc is None:
                return http_response(503, NO_UPDATER)
            return http_response(200, _json.dumps(doc))
        try:
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            sent = _json.loads(body or "{}")
            # DEFAULTS TO LOOKING, NEVER TO INSTALLING. A body that named no
            # action is a caller that did not say, and the harmless reading is
            # the only honest one to pick for a verb that replaces the board.
            action = sent.get("action") or "check"
            # The caller's CHANNEL choice rides the request. On a headless
            # board the browser is the ONLY place that choice can be made --
            # it has no Settings of its own -- so the field is part of the
            # verb, not a setting anybody could have written here first.
            channel = sent.get("channel") or None
        except (ValueError, AttributeError):
            return http_response(400, '{"error":"bad json"}')
        doc = self.update.status()
        if doc is None:
            return http_response(503, NO_UPDATER)
        ok, msg = self.update.request(action, channel)
        # ANSWERED IMMEDIATELY, with the state the request left behind. The
        # work happens in step(): holding the socket across a 2MB download (or
        # across a hand-off that closes this very socket) makes every outcome
        # look like a browser timeout, which on a board with no screen is the
        # same as no report at all.
        doc = self.update.status() or doc
        doc["ok"] = ok
        doc["message"] = msg
        return http_response(200 if ok else 409, _json.dumps(doc))

    def _pack(self, root):
        """One sync root, STREAMED as JSON -- the pull half for any registered
        `root` (moy_sync.SYNC_ROOTS), never assembled into a dict.

        Measured on P4 glass before this was a generator: 46 carts / 225 files
        packed in 21.8s and `json.dumps`ed in 39.3s into a 982KB string, so the
        request timed out at 60s having sent nothing, with the frame loop
        blocked throughout. The dict and the dump are both gone; what is left is
        the unavoidable part, reading the files.

        A KINDED root (files) is walked through its kind allowlist rather than a
        skip list, so `.history/` (each side's own undo sidecars) and `trash/`
        (a LOCAL recovery bin -- shipping it hands a peer a deletion it cannot
        undo) stay home with no second rule to keep in step. Its two "nothing
        here" cases answer DIFFERENTLY on purpose, because the page reads this as
        a capability probe: an EMPTY files layer -> `{}` 200 (the board speaks
        files; a 404 would disable the push half and strand the kid's first
        drawing), a host with NO files layer at all (the headless XIAO cart
        store: moy_webhost + moy_sync, no moy_carts) -> 404, the same answer a
        board flashed before files sync gives, so the page never builds a files
        watcher against a wall.

        The SD gate is entered ONCE, here, and the walk then runs outside it --
        correct only because of what `with_sd_live` is: it mounts the card once
        and KEEPS IT RESIDENT for the session (moybyte_sd; tearing it down per op
        corrupts the shared bus and hangs the next panel flush). So the gate's
        job is "make sure the card is up", and a generator that yields for a
        minute must hold nothing.
        """
        self._with_sd(lambda: None)          # ensure the card is mounted
        path = root.path(self.carts_root)
        if path is None:
            return None                      # no such layer -> 404 from transport
        tops = moy_sync.file_kinds() if root.kinds else None
        return ChunkedResponse(stream_store_json(path, tops=tops))

    def _root_for(self, root_id):
        """The filesystem path one batch's `root` names, or None (unservable)."""
        root = moy_sync.root_by_id(root_id)
        return None if root is None else root.path(self.carts_root)


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

    The #197 wiring -- the pairing pin and PLAY ON DEVICE -- is here for that
    same reason and NOT in three board files. An explicit `pin=` still wins (a
    test, a host with its own policy); otherwise the pin is read off the live
    `ws` at START, never now: a board builds this before system.json is loaded,
    and a pin captured at construction would be minted against an empty store.

    And the /update backend for the same reason a third time. `ConsoleUpdate`
    is UNCONDITIONAL here -- it asks `ws` whether this build can take an OTA at
    every request rather than being gated at construction, so a board never
    ends up with the routes and no answer, or an answer and no routes. This is
    the injection that was written once instead of three times, which is the
    whole lesson of `ensure_online`'s docstring above.
    """
    return WebHost(carts_root, web_dir, port=port, with_sd=with_sd,
                   ensure_online=lambda: ensure_online(
                       getattr(ws, "wifi", None), autoconnect),
                   pin=pin,
                   pin_source=None if pin else lambda: ws.web_pin(),
                   on_sync=lambda: ws.rescan_carts(),
                   on_run=lambda name: ws.launch_named(name),
                   update=ConsoleUpdate(ws))
