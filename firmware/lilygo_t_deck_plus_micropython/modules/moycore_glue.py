"""The host half of moycore (stage 2): what the frame loop does around tick().

`LuaCartRun` next door registers ~40 Python closures as Lua globals and the
cart calls back into Python hundreds of times a frame. `MoycoreRun` registers
nothing: libmoy's own binding installs the whole verb table as C functions, and
this class does the three things that cannot live in C --

  * REFRESH the input snapshot before the tick. Buttons, time, the pointer and
    the last typed key are written into one `array("i")`; the cart's btn() is
    then an array read on the C side of the wall, however many times it asks.
  * DRAIN the audio queue after it, through the SAME `make_api` closures a
    Python cart uses. That is deliberate: sfx/music semantics (bank sync, the
    volume model the Settings surface reads, the diag triggers) stay in one
    place, and what the crossing deletes is the per-CALL trip, not the
    behaviour. Order is preserved because the queue is a queue.
  * PERSIST pmem at boundaries. The C side owns 256 int32 slots with a dirty
    flag, which is the shape the device already deferred to (#66) -- RAM during
    play, written at exit, crash capture, workspace swap and the periodic
    frame-boundary save.

Everything else the shell needs from a run -- `init`/`update`/`draw`/`close` --
has the same shape `LuaCartRun` exposes, so `Player` needs no branch.

WHAT THIS DOES NOT DO, and how you can tell: moybyte's superset verbs
(make_layer/draw_layer/image, scenes, tables, texts, view) are not in libmoy's
table, so a cart calling one gets a Lua error rather than silently drawing
nothing. `supports()` reports that up front by scanning the source, so the
caller can fall back to `LuaCartRun` for those carts instead of failing at
frame one. The cart census behind that decision is in the plan (6.0).
"""

from array import array

try:
    import moycore as _moycore
except ImportError:                      # a build without the module
    _moycore = None

# The verbs libmoy's binding does NOT install. A cart using any of them runs on
# the old path; see the module docstring.
SUPERSET = ("make_layer", "draw_layer", "image", "scene", "load_scene",
            "actors", "touching", "move_actor", "move_actor_to",
            "remove_actor", "draw_scene", "table", "text", "view",
            "spr_batch", "rect_batch", "spans", "mouse", "background",
            "col", "on_net", "wifi", "net")

# moy_button bit positions, SPEC.md 7.1 order. The snapshot packs them into one
# int; this is the only place the two orders meet, so it is a table rather than
# an assumption spread across the file.
BUTTONS = ("left", "right", "up", "down", "a", "b", "run", "home")


def _calls(src, name):
    """True when `src` CALLS `name` -- the bare word followed by "(".

    A hand scan, not a regex, for two reasons. MicroPython's `re` has no
    lookbehind, so the obvious pattern raises on the device -- which it did:
    the gate worked on the host, threw on the board, and every cart fell back
    to the old runtime without a word. And a plain substring test (the version
    before that) matched `table.insert`, a variable named `col` and the letters
    "net" inside identifiers, disqualifying every cart in the tree. Both
    failures look identical from outside: the new path exists and nothing takes
    it.
    """
    n = len(name)
    i = src.find(name)
    while i >= 0:
        before = src[i - 1] if i else " "
        j = i + n
        while j < len(src) and src[j] == " ":
            j += 1
        if (j < len(src) and src[j] == "("
                and not (before.isalpha() or before.isdigit()
                         or before in "_.:")):
            return True
        i = src.find(name, i + 1)
    return False


def supports(src):
    """False when `src` uses a verb libmoy does not bind (see SUPERSET).

    A source scan, not a runtime probe, because the alternative is discovering
    it when the cart is already on screen. Erring toward the old path is right;
    erring so far that the new path is unreachable is a silent no-op, which is
    what both earlier versions of this did -- see _calls().
    """
    if _moycore is None:
        return False
    for name in SUPERSET:
        if _calls(src, name):
            return False
    return True


class MoycoreRun:
    """One cart run under moycore. Same shape as LuaCartRun."""

    # How much audio a single frame may queue before the rest is dropped. The
    # C side caps it too; this mirrors the constant so the array is the right
    # size rather than merely big.
    AUDIO_MAX = 32

    def __init__(self, ws, ns, src):
        if _moycore is None:
            raise RuntimeError("moycore is not in this build")
        self.ws = ws
        self.ns = ns
        self._dt = 0.0
        canvas = ws.canvas
        project = getattr(ws, "project", None)
        sheet = getattr(project, "sheet", None) if project is not None else None
        tilemap = getattr(project, "tilemap", None) if project is not None else None

        self.snap = array("i", bytearray(4 * _moycore.SNAP_LEN))
        self.aq = array("h", bytearray(2 * (1 + _moycore.AQ_SLOTS * self.AUDIO_MAX)))
        self.pmem_img = array("i", bytearray(4 * 256))

        pmem = getattr(ws, "pmem", None)
        if pmem is not None:
            cells = getattr(pmem, "cells", None)
            if cells is not None:
                for i in range(min(256, len(cells))):
                    self.pmem_img[i] = int(cells[i])

        # The wire table: what an index looks like in THIS canvas's buffer.
        # Read from the canvas rather than assumed, for the same reason
        # web_boot reports it to the page -- device_canvas picks canonical or
        # byte-swapped RGB565 from the panel it is talking to.
        wire = None
        try:
            import device_canvas
            wire = device_canvas._PAL565_WIRE_BUF
        except Exception:  # noqa: BLE001 -- no table: libmoy uses the spec palette
            wire = None

        cfg = ns.get("_moy_cfg") if hasattr(ns, "get") else None
        err = _moycore.run_begin(
            canvas._buf, canvas.w, canvas.h, wire,
            getattr(sheet, "pix", None),
            getattr(tilemap, "cells", None),
            getattr(tilemap, "w", 0) or 0, getattr(tilemap, "h", 0) or 0,
            self.snap, self.aq, self.pmem_img, cfg, src, "@cart")
        if err:
            try:
                _moycore.close()
            finally:
                raise RuntimeError(err)

        self.snap[_moycore.SNAP_PLAYERS] = 1
        # init already ran inside run_begin (libmoy's moy_lua_init), so the
        # Player's `lua.init()` step has nothing left to do.
        self.init = None
        self.update = self._update
        self.draw = None                 # tick() drew; see _update

    # -- the frame ----------------------------------------------------------

    def _refresh(self):
        s = self.snap
        ws = self.ws
        inp = ws.input
        held = pressed = 0
        for i in range(len(BUTTONS)):
            name = BUTTONS[i]
            try:
                if inp.held(name):
                    held |= 1 << i
                if inp.pressed(name):
                    pressed |= 1 << i
            except Exception:  # noqa: BLE001 -- a missing button is not held
                pass
        s[_moycore.SNAP_BTN] = held
        s[_moycore.SNAP_BTNP] = pressed
        try:
            from device_util import _ticks_ms, _ticks_diff
            s[_moycore.SNAP_TIME_MS] = _ticks_diff(_ticks_ms(), inp.cart_start_ms)
        except Exception:  # noqa: BLE001
            pass
        # The pointer, in the cart's own coordinates. touch() reads nil when
        # down is 0, which is what "no pointer" means in SPEC.md 7.3.
        t = getattr(inp, "touch_state", None)
        if t is not None:
            try:
                x, y, down, ms = t()
                s[_moycore.SNAP_TOUCH_X] = int(x)
                s[_moycore.SNAP_TOUCH_Y] = int(y)
                s[_moycore.SNAP_TOUCH_DOWN] = 1 if down else 0
                s[_moycore.SNAP_TOUCH_MS] = int(ms)
            except Exception:  # noqa: BLE001
                s[_moycore.SNAP_TOUCH_DOWN] = 0
        s[_moycore.SNAP_KEY] = int(getattr(inp, "last_key", 0) or 0)

    def _drain_audio(self):
        n = self.aq[0]
        if n <= 0:
            return
        self.aq[0] = 0
        ns = self.ns
        slots = _moycore.AQ_SLOTS
        for i in range(n):
            p = 1 + i * slots
            op = self.aq[p]
            a, b = self.aq[p + 1], self.aq[p + 2]
            try:
                if op == _moycore.AQ_SFX:
                    ns["sfx"](a, None if b < 0 else b)
                elif op == _moycore.AQ_MUSIC:
                    ns["music"](a, bool(b))
                elif op == _moycore.AQ_BEEP:
                    ns["beep"](a, b / 1000.0)
                elif op == _moycore.AQ_MUSIC_STOP:
                    ns["music_stop"]()
                elif op == _moycore.AQ_SOUND_STOP:
                    ns["sound_stop"](None if a < 0 else a)
                elif op == _moycore.AQ_VOLUME:
                    ns["volume"](a)
            except Exception:  # noqa: BLE001 -- one bad command is not the frame
                pass

    def _update(self, dt):
        """The whole cart frame. `draw` is None because this already drew: the
        C loop runs _update and _draw back to back, which is the point."""
        self._refresh()
        # A tier that swaps framebuffers per frame (the P4's ping-pong, the
        # T-Deck's bounce) must re-point the canvas, exactly as
        # DeviceCanvas.sync_back does for the Python lanes.
        buf = self.ws.canvas._buf
        if buf is not self._last_buf():
            _moycore.retarget(buf)
            self._buf = buf
        err = _moycore.tick(dt)
        self._drain_audio()
        if err:
            raise RuntimeError(err)

    _buf = None

    def _last_buf(self):
        return self._buf

    # -- exit ---------------------------------------------------------------

    def flush_pmem(self):
        """Write the C-side pmem image back into the console's Pmem, if it
        moved. Called at the same boundaries the deferred save already uses."""
        if _moycore is None or not _moycore.active():
            return False
        if not _moycore.pmem_image(self.pmem_img):
            return False
        pmem = getattr(self.ws, "pmem", None)
        if pmem is None:
            return False
        cell = getattr(pmem, "cell", None)
        if cell is None:
            return False
        for i in range(256):
            try:
                cell(i, int(self.pmem_img[i]))
            except Exception:  # noqa: BLE001
                break
        return True

    def close(self):
        try:
            self.flush_pmem()
        finally:
            if _moycore is not None:
                _moycore.close()


def make_moycore_runtime(ws):
    """The `ws.lua_runtime`-shaped factory, or None when unavailable."""
    if _moycore is None:
        return None

    def _make(ns, src):
        return MoycoreRun(ws, ns, src)
    return _make
