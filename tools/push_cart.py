#!/usr/bin/env python3
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
`py`-line chunk and the raw upload window (#202 Phase A's pattern, the same one
[flash]/[monitor] follow). Read those declarations before changing anything here
-- each field records a failure that cost an attempt.

THE STORE PATH IS DISCOVERED, NOT DECLARED: it comes from the live console's
`ws.carts_root`. The Guition's store is CONDITIONAL (a TF card when present,
else the internal VFS, #202), so a hardcoded path would be wrong on that board
half the time and a second source of truth on the others.

FOUR THINGS THIS GETS RIGHT, each of which cost an attempt:

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

ONE TRANSPORT, AND NO FALLBACK: the dev channel's `recv`
(runtime/dev_channel.py's header and `_recv` are the authority). Carrying the
payload as base64 in `py` lines instead moves about 2KB/s -- fifty to sixty
seconds per cart over a cable that does hundreds of KB/s -- and keeping it
alongside `recv` would mean two upload protocols, one of them exercised only by
boards nobody had flashed. So a board whose firmware predates `recv` does not
get a slower push; it gets one line saying to flash it. What survives on the
`py` channel is the small stuff: the already-current hash, the mkdir, and the
rename.
"""
import argparse
import glob
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_config                                              # noqa: E402
from p4_autotest import P4Board                                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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

# The only device-side helpers left: the already-current check and the mkdir.
# `_sha` reads the file back rather than trusting what was written, which is the
# same thing the board does at the end of a `recv` -- and the reason both do is
# item 2 above.
HELPERS = """
import hashlib, os
def _sha(p):
    try: return hashlib.sha256(open(p, 'rb').read()).digest().hex()[:12]
    except Exception: return None
def _mkdir(p):
    try: os.mkdir(p)
    except Exception: pass
    return 1
ws._g['_sha'] = _sha; ws._g['_mkdir'] = _mkdir
"""


# A board that advertises `recv` but declares no window in its [serial] block
# gets the P4's -- the smallest, and the only one that is safe on a transport
# with no flow control. Not a guess about that board: a floor no board needs
# less than.
RAW_WINDOW_FALLBACK = 4096
# How long to wait for the probe's answer. Generous: it is spent ONCE per
# session, and the console answers a command at frame cadence -- a board with a
# cart running and the diag lines streaming is not a fast responder.
RAW_PROBE_S = 6.0


def quiet_diag(b):
    """Turn the diag stream OFF for the upload, and say whether it was on.

    NOT tidiness. The dev channel's diag lines and the raw payload share one
    UART with no flow control, so a diag line printed while a window is in
    flight lands in the middle of it: the board reads short, never acks, and
    its idle timeout reports "timeout after N of M bytes" -- which reads as a
    cable or a window-size problem and is neither. Seen on a P4 twice in a row
    (12270/17116, then 4083/17116); the same push went through first time with
    the stream off.

    `diag` does not persist (the console takes persist=False for exactly this
    reason), so this is a session-local change and restore_diag puts it back."""
    try:
        was = bool(b.pyval("bool(getattr(ws, 'diag_live', False))", timeout=20))
    except Exception:  # noqa: BLE001 -- an older console has no flag to read
        was = False
    if was:
        b.cmd("diag 0", wait_for="REMOTE diag")
        b.drain(0.3)                 # let anything already queued clear the wire
    return was


def restore_diag(b, was_on):
    """Put the diag stream back if this push turned it off. Best-effort: the
    upload is done by now, and a board that has gone quiet is not worth an
    error the user cannot act on."""
    if not was_on:
        return
    try:
        b.cmd("diag 1", wait_for="REMOTE diag")
    except Exception:  # noqa: BLE001
        pass


def raw_window(b, declared, log=None):
    """The window to blast in -- or a one-line exit naming the firmware.

    ONE probe per session, and the answer is POSITIVE either way: an image with
    the command prints `RECV caps max=<n>`, one without prints `REMOTE ? recv`
    from the same dispatcher, which is a definite no rather than a silence to
    interpret. There is no second transport to fall back to (see the header),
    so a no ends the run here, before a single byte of cart has been sent."""
    log = log or (lambda s: None)
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
                for tok in line.split():
                    if tok.startswith("max="):
                        # The BOARD's ceiling wins over the declaration: it is
                        # the side that allocates the buffer.
                        return min(int(declared), int(tok[4:]))
                return int(declared)
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


def push_file_raw(b, src, dst, window, verbose=False):
    """One file over the dev channel's raw receive. True if it was written.

    The host writes one window and then WAITS for the ack, which is what keeps
    the P4's flow-control-free UART safe (its board.toml carries the why). A
    window that comes back short never acks: the board's own idle timeout fires,
    it removes the tmp and says how far it got, and that error is what this
    raises -- by file name, with the board's words."""
    name = os.path.basename(src)
    raw = open(src, "rb").read()
    want = hashlib.sha256(raw).hexdigest()[:12]
    if b.pyval("ws._g['_sha'](%r)" % dst) == want:
        print("  = %-16s %d B (already current)" % (name, len(raw)))
        return False
    tmp = dst + ".new"
    t0 = time.time()
    seen = len(b.lines)
    # NO RESEND anywhere on this path (`cmd`'s retry exists for a lost REPLY):
    # a second `recv` line would arrive after the board armed -- as payload,
    # not as a command -- and every byte after it would be off by that much.
    b._write_line("recv %d %d %s" % (len(raw), window, dst))
    r, seen = _recv_reply(b, seen, timeout=30.0)
    if not r or r[0] != "ready":
        raise RuntimeError("%s: the board did not arm the raw upload (%s)"
                           % (name, " ".join(r or ["no reply"])))
    sent = 0
    n = (len(raw) + window - 1) // window
    while sent < len(raw):
        blk = raw[sent:sent + window]
        b.ser.write(blk)
        b.ser.flush()
        sent += len(blk)
        r, seen = _recv_reply(b, seen)
        if r is None:
            raise RuntimeError(
                "%s: no ack for the window ending at %d/%d B -- the board went "
                "quiet mid-upload" % (name, sent, len(raw)))
        if r[0] == "ERR":
            raise RuntimeError("%s: the board stopped the upload: %s"
                               % (name, " ".join(r[1:])))
        if r[0] != "ack" or r[1:2] != [str(sent)]:
            raise RuntimeError(
                "%s: window %d/%d acked %s, expected %d -- bytes were lost on "
                "the wire" % (name, (sent + window - 1) // window, n,
                              " ".join(r[1:]), sent))
        if verbose:
            print("     window %d/%d" % ((sent + window - 1) // window, n))
    r, seen = _recv_reply(b, seen)
    if r is None or r[0] != "done":
        raise RuntimeError("%s: the board never reported what it wrote (%s)"
                           % (name, " ".join(r or ["no reply"])))
    got = r[1]
    if got != want:
        b.pyval("__import__('os').remove(%r) or 1" % tmp)
        raise RuntimeError("%s: hash %s != %s -- left the old file in place"
                           % (name, got, want))
    b.pyval("__import__('os').remove(%r) or 1" % dst)     # no-op if absent
    b.pyval("__import__('os').rename(%r, %r) or 1" % (tmp, dst))
    print("  > %-16s %d B in %.0fs  sha %s"
          % (name, len(raw), time.time() - t0, want))
    return True


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
                    help="push just this file (repeatable)")
    ap.add_argument("--force", action="store_true",
                    help="push even when the hash already matches")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    cart = a.cart.rstrip("/")
    if not os.path.isdir(cart):
        sys.exit("not a cart folder: " + cart)
    names = sorted(f for f in os.listdir(cart)
                   if os.path.isfile(os.path.join(cart, f))
                   and not f.startswith("."))
    if a.only:
        missing = [f for f in a.only if f not in names]
        if missing:
            sys.exit("not in the cart: " + ", ".join(missing))
        names = [f for f in names if f in a.only]
    ser = serial_cfg(a.board)
    board_dir = os.path.join(ROOT, BOARDS[a.board])
    b = P4Board(a.port, log=(print if a.verbose else (lambda s: None)),
                board_dir=board_dir)
    # The chunk a `py` line may carry -- only the helper install's, since the
    # payload does not ride `py` at all. Still per board: the P4's UART
    # drops an over-long line as noise with no error (see its board.toml).
    b.CHUNK = int(ser.get("chunk") or P4Board.CHUNK)
    diag_was_on = False
    try:
        if ser.get("attach_only"):
            # ATTACH: never pulse the line. P4Board.reset() is CH343-specific and
            # on a USB-Serial/JTAG board it re-enumerates the device under our own
            # open handle, after which every read returns nothing, forever.
            if b.pyval("1+1", timeout=20) != 2:
                sys.exit("%s is not responding -- this board is attached to, not "
                         "reset, so its console must already be running" % a.port)
            # Liveness is not identity: the two S3s share a usb id and both
            # answer. A cart pushed to the wrong board's store is a silent
            # wrong outcome, so a POSITIVE mismatch refuses here.
            try:
                b.verify_board()
            except RuntimeError as exc:
                sys.exit(str(exc))
        else:
            # A running desk answers and names itself; a reset is for a silent
            # board only (its boot banner is the other way to learn who it is).
            # Resetting unconditionally cost the P4 a 60s boot on every push.
            if b.pyval("1+1", timeout=20) == 2:
                try:
                    b.verify_board()
                except RuntimeError as exc:
                    sys.exit(str(exc))
            else:
                b.reset()
        # The store the CONSOLE says it uses -- the Guition's is conditional on a
        # TF card being present, so asking beats declaring.
        dest = a.dest or (str(b.pyval("str(ws.carts_root)", timeout=20)).rstrip("/")
                          + "/" + os.path.basename(cart))
        # Before the probe, not just before the payload: a diag line can land
        # inside the probe's answer too.
        diag_was_on = quiet_diag(b)
        # ONE probe per session, before the first file: `recv` is a property of
        # the IMAGE, not of the cart, and asking per file would spend a round
        # trip each time to learn the same thing.
        win = raw_window(b, int(ser.get("window") or RAW_WINDOW_FALLBACK),
                         log=(print if a.verbose else None))
        print("%s -> %s  (%d file%s, %s, raw %d)"
              % (cart, dest, len(names), "" if len(names) == 1 else "s",
                 a.board, win))
        if not b.pyexec(HELPERS):
            sys.exit("could not install the upload helpers")
        b.pyval("ws._g['_mkdir'](%r)" % dest)
        wrote = 0
        for f in names:
            if a.force:
                b.pyval("__import__('os').remove(%r) or 1" % (dest + "/" + f))
            wrote += push_file_raw(b, os.path.join(cart, f), dest + "/" + f,
                                   win, verbose=a.verbose)
        print("%d file%s written, %d already current"
              % (wrote, "" if wrote == 1 else "s", len(names) - wrote))
        # The store is scanned at boot, so a pushed cart appears on the next one.
        print("reset the board (or `machine.reset()`) for the launcher to rescan")
    finally:
        # In the finally, so a push that FAILS leaves the board as it found it.
        restore_diag(b, diag_was_on)
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
