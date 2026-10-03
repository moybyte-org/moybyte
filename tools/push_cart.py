#!/usr/bin/env python3
# Map (grep -n a name to jump there):
#   serial_cfg       a board's [serial] declaration
#   StoreFull        the board's store has no room for the cart
#   check_room       refuse a cart the store cannot hold
#   Link             what a session knows about the board's recv
#   RateRefused      the board did not answer at the payload rate
#   push_file_raw    one file over the raw receive
#   compiled_module  a compiled cart's module for the board's chip
#   push_files       push paths inside the cart
#   cart_files       every file in the cart folder
#   main             the command line
#   connect          an identity-checked P4Board on a board's port
"""Copy a cart folder onto a board's cart store, over the serial console.

    python tools/push_cart.py ports/celeste.moy --board p4
    python tools/push_cart.py ports/celeste.moy --board tdeck
    python tools/push_cart.py ports/celeste.moy --board guition_s3 --port /dev/ttyACM1
    python tools/push_cart.py ports/celeste.moy --board p4 --only main.lua --force

--board is REQUIRED and deliberately has no default: the boards differ in line
state, reset policy and upload window, so a default is a silent wrong transport
on every board but one.

WHY THIS EXISTS. A board's cartridges live on its store -- the P4's internal VFS,
the S3 boards' SD -- seeded from the build for system carts and put there by hand
for anything else, which meant a hand-carried cart arrived by whatever route that
session improvised, with no record. One did: the P4 was carrying a celeste whose
`local P8_VH = 128` made its own `if view ~= nil and P8_VH < 128` guard never
fire, so it never declared view(128, 120) and played letterboxed at 1x. That is
the missing `--zoom` at port time, shipped to glass, and nobody could say how it
got there. A cart is data; putting data on the board should be a command, not an
improvisation.

Skips files whose hash already matches, so re-running is cheap and a partial
push is resumable.

THE BOARD DIFFERENCES ARE DATA, not branches here: each board.toml carries a
[serial] block with the line state at open, whether the board may be reset, the
`py`-line chunk, the raw upload window and, where the console is a UART, the
rate its payload crosses at (#202 Phase A's pattern, the same one
[flash]/[monitor] follow). Read those declarations before changing anything here
-- each field records a failure that cost an attempt.

THE STORE PATH IS DISCOVERED, NOT DECLARED: it comes from the live console's
`ws.carts_root`. The Guition's store is CONDITIONAL (a TF card when present,
else the internal VFS, #202), so a hardcoded path would be wrong on that board
half the time and a second source of truth on the others.

FIVE THINGS THIS GETS RIGHT, each of which cost an attempt:

  1. `P4Board.pyexec` stages ITS OWN snippet in `ws._up`, so every helper has to
     be defined BEFORE anything else goes there or the upload is silently wiped.
  2. `open(p, 'wb').write(d)` returns the byte count and leaves the file for the
     gc to finalise whenever. It reported 43658 bytes written and then read the
     file back EMPTY. Close it, and hash the FILE rather than the bytes that
     went into it -- which is what the board's `recv` does.
  3. Keep the expressions the device evaluates trivial. A list comprehension
     inside its eval env does not resolve names the way it does locally.
  4. Verify the hash of a `.new` and rename only then. A half-written main.lua
     is a cart that will not load, and the board is not where you want to
     discover that.
  5. The already-current hash reads 8KB at a time (HELPERS' `_sha`). A multi-MB
     file read in one `.read()` costs well over the device's own 30s `py`
     timeout, whose retry then queues a SECOND read behind the first and leaves
     the board still busy with both when `recv` arrives to arm the upload --
     which surfaces as "the board did not arm the raw upload (no reply)", nowhere
     near the hash that actually stalled it.

ONE TRANSPORT, AND NO FALLBACK: the dev channel's `recv`
(runtime/dev_channel.py's header and `_recv` are the authority). Carrying the
payload as base64 in `py` lines instead moves about 2KB/s -- fifty to sixty
seconds per cart over a cable that does hundreds of KB/s -- and keeping it
alongside `recv` would mean two upload protocols, one of them exercised only by
boards nobody had flashed. So a board whose firmware predates `recv` does not
get a slower push; it gets one line saying to flash it. What survives on the
`py` channel is the small stuff: the already-current hash, the mkdir, the
store's room and the rename.

A COMPILED CART GETS ONLY ITS OWN BOARD'S MODULE HERE. A cart may carry any
number of compiled modules (SPEC.md 16: one per chip and compiled-code format
version, `main.<chip>.f<format>.aot` beside `main.wasm`,
native/moy_wasm/README.md, "A cart survives its firmware") so it stays
portable off a console, but this push copies `main.wasm` plus ONLY the one
this board's chip and this tree's format would take -- every OTHER module
already sitting in the cart folder is left there, unpushed, exactly as a
`.gitignore`d build artifact would be. A cart made with moy-spec's `moy build`
carries none at all. So when the cart has no module for this board's chip --
the chip is its board.toml's `[board] chip` -- or has one built for another
main.wasm, this compiles one with tools/wasm_module.py, UNSIGNED, and pushes
it in its place; the cart folder itself is left as it was. An unsigned module
runs at full speed only while the console's Settings -> Unknown sources is
on; with it off the module is ignored and the cart plays on the interpreter
instead, so when the module going over is unsigned the tool says so plainly
before the push either way.

ON THE BOARD, THE FOLDER KEEPS ONLY THE MODULES THE PUSH CARRIES. A module is
keyed to the main.wasm it was compiled from, so one already in the folder --
from an earlier push, or the release Get Carts installed -- belongs to a
main.wasm this push may be replacing, and a console that loads it refuses it
or sizes the cart by it. A push that carries the cart's `main.wasm` or any of
its modules removes every other module of that main from the folder first
(`push_files`); a push that touches neither, such as `--only config.json`,
leaves them alone.

A STORE WITHOUT THE ROOM IS ONE LINE, NOT A TRACEBACK. Before the first window
the tool weighs what the push adds against the free bytes of the store it
lands on and refuses a cart that cannot fit, so nothing half-arrives; a store
that fills anyway (a card rounds every file up to its clusters) answers `RECV
ERR store full ...`, the board's banner says CAN'T ADD CART, and the tool
says the same thing in one line. Every other failure is one line as well.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_config                                              # noqa: E402
from p4_autotest import P4Board                                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from runtime.cart_index import module_parts                      # noqa: E402
from runtime.dev_channel import (RECV_DEAD_WINDOWS, RECV_IDLE_MS,  # noqa: E402
                                 RECV_SYNC, RECV_SYNC_MS)


def _boards(root=ROOT):
    """Short name -> the board directory holding its board.toml, DISCOVERED by
    globbing `firmware/*/board.toml`.

    A hand-kept dict here would be the FOURTH list of the boards -- beside the
    Makefile's flash/monitor targets, the CI matrix and tools/fetch_ci_firmware
    -- and the one most likely to rot, because nothing fails when a board is
    missing from it: the board simply cannot be pushed to and no test notices.
    It had already drifted in SPELLING (this map said `guition`, everything
    else says `guition_s3`).

    The short name is the board file's own `[board] ota` id -- the name that is
    already inside a signed OTA manifest, so it is a published identifier
    rather than a nickname invented here, and it is what fetch_ci_firmware and
    the CI matrix spell. A board file with no `ota` id is not a flashable board
    (`firmware/web_runner` is the browser build) and drops out by itself; one
    with an id but no [serial] block is a real board that has not declared its
    transport, and serial_cfg says exactly that rather than "unknown board"."""
    out = {}
    for path in sorted(glob.glob(os.path.join(root, "firmware", "*", "board.toml"))):
        d = os.path.dirname(path)
        name = board_config.load(d).get("board", {}).get("ota")
        if name:
            out[name] = os.path.relpath(d, root)
    return out


BOARDS = _boards()


def serial_cfg(board):
    """The board's [serial] declaration, or a clear failure.

    Deliberately NOT defaulted: a board whose line state we have not established
    is one where a wrong guess either chip-resets it mid-write (the S3 parts) or
    silently truncates the upload (the P4's unflow-controlled UART). Both cost an
    attempt to find; neither announces itself."""
    d = BOARDS.get(board)
    if d is None:
        sys.exit("unknown board %r -- one of: %s"
                 % (board, ", ".join(sorted(BOARDS))))
    cfg = board_config.load(os.path.join(ROOT, d))
    ser = cfg.get("serial")
    if not ser:
        sys.exit("%s/board.toml has no [serial] section" % d)
    return ser

# The only device-side helpers left: the already-current check, the mkdir, the
# store's room and the rename -- each one round trip, and each inside the
# console's storage gate (`ws._with_sd`), because on the T-Deck the card shares
# the panel's SPI host and a store op that overlaps a panel transfer hangs the
# board. `_sha` reads the file back rather than trusting what was written,
# which is what the board does at the end of a `recv` too -- item 2 above --
# and answers None without reading when the size already differs. It reads in
# 8KB pieces, not whole (item 5). `_room` is the free bytes of the store a
# path is on (None where the board cannot say), `_sizes` each file's size
# there (0 when it is not there yet), `_ls` what a folder holds ([] when it is
# not there), and `_put` moves a verified `.new` over its file and drops the
# stamp beside it (see push_file_raw).
HELPERS = """
import hashlib, os
def _gated(fn):
    g = getattr(ws, '_with_sd', None)
    return fn() if g is None else g(fn)
def _size(p):
    try: return os.stat(p)[6]
    except Exception: return 0
def _sha(p, n=-1):
    def go():
        if n >= 0 and _size(p) != n: return None
        try: f = open(p, 'rb')
        except Exception: return None
        h = hashlib.sha256()
        try:
            while True:
                b = f.read(8192)
                if not b: break
                h.update(b)
        except Exception:
            f.close(); return None
        f.close()
        return h.digest().hex()[:12]
    return _gated(go)
def _rm(p):
    try: os.remove(p)
    except Exception: pass
    return 1
def _mkdirs(ps):
    def go():
        for p in ps:
            try: os.mkdir(p)
            except Exception: pass
        return 1
    return _gated(go)
def _room(p):
    def go():
        try:
            st = os.statvfs(p)
            return st[0] * st[3]
        except Exception: return None
    return _gated(go)
def _sizes(ps):
    return _gated(lambda: [_size(p) for p in ps])
def _ls(d):
    def go():
        try: return sorted(os.listdir(d))
        except Exception: return []
    return _gated(go)
def _put(tmp, dst):
    def go():
        _rm(dst)
        os.rename(tmp, dst)
        _rm(dst + '.bak')
        return 1
    return _gated(go)
def _drop(p):
    return _gated(lambda: _rm(p))
"""


class StoreFull(RuntimeError):
    """The board's store has no room for the cart."""


def _mb(n):
    return "%.1f MB" % (n / (1024.0 * 1024.0))


def check_room(b, local, names, dest):
    """Refuse, before a byte is sent, a cart the store cannot hold: what the
    push adds (each file's size less the size it replaces) against the free
    bytes of the store `dest` is on. A board that cannot say how much room it
    has is pushed to, and the board's own `store full` answer stops it."""
    root = dest.rstrip("/").rsplit("/", 1)[0] or "/"
    free = b.pyval("ws._g['_room'](%r)" % root, timeout=30)
    if not isinstance(free, int):
        return
    have = b.pyval("ws._g['_sizes'](%r)" % [dest + "/" + f for f in names],
                   timeout=30)
    if not isinstance(have, list) or len(have) != len(names):
        have = [0] * len(names)
    need = sum(os.path.getsize(local[f]) - h for f, h in zip(names, have))
    if need > free:
        raise StoreFull("the cart does not fit: it needs %s more and the store "
                        "at %s has %s free" % (_mb(need), root, _mb(free)))


# A board that advertises `recv` but declares no window in its [serial] block
# gets this one. It is nobody's declared window: the Waveshare P4's is 3072
# (three quarters of its UART's stdin ring) and the USB boards' is 16384. Every
# board in the tree that HAS a dev channel declares one, so this is only what
# an undeclared board would get: big enough to be worth a round trip, small
# enough not to ask a board that has said nothing to keep up with 16KB unaided.
RAW_WINDOW_FALLBACK = 4096
# How many windows a single file may have to re-send before the push gives up.
# The board asks for one when a window arrives short -- a byte lost on the way.
# A budget rather than a free-for-all: a cable that drops a byte every window is
# a broken cable, and should say so instead of crawling.
RAW_MAX_RETRIES = 24
# How long to wait for the probe's answer. Generous: it is spent ONCE per
# session, and the console answers a command at frame cadence -- a board with a
# cart running and the diag lines streaming is not a fast responder.
RAW_PROBE_S = 6.0
# The board acks a window before writing it, so the next window's write can
# wait out the store's write of the last one -- on a USB board the endpoint
# stalls until the board reads again. The driver's own write timeout is sized
# for a line, not for that.
RAW_WRITE_S = 30.0
# `recv rate=`: how long to wait for the board's `RECV sync` before giving the
# rate up. Longer than the board's own wait (RECV_SYNC_MS), so by the time
# this side goes back to the console rate the board is already there.
RATE_SYNC_S = RECV_SYNC_MS / 1000.0 + 1.5
# ...and after giving it up, how long the board may still be listening at the
# payload rate: it heard the sync but its answer never arrived here, so it
# waits out a window that is not coming, RECV_DEAD_WINDOWS idle timeouts.
RATE_SETTLE_S = RECV_DEAD_WINDOWS * RECV_IDLE_MS / 1000.0 + 1.0


class Link:
    """What one session knows about the board's `recv`: the window, the
    console UART's own rate where the board can switch it (from its caps
    line), and the payload rate -- the board.toml's `recv_baud`, or None
    where there is none, the board cannot switch, or a switch has failed this
    session."""

    def __init__(self, window, console=None, rate=None):
        self.window = int(window)
        self.console = console
        self.rate = rate if (rate and console and rate != console) else None


def raw_link(b, ser, log=None):
    """The session's Link -- or a one-line exit naming the firmware.

    ONE probe per session, and the answer is POSITIVE either way: an image with
    the command prints `RECV caps max=<n>`, one without prints `REMOTE ? recv`
    from the same dispatcher, which is a definite no rather than a silence to
    interpret. There is no second transport to fall back to (see the header),
    so a no ends the run here, before a single byte of cart has been sent."""
    log = log or (lambda s: None)
    declared = int(ser.get("window") or RAW_WINDOW_FALLBACK)
    b._write_line("recv")
    seen = len(b.lines)
    end = time.time() + RAW_PROBE_S
    while time.time() < end:
        b._pump()
        while seen < len(b.lines):
            line = b.lines[seen]
            seen += 1
            if "REMOTE ? recv" in line or "RECV ERR" in line:
                # Two definite noes: an image without the command at all, and
                # one whose build cannot turn the interrupt char off (a byte
                # equal to it never reaches stdin, so there is no 8-bit route).
                sys.exit("this board's firmware has no `recv` and is too old "
                         "for push_cart -- it answered %r. Flash or OTA a "
                         "current image; there is no slower push to fall back "
                         "to." % line.strip())
            if "RECV caps" in line:
                window, console = declared, None
                for tok in line.split():
                    if tok.startswith("max="):
                        # The BOARD's ceiling wins over the declaration: it is
                        # the side that allocates the buffer.
                        window = min(declared, int(tok[4:]))
                    elif tok.startswith("rate="):
                        console = int(tok[5:])
                return Link(window, console, ser.get("recv_baud"))
    sys.exit("no answer to the `recv` probe in %gs -- the console is running "
             "(it answered up to here), so its dev channel is from before the "
             "raw upload landed, or it is wedged. Flash a current image."
             % RAW_PROBE_S)


def _recv_reply(b, seen, timeout=60.0):
    """The next RECV line past `seen`, as (words, new cursor).

    A CURSOR, not `P4Board.wait_line`: that one starts looking at whatever the
    transcript length is when it is called, so two board lines that arrive in
    one read -- the last window's `ack` and the `done` right behind it -- leave
    the second one already behind the mark, and the caller waits out its
    timeout for a line it has already been sent."""
    end = time.time() + timeout
    while True:
        while seen < len(b.lines):
            line = b.lines[seen]
            seen += 1
            if "RECV " in line:
                return line.split("RECV ", 1)[1].split(), seen
        if time.time() > end:
            return None, seen
        b._pump()


class RateRefused(RuntimeError):
    """The board did not answer at the payload rate."""


def push_file_raw(b, src, dst, link, verbose=False):
    """One file over the dev channel's raw receive. True if it was written.

    The host writes one window and then WAITS for the ack, which is what keeps
    the Waveshare P4's flow-control-free UART safe (its board.toml carries the
    why). A window that comes back short does not end the push: the board
    throws it away, names the boundary its file is still on, and this re-sends
    from there -- see RECV_RETRIES in runtime/dev_channel.py for why one
    dropped byte used to cost a whole cart. Only a board out of retries, or
    one that has gone quiet entirely, raises -- by file name, with the board's
    words.

    A file bigger than one window goes at `link.rate` where there is one; a
    board that does not answer at that rate costs this file a wait and the
    rest of the session the rate."""
    name = os.path.basename(src)
    raw = open(src, "rb").read()
    want = hashlib.sha256(raw).hexdigest()[:12]
    if b.pyval("ws._g['_sha'](%r, %d)" % (dst, len(raw))) == want:
        print("  = %-16s %d B (already current)" % (name, len(raw)))
        return False
    tmp = dst + ".new"
    t0 = time.time()
    fast = link.rate if len(raw) > link.window else None
    try:
        got = _upload(b, raw, dst, link, fast, name, verbose)
    except RateRefused as exc:
        link.rate = None
        print("  ! %s: no answer at %d baud, so this push goes on at %d"
              % (name, exc.args[0], link.console))
        b.drain(RATE_SETTLE_S)
        b._write_line("")
        got = _upload(b, raw, dst, link, None, name, verbose)
    if got != want:
        b.pyval("ws._g['_drop'](%r)" % tmp)
        raise RuntimeError("%s: hash %s != %s -- left the old file in place"
                           % (name, got, want))
    # moy_fs's invariant (#154): a file the store published carries a stamped
    # `.bak` describing it, and a writer that puts different bytes at the path
    # has to drop that stamp -- or the board's next read "recovers" the kid's
    # own last save over what was just pushed. `_put` does the rename and the
    # drop in one round trip.
    b.pyval("ws._g['_put'](%r, %r)" % (tmp, dst))
    print("  > %-16s %d B in %.1fs  sha %s%s"
          % (name, len(raw), time.time() - t0, want,
             "  (%d baud)" % fast if fast and link.rate else ""))
    return True


def _upload(b, raw, dst, link, fast, name, verbose):
    """`recv` one file, at `fast` baud when given; the board's sha12."""
    window, console = link.window, link.console
    seen = len(b.lines)
    # NO RESEND anywhere on this path (`cmd`'s retry exists for a lost REPLY):
    # a second `recv` line would arrive after the board armed -- as payload,
    # not as a command -- and every byte after it would be off by that much.
    b._write_line("recv %d %d %s%s" % (len(raw), window,
                                       "rate=%d " % fast if fast else "", dst))
    r, seen = _recv_reply(b, seen, timeout=30.0)
    if r and r[0] == "ERR" and r[1:3] == ["store", "full"]:
        raise StoreFull("%s did not fit: the board's store is full (%s)"
                        % (name, " ".join(r[1:])))
    if not r or r[0] != "ready":
        raise RuntimeError("%s: the board did not arm the raw upload (%s)"
                           % (name, " ".join(r or ["no reply"])))
    at = None
    was = b.ser.write_timeout
    try:
        if fast and "rate=%d" % fast in r:
            # The board switched the moment `ready` had left it. Whatever this
            # switch puts on the line ends at the sync, which is how the board
            # knows the payload starts after it.
            b.ser.baudrate = fast
            at = fast
            b.ser.write(RECV_SYNC)
            b.ser.flush()
            r, seen = _recv_reply(b, seen, timeout=RATE_SYNC_S)
            if not r or r[0] != "sync":
                b.ser.baudrate = console
                at = None
                raise RateRefused(fast)
        b.ser.write_timeout = RAW_WRITE_S
        sent = 0
        n = (len(raw) + window - 1) // window
        resent = 0
        while sent < len(raw):
            blk = raw[sent:sent + window]
            b.ser.write(blk)
            b.ser.flush()
            sent += len(blk)
            r, seen = _recv_reply(b, seen)
            if r is None:
                raise RuntimeError(
                    "%s: no ack for the window ending at %d/%d B -- the board "
                    "went quiet mid-upload" % (name, sent, len(raw)))
            if r[0] == "retry":
                # That window arrived short -- a byte lost on the way. The
                # board wrote nothing of it, so it names the boundary it is
                # still standing on and this sends the window again from
                # there. Believe the BOARD's offset rather than our own: it is
                # the one that knows what reached the file, and a disagreement
                # would corrupt the rest of the push.
                try:
                    sent = int(r[1])
                except (IndexError, ValueError):
                    raise RuntimeError("%s: the board asked for a re-send but "
                                       "named no offset (%s)"
                                       % (name, " ".join(r)))
                resent += 1
                if resent > RAW_MAX_RETRIES:
                    raise RuntimeError(
                        "%s: %d windows re-sent and still dropping at %d/%d B "
                        "-- that is a cable, not a hiccup"
                        % (name, resent, sent, len(raw)))
                if verbose:
                    print("     re-sending the window at %d" % sent)
                continue
            if r[0] == "ERR":
                if r[1:3] == ["store", "full"]:
                    raise StoreFull("%s did not fit: the board's store is full "
                                    "(%s)" % (name, " ".join(r[1:])))
                raise RuntimeError("%s: the board stopped the upload: %s"
                                   % (name, " ".join(r[1:])))
            if r[0] != "ack" or r[1:2] != [str(sent)]:
                raise RuntimeError(
                    "%s: window %d/%d acked %s, expected %d -- bytes were lost "
                    "on the wire" % (name, (sent + window - 1) // window, n,
                                     " ".join(r[1:]), sent))
            if at and sent == len(raw):
                # The board went back to the console rate right behind that
                # ack; its `done` comes after this sync, at that rate.
                b.ser.baudrate = console
                at = None
                b.ser.write(RECV_SYNC)
                b.ser.flush()
            if verbose:
                print("     window %d/%d" % ((sent + window - 1) // window, n))
        r, seen = _recv_reply(b, seen)
        if r is None or r[0] != "done":
            raise RuntimeError("%s: the board never reported what it wrote (%s)"
                               % (name, " ".join(r or ["no reply"])))
        return r[1]
    finally:
        b.ser.write_timeout = was
        if at:
            # Out of the payload on an error: the board says ERR at the payload
            # rate and goes back, and this newline ends whatever the switch
            # left in its line reader.
            b.ser.baudrate = console
            b._write_line("")


def board_chip(board):
    """The chip a board's compiled-cart modules are built for, from its
    board.toml; None for a board that declares none."""
    cfg = board_config.load(os.path.join(ROOT, BOARDS[board]))
    return cfg.get("board", {}).get("chip")


def compiled_module(cart, chip, work, log=print):
    """For a compiled cart, (its module's file name for `chip`, the local path
    of the module to push under it, whether that module is signed); None for a
    cart that is not compiled. The cart's own module when it carries the key
    this board wants for its main.wasm; otherwise one compiled now, unsigned,
    into `work`."""
    try:
        with open(os.path.join(cart, "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError):
        return None             # not a cart this can read; pushed as it is
    if not isinstance(man, dict) or man.get("runtime") != "wasm":
        return None
    sys.path.insert(0, ROOT)
    from tools import wasm_cart, wasm_module
    main = man.get("main") or "main.wasm"
    try:
        with open(os.path.join(cart, main), "rb") as f:
            wasm = f.read()
    except OSError:
        sys.exit("%s is a compiled cart with no %s: build it first (moy build %s)"
                 % (cart, main, cart))
    name = wasm_cart.aot_name(main, chip)
    have = os.path.join(cart, name)
    if os.path.isfile(have):
        with open(have, "rb") as f:
            data = f.read()
        if wasm_module.key_matches(data, wasm, chip):
            return name, have, bool(wasm_module.split(data)[1])
        log("%s was built for another main.wasm or another runtime; compiling "
            "a fresh one for %s, unsigned" % (name, chip))
    else:
        log("%s has no module for this board's chip (%s); compiling one, unsigned"
            % (os.path.basename(cart), chip))
    out = os.path.join(work, name)
    try:
        wasm_module.build(wasm, chip, out, signed=False)
    except wasm_module.ToolError as exc:
        sys.exit("could not compile the module for %s: %s" % (chip, exc))
    return name, out, False


def other_compiled_modules(names, keep):
    """This cart's OTHER compiled modules among `names` -- every `.aot` file
    besides `keep`, the one a push selected for this board. A cart may carry
    any number of them (one per chip and compiled-code format version, "A
    cart survives its firmware"), and a push leaves every one it does not
    need exactly where it was, unpushed -- portable, but not this board's
    business."""
    return [n for n in names if n.endswith(".aot") and n != keep]


def compiled_main(local):
    """The `main` of the compiled cart whose files `local` maps (a path
    inside the cart -> the host file pushed under it), or None when they are
    not a compiled cart's."""
    path = local.get("manifest.json")
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(man, dict) or man.get("runtime") != "wasm":
        return None
    return man.get("main") or "main.wasm"


def stale_modules(main, names, there):
    """The modules of `main` among `there` (what the cart's folder on the
    board holds) that a push of `names` does not carry, when the push carries
    the cart's compiled side -- `main` or one of its modules; [] when it
    carries neither. Every module is keyed to the main.wasm it was compiled
    from, so one the push leaves behind belongs to some other main.wasm."""
    stem = main[:-5] if main.endswith(".wasm") else main

    def ours(name):
        parts = module_parts(name)
        return parts is not None and parts[0] == stem

    if main not in names and not any(ours(n) for n in names):
        return []
    return [n for n in there if ours(n) and n not in names]


def push_files(b, local, names, dest, link, force=False, verbose=False):
    """Push `names` -- paths inside the cart, each from the host file
    `local[name]` -- into `dest` on the board over `recv`, with the helpers
    installed. Returns how many were written. The folders are made first,
    then every module `stale_modules` names is removed, so the folder never
    holds a module for a main.wasm it no longer has; `force` sends files
    whose hash already matches."""
    b.pyval("ws._g['_mkdirs'](%r)"
            % ([dest] + [dest + "/" + sub for sub in sub_dirs(names)]))
    main = compiled_main(local)
    if main is not None:
        there = b.pyval("ws._g['_ls'](%r)" % dest, timeout=30, strict=True)
        for name in stale_modules(main, names, there):
            b.pyval("ws._g['_drop'](%r)" % (dest + "/" + name), timeout=30,
                    strict=True)
            print("  - %-16s removed (a module this push does not carry)" % name)
    wrote = 0
    for f in names:
        if force:
            b.pyval("ws._g['_drop'](%r)" % (dest + "/" + f))
        wrote += push_file_raw(b, local[f], dest + "/" + f, link,
                               verbose=verbose)
    return wrote


def cart_files(cart):
    """Every file in the cart folder, RELATIVE to it, forward-slashed.

    A cart is a TREE, not a flat list: `scenes/`, `images/` and `tables/` are
    as much the cart as main.py is, and a listdir walk left every one of them
    on the host -- the cart arrived on the board without the assets it needs,
    and `--only scenes/x.moyscene` could not name one. Forward slashes because
    these become device paths, sorted so the transcript is stable."""
    out = []
    for dirpath, dirnames, filenames in os.walk(cart):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel = os.path.relpath(dirpath, cart)
        for f in filenames:
            if f.startswith("."):
                continue
            out.append(f if rel == "." else
                       rel.replace(os.sep, "/") + "/" + f)
    return sorted(out)


def sub_dirs(names):
    """The folders those paths need, SHALLOWEST FIRST -- `_mkdirs` makes them
    in order with one os.mkdir each, which does not make parents."""
    out = set()
    for n in names:
        parts = n.split("/")[:-1]
        for i in range(len(parts)):
            out.add("/".join(parts[:i + 1]))
    return sorted(out, key=lambda p: (p.count("/"), p))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cart", help="the cart folder (e.g. ports/celeste.moy)")
    ap.add_argument("--board", required=True, choices=sorted(BOARDS),
                    help="which board's [serial] declaration to use (required: "
                         "a default here is a silent wrong transport)")
    ap.add_argument("--port", default="auto",
                    help="serial port, or 'auto' (default): resolve it from "
                         "the board's [serial] usb id + its own identity "
                         "answer -- ttyACM numbers shuffle across replugs")
    ap.add_argument("--dest",
                    help="target path (default <ws.carts_root>/<foldername>)")
    ap.add_argument("--only", action="append",
                    help="push just this file, as its path inside the cart "
                         "(scenes/x.moyscene); repeatable")
    ap.add_argument("--force", action="store_true",
                    help="push even when the hash already matches")
    ap.add_argument("--no-rescan", action="store_true",
                    help="leave the launcher's shelf as it is (it lists what "
                         "the store held when it last scanned)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    cart = a.cart.rstrip("/")
    if not os.path.isdir(cart):
        sys.exit("not a cart folder: " + cart)
    names = cart_files(cart)
    local = dict((f, os.path.join(cart, f)) for f in names)
    work = tempfile.mkdtemp(prefix="push_cart-")
    try:
        return _push(a, cart, names, local, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def connect(board, port, verbose=False):
    """A live, identity-checked P4Board on `board`'s `port`: attached and
    verified (never pulsed) for an `attach_only` board, reset only when a
    silent one does not already answer -- resetting unconditionally cost the
    P4 a 60s boot on every push. Exits (one line) on a wrong or unresponsive
    board; the caller owns `.close()`.

    Shared by `_push` and `tools/refresh_wasm.py`, which both need a
    connected board and nothing else from a push's setup."""
    ser = serial_cfg(board)
    board_dir = os.path.join(ROOT, BOARDS[board])
    b = P4Board(port, log=(print if verbose else (lambda s: None)),
                board_dir=board_dir)
    # The chunk a `py` line may carry -- only the helper install's, since the
    # payload does not ride `py` at all. Still per board: the P4's UART
    # drops an over-long line as noise with no error (see its board.toml).
    b.CHUNK = int(ser.get("chunk") or P4Board.CHUNK)
    if ser.get("attach_only"):
        # ATTACH: never pulse the line. P4Board.reset() is CH343-specific and
        # on a USB-Serial/JTAG board it re-enumerates the device under our own
        # open handle, after which every read returns nothing, forever.
        if b.pyval("1+1", timeout=20) != 2:
            b.close()
            sys.exit("%s is not responding -- this board is attached to, not "
                     "reset, so its console must already be running" % port)
        # Liveness is not identity: the two S3s share a usb id and both
        # answer. A cart pushed to the wrong board's store is a silent
        # wrong outcome, so a POSITIVE mismatch refuses here.
        try:
            b.verify_board()
        except RuntimeError as exc:
            b.close()
            sys.exit(str(exc))
    else:
        # A running desk answers and names itself; a reset is for a silent
        # board only (its boot banner is the other way to learn who it is).
        if b.pyval("1+1", timeout=20) == 2:
            try:
                b.verify_board()
            except RuntimeError as exc:
                b.close()
                sys.exit(str(exc))
        else:
            b.reset()
    return b


def _push(a, cart, names, local, work):
    chip = board_chip(a.board)
    module = compiled_module(cart, chip, work) if chip else None
    if module is not None:
        name, path, _signed = module
        local[name] = path
        if name not in names:
            names = sorted(names + [name])
        skip = other_compiled_modules(names, name)
        if skip:
            names = [n for n in names if n not in skip]
            print("not pushing %s (another chip/format's module, unneeded here)"
                 % ", ".join(skip))
    if a.only:
        missing = [f for f in a.only if f not in names]
        if missing:
            sys.exit("not in the cart: " + ", ".join(missing))
        names = [f for f in names if f in a.only]
    ser = serial_cfg(a.board)
    b = connect(a.board, a.port, a.verbose)
    try:
        # The store the CONSOLE says it uses -- the Guition's is conditional on a
        # TF card being present, so asking beats declaring.
        dest = a.dest or (str(b.pyval("str(ws.carts_root)", timeout=20)).rstrip("/")
                          + "/" + os.path.basename(cart))
        # ONE probe per session, before the first file: `recv` is a property of
        # the IMAGE, not of the cart, and asking per file would spend a round
        # trip each time to learn the same thing.
        link = raw_link(b, ser, log=(print if a.verbose else None))
        print("%s -> %s  (%d file%s, %s, raw %d%s)"
              % (cart, dest, len(names), "" if len(names) == 1 else "s",
                 a.board, link.window,
                 ", %d baud" % link.rate if link.rate else ""))
        if not b.pyexec(HELPERS):
            sys.exit("could not install the upload helpers")
        if module is not None and not module[2] and module[0] in names:
            on = b.pyval("int(bool(getattr(ws, 'unknown_sources', False)))", timeout=20)
            if on == 0:
                print("NOTE: %s's module is unsigned, and this console has Unknown "
                      "sources off, so it will refuse to run the cart. Turn on "
                      "Settings -> Unknown sources on the console to play it."
                      % os.path.basename(cart))
        # A store without the room says so in one line, before or during the
        # push, and so does any other failure: this is a command a person
        # reads, not a traceback.
        try:
            check_room(b, local, names, dest)
            wrote = push_files(b, local, names, dest, link, force=a.force,
                               verbose=a.verbose)
        except StoreFull as exc:
            sys.exit("STORE FULL: %s. Nothing more was written; free some room "
                     "on the board and push again." % exc)
        except RuntimeError as exc:
            sys.exit(str(exc))
        print("%d file%s written, %d already current"
              % (wrote, "" if wrote == 1 else "s", len(names) - wrote))
        # The launcher lists what the store held when it last scanned, so a
        # new cart is not on the shelf -- and `run <title>` does not find it --
        # until the store is scanned again.
        if not a.no_rescan:
            n = b.pyval("len(ws.rescan_carts() or ())", timeout=60)
            print("store rescanned" if n is not None else
                  "the rescan did not answer -- reset the board to rescan")
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
