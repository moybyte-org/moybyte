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
boot spine, the FrameLoop, the Workstation, the Player, `moycore_glue`'s
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

WHEN IT FAILS. Something on the frame path allocates again, and the per-frame
deltas are printed. What names the LINE is a MicroPython whose gc_alloc and
gc_free charge each block to the Python line running (the code state's ip,
with MICROPY_PY_SYS_SETTRACE for the current-code-state pointer and its frame
objects switched off); the commit that added this file says how it was built.
"""

import json
import shutil
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
    # stands in for the engine), hand over ONE re-aimed frame view, and serve a
    # store read through the board's gate every few ticks -- Doom streams its
    # WAD at about eleven reads a second.
    "moycore_shim.py": '''
_frame = bytearray(320 * 240)
_view = memoryview(_frame)
_state = [False]
_real = [None]
_gate = [None]
_ticks = [0]
READ_EVERY = 3


def _read_now():
    return None


def run_begin(fb, w, h, wire, sheet, cells, mw, mh, snap, aq, pmem, cfg, flags,
              vm):
    return _real[0].run_begin(fb, w, h, wire, sheet, cells, mw, mh, snap, aq,
                              pmem, cfg, flags, True)


def wasm_open(module, head, pages, sha, path, swapped, gate=None,
              allow_unsigned=False, interp=False, writable=None, files=None):
    _gate[0] = gate
    return None


def tick(dt, draw=True):
    err = _real[0].tick(dt, draw)
    g = _gate[0]
    if g is not None:
        n = _ticks[0] + 1
        _ticks[0] = n
        if n % READ_EVERY == 0:
            g(_read_now)
    return err


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


def _frames(loop, ms, held):
    import fake_machine
    fake_machine.KBD[0] = 0x02 if held else 0      # "w": the up button
    from gc import mem_alloc
    out = []
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
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
        if not any(d and time.ticks_diff(d, now) > 0 for d in (
                ws._toast_until, ws._confetti_until, ws._egg_until)):
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
    info["idle"] = _frames(loop, @WINDOW_MS@, False)
    _frames(loop, 100, True)             # the press edge itself may allocate
    info["held"] = _frames(loop, @WINDOW_MS@, True)
    gc.enable()
    print("RESULT " + json.dumps(info))


class _Measured(Exception):
    pass


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
        import frame_loop
        import moy_runtime

        def _load(self, boot, store):
            carts, root = boot.load_carts(store, [], root=ROOT + "/carts",
                                          media="SD")
            self.on_sd = True                # the T-Deck's store is its card
            return carts, root, None

        def _nosleep(ms):
            return None

        def _run(self):
            measure(self)
            raise _Measured()

        moy_runtime._Storage.load = _load
        moy_runtime.POWER_SAVE_MS = 0
        frame_loop._sleep_ms = _nosleep     # the loop's pacing; nothing else
        frame_loop.FrameLoop.run = _run
        moy_runtime.run_desktop()
    except _Measured:
        pass
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


def _stage(dest, exe):
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


def _run(tmp_path):
    exe = require_unix_mp(
        "moycore", "moy_gfx", "moy_audio", board_model=True,
        why="This is the only check that a play frame allocates nothing on "
            "the boards' Python heap. A board pays for garbage in whole-heap "
            "collections that stop the frame and starve a compiled cart's "
            "sound; nothing else in the suite measures a frame's allocation.")
    _stage(tmp_path, exe)
    script = tmp_path / "driver.py"
    script.write_text(DRIVER.replace("@ROOT@", repr(str(tmp_path)))
                      .replace("@WINDOW_MS@", str(WINDOW_MS)))
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


def _assert_quiet(deltas, what, text):
    assert len(deltas) > 20, "%s: only %d frames measured" % (what, len(deltas))
    spent = [d for d in deltas if d]
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
