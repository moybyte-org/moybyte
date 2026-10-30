"""A play frame allocates NOTHING on the Python heap.

While a compiled cart plays, the console's own Python runs around it every
frame -- the frame loop, the inputs, the dev channel, the Player and its tick
model, the glue, the compositor hand-off. Whatever that path allocates becomes
garbage, and on the boards garbage is paid for in COLLECTIONS: a whole-heap
mark and sweep that stops the frame, and the compiled cart's sound with it.
On a T-Deck under Doom (2026-09-30) the free heap filled in about a minute and
a half and every collection outlasted the sound the stream queues, so the
speaker ran dry each time (#158 has the numbers). None of it was the cart's:
every byte was the console's frame.

So this boots the REAL T-Deck desktop -- `moy_runtime.run_desktop`, the shared
boot spine, the kernel's frame loop (`moy_loop`), the Workstation, the Player, `moycore_glue`'s
WasmRun with its CartFrame hand-off -- on the desktop MicroPython built in the
BOARDS' model (32-bit words, REPR_C, single floats, threads under one GIL: see
`make unix-micropython`), with its hardware replaced underneath by fakes that
return what the C returns in the shapes the C returns them. It runs a compiled
cart, lets it warm up, and then counts `gc.mem_alloc()` across whole loop frames.

THE GIL IS PART OF THE MODEL. The console's input poller is a thread that
mutates the keyboard object while the frame loop runs, and a board's threads
take turns under one GIL. The unix port's default is parallel threads with no
GIL, and on that build the poller and the loop corrupt each other's maps and
collections free what the other thread holds: a segfault or a lost attribute
mid-boot, with the driver reporting nothing. `tests/unix_mp.py` refuses a
board-model binary that is not under a GIL.

WHAT IT PINS. The frames allocate nothing -- with nothing held, and with a
button held down -- over windows long enough to take in two PERF periods and a
T-Deck diag tick (net of a thread's straddling block, see SLACK). The console boots in kid mode (PERF DIAG off,
the default), where nothing periodic is formatted, printed or ringed (owner
call 2026-09-30), so there is no frame that is allowed to allocate. The `perf`
skill lists the idioms that allocate on a board while reading as free; a dozen
of them were on this path at once.

It then runs Notes, the app cart on the public UI API, with a note open: idle
it paints nothing and allocates nothing, and typed into, one key every fourth
frame (the edit itself handed in between frames, as shell_bench types), the
frame each key paints allocates nothing either -- the verbs, the editor
handle's draw, the host's bar and the Player's app frame included.

Then it opens the shell's restyled apps on the public UI API -- Appearance,
Storybook, Get Carts over an in-memory shelf, My Art -- each idle and with its
content dragged, three swipes through the real pointer path. Idle, an app
allocates nothing. A drag frame allocates nothing of its own: what it may
allocate is the answers whose values the drag moved -- each card's (x, y, w,
h, state), a tuple a frame whose y differs from every recent answer
(modmoy_ui.c's ret_tuple ring keeps the ones that repeat), and the moving
finger's: the app's read of it (surface.pointer()), the game pointer the
console publishes while it is down, and the state the frame keeps to compare
the next with. So a drag frame is held to one answer a card it drew and four
more; the bar's clock turning its minute is an event of its own, one frame.

Then Settings' Achievements page (docs/look_2026-10.md step 32), idle at its
top and idle again swiped to its end: it allocates nothing either way.

Then the home, idle: the launcher's input and frame path with nothing pressed
paints nothing and allocates nothing.

WHEN IT FAILS. Something on the frame path allocates again, and the per-frame
deltas are printed. What names the LINE is a MicroPython whose gc_alloc and
gc_free charge each block to the Python line running (the code state's ip,
with MICROPY_PY_SYS_SETTRACE for the current-code-state pointer and its frame
objects switched off); the commit that added this file says how it was built.
"""

import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools.wasm_module import format_version
from unix_mp import require_unix_mp

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"

# -- the hardware under the T-Deck's modules ----------------------------------
#
# Each returns what its C counterpart returns, in the same SHAPE, because the
# shape is what allocates: a function returning a small int or a bool costs
# nothing, one returning a tuple or a memoryview costs a heap object.

FAKES = {
    "moy_lcd.py": '''
WIDTH = 320
HEIGHT = 240
BAND_ROWS = 32
BOUNCE_SLOTS = 2
BYTE_SWAP = True
_fbs = []
_arms = [0]


def init(nfbs=2):
    del _fbs[:]
    for _ in range(nfbs):
        _fbs.append(bytearray(WIDTH * HEIGHT * 2))


def nfbs():
    return len(_fbs)


def fb(i):
    return memoryview(_fbs[i])


def show(i):
    return None


def kick(i):
    return None


def drain():
    return True


def pending():
    return False


def sd_guard(on):
    return None


def backlight(on):
    return None


def fold_fence():
    return None


def fold_snap_fence():
    return None


def disarm_fold(i):
    return True


def arm_fold_snap(live, live_off, scratch, vw, vh, sx, sstride, ox, oy, scale):
    return True


def arm_fold_frame(frame, fmt, lut, scratch, gw, gh, sx, sy, vw, vh, ox, oy,
                   scale, rects, canvas):
    _arms[0] += 1
    return 0


def frame_arms():
    return _arms[0]


def fold_stats():
    return (0, 0, 0, 0)


def snap_stats():
    return (0, 0, 0, 0)


def pump_stats():
    return (0, 0, 0, 0, 0, 0, 0, 0, 0)


def stats():
    return (0, 0, 0, 0)
''',
    "fake_machine.py": '''
KBD = bytearray(5)          # the keyboard C3's raw matrix, as the test sets it


class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    IRQ_FALLING = 4
    IRQ_RISING = 8

    def __init__(self, n, mode=None, pull=None, value=None):
        self._v = 1

    def irq(self, handler=None, trigger=None):
        return None

    def value(self, v=None):
        return self._v


class I2C:
    def __init__(self, *a, **k):
        pass

    def readfrom(self, addr, n):
        return bytes(n)

    def readfrom_into(self, addr, buf):
        if len(buf) == 5:
            buf[:] = KBD
        else:
            buf[0] = 0

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        return bytes(n)

    def readfrom_mem_into(self, addr, reg, buf, addrsize=8):
        for i in range(len(buf)):
            buf[i] = 0

    def writeto(self, addr, buf):
        return len(buf)

    def writeto_mem(self, addr, reg, buf, addrsize=8):
        return None


def reset():
    raise SystemExit("reset")
''',
    "moy_alloc.py": '''
MEMORY_DMA = 8
MEMORY_INTERNAL = 2048
MEMORY_SPIRAM = 1024


def alloc(size, caps=MEMORY_SPIRAM):
    return memoryview(bytearray(size))


def malloc_dma(size, caps=MEMORY_DMA):
    return memoryview(bytearray(size))


def free(view):
    return None


def stats():
    return (0, 0)
''',
    "moy_wasm.py": '''
CHIP = "xtensa"
FORMAT = "@FORMAT@"


def footprint(mem, size):
    return (mem + size, mem)


def mem():
    return (0, 0, 0, 64 << 20, 32 << 20)
''',
    # The real moycore for everything a desktop build has, and for the compiled
    # run what a board's C does: open the console (a Lua VM holding no cart
    # stands in for the engine) and hand over ONE re-aimed frame view. The
    # cart's store reads are C (moycore's files, over moy_files) and never
    # reach Python.
    "moycore_shim.py": '''
_frame = bytearray(320 * 240)
_view = memoryview(_frame)
_state = [False]
_real = [None]


def run_begin(fb, w, h, wire, sheet, cells, mw, mh, snap, aq, pmem, cfg, flags,
              vm):
    return _real[0].run_begin(fb, w, h, wire, sheet, cells, mw, mh, snap, aq,
                              pmem, cfg, flags, True)


def wasm_open(module, head, pages, sha, path, swapped, allow_unsigned=False,
              interp=False, writable=None):
    return None


def tick(dt, draw=True):
    return _real[0].tick(dt, draw)


def take_frames(on, palette=True):
    _state[0] = bool(on)


def frame(lut):
    return _view if _state[0] else None


def frame_kept():
    return False


def frame_settle():
    return None


def frame_presented(kept=None, off=0):
    return None


def install(real, mod):
    mine = {}
    for k in ("run_begin", "wasm_open", "tick", "take_frames", "frame",
              "frame_kept", "frame_settle", "frame_presented"):
        mine[k] = getattr(mod, k)
    for k in dir(real):
        if not k.startswith("__"):
            setattr(mod, k, getattr(real, k))
    for k in mine:
        setattr(mod, k, mine[k])
    mod.WASM = 1
    mod.TICK_DRAW = 1
    _real[0] = real
''',
}

DRIVER = r'''
import sys
import gc
import json
import _thread
import time
ROOT = @ROOT@
sys.path.insert(0, ROOT + "/stage")
sys.path.insert(0, ROOT + "/fakes")
DONE = _thread.allocate_lock()
DONE.acquire()


def _frames(loop, ms, held, least=0):
    """What each loop frame allocates, over `ms` and at least `least` frames."""
    import fake_machine
    fake_machine.KBD[0] = 0x02 if held else 0      # "w": the up button
    from gc import mem_alloc
    out = []
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms or len(out) < least:
        a = mem_alloc()
        loop.step()
        out.append(mem_alloc() - a)
    return out


def measure(loop):
    import fake_machine
    ws = loop.ws
    for _ in range(30):
        loop.step()
    title = ws.launch_named("Frame Probe")
    for _ in range(60):
        loop.step()
    info = {"title": title, "error": str(ws.cart_error),
            "frame": getattr(ws, "cart_frame", None) is not None,
            "top": ws.wm.top_kind()}
    # A first run unlocks an achievement, and its toast paints over the game
    # for a few seconds: an event, not the steady frame. Let it end.
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 10000:
        now = time.ticks_ms()
        if not (ws._toast_until and time.ticks_diff(ws._toast_until, now) > 0):
            break
        loop.step()
    for _ in range(30):                  # the held key's first edges, settled
        fake_machine.KBD[0] = 0x02
        loop.step()
    fake_machine.KBD[0] = 0
    for _ in range(30):
        loop.step()
    gc.collect()
    gc.disable()
    info["diag"] = bool(getattr(ws, "diag_live", False))
    info["idle"] = _frames(loop, @WINDOW_MS@, False, @MIN_FRAMES@)
    _frames(loop, 100, True)             # the press edge itself may allocate
    info["held"] = _frames(loop, @WINDOW_MS@, True, @MIN_FRAMES@)
    gc.enable()
    notes(loop, info)
    code(loop, info)
    apps(loop, info)
    achievements(loop, info)
    home(loop, info)
    editor(loop, info)
    panes(loop, info)
    print("RESULT " + json.dumps(info))


def _typing(loop, ms, least=0):
    """Notes' note typed into, one key every fourth frame, handed to its
    editor handle as shell_bench's typing hands it (the edit is the
    document's, outside the frame measured): what each frame allocates,
    over `ms` and at least `least` frames."""
    from gc import mem_alloc
    keys = b"the quick brown fox jumps over the lazy dog "
    fed = loop.ws.player._focus_editor
    out = []
    n = 0
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms or n < least:
        if n % 4 == 0:
            fed.key(keys[(n // 4) % len(keys)])
        n += 1
        a = mem_alloc()
        loop.step()
        out.append(mem_alloc() - a)
    return out


def notes(loop, info):
    """Notes with a note open, idle and typed into: the app cart a desk
    runs every day, on the verbs."""
    ws = loop.ws
    ws.exit()
    for _ in range(30):
        loop.step()
    info["notes_title"] = ws.launch_named("Notes")
    for _ in range(30):
        loop.step()
    ns = ws.player.ns
    got, err = ns["files"].new_name("probe")
    ns["_open"](got)
    for _ in range(60):
        loop.step()
    info["notes_error"] = str(ws.cart_error)
    info["notes_focused"] = ws.player._focus_editor is not None
    _typing(loop, 1500)                  # the first keys' edges, settled
    # ...and the idle save they owe (EditorHandle.AUTOSAVE_MS), an event of
    # its own: idle is what comes after it.
    fed = ws.player._focus_editor
    t0 = time.ticks_ms()
    while fed.dirty() and time.ticks_diff(time.ticks_ms(), t0) < 15000:
        loop.step()
    info["notes_saved"] = not fed.dirty()
    for _ in range(30):
        loop.step()
    gc.collect()
    gc.disable()
    d0 = ws._frames_drawn
    info["notes_idle"] = _frames(loop, @WINDOW_MS@, False, @MIN_FRAMES@)
    d1 = ws._frames_drawn
    info["notes_text0"] = len(ws.player._focus_editor.text())
    info["notes_typing"] = _typing(loop, @WINDOW_MS@, @MIN_FRAMES@)
    info["notes_idle_drawn"] = d1 - d0
    info["notes_typing_drawn"] = ws._frames_drawn - d1
    info["notes_text"] = len(ws.player._focus_editor.text())
    gc.enable()
    notes_swiped(loop, info)


def notes_swiped(loop, info):
    """The note swiped up and down its long text: what each frame of the
    drag and of the coast after it allocates, whether it drew and whether it
    shifted."""
    import moy_loop
    from gc import mem_alloc
    ws = loop.ws
    fed = ws.player._focus_editor
    fed.set_text("\n".join("Line %d of a long note, with words enough to wrap past the "
                           "edge of the paper" % i for i in range(120)))
    fed.save()
    for _ in range(60):
        loop.step()
    x, y, w, h = ws.player.ns["sheet"]
    out = []
    for rep in range(4):
        y0, y1 = (y + h * 3 // 4, y + h // 4) if rep % 2 == 0 else (y + h // 4, y + h * 3 // 4)
        moy_loop.swipe(x + w // 2, y0, x + w // 2, y1, 12)
        gc.collect()
        gc.disable()
        for _ in range(40):
            d0 = ws._frames_drawn
            m = mem_alloc()
            loop.step()
            if rep:
                out.append((mem_alloc() - m, ws._frames_drawn - d0,
                            ws.frame_change == "shift"))
        gc.enable()
    info["notes_swipe"] = out


def code(loop, info):
    """The Editor's Code tab, a `ui.text_view`: typed into (a key every
    fourth frame, the edit the editor's, outside the frame measured), then
    its view sent down the file and back by the wheel, which glides it. What each frame allocates, and what the tab's draw does: its
    text view's pass (`_rows`) and the rest of it but the status line,
    which a moved caret makes again."""
    from gc import mem_alloc
    ws = loop.ws
    ws.exit()
    for _ in range(30):
        loop.step()
    ed = None
    for cart in ws.carts.all:            # a cart whose code the Editor opens
        ws.open_in_editor(cart)
        ws.set_menu_view("code")
        ed = ws.editor
        if ed is not None:
            break
        ws.exit()
    info["code_cart"] = cart.get("title")
    ed.set_text("\n".join("value_%d = probe(%d) + 1  # a line of the cart" % (i, i)
                          for i in range(120)))
    ed.row = 5
    ed.col = 0
    for _ in range(60):
        loop.step()
    info["code_top"] = ws.wm.top_kind()
    cl = ws.code_layer
    acc = [0, 0, 0, 0, 0]               # rows bytes, rows calls, draw bytes, status bytes, draws
    rows = cl._rows
    draw = cl._draw_code
    status = cl._draw_status

    def _rows(cv, r, e):
        m = mem_alloc()
        rows(cv, r, e)
        acc[0] += mem_alloc() - m
        acc[1] += 1

    def _draw():
        m = mem_alloc()
        draw()
        acc[2] += mem_alloc() - m
        acc[4] += 1

    def _status(cv, lay, e):
        m = mem_alloc()
        status(cv, lay, e)
        acc[3] += mem_alloc() - m

    cl._rows = _rows
    cl._draw_code = _draw
    cl._draw_status = _status
    keys = b"score = score + 1  "

    def typing(ms):
        out = []
        n = 0
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < ms:
            if n % 4 == 0:
                ed.key(keys[(n // 4) % len(keys)])
                ws.mark_dirty()
            n += 1
            a = mem_alloc()
            loop.step()
            out.append(mem_alloc() - a)
        return out

    def take():
        got = list(acc)
        for i in range(len(acc)):
            acc[i] = 0
        return got

    typing(1000)                         # the first keys' edges, settled
    gc.collect()
    gc.disable()
    take()
    info["code_typing"] = typing(@WINDOW_MS@)
    info["code_typing_acc"] = take()
    gc.enable()
    for _ in range(30):
        loop.step()
    gc.collect()
    gc.disable()
    take()
    out = []

    class _Wheel:
        wheel_x = 0
        wheel_y = 0
        mods = 0
    wheel = _Wheel()
    for notches in (6, -6) * 4:
        wheel.wheel_y = notches          # the wheel, which glides the view
        cl._wheel(wheel)
        for _ in range(40):
            a = mem_alloc()
            loop.step()
            out.append(mem_alloc() - a)
    info["code_glide"] = out
    info["code_glide_acc"] = take()
    gc.enable()
    del cl._rows
    del cl._draw_code
    del cl._draw_status
    ws.exit()
    for _ in range(30):
        loop.step()


class _Resp:
    def __init__(self, data, status=200):
        import io
        self._b = io.BytesIO(data)
        self.status = status
        self.length = len(data)
        self.closed = False

    def readinto(self, buf):
        return self._b.readinto(buf)

    def close(self):
        self.closed = True


class _Net:
    """Get Carts' transport over the staged shelf: {url: file}."""

    def __init__(self, routes):
        self.routes = routes
        self.up = True

    def online(self):
        return True

    def open(self, url):
        f = self.routes.get(url)
        if f is None:
            return _Resp(b"not found", 404)
        with open(f, "rb") as fh:
            return _Resp(fh.read())


APPS = ("appearance", "storybook", "getcarts", "myart")


def apps(loop, info):
    """Each restyled app open, idle, then its content swiped: what each
    frame allocates, and how many cards each drag frame drew."""
    import moy_loop
    import ui
    from gc import mem_alloc
    ws = loop.ws
    ws.exit()
    for _ in range(30):
        loop.step()
    ws.cart_net = _Net(json.load(open(ROOT + "/net/routes.json")))
    cards = [0]
    card = ui.card

    def counted(title, sub="", o=None):
        cards[0] += 1
        return card(title, sub, o)

    ui.card = counted
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    for app in APPS:
        a = ws._apps_by_id[app]
        info[app + "_open"] = ws.open_app(a)
        for _ in range(60):
            loop.step()
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < 20000:
            now = time.ticks_ms()
            if not ((ws._toast_until and time.ticks_diff(ws._toast_until, now) > 0)
                    or getattr(a, "phase", "list") != "list"
                    or getattr(a, "_covers", None)):
                break
            loop.step()
        for _ in range(30):
            loop.step()
        info[app + "_top"] = ws.wm.top_kind()
        gc.collect()
        gc.disable()
        idle = []
        for _ in range(60):
            m = mem_alloc()
            loop.step()
            idle.append(mem_alloc() - m)
        info[app + "_idle"] = idle
        gc.enable()
        for y0, y1 in ((H * 3 // 4, H // 4), (H // 4, H * 3 // 4)):
            moy_loop.swipe(W // 2, y0, W // 2, y1, 25)     # every picture met once
            for _ in range(30):
                loop.step()
        # ...and made: My Art makes a picture a quiet frame, and a loaded
        # machine's longer frames carry a swipe past more of them
        for _ in range(600):
            if not getattr(a, "idle_owed", False):
                break
            loop.step()
        drag = []
        for rep in range(3):
            y0, y1 = (H * 3 // 4, H // 4) if rep % 2 == 0 else (H // 4, H * 3 // 4)
            moy_loop.swipe(W // 2, y0, W // 2, y1, 25)
            gc.collect()
            gc.disable()
            for _ in range(30):
                cards[0] = 0
                d0 = ws._frames_drawn
                m = mem_alloc()
                loop.step()
                drag.append((mem_alloc() - m, ws._frames_drawn - d0, cards[0],
                             ws.frame_change == "shift"))
            gc.enable()
        info[app + "_drag"] = drag
        ws.exit()
        for _ in range(30):
            loop.step()
    ui.card = card


def editor(loop, info):
    """The Editor's table of parts (docs/ui_blocks_2026-10.md section 4.1):
    the Blocks outline grown to 40 rows, its cursor walked a row a frame
    (the move handed in between frames, as a key's edit is) and its rows
    swiped and coasting: what each frame and the page's draw allocate."""
    import moy_loop
    from gc import mem_alloc
    ws = loop.ws
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Star Catcher"))
    for tab in ("blocks",):
        ws.set_menu_view(tab)
        bu = ws.block_ui
        be = bu.blocks_ed
        while len(be.rows) < 40:
            be.rows.append(be.rows[-1])
        be._hist.gen += 1
        be.cur = 0
        move = bu._blk_move_cursor
        ws.mark_dirty()
        for _ in range(60):
            loop.step()
        info[tab + "_view"] = ws.menu_view
        # the page's own draw, measured apart from the Editor's frame around it
        import editor_blocks
        page = editor_blocks.BlocksPage
        drew = [0]
        draw = page.draw

        def counted(self, cv, th, rect, desk):
            m0 = mem_alloc()
            draw(self, cv, th, rect, desk)
            drew[0] += mem_alloc() - m0
        page.draw = counted
        gc.collect()
        gc.disable()
        walk = []
        for d in (1,) * 24 + (-1,) * 24:
            move(d)
            ws.mark_dirty()
            d0 = ws._frames_drawn
            m = mem_alloc()
            loop.step()
            walk.append((mem_alloc() - m, ws._frames_drawn - d0))
        gc.enable()
        info[tab + "_walk"] = walk
        info[tab + "_page"] = drew[0]
        drag = []
        for rep in range(2):
            y0, y1 = (H * 3 // 4, H // 3) if rep % 2 == 0 else (H // 3, H * 3 // 4)
            moy_loop.swipe(W // 4, y0, W // 4, y1, 25)
            gc.collect()
            gc.disable()
            for _ in range(30):
                d0 = ws._frames_drawn
                p0 = drew[0]
                loop.step()
                drag.append((drew[0] - p0, ws._frames_drawn - d0,
                             ws.frame_change == "shift"))
            gc.enable()
        page.draw = draw
        info[tab + "_drag"] = drag
    ws.exit()
    for _ in range(30):
        loop.step()


def panes(loop, info):
    """ui.split's moving frames (docs/ui_blocks_2026-10.md section 4.2): on
    an app host of its own, a divider pressed, dragged 40 px and let go, then
    a tap on a part in the second pane (ui.part_at): what the split
    allocates each frame, and what part_at does."""
    from gc import mem_alloc
    from array import array
    import ui
    import ui_draw
    ws = loop.ws
    cv = ws.sys_canvas
    th = ws.theme_colors
    W = cv.w
    host = ui_draw.AppHost()
    sizes = array("i", [100, 0])
    out = array("i", [0] * 12)
    o = {"drag": True, "focus": 0, "gap": 6, "mins": (40, 40)}
    steps = ([(None, 0, False, False)] * 3 + [(103, 50, True, True)]
             + [(103 + 2 * k, 50, False, True) for k in range(1, 21)]
             + [(143, 50, False, False)] + [(None, 0, False, False)] * 2
             + [(200, 50, True, True), (200, 50, False, False), (None, 0, False, False)])
    split = []
    at = []
    gc.collect()
    gc.disable()
    for x, y, click, down in steps:
        if x is not None:
            host.point(x, y, click, down)
        host.begin(cv, th, (0, 0, W, 100))
        ui.at(0, 0, W, 100)
        m = mem_alloc()
        o["focus"] = ui.split("panes", "x", sizes, o, out)
        split.append(mem_alloc() - m)
        ui.at(out[4], out[5], out[6], out[7])
        st = ui.part("cell")[4]
        m = mem_alloc()
        got = ui.part_at()
        at.append((mem_alloc() - m, st & ui.ACTIVATED != 0, got is not None))
        ui.end()
    gc.enable()
    info["split"] = split
    info["split_sizes"] = list(sizes)
    info["split_focus"] = o["focus"]
    info["part_at"] = at
    ws.mark_dirty()
    for _ in range(10):
        loop.step()


def achievements(loop, info):
    """Settings' Achievements page, idle at its top, then swiped to its end
    and idle there: what each idle frame allocates."""
    import moy_loop
    from gc import mem_alloc
    ws = loop.ws
    ws.ach.unlocked = {"first_open": 20261012, "first_run": 0, "konami": 0}
    ws.open_settings()
    # the rows page swiped, its pictures met once: what each frame of a drag
    # and its coast allocates, and which shifted
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    for _ in range(60):
        loop.step()
    drag = []
    for rep in range(4):
        y0, y1 = (H * 3 // 4, H // 4) if rep % 2 == 0 else (H // 4, H * 3 // 4)
        moy_loop.swipe(W // 2, y0, W // 2, y1, 25)
        gc.collect()
        gc.disable()
        for _ in range(30):
            d0 = ws._frames_drawn
            m = mem_alloc()
            loop.step()
            if rep:
                drag.append((mem_alloc() - m, ws._frames_drawn - d0,
                             ws.frame_change == "shift"))
        gc.enable()
    info["settings_drag"] = drag
    ws.settings_layer.open_page("achievements")
    for _ in range(60):
        loop.step()
    info["ach_page"] = ws.settings_layer.page
    for key in ("ach_idle", "ach_scrolled"):
        for _ in range(30):
            loop.step()
        for _ in range(300):              # the swipe's coast comes to rest
            if not ws.settings_layer._input_host().moving:
                break
            loop.step()
        gc.collect()
        gc.disable()
        idle = []
        for _ in range(60):
            m = mem_alloc()
            loop.step()
            idle.append(mem_alloc() - m)
        info[key] = idle
        gc.enable()
        moy_loop.swipe(W // 2, H * 3 // 4, W // 2, H // 4, 25)
    ws._exit_settings()
    for _ in range(30):
        loop.step()


def home(loop, info):
    """The home with nothing pressed: what each frame paints and allocates."""
    ws = loop.ws
    for _ in range(60):
        loop.step()
    info["home_top"] = ws.wm.top_kind()
    gc.collect()
    gc.disable()
    d0 = ws._frames_drawn
    info["home_idle"] = _frames(loop, @WINDOW_MS@, False, @MIN_FRAMES@)
    info["home_idle_drawn"] = ws._frames_drawn - d0
    gc.enable()


class _Loop:
    """The kernel's loop as the measurement drives it: one step a frame over
    the console the board's run_desktop handed it."""

    def __init__(self, desktop):
        import moy_loop
        self.ws = desktop.ws
        self.step = moy_loop.step


def main():
    try:
        import fake_machine
        sys.modules["machine"] = fake_machine
        import moycore as _real_moycore
        import moycore_shim
        moycore_shim.install(_real_moycore, moycore_shim)
        sys.modules["moycore"] = moycore_shim

        import moybyte_sd
        moybyte_sd._live_mounted = True      # the card attached at boot
        import device_boot
        import moy_runtime

        def _load(self, boot, store):
            carts, root = boot.load_carts(store, [], root=ROOT + "/carts",
                                          media="SD")
            self.on_sd = True                # the T-Deck's store is its card
            return carts, root, None

        moy_runtime._Storage.load = _load
        moy_runtime.POWER_SAVE_MS = 0
        measure(_Loop(moy_runtime.run_desktop()))
    except BaseException as exc:             # noqa -- say it, then end the run
        sys.print_exception(exc)
    DONE.release()


# The console's imports nest deeper than the desktop port's main-thread C stack
# allows; a board's task has the room, and so does a thread given it.
_thread.stack_size(4 << 20)
_thread.start_new_thread(main, ())
DONE.acquire()
time.sleep_ms(50)
'''

MANIFEST = {"format": "moy-1", "title": "Frame Probe", "runtime": "wasm",
            "main": "main.wasm", "memory": 16, "fps": "free",
            "input": ["buttons", "touch", "keyboard"]}

# Each measured window, in ms: two PERF periods (2 s) and a T-Deck diag tick
# (3 s) fall inside it.
WINDOW_MS = 4500

# ...and the frames each takes in at least, whatever the machine's load. A
# window's power is its frame count -- an allocation on one frame in eight
# nets 16 bytes x frames / 8 against SLACK -- and a loaded machine (xdist
# beside the rest of the suite) fits a third of the frames into the same
# milliseconds, so it runs its windows longer instead: about what the loop
# paces into WINDOW_MS on a machine to itself, as CI's serial run has it.
MIN_FRAMES = 150


def _stage(dest, exe, apps=False):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import board_config
    finally:
        sys.path.pop(0)
    stage = dest / "stage"
    stage.mkdir()
    for name, src in board_config.staged_modules(TDECK, root=ROOT).items():
        shutil.copyfile(src, stage / name)
    for name, src in board_config.staged_packages(TDECK, root=ROOT).items():
        shutil.copytree(src, stage / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("moy_runtime.py", "tdeck_input.py"):
        shutil.copyfile(TDECK / "modules" / name, stage / name)
    (stage / "carts_data.py").write_text("CARTS_Z = []\n")
    # FROZEN, as on a board: compiled ahead by the tree's own mpy-cross. A
    # desktop MicroPython compiling a hundred modules at import nests past its
    # C-stack limit, and no board ever compiles them at all.
    mpy_cross = Path(exe).resolve().parents[3] / "mpy-cross" / "build" / "mpy-cross"
    for src in sorted(stage.rglob("*.py")):
        subprocess.run([str(mpy_cross), "-o", str(src.with_suffix(".mpy")),
                        "-s", src.name, str(src)], check=True,
                       capture_output=True, cwd=str(src.parent))
        src.unlink()
    fakes = dest / "fakes"
    fakes.mkdir()
    for name, body in FAKES.items():
        (fakes / name).write_text(body.lstrip("\n").replace("@FORMAT@", format_version()))
    cart = dest / "carts" / "frame_probe.moy"
    cart.mkdir(parents=True)
    (cart / "manifest.json").write_text(json.dumps(MANIFEST))
    # The module head up to its memory section (1 page), and a placeholder for
    # this chip's compiled module: the shim's engine never reads either. Named
    # for the shim's FORMAT too (moy_wasm.py above), or moycore_glue finds no
    # module by that name and this test measures the interpreter fallback
    # instead of the AOT path it means to.
    (cart / "main.wasm").write_bytes(b"\0asm\1\0\0\0\5\3\1\0\x10")
    (cart / ("main.xtensa.f%s.aot" % format_version())).write_bytes(b"AOT")
    (cart / "config.json").write_text("{}")
    shutil.copytree(ROOT / "system_carts" / "moybyte.notes.moy",
                    dest / "carts" / "moybyte.notes.moy")
    shutil.copytree(ROOT / "system_carts" / "moybyte.star_catcher.moy",
                    dest / "carts" / "moybyte.star_catcher.moy")
    if apps:
        for app in ("appearance", "storybook", "get_carts", "my_art"):
            shutil.copytree(ROOT / "system_carts" / ("moybyte.%s.moy" % app),
                            dest / "carts" / ("moybyte.%s.moy" % app))
        _stage_app_data(dest)


def _stage_app_data(dest):
    """What the apps show: a shelf of twelve carts with covers for Get Carts
    (an index and its covers, served from files), eight stories, fourteen
    drawings."""
    from runtime import moy_carts
    from runtime import story_doc as D
    from cart_store_fixtures import INDEX_URL, snapshot
    root = str(dest / "carts")
    net = dest / "net"
    net.mkdir()
    index = json.loads(snapshot("index.json"))
    base = INDEX_URL.rsplit("/", 1)[0] + "/"
    pngs = sorted((ROOT / "system_carts").glob("*/cover.png"))
    routes = {}
    shelf = []
    for i in range(12):
        c = dict(index["carts"][i % len(index["carts"])])
        c["id"] = "c%02d" % i
        c["name"] = "Cart %02d" % i
        c["folder"] = "c%02d.moy" % i
        png = pngs[i % len(pngs)].read_bytes()
        w, h = struct.unpack(">II", png[16:24])
        url = "carts/c%02d/cover.png" % i
        c["cover"] = {"url": url, "size": len(png), "w": w, "h": h,
                      "sha256": hashlib.sha256(png).hexdigest()}
        (net / ("%d.png" % i)).write_bytes(png)
        routes[base + url] = str(net / ("%d.png" % i))
        shelf.append(c)
    index["carts"] = shelf
    (net / "index.json").write_text(json.dumps(index))
    routes[INDEX_URL] = str(net / "index.json")
    (net / "routes.json").write_text(json.dumps(routes))
    for i in range(8):
        doc = D.new_doc("Story %d" % i)
        moy_carts.save_file("stories", "story%d/story.json" % i, D.write(doc), root)
    for i in range(14):
        w, h = 320, 240
        px = bytearray([1 + i % 15]) * (w * h)
        for y in range(h // 4, h * 3 // 4):
            px[y * w + w // 4:y * w + w * 3 // 4] = bytes([(i * 3) % 16]) * (w // 2)
        moy_carts.save_file("drawings", "art%02d" % i,
                            moy_carts.encode_moyimg(w, h, bytes(px)), root)


def _run(tmp_path):
    exe = require_unix_mp(
        "moycore", "moy_gfx", "moy_audio", board_model=True,
        why="This is the only check that a play frame allocates nothing on "
            "the boards' Python heap. A board pays for garbage in whole-heap "
            "collections that stop the frame and starve a compiled cart's "
            "sound; nothing else in the suite measures a frame's allocation.")
    _stage(tmp_path, exe, apps=True)
    script = tmp_path / "driver.py"
    script.write_text(DRIVER.replace("@ROOT@", repr(str(tmp_path)))
                      .replace("@WINDOW_MS@", str(WINDOW_MS))
                      .replace("@MIN_FRAMES@", str(MIN_FRAMES)))
    out_path = tmp_path / "out.txt"
    # stdin stays OPEN and silent, as a board's serial does between commands:
    # the dev channel polls it every frame.
    with open(out_path, "w") as out:
        proc = subprocess.Popen([exe, "-X", "heapsize=64M", str(script)],
                                stdin=subprocess.PIPE, stdout=out,
                                stderr=subprocess.STDOUT)
        try:
            proc.wait(timeout=240)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdin.close()
    text = out_path.read_text()
    for line in text.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:]), text
    raise AssertionError("the driver reported nothing:\n" + text[-4000:])


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("frame_alloc"))


# What a window may net, in bytes. Not zero: the input poller is a thread, and
# a call of its that spills its frame to the heap can straddle two readings --
# the block lands in one window and its free in the next. Garbage is what never
# comes back, and a per-frame allocation of even one block nets 16 bytes a
# frame, hundreds of frames a window.
SLACK = 256


def _assert_quiet(deltas, what, text, minute=False):
    """`minute`: a surface under the bar may pay one frame for the clock's
    minute turning inside the window (its text re-set, about 1.5 KB)."""
    assert len(deltas) > 20, "%s: only %d frames measured" % (what, len(deltas))
    spent = [d for d in deltas if d]
    if minute and spent:
        deltas = list(deltas)
        deltas.remove(max(deltas))
    assert sum(deltas) < SLACK, (
        "%s: %d bytes over %d frames; the frames that moved: %s\n%s"
        % (what, sum(deltas), len(deltas), spent[:40], text[-3000:]))


def test_the_compiled_cart_is_the_run_being_measured(result):
    info, text = result
    assert info["title"] == "Frame Probe", text[-3000:]
    assert info["diag"] is False, "not kid mode: PERF DIAG is on"
    assert info["error"] == "None" and info["top"] == "desktop", text[-3000:]
    # The T-Deck shows a compiled cart's frame from the cart's memory, so the
    # hand-off (CartFrame -> present_frame -> the fold) is on the path.
    assert info["frame"] is True, text[-3000:]


def test_a_play_frame_allocates_nothing(result):
    info, text = result
    _assert_quiet(info["idle"], "nothing held", text)


def test_a_play_frame_with_a_button_held_allocates_nothing(result):
    info, text = result
    _assert_quiet(info["held"], "'up' held", text)


def test_notes_is_the_run_being_measured(result):
    info, text = result
    assert info["notes_title"] == "Notes", text[-3000:]
    assert info["notes_error"] == "None", text[-3000:]
    assert info["notes_focused"] is True, text[-3000:]
    assert info["notes_saved"] is True, "the idle save never came"
    # The typing window typed, and every key it fed reached the note. The
    # window is wall-clock, so how many keys fit depends on the machine.
    keys = (len(info["notes_typing"]) + 3) // 4
    assert keys >= 4, keys
    assert info["notes_text"] - info["notes_text0"] == keys, (
        info["notes_text0"], info["notes_text"], keys)


def test_notes_idle_paints_nothing_and_a_key_paints_its_frame(result):
    """An app on the verbs is drawn on change (Player.animates): idle, not a
    frame; typed into, the frame each key lands in."""
    info, text = result
    assert info["notes_idle_drawn"] == 0, info["notes_idle_drawn"]
    keys = (len(info["notes_typing"]) + 3) // 4
    assert keys - 1 <= info["notes_typing_drawn"] <= keys + 1, (
        info["notes_typing_drawn"], keys)


def test_notes_idle_allocates_nothing(result):
    info, text = result
    _assert_quiet(info["notes_idle"], "Notes idle, a note open", text, minute=True)


def test_notes_typing_allocates_nothing(result):
    info, text = result
    _assert_quiet(info["notes_typing"], "Notes typed into", text, minute=True)


def test_a_notes_swipe_shifts_and_allocates_only_the_fingers_answers(result):
    """Notes' note swiped and coasting (its `ui.text_view`): its frames
    shift, and a frame of the drag or the coast allocates no more than the
    finger's answers (the cart's `touch()`), but for each swipe's press,
    whose tap reads the line under the finger (`EditorHandle.tap`)."""
    info, text = result
    swipe = info["notes_swipe"]
    drawn = [d for d in swipe if d[1]]
    shifted = [d for d in drawn if d[2]]
    assert len(drawn) > 30 and len(shifted) > len(drawn) // 2, (len(shifted), len(drawn))
    over = [d for d in drawn if d[0] > ANSWER * 4]
    assert len(over) <= 3, over[:10]           # the three swipes measured: a press each
    assert all(d[0] <= ANSWER * 8 for d in over), over


def test_the_code_tabs_text_view_allocates_nothing_typed_or_gliding(result):
    """The Code tab draws through `ui.text_view` on every painted frame: its
    text view's pass allocates nothing, typed into or gliding, and the rest
    of its draw nothing but the status line a moved caret makes again. (A
    frame of the Editor also pays its bar's strip, which every tab draws.)"""
    info, text = result
    assert info["code_top"] == "menu", info["code_top"]
    for what in ("typing", "glide"):
        rows, calls, drew, status, draws = info["code_%s_acc" % what]
        assert calls > 20 and draws > 20, "Code %s: %d text view passes" % (what, calls)
        assert rows == 0, "Code %s: its text view's passes allocated %d bytes over %d" % (
            what, rows, calls)
        assert drew - status == 0, (
            "Code %s: its draws allocated %d bytes over %d, the status line aside"
            % (what, drew - status, draws))


# -- the desk Editor's Code pane -----------------------------------------------
#
# Not a play frame, but the same bill: on the Waveshare P4 a drag in the desk
# Code pane allocated ~136 KB EVERY frame, so how often the collector came
# depended on the heap's layout (1 collection on one image, 8 on another,
# for the same code). The bytes were a whole Code layout rebuilt per frame:
# the pane's cached layout said "no symbol keys" while the last input, a
# touch, asked for them, so the tab re-laid itself out as it drew and the
# pane put its stale layout back after. What allocated is pinned here by the
# work it did, on the host: the board-model MicroPython above boots a
# handheld, which has no panes.

def test_a_desk_code_drag_frame_does_not_rebuild_the_code_layout(tmp_path):
    sys.path.insert(0, str(ROOT))
    from runtime import host_app, ui_draw
    dt = 1 / 30
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    panel_diagonal_in=7.0, font_scale=1, windowed=True)
    ws.open_desk()
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(dt)
    ws.open_in_editor(next(c for c in ws.carts.all if c.get("title") == "Star Catcher"))
    ws.editor_app.set_tab("code")
    for _ in range(4):
        drv.frame(dt)
    rebuilt = []
    real = ws._relayout_code
    ws._relayout_code = lambda: rebuilt.append(1) or real()
    was = ui_draw.input_kind()
    ui_draw.input_kind("touch")
    try:
        drv.touch(400, 420)
        drv.frame(dt)
        painted = ws._frames_drawn
        for i in range(12):
            drv.touch_drag(400, 400 - 20 * i)
            drv.frame(dt)
        drv.touch_up()
        drv.frame(dt)
    finally:
        ui_draw.input_kind(was)
    assert ws._frames_drawn - painted >= 10, "the drag painted too few frames to say"
    assert len(rebuilt) <= 1, "the Code layout was rebuilt %d times in a drag" % len(rebuilt)


@pytest.mark.parametrize("app", ("appearance", "storybook", "getcarts", "myart"))
def test_an_app_idle_allocates_nothing(result, app):
    info, text = result
    assert info[app + "_open"] is True and info[app + "_top"] == app, text[-3000:]
    _assert_quiet(info[app + "_idle"], app + " idle", text)


@pytest.mark.parametrize("key", ("ach_idle", "ach_scrolled"))
def test_the_achievements_page_allocates_nothing(result, key):
    """Settings' Achievements page, idle at its top and idle scrolled: its
    rows are made when the badges change, never a frame."""
    info, text = result
    assert info["ach_page"] == "achievements", text[-3000:]
    _assert_quiet(info[key], key, text)


def test_the_home_idle_paints_nothing_and_allocates_nothing(result):
    """The launcher's input path runs every frame whether or not it paints:
    a key probe that holds a generator makes its closure cells each call."""
    info, text = result
    assert info["home_top"] == "launcher", text[-3000:]
    assert info["home_idle_drawn"] == 0, info["home_idle_drawn"]
    _assert_quiet(info["home_idle"], "the home idle", text, minute=True)


# One answer tuple: a 5-tuple (the card's) is seven words, two 16-byte blocks.
ANSWER = 32
# A Settings frame's own answers: the strings and tuples its rows show, read
# again each frame (`SettingsLayer._value`); the shift frame adds none.
SETTINGS_FRAME = 1024


@pytest.mark.parametrize("app", ("getcarts", "myart"))
def test_an_app_drag_is_shift_frames(result, app):
    """The drag frames of an app whose shelf carries its gens shift
    (docs/scroll_and_text_2026-10.md §3.3) on the T-Deck's tier: the ring's
    slots and the floor's rects are kept, so the budget the test below holds
    every drawn frame to holds the shift frames."""
    info, text = result
    drawn = [d for d in info[app + "_drag"] if d[1]]
    shifted = [d for d in drawn if d[3]]
    assert len(shifted) > len(drawn) // 2, (app, len(shifted), len(drawn))


def test_a_settings_drag_shifts_and_allocates_nothing(result):
    """Settings' rows page dragged and coasting: its frames shift, and a
    shift frame allocates no more than the rows' own answers."""
    info, text = result
    drag = info["settings_drag"]
    drawn = [d for d in drag if d[1]]
    shifted = [d for d in drawn if d[2]]
    assert len(drawn) > 30 and len(shifted) > len(drawn) // 2, (len(shifted), len(drawn))
    over = sorted(d[0] for d in shifted)
    assert over[len(over) // 2] < SETTINGS_FRAME, ("a shift frame's median", over)


@pytest.mark.parametrize("app", ("appearance", "storybook", "getcarts", "myart"))
def test_an_app_drag_frame_allocates_only_its_moved_answers(result, app):
    """A drag frame of the app's content, its pictures met once before: the
    frames drew, and each allocated no more than one answer a card it drew
    and the finger's four, but for one frame (the clock's minute)."""
    info, text = result
    drag = info[app + "_drag"]
    drawn = [d for d in drag if d[1]]
    assert len(drawn) > 30, (app, len(drawn))
    over = [d for d in drawn if d[0] > ANSWER * (d[2] + 4)]
    assert len(over) <= 1, (app, over[:10])


# -- the Code tab's paint ------------------------------------------------------
#
# Two per-frame costs of a Code frame on the Waveshare P4, found with
# tools/p4_alloc.py over p4_surface_sweep's Code drag, where they set how
# often the collector came:
#
# * The bar reads the Editor's undo state into its strip key on every painted
#   frame, and while a typing burst was open that read compared the whole
#   source with the burst's start by joining it: ~44 KB a frame for a
#   1759-line cart, through every frame after a keystroke (a press on the
#   Code pane's symbol keys included) until the debounce closed the burst.
# * Each visible line printed its highlight runs as slices of the line, and
#   looked its runs up under a fresh (language, line) key: ~200 bytes a line,
#   every line, every frame. The rows are the text view's now, planned and
#   drawn in C inside `ui.text_view`.
#
# The first is measured here on the board-model MicroPython, over the module
# itself: it allocates nothing.

BURST_DRIVER = r'''
import sys, gc
sys.path.insert(0, @RUNTIME@)
from editors_code import CodeEditor
from history_router import HistoryRouter


class _Ws:
    menu_view = "code"


class _Project:
    def history_for(self, view):
        return router.code_op_history()


ws = _Ws()
ws.editor = CodeEditor("\n".join("x = %d  # a line of a long cart" % i
                                 for i in range(1500)))
ws.project = _Project()
router = HistoryRouter(ws, None)
idle = router.bar_undo_bits()
router.code_burst_open()
ws.editor.key(ord("."))
typed = router.bar_undo_bits()
read = router.bar_undo_bits
gc.collect()
gc.disable()
n = 0
m0 = gc.mem_alloc()
while n < 50:
    read()
    n += 1
print("RESULT", idle, typed, gc.mem_alloc() - m0)
'''

def _mp_result(tmp_path, driver, why):
    exe = require_unix_mp(board_model=True, why=why)
    script = tmp_path / "driver.py"
    script.write_text(driver.replace("@RUNTIME@", repr(str(ROOT / "runtime"))))
    out = subprocess.run([exe, "-X", "heapsize=16M", str(script)],
                         capture_output=True, text=True, timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, out.stdout + out.stderr
    return [int(v) for v in line[0].split()[1:]]


def test_the_bar_undo_read_allocates_nothing_through_a_typing_burst(tmp_path):
    idle, typed, spent = _mp_result(
        tmp_path, BURST_DRIVER,
        "The bar's undo read runs on every painted Editor frame; only the "
        "board-model MicroPython measures what it allocates.")
    assert (idle, typed) == (0, 1), "the burst's edit does not light the bar's undo"
    assert spent == 0, "50 bar undo reads through a typing burst allocated %d bytes" % spent


def test_the_blocks_outlines_moving_frames_allocate_nothing(result):
    """The Blocks outline, a table of parts: a frame whose cursor moved a
    row draws, and the page's draw allocates no more than the table's
    answer; a swipe's frames shift, and the page's draw in each
    allocates no more than that."""
    info, text = result
    tab = "blocks"
    assert info.get(tab + "_view") == tab, text[-3000:]
    walk = info[tab + "_walk"]
    assert sum(1 for d in walk if d[1]) > len(walk) // 2, (tab, walk)
    # The page's draw allocates no more than the table's answer a frame
    # (the Editor's frame around it is not the table's to pin).
    assert info[tab + "_page"] <= ANSWER * len(walk), (tab, info[tab + "_page"], walk)
    drawn = [d for d in info[tab + "_drag"] if d[1]]
    shifted = [d for d in drawn if d[2]]
    assert len(shifted) > len(drawn) // 2, (tab, len(shifted), len(drawn))
    over = [d for d in drawn if d[0] > ANSWER]
    assert len(over) <= 1, (tab, over)


def test_a_splits_divider_drag_allocates_nothing(result):
    """ui.split (docs/ui_blocks_2026-10.md section 4.2): no frame of a
    divider's press, drag and let-go allocates, the drag lands in `sizes`,
    and ui.part_at answers a tap on a part in a pane, allocating its
    answer alone on the frame it answers."""
    info, text = result
    assert info.get("split_sizes") == [140, info["split_sizes"][1]], text[-3000:]
    assert info["split"] == [0] * len(info["split"]), info["split"]
    assert info["split_focus"] == 1, "the tap in the second pane focused it"
    at = info["part_at"]
    assert any(act and got for _b, act, got in at), at
    assert all(b == 0 for b, _act, got in at if not got), at
    assert all(b <= ANSWER for b, _act, got in at if got), at
