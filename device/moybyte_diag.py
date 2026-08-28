# Moybyte on-device diagnostics -- offline log capture for the LilyGO T-Deck.
#
# THE PROBLEM. Once run_desktop() enters its native-takeover frame loop, USB
# serial is starved: the loop never services TinyUSB, so there is no live REPL,
# no esptool reset, and -- crucially -- no log output. A cart that crashes a few
# minutes into play, or a per-frame timing we want to read, is invisible. Serial
# is only alive during the ~2s boot, before the loop starts.
#
# THE SOLUTION (persist-then-dump). During the session we keep a tiny RAM ring
# buffer of log lines (near-zero cost) and flush it to /sd/moybyte/diag.log
# infrequently (~every 5s, and on a cart crash). Each flush OVERWRITES the file
# with the whole current ring (truncate + write -- never appends), so the file
# always holds exactly ONE session: the most recent, bounded to the ring size.
# At the NEXT boot -- while serial is still alive -- we read that file (the
# PREVIOUS session's final ring) and print it to serial inside clear markers.
# There is no second file and no rotation: the new session's flushes simply
# overwrite the same file. So the owner workflow is: play carts -> reboot ->
# capture /dev/ttyACM* at boot -> read the "===== Moybyte diag dump =====" block.
#
# SAFETY CONTRACT (this module must NEVER brick the device). The SD card shares
# the display's SPI bus, so every SD touch here is wrapped so a failure degrades
# to a no-op -- it must never crash the desktop loop or the boot. The RAM ring is
# pure Python (host-testable); only the persistence/dump helpers touch hardware,
# and they are all guarded. This module is DEVICE-ONLY (it lives in the firmware
# modules/ tree, not in shared runtime/), so the host console never imports it.
#
# TIMESTAMPS. Each line is prefixed with a ticks_ms() timestamp + a short tag, so
# the dumped log reads as an ordered trace. We use time.ticks_ms when present and
# fall back to time.time()*1000 on the host (for the unit tests).

try:
    import time
except ImportError:  # pragma: no cover -- always present on host + device
    time = None


# --- on/off flag ------------------------------------------------------------
#
# Diagnostics default ON: the cost is tiny (a bounded RAM list + one SD write
# every ~5s between frames). To DISABLE, set ENABLED = False here and rebuild --
# every entry point below becomes a guarded no-op, so nothing else has to change.
ENABLED = True

# Ring-buffer bound. We keep at most MAX_LINES log lines AND cap the total bytes
# (MAX_BYTES) so a chatty session can't grow RAM unbounded; the oldest lines are
# dropped first. ~150 lines / ~8KB is plenty to capture a boot + a crash trace +
# a run of PERF samples.
MAX_LINES = 150
MAX_BYTES = 8192

# SD log file. There is exactly ONE file: each flush overwrites it with the whole
# current ring, so it always holds just the most-recent session (no append, no
# rotation, no second file). At boot we read it (the prior session's final ring)
# and dump it to serial; the new session's flushes then overwrite it.
SD_DIR = "/sd/moybyte"
LOG_PATH = "/sd/moybyte/diag.log"

# Hard cap on the on-SD file: even though the RAM ring is bounded, guard the write
# so a corrupt/huge buffer can never blow up the card or the dump print.
MAX_FILE_BYTES = 32768

DUMP_HEADER = "===== Moybyte diag dump (previous session) ====="
DUMP_FOOTER = "===== end diag dump ====="


def _ticks_ms():
    if time is None:
        return 0
    try:
        return time.ticks_ms()
    except AttributeError:
        try:
            return int(time.time() * 1000)
        except Exception:
            return 0


class _Ring(object):
    """A bounded ring buffer of formatted log lines.

    Bounds on BOTH count (max_lines) and total bytes (max_bytes); appending past
    either drops the oldest lines first. Pure Python with no hardware deps, so the
    host unit tests exercise it directly."""

    def __init__(self, max_lines=MAX_LINES, max_bytes=MAX_BYTES):
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self._lines = []
        self._bytes = 0

    def append(self, line):
        # Defensive: only ever store strings, never let a weird value raise here.
        try:
            line = str(line)
        except Exception:
            line = "<unprintable>"
        self._lines.append(line)
        self._bytes += len(line) + 1   # +1 for the newline the dump joins with
        self._trim()

    def _trim(self):
        # Drop oldest until BOTH bounds hold. Keep at least one line so a single
        # over-long line still records (truncated to the byte cap below).
        while len(self._lines) > self.max_lines and self._lines:
            dropped = self._lines.pop(0)
            self._bytes -= len(dropped) + 1
        while self._bytes > self.max_bytes and len(self._lines) > 1:
            dropped = self._lines.pop(0)
            self._bytes -= len(dropped) + 1

    def lines(self):
        return list(self._lines)

    def text(self):
        return "\n".join(self._lines)

    def clear(self):
        self._lines = []
        self._bytes = 0


def format_line(tag, msg, t=None):
    """Format one log line: "<ticks_ms> <tag> <msg>".

    Pure + deterministic when `t` is supplied, so the host tests assert the exact
    shape. Both tag and msg are coerced to str and stripped of newlines so a line
    is always exactly one line (newlines would corrupt the ring's byte accounting
    and the dump's line markers)."""
    if t is None:
        t = _ticks_ms()
    try:
        tag = str(tag)
    except Exception:
        tag = "?"
    try:
        msg = str(msg)
    except Exception:
        msg = "<unprintable>"
    line = "%d %s %s" % (t, tag, msg)
    # Collapse any embedded newlines so one entry stays one line.
    if "\n" in line:
        line = line.replace("\r", " ").replace("\n", " ")
    return line


# Module-level ring; created once, survives the whole session.
_ring = _Ring()


def reset(max_lines=MAX_LINES, max_bytes=MAX_BYTES):
    """Reinitialise the ring (used by the host tests to control its bounds)."""
    global _ring
    _ring = _Ring(max_lines, max_bytes)


# Echo every diag line to stdout live, in addition to buffering it. This is
# deliberate: it doubles as an empirical test of whether serial actually flows
# during the run_desktop loop. This build runs on USB-Serial-JTAG (hardware-
# serviced CDC) and the loop sleeps each frame, so a plain print() MAY reach the
# host live even mid-cart. If you read /dev/ttyACM* while a cart runs and the
# "PERF ..." lines stream live, loop-serial works and the SD persist + boot dump
# are belt-and-suspenders (crash/hang survival + on-device reading); if they do
# NOT stream, the persist-then-dump-at-boot path is the only way to see them, and
# it's vindicated. Set ECHO_LIVE = False to silence the live echo (the ring + SD
# persistence are unaffected). The boot dump prints regardless.
ECHO_LIVE = True


def log(tag, msg):
    """Append one line to the RAM ring (near-zero cost; NO SD touch) AND -- when
    ECHO_LIVE -- echo it to stdout live. The live echo is the empirical test of
    loop-serial (see ECHO_LIVE above); the ring is what survives to the boot dump.

    A guarded no-op when disabled or if formatting somehow fails -- logging must
    never be the thing that crashes the caller. The live echo is on its own try so
    a print failure never eats the persisted line, and vice versa."""
    if not ENABLED:
        return
    try:
        line = format_line(tag, msg)
    except Exception:
        return
    if ECHO_LIVE:
        try:
            print("Moybyte", line)
        except Exception:
            pass
    try:
        _ring.append(line)
    except Exception:
        pass


def logp(tag, msg):
    """Back-compat alias for log(): both persist to the ring AND echo live now, so
    logp is just log. Kept as a name because the call sites that route the existing
    print("Moybyte ...") diagnostics through diag use it to mean "printed AND
    persisted" -- which log() now always is."""
    log(tag, msg)


def lines():
    """Current ring contents (host tests + flush_to_sd)."""
    return _ring.lines()


def text():
    return _ring.text()


# --- SD persistence (device-only; all guarded) ------------------------------
#
# These touch the SD card, which shares the panel's SPI bus. The CALLER supplies
# the SD-session wrapper (moybyte_sd.with_sd_live on device) so the mount happens
# on the native single-bus path between frames; we never mount the card ourselves
# and never flush the panel inside an SD op. Every helper is wrapped so an SD
# failure degrades to a no-op.

def _ensure_dir():
    try:
        import os

        try:
            os.mkdir(SD_DIR)
        except OSError:
            pass   # already exists (or parent /sd not ready -> write will no-op)
    except Exception:
        pass


def _write_log_file(body):
    """Write `body` to LOG_PATH, capped at MAX_FILE_BYTES. Runs INSIDE an SD
    session (the caller has SD mounted); pure file I/O, no panel flush."""
    _ensure_dir()
    if len(body) > MAX_FILE_BYTES:
        # Keep the TAIL (most recent lines) when over the file cap.
        body = body[-MAX_FILE_BYTES:]
    f = open(LOG_PATH, "w")
    try:
        f.write(body)
    finally:
        f.close()


def flush_to_sd(with_sd):
    """Persist the current RAM ring to /sd/moybyte/diag.log via the supplied SD
    session wrapper (device: moybyte_sd.with_sd_live, which mounts the card on the
    native single-bus path and keeps it resident). Called on a ~5s timer from the
    desktop loop and from the cart frame-error handler, so a crash we can't see
    live is still captured.

    Fully guarded: any failure (no SD, write error, no wrapper) degrades to a
    no-op -- a diag flush must never crash the render loop. Returns True on a
    successful write, else False."""
    if not ENABLED:
        return False
    if with_sd is None:
        return False
    try:
        body = _ring.text()
    except Exception:
        return False
    try:
        return bool(with_sd(lambda: _do_flush(body)))
    except Exception:
        return False


def _do_flush(body):
    try:
        _write_log_file(body)
        return True
    except Exception:
        return False


# --- boot dump (device-only; runs while serial is alive) --------------------

def _read_prev_log_pre_display():
    """Read the previous session's diag.log using the PRE-DISPLAY SD path
    (machine.SDCard via moybyte_sd.with_sd). This is the SAFE read path when
    called BEFORE init_display(): the panel isn't up yet, so machine.SDCard
    mounting + re-running spi_bus_initialize() is fine here -- exactly the same
    pre-display window the boot cart prefetch uses. (Calling machine.SDCard AFTER
    the panel is live would hard-hang the shared bus; that's why this runs in
    main() before _init_display.)

    We do NOT rotate or truncate the file -- the new session's first flush (~5s
    into the loop) simply overwrites it with the new ring, so the file always
    holds exactly one (the most recent) session. Returns the file's text
    (possibly empty), or None on any failure / no previous log."""
    try:
        import moybyte_sd
    except Exception:
        return None

    def _read():
        try:
            f = open(LOG_PATH, "r")
            try:
                return f.read()
            finally:
                f.close()
        except OSError:
            return None   # no previous log -> nothing to dump

    try:
        return moybyte_sd.with_sd(_read)
    except Exception:
        return None


def dump_previous_to_serial():
    """At boot, before the takeover loop starts (serial alive), print the previous
    session's diag log to serial wrapped in clear markers.

    This is the offline-capture payoff: reboot, capture /dev/ttyACM* at boot, and
    the marked block holds the PERF samples + any crash trace from the last run.
    No rotation: the new session's periodic flushes overwrite the same file, so it
    always contains exactly one (the most recent) session.

    NOT called automatically anymore: the boot hook rode the #56 pre-display SD
    prefetch path, which shipped OFF (a pre-display machine.SDCard mount can
    break display init on a populated card) and has been removed. Call it from
    the REPL before the desktop starts -- that's the bus-safe window for the
    machine.SDCard read path (see _read_prev_log_pre_display). Fully guarded:
    never crashes the caller."""
    if not ENABLED:
        return
    try:
        body = _read_prev_log_pre_display()
    except Exception:
        body = None
    try:
        print(DUMP_HEADER)
        if body:
            # Print as-is; the file already holds one log line per line.
            print(body)
        else:
            print("(no previous diag log)")
        print(DUMP_FOOTER)
    except Exception:
        pass


# --- perf sampling helper ---------------------------------------------------

def format_perf(cart, fps, flush_ms, draw_ms, net):
    """Format a structured perf sample line:
        PERF cart=<name> fps=<n> net=<ticks/s|-> flush=<ms> draw=<ms>
    Numbers are rounded to ints. Pure (host-testable). The cart name is sanitised
    to a single token (spaces -> '_') so the line stays cleanly parseable.

    `net` is the #65 lockstep witness (ws.perf_net()): the shared tick rate a
    LINKED game's world advances at, which is also the rate it renders at -- the
    console gates every frame the tick is not due for, so a linked fps=30 on a
    board looping at 55 is a correct match and not a regression. **None prints
    `-`, never 0**: absence and a frozen meter must not look alike (the
    2026-08-22 doctrine), and 0 here is a real reading -- matched, not
    advancing. It sits right after fps= because it is what fps= means."""
    try:
        name = str(cart) if cart is not None else "?"
    except Exception:
        name = "?"
    name = name.replace(" ", "_").replace("\n", "_").replace("\r", "_")
    if not name:
        name = "?"
    return "PERF cart=%s fps=%d net=%s flush=%d draw=%d" % (
        name, _round_int(fps), "-" if net is None else _round_int(net),
        _round_int(flush_ms), _round_int(draw_ms))


def _round_int(v):
    try:
        return int(v + 0.5)
    except Exception:
        try:
            return int(v)
        except Exception:
            return 0


def log_perf(cart, fps, flush_ms, draw_ms, net):
    """Append a PERF sample to the ring (the offline-readable per-cart timing).

    `net` has no default on purpose: a caller that forgets it would print the
    absent marker while a match was quietly eating half the frames, which is
    the exact failure this field exists to end."""
    log("PERF", format_perf(cart, fps, flush_ms, draw_ms, net)[5:])
