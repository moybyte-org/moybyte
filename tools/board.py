#!/usr/bin/env python3
"""Drive an attached board by name: the on-glass loop, one command per step.

    tools/board.py ports                        which port is which board, who holds it
    tools/board.py tdeck port                   its port, for MOYBYTE_TDECK_PORT=... (opens nothing)
    tools/board.py tdeck state                  what the console is doing (--json: all of it)
    tools/board.py p4 py "ws.screen" "len(ws.carts.all)"
    tools/board.py tdeck run "Brick Siege"      start a cart; it stays running
    tools/board.py tdeck leave                  end the cart, put the desk back
    tools/board.py tdeck desk                   back to the launcher/desk from anywhere
    tools/board.py tdeck perf "Brick Siege"     median drawn fps; --diag adds phase ms
    tools/board.py tdeck push ports/x.moy       copy a cart folder to the store, rescan;
                                                a compiled cart gets its chip's module
    tools/board.py tdeck shot /tmp/tdeck.png    the glass as a PNG (--source game: the cart)
    tools/board.py tdeck pmem [--text]          the running cart's 256 pmem cells
    tools/board.py tdeck mem                    python heap, internal SRAM, PSRAM
    tools/board.py tdeck tap X Y | swipe X0 Y0 X1 Y1 [FRAMES] | open settings
    tools/board.py tdeck tail 10 [--send CMD]   print what the board says for 10 s
    tools/board.py tdeck flash [--build]        cable-flash its image, wait for the desk
    tools/board.py tdeck reboot [--soft]        reset it the way it allows, wait for the desk
    tools/board.py tdeck wait                   until the desk answers

The board is its `[board] ota` id (p4, tdeck, guition_s3, guition_p4,
xiao_zero), the same name `--board` takes everywhere else. Everything goes
through `tools/p4_autotest.py`'s `P4Board`, which opens each port with the
line state its board.toml `[serial]` block declares.

WHICH PORT, WITHOUT OPENING ONE. The ttyACM numbers shuffle across resets and
four boards share usb id 303a:1001, so a port is resolved from facts that do
not need the port opened: an explicit --port; the USB serial number (the
chip's MAC on the SoC-USB boards, read from sysfs) matched against the Zero's
board.toml or against this machine's learned identities; or a usb id only one
board declares (the Waveshare P4's CH343). The learned identities are a file
outside the repo (`~/.config/moybyte/boards.json`, or MOYBYTE_BOARDS_FILE),
because which MAC is which board is a fact about this desk. A board that
answers its identity on a port is recorded there, so one `--port` teaches it
for good; `ports --probe` asks every unclaimed, unheld port once.

A PORT ANOTHER PROCESS HOLDS IS REFUSED, naming the holder. Two readers on one
tty split its bytes, and a second open steals replies from whatever suite or
agent opened it first.

LEAVING THE DESK. `perf` ends its cart and restores diag and uncap; `push`
changes the store and rescans it; `shot`, `pmem`, `state` and `mem` change
nothing. `run`, `tap`, `swipe` and `open` change the console on purpose, and
`desk` undoes them.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import p4_autotest as pa                                           # noqa: E402
from p4_autotest import P4Board                                    # noqa: E402

BY_ID = "/dev/serial/by-id"
# Boards with a dev channel to talk to. The Zero is headless and speaks a bare
# REPL, and the web runner has no serial at all.
CONSOLE_TIERS = ("handheld", "desktop")
# A console that has not answered `state` by then is not booting any more.
WAIT_S = 120.0


class BoardError(RuntimeError):
    """A step that cannot go on, in words a person can act on."""


# -- the boards and their declarations ---------------------------------------


def boards(root=ROOT):
    """{ota id: board dir} for every flashable board in the tree."""
    return pa.board_dirs(root)


def board_file(board_dir):
    import board_config
    return board_config.load(board_dir)


def is_console(board_dir):
    return board_file(board_dir).get("board", {}).get("tier") in CONSOLE_TIERS


# -- which port: facts that need no open --------------------------------------


identities_path = pa.identities_path
load_identities = pa.load_identities


def learn(serial, board):
    """Record that USB serial `serial` is `board`. Atomic, so two tools
    learning at once leave one of their answers rather than half of each."""
    if not serial:
        return
    known = load_identities()
    if known.get(serial) == board:
        return
    known[serial] = board
    path = identities_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(known, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def by_id_names(by_id=BY_ID):
    """{/dev/ttyXXX: its /dev/serial/by-id path}."""
    try:
        names = os.listdir(by_id)
    except OSError:
        return {}
    return {os.path.realpath(os.path.join(by_id, n)): os.path.join(by_id, n)
            for n in names}


def holders(port, proc="/proc"):
    """[(pid, command line)] of every other process with `port` open."""
    real = os.path.realpath(port)
    out = []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        if int(pid) == os.getpid():
            continue
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) != real:
                    continue
            except OSError:
                continue
            try:
                with open(os.path.join(proc, pid, "cmdline"), "rb") as f:
                    cmd = f.read().replace(b"\0", b" ").decode(
                        "utf-8", "replace").strip()
            except OSError:
                cmd = "?"
            out.append((int(pid), cmd))
            break
    return out


def port_table(ports=None, usb_of=None, serial_of=None):
    """[(port, usb id, usb serial)] for every serial port on the machine."""
    usb_of = usb_of or pa.usb_id_of
    serial_of = serial_of or pa.usb_serial_of
    ports = pa.serial_ports() if ports is None else ports
    return [(p, usb_of(p), serial_of(p)) for p in ports]


def claim(table, dirs, known):
    """{port: (board, how)} for every port a fact settles without an open."""
    declared = {name: pa.declared_serial(d) for name, d in dirs.items()}
    out = {}
    for port, usb, serial in table:
        for name, ser in declared.items():
            if serial and ser.get("serial_number") == serial:
                out[port] = (name, "board.toml serial")
        if port not in out and serial in known:
            out[port] = (known[serial], "learned serial")
    for name, ser in declared.items():
        usb = ser.get("usb")
        if not usb or name in {b for b, _ in out.values()}:
            continue
        twins = [n for n, s in declared.items() if s.get("usb") == usb]
        hits = [p for p, u, _s in table if u == usb and p not in out]
        if len(twins) == 1 and len(hits) == 1:
            out[hits[0]] = (name, "only %s" % usb)
    return out


def resolve(name, dirs, table=None, known=None):
    """The port `name` is on, and how that is known. Raises BoardError with
    the candidates and what to do when nothing settles it."""
    table = port_table() if table is None else table
    known = load_identities() if known is None else known
    claimed = claim(table, dirs, known)
    mine = [p for p, (b, _how) in claimed.items() if b == name]
    if len(mine) == 1:
        return mine[0], claimed[mine[0]][1]
    if len(mine) > 1:
        raise BoardError("%s is claimed by %s -- pass --port"
                         % (name, ", ".join(sorted(mine))))
    usb = pa.declared_serial(dirs[name]).get("usb")
    loose = [p for p, u, _s in table if u == usb and p not in claimed]
    if not loose:
        raise BoardError("no port looks like %s (usb %s) -- is it plugged in? "
                         "`tools/board.py ports` lists what is" % (name, usb))
    raise BoardError(
        "%s is one of %s (usb %s), and no serial number on this machine is "
        "known to be it. Name the port once -- `tools/board.py %s --port "
        "<port> state` -- and it is remembered; or `tools/board.py ports "
        "--probe` asks each unclaimed port." % (name, ", ".join(loose), usb, name))


# -- opening a board ----------------------------------------------------------


def refuse_if_held(port):
    held = holders(port)
    if held:
        raise BoardError(
            "%s is open in another process -- a second reader steals its "
            "replies:\n%s\n(--force opens it anyway)"
            % (port, "\n".join("  pid %d: %s" % (pid, cmd[:160])
                               for pid, cmd in held)))


def attach(name, dirs, port=None, force=False, log=None):
    """A P4Board on `name`'s port, identity checked unless its serial number
    already said which board it is; a verified identity is learned."""
    how = "--port"
    if port is None:
        port, how = resolve(name, dirs)
    if not force:
        refuse_if_held(port)
    b = P4Board(port, board_dir=dirs[name], log=log)
    b.drain(0.4)
    if how.endswith("serial"):
        return b
    # What the board says it is, learned whichever board it turns out to be.
    got = b.identify()
    if got in dirs:
        learn(pa.usb_serial_of(port), got)
    if got is not None and got != name:
        b.close()
        raise BoardError("%s answers as %s, not %s -- `tools/board.py ports` "
                         "shows where each board is" % (port, got, name))
    return b


def stable(port):
    """The /dev/serial/by-id name of `port` where there is one: it follows the
    board across the renumbering a reset or a flash causes."""
    return by_id_names().get(os.path.realpath(port), port)


def state_or_none(b, timeout=8.0):
    try:
        return b.state(timeout=timeout)
    except (RuntimeError, ValueError):
        return None


NOT_RUNNING = ("%s did not answer: its desktop is not running (booting, or "
               "at the REPL after `quit`). `tools/board.py %s reboot` resets "
               "it and waits; `reboot --soft` when it sits at >>>.")


def need_desk(b, name):
    """The state snapshot, or the way back when the console is not there."""
    st = state_or_none(b)
    if st is None:
        raise BoardError(NOT_RUNNING % (name, name))
    return st


def need_console(b, name):
    """One short round trip: the `state` line is kilobytes, which the P4's
    115200-baud UART takes a quarter of a second to carry."""
    if b.pyval("1", timeout=8.0) != 1:
        raise BoardError(NOT_RUNNING % (name, name))


# -- verbs --------------------------------------------------------------------


SUMMARY = ("stack", "screen", "cart", "cart_error", "notice", "desk", "order",
           "diag", "uncap", "wifi_held", "frames")


def cmd_state(b, a):
    st = need_desk(b, a.board)
    if a.json:
        print(json.dumps(st, indent=1, sort_keys=True))
        return 0
    keys = a.keys.split(",") if a.keys else [k for k in SUMMARY if k in st]
    for k in keys:
        print("%-11s %s" % (k, json.dumps(st.get(k))))
    return 0


def cmd_py(b, a):
    need_console(b, a.board)
    if a.file:
        with open(a.file) as f:
            ok = b.pyexec(f.read(), timeout=a.timeout)
        print("ok" if ok else "ERR %s" % b.last_error)
        if not ok:
            return 1
    status = 0
    for expr in a.exprs:
        if a.exec:
            ok = b.pyexec(expr, timeout=a.timeout)
            print("%s -> %s" % (expr[:80], "ok" if ok else "ERR %s" % b.last_error))
            status |= 0 if ok else 1
            continue
        val = b.pyval(expr, timeout=a.timeout)
        if b.last_error:
            print("%s -> ERR %s" % (expr[:80], b.last_error))
            status = 1
        else:
            print("%s -> %r" % (expr[:80], val))
    return status


def cmd_run(b, a):
    need_desk(b, a.board)
    line = b.cmd("run %s" % a.title, wait_for="REMOTE run", timeout=20.0)
    if line is None or "no cart match" in line:
        print("no cart matches %r -- `py \"[c['title'] for c in ws.carts.all]\"` "
              "lists the shelf; a cart pushed without a rescan is not on it"
              % a.title)
        return 1
    b.drain(a.settle)
    st = b.state()
    print("running %s  stack=%s" % (st.get("cart"), st.get("stack")))
    if st.get("cart_error") or st.get("notice"):
        print("  error: %s" % (st.get("cart_error") or st.get("notice")))
        return 1
    return 0


def cmd_leave(b, a):
    need_desk(b, a.board)
    b.leave_cart()
    st = b.state()
    print("stack=%s desk=%s" % (st.get("stack"), st.get("desk")))
    return 0


def cmd_desk(b, a):
    """Back to where a board boots: no cart, the launcher on top, and on the
    windowed tier the desk open."""
    st = need_desk(b, a.board)
    if st.get("cart"):
        b.leave_cart()
        st = b.state()
    stack = st.get("stack") or []
    if stack and stack[-1] not in ("launcher", "desk"):
        b.pyval("ws.go_home() or 1", timeout=20)
        b.drain(0.5)
        st = b.state()
    if st.get("desk") is False:
        b.pyexec("ws.open_desk()")
        b.drain(0.5)
        st = b.state()
    print("stack=%s desk=%s order=%s"
          % (st.get("stack"), st.get("desk"), st.get("order")))
    return 0


def cmd_perf(b, a):
    import p4_perf
    st = need_desk(b, a.board)
    diag_was = bool(st.get("diag"))
    # `state`'s uncap is the RUNNING cart's; the switch the next run takes is
    # `ws._uncap`, which is what `uncap` sets.
    uncap_was = bool(b.pyval("getattr(ws, '_uncap', False)", timeout=10))
    held = st.get("wifi_held") or []
    status = 0
    try:
        # Off for the shipping fps, which p4_perf reads off the drawn-frame
        # counter; on for --diag, whose phases only the PERF line carries.
        b.cmd("diag %d" % (1 if a.diag else 0), wait_for="REMOTE diag")
        if a.uncap:
            b.cmd("uncap 1", wait_for="REMOTE uncap")
        for title in a.titles:
            try:
                r = p4_perf.measure(b, title, a.secs, lambda *x: None,
                                    diag=a.diag)
            except RuntimeError as exc:
                print("%s: ERROR %s" % (title, exc))
                status = 1
                continue
            if r is None:
                print("%s: not on this board's shelf" % title)
                status = 1
                continue
            print("%s on %s: %.1f fps drawn (median of %d, worst %.0f)%s%s"
                  % (r["title"], a.board, r["fps"], r["n"], r["min"],
                     ("  draw/flush/logic/render/chrome %s ms"
                      % p4_perf.phase_text(r["phases"])) if a.diag else "",
                     ("  LINKED net=%.0f" % r["linked"])
                     if r["linked"] is not None else ""))
    finally:
        b.leave_cart()
        if a.uncap and not uncap_was:
            b.cmd("uncap 0", wait_for="REMOTE uncap")
        if bool(a.diag) != diag_was:
            b.cmd("diag %d" % diag_was, wait_for="REMOTE diag")
    print("  (diag %s, uncap %s, wifi %s)"
          % ("on" if a.diag else "off", "on" if a.uncap else "off",
             "held by %s" % ",".join(held) if held else "off"))
    return status


PMEM = ("(lambda p: (getattr(getattr(p, '_lua', None), 'flush_pmem', "
        "lambda: 0)(), list(ws.pmem.cells) if getattr(ws, 'pmem', None) "
        "is not None else None)[1])(getattr(ws, 'player', None))")


def pmem_text(cells):
    """The cells as the bytes a compiled cart packs into them: little-endian,
    four to a cell, up to the first NUL."""
    raw = b"".join((c & 0xFFFFFFFF).to_bytes(4, "little") for c in cells)
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def cmd_pmem(b, a):
    need_console(b, a.board)
    cells = b.pyval(PMEM, timeout=20)
    if cells is None:
        print("no pmem: %s" % (b.last_error or "no cart has one open"))
        return 1
    if a.text:
        print(pmem_text(cells[a.start:]))
        return 0
    last = max([i for i, c in enumerate(cells) if c] or [-1])
    for i in range(0, last + 1, 8):
        print("%3d: %s" % (i, " ".join("%11d" % c for c in cells[i:i + 8])))
    if last < 0:
        print("all 256 cells are 0")
    return 0


MEM = ("(lambda gc, e: (gc.collect(), (gc.mem_free(), gc.mem_alloc(), "
       "[(h[1], h[2]) for h in e.idf_heap_info(0x800)], "
       "[(h[1], h[2]) for h in e.idf_heap_info(0x400)]))[1])"
       "(__import__('gc'), __import__('esp32'))")


def cmd_mem(b, a):
    need_console(b, a.board)
    got = b.pyval(MEM, timeout=20)
    if got is None:
        print("ERR %s" % b.last_error)
        return 1
    free, alloc, sram, psram = got

    def heaps(hs):
        return "free %dK, largest %dK" % (sum(h[0] for h in hs) // 1024,
                                          max([h[1] for h in hs] or [0]) // 1024)
    print("python heap   free %dK, used %dK" % (free // 1024, alloc // 1024))
    print("internal SRAM %s" % heaps(sram))
    print("PSRAM         %s" % heaps(psram))
    return 0


def cmd_tap(b, a):
    need_console(b, a.board)
    b.tap(a.x, a.y)
    print("tapped %d,%d  stack=%s" % (a.x, a.y, b.state().get("stack")))
    return 0


def cmd_swipe(b, a):
    need_console(b, a.board)
    b.swipe(a.x0, a.y0, a.x1, a.y1, frames=a.frames)
    print("swiped  stack=%s" % b.state().get("stack"))
    return 0


def cmd_open(b, a):
    need_console(b, a.board)
    print(b.open(a.what))
    return 0


def cmd_tail(b, a):
    n0 = len(b.lines)
    if a.send:
        b._write_line(a.send)
    end = time.time() + a.secs
    shown = n0
    while time.time() < end:
        b.drain(0.2)
        for line in b.lines[shown:]:
            if not a.grep or a.grep in line:
                print(line)
        shown = len(b.lines)
    return 0


# -- the picture on the glass -------------------------------------------------

# Uploaded once per shot (P4Board.pyexec, the persistent `ws._g` namespace).
# `_shot_open` takes the frame in ONE command, between two frames, so the
# picture is consistent; `_shot_next` hands it over a band at a time, deflated
# with a 256-byte window -- MicroPython's compressor searches its whole window
# for every byte, so a 4 KB window costs sixteen times as long for little
# more on a flat UI.
#
# "screen" is the buffer the panel last took: the ping-pong/triple buffers on
# every board (`comp._fbs`, the one before `_back`, or `_front` where the
# compositor keeps it). On the Guition P4 that buffer is the portrait scan
# buffer, and the host turns it back by the compositor's angle. "game" is the
# game canvas the cart draws into, cropped to its view.
SHOT_HELPERS = """
def _shot_open(comp, src, z, band):
    import device_canvas as dc
    le = dc.PAL565_WIRE[1] == dc.PAL565[1]
    angle = 0
    ox = 0
    oy = 0
    if src == 'game':
        c = ws.canvas
        buf = c._buf
        stride = getattr(c, '_stride', None) or c.w
        ox = getattr(c, '_ox', 0)
        oy = getattr(c, '_oy', 0)
        w = c.w
        h = c.h
    else:
        fbs = comp._fbs
        k = len(fbs)
        i = getattr(comp, '_front', None)
        if i is None:
            q = len(getattr(comp, '_pend3', None) or ())
            i = (comp._back - 1 - q) % k if k > 1 else 0
        buf = fbs[i]
        pw = getattr(comp, '_pw', None)
        w = pw or comp._w
        h = getattr(comp, '_ph', None) or comp._h
        stride = w
        if pw:
            angle = comp.angle
    mv = memoryview(buf)
    n = w * h * 2
    if stride == w and ox == 0 and oy == 0:
        try:
            pic = bytes(mv[:n])
        except MemoryError:
            pic = mv[:n]
    else:
        pic = bytearray(n)
        row = w * 2
        for r in range(h):
            a = ((oy + r) * stride + ox) * 2
            pic[r * row:(r + 1) * row] = mv[a:a + row]
    st = {'pic': pic, 'pos': 0, 'n': n, 'band': band, 'out': None,
          'z': None, 'sent': 0}
    if z:
        import io
        import deflate
        st['out'] = io.BytesIO()
        st['z'] = deflate.DeflateIO(st['out'], deflate.ZLIB, 8)
    ws._g['_shot'] = st
    return (w, h, le, angle, n)


def _shot_next():
    import binascii
    st = ws._g['_shot']
    a = st['pos']
    e = min(st['n'], a + st['band'])
    piece = st['pic'][a:e]
    st['pos'] = e
    z = st['z']
    if z is None:
        out = piece
    else:
        z.write(piece)
        if e >= st['n']:
            z.close()
        o = st['out']
        o.seek(st['sent'])
        out = o.read()
        st['sent'] += len(out)
    done = e >= st['n']
    if done:
        ws._g['_shot'] = None
    return (done, binascii.b2a_base64(out))
"""

SHOT_BAND = 32768


def rgb565_lut():
    """RGB565 word -> its 3 bytes of RGB888."""
    out = []
    for v in range(65536):
        r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
        out.append(bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4),
                          (bl << 3) | (bl >> 2))))
    return out


def words(raw, little_endian):
    """The frame's pixels as RGB565 words, whatever order the board wrote."""
    px = array("H")
    px.frombytes(raw)
    if little_endian != (sys.byteorder == "little"):
        px.byteswap()
    return px


def unrotate(px, pw, ph, angle):
    """A portrait scan buffer (pw x ph) back to the landscape picture the
    console painted, inverting `device/dsi_panel.rotate_rect` pixel by pixel.
    Returns (words, w, h)."""
    lw, lh = ph, pw
    out = array("H", bytes(2 * lw * lh))
    for y in range(lh):
        row = y * lw
        if angle == 90:         # landscape (x, y) -> portrait (y, lw - 1 - x)
            for x in range(lw):
                out[row + x] = px[(lw - 1 - x) * pw + y]
        elif angle == 270:      # landscape (x, y) -> portrait (lh - 1 - y, x)
            for x in range(lw):
                out[row + x] = px[x * pw + (lh - 1 - y)]
        else:
            raise ValueError("angle 90 or 270")
    return out, lw, lh


def png_of(px, w, h):
    sys.path.insert(0, ROOT)
    from tools import pngwrite
    lut = rgb565_lut()
    rows = [b"".join(map(lut.__getitem__, px[y * w:(y + 1) * w]))
            for y in range(h)]
    return pngwrite.png_bytes(rows, w, h)


def grab(b, source="screen", deflate=True, band=SHOT_BAND):
    """(words, w, h) of the picture on `b`, landscape as the console paints."""
    import zlib
    if not b.pyexec(SHOT_HELPERS, timeout=30):
        raise BoardError("could not install the shot helpers: %s" % b.last_error)
    got = b.pyval("ws._g['_shot_open'](comp, %r, %d, %d)"
                  % (source, 1 if deflate else 0, band), timeout=60)
    if got is None:
        raise BoardError("the board would not take the frame: %s" % b.last_error)
    w, h, le, angle, n = got
    parts = []
    done = False
    while not done:
        got = b.pyval("ws._g['_shot_next']()", timeout=60)
        if got is None:
            raise BoardError("the frame stopped halfway: %s" % b.last_error)
        done, chunk = got
        parts.append(base64.b64decode(chunk))
    data = b"".join(parts)
    raw = zlib.decompress(data) if deflate else data
    if len(raw) != n:
        raise BoardError("got %d bytes of a %d-byte frame" % (len(raw), n))
    px = words(raw, le)
    if angle:
        return unrotate(px, w, h, angle)
    return px, w, h


def cmd_shot(b, a):
    need_console(b, a.board)
    t0 = time.time()
    px, w, h = grab(b, a.source, deflate=not a.raw)
    with open(a.out, "wb") as f:
        f.write(png_of(px, w, h))
    print("%s  %dx%d  %s  %.1fs" % (a.out, w, h, a.source, time.time() - t0))
    return 0


# -- the ports ----------------------------------------------------------------


def cmd_ports(a, dirs):
    for pair in a.remember or ():
        serial, _, name = pair.rpartition("=")
        if name not in dirs or not serial:
            raise BoardError("--remember takes SERIAL=BOARD, BOARD one of %s"
                             % ", ".join(sorted(dirs)))
        learn(serial, name)
    table = port_table()
    known = load_identities()
    if a.probe:
        _probe(table, dirs, known)
        known = load_identities()
    claimed = claim(table, dirs, known)
    if not table:
        print("no serial ports -- is a board plugged in?")
        return 1
    for port, usb, serial in table:
        board, how = claimed.get(port, ("?", "unclaimed"))
        held = holders(port)
        print("%-13s %-11s %-10s %-19s %s"
              % (port, board, usb or "?", serial or "?", how))
        for pid, cmd in held:
            print("%13s held by pid %d: %s" % ("", pid, cmd[:110]))
    missing = sorted(set(dirs) - {b for b, _ in claimed.values()})
    if missing:
        print("not claimed: %s" % ", ".join(missing))
    return 0


def _probe(table, dirs, known):
    """Ask each unclaimed, unheld port who it is, once, and learn the answer.
    Only ports whose usb id an attach-only board declares are asked: an open
    there is side-effect free, where a CH343 open can reset its board."""
    claimed = claim(table, dirs, known)
    for port, usb, serial in table:
        if port in claimed or holders(port):
            continue
        asker = [d for d in dirs.values()
                 if pa.declared_serial(d).get("usb") == usb
                 and pa.declared_serial(d).get("attach_only")]
        if not asker:
            continue
        got = pa._probe_identity(port, asker[0], lambda s: None)
        print("probed %s -> %s" % (port, got or "no answer"))
        if got in dirs:
            learn(serial, got)


# -- reset, flash, wait -------------------------------------------------------


# A board just reset is not written to until it has printed a line, or until
# this long has passed in silence (a board whose boot is already over).
HEAR_S = 10.0


def wait_for_desk(name, dirs, port=None, timeout=WAIT_S, quiet=False,
                  reset=False):
    """Poll until `name` answers `state`, re-resolving the port each time (a
    reset can move it, and on some boards replaces the node under a handle
    held across it). Returns the state.

    A board just `reset` is not written to until it has said something. A
    USB-Serial/JTAG console that gets a line before its boot has started
    reading holds it in the endpoint, and a host that closes and reopens the
    port meanwhile lands its line-state request in the board's stdin -- 0x03
    among it, Ctrl-C to the boot (tools/patch_usj_rx_init.py fixes the board
    and has the measurement; this keeps an older image whole too). Opening
    and closing alone is harmless, so the polls go on as before, silent."""
    end = time.time() + timeout
    last = None
    heard = not reset
    t0 = time.time()
    while time.time() < end:
        try:
            p = port if port and os.path.exists(port) else resolve(name, dirs)[0]
            b = P4Board(p, board_dir=dirs[name])
            try:
                got = b.drain(0.5 if heard else 2.0)
                heard = heard or bool(got) or time.time() - t0 > HEAR_S
                st = state_or_none(b, timeout=4.0) if heard else None
                at_repl = any(ln.startswith(">>>") or "isn't defined" in ln
                              for ln in b.lines[-8:])
            finally:
                b.close()
            if st is not None:
                if not quiet:
                    print("%s answers on %s  stack=%s" % (name, p, st.get("stack")))
                return st
            last = ("it sits at the REPL -- `tools/board.py %s reboot --soft`"
                    % name if at_repl else
                    "no state reply" if heard else "it has said nothing yet")
        except Exception as exc:  # noqa: BLE001 -- booting: the port may not exist yet
            last = str(exc).splitlines()[0]
        time.sleep(3.0 if heard else 0.5)
    raise BoardError("%s did not answer within %.0fs (%s)" % (name, timeout, last))


def esptool_reset(name, dirs, port):
    """Reset through the ROM loader: esptool drives the SoC's USB-JTAG, which
    an attach-only board survives (the port node stays; never a line pulse
    under an open handle there). The entry is the board's own `[flash]
    before`, which on the T-Deck is `usb_reset`."""
    before = board_file(dirs[name]).get("flash", {}).get("before")
    cmd = [sys.executable, "-m", "esptool", "--port", port]
    if before:
        cmd += ["--before", before]
    cmd += ["--after", "hard_reset", "read_mac"]
    if subprocess.call(cmd, stdout=subprocess.DEVNULL) != 0:
        raise BoardError("esptool could not reach %s on %s" % (name, port))


def cmd_reboot(name, dirs, a):
    port = stable(a.port or resolve(name, dirs)[0])
    if not a.force:
        refuse_if_held(port)
    decl = pa.declared_serial(dirs[name])
    if a.soft:
        # Ctrl-C to reach >>>, then Ctrl-D: a soft reset re-runs main.py without
        # re-enumerating USB. Sent before >>> appears, the Ctrl-D is swallowed.
        b = P4Board(port, board_dir=dirs[name])
        try:
            b.ser.write(b"\r\x03")
            b.wait_line(">>>", timeout=3.0)
            b.ser.write(b"\x04")
            b.drain(1.0)
        finally:
            b.close()
    elif decl.get("attach_only"):
        esptool_reset(name, dirs, port)
    else:
        b = P4Board(port, board_dir=dirs[name])
        try:
            b.reset()
        finally:
            b.close()
    wait_for_desk(name, dirs, port, a.timeout,
                  reset=bool(decl.get("attach_only")) and not a.soft)
    return 0


def cmd_flash(name, dirs, a):
    d = dirs[name]
    if a.build:
        if subprocess.call(["bash", os.path.join(d, "build.sh")], cwd=ROOT) != 0:
            raise BoardError("the %s build failed" % name)
    port = stable(a.port or resolve(name, dirs)[0])
    if not a.force:
        refuse_if_held(port)
    if subprocess.call([sys.executable, os.path.join(HERE, "board_flash.py"),
                        "flash", d, "--port", port], cwd=ROOT) != 0:
        raise BoardError("the flash of %s failed" % name)
    if not is_console(d):
        return 0
    try:
        wait_for_desk(name, dirs, port, a.timeout / 2, reset=True)
    except BoardError:
        # A board can sit in the loader after write_flash's own reset; one
        # reset through esptool starts the image just written.
        print("no desk yet -- one reset through esptool, then waiting again")
        esptool_reset(name, dirs, port)
        wait_for_desk(name, dirs, port, a.timeout / 2, reset=True)
    return 0


# -- the command line ---------------------------------------------------------


ON_THE_CONSOLE = {"state": cmd_state, "py": cmd_py, "run": cmd_run,
                  "leave": cmd_leave, "desk": cmd_desk, "perf": cmd_perf,
                  "pmem": cmd_pmem, "mem": cmd_mem, "tap": cmd_tap,
                  "swipe": cmd_swipe, "open": cmd_open, "tail": cmd_tail,
                  "shot": cmd_shot}


def _common(ap, default):
    """The options every verb takes, wherever they are typed. The copies on
    the verbs default to SUPPRESS so they cannot overwrite a value given
    before the verb."""
    ap.add_argument("--port", default=default(None),
                    help="the serial port, when the machine cannot tell (it "
                         "is learned once the board answers on it)")
    ap.add_argument("--force", action="store_true", default=default(False),
                    help="open the port even when another process holds it")
    ap.add_argument("-v", "--verbose", action="store_true",
                    default=default(False), help="echo every serial line")


def parser(dirs):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        epilog="`tools/board.py BOARD VERB -h` for a verb's options.")
    ap.add_argument("board", choices=sorted(dirs) + ["ports"],
                    help="the board's [board] ota id, or `ports`")
    _common(ap, lambda v: v)
    ap.add_argument("--probe", action="store_true",
                    help="(ports) ask each unclaimed port who it is, once")
    ap.add_argument("--remember", action="append", metavar="SERIAL=BOARD",
                    help="(ports) record which board a USB serial number is, "
                         "without opening anything")
    common = argparse.ArgumentParser(add_help=False)
    _common(common, lambda v: argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="verb", parser_class=argparse.ArgumentParser)
    add = sub.add_parser

    def verb(name, **kw):
        return add(name, parents=[common], **kw)

    verb("port", help="print its port (the by-id name); opens nothing")
    p = verb("state", help="the console's state")
    p.add_argument("--json", action="store_true", help="every field")
    p.add_argument("--keys", help="comma-separated fields to print")
    p = verb("py", help="evaluate expressions on the console")
    p.add_argument("exprs", nargs="*")
    p.add_argument("--exec", action="store_true",
                   help="run each as statements (the shared ws._g namespace)")
    p.add_argument("--file", help="a snippet to run first, as statements")
    p.add_argument("--timeout", type=float, default=30.0)
    p = verb("run", help="start a cart by title; it stays running")
    p.add_argument("title")
    p.add_argument("--settle", type=float, default=2.5,
                   help="seconds before checking it came up")
    verb("leave", help="end the running cart, put the desk back")
    verb("desk", help="back to the launcher (and desk) from anywhere")
    p = verb("perf", help="median drawn fps of a cart")
    p.add_argument("titles", nargs="+")
    p.add_argument("--secs", type=float, default=8.0)
    p.add_argument("--diag", action="store_true",
                   help="PERF DIAG on: phase ms, at the diag's own cost")
    p.add_argument("--uncap", action="store_true",
                   help="every loop frame draws (how the compiled carts' "
                        "floors are measured)")
    p = verb("push", help="copy a cart folder to the store, rescan (a compiled "
                          "cart with no module for the board's chip gets one, "
                          "unsigned)")
    p.add_argument("cart")
    p.add_argument("--only", action="append",
                   help="push just this file, as its path inside the cart "
                        "(scenes/x.moyscene); repeatable")
    p.add_argument("--dest")
    p.add_argument("--no-rescan", action="store_true")
    p = verb("refresh-wasm", help="rebuild the stale compiled-cart modules "
                                  "this BOARD'S STORE actually has "
                                  "(tools/refresh_wasm.py) -- what a "
                                  "format-version bump asks for")
    p.add_argument("paths", nargs="*",
                   help="local folders to look for a matching source in, "
                        "beside ports/jet (default: system_carts/ and ports/)")
    p = verb("shot", help="the glass as a PNG")
    p.add_argument("out")
    p.add_argument("--source", choices=("screen", "game"), default="screen",
                   help="screen: the buffer the panel last took; game: the "
                        "cart's canvas")
    p.add_argument("--raw", action="store_true",
                   help="send the frame uncompressed")
    p = verb("pmem", help="the running cart's pmem cells")
    p.add_argument("--text", action="store_true",
                   help="decode the cells as a NUL-terminated string")
    p.add_argument("--start", type=int, default=0, help="first cell (--text)")
    verb("mem", help="python heap, internal SRAM, PSRAM")
    p = verb("tap", help="a tap at X Y, through the pointer feed")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p = verb("swipe", help="a drag from X0 Y0 to X1 Y1 over FRAMES frames")
    for k in ("x0", "y0", "x1", "y1"):
        p.add_argument(k, type=int)
    p.add_argument("frames", type=int, nargs="?", default=20)
    p = verb("open", help="open settings|picker|appearance|wifi")
    p.add_argument("what")
    p = verb("tail", help="print what the board says")
    p.add_argument("secs", type=float)
    p.add_argument("--send", help="a command line to send first")
    p.add_argument("--grep", help="only lines containing this")
    p = verb("flash", help="cable-flash the built image, then wait")
    p.add_argument("--build", action="store_true", help="build it first")
    p.add_argument("--timeout", type=float, default=WAIT_S)
    p = verb("reboot", help="reset the way this board allows, wait")
    p.add_argument("--soft", action="store_true",
                   help="Ctrl-C then Ctrl-D: for a board sitting at >>>")
    p.add_argument("--timeout", type=float, default=WAIT_S)
    p = verb("wait", help="until the desk answers")
    p.add_argument("--timeout", type=float, default=WAIT_S)
    return ap


def main(argv=None):
    dirs = boards()
    a = parser(dirs).parse_args(argv)
    try:
        if a.board == "ports":
            return cmd_ports(a, dirs)
        if a.verb is None:
            raise BoardError("which verb? `tools/board.py %s -h` lists them"
                             % a.board)
        if a.verb == "push":
            port = a.port or resolve(a.board, dirs)[0]
            if not a.force:
                refuse_if_held(port)
            import push_cart
            argv2 = [a.cart, "--board", a.board, "--port", port]
            for f in a.only or ():
                argv2 += ["--only", f]
            if a.dest:
                argv2 += ["--dest", a.dest]
            if a.no_rescan:
                argv2.append("--no-rescan")
            return push_cart.main(argv2)
        if a.verb == "refresh-wasm":
            port = a.port or resolve(a.board, dirs)[0]
            if not a.force:
                refuse_if_held(port)
            import refresh_wasm
            paths = a.paths or list(refresh_wasm.DEFAULT_PATHS)
            done, total = refresh_wasm.refresh(a.board, port, paths,
                                               verbose=a.verbose)
            return 0 if done == total else 1
        if a.verb == "port":
            port = stable(a.port or resolve(a.board, dirs)[0])
            held = holders(port)
            print(port)
            for pid, cmd in held:
                print("held by pid %d: %s" % (pid, cmd[:110]), file=sys.stderr)
            return 1 if held else 0
        if a.verb == "flash":
            return cmd_flash(a.board, dirs, a)
        if a.verb == "reboot":
            return cmd_reboot(a.board, dirs, a)
        if a.verb == "wait":
            wait_for_desk(a.board, dirs, a.port, a.timeout)
            return 0
        if not is_console(dirs[a.board]):
            raise BoardError("%s has no dev channel: `ports`, `flash` and "
                             "`reboot` are what apply" % a.board)
        b = attach(a.board, dirs, a.port, a.force,
                   log=(lambda s: print("  | " + s)) if a.verbose else None)
        try:
            return ON_THE_CONSOLE[a.verb](b, a)
        finally:
            b.close()
    except BoardError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
