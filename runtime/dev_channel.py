# Map (grep -n a name to jump there):
#   verbs_line         the VERBS line: where a Lua frame's time goes
#   perfcnt_line       the PERFCNT line: instructions per cycle
#   luaprof_line       the LUAPROF line: the interpreter's split
#   heapcaps_line      the HEAPCAPS line: what each heap holds
#   DevChannel         the serial line commands: one class, every board
#   DevChannel.run     the command table, after the registered words
#   DevChannel.report  the per-tick diag line
"""The serial DEV CHANNEL: drive a running console over the board's serial line.

ONE implementation, every board that has a working stdin. Extracted from the
T-Deck mainline port on 2026-08-16, the day RX started working there -- and
UNIFIED 2026-08-17: until then this file had zero importers (the fork it was
extracted for was deleted the next day) while the T-Deck ran a verbatim copy
and the P4 ran an older inline loop with a diverged vocabulary. That triple is
exactly what this module's opening line warns against: three boards, three
vocabularies, is how `swipe` ends up meaning different things. Both boards
construct `DevChannel` now; the on-glass suites are the wire-compat pin.

WHY A CHANNEL AND NOT A REPL. The console owns the loop and never returns to
the REPL, so there is nothing to type at. The kernel's loop reads the serial
line between frames and runs whole lines as commands, which is also what
makes a board scriptable: `tools/p4_autotest.py` and tests/test_p4_on_glass.py
are built on exactly this shape. The reader takes bytes off the stdin ring
from C, never a readline, so line noise costs a bounded slice of a frame; an
over-long partial line is dropped, and so is a line that is not UTF-8; and it
COUNTS what it swallowed (`rx=` on the SERIAL line), so "something is
injecting into stdin" is a number rather than a mystery hang.

THE READER IS THE KERNEL'S (native/moy_kernel/moy_devch.c): it takes the
bytes, runs the kernel's own words (`power`, `bl`, `quit`) and hands every
other line to `run` here through the loop's word upcall. The gestures `tap`,
`swipe` and `drag` play from C too; the words below find what they aim at.

ONE COMMAND LEAVES THE LINE DISCIPLINE: `recv`. Everything above is a line, and
a line is the wrong shape for a cartridge -- a 124KB main.lua base64'd into
768-char `py` lines, each acked before the next and each read a byte per frame,
took 50-60s per cart on every board (~2KB/s) while the cable underneath carries
hundreds of KB/s. So `recv <nbytes> <window> <path>` hands the stdin stream to
`_recv` for exactly nbytes, straight into `<path>.new`, 8 bits wide with no
base64 -- and it is the ONLY way a cart reaches a board over serial, with no
fallback in `tools/push_cart.py`, so a change here is a change to the only
route. Read that method's own comments before changing it; the three facts that
shaped it are:

  * the payload is NOT read through `sys.stdin`. That stream is TEXT and
    `stdio_read` maps CR to LF as it goes -- every 0x0D would land as 0x0A.
    `sys.stdin.buffer` is the same ring with no translation.
  * a byte equal to the INTERRUPT CHAR never reaches the ring at all: both RX
    ISRs (esp32's `usb_serial_jtag.c` and `uart.c`) compare each arriving byte
    against `mp_interrupt_char` and swallow it into a scheduled
    KeyboardInterrupt -- and TinyUSB's CDC path additionally EMPTIES the ring
    when it hits one. So the transfer runs with `micropython.kbd_intr(-1)`,
    exactly as pyexec's raw-paste mode does, restored in a `finally`.
  * a bulk `read(n)` blocks inside `mp_hal_stdin_rx_chr` with no timeout, so
    a host that dies mid-window would park the frame loop forever. The boards
    read the ring from C instead (native/moy_serial: blocks as they land, and
    an idle timeout); where that module is absent every byte is preceded by a
    poll that already promised it, which is the same timeout at a byte a poll.

`moy push` REACHES THE CONSOLE HERE TOO: moy-spec's `moy push` finds a console
by writing `moy?` to every USB serial port and copies a cart line by line
(proposals/sideload.md in moy-spec, tier 1). The channel answers `moy?` with
the console's descriptor and takes `moy-put`, `moy-del`, `moy-rescan` and
`moy-run` into the store, the same way `recv` does -- a `.new` renamed only
when every byte arrived, the stamp beside it dropped. Base64 at a line a
frame is slower than `recv` and needs no tool but moy's, which is the point.
A compiled cart pushed that way carries no module for this board's chip --
only tools/push_cart.py builds one -- and `moy-rescan` says so in a
`moy-note` line the tool shows to the person pushing.
"""

try:                                    # device: ticks is frozen flat
    from ticks import _ticks_diff, _ticks_ms
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_diff, _ticks_ms
try:                                    # each subsystem's words
    import devch_input
    import devch_audio
    import devch_links
except ImportError:                     # host: the runtime package
    from runtime import devch_input, devch_audio, devch_links
try:                       # device (device_util is staged from device/)
    from device_util import _diag_log
except ImportError:        # host / test -- no diag ring; print is it

    def _diag_log(tag, msg, diag):
        print("Moybyte", tag, msg)

try:                       # device: the interrupt-char switch `recv` needs
    import micropython as _micropython
except ImportError:        # host CPython: nothing intercepts a payload byte
    _micropython = None
try:                       # device: the console's stdin read from C, and its rate
    import moy_serial as _moy_serial
except ImportError:        # host CPython, the unix port: `_fill`'s own loop
    _moy_serial = None
try:                       # a VM: the kernel's frame loop (native/moy_kernel)
    import moy_loop as _loop
except ImportError:        # host CPython: the host drives its own frames
    _loop = None
try:                       # device: the kernel's task watchdog, which a long
    from moy_kernel import feed as _kernel_feed   # upload outlasts unfed
except ImportError:        # host, and a board without the kernel
    _kernel_feed = None

# The ONE declaration of the persisted ON/OFF settings (#209 section 7). The
# serial words below are derived from it -- an entry with a `dev` name IS the
# command, gate and all, so a new toggle reaches the channel without a branch
# being written here.
try:
    from settings_layer import SETTINGS_TOGGLES
    from crash_guard import last_crash
except ImportError:        # host / test -- the runtime package
    from runtime.settings_layer import SETTINGS_TOGGLES
    from runtime.crash_guard import last_crash

# A partial line longer than this is noise; drop it. NOT sized for a human:
# the on-glass harness's `pyexec` uploads code in 768-char chunks wrapped in a
# `py ws._up.__setitem__(...)` line whose %r escaping can nearly double it --
# the first unification shipped this at 96 (the T-Deck's typed-command size)
# and every P4 pyexec line was silently dropped as noise, which presented as
# the RSA-verifier test hanging forever (2026-08-17).
SERIAL_LINE_MAX = 4096
# Bounded drain: noise cannot own the frame. 512 * read(1) is well under a
# millisecond on either board, and a harness chunk line arrives in ~4 frames
# instead of a human-scaled 13.
SERIAL_BYTES_PER_FRAME = 512
# Bytes that may arrive without EVER completing a command before the channel
# gives up on itself. A real operator types a line within a few dozen bytes and
# even a harness completes one inside a couple of chunk lines; sixteen
# kilobytes of newline-free traffic is a byte SOURCE, not a person. Rather
# than spend a slice of every frame chewing it forever, the channel disarms and
# says so once -- turning a permanent drag on the desktop into one serial line
# naming the condition. Re-arm by re-entering run_desktop.
SERIAL_NOISE_LIMIT = 16384
# `recv`: the largest window this board will accept, i.e. the most bytes a host
# may have in flight before it must wait for an ack. It is a CEILING on the
# host's declaration (each board.toml's `[serial] window` carries the value and
# its reason), not a value anyone should meet: the buffer is allocated for the
# window, and on a UART board it is also how much the ring has to absorb if the
# frame loop is preempted mid-window.
RECV_MAX_WINDOW = 32768
# No byte for this long inside a window and the window is given up on. A host
# that is alive but slow refreshes it with every byte -- inside a window bytes
# arrive microseconds apart -- so a quiet stretch this long means the stream
# STOPPED, which on a ring with no flow control means bytes were dropped.
# It is not a rate floor, and it is no longer fatal: see RECV_RETRIES.
RECV_IDLE_MS = 2000
# `recv rate=`: what the host sends after each switch of the UART's rate, and
# how long the board waits for it. Anything ahead of it on the line is what
# the switch left there and is dropped. The host waits longer than this before
# it gives the rate up, so the board is back at the console's own by then.
RECV_SYNC = b"\xa5\x5aRECV-SYNC\x5a\xa5"
RECV_SYNC_MS = 1000
# How many windows may be re-sent before the transfer is abandoned. A UART ring
# with no flow control drops a byte with no error when it overflows, and one
# dropped byte used to kill the whole cart: on the P4's stock 260-byte ring, a
# handful of bytes (2, 7, 12) lost about once every 300 windows, which was a
# failed 120KB push one time in five. The file only ever advances by WHOLE
# windows, so `got` is a resync point that costs nothing to keep -- the board
# throws the short window away and asks for it again. The final sha still has
# to agree, so a retry that resynced wrongly fails loudly rather than landing a
# corrupt cart.
RECV_RETRIES = 8
# Consecutive windows that arrive EMPTY before the board stops believing there
# is a host. A dropped byte leaves a window nearly full; nothing at all means
# the other end is gone, and two of those end it in ~4s -- about what the one
# fatal timeout above used to cost.
RECV_DEAD_WINDOWS = 2
# A `moy-put` in flight drains this much a frame instead of
# SERIAL_BYTES_PER_FRAME, on a stdin with no poll to wait on (see _moy_drain,
# which takes the whole file at once where there is one).
MOY_PUT_BYTES_PER_FRAME = 4096


def _kbd_intr(ch):
    """Set the interrupt char, where the board has one to set.

    A payload byte equal to it never reaches stdin (both esp32 RX ISRs swallow
    it, and the CDC path clears the ring as well), so `recv` turns it off for
    the transfer and back on afterwards. On host CPython there is no ISR in the
    path and nothing to do; on a build with MICROPY_KBD_EXCEPTION off there is
    no switch, and `recv` declines rather than transferring 255 of 256 byte
    values."""
    if _micropython is not None:
        _micropython.kbd_intr(ch)


def _console_rate():
    """The console UART's rate where this build can switch it, else None."""
    baud = getattr(_moy_serial, "baud", None)
    return baud() if baud is not None else None


# Can this build carry an arbitrary byte on stdin at all? A device that cannot
# turn the interrupt char off has no 8-bit-clean route, and says so rather than
# transferring 255 of 256 byte values and leaving the hash to find out.
RECV_8BIT = _micropython is None or hasattr(_micropython, "kbd_intr")


def _toggle_cmd(cmd):
    """The SETTINGS_TOGGLES entry this word drives, or None."""
    for t in SETTINGS_TOGGLES:
        if t[5] == cmd:
            return t
    return None


def verbs_line(hz, frames, rows, top=14):
    """The `VERBS` line: where a Lua/p8 cart's frame actually goes, PER FRAME.

    Per frame and not per window, because the question this answers is always
    "what is in a frame" -- and because the two shapes it distinguishes are only
    legible that way. `spr n=180 t=14.20` is a dispatch problem (a hundred and
    eighty crossings), `map n=1.0 t=14.20` is one kernel doing too much work,
    and the fix for one is not the fix for the other. Sorted by time, because
    the top row is nearly always the whole answer.

    `t` is SELF time -- the verb's own, with every wrapped verb called under it
    subtracted. `in` is its INCLUSIVE time and appears only where the two
    differ, which is exactly the verbs that run Lua: foreach and all. Without
    the split, foreach reads as the most expensive thing in the cart while
    costing nothing itself, and points a fix at the wrong file.

    Read `t` as C cost for a LEAF verb, which is nearly all of them. On a verb
    carrying an `in`, `t` is its C cost PLUS whatever Lua ran under it that was
    not itself a verb -- because non-verb Lua has no row of its own and can only
    be charged to the nearest verb enclosing it.

    `tot` sums SELF, so it is the frame's time in C. The difference between it
    and PERF's logic+render is the cart's own Lua -- the residual that decides
    whether a cart is short of 30 fps because of the engine or because of what
    it asks for, and the number no other instrument here reports.
    """
    if not frames or not hz:
        return "VERBS frames=0"
    scale = 1000.0 / (float(hz) * frames)          # ticks -> ms per frame
    rows = sorted(rows, key=lambda r: -r[2])
    tot = 0.0
    for row in rows:
        tot += row[2] * scale
    out = ["VERBS frames=%d tot=%.2f" % (frames, tot)]
    for row in rows[:top]:
        name, calls, self_t = row[0], row[1], row[2]
        incl = row[3] if len(row) > 3 else self_t
        cell = "%s n=%.1f t=%.2f" % (name, calls / float(frames), self_t * scale)
        if incl > self_t * 1.05:
            cell += " in=%.2f" % (incl * scale)
        out.append(cell)
    return " | ".join(out)


# The two marker comments tools/p8_lua_port.py emits around its shim. They are
# how a cart says where the generated 1,348 lines end and its own code starts,
# and they are the only thing that CAN say it: the data tables emitted above
# the shim vary in length per cart (26 lines for moss moss, 163 for one that
# needs the raw sheet), so the block sits at a different offset in every port.
P8_SHIM_OPEN = b"PICO-8 compatibility shim (generated"
P8_SHIM_CLOSE = b"end shim ==="


def cart_shim_range(cart_path):
    """The shim's (first, last) lines in whichever script holds it, or None.

    `p8.lua` on a cart the current importer wrote (SPEC.md 4). That is not a
    preference -- the range is pinned against the chunk that defines `_draw`,
    and the shim owns `_draw`, so on a split port the VM reports p8.lua's line
    numbers. A range read off main.lua would fail the pin and charge NOTHING as
    shim, which reads as "this cart has no generated half". A single-file port
    still answers from main.lua.
    """
    return (shim_line_range(cart_path + "/p8.lua")
            or shim_line_range(cart_path + "/main.lua"))


def shim_line_range(path, block=512):
    """The emitted p8 shim's (first, last) lines in the script holding it, or
    None.

    That script is `p8.lua` on a cart the current importer wrote (SPEC.md 4);
    the markers are the shim's own, so this reads either shape.

    Read in BLOCKS and never held. main.lua is ~100KB on a ported cart and the
    board being asked has that same cart resident -- moss moss holds a
    megabyte of Lua heap and barely loads at all -- so a reader that pulled the
    file in to splitlines() would OOM the very cart it was about to measure.
    This counts newlines through a small window with a carry, which is O(1) in
    the file's size and costs one pass.
    """
    keep = max(len(P8_SHIM_OPEN), len(P8_SHIM_CLOSE)) - 1
    lo = hi = None
    carry = b""
    carry_line = 1                    # the line number carry[0] sits on
    try:
        f = open(path, "rb")
    except OSError:
        return None
    try:
        while True:
            buf = f.read(block)
            if not buf:
                break
            hay = carry + buf
            if lo is None:
                i = hay.find(P8_SHIM_OPEN)
                if i >= 0:
                    # The banner line above the marker opens the emitted block.
                    lo = carry_line + hay.count(b"\n", 0, i) - 1
            if lo is not None and hi is None:
                i = hay.find(P8_SHIM_CLOSE)
                if i >= 0:
                    hi = carry_line + hay.count(b"\n", 0, i)
                    break
            cut = len(hay) - keep
            if cut < 0:
                cut = 0
            carry_line += hay.count(b"\n", 0, cut)
            carry = hay[cut:]
    finally:
        f.close()
    if lo is None or hi is None or hi < lo:
        return None
    return (lo, hi)


# The Xtensa selector numbers worth naming, so the serial word is a word and
# not a magic integer. They are XTPERF_CNT_* from xtensa/xt_perf_consts.h; the
# RISC-V side counts retired instructions and nothing else, and says so.
# EVERY MASK HERE IS COPIED FROM xt_perf_consts.h, not inferred. Four of them
# were guessed on the first pass and three were wrong -- INSN_ALL is 0x8DFF and
# not 0xffff, D_STALL_ALL is 0x01FE, and "calls" as 0x0060 is CALL|J, which
# counts jumps. A wrong mask does not fail; it answers confidently in the wrong
# units, which is the one thing an instrument must never do.
PERF_EVENTS = {
    "insn":    (2, 0x8DFF),   # INSN_ALL -- retired instructions, the IPC half
    "calls":   (2, 0x0042),   # INSN_CALL | INSN_CALLX -- dispatch, counted
    "dstall":  (3, 0x01FE),   # D_STALL_ALL -- the other half, if it is data
    # THE TWO CACHE-MISS MASKS ARE CORE-LEVEL AND NEARLY BLIND HERE. The S3's
    # Xtensa declares XCHAL_ICACHE_SIZE 0 and XCHAL_DCACHE_SIZE 0 -- the core
    # has no cache of its own, and the cache that matters is Espressif's,
    # OUTSIDE the core and invisible to its performance monitor. Both read ~0
    # on every cart measured, and that zero is probably structural rather than
    # a finding. Do not conclude "the working set fits the cache" from it; the
    # evidence that actually carries that conclusion is behavioural -- the
    # SRAM-floor A/B and the cross-board control in #66.
    "dmiss":   (3, 0x0008),   # D_STALL_CACHE_MISS -- see the note above
    "istall":  (4, 0x01FF),   # I_STALL_ALL -- or if it is instruction fetch
    "imiss":   (4, 0x0001),   # I_STALL_CACHE_MISS -- same caveat
    "bubbles": (6, 0x01FD),   # BUBBLES_ALL -- pipeline, not memory
    "window":  (5, 0x0020),   # EXR_WINDOW -- the windowed ABI's register
                              # spills, which present AS memory traffic
    # The sub-masks. A total tells you which bucket, and only these tell you
    # what to DO about it: CTI is the dispatch loop's branches (fuse opcodes,
    # or stop branching), REG_DEP is a load-use hazard (the operand layout the
    # opcode reads), and the two want opposite work.
    "taken":     (2, 0x0010),   # INSN_BRANCH_TAKEN -- branches RETIRED, which
                                # turns "bubbles / a guess at the per-branch
                                # cost" into a measured number
    "b_cti":     (6, 0x0080),   # BUBBLES_CTI -- control transfer
    "b_regdep":  (6, 0x0010),   # BUBBLES_R_HOLD_REG_DEP
    "b_dcache":  (6, 0x0004),   # BUBBLES_R_HOLD_D_CACHE_MISS
    "b_store":   (6, 0x0008),   # BUBBLES_R_HOLD_STORE_RELEASE
    "b_wait":    (6, 0x0020),   # BUBBLES_R_HOLD_WAIT
    "d_storebuf": (3, 0x0002),  # D_STALL_STORE_BUF_FULL
    "d_storeconf": (3, 0x0004), # D_STALL_STORE_BUF_CONFLICT
    "d_busy":    (3, 0x0010),   # D_STALL_BUSY
    "d_pif":     (3, 0x0020),   # D_STALL_IN_PIF -- the bus behind the cache
    "d_bank":    (3, 0x0100),   # D_STALL_BANK_CONFLICT
    "i_busy":    (4, 0x0002),   # I_STALL_BUSY
    "i_pif":     (4, 0x0004),   # I_STALL_IN_PIF
    "i_l32r":    (4, 0x0040),   # I_STALL_FAST_L32R -- literal loads, which an
                                # interpreter does constantly
    "i_mul":     (4, 0x0080),   # I_STALL_ITERATIVE_MUL
    "i_div":     (4, 0x0100),   # I_STALL_ITERATIVE_DIV
}


def perfcnt_line(st, name=None):
    """The `PERFCNT` line: retired instructions per cycle, over a cart's own
    halves, with the raw counts behind it.

    IPC IS THE WHOLE POINT and it is printed first. Four levers measured null
    on the S3 tick and the conclusion drawn was "memory, not instructions" --
    which is an elimination, and which closes the door on every
    code-generation idea. This is the number that either confirms it or
    re-opens it, and it is a RATIO so it survives the boards running at
    different clocks.

    Update and draw stay apart because the corpus splits that way: moss moss
    is update-bound and dank tomb draw-bound. One figure over both would
    average the answer away.
    """
    hz, frames, uc, ue, dc, de, sel, mask, selectable = st
    if not frames:
        return "PERFCNT no frames (run a cart with `perfcnt on`)"
    ev = name or ("%d/%04x" % (sel, mask))
    out = ["PERFCNT frames=%d %s" % (frames, ev)]
    for tag, cyc, evt in (("upd", uc, ue), ("draw", dc, de)):
        if not cyc:
            continue
        # per frame, and per cycle: the first says how big the half is, the
        # second is the ratio the question is about
        out.append("%s cyc=%d %s=%d r=%.3f %.3fms"
                   % (tag, cyc // frames, ev, evt // frames, evt / float(cyc),
                      (cyc / float(hz)) * 1000.0 / frames if hz else 0.0))
    if not selectable:
        out.append("(riscv: retired only)")
    return " | ".join(out)


# The HEAPCAPS line's heap_caps sets, as the raw MALLOC_CAP_* bits
# esp32.idf_heap_info takes: SPIRAM, INTERNAL, and INTERNAL|DMA -- the share an
# S3's WiFi, BLE and panel DMA draw on.
HEAP_CAPS = (("psram", 0x400), ("sram", 0x800), ("dma", 0x808))


def _num(v):
    return "-" if v is None else "%d" % v


def stale_handle_probe(ws):
    """The `kstale` line: the spine's handle gate, probed (#224 sprint 2).

    A RELEASED handle (a throwaway table's own, released and presented again),
    a FORGED one (the live app registry's first handle with its generation
    moved on), one of the WRONG KIND (the same slot and generation, presented
    as another table's) and 0 are each handed to the spine, and the line says
    how every one was answered. A refusal is a `StaleHandle` that names its
    table and the handle, so `kstale ok` means the spine said so out loud four
    times and still serves the live handle. `impl` is where the spine runs:
    `native` is native/moy_spine, `python` is runtime/moy_spine.py."""
    try:
        import moy_spine as sp
    except ImportError:        # host / test -- the runtime package
        from runtime import moy_spine as sp
    reg = ws.apps
    live = reg.handles()[0]
    scratch = sp.Table(sp.KIND_APP, "app", 4)
    gone = scratch.new("row")
    scratch.release(gone)
    probes = (
        ("released", lambda: scratch.get(gone)),
        ("forged", lambda: reg.title(live ^ (1 << sp.GEN_SHIFT))),
        ("kind", lambda: reg.title(live ^ (1 << sp.KIND_SHIFT))),
        ("zero", lambda: reg.title(0)),
    )
    out, ok = [], True
    for name, fn in probes:
        try:
            fn()
            out.append("%s=SERVED" % name)
            ok = False
        except sp.StaleHandle as exc:
            out.append("%s=%s" % (name, exc))
        except Exception as exc:  # noqa: BLE001 -- a refusal of any other kind is a finding
            out.append("%s=%s:%s" % (name, type(exc).__name__, exc))
            ok = False
    try:
        reg.title(live)
    except Exception as exc:  # noqa: BLE001
        out.append("live=%s:%s" % (type(exc).__name__, exc))
        ok = False
    impl = "python" if getattr(sp, "__file__", None) else "native"
    return "REMOTE kstale %s impl=%s %s" % ("ok" if ok else "FAIL", impl, "; ".join(out))


def heapcaps_line(esp32=None, gc=None):
    """The `HEAPCAPS` line: what each heap holds, in bytes.

        HEAPCAPS psram=T/F/L/W sram=T/F/L/W dma=T/F/L/W gc=H/V/A

    Per heap_caps set: total, free, the largest free block, and the low-water
    -- the least free since boot, summed over the set's regions as IDF's
    `heap_caps_get_minimum_free_size` sums it. `gc` is the MicroPython heap:
    H bytes its areas hold and A areas (`gc.areas()`), read BEFORE the collect
    that makes V, the bytes live -- a split heap keeps an area until a sweep
    empties it (docs/native_kernel_2026-09.md 1.3), so a collect first would
    report what the heap held after it, not what it holds.

    A figure this board or host cannot read is `-`, never 0: no `esp32` module
    (a host), no region with those caps (no PSRAM), no `gc.areas()` (a build
    without tools/patch_gc_meters.py). `gc.mem_alloc()` walks the whole heap
    -- a word's cost, never a frame's."""
    out = ["HEAPCAPS"]
    for name, caps in HEAP_CAPS:
        regs = None
        if esp32 is not None:
            try:
                regs = esp32.idf_heap_info(caps)
            except Exception:  # noqa: BLE001 -- a meter says `-`, it never raises
                regs = None
        if not regs:
            out.append(" %s=-" % name)
            continue
        tot = free = big = low = 0
        for r in regs:
            tot += r[0]
            free += r[1]
            low += r[3]
            if r[2] > big:
                big = r[2]
        out.append(" %s=%d/%d/%d/%d" % (name, tot, free, big, low))
    areas = held = live = None
    if gc is not None:
        if hasattr(gc, "areas"):
            areas, held = gc.areas()
        if hasattr(gc, "mem_alloc"):
            gc.collect()
            live = gc.mem_alloc()
    out.append(" gc=%s/%s/%s" % (_num(held), _num(live), _num(areas)))
    return "".join(out)


def luaprof_line(st, rng, top=10):
    """The `LUAPROF` line: how a Lua/p8 frame's INTERPRETER time divides.

    The verb profiler answers what a frame spends in C. This answers the other
    half, and the half that had never been separated: a ported cart is two
    bodies of Lua, its own and the 1,348 lines of PICO-8 standard library the
    importer emits into every cart it makes, and `shim=` is the share of
    sampled interpreter time spent in the emitted half.

    READ THE FIRST SHARE, NOT THE SECOND. Samples are taken every `iv` VM
    instructions, so what they weigh is instructions executed -- and the count
    hook's tax is the same for every Lua function, which is why that share
    survives it even though the frame rate does not. The second share, and the
    per-row `t`, are WALL CLOCK between samples: they carry the C verbs and,
    crucially, the COLLECTOR, which runs inside the allocator and executes no
    counted instructions at all. A row whose `t` share far exceeds its sample
    share is allocating, not computing, and on a cart holding a megabyte of
    Lua heap the difference between the two IS the collector.

    Rows are `s`/`c` for shim or cart, then the function's `linedefined` --
    which is what identifies it, since most of these functions are anonymous
    or local and a name would be a guess.
    """
    hz, frames, iv, tot, rows, srcs = st
    smp, us, calls, ccalls, ssmp, sus, scalls, drop, used, pinned = tot
    if not frames or not smp:
        return "LUAPROF frames=%d smp=0" % (frames or 0)
    lo, hi = rng if rng else (0, 0)
    share = "%d%%/%d%%" % (100 * ssmp // smp, 100 * sus // us) if us else "-"
    # n is Lua calls a frame and sn how many of them entered the shim; c is C
    # calls, which is the same population the VERBS line breaks down by name.
    out = ["LUAPROF frames=%d iv=%d smp=%d shim=%s n=%.0f sn=%.0f c=%.0f "
           "used=%d drop=%d"
           % (frames, iv, smp, share if pinned else "n/a",
              calls / float(frames), scalls / float(frames),
              ccalls / float(frames), used, drop)]
    if not pinned:
        out[0] += " (unpinned: lines %d-%d are not this cart's shim)" % (lo, hi)
    scale = 1.0 / (1000.0 * frames)             # microseconds -> ms a frame
    cart_src = 0
    for i, s in enumerate(srcs):
        if s == "cart":
            cart_src = i
    for r in rows[:top]:
        src, line, n, s, rus = r
        tag = "s" if (pinned and src == cart_src and lo <= line <= hi) else "c"
        out.append("%s%d %.1f%% n=%.1f t=%.2f"
                   % (tag, line, 100.0 * s / smp, n / float(frames), rus * scale))
    return " | ".join(out)


def _recv_err(ws, exc, got, total):
    """The RECV ERR text for a write that failed: `store full ...` when the
    store says it had no room (moy_carts.store_full), which the push tool and
    the screen both put plainly; the exception otherwise."""
    full = getattr(getattr(ws, "carts_store", None), "store_full", None)
    if full is not None and full(exc):
        return "store full after %d of %d bytes" % (got, total)
    return "%s: %s" % (type(exc).__name__, exc)


def _moy_path(ws, rel):
    """`<carts_root>/<rel>` for a path `moy push` names, or None when it would
    leave the store: every segment non-empty and neither `.` nor `..`, no `\\`
    and no NUL (proposals/sideload.md: paths never escape cart_root)."""
    root = getattr(ws, "carts_root", None)
    if not root or not rel:
        return None
    for seg in rel.split("/"):
        if not seg or seg in (".", "..") or "\\" in seg or "\0" in seg:
            return None
    return str(root).rstrip("/") + "/" + rel


def _moy_mkdirs(path):
    """Every folder above `path`, shallowest first."""
    import os
    parts = path.split("/")[:-1]
    for i in range(2, len(parts) + 1):
        d = "/".join(parts[:i])
        try:
            os.mkdir(d)
        except OSError:
            pass


def _moy_remove(path):
    import os
    try:
        entries = os.listdir(path)
    except OSError:
        os.remove(path)
        return
    for name in entries:
        _moy_remove(path + "/" + name)
    os.rmdir(path)


def _moy_chip():
    """This board's chip, as its compiled carts' modules name it; None on a
    build without the WebAssembly engine."""
    try:
        import moy_wasm
        return moy_wasm.CHIP
    except (ImportError, AttributeError):
        return None


def _moy_format():
    """This board's compiled-code format version, which its modules' names
    carry; None on a build without the WebAssembly engine."""
    try:
        import moy_wasm
        return moy_wasm.FORMAT
    except (ImportError, AttributeError):
        return None


def _moy_descriptor(ws):
    """proposals/sideload.md's descriptor, answered to `moy?`."""
    try:
        import _ota_build
        board = _ota_build.BOARD
    except (ImportError, AttributeError):
        board = "host"
    desc = {"moy_console": "0.1", "name": "Moybyte %s" % board,
            "transports": ["serial"], "cart_root": "carts",
            "runtimes": ["lua", "wasm"] if _moy_chip() else ["lua"]}
    root = getattr(ws, "carts_root", None)
    if root:
        try:
            import os
            st = os.statvfs(str(root))
            desc["free_kb"] = st[0] * st[3] // 1024
        except (OSError, AttributeError):
            pass
    return desc


def _moy_notes(folders, ws):
    """What a `moy push` of `folders` leaves slow here: a compiled cart with no
    module for this board's chip and format, which `moy push` cannot build --
    it plays on the interpreter (docs/wasm_tier_plan_2026-09.md, "A cart
    survives its firmware")."""
    chip = _moy_chip()
    fmt = _moy_format()
    root = getattr(ws, "carts_root", None)
    if not chip or not root:
        return []
    import json
    import os
    notes = []
    for folder in sorted(folders):
        base = str(root).rstrip("/") + "/" + folder
        try:
            with open(base + "/manifest.json") as f:
                man = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(man, dict) or man.get("runtime") != "wasm":
            continue
        main = man.get("main") or "main.wasm"
        stem = main[:-5] if main.endswith(".wasm") else main
        try:
            os.stat("%s/%s.%s.f%s.aot" % (base, stem, chip, fmt))
        except OSError:
            notes.append("%s is a compiled cart with no module for this console "
                         "(%s, format %s), so it plays slowly on the interpreter. "
                         "Push it with Moybyte's tools/push_cart.py, which "
                         "builds that module." % (folder, chip, fmt))
    return notes


def _stage_report():
    """#210's per-stage meters from the kernel's loop: {stage: {budget_us,
    avg_us, last_us, max_us, misses, n}} in the loop's order, every measured
    field None for a stage never sampled (PERF DIAG off, or a stage this
    board has no op for), and None for a tier whose frames are not the
    kernel's."""
    if _loop is None:
        return None
    out = {}
    m = _loop.meters()
    for name in _loop.stages():
        b, avg, last, mx, misses, n = m[name]
        out[name] = {"budget_us": b, "avg_us": avg, "last_us": last,
                     "max_us": mx, "misses": misses, "n": n}
    return out


def _remote_state(ws):
    """One-line JSON snapshot for the `state` command -- the assertion source an
    on-glass harness reads instead of pixels. Every field best-effort: a broken
    subsystem reads as an error string, never a crash that kills the loop.

    ONE snapshot for every tier: the windowed fields (desk/order/focus/wins)
    appear when the WM has windows (`_wins`), the fullscreen back-stack when it
    has one (`_stack`). tests/test_p4_on_glass.py is the shape's consumer of
    record -- its assertions read these exact keys."""
    st = {}
    try:
        st["screen"] = ws.screen
        st["frames"] = getattr(ws, "_frames_drawn", None)
        st["cart"] = (getattr(ws, "cart", None) or {}).get("title")
        # A fit notice is a panel too, and not an error: a compiled cart this
        # console cannot hold, refused before it loaded (Player.notice).
        notice = getattr(getattr(ws, "player", None), "notice", None)
        st["notice"] = notice
        st["cart_error"] = None if notice else getattr(ws, "cart_error", None)
        st["diag"] = bool(getattr(ws, "diag_live", False))
        # Settings -> UNKNOWN SOURCES: whether a compiled cart whose module
        # carries no signature may load on this console.
        st["unknown_sources"] = bool(getattr(ws, "unknown_sources", False))
        # Idle screen blank: the harness has to be able to tell a blanked panel
        # from a hung one -- they look identical from the host end.
        # The kernel's ladder (native/moy_kernel/moy_idle.c): [asleep, the
        # blank rung's seconds], and the whole ladder beside it.
        idle = _loop.idle() if _loop is not None else None
        st["psave"] = None if idle is None else [idle[0] >= 3, idle[3]]
        st["idle"] = None if idle is None else {
            "state": idle[0], "dim": idle[1], "saver": idle[2], "blank": idle[3]}
        # Expensive-event counters (ws.note_cost): cache builds + storage
        # reads. A cache that is silently missing shows up here as a count that
        # tracks the frame count.
        st["costs"] = dict(getattr(ws, "costs", {}) or {})
        # history_commit swallows a failed sidecar prune so the commit stays
        # honest, so this count is the only sign a store stopped pruning.
        store = getattr(ws, "carts_store", None)
        fails = getattr(store, "history_prune_fails", None)
        st["prune_fails"] = fails() if fails is not None else None
    except Exception as exc:  # noqa: BLE001
        st["ws_err"] = str(exc)
    try:
        wm = ws.wm
        if hasattr(wm, "stack"):
            # The process back-stack, which on the fullscreen tier IS the whole
            # window model: `ws.screen` is a read-only projection of its top.
            st["stack"] = wm.stack.kinds()
        if hasattr(wm, "_wins"):
            # The windowed tier's window model (#73/#105).
            desk = getattr(wm, "desk_open", None)
            st["desk"] = bool(desk()) if desk is not None else None
            st["order"] = list(getattr(wm, "_order", ()) or ())
            st["focus"] = getattr(wm, "_focus", None)
            wins = {}
            for k in st["order"]:
                win = wm._wins.get(k)
                if win is not None:
                    wins[k] = [win.x, win.y, win.w, win.h, win.title_h,
                               win.kind, bool(win.minimized)]
            st["wins"] = wins
            # #113: a window buffer is ONE retained surface -- 2 here means a
            # blit-scroll would measure against the wrong paint and ghost.
            st["win_retained"] = dict(
                (k, getattr(wm._wins[k].buf, "RETAINED_FRAMES", None))
                for k in st["order"]
                if wm._wins.get(k) is not None and wm._wins[k].buf is not None)
    except Exception as exc:  # noqa: BLE001
        st["wm_err"] = str(exc)
    try:
        sl = ws.settings_layer
        sr = sl.scroll
        st["settings"] = {
            "set_top": sl.set_top, "sel": sl.set_msel,
            "rows": len(sl._settings_rows()),
            "offset": None if sr is None else sr.offset,
            "view": None if sr is None else list(sr.view),
            "content": None if sr is None else sr.content,
            "wifi_view": bool(sl.wifi_view),
            "bt_view": bool(getattr(sl, "bt_view", False)),
            # the toggle whose warning is up before it turns ON, or None
            "confirm": getattr(sl, "confirm_key", None),
            # the kernel's last-crash panel is up (Settings -> LAST CRASH)
            "crash_view": bool(getattr(sl, "crash_view", False)),
        }
        win = ws.wm._wins.get("settings") if hasattr(ws.wm, "_wins") else None
        if win is not None and win.ctx is not None:
            lay = win.ctx.layout
            st["settings"]["lay"] = [lay.set_x, lay.set_row_y0,
                                     lay.set_w, lay.set_row_h]
    except Exception as exc:  # noqa: BLE001
        st["settings_err"] = str(exc)
    try:
        st["wifi"] = list(ws.wifi.status()) if ws.wifi is not None else None
        st["wifi_held"] = sorted(ws.leases.holders())  # the radio lease's holders
    except Exception as exc:  # noqa: BLE001
        st["wifi_err"] = str(exc)
    try:
        # The last crash the kernel recorded (native/moy_kernel): what was
        # OPEN in the strike ledger, why it stopped, where. None on a board
        # with no record and on every tier without the kernel.
        st["crash"] = last_crash()
    except Exception as exc:  # noqa: BLE001
        st["crash_err"] = str(exc)
    try:
        # How this VM started and what each part of it took, from the
        # kernel's stamps (docs/kernel_cartpath_2026-10.md section 5.5).
        # None on a tier without them.
        st["start"] = kernel_start()
    except Exception as exc:  # noqa: BLE001
        st["start_err"] = str(exc)
    try:
        # The #7 radio. A board with no link reports None -- never a zeroed
        # tuple, because a board that HAS one and is simply not paired must be
        # distinguishable from a board that can never pair. (The scale-fold
        # meter printed a frozen 0 for a month for exactly the other reason.)
        _lk = getattr(ws, "link", None)
        st["link"] = list(_lk.status()) if _lk is not None else None
    except Exception as exc:  # noqa: BLE001
        st["link_err"] = str(exc)
    try:
        # The banded flush's meters, for the boards that have one (absent on the
        # P4, whose DSI panel scans and does not push). `pump` is the whole
        # moy_flush tuple:
        #
        #   (pump_us, idle_us, idle_n, feed_us, bands, blocked_us,
        #    timeouts, errs, stop_fails)
        #
        # This is the ONLY route on the Guition, which denies `device_diag` and
        # so has no PUMP line -- and timeouts/errs/stop_fails are failures the C
        # cannot raise (a drain must not throw into the frame loop, and a deinit
        # that gave up on the feeder keeps the console running), so without this
        # field they are invisible there.
        comp = getattr(ws, "comp", None)
        bs = getattr(comp, "bounce_stats", None)
        if bs is not None:
            st["pump"] = list(bs())
        # #190's scale fold. `None` and not 0 when a board lacks the lever: 0 is
        # also what a fold that never fires looks like, so a default of 0 reads
        # as "working, just quiet".
        st["fold"] = getattr(comp, "fold_count", None)
        # ...and how many of those were a compiled cart's frame, folded from
        # the cart's memory (moy_fold.h's frame fold), by the same rule.
        st["ffold"] = getattr(comp, "frame_fold_count", None)
        # The P4's counterpart, same None-not-0 rule: a scanning panel has no
        # pump but does have the async-PPA overlap (#58). `ppa` is the whole
        # overlap_stats tuple, whose fields that method documents; `timeouts`
        # is moy_ppa's `errs` -- a failure a fence cannot raise into the frame
        # loop, so it shows up here or nowhere.
        ov = getattr(comp, "overlap_stats", None)
        st["ppa"] = list(ov()) if ov is not None else None
    except Exception as exc:  # noqa: BLE001
        st["pump_err"] = str(exc)
    try:
        # #210's per-stage deadline meters: {stage: {budget_us, last_us, max_us,
        # misses, n}} in the loop's invariant order. `state` is the route
        # because it is the one every board serves -- the Guition stages no
        # device_diag and so has no PUMP line to hang this off.
        #
        # The measurement is perf_capture-gated like every other frame-eater
        # here, so a kid's console reports every stage unsampled (n=0, the rest
        # None) and `diag 1` is what arms it. None is also the answer for a
        # stage this board has no hook for and for a whole tier with no shared
        # frame loop -- never 0, which is what a broken meter reads as.
        st["stages"] = _stage_report()
        ups = None if _loop is None else _loop.upcalls()
        st["upcalls"] = None if ups is None else list(ups[0])
        st["upcall_totals"] = None if ups is None else list(ups[1])
    except Exception as exc:  # noqa: BLE001
        st["stages_err"] = str(exc)
    try:
        app = getattr(ws, "appearance_app", None)
        cart = None
        for c in ws.carts.all:
            if c.get("title") == "Appearance":
                cart = c
                break
        if cart is None:
            st["appearance_cart"] = None
        else:
            st["appearance_cart"] = {
                "title": cart.get("title"), "version": cart.get("version"),
                "path": cart.get("path"),
                "perms": list(cart.get("permissions") or ()),
                "is_app": bool(app.is_app(cart)) if app is not None else None,
            }
        claims = {}
        for _app, _text in getattr(ws, "_apps", ()):
            claims[_app.id] = sum(1 for c in ws.carts.all if _app.is_app(c))
        st["app_claims"] = claims
    except Exception as exc:  # noqa: BLE001
        st["app_err"] = str(exc)
    try:
        # The tick model (#217): [rate, divisor, misses, steady] while a game
        # is paced, else None -- the on-glass suites read "did the cart hold
        # its tick" from here rather than from pixels.
        pl = ws.player
        st["tick"] = ([pl.sched.rate, pl.sched.div, pl.sched.misses,
                       bool(pl.sched.steady)] if pl.tick_ms else None)
        st["uncap"] = bool(pl.sched.uncapped) if pl.tick_ms else None
        # The run's VM-free verdict (docs/kernel_cartpath_2026-10.md §2), the
        # kernel's rule over the cart's catalogue entry: what the census pins
        # and the zero-upcall gate reads beside the frame's upcalls. None with
        # no cart in front, or no rule in the image.
        v = pl.verdict if st.get("cart") else None
        st["run"] = (None if v is None else
                     {"runtime": v[0], "vm_free": bool(v[1]), "why": v[2]})
        # The run's frame in the kernel's Player (native/moy_play): its
        # frames and ticks there, and the upcalls by class since its launch
        # (CONSOLE, APP, DRIVER, SERVICE, REFUSED). None while the run ticks
        # through Python, or with no cart in front.
        info = None
        if st.get("cart") and getattr(pl, "_play", None) is not None:
            import moy_play
            info = moy_play.info()
        # The launch's stop verdict and whether the run had the VM down
        # (docs/kernel_cartpath_2026-10.md section 5), where the Player has
        # the run.
        if st["run"] is not None and info is not None and len(info) > 12:
            st["run"]["stop"] = info[11]
            st["run"]["vm_down"] = bool(info[12])
            # the verdict's fit check with the VM up: (need, need_block,
            # free, largest, vm_heap) bytes, None where it reached none
            st["run"]["fit"] = list(info[13]) if len(info) > 13 and info[13] else None
        st["play"] = (None if info is None else
                      {"runtime": info[0], "frames": info[3], "ticks": info[4],
                       "upcalls": list(info[5]),
                       # the task's stack high-water mark in bytes: before the
                       # runtime opened, after the Player's open, after the
                       # last frame (None off a board)
                       "stack": [pl.stack_pre, info[8], info[9]]})
    except Exception as exc:  # noqa: BLE001
        st["tick_err"] = str(exc)
    try:
        # #211: the internal-SRAM headroom the cart run actually had --
        # {sram_free_min, psram_fallback, floor} -- live while a Lua cart is
        # open, the ended run's at the launcher. None (never zeros) for a Python
        # cart and for a tier whose allocator has one region: the moment a cart
        # tips into the ~2x-slower PSRAM regime has to be distinguishable from
        # the board that cannot tip at all.
        st["sram"] = ws.player.sram_report()
    except Exception as exc:  # noqa: BLE001
        st["sram_err"] = str(exc)
    return st



START_PARTS = ("vm", "imports", "workstation", "wiring", "first_frame")


def start_parts(kind, stamps):
    """The start's parts from the kernel's six stamps (ms after power-on:
    exit, vm, imports, ws, wired, frame; None where not reached): {"kind",
    "total", and each of START_PARTS} in ms, a part None when either of its
    ends is missing. `exit` is 0 at power-on, so a boot's total is from it."""
    out = {"kind": kind}
    for k, name in enumerate(START_PARTS):
        a, b = stamps[k], stamps[k + 1]
        out[name] = None if a is None or b is None else b - a
    first, last = stamps[0], stamps[-1]
    out["total"] = None if first is None or last is None else last - first
    return out


def kernel_start():
    """start_parts() for the running VM, or None without the kernel's stamps."""
    try:
        import moy_kernel
    except ImportError:
        return None
    stamps = getattr(moy_kernel, "stamps", None)
    if stamps is None:
        return None
    return start_parts(moy_kernel.start(), stamps())

class DevChannel:
    """Line commands over USB-CDC stdin, read one byte at a time.

    See SERIAL_CMDS above for why this exists at all and why the byte-at-a-time
    reader is not fussiness. In one sentence: `poll()` is trustworthy (the esp32
    port sets MP_STREAM_POLL_RD only when the stdin ring buffer is non-empty),
    `readline()` is not (it blocks per character until a newline that noise will
    never supply), so this reads exactly the bytes poll promised and no more.

    ONE vocabulary, every board. A command that needs a capability the board
    lacks says so ("no window open", "no backlight control") instead of not
    existing -- a declined verb is greppable, a missing one is a mystery.
    `tools/p4_autotest.py`'s approach -- drive the console over serial, assert
    against `state` -- points at this directly.

      state           one-line JSON snapshot (_remote_state above)
      tap <x> <y>     a synthetic tap at canvas coords
      tap <name>      tap a named bar button (any ws.layout.<name>_btn rect)
      run [name]      select the first cart whose title matches, and run it
      open <what>     settings|picker|appearance|wifi -- deterministic app open
      swipe x0 y0 x1 y1 [frames]   a touch gesture through the real pointer
                      feed: press edge, held interpolation, real release
      drag [frames] [step]         grab the TOP window's title strip and
                      oscillate it (windowed tier; declines with no window)
      diag 0|1        PERF DIAG: the frame-eaters (perf_capture + the FPS chip)
                      and every periodic line, PERF included
      steady 0|1      the tick model's STEADY / FREE knob (#217)
      crisp 0|1       the #204 nearest-neighbour composite of palette-based
                      game frames
      unknown_sources 0|1   Settings -> UNKNOWN SOURCES: whether a compiled
                      cart's unsigned module may load, set without the
                      screen's warning -- these three are SETTINGS_TOGGLES
                      entries that declared a serial word, not branches
                      written here; a board whose capability gate says no
                      declines the word. None persists, so a test or a
                      measurement session cannot leave the board off-default.
      uncap 0|1       DIAG: every loop frame draws while logic keeps its rate
                      -- the draw+present path flat out, the game at its own
                      speed. Never persists; `fps=` is the answer
      skip, gov       retired with FRAMESKIP and the governor (#217); both
                      decline and name `steady`
      mem             a forced collect + the live/free split
      bl 0|1          panel backlight. The board keeps RENDERING either way, so
                      a dark screen is a fine way to bench unattended.
      vol <0-7>       master audio level; 0 is silent
      power <secs>    idle screen-blank timeout (0 disables); `power off` blanks
                      now, `power on` wakes now. The board keeps RENDERING while
                      dark, so an unattended bench run still produces frames.
      web [start]     serve the wasm console from this board (ws.webhost)
      link [on|off|cart <title>]   the #7 ESP-NOW radio: peers, pairing and
                      the live lockstep match, as JSON. `on`/`off` arm it by
                      hand (the Player only arms it for a multiplayer cart)
      py <code>       eval/exec one line against the LIVE console
      recv <n> <window> [rate=<baud>] <path>   take n RAW bytes off stdin
                      into <path>.new, acking every <window> of them, the
                      payload at <baud> where the console UART can switch;
                      prints the sha256 prefix of what it wrote. Bare `recv`
                      answers with the caps line an older image cannot fake
                      (see _recv)
      quit            leave the desktop for the REPL

    THE WORDS OF A SUBSYSTEM ARE REGISTERED: `tap`, `swipe` and `drag` from
    devch_input.py, `vol` from devch_audio.py, `link`, `web`, `recv` and the
    `moy` lines from devch_links.py, each a WORDS table this class's `words`
    holds; `run` keeps the rest.

    BOARD BITS ARE INJECTED: `env` (extra names for `py` -- comp/game/boot
    on the boards), and `extra`, a
    {name: handler(ws, parts, line)} dict of board-only commands (the P4's
    `bt`/`union`/`cache`). Extras dispatch AFTER the built-ins and cannot
    shadow them -- one vocabulary is the point.

    A SETTINGS TOGGLE is not board-only even when only one board can serve it:
    it declares its word in SETTINGS_TOGGLES and its capability gate declines
    everywhere else, which is why `crisp` is a built-in here and the P4's
    identically-behaved `crisp` extra is now shadowed dead.
    """

    def __init__(self, ws, pointer, extra=None, env=None):
        self.pointer = pointer
        self._ws = ws
        self.quit = False       # `quit` asked for the REPL; the loop ends
        self.raw = 0            # bytes taken by `recv`, around the line reader
        self.rx = 0             # bytes `recv` and `moy-put` read off the stream
        self.extra = extra or {}             # board-only commands
        # The word table each subsystem registers into (devch_input,
        # devch_audio, devch_links); `run` dispatches through it first.
        self.words = {}
        for mod in (devch_input, devch_audio, devch_links):
            self.words.update(mod.WORDS)
        self.env = env or {}                 # extra names in the `py` scope
        self._poll = None
        self._stdin = None
        self._rawin = None      # sys.stdin.buffer: the same ring, 8 bits wide
        self._ipoll = None      # ipoll where there is one -- see below
        self._one = bytearray(1)  # `_fill`'s byte when there is no moy_serial
        self._put = None        # the `moy-put` in flight: see _moy_put
        self._pushed = set()    # cart folders `moy-put` wrote this session
        try:
            import select
            import sys
            self._stdin = sys.stdin
            self._rawin = getattr(sys.stdin, "buffer", None)
            self._poll = select.poll()
            # POLLIN and nothing else. A bare register() defaults to RD|WR, and
            # mphalport.c grants POLL_WR unconditionally -- so a bare
            # registration is truthy on EVERY call, forever.
            self._poll.register(self._stdin, select.POLLIN)
            # `recv` and `moy-put` poll once PER BYTE where there is no
            # moy_serial; ipoll reuses one tuple and allocates nothing after
            # the first call.
            self._ipoll = getattr(self._poll, "ipoll", None) or self._poll.poll
        except Exception:  # noqa: BLE001 -- the stream-takers decline without one
            pass

    def word(self, ws, line):
        """One line the kernel's reader handed over (the loop's word upcall):
        a `moy-put`'s next line while one streams, else a command. Answers
        True when the line asked for the REPL. Never raises -- a word that
        fails says so on the line and the loop goes on."""
        try:
            if self._put is not None:
                self._moy_put_line(ws, line)
            else:
                self.run(ws, line)
        except Exception as exc:  # noqa: BLE001 -- never kill the loop
            print("REMOTE ERR %s: %s" % (type(exc).__name__, exc))
        if _loop is not None:
            # A put streaming its lines through the reader takes them faster.
            _loop.devch_budget(MOY_PUT_BYTES_PER_FRAME if self._put is not None
                               else SERIAL_BYTES_PER_FRAME)
        return self.quit

    @property
    def armed(self):
        """The kernel's line reader is armed (native/moy_kernel/moy_devch.c)."""
        return _loop is None or _loop.devch_stats()[3]

    def _verbs(self, parts):
        """`verbs on|off|reset` and bare `verbs` -- the Lua/p8 tier's per-verb
        profiler (moycore.profile / verb_stats).

        It is its OWN switch rather than a rider on `diag`, and deliberately:
        every diag session would otherwise pay a profiler's per-call tax to
        answer a question a measurement session asks on purpose. Off is the
        default and off means the cart's globals are libmoy's C functions with
        nothing wrapped around them.
        """
        try:
            import moycore
        except ImportError:
            print("REMOTE verbs: no moycore on this board")
            return
        arg = parts[1] if len(parts) > 1 else ""
        if arg in ("0", "off"):
            moycore.profile(0)
            print("REMOTE verbs off")
            return
        if arg in ("1", "on"):
            n = moycore.profile(1)
            moycore.verb_reset()
            # None means armed but not installed -- no VM to install into yet.
            # A cart CAPTURES its globals as it loads, so arming and then
            # launching is the reading that misses nothing; installing into a
            # running cart still catches every verb it calls by global name,
            # which is nearly all of them.
            if n is None:
                print("REMOTE verbs armed (takes effect at the next launch)")
            else:
                print("REMOTE verbs on wrapped=%d" % n)
            return
        if arg == "reset":
            moycore.verb_reset()
            print("REMOTE verbs reset")
            return
        st = moycore.verb_stats()
        if st is None:
            print("REMOTE verbs: not armed (`verbs on`, then relaunch the cart)")
            return
        print(verbs_line(st[0], st[1], st[2]))

    def _perfcnt(self, parts):
        """`perfcnt on|off|reset [event]` and a bare `perfcnt` -- the CPU's own
        performance counters, across a cart's update and draw.

        Its own switch, like `verbs` and `luaprof`: disarmed, `tick` tests one
        byte a frame and touches no register, so an ordinary diag session pays
        nothing for a question a measurement session asks on purpose.

        `event` names what counter 1 counts (the Xtensa part has exactly two
        and counter 0 is always cycles). Default `insn`, which is the ratio
        the instrument exists for; `perfcnt on dstall` and the rest are the
        follow-up when IPC says memory.
        """
        try:
            import moycore
        except ImportError:
            print("REMOTE perfcnt: no moycore on this board")
            return
        arg = parts[1] if len(parts) > 1 else ""
        if arg in ("0", "off"):
            moycore.perf_counters(0)
            print("REMOTE perfcnt off")
            return
        if arg in ("1", "on"):
            want = parts[2] if len(parts) > 2 else "insn"
            if want not in PERF_EVENTS:
                print("REMOTE perfcnt: no event %r (%s)"
                      % (want, " ".join(sorted(PERF_EVENTS))))
                return
            sel, mask = PERF_EVENTS[want]
            if moycore.perf_counters(1, sel, mask) is None:
                print("REMOTE perfcnt: this board has no counters")
                return
            moycore.perf_reset()
            self._perf_ev = want
            print("REMOTE perfcnt on %s" % want)
            return
        if arg == "reset":
            moycore.perf_reset()
            print("REMOTE perfcnt reset")
            return
        st = moycore.perf_stats()
        if st is None:
            print("REMOTE perfcnt: this board has no counters")
            return
        print(perfcnt_line(st, getattr(self, "_perf_ev", None)))

    def _luaprof(self, ws, parts):
        """`luaprof on|off|reset [interval]` and a bare `luaprof` -- the Lua
        tier's per-FUNCTION sampling profiler (moycore.lua_profile).

        Its own switch, like `verbs` and for the same reason: a count hook
        makes EVERY VM instruction detour through luaG_traceexec, which is a
        tax an ordinary diag session must not pay to answer a question a
        measurement session asks on purpose.

        `on` resolves the running cart's shim line range from its own main.lua
        before arming, so the split is measured against the file that is
        actually loaded rather than a constant baked in here -- the emitted
        block is a fixed 1,348 lines but it starts wherever that cart's data
        tables ended.
        """
        try:
            import moycore
        except ImportError:
            print("REMOTE luaprof: no moycore on this board")
            return
        arg = parts[1] if len(parts) > 1 else ""
        if arg in ("0", "off"):
            moycore.lua_profile(0)
            self._shim_rng = None
            print("REMOTE luaprof off")
            return
        if arg in ("1", "on"):
            try:
                iv = int(parts[2]) if len(parts) > 2 else 1024
            except ValueError:
                iv = 1024
            cart = getattr(ws, "cart", None) or {}
            rng = cart_shim_range(cart["path"]) if cart.get("path") else None
            self._shim_rng = rng
            lo, hi = rng if rng else (0, 0)
            n = moycore.lua_profile(1, iv, lo, hi)
            moycore.lua_reset()
            if n is None:
                print("REMOTE luaprof armed iv=%d shim=%d-%d" % (iv, lo, hi))
            else:
                print("REMOTE luaprof on iv=%d shim=%d-%d" % (iv, lo, hi))
            return
        if arg == "reset":
            moycore.lua_reset()
            print("REMOTE luaprof reset")
            return
        st = moycore.lua_stats(10)
        if st is None:
            print("REMOTE luaprof: not armed (`luaprof on`)")
            return
        print(luaprof_line(st, getattr(self, "_shim_rng", None)))

    def _luagc(self, parts):
        """`luagc [stop|run|inc [pause step size]|gen [minor major]]` -- the
        cart VM's collector, read and set.

        It is a knob here and not upstream because moycore OPENS the VM: the
        engine and the shim are both vendored, but the collector's schedule is
        this file's. And it is the one cost `luaprof` cannot see -- collection
        runs inside the allocator rather than as counted VM instructions -- so
        the two are read together or not at all.
        """
        try:
            import moycore
        except ImportError:
            print("REMOTE luagc: no moycore on this board")
            return
        arg = parts[1] if len(parts) > 1 else ""
        nums = []
        for p in parts[2:]:
            try:
                nums.append(int(p))
            except ValueError:
                pass
        while len(nums) < 3:
            nums.append(-1)
        mode = {"stop": 0, "run": 1, "inc": 2, "gen": 3}.get(arg, -1)
        st = moycore.lua_gc_mode(mode, nums[0], nums[1], nums[2])
        if st is None:
            # ARMED, not ignored: the mode is applied at the next load(), which
            # is what makes an A/B possible at all -- every tool here changes
            # one thing by relaunching the cart.
            print("REMOTE luagc armed mode=%s (no cart running)" % (arg or "-"))
            return
        print("REMOTE luagc heap=%dKB running=%d gen=%d"
              % (st[0], 1 if st[1] else 0, 1 if st[2] else 0))

    def report(self, diag):
        """One SERIAL line per diag tick, and it is the channel's self-diagnosis:
        `rx` climbing while `lines` stays 0 means something is injecting bytes
        into stdin that are not commands -- UART0's ISR shares this ring buffer,
        so a floating U0RXD (GPIO44, on the expansion header) reads exactly like
        this. That is a fact, printed, instead of a hang to be puzzled over."""
        st = _loop.devch_stats() if _loop is not None else (0, 0, 0, False)
        _diag_log("SERIAL", "rx=%d lines=%d dropped=%d raw=%d"
                  % (st[0], st[1], st[2], self.raw), diag)

    def _recv(self, line, parts, ws=None):
        """`recv <nbytes> <window> [rate=<baud>] <path>`: nbytes RAW off stdin
        into <path>.new, in windows the host may not run ahead of.

        THE WINDOW IS THE ONLY BACKPRESSURE THE WAVESHARE P4 HAS. Its stdin is
        a 4 KB ring fed by a UART ISR with no flow control -- a byte that
        arrives with the ring full is dropped, silently -- so the host writes
        one window, smaller than the ring, and then WAITS for its ack. The ack
        goes out as soon as the window is in the buffer and BEFORE the file
        write, so the next window crosses the wire while the store writes: at
        most one window is ever in flight, and the ring holds it whole through
        any stall, the write and a heap collection included. USB boards
        backpressure for real (the USB-Serial/JTAG ISR only drains what the
        ring has room for), so their window is bigger for fewer round trips,
        not for safety. Both numbers live in board.toml.

        `rate=<baud>` runs the payload at another UART rate where the build
        can switch its console's (moy_serial.baud), and the console goes back
        to its own rate before anything else is said. Every switch is followed
        by the host's RECV_SYNC, so a byte the switch left on the line is
        dropped rather than taken as payload. A board that cannot switch
        ignores the token and the payload comes at the console's rate.

        The transcript, which tools/push_cart.py is the reader of record for:

            RECV ready <n> <window> [rate=<baud>] <path>.new   armed; with a
                                                  rate, the board is at it now
            RECV sync <baud>                      the host's sync arrived at
                                                  the new rate; send window 1
            RECV ack <bytes so far>               one per window, before its
                                                  write; after the last one the
                                                  board is back at the console
                                                  rate and waits for a sync
            RECV retry <bytes so far>             that window came up short;
                                                  re-send FROM this offset
            RECV done <sha12> <nbytes>            what landed, hashed
            RECV ERR <what>                       gave up; tmp removed; said
                                                  at the payload's rate when
                                                  that is where the host is
            RECV ERR store full after <n> of <total>   the store has no room;
                                                  the screen says so too
            RECV caps max=<n> idle=<ms> [rate=<baud>]  bare `recv`: the probe;
                                                  rate= is the console's, and
                                                  says this board can switch

        `RECV caps` is the whole capability handshake. An image without this
        command answers `REMOTE ? recv` from the same dispatcher, which is a
        definite "no" -- and since there is no other upload path left, the host
        stops there and says the firmware is too old, rather than blasting
        bytes at a board that is still reading lines.

        The transfer runs inside the console's storage gate (`ws._with_sd`):
        on the T-Deck the card shares the panel's SPI host, and a store op
        that overlaps a panel transfer hangs the board.

        Nothing is left half-written: the payload lands in a .new the caller
        renames only after the hash agrees, and every failure path here removes
        it before printing why."""
        raw = self._rawin
        polled = raw is not None and self._ipoll is not None
        if not RECV_8BIT or (_moy_serial is None and not polled):
            print("RECV ERR no 8-bit route on this build (stdin.buffer=%s "
                  "poll=%s kbd_intr=%s)"
                  % (raw is not None, self._ipoll is not None, RECV_8BIT))
            return
        if len(parts) < 4:
            rate = _console_rate()
            print("RECV caps max=%d idle=%d%s"
                  % (RECV_MAX_WINDOW, RECV_IDLE_MS,
                     " rate=%d" % rate if rate else ""))
            return
        try:
            total = int(parts[1])
            window = int(parts[2])
        except ValueError:
            print("RECV ERR bad count/window: %s" % line)
            return
        if total < 0 or window < 1 or window > RECV_MAX_WINDOW:
            print("RECV ERR %d bytes / window %d (max %d)"
                  % (total, window, RECV_MAX_WINDOW))
            return
        path = line.split(None, 3)[3]
        fast = 0
        if path.startswith("rate="):
            tok, _, path = path.partition(" ")
            path = path.strip()
            try:
                fast = int(tok[5:])
            except ValueError:
                print("RECV ERR bad rate: %s" % tok)
                return
        console = _console_rate() if fast else None
        if not console or fast == console:
            fast = 0
        gate = getattr(ws, "_with_sd", None)
        args = (ws, total, window, path + ".new", fast, console)
        err, said = (self._recv_file(*args) if gate is None
                     else gate(lambda: self._recv_file(*args)))
        if err is None:
            return
        if not said:
            print("RECV ERR %s" % err)
        say = getattr(ws, "notice", None)
        if err.startswith("store full") and say is not None:
            say("CAN'T ADD CART", "the store is full", "warn")

    def _recv_file(self, ws, total, window, tmp, fast, console):
        """The body of `recv`: (None, _) once it has printed `RECV done`,
        else (the error, whether it was already said at the payload's
        rate)."""
        try:
            f = open(tmp, "wb")
        except Exception as exc:  # noqa: BLE001 -- a bad path is an answer
            return "cannot open %s: %s" % (tmp, exc), False
        import hashlib
        buf = bytearray(window)
        mv = memoryview(buf)
        # The kernel's line reader dispatched this command the instant it saw
        # the newline and reads nothing past it, so the payload's first byte
        # is the stream's next.
        pending = b""
        got = 0
        err = None
        rate = 0                # the payload rate the board is at, 0 = console
        synced = False          # the host's closing sync is still to come
        said = False
        _kbd_intr(-1)
        print("RECV ready %d %d %s%s"
              % (total, window, "rate=%d " % fast if fast else "", tmp))
        left = RECV_RETRIES
        empty = 0
        try:
            if fast:
                _moy_serial.baud(fast)
                rate = fast
                if self._sync(RECV_SYNC_MS):
                    print("RECV sync %d" % fast)
                else:
                    # Nobody to say it to: the host gives up on its own clock,
                    # which is longer, and comes back at the console rate.
                    _moy_serial.baud(console)
                    rate = 0
                    err = "no sync at %d" % fast
            while err is None and got < total:
                n = total - got
                if n > window:
                    n = window
                i = 0
                held = pending
                if pending:
                    i = len(pending)
                    if i > n:
                        i = n
                    buf[0:i] = pending[:i]
                    pending = pending[i:]
                if _kernel_feed is not None:
                    _kernel_feed()
                i = self._fill(buf, i, n, RECV_IDLE_MS)
                if i < n:
                    # The stream stopped inside the window, which on a ring
                    # with no flow control is what a DROPPED byte looks like:
                    # the host wrote the whole window and is now waiting for an
                    # ack it will never get. Nothing of it has been written,
                    # so `got` is still a window boundary -- throw the short
                    # window away and ask for it again. The wire is quiet by
                    # construction (that is what the timeout just proved), so
                    # nothing is in flight to prefix the re-send.
                    empty = empty + 1 if i == 0 else 0
                    if left <= 0 or empty >= RECV_DEAD_WINDOWS:
                        # `got`, not `got + i`: the i bytes of this window are
                        # about to be thrown away, and naming them sends the
                        # reader looking for a file that never existed.
                        err = "timeout after %d of %d bytes" % (got, total)
                        break
                    left -= 1
                    pending = held
                    print("RECV retry %d" % got)
                    continue
                print("RECV ack %d" % (got + n))
                last = got + n == total
                if last and rate:
                    # Back to the console rate the moment the ack has left,
                    # which is before the host can have read it; the host's
                    # sync at that rate waits in the ring for the end.
                    _moy_serial.baud(console)
                    rate = 0
                    synced = True
                try:
                    f.write(mv[:n])
                except Exception:  # noqa: BLE001 -- said below, by the caller
                    if not last:
                        # The host had the ack and is sending the next window:
                        # take it off the wire, so it never reaches the line
                        # reader as commands.
                        self._fill(buf, 0, min(window, total - got - n),
                                   RECV_IDLE_MS)
                    raise
                got += n
                self.raw += n
        except Exception as exc:  # noqa: BLE001 -- a full store must not kill the loop
            err = _recv_err(ws, exc, got, total)
        finally:
            if rate:
                # The host is still at the payload's rate, waiting on a
                # window's reply: say it there, then go back.
                if err is not None:
                    print("RECV ERR %s" % err)
                    said = True
                _moy_serial.baud(console)
            try:
                f.close()
            except Exception as exc:  # noqa: BLE001 -- a close can be the write that fails
                if err is None:
                    err = _recv_err(ws, exc, got, total)
            if synced:
                # Still with the interrupt char off: what the host's switch
                # back left on the line is the sync's to drop, and a 0x03 in
                # it would otherwise interrupt the console.
                try:
                    self._sync(RECV_SYNC_MS)
                except Exception:  # noqa: BLE001 -- the transfer is decided
                    pass
            _kbd_intr(3)
        if err is None:
            # Hash the FILE, not the buffer that filled it. `open(p,'wb')` has
            # reported a byte count on this console for a file that read back
            # EMPTY (push_cart's header, item 2), and a hash taken from RAM
            # would agree with the host about a cart that is not on the store.
            # Read back through the same window buffer -- a whole-file read
            # would be a transient the size of the file.
            sha = hashlib.sha256()
            try:
                f = open(tmp, "rb")
                while True:
                    k = f.readinto(buf)
                    if not k:
                        break
                    sha.update(mv[:k])
                f.close()
            except Exception as exc:  # noqa: BLE001
                err = "cannot read back %s: %s" % (tmp, exc)
        if err is not None:
            try:
                import os
                os.remove(tmp)
            except Exception:  # noqa: BLE001 -- it may never have been created
                pass
            return err, said
        print("RECV done %s %d" % (sha.digest().hex()[:12], got))
        return None, False

    def _fill(self, buf, i, n, idle_ms):
        """buf[i:n] off stdin; the index reached, short only once the stream
        has been quiet for idle_ms. moy_serial reads the ring from C where
        the build has it; this loop polls before every byte, because a bulk
        read blocks in `mp_hal_stdin_rx_chr` with no timeout at all."""
        if _moy_serial is not None:
            return _moy_serial.readinto(buf, i, n, idle_ms)
        ipoll = self._ipoll
        rd = self._rawin.readinto
        one = self._one
        while i < n:
            ready = False
            for _ in ipoll(idle_ms):
                ready = True
            if not ready:
                break
            rd(one)
            buf[i] = one[0]
            i += 1
        return i

    def _sync(self, ms):
        """True once RECV_SYNC has arrived, within `ms`. Whatever comes before
        it is what a rate switch leaves on the line, and is dropped."""
        tok = RECV_SYNC
        k = len(tok)
        seen = bytearray(k)
        one = self._one
        t0 = _ticks_ms()
        n = 0
        while True:
            left = ms - _ticks_diff(_ticks_ms(), t0)
            if left <= 0 or self._fill(one, 0, 1, left) < 1:
                return False
            seen[0:k - 1] = seen[1:k]
            seen[k - 1] = one[0]
            n += 1
            if n >= k and seen == tok:
                return True

    # -- proposals/sideload.md's tier 1 (moy-spec), for `moy push` ----------

    def _moy(self, ws, cmd, parts, line):
        """The tier-1 lines. True when `cmd` was one of them."""
        if cmd == "moy?":
            import json
            print("moy-info %s" % json.dumps(_moy_descriptor(ws)))
        elif cmd == "moy-put":
            self._moy_put(ws, parts)
        elif cmd == "moy-del":
            self._moy_del(ws, parts)
        elif cmd == "moy-rescan":
            for note in _moy_notes(self._pushed, ws):
                print("moy-note %s" % note)
            self._pushed = set()
            # The answer goes out first: a rescan of a full store can run
            # past the sender's ten-second wait, and whatever the sender asks
            # next queues behind it on the line.
            print("moy-ok")
            rescan = getattr(ws, "rescan_carts", None)
            if rescan is not None:
                rescan()
        elif cmd == "moy-run":
            name = line.split(None, 1)[1] if len(parts) > 1 else ""
            launch = getattr(ws, "launch_named", None)
            if launch is not None and launch(name):
                print("moy-ok")
            else:
                print("moy-err no cart called %s" % name)
        else:
            return False
        return True

    def _moy_put(self, ws, parts):
        """`moy-put <path> <bytes>`: the base64 lines that follow, up to a
        line holding `.`, land in `<carts_root>/<path>` -- through a `.new`
        renamed only once exactly `<bytes>` arrived. The file is written
        inside the console's storage gate, as `recv`'s is."""
        dst = _moy_path(ws, parts[1] if len(parts) == 3 else "")
        try:
            size = int(parts[2]) if len(parts) == 3 else -1
        except ValueError:
            size = -1
        if dst is None or size < 0:
            print("moy-err usage: moy-put <path in the cart store> <bytes>")
            return
        gate = getattr(ws, "_with_sd", None)
        if gate is None:
            self._moy_put_open(ws, parts[1], dst, size)
        else:
            gate(lambda: self._moy_put_open(ws, parts[1], dst, size))

    def _moy_put_open(self, ws, rel, dst, size):
        """Arm the put. Where the board reads stdin from C the file is taken
        into RAM and written at its `.`: the stream has no flow control but
        the stdin ring, and a store write inside it -- a flash erase, the
        walk for a free block -- outlasts the ring on a UART at 115200.
        Where RAM cannot hold it, or on the host, it streams to the file."""
        try:
            _moy_mkdirs(dst)
            sink = None
            if _moy_serial is not None:
                try:
                    sink = bytearray(size)
                except MemoryError:
                    sink = None
            if sink is None:
                sink = open(dst + ".new", "wb")
        except Exception as exc:  # noqa: BLE001 -- a bad store is an answer
            print("moy-err cannot write %s: %s" % (rel, exc))
            return
        self._put = [rel, dst, sink, size, 0, None]
        print("moy-ok")
        self._moy_drain(ws)

    def _moy_drain(self, ws):
        """The rest of a `moy-put`, read straight off stdin to its `.`, the
        way `recv` reads a window: the tool streams the whole file's lines
        once it has `moy-ok`, with no flow control but the stdin ring's, so
        the reader has to keep up with the line -- a byte a poll does not on
        a P4, and the ring overflows while the store writes. moy_serial takes
        what has arrived in one call; where it is absent, a byte a poll. A
        stream that stops for RECV_IDLE_MS ends the put as short. Where stdin
        has no poll to wait on, the frame loop's reader takes the lines
        instead."""
        ipoll = self._ipoll
        rd = self._stdin.read if self._stdin is not None else None
        if _moy_serial is None and (ipoll is None or rd is None):
            return
        line = bytearray(SERIAL_LINE_MAX)
        n = 0
        blk = bytearray(512 if _moy_serial is not None else 1)
        while self._put is not None:
            if _moy_serial is not None:
                k = _moy_serial.readinto(blk, 0, 1, RECV_IDLE_MS)
                if k:
                    k = _moy_serial.readinto(blk, 1, len(blk), 0)
            else:
                ready = False
                for _ in ipoll(RECV_IDLE_MS):
                    ready = True
                ch = rd(1) if ready else ""
                k = 1 if ch else 0
                if k:
                    blk[0] = ord(ch) & 0xFF
            if not k:
                if self._put[5] is None:
                    self._put[5] = "the stream stopped after %d bytes" % self._put[4]
                self._moy_put_line(ws, ".")
                return
            self.rx += k
            for j in range(k):
                c = blk[j]
                if c == 10 or c == 13:
                    if n:
                        try:
                            text = bytes(line[:n]).decode().strip()
                        except UnicodeError:
                            text = None
                            if self._put[5] is None:
                                self._put[5] = "a line that is not base64"
                        n = 0
                        if text is not None:
                            self._moy_put_line(ws, text)
                        if self._put is None:
                            # Whatever followed the `.` is the line reader's.
                            if _loop is not None and j + 1 < k:
                                _loop.devch_unread(blk[j + 1:k])
                            return
                elif n < SERIAL_LINE_MAX:
                    line[n] = c
                    n += 1
                elif self._put[5] is None:
                    self._put[5] = "a line longer than %d" % SERIAL_LINE_MAX

    def _moy_put_line(self, ws, line):
        """One line of a `moy-put`: base64 to append, or `.` to finish."""
        put = self._put
        rel, dst, f, size, got, err = put
        held = isinstance(f, bytearray)
        if line != ".":
            if err is None:
                try:
                    import binascii
                    data = binascii.a2b_base64(line)
                    if not held:
                        f.write(data)
                    elif got + len(data) > size:
                        raise ValueError
                    else:
                        f[got:got + len(data)] = data
                    put[4] = got + len(data)
                except ValueError:
                    put[5] = "a line that is not base64"
                except Exception as exc:  # noqa: BLE001 -- a full store is an answer
                    put[5] = _recv_err(ws, exc, got, size)
            return
        self._put = None
        if held and err is None and got == size:
            try:
                out = open(dst + ".new", "wb")
                try:
                    out.write(f)
                finally:
                    out.close()
            except Exception as exc:  # noqa: BLE001 -- a full store is an answer
                err = _recv_err(ws, exc, 0, size)
        elif not held:
            try:
                f.close()
            except Exception as exc:  # noqa: BLE001 -- a close can be the write that fails
                if err is None:
                    err = _recv_err(ws, exc, got, size)
        if err is None and got != size:
            err = "%d of %d bytes arrived" % (got, size)
        import os
        if err is not None:
            try:
                os.remove(dst + ".new")
            except OSError:
                pass
            print("moy-err %s: %s" % (rel, err))
            say = getattr(ws, "notice", None)
            if err.startswith("store full") and say is not None:
                say("CAN'T ADD CART", "the store is full", "warn")
            return
        for gone in (dst, dst + ".bak"):
            # The stamped .bak describes the bytes it sat beside (moy_fs, #154);
            # left behind, it would "recover" them over what just arrived.
            try:
                os.remove(gone)
            except OSError:
                pass
        os.rename(dst + ".new", dst)
        self._pushed.add(rel.split("/")[0])
        print("moy-ok")

    def _moy_del(self, ws, parts):
        """`moy-del <path>`: a file, or a whole cart folder, from the store."""
        dst = _moy_path(ws, parts[1] if len(parts) == 2 else "")
        if dst is None:
            print("moy-err usage: moy-del <path in the cart store>")
            return
        try:
            _moy_remove(dst)
        except OSError as exc:
            print("moy-err cannot delete %s: %s" % (parts[1], exc))
            return
        print("moy-ok")

    def run(self, ws, line):
        parts = line.split()
        cmd = parts[0]
        if cmd == "quit":
            # A FLAG, not a raised KeyboardInterrupt. MicroPython derives that
            # one from BaseException, so it would sail past every `except
            # Exception` between here and the top -- including the frame's --
            # and leave the panel mid-flush. run_desktop returns cleanly instead.
            print("REMOTE quit -> REPL")
            self.quit = True
            return
        word = self.words.get(cmd)
        if word is not None and word(self, ws, parts, line) is not False:
            return
        if cmd == "state":
            import json
            print("STATE %s" % json.dumps(_remote_state(ws)))
            return
        if cmd == "run":
            # The lookup is ws.launch_named -- the SAME body the browser's PLAY
            # ON DEVICE goes through (moy_webhost POST /run), so a name that
            # works over serial works from the page and neither can grow its own
            # idea of what a cart is called.
            name = " ".join(parts[1:]) if len(parts) > 1 else ""
            title = ws.launch_named(name)
            if title:
                print("REMOTE run %s" % title)
            else:
                print("REMOTE run: no cart match")
            return
        if cmd == "diag":
            on = not (len(parts) == 2 and parts[1] == "0")
            # Through set_diag_live, not around it: the 3s diag tick re-syncs
            # perf_capture FROM diag_live, so poking perf_capture alone would be
            # silently undone. persist=False -- a serial A/B must not rewrite the
            # kid's system.json.
            try:
                ws.set_diag_live(on, persist=False)
            except Exception:  # noqa: BLE001 -- older console: flag only
                ws.diag_live = on
            ws.show_fps = on
            ws._dirty = True
            print("REMOTE diag %s" % ("on" if on else "off"))
            return
        if cmd == "verbs":
            self._verbs(parts)
            return
        if cmd == "luaprof":
            self._luaprof(ws, parts)
            return
        if cmd == "perfcnt":
            self._perfcnt(parts)
            return
        if cmd == "luagc":
            self._luagc(parts)
            return
        tog = _toggle_cmd(cmd)
        if tog is not None:
            # A settings toggle by its registry word (`skip`, `crisp`). The
            # capability gate is EXPRESSED here too: a board that cannot serve
            # the setting declines the word instead of flipping a flag nothing
            # reads. persist=False -- a serial A/B must never rewrite the kid's
            # system.json -- and the reported state is the one the console
            # actually reached, not the one that was asked for.
            key, _label, _default, setter, gate, _dev = tog
            if gate is not None and not gate(ws):
                print("REMOTE %s: not available on this board" % cmd)
                return
            on = not (len(parts) == 2 and parts[1] == "0")
            getattr(ws, setter)(on, persist=False)
            print("REMOTE %s %s"
                  % (cmd, "on" if getattr(ws, key, on) else "off"))
            return
        if cmd == "uncap":
            on = not (len(parts) == 2 and parts[1] == "0")
            ws._uncap = on                    # the next run starts uncapped
            pl = getattr(ws, "player", None)
            if pl is not None and getattr(pl, "tick_ms", 0):
                pl.uncap_mode(on)             # ...and the running one flips now
            print("REMOTE uncap %s" % ("on" if on else "off"))
            return
        if cmd in ("skip", "gov"):
            print("REMOTE %s: retired by the tick model (#217) -- the Player "
                  "schedules logic and draw; `steady 0|1` is the knob" % cmd)
            return
        if cmd == "kcrash":
            # DEV, the kernel's crash record end to end (#224): `kcrash [id]
            # [fault|abort]` arms `id` (default kernel-test) in the app strike
            # ledger, then faults the board. After the reboot `state`'s
            # `crash` names the id and the cause, and the notice is up;
            # `py ws.app_guard.forgive('<id>')` drops the strike it took.
            try:
                import moy_crash
            except ImportError:
                print("REMOTE kcrash: no kernel on this board")
                return
            ws.app_guard.arm(parts[1] if len(parts) > 1 else "kernel-test")
            moy_crash.panic(parts[2] if len(parts) > 2 else "fault")
            return
        if cmd == "khang":
            # DEV, the task watchdog end to end (#160/#224): `khang [id]` arms
            # `id` in the app strike ledger, then never ends the frame. The
            # kernel's task watchdog panics the board once no frame has fed it
            # for its timeout; after the reboot `state`'s `crash` is a `task_wdt`
            # naming the id, and `py ws.app_guard.forgive('<id>')` drops the
            # strike it took.
            try:
                import moy_kernel
            except ImportError:
                print("REMOTE khang: no kernel on this board")
                return
            armed = moy_kernel.watchdog()
            if not armed[0]:
                print("REMOTE khang: the console's loop has not armed the watchdog")
                return
            ws.app_guard.arm(parts[1] if len(parts) > 1 else "kernel-test")
            print("REMOTE khang: hanging, the watchdog fires in %ds" % (armed[1] // 1000))
            while True:
                pass
        if cmd == "kfail":
            # DEV, the recovery floor (#224): `kfail [vm_start|heap]` restarts
            # the board with that VM start failure armed for one boot, which
            # lands on the recovery screen.
            try:
                import moy_kernel
            except ImportError:
                print("REMOTE kfail: no kernel on this board")
                return
            moy_kernel.test(parts[1] if len(parts) > 1 else "vm_start")
            return
        if cmd == "kstop":
            # DEV, the kernel's teardown guard (#224, docs/kernel_survival_2026-10.md
            # §7.5): `kstop N` runs the VM service's soft reset N times with the
            # kernel's drivers alive, the console booting between, and prints a
            # KSTOP line of the heaps before and after each teardown.
            try:
                import moy_kernel
                kstop = moy_kernel.kstop
            except (ImportError, AttributeError):
                print("REMOTE kstop: no kernel on this board")
                return
            n = int(parts[1]) if len(parts) > 1 else 1
            print("REMOTE kstop %d" % n)
            kstop(n)
            return
        if cmd == "kstale":
            # DEV, the spine's handle gate (#224): see stale_handle_probe.
            print(stale_handle_probe(ws))
            return
        if cmd == "heapcaps":
            import gc
            try:
                import esp32
            except ImportError:
                esp32 = None
            print(heapcaps_line(esp32, gc))
            return
        if cmd == "mem":
            import gc
            gc.collect()
            print("REMOTE mem live=%dk free=%dk"
                  % (gc.mem_alloc() // 1024, gc.mem_free() // 1024))
            return
        if cmd == "open":
            # Deterministic app open (no tile-hunting), so a drag/scroll can be
            # measured against a known surface. `open appearance` reports
            # open_app's claim result -- a False is the silent no-op an on-glass
            # harness needs to SEE; `open wifi` deep-links Settings -> wifi.
            what = parts[1] if len(parts) > 1 else ""
            if what == "appearance" and getattr(ws, "appearance_app", None) is not None:
                ok = ws.open_app(ws.appearance_app)
                print("REMOTE open appearance -> %s" % ok)
                return
            if what == "wifi" and hasattr(ws, "open_settings"):
                ws.open_settings()
                ws.settings_layer.open_wifi()
                print("REMOTE open wifi")
                return
            fn = {"settings": getattr(ws, "open_settings", None),
                  "picker": getattr(ws, "open_picker", None)}.get(what)
            if fn is not None:
                fn()
                print("REMOTE open %s" % what)
            else:
                print("REMOTE open ? %s" % line)
            return
        if cmd == "py" and len(parts) > 1:
            code = line.split(None, 1)[1]
            env = {"ws": ws, "wm": ws.wm, "pointer": self.pointer, "chan": self}
            env.update(self.env)
            try:
                try:
                    print("PY %r" % (eval(code, env),))
                except SyntaxError:
                    exec(code, env)       # noqa: S102 -- dev-board serial only
                    print("PY ok")
            except Exception as exc:  # noqa: BLE001
                print("PY ERR %s: %s" % (type(exc).__name__, exc))
            return
        handler = self.extra.get(cmd)
        if handler is not None:
            handler(ws, parts, line)
            return
        print("REMOTE ? %s" % line)

