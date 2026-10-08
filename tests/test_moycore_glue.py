"""`device/moycore_glue.py`, EXECUTED (#208's closing residue).

The largest untested SHARED body left in the tree: staged to all three boards
AND the wasm head, and until this file its host coverage was zero -- coverage
reported the module "was never imported". The one lane that ran it,
`tests/test_semantic_traces.py`, drives it inside a desktop MicroPython
subprocess, so it needs `make unix-micropython`, it SKIPS without one, and
nothing it proves is visible to a host coverage sweep. That is the #208 shape:
a body promoted so four consumers can share it, guarded by greps
(`test_board_routing`, `test_moy_button_order`, `test_streaming_sunset`)
that read source text.

Nothing here is transcribed. The real file is loaded and executed against a
fake `moycore` whose CONSTANTS AND VERB NAMES ARE PARSED OUT OF
`native/moycore/modmoycore.c` -- its `moycore_globals_table`, the two
snapshot/audio enums, `AQ_SLOTS`/`AQ_MAX`, and `run_begin`'s arity. So the
double cannot drift from the module it stands in for: a `SNAP_*` slot renamed
in C makes the glue's read raise AttributeError here, and a `run_begin` that
grows an argument fails every construction in this file. That inversion is the
point -- an ABI mismatch between these two bodies is invisible on a host and
presents on glass as a cart that will not start.

NOT reachable from a host, and named rather than faked into looking covered:

* libmoy itself. `tick()` running `_update` and `_draw` in C, the `rnd` seed
  (`RUN.con.rng = mp_hal_ticks_us()` in `run_begin`, the fix for every run of
  every cart drawing the same sequence) and the p8 shim's `__moy_map_masked` /
  `__moy_map_flags` globals all live on the far side of `run_begin`, where a
  fake module is by definition the wrong instrument. `tests/test_moycore_loop.py`
  owns those under the real VM; what is testable here is that the glue calls
  `run_begin` with the shape the C demands, and that is what is pinned.
* the SRAM-floor knob, which is `run_desktop`'s (`moycore.set_sram_floor`), not
  this file's -- pinned by `test_board_routing`'s boot-path check.
* the frame COST the docstrings quote (~1ms of per-frame `_refresh` on the S3).
  Timing is glass work; the structure that bought it -- one `button_masks`
  call instead of sixteen, slot numbers bound once, no per-frame import -- is
  observable here and is what these tests assert.

Mutation-checked per #208: 69 perturbations of the glue and 13 of the shared
`runtime/lua_ext.py` beside it (whose handle registry these tests are the only
host execution of), 82 red, no survivors.
"""

import ast
import importlib.util
import os
import re
import sys
import types
from pathlib import Path

import pytest

from tools.wasm_module import format_version

ROOT = Path(__file__).resolve().parent.parent
GLUE_SRC = ROOT / "device" / "moycore_glue.py"
C_SRC = ROOT / "native" / "moycore" / "modmoycore.c"
# The console half's ABI -- the snapshot's slots and the audio queue's ops --
# lives in moycore_run.h, the one copy every tier compiles.
RUN_H = ROOT / "native" / "moycore" / "moycore_run.h"
# The second ABI this file's parser is pointed at: the native draw gates and
# the shape kernel, whose enums device/device_canvas.py mirrors by hand.
GFX_SRC = ROOT / "native" / "moy_gfx" / "modmoy_gfx.c"
GFX_KERNELS = ROOT / "native" / "moy_gfx" / "moy_gfx_kernels.h"
CANVAS_SRC = ROOT / "device" / "device_canvas.py"


# -- the C side, parsed --------------------------------------------------------


def _c_text(path=None):
    src = (path or C_SRC).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _c_enum(first, path=None):
    """The enum block that starts with `first`, as {name: value}."""
    text = _c_text(path)
    for block in re.findall(r"enum\s*\{(.*?)\}\s*;", text, flags=re.S):
        if not re.search(r"\b%s\b" % first, block):
            continue
        out, nxt = {}, 0
        for item in block.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                name, val = item.split("=", 1)
                nxt = int(val.strip(), 0)
                item = name.strip()
            out[item] = nxt
            nxt += 1
        return out
    raise AssertionError("no enum containing %s in %s" % (first, path or C_SRC))


def _c_define(name, path=None):
    m = re.search(r"^#define\s+%s\s+(\d+)" % name, _c_text(path), flags=re.M)
    assert m, "%s is not #defined in %s" % (name, path or C_SRC)
    return int(m.group(1))


def _c_module_names():
    """Every name `import moycore` exposes, from `moycore_globals_table`."""
    text = _c_text()
    body = text[text.index("moycore_globals_table[]"):]
    body = body[:body.index("};")]
    return {n for n in re.findall(r"MP_ROM_QSTR\(MP_QSTR_(\w+)\)", body)
            if n != "__name__"}


def _c_run_begin_arity():
    m = re.search(r"MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN\(mod_run_begin_obj,"
                  r"\s*(\d+),\s*(\d+),", _c_text())
    assert m, "run_begin's arity is not declared the way this parser reads it"
    assert m.group(1) == m.group(2), "run_begin took optional args"
    return int(m.group(1))


def _c_run_begin_fields():
    """The parameter names from the C's own `run_begin(...)` header comment."""
    src = C_SRC.read_text(encoding="utf-8")
    m = re.search(r"// run_begin\((.*?)\)\s*\n//\s*\n", src, flags=re.S)
    assert m, "run_begin's header comment no longer names its parameters"
    return tuple(p.strip() for p in m.group(1).replace("//", " ").split(","))


C_CONSTS = dict(_c_enum("SNAP_BTN", RUN_H))
C_CONSTS.update(_c_enum("AQ_SFX", RUN_H))
C_CONSTS["AQ_SLOTS"] = _c_define("AQ_SLOTS", RUN_H)
C_CONSTS["AQ_MAX"] = _c_define("AQ_MAX", RUN_H)
C_NAMES = _c_module_names()
RB_ARITY = _c_run_begin_arity()
RB_FIELDS = _c_run_begin_fields()


# -- the doubles ---------------------------------------------------------------


class FakeMoycore(types.ModuleType):
    """`moycore`, with the C's own constant table and nothing invented.

    Only the names `moycore_globals_table` exports are set, so a glue that
    reaches for a slot the module does not have fails here the way it would on
    a board -- which is the drift this double exists to catch.
    """

    def __init__(self):
        super().__init__("moycore")
        for name in C_NAMES:
            if name in C_CONSTS:
                setattr(self, name, C_CONSTS[name])
        self.calls = []
        self.registered = {}
        self.run_begin_args = None
        self.retargets = []
        self.exec_err = None
        self.load_err = None
        self.tick_err = None
        self.register_error = None
        self.view_value = None
        self.split = (1500, 2500)
        self.is_active = True
        self.pmem_image_result = True
        self.pmem_image_fill = None
        self.closes = 0
        self.wasm_open_err = None
        # A retry (docs/wasm_tier_plan_2026-09.md, "A cart survives its
        # firmware") calls wasm_open a second time; set this to a list to
        # answer each call in turn instead of the same wasm_open_err always.
        self.wasm_open_errs = None
        self.owed_frame = None
        self.owed_lut = None
        self.kept = False
        for verb in ("run_begin", "register", "exec", "load", "tick",
                     "tick_split", "pmem_image", "retarget", "close",
                     "active", "view", "set_sram_floor", "alloc_stats",
                     "get_global", "wasm_open", "take_frames", "frame",
                     "frame_settle", "frame_presented", "frame_kept",
                     "layer_bind"):
            assert verb in C_NAMES, verb
            setattr(self, verb, getattr(self, "_" + verb))

    def _log(self, verb, *args):
        self.calls.append((verb,) + args)

    def verbs(self):
        return [c[0] for c in self.calls]

    def _run_begin(self, *a):
        if len(a) != RB_ARITY:
            raise TypeError("run_begin: %d args" % RB_ARITY)
        self.run_begin_args = a
        self._log("run_begin")

    def rb(self, field):
        """One `run_begin` argument, by the C's own parameter name."""
        assert self.run_begin_args is not None, "run_begin never ran"
        return self.run_begin_args[RB_FIELDS.index(field)]

    def _register(self, name, fn):
        if self.register_error is not None and name == self.register_error[0]:
            raise self.register_error[1]
        self.registered[name] = fn
        self._log("register", name)

    def _layer_bind(self, buf, w, h):
        self._log("layer_bind", w, h)

    def _exec(self, src, chunk):
        self._log("exec", src, chunk)
        return self.exec_err

    def _load(self, chunks):
        # SPEC.md 4: the WHOLE cart in one call -- a list of (src, chunkname).
        self._log("load", list(chunks))
        return self.load_err

    def _tick(self, dt):
        self._log("tick", dt)
        return self.tick_err

    def _tick_split(self, out=None):
        self._log("tick_split")
        if out is None:
            return self.split
        out[0], out[1] = self.split
        return out

    def _pmem_image(self, arr):
        self._log("pmem_image")
        for i, v in (self.pmem_image_fill or {}).items():
            arr[i] = v
        return self.pmem_image_result

    def _retarget(self, buf):
        self.retargets.append(buf)
        self._log("retarget")

    def _close(self):
        self.closes += 1
        self._log("close")

    def _active(self):
        return self.is_active

    def _view(self):
        return self.view_value

    def _set_sram_floor(self, kb):
        self._log("set_sram_floor", kb)

    def _alloc_stats(self):
        return ()

    def _get_global(self, name):
        return None

    def _wasm_open(self, *a):
        self._log("wasm_open", *a)
        if self.wasm_open_errs is not None:
            return self.wasm_open_errs.pop(0) if self.wasm_open_errs else None
        return self.wasm_open_err

    def _take_frames(self, on, palette=True):
        self._log("take_frames", on, palette)

    def _frame(self, lut_out):
        self._log("frame")
        if self.owed_lut is not None:
            memoryview(lut_out).cast("B")[:] = self.owed_lut
        return self.owed_frame

    def _frame_settle(self):
        self._log("frame_settle")

    def _frame_presented(self, kept=None, off=0):
        self._log("frame_presented", kept, off)

    def _frame_kept(self):
        return self.kept


class Clock:
    """`ticks._since_ms`, injected -- no wall clock anywhere."""

    def __init__(self, ms=0):
        self.ms = ms

    def since_ms(self, start):
        return self.ms - start


class FakeCanvas:
    def __init__(self, w=320, h=240, wire=None):
        self.w = w
        self.h = h
        self._buf = bytearray(w * h * 2)
        if wire is not None:
            self._wire = wire

    def swap(self):
        """A tier that ping-pongs framebuffers (P4 DPI, T-Deck bounce)."""
        self._buf = bytearray(self.w * self.h * 2)
        return self._buf


class FakeSheet:
    def __init__(self):
        self.pix = bytearray(128 * 128)


class FakeTilemap:
    def __init__(self, w=16, h=16):
        self.w = w
        self.h = h
        self.cells = bytearray(w * h)


class FakeProject:
    def __init__(self, sheet=None, tilemap=None, flags=None):
        self.sheet = sheet
        self.tilemap = tilemap
        self.flags = flags


_UNSET = object()


class FakePmem:
    def __init__(self, cells=_UNSET):
        self.cells = [0] * 256 if cells is _UNSET else cells
        self.written = []

    def cell(self, i, v):
        self.written.append((i, v))
        self.cells[i] = v


class FakePlayers:
    def __init__(self, n=1, held=0, pressed=0):
        self.n = n
        self.held = held
        self.pressed = pressed
        self.mask_calls = []

    def count(self):
        return self.n

    def button_masks(self, order, player):
        self.mask_calls.append((order, player))
        return self.held, self.pressed


class FakeInput:
    """The InputState surface `_refresh` reads, with every arm optional.

    Deliberately not a subclass of either real InputState: the fallback lane
    exists precisely because the glue meets input objects it has never heard
    of, and the two real classes are exercised against it below.
    """

    def __init__(self, **kw):
        self.cart_start_ms = 0
        self.last_key = 0
        self.game_view = None
        self.view_writes = []
        self.mask_calls = []
        self._held = set()
        self._pressed = set()
        self.touch = None
        self.touch_error = None
        for k, v in kw.items():
            setattr(self, k, v)

    def button_masks(self, order, player=None, out=None):
        self.mask_calls.append(order)
        h = p = 0
        for i, name in enumerate(order):
            if name in self._held:
                h |= 1 << i
            if name in self._pressed:
                p |= 1 << i
        if out is None:
            return h, p
        out[0], out[1] = h, p
        return out

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in self._pressed

    @property
    def pointer(self):
        """What `widgets.pointer_state` actually reads.

        This fake used to expose a `touch_state()` method, which was the glue's
        old seam -- a method on InputState. There are TWO InputStates (the
        host's and `device/moybyte/input.py`'s) and only one of them ever grew
        it, so the boards got no pointer while every host test passed. The glue
        asks the resolver directly now, so the fake supplies a POINTER.
        """
        if self.touch_error is not None:
            raise self.touch_error
        if self.touch is None:
            return None
        return _FakePointer(*self.touch)


class _FakePointer:
    """x/y/state/ms as `pointer_state` wants to read them off a Pointer."""

    def __init__(self, x, y, state, ms):
        from runtime.moy_input import P_CLICK, P_HELD, P_LIVE

        self.x, self.y, self.ms = x, y, ms
        self._state = state
        self.down = bool(state & P_HELD)
        self.click = bool(state & P_CLICK)
        self._live = bool(state & P_LIVE)

    def live(self):
        return self._live


class MinimalInput:
    """"an input object this file has never heard of" -- the guard's own words.

    No `button_masks`, no `players`, no `touch_state`: every getattr default in
    `_refresh` at once.
    """

    cart_start_ms = 0
    last_key = 0
    game_view = None

    def __init__(self, held=(), pressed=()):
        self._held = set(held)
        self._pressed = set(pressed)

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in self._pressed


class _ViewRecordingInput(FakeInput):
    """`ws.input.game_view` is a plain attribute; this counts the writes so
    `_sync_view`'s skip-when-unchanged is observable rather than inferred."""

    def __setattr__(self, name, value):
        if name == "game_view":
            self.__dict__.setdefault("view_writes", []).append(value)
        object.__setattr__(self, name, value)


class FakeWs:
    def __init__(self, canvas=None, inp=None, project=None, pmem=None):
        self.canvas = canvas if canvas is not None else FakeCanvas()
        self.input = inp if inp is not None else FakeInput()
        if project is not None:
            self.project = project
        if pmem is not None:
            self.pmem = pmem


class NsSession:
    """The run's audio session as the drain sees it: each verb is the
    namespace's recorder of that name, looked up per call so a test can swap
    one."""

    def __init__(self, ns):
        self._ns = ns

    def __getattr__(self, verb):
        return lambda *a: self._ns[verb](*a)


def make_ns(**extra):
    """A cart api namespace shaped like `make_api`'s: the audio closures the
    drain calls, a few libmoy verbs that must NOT be re-registered, and the
    object-valued trio that rides the handle registry."""
    log = []
    ns = {
        "sfx": lambda *a: log.append(("sfx",) + a),
        "music": lambda *a: log.append(("music",) + a),
        "beep": lambda *a: log.append(("beep",) + a),
        "music_stop": lambda *a: log.append(("music_stop",) + a),
        "sound_stop": lambda *a: log.append(("sound_stop",) + a),
        "volume": lambda *a: log.append(("volume",) + a),
        "spr": lambda *a: None,
        "cls": lambda *a: None,
        "rnd": lambda *a: None,
        "scene": lambda *a: log.append(("scene",) + a),
        "draw_scene": lambda *a: log.append(("draw_scene",) + a),
        "text": lambda *a: log.append(("text",) + a),
        "make_layer": lambda w, h: FakeLayer(w, h, log),
        "draw_layer": lambda lay, cx, cy: log.append(("draw_layer", lay, cx, cy)),
        "image": lambda name: ("img", name) if name != "missing" else None,
        "Image": FakeLayer,
        "_moy_cfg": {"speed": 3},
    }
    ns.update(extra)
    ns["_log"] = log
    return ns


class FakeLayerCanvas:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self._buf = bytearray(w * h * 2)


class FakeLayer:
    """A layer as the glue sees it: the canvas whose pixels it binds, and the
    one Python-side draw a Lua layer still makes (a paint image)."""

    def __init__(self, w, h, log=None):
        self.w = w
        self.h = h
        self._canvas = FakeLayerCanvas(w, h)
        self.log = [] if log is None else log

    def spr(self, *a):
        self.log.append(("layer.spr", self) + a)


LUA_SRC = "function _update() end"


class World:
    """A freshly executed `moycore_glue` over a fresh fake `moycore`.

    Re-loaded per test because `_moycore` and `_since_ms` are MODULE globals
    bound at import: a leaked module would make the second test in a file
    exercise the first one's board.
    """

    NAMES = ("moycore", "ticks", "device_canvas", "lua_ext", "moy_wasm")

    def __init__(self, moycore=True, flat_ticks=True, flat_lua_ext=True,
                 wire_fallback=b"\1" * 128, wasm_chip=None):
        self.saved = {n: sys.modules.get(n, KeyError) for n in self.NAMES}
        if wasm_chip is None:
            sys.modules["moy_wasm"] = None     # no engine in this build
        else:
            mw = types.ModuleType("moy_wasm")
            mw.CHIP = wasm_chip
            mw.FORMAT = format_version()
            sys.modules["moy_wasm"] = mw
        if not flat_lua_ext:
            sys.modules["lua_ext"] = None      # no frozen flat name: the host
        self.clock = Clock()
        self.core = FakeMoycore() if moycore else None
        if moycore:
            sys.modules["moycore"] = self.core
        else:
            sys.modules["moycore"] = None      # PEP 328: raises ImportError
        if flat_ticks:
            tk = types.ModuleType("ticks")
            tk._since_ms = self.clock.since_ms
            sys.modules["ticks"] = tk
        else:
            sys.modules["ticks"] = None        # the host: runtime.ticks
        if wire_fallback is not None:
            dc = types.ModuleType("device_canvas")
            dc._PAL565_WIRE_BUF = wire_fallback
            dc.PAL565 = (0, 0xF800)
            dc.PAL565_WIRE = (0, 0x00F8)       # a byte-swapped panel
            sys.modules["device_canvas"] = dc
        else:
            sys.modules["device_canvas"] = None
        spec = importlib.util.spec_from_file_location(
            "moycore_glue_under_test", GLUE_SRC)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def run(self, ws=None, ns=None, src=LUA_SRC):
        self.ws = FakeWs() if ws is None else ws
        self.ns = make_ns() if ns is None else ns
        if getattr(self.ws, "audio", None) is None:
            self.ws.audio = NsSession(self.ns)
        return self.mod.MoycoreRun(self.ws, self.ns, src)

    def close(self):
        for name, prev in self.saved.items():
            if prev is KeyError:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


@pytest.fixture
def w():
    world = World()
    try:
        yield world
    finally:
        world.close()


# -- the module is in this build, or it is not ---------------------------------


def test_a_build_without_the_module_yields_no_runtime_rather_than_an_error():
    """`device_boot.runtimes` prints "lua runtime ABSENT" off this None and
    a `"runtime": "lua"` cart opens the Player's runtime-missing panel."""
    world = World(moycore=False)
    try:
        assert world.mod._moycore is None
        assert world.mod.make_moycore_runtime(FakeWs()) is None
    finally:
        world.close()


def test_constructing_a_run_without_the_module_says_so():
    world = World(moycore=False)
    try:
        with pytest.raises(RuntimeError, match="not in this build"):
            world.mod.MoycoreRun(FakeWs(), make_ns(), LUA_SRC)
    finally:
        world.close()


def test_the_factory_binds_the_workstation_and_takes_ns_and_src(w):
    ws = FakeWs()
    factory = w.mod.make_moycore_runtime(ws)
    run = factory(make_ns(), LUA_SRC)
    assert isinstance(run, w.mod.MoycoreRun)
    assert run.ws is ws


# -- run_begin: the ABI the C enforces -----------------------------------------


def test_run_begin_is_called_with_the_arity_the_c_demands(w):
    """`mod_run_begin` raises TypeError on anything but exactly this count, so
    an argument added on one side of the wall is a cart that will not start."""
    w.run()
    assert len(w.core.run_begin_args) == RB_ARITY == len(RB_FIELDS)


def test_run_begin_receives_the_live_framebuffer_and_its_dimensions(w):
    canvas = FakeCanvas(w=200, h=100)
    w.run(ws=FakeWs(canvas=canvas))
    assert w.core.rb("fb") is canvas._buf
    assert (w.core.rb("w"), w.core.rb("h")) == (200, 100)


def test_the_wire_table_comes_from_this_canvas_not_the_module_constant(w):
    """A cart shipping its own palette (SPEC.md 3.1) leaves the canvas holding
    a PRIVATE table; reading `device_canvas._PAL565_WIRE_BUF` here would draw
    every Lua verb in stock MOY64 while the Python verbs on the same canvas
    honoured the cart's."""
    private = bytearray(b"\2" * 128)
    w.run(ws=FakeWs(canvas=FakeCanvas(wire=private)))
    assert w.core.rb("wire") is private


def test_a_canvas_with_no_wire_table_falls_back_to_the_device_default(w):
    w.run(ws=FakeWs(canvas=FakeCanvas()))
    assert w.core.rb("wire") == b"\1" * 128


def test_no_wire_table_anywhere_passes_none_and_libmoy_uses_the_spec_palette():
    world = World(wire_fallback=None)
    try:
        world.run(ws=FakeWs(canvas=FakeCanvas()))
        assert world.core.rb("wire") is None
    finally:
        world.close()


def test_the_sheet_and_tilemap_cross_as_the_projects_own_buffers(w):
    sheet, tilemap = FakeSheet(), FakeTilemap(w=12, h=9)
    w.run(ws=FakeWs(project=FakeProject(sheet, tilemap)))
    assert w.core.rb("sheet_pix") is sheet.pix
    assert w.core.rb("map_cells") is tilemap.cells
    assert (w.core.rb("map_w"), w.core.rb("map_h")) == (12, 9)


def test_the_cart_tile_flags_cross_to_the_console(w):
    """SPEC.md 3.5's table, the 13th run_begin argument. Unlike the sheet and
    the map this one is COPIED on the C side, so what has to be right here is
    only that the project's 512 bytes are what run_begin is handed -- a glue
    that passed None instead leaves fget reading 0, map(..., layers) drawing
    nothing and the PICO-8 machine mirroring zeros into 0x3000, none of which
    raises anywhere."""
    flags = bytearray(512)
    flags[7] = 0x81
    w.run(ws=FakeWs(project=FakeProject(FakeSheet(), FakeTilemap(), flags)))
    assert w.core.rb("flags") is flags


def test_a_project_with_no_flags_file_passes_none_not_a_stub(w):
    """`None` is the C's signal to zero its own table. A glue that invented an
    empty bytearray here would be indistinguishable today and wrong the moment
    the C learns to tell 'no flags' from 'all zero'."""
    w.run(ws=FakeWs(project=FakeProject(FakeSheet(), FakeTilemap())))
    assert w.core.rb("flags") is None


def test_a_brand_new_project_has_no_sheet_and_no_map_and_says_so(w):
    """`moy_console` holds both by POINTER and libmoy's binding used to
    segfault on `spr(0,0,0)` in an empty cart -- a board reset with no
    message. None here is what makes the C leave `con.sheet`/`con.map` NULL."""
    w.run(ws=FakeWs(project=FakeProject()))
    assert w.core.rb("sheet_pix") is None
    assert w.core.rb("map_cells") is None
    assert (w.core.rb("map_w"), w.core.rb("map_h")) == (0, 0)


def test_a_workstation_with_no_project_at_all_still_starts(w):
    w.run(ws=FakeWs())
    assert w.core.rb("sheet_pix") is None
    assert w.core.rb("map_cells") is None
    assert w.core.rb("flags") is None


def test_the_cart_config_crosses_from_the_namespace(w):
    """As the C table the cart's cfg() reads: copied in, so nothing of the
    VM's is held by the run."""
    from runtime.lua_ext import cfg_blob
    ns = make_ns()
    w.run(ns=ns)
    assert w.core.rb("cfg") == (cfg_blob(ns["_moy_cfg"]) or None)


def test_the_snapshot_array_is_sized_from_the_c_layout(w):
    run = w.run()
    assert run.snap.typecode == "i"
    assert len(run.snap) == C_CONSTS["SNAP_LEN"]
    assert run.snap.buffer_info()[1] * run.snap.itemsize >= C_CONSTS["SNAP_LEN"] * 4
    assert w.core.rb("snap") is run.snap


def test_the_audio_queue_mirrors_the_c_cap_rather_than_merely_being_big(w):
    """`AUDIO_MAX` is a copy of the C's `AQ_MAX`; the queue is one header slot
    plus `AQ_SLOTS` int32 per command."""
    run = w.run()
    assert run.AUDIO_MAX == C_CONSTS["AQ_MAX"]
    assert run.aq.typecode == "i"
    assert len(run.aq) == 1 + C_CONSTS["AQ_SLOTS"] * C_CONSTS["AQ_MAX"]
    assert len(run.aq) >= 1 + C_CONSTS["AQ_SLOTS"]     # the C's own floor
    assert w.core.rb("audio_q") is run.aq


def test_pmem_crosses_as_256_int32_seeded_from_the_consoles_own_cells(w):
    cells = list(range(256))
    run = w.run(ws=FakeWs(pmem=FakePmem(cells)))
    assert run.pmem_img.typecode == "i"
    assert len(run.pmem_img) == 256
    assert list(run.pmem_img) == cells
    assert w.core.rb("pmem_bytes") is run.pmem_img


def test_a_short_pmem_seeds_what_it_has_and_a_long_one_is_clamped(w):
    run = w.run(ws=FakeWs(pmem=FakePmem([7, 8, 9])))
    assert list(run.pmem_img[:4]) == [7, 8, 9, 0]
    world = World()
    try:
        run = world.run(ws=FakeWs(pmem=FakePmem(list(range(400)))))
        assert len(run.pmem_img) == 256
        assert run.pmem_img[255] == 255
    finally:
        world.close()


def test_a_console_with_no_pmem_starts_from_zeroes(w):
    run = w.run(ws=FakeWs())
    assert set(run.pmem_img) == {0}


def test_a_pmem_holder_that_has_not_loaded_its_cells_starts_from_zeroes(w):
    run = w.run(ws=FakeWs(pmem=FakePmem(cells=None)))
    assert set(run.pmem_img) == {0}


# -- the superset: a DENY list, not an allow list ------------------------------


def _libmoy_installed_globals():
    """The global names libmoy's own binding installs, parsed from it."""
    src = (ROOT / "native" / "moycore" / "libmoy" / "moy_lua.c").read_text(
        encoding="utf-8")
    head = src.index("static const luaL_Reg VERBS[] = {")
    table = src[head:]
    table = table[:table.index("{NULL, NULL}")]
    names = set(re.findall(r'\{"(\w+)",', table))
    host = src[src.index("static void open_host_verbs"):head]
    # W and H are integers (SPEC.md 9's canvas size), not verbs.
    return names | set(re.findall(r'lua_setglobal\(L, "(\w+)"\)', host))


def test_the_deny_list_is_exactly_what_libmoys_binding_installs():
    """The list the registration loop subtracts is only as good as its
    contents, and every test below derives its namespace FROM it -- so this is
    what stops the whole group agreeing with a wrong list. A verb libmoy gains
    that this set does not learn gets a Python trampoline over its C function,
    silently; a name here that libmoy does NOT install stops moybyte
    registering its own and the cart calls nil."""
    from runtime.lua_ext import LIBMOY_VERBS, NOT_REGISTRABLE

    # make_layer/draw_layer became libmoy core in moy-spec b9dbba1 and moybyte
    # REPLACES them through the prelude -- theirs return nil with no Display
    # seam, ours are object-valued and composite. The one deliberate overlap,
    # which is why they sit in NOT_REGISTRABLE instead.
    overridden = {"make_layer", "draw_layer"}
    assert overridden <= NOT_REGISTRABLE
    installed = _libmoy_installed_globals()
    assert installed - overridden - LIBMOY_VERBS == set(), \
        "libmoy installs verbs LIBMOY_VERBS has not learnt"
    assert LIBMOY_VERBS - installed == set(), \
        "LIBMOY_VERBS names verbs libmoy does not install"


def test_a_moybyte_verb_nobody_remembered_is_registered_anyway(w):
    """The inversion #67 exists for. An allow list silently drops whatever was
    never added to it -- and did. A name neither list mentions must reach the
    cart, because an extra global costs one closure and a missing one is a
    nil-call crash."""
    ns = make_ns(brand_new_verb_2026=lambda: 42)
    w.run(ns=ns)
    assert "brand_new_verb_2026" in w.core.registered
    assert w.core.registered["brand_new_verb_2026"] is ns["brand_new_verb_2026"]
    for shared in ("draw_scene", "text"):
        assert shared in w.core.registered
    # ...and scene() is not one of them: it answers with a LIST of rows, so it
    # rides the handle glue instead (#214).
    assert "scene" not in w.core.registered


def test_libmoys_own_verbs_are_never_shadowed_by_a_trampoline(w):
    """Registering one would put a Python upcall over the C function, which is
    the opposite of the point of moycore."""
    from runtime.lua_ext import LIBMOY_VERBS

    ns = make_ns(**{v: (lambda *a: None) for v in sorted(LIBMOY_VERBS)})
    w.run(ns=ns)
    assert not (set(w.core.registered) & LIBMOY_VERBS)


def test_the_object_valued_verbs_are_never_registry_entries(w):
    """A trampoline marshals scalars and tuples, so a Layer comes back as
    "unsupported value"; these ride int handles plus the Lua prelude."""
    from runtime.lua_ext import NOT_REGISTRABLE

    w.run()
    assert not (set(w.core.registered) & NOT_REGISTRABLE)


def test_non_callable_namespace_entries_are_skipped(w):
    ns = make_ns(SOME_CONSTANT=7, some_table={"a": 1})
    w.run(ns=ns)
    assert "SOME_CONSTANT" not in w.core.registered
    assert "some_table" not in w.core.registered
    assert "_moy_cfg" not in w.core.registered


def test_the_glue_finds_the_shared_lists_with_or_without_the_frozen_flat_name():
    """A board freezes `lua_ext` flat; a host has only the `runtime` package.
    Both arms must bind the SAME objects -- the fallback is what makes this
    file, and every other host test of the device module, real."""
    from runtime import lua_ext

    world = World(flat_lua_ext=False)
    try:
        assert world.mod.LIBMOY_VERBS is lua_ext.LIBMOY_VERBS
        assert world.mod.MOY_BUTTONS is lua_ext.MOY_BUTTONS
        assert world.mod.install_handles is lua_ext.install_handles
        world.run()                        # and it still builds a run
    finally:
        world.close()


def test_the_deny_lists_are_the_shared_ones_and_not_a_local_copy(w):
    """They lived twice -- here and in `lua_host` -- with 46 names agreeing by
    hand and nothing comparing them."""
    from runtime import lua_ext

    assert w.mod.LIBMOY_VERBS is lua_ext.LIBMOY_VERBS
    assert w.mod.NOT_REGISTRABLE is lua_ext.NOT_REGISTRABLE
    assert w.mod.MOY_BUTTONS is lua_ext.MOY_BUTTONS


# -- the handle route (object-valued verbs) ------------------------------------


def test_the_prelude_runs_before_the_cart_and_after_the_registrations(w):
    """`moycore.register()` between `run_begin` and `load` IS the window a
    cart needs: it captures its globals into locals as it executes."""
    w.run()
    verbs = w.core.verbs()
    assert verbs[0] == "run_begin"
    assert verbs[-1] == "load"
    assert verbs.count("exec") == 1
    # EVERY registration, not merely the first: the prelude copies
    # `__layer_new` and its siblings into locals and then nils the
    # globals, so a handle registered after the exec is captured as nil and
    # `make_layer` dies on "attempt to call a nil value".
    last_register = max(i for i, v in enumerate(verbs) if v == "register")
    assert last_register < verbs.index("exec")


def test_the_prelude_is_the_shared_source_and_omits_the_fastmath_half(w):
    """`PRELUDE_FASTMATH` is moy_lua's alone: shadowing libmoy's C `rnd` with
    a Lua one is a pessimisation AND a semantic change -- libmoy's draws from
    the console rng the C seeds, which is the sequence SPEC.md 9 pins."""
    from runtime.lua_ext import PRELUDE_HANDLES, PRELUDE_FASTMATH

    w.run()
    src = [c[1] for c in w.core.calls if c[0] == "exec"][0]
    assert src == PRELUDE_HANDLES
    assert PRELUDE_FASTMATH not in src
    assert "function rnd(" not in src


def test_every_handle_the_prelude_consumes_is_registered(w):
    """The two halves are one source (`lua_ext`) precisely because a rename on
    one side is a layer cart dying on "index a nil value".

    The editor family (#112) is the one GATED set: `open_editor` rides the
    `files` permission, so its trampolines exist only for a cart that earned
    it and the prelude guards its whole block on their presence. Asserted in
    both states below rather than exempted, because "registered when granted"
    is the actual invariant and a rename would still break it."""
    from runtime.lua_ext import PRELUDE_HANDLES

    w.run()
    # Fields and a metamethod, not handles: a layer's id, canvas and edited
    # mark, an image's id. And the two layer natives are the RUNTIME's own C
    # (moycore_layers.h), installed by run_begin and hl_new, never registered.
    fields = {"__id", "__img", "__c", "__e", "__index"}
    natives = {"__layer_canvas", "__layer_verb"}
    wanted = set(re.findall(r"__\w+", PRELUDE_HANDLES)) - fields - natives
    gated = {n for n in wanted if n.startswith("__ed_")}
    assert gated, "the editor handles vanished from the prelude"
    got = {n for n in w.core.registered if n.startswith("__")}
    assert wanted - gated == got, "an UNGATED handle is missing"


def test_the_editor_handles_are_registered_for_a_cart_that_earned_them(w):
    from runtime.lua_ext import PRELUDE_HANDLES

    w.run(ns=make_ns(open_editor=lambda name=None, mode=None: None))
    wanted = set(re.findall(r"__ed_\w+", PRELUDE_HANDLES))
    assert wanted <= {n for n in w.core.registered if n.startswith("__")}


def test_a_layer_made_through_a_handle_is_pinned_by_the_run(w):
    run = w.run()
    reg = w.core.registered
    # Every argument arrives as a Lua NUMBER -- the boards build LUA_32BITS and
    # a tile index reaching the sheet as 7.0 is a TypeError, so the handle half
    # is where the coercion has to happen.
    lid = reg["__layer_new"](64.0, 32.0)
    assert lid == 0
    lay = run._layers[0]
    assert (lay.w, lay.h) == (64, 32)
    # Its pixels went to the run, which draws into them with libmoy's verbs.
    assert ("layer_bind", 64, 32) in w.core.calls
    reg["__draw_layer"](lid, 8, 9)
    assert ("draw_layer", lay, 8, 9) in w.ns["_log"]
    assert not getattr(lay._canvas, "_edited", False)
    # A layer libmoy drew into reaches draw_layer marked, so the console takes
    # no copy it predicted from the old pixels.
    reg["__draw_layer"](lid, 8, 9, True)
    assert lay._canvas._edited is True
    # By TYPE, not by value: `64.0 == 64`, so a comparison alone cannot see
    # the coercion being dropped.
    assert isinstance(lay.w, int) and isinstance(lay.h, int), (lay.w, lay.h)


def test_an_image_handle_indexes_the_runs_own_registry(w):
    run = w.run()
    h = w.core.registered["__image_handle"]("bg")
    assert h == 0 and run._images[0] == ("img", "bg")
    lid = w.core.registered["__layer_new"](8, 8)
    w.core.registered["__layer_spr_img"](float(lid), float(h), 1.0, 2.0)
    assert ("layer.spr", run._layers[0], ("img", "bg"), 1, 2) in w.ns["_log"]


def test_a_missing_image_answers_a_negative_handle_and_pins_nothing(w):
    run = w.run()
    assert w.core.registered["__image_handle"]("missing") == -1
    assert run._images == []


# -- a bad verb must not strand the VM -----------------------------------------


def test_a_register_that_raises_closes_the_vm_and_reraises(w):
    w.core.register_error = ("text", ValueError("bad verb"))
    with pytest.raises(ValueError):
        w.run()
    assert w.core.closes == 1
    assert "load" not in w.core.verbs()


def test_a_prelude_that_fails_closes_the_vm_and_names_the_error(w):
    w.core.exec_err = "prelude:3: syntax error"
    with pytest.raises(RuntimeError, match="syntax error"):
        w.run()
    assert w.core.closes == 1
    assert "load" not in w.core.verbs()


def test_a_cart_that_fails_to_load_closes_the_vm_and_names_the_error(w):
    w.core.load_err = "cart:12: unexpected symbol"
    with pytest.raises(RuntimeError, match="cart:12"):
        w.run()
    assert w.core.closes == 1


def test_the_cart_chunk_is_named_for_the_crash_to_code_panel(w):
    """`player._lua_cart_line` parses `cart:12:` out of a runtime error to put
    the caret on the failing line (#24); "@" is Lua's own source-name sigil."""
    w.run()
    load = [c for c in w.core.calls if c[0] == "load"][0]
    assert load[1] == [(LUA_SRC, "@cart")]


def test_the_whole_sources_list_goes_to_load_in_order(w):
    """SPEC.md 4: every script, in the manifest's order, in ONE load() call.

    Not exec()s followed by load(): load is where the verb profiler arms and,
    on the host tier, where the PICO-8 machine opens. A shim chunk run outside
    it captures the unwrapped verbs and resolves to the slow Lua fallbacks --
    both silent, and `verbs` is the only meter that sees this tier at all."""
    ns = make_ns()
    ns["_moy_pre"] = [("p8.lua", "-- shim")]
    ns["_moy_post"] = [("perf.lua", "-- wrapper")]
    w.run(ns=ns)
    load = [c for c in w.core.calls if c[0] == "load"][0]
    assert load[1] == [("-- shim", "@p8.lua"),
                       (LUA_SRC, "@cart"),
                       ("-- wrapper", "@perf.lua")]
    assert "exec" not in [c[0] for c in w.core.calls
                          if len(c) > 1 and c[1] in ("-- shim", "-- wrapper")]


# -- the shape the Player reads ------------------------------------------------


def test_init_is_none_because_run_begin_already_ran_it(w):
    run = w.run()
    assert run.init is None


def test_draw_is_present_and_empty_rather_than_none(w):
    """A None draw would change the shape every other runtime presents, which
    the Player and its tests both read."""
    run = w.run()
    assert run.draw.__func__ is w.mod.MoycoreRun._draw_noop
    assert run.draw() is None
    assert run.update.__func__ is w.mod.MoycoreRun._update


# -- _refresh ------------------------------------------------------------------


def test_the_buttons_cross_as_one_masks_call_in_the_abi_order(w):
    from runtime.lua_ext import MOY_BUTTONS

    inp = FakeInput()
    inp._held = {"up", "a"}
    inp._pressed = {"a"}
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert inp.mask_calls == [MOY_BUTTONS]
    assert run.snap[C_CONSTS["SNAP_BTN"]] == (1 << MOY_BUTTONS.index("up")
                                              | 1 << MOY_BUTTONS.index("a"))
    assert run.snap[C_CONSTS["SNAP_BTNP"]] == 1 << MOY_BUTTONS.index("a")


def test_an_input_without_button_masks_falls_back_and_agrees_bit_for_bit(w):
    """The guard is BACK because there are TWO InputState classes and the
    boards run the second; removing it dropped a Lua cart into the
    crash-to-code editor with `no attribute button_masks`. The fallback used
    to carry its OWN copy of the order, so the slow path and the fast path
    disagreed about which button the kid pressed."""
    from runtime.lua_ext import MOY_BUTTONS

    combos = ({"left"}, {"up", "b"}, {"run"}, set(MOY_BUTTONS), set())
    for held in combos:
        fast = FakeInput()
        fast._held, fast._pressed = set(held), set(held)
        slow = FakeInput()
        slow._held, slow._pressed = set(held), set(held)
        slow.button_masks = None
        a = World()
        b = World()
        try:
            ra = a.run(ws=FakeWs(inp=fast))
            rb = b.run(ws=FakeWs(inp=slow))
            ra._refresh()
            rb._refresh()
            assert list(ra.snap) == list(rb.snap), held
        finally:
            b.close()                    # LIFO: each World restores what the
            a.close()                    # one before it had installed


def test_both_real_input_states_drive_the_snapshot_the_same_way(w):
    """`runtime/input.py` and the boards' table differ in BUTTONS length; the
    snapshot must not."""
    from runtime.input import InputState as HostInput
    from runtime.moy_input import InputTable as BoardInput

    snaps = []
    for cls in (HostInput, BoardInput):
        inp = cls()
        inp.set_button("up", True)
        inp.set_button("b", True)
        inp.begin_frame()
        inp.cart_start_ms = 0
        world = World()
        try:
            run = world.run(ws=FakeWs(inp=inp))
            run._refresh()
            snaps.append(list(run.snap))
        finally:
            world.close()
    assert snaps[0] == snaps[1]


def test_one_player_costs_a_count_and_leaves_the_second_slot_alone(w):
    inp = FakeInput(players=FakePlayers(n=1))
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_PLAYERS"]] == 1
    assert inp.players.mask_calls == []
    assert run.snap[C_CONSTS["SNAP_BTN_P1"]] == 0


def test_player_two_fills_its_own_slots_through_the_router(w):
    """Until 2026-08-22 nothing filled them, so libmoy's `players()` answered
    1 forever and the Lua twin of a 2P cart fielded one tank."""
    from runtime.lua_ext import MOY_BUTTONS

    inp = FakeInput(players=FakePlayers(n=2, held=0b101, pressed=0b100))
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_PLAYERS"]] == 2
    assert inp.players.mask_calls == [(MOY_BUTTONS, 1)]
    assert run.snap[C_CONSTS["SNAP_BTN_P1"]] == 0b101
    assert run.snap[C_CONSTS["SNAP_BTNP_P1"]] == 0b100


def test_an_input_with_no_player_router_reports_one_player(w):
    run = w.run()
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_PLAYERS"]] == 1


def test_the_run_begin_snapshot_declares_one_player_before_any_frame(w):
    """`players()` must never read 0 in `_init`, which runs inside run_begin."""
    run = w.run()
    assert run.snap[C_CONSTS["SNAP_PLAYERS"]] == 1


def test_the_time_slot_is_elapsed_since_the_cart_started(w):
    inp = FakeInput(cart_start_ms=1000)
    run = w.run(ws=FakeWs(inp=inp))
    w.clock.ms = 1750
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TIME_MS"]] == 750


def test_an_input_with_no_cart_clock_leaves_the_time_slot_alone(w):
    inp = FakeInput()
    del inp.cart_start_ms
    run = w.run(ws=FakeWs(inp=inp))
    w.clock.ms = 500
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TIME_MS"]] == 0


def test_the_host_import_path_reads_the_same_clock():
    """With no flat `ticks` the glue takes `runtime.ticks` -- the clock the
    Player stamps with -- and still fills the slot: a tier that skipped it ran
    every Lua and compiled cart's time() at 0 plus the tick's own ms."""
    from runtime import ticks
    world = World(flat_ticks=False)
    try:
        assert world.mod._since_ms is ticks._since_ms
        run = world.run(ws=FakeWs(inp=FakeInput(cart_start_ms=ticks._ticks_ms() - 5000)))
        run._refresh()
        assert 5000 <= run.snap[C_CONSTS["SNAP_TIME_MS"]] < 6000
    finally:
        world.close()


def test_the_import_of_the_clock_is_hoisted_out_of_the_frame(w):
    """It was an `import` statement executed once per frame."""
    assert w.mod._since_ms == w.clock.since_ms
    sys.modules["ticks"] = None                  # gone mid-run: still fine
    run = w.run(ws=FakeWs(inp=FakeInput(cart_start_ms=0)))
    w.clock.ms = 42
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TIME_MS"]] == 42


def test_the_pointer_crosses_in_the_carts_own_coordinates(w):
    """...FLAGS INTACT, which is the part a boolean fake cannot see.

    The slot carries moy_input.py's P_LIVE/P_HELD/P_CLICK together, because
    h_touch has one slot and touch() has three questions to answer out of it.
    This test used to hand the glue a BOOLEAN and assert the slot was 1 -- true
    of `int(True)` as well, so it went on passing when the contract underneath
    it changed and pinned nothing at all.
    """
    from runtime.moy_input import P_LIVE, P_HELD, P_CLICK

    inp = FakeInput(touch=(11, 22, P_LIVE | P_HELD | P_CLICK, 300))
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_X"]] == 11
    assert run.snap[C_CONSTS["SNAP_TOUCH_Y"]] == 22
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == P_LIVE | P_HELD | P_CLICK
    # The MS slot is vestigial: it existed so h_touch could read `held` out of
    # it, and `held` is a flag now. Still in the C ABI, read by nothing.
    assert run.snap[C_CONSTS["SNAP_TOUCH_MS"]] == 0
    # A pointer with nothing held is still a POINTER: the flags have to survive
    # apart, or a hovering mouse reads as no mouse.
    inp.touch = (11, 22, P_LIVE, 0)
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == P_LIVE


def test_a_lifted_pointer_reads_down_zero_which_is_touch_returning_nil(w):
    """SPEC.md 7.3: 0 means no pointer at all."""
    inp = FakeInput(touch=(11, 22, 0, 0))
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == 0


def test_a_pointer_read_that_raises_reports_no_pointer_rather_than_dying(w):
    inp = FakeInput(touch=(5, 6, 3, 9))
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    inp.touch_error = OSError("i2c")
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == 0


def test_an_input_with_no_pointer_and_no_masks_and_no_players_still_refreshes(w):
    from runtime.lua_ext import MOY_BUTTONS

    run = w.run(ws=FakeWs(inp=MinimalInput(held=("b",), pressed=("b",))))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == 0
    assert run.snap[C_CONSTS["SNAP_PLAYERS"]] == 1
    assert run.snap[C_CONSTS["SNAP_BTN"]] == 1 << MOY_BUTTONS.index("b")


def test_the_last_typed_key_crosses_as_an_int_and_none_reads_zero(w):
    inp = FakeInput(last_key=0x41)
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_KEY"]] == 0x41
    inp.last_key = None
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_KEY"]] == 0


def test_the_slot_numbers_are_bound_once_at_construction(w):
    """They were a module attribute lookup per frame, a dozen times a frame."""
    run = w.run()
    for name in ("SNAP_BTN", "SNAP_BTNP", "SNAP_BTN_P1", "SNAP_BTNP_P1",
                 "SNAP_PLAYERS", "SNAP_TIME_MS", "SNAP_TOUCH_X",
                 "SNAP_TOUCH_Y", "SNAP_TOUCH_DOWN", "SNAP_TOUCH_MS",
                 "SNAP_KEY"):
        delattr(w.core, name)
    run._refresh()                       # reads nothing off the module
    assert run.snap[run._I_KEY] == 0


# -- _update -------------------------------------------------------------------


def test_a_frame_refreshes_then_ticks_then_syncs_the_view_then_drains(w):
    inp = FakeInput(touch=(1, 2, True, 3))
    run = w.run(ws=FakeWs(inp=inp))
    run._update(0.033)                   # settle the framebuffer pointer
    w.core.calls.clear()
    w.ns["_log"].clear()
    inp._held = {"a"}
    w.core.view_value = (128, 120)
    run.aq[0] = 1
    run.aq[1] = C_CONSTS["AQ_MUSIC_STOP"]
    run._update(0.033)
    assert w.core.verbs() == ["tick"]
    assert run.snap[C_CONSTS["SNAP_BTN"]] != 0        # refreshed before tick
    assert inp.game_view == (128, 120)
    assert ("music_stop",) in w.ns["_log"]
    assert run.aq[0] == 0


def test_the_dt_reaches_the_c_tick_unchanged(w):
    run = w.run()
    run._update(0.0166)
    assert [c for c in w.core.calls if c[0] == "tick"][-1] == ("tick", 0.0166)


def test_a_tick_error_raises_after_the_frames_view_and_audio_are_handled(w):
    run = w.run()
    w.core.tick_err = "cart:7: attempt to index a nil value"
    run.aq[0] = 1
    run.aq[1] = C_CONSTS["AQ_MUSIC_STOP"]
    with pytest.raises(RuntimeError, match="cart:7"):
        run._update(0.016)
    assert ("music_stop",) in w.ns["_log"]


def test_a_swapped_framebuffer_is_retargeted_and_a_steady_one_is_not(w):
    """A tier that ping-pongs per frame (the P4's DPI pair, the T-Deck's
    bounce) must re-point the canvas exactly as `DeviceCanvas.sync_back` does
    for the Python lanes."""
    canvas = FakeCanvas()
    run = w.run(ws=FakeWs(canvas=canvas))
    run._update(0.016)
    assert w.core.retargets == [canvas._buf]
    run._update(0.016)
    assert len(w.core.retargets) == 1                  # unchanged: no call
    second = canvas.swap()
    run._update(0.016)
    assert w.core.retargets == [w.core.retargets[0], second]
    canvas._buf = w.core.retargets[0]
    run._update(0.016)
    assert len(w.core.retargets) == 3


# -- _sync_view ----------------------------------------------------------------


def test_a_view_declared_in_init_lands_before_the_first_frame(w):
    """`_init` runs inside `run_begin`, so a cart that declares its region
    there must reach `ws.input.game_view` at construction."""
    w.core.view_value = (128, 120)
    run = w.run()
    assert run.ws.input.game_view == (128, 120)


def test_an_unchanged_view_costs_one_comparison_and_no_write(w):
    inp = _ViewRecordingInput()
    w.core.view_value = (128, 120)
    run = w.run(ws=FakeWs(inp=inp))
    writes = len(inp.view_writes)
    run._sync_view()
    run._sync_view()
    assert len(inp.view_writes) == writes


def test_a_cart_that_changes_its_region_at_runtime_is_followed(w):
    inp = _ViewRecordingInput()
    run = w.run(ws=FakeWs(inp=inp))
    w.core.view_value = (64, 64)
    run._sync_view()
    assert inp.game_view == (64, 64)
    w.core.view_value = None
    run._sync_view()
    assert inp.game_view is None


def test_a_console_without_the_field_is_fine(w):
    class NoField:
        def __setattr__(self, name, value):
            raise AttributeError(name)

    ws = FakeWs()
    run = w.run(ws=ws)
    ws.input = NoField()
    w.core.view_value = (10, 10)
    run._sync_view()


# -- _drain_audio --------------------------------------------------------------


def _queue(run, *cmds):
    slots = C_CONSTS["AQ_SLOTS"]
    run.aq[0] = len(cmds)
    for i, cmd in enumerate(cmds):
        p = 1 + i * slots
        for j, v in enumerate(cmd):
            run.aq[p + j] = v


def test_an_empty_queue_costs_one_read(w):
    """Most frames queue nothing, so the early-out is the common path. Proven
    the way the bound slot numbers are: with `AQ_SLOTS` taken off the module,
    anything executing past the guard raises."""
    run = w.run()
    del w.core.AQ_SLOTS
    run._drain_audio()
    assert w.ns["_log"] == []


def test_every_op_is_one_call_on_the_runs_audio_session(w):
    """One kernel call per queued command, on the run's session (ws.audio),
    in the queue's order."""
    run = w.run()
    _queue(run,
           (C_CONSTS["AQ_SFX"], 3, 5),
           (C_CONSTS["AQ_SFX"], 4, -1),
           (C_CONSTS["AQ_MUSIC"], 2, 1),
           (C_CONSTS["AQ_MUSIC"], 2, 0),
           (C_CONSTS["AQ_BEEP"], 440, 250),
           (C_CONSTS["AQ_MUSIC_STOP"], 0, 0),
           (C_CONSTS["AQ_SOUND_STOP"], 6, 0),
           (C_CONSTS["AQ_SOUND_STOP"], -1, 0),
           (C_CONSTS["AQ_VOLUME"], 7, 0))
    run._drain_audio()
    assert w.ns["_log"] == [
        ("sfx", 3, 5),
        ("sfx", 4, None),
        ("music", 2, True),
        ("music", 2, False),
        ("beep", 440, 0.25),
        ("music_stop",),
        ("sound_stop", 6),
        ("sound_stop", None),
        ("volume", 7),
    ]
    # ...by IDENTITY where the marshalling is the point: `1 == True` in Python,
    # so a tuple compare cannot see `bool(b)` being dropped.
    assert w.ns["_log"][2][2] is True and w.ns["_log"][3][2] is False
    assert isinstance(w.ns["_log"][4][2], float)


def test_order_is_preserved_because_the_queue_is_a_queue(w):
    run = w.run()
    _queue(run, *[(C_CONSTS["AQ_SFX"], i, -1) for i in range(8)])
    run._drain_audio()
    assert [c[1] for c in w.ns["_log"]] == list(range(8))


def test_the_header_is_cleared_before_dispatch_so_a_frame_never_replays(w):
    seen = []
    ns = make_ns()
    ns["sfx"] = lambda a, b: seen.append(ns["_run"].aq[0])
    run = w.run(ns=ns)
    ns["_run"] = run
    _queue(run, (C_CONSTS["AQ_SFX"], 1, -1))
    run._drain_audio()
    assert seen == [0]
    run._drain_audio()
    assert len(seen) == 1


def test_one_bad_command_is_not_the_frame(w):
    def boom(*a):
        raise RuntimeError("no audio backend")

    ns = make_ns(sfx=boom)
    run = w.run(ns=ns)
    _queue(run,
           (C_CONSTS["AQ_SFX"], 1, -1),
           (C_CONSTS["AQ_VOLUME"], 4, 0))
    run._drain_audio()
    assert ("volume", 4) in ns["_log"]


def test_an_unknown_op_code_is_ignored(w):
    run = w.run()
    _queue(run, (max(C_CONSTS["AQ_VOLUME"], 0) + 40, 1, 2))
    run._drain_audio()
    assert w.ns["_log"] == []


def test_a_negative_count_is_not_a_drain(w):
    run = w.run()
    run.aq[0] = -1
    run._drain_audio()
    assert w.ns["_log"] == []
    assert run.aq[0] == -1


def test_commands_are_read_at_the_c_stride(w):
    """`AQ_SLOTS` is the op plus three args; reading at any other stride puts
    the next command's fields into this one's."""
    run = w.run()
    _queue(run, (C_CONSTS["AQ_SFX"], 9, 2), (C_CONSTS["AQ_VOLUME"], 5, 0))
    run._drain_audio()
    assert w.ns["_log"] == [("sfx", 9, 2), ("volume", 5)]


# -- frame_split ---------------------------------------------------------------


def test_the_logic_render_split_comes_back_from_the_c_side_in_ms(w):
    """Both halves happen inside our `update()`, so without this the diag
    reads `logic = the whole cart frame, render = 0` -- which compared against
    every per-cart number recorded since #67 reads as a doubling of logic that
    never happened."""
    run = w.run()
    w.core.split = (4200, 8100)
    assert list(run.frame_split()) == [4.2, 8.1]


def test_a_build_whose_module_predates_tick_split_reports_none(w):
    run = w.run()
    del w.core.tick_split
    assert run.frame_split() is None


# -- pmem ----------------------------------------------------------------------


def test_pmem_is_written_back_through_the_consoles_own_cell_verb(w):
    pmem = FakePmem()
    run = w.run(ws=FakeWs(pmem=pmem))
    w.core.pmem_image_fill = {0: 11, 255: 22}
    assert run.flush_pmem() is True
    assert len(pmem.written) == 256
    assert pmem.cells[0] == 11 and pmem.cells[255] == 22


def test_an_unmoved_pmem_writes_nothing(w):
    """The C side owns 256 int32 slots with a dirty flag -- RAM during play,
    written only at the #66 boundaries."""
    pmem = FakePmem()
    run = w.run(ws=FakeWs(pmem=pmem))
    w.core.pmem_image_result = False
    assert run.flush_pmem() is False
    assert pmem.written == []


def test_a_closed_vm_is_not_asked_for_its_pmem(w):
    pmem = FakePmem()
    run = w.run(ws=FakeWs(pmem=pmem))
    w.core.is_active = False
    assert run.flush_pmem() is False
    assert "pmem_image" not in w.core.verbs()


def test_a_console_with_no_pmem_holder_declines_the_flush(w):
    run = w.run(ws=FakeWs())
    assert run.flush_pmem() is False

    class NoCell:
        cells = [0] * 256

    world = World()
    try:
        run = world.run(ws=FakeWs(pmem=NoCell()))
        assert run.flush_pmem() is False
    finally:
        world.close()


def test_a_cell_that_raises_stops_the_walk_rather_than_the_exit(w):
    class Fussy(FakePmem):
        def cell(self, i, v):
            if i == 3:
                raise OSError("sd gone")
            FakePmem.cell(self, i, v)

    pmem = Fussy()
    run = w.run(ws=FakeWs(pmem=pmem))
    assert run.flush_pmem() is True
    assert len(pmem.written) == 3


# -- close ---------------------------------------------------------------------


def test_closing_persists_pmem_and_then_closes_the_vm(w):
    pmem = FakePmem()
    run = w.run(ws=FakeWs(pmem=pmem))
    w.core.calls.clear()
    run.close()
    assert w.core.verbs() == ["pmem_image", "close"]
    assert len(pmem.written) == 256


def test_closing_drops_the_handle_registries_that_pin_the_layers(w):
    """They are what PIN the run's layers and images, and a layer is a
    full-canvas allocation."""
    run = w.run()
    w.core.registered["__layer_new"](320, 240)
    w.core.registered["__image_handle"]("bg")
    assert run._layers and run._images
    run.close()
    assert run._layers is None and run._images is None


def test_closing_a_run_whose_module_went_away_is_still_a_clean_exit(w):
    """The defensive arms in `flush_pmem` and `close`. Unreachable on a board
    -- `_moycore` is bound at import and a run cannot exist without it -- so
    they are pinned here rather than left as the only untested lines in the
    file."""
    run = w.run(ws=FakeWs(pmem=FakePmem()))
    w.mod._moycore = None
    assert run.flush_pmem() is False
    run.close()
    assert w.core.closes == 0
    assert run._layers is None


def test_a_failing_pmem_write_still_closes_the_vm(w):
    class Exploding:
        cells = [0] * 256

        def cell(self, i, v):
            raise KeyboardInterrupt

    run = w.run(ws=FakeWs(pmem=FakePmem()))
    run.ws.pmem = Exploding()
    with pytest.raises(KeyboardInterrupt):
        run.close()
    assert w.core.closes == 1


# -- the two bodies must agree -------------------------------------------------


def _glue_module_reads():
    """Every `_moycore.<name>` the glue touches."""
    src = GLUE_SRC.read_text(encoding="utf-8")
    return set(re.findall(r"_moycore\.(\w+)", src)) | set(
        re.findall(r'getattr\(_moycore, "(\w+)"', src))


def test_every_name_the_glue_reads_is_exported_by_the_c_module():
    """A `SNAP_*` renamed in C is invisible on a host and presents on glass as
    a cart that will not start. The doubles above are built from this same
    table, so this test is what keeps that construction honest."""
    missing = _glue_module_reads() - C_NAMES
    assert not missing, missing


def test_the_executed_body_is_the_file_the_boards_stage():
    """`test_board_routing` keeps the ROUTING greps (a board still calls
    `make_moycore_runtime`); the body assertions are executed above, and both
    are only looking at the same file for as long as this holds."""
    world = World()
    try:
        assert world.mod.__file__ == str(GLUE_SRC)
    finally:
        world.close()
    for board in ("lilygo_t_deck_plus_mainline", "guition_jc3248w535",
                  "esp32_p4_wifi6_touch_lcd_7b", "guition_jc8012p4a1c",
                  "web_runner"):
        toml = (ROOT / "firmware" / board / "board.toml").read_text(
            encoding="utf-8")
        assert "moycore_glue.py" in toml, board


def test_a_dead_pointer_is_dead_even_when_a_game_pointer_still_stands(w):
    """Liveness is the POINTER's, never the game-space mapping of it.

    console.py republishes `input.game_pointer` with a position every frame
    whether or not the pointer is still alive -- it gates only the tap/hold
    flags on it. A resolver that read "there is a game_pointer" as "there is a
    pointer" therefore never expired on a board: found on glass, where a p8
    cart held a cursor over `dungeons & diagrams`' board forever and its d-pad
    was stamped over every frame by the cart's own mouse handler.
    """
    from runtime.moy_input import P_LIVE, P_NONE

    inp = FakeInput(touch=(11, 22, P_LIVE, 0))
    inp.game_pointer = (5, 6, False, False)       # a stale mapping, still there
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_X"]] == 5, "the mapping should win the COORDS"
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == P_LIVE

    inp.touch = None                               # ...and now the pointer is gone
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == P_NONE, (
        "a game_pointer outlived the pointer it maps")


# -- the OTHER hand-mirrored ABI: moy_gfx's enums in device_canvas -------------
#
# Same failure shape as the moycore constants above, one module over, and
# nothing pinned it: `device/device_canvas.py` restates moy_gfx's state-array
# indices, its gate kinds and mg_shape's `kind` as Python literals, and the two
# sides are ONE BINARY LAYOUT. A slot inserted in the C enum renumbers every
# index after it, the Python keeps writing the old ones, and the gate reads
# camera where it expects clip -- on glass, silently, in the fast lane that
# exists to skip the Python frame. The parser above is already the instrument;
# these tests point it at the second header.


def _py_int_consts(path):
    """Module-level int constants of a Python source, by name. Read as source
    rather than imported: device_canvas pulls in the device tier's flat module
    names, and the layout is literal assignments either way."""
    out = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt, val = node.targets[0], node.value
        pairs = (zip(tgt.elts, val.elts)
                 if isinstance(tgt, ast.Tuple) and isinstance(val, ast.Tuple)
                 else [(tgt, val)])
        for t, v in pairs:
            if (isinstance(t, ast.Name) and isinstance(v, ast.Constant)
                    and isinstance(v.value, int) and not isinstance(v.value, bool)):
                out[t.id] = v.value
    return out


def _mirror(c_enum, c_prefix, py_prefix):
    """{python name: (c name, value)} for the members of one C enum."""
    return {py_prefix + n[len(c_prefix):]: (n, v) for n, v in c_enum.items()}


GFX_MIRRORS = (
    ("ST_CAM_X", GFX_SRC, "ST_", "_ST_"),
    ("GATE_RECT", GFX_SRC, "GATE_", "_GATE_"),
    ("MG_SHAPE_LINE", GFX_KERNELS, "MG_SHAPE_", "_MG_"),
)


@pytest.mark.parametrize("first,src,c_prefix,py_prefix", GFX_MIRRORS)
def test_device_canvas_mirrors_the_moy_gfx_enum_exactly(first, src, c_prefix,
                                                        py_prefix):
    py = _py_int_consts(CANVAS_SRC)
    want = _mirror(_c_enum(first, src), c_prefix, py_prefix)
    for name, (c_name, value) in sorted(want.items()):
        assert name in py, (
            "%s defines %s and device_canvas has no %s" % (src.name, c_name, name))
        assert py[name] == value, (
            "%s = %d in device_canvas, %s = %d in %s"
            % (name, py[name], c_name, value, src.name))


@pytest.mark.parametrize("first,src,c_prefix,py_prefix", GFX_MIRRORS)
def test_device_canvas_mirrors_no_member_the_c_dropped(first, src, c_prefix,
                                                       py_prefix):
    """The other direction: a constant the C no longer has is a Python name
    still being written into the shared array."""
    want = _mirror(_c_enum(first, src), c_prefix, py_prefix)
    stray = {n for n in _py_int_consts(CANVAS_SRC)
             if n.startswith(py_prefix)} - set(want)
    assert not stray, (
        "device_canvas keeps %s, which %s's enum does not define"
        % (sorted(stray), src.name))


# -- the compiled cart (WasmRun) -------------------------------------------------


class _CartProject(FakeProject):
    def __init__(self, cart, **kw):
        super().__init__(**kw)
        self.cart = cart


_MODULE = """
(module
  (import "moy" "cls" (func $cls (param i32)))
  (memory (export "memory") 3 3)
  (func (export "_init"))
  (func (export "_update") (param f32))
  (func (export "_draw") (call $cls (i32.const 1))))
"""


def _compiled(tmp_path, chips=("esp32s3",), memory=3):
    """A compiled cart folder: a module assembled from WAT, and a stand-in
    compiled module per chip (the glue only checks it is there and hands the
    path on -- the engine is what reads it)."""
    from tools import wat
    d = tmp_path / "hello.moy"
    d.mkdir()
    main = d / "main.wasm"
    main.write_bytes(wat.assemble(_MODULE))
    cart = {"path": str(d), "main": "main.wasm", "runtime": "wasm",
            "memory": memory}
    for chip in chips:
        with open(_glue_aot(cart, chip), "wb") as f:
            f.write(b"aot")
    return cart, str(main)


def _glue_aot(cart, chip, format=None):
    world = World()
    try:
        return world.mod.aot_path(cart["path"], cart["main"], chip,
                                  format or format_version())
    finally:
        world.close()


def _wasm_world(chip="esp32s3"):
    world = World(wasm_chip=chip)
    world.core.WASM = 1
    return world


def test_a_compiled_cart_opens_on_a_console_with_no_vm(tmp_path):
    import hashlib
    cart, main = _compiled(tmp_path)
    world = _wasm_world()
    try:
        ws = FakeWs(project=_CartProject(cart), pmem=FakePmem())
        ws._with_sd = lambda fn: fn()
        run = world.mod.WasmRun(ws, make_ns(), None)
        assert world.core.verbs()[:2] == ["run_begin", "wasm_open"]
        assert world.core.rb("vm") is False
        (_v, module, head, pages, sha, cdir, swapped, gate,
         allow_unsigned, interp, writable, files) = world.core.calls[1]
        assert module == cart["path"] + "/main.esp32s3.f%s.aot" % format_version()
        blob = open(main, "rb").read()
        assert blob.startswith(head) and len(head) < len(blob)
        assert pages == 3 and cdir == cart["path"] and swapped is True
        # the cart's reads take the store's gate, as every store access does
        assert gate is ws._with_sd
        # a console that never turned Unknown sources on loads signed modules only
        assert allow_unsigned is False
        assert interp is False         # a module by this console's own name -- AOT
        # the cart declares no writable paths; its files are kept beside the
        # store, under the same gate
        assert writable is None
        assert files.dir == os.path.dirname(cart["path"]).rsplit("/", 1)[0] \
            + "/written/" + os.path.basename(cart["path"])[:-4]
        assert files.gate is ws._with_sd
        assert not run.interp
        assert run.interp_cause is None
        assert sha == hashlib.sha256(blob).hexdigest()
        # the frame is MoycoreRun's: _update ticks, draw is the fused no-op
        assert run.init is None and run.draw() is None
        run.update(1 / 30)
        assert "tick" in world.core.verbs()
    finally:
        world.close()


def test_the_cart_s_writable_paths_reach_the_binding_joined(tmp_path):
    """The manifest's "writable" entries go to wasm_open as one string, NUL
    between them, which moycore hands libmoy's binding as its list; the
    store is the cart's, beside the carts store, with the page's keeper when
    the console has one (moy-spec SPEC.md 16.12)."""
    cart, _main = _compiled(tmp_path)
    cart["writable"] = ["saves/", "options.cfg"]
    world = _wasm_world()
    try:
        ws = FakeWs(project=_CartProject(cart), pmem=FakePmem())
        keep = object()
        ws.cart_keep = keep
        world.mod.WasmRun(ws, make_ns(), None)
        call = world.core.calls[1]
        assert call[0] == "wasm_open"
        assert call[10] == "saves/\0options.cfg"
        assert call[11].keep is keep and call[11].id == "hello"
    finally:
        world.close()


def test_the_load_asks_the_engine_what_unknown_sources_says_now(tmp_path):
    """The owner's switch rides every load as it stands at that load: the
    engine lets a module with no signature through only when it is on, and a
    flip reaches the next cart started."""
    cart, _main = _compiled(tmp_path)
    for on in (True, False, True):
        world = _wasm_world()
        try:
            ws = FakeWs(project=_CartProject(cart))
            ws.unknown_sources = on
            world.mod.WasmRun(ws, make_ns(), None)
            assert world.core.calls[1][0] == "wasm_open"
            assert world.core.calls[1][8] is on
        finally:
            world.close()


def test_an_unsigned_refusal_retries_on_the_interpreter(tmp_path):
    """The engine's refusal for a module with no signature (Unknown sources
    off) is not tamper evidence, so WasmRun retries the open on the
    interpreter -- main.wasm itself -- instead of raising
    (docs/wasm_tier_plan_2026-09.md, "A cart survives its firmware",
    2026-09-30). The cart plays; run.interp says so, and interp_cause says
    why -- "unsigned", so the Player's notice reads "isn't signed" rather
    than "needs an update"."""
    cart, main = _compiled(tmp_path)
    world = _wasm_world()
    world.core.wasm_open_errs = ["refused: unsigned module", None]
    try:
        ws = FakeWs(project=_CartProject(cart), pmem=FakePmem())
        run = world.mod.WasmRun(ws, make_ns(), None)
        assert run.interp
        assert run.interp_cause == "unsigned"
        opens = [c for c in world.core.calls if c[0] == "wasm_open"]
        assert len(opens) == 2
        assert opens[0][1] == cart["path"] + "/main.esp32s3.f%s.aot" % format_version()
        assert opens[0][9] is False            # AOT, tried first
        assert opens[1][1] == main             # the retry is main.wasm itself
        assert opens[1][9] is True              # on the interpreter
        assert world.core.closes == 0
    finally:
        world.close()


def test_a_cart_with_no_module_for_this_chip_runs_on_the_interpreter(tmp_path):
    """A stale or absent module is never opened at all -- it is simply the
    wrong file name -- so the cart goes straight to the interpreter, one
    wasm_open call, no failed attempt logged. interp_cause reads "missing",
    so the Player's notice says the cart needs an update."""
    cart, main = _compiled(tmp_path, chips=("esp32p4",))
    world = _wasm_world("esp32s3")
    try:
        ws = FakeWs(project=_CartProject(cart), pmem=FakePmem())
        run = world.mod.WasmRun(ws, make_ns(), None)
        assert run.interp
        assert run.interp_cause == "missing"
        opens = [c for c in world.core.calls if c[0] == "wasm_open"]
        assert len(opens) == 1
        assert opens[0][1] == main
        assert opens[0][9] is True
        assert "run_begin" in world.core.verbs()
    finally:
        world.close()


def _browser_world():
    """The browser's engine (native/moy_wasm_web): `moy_wasm` with no
    compiled-module tier, CHIP and FORMAT None."""
    world = _wasm_world()
    world.mod._moy_wasm.CHIP = None
    world.mod._moy_wasm.FORMAT = None
    return world


def test_an_engine_with_no_compiled_tier_runs_main_wasm_at_its_full_speed(tmp_path):
    """The browser runs main.wasm itself on its own engine: no module is
    looked for (the cart's chips' modules are not this console's), none is
    missing, so the run is not `interp` and the Player shows no slow-play
    notice -- and main.wasm needs no hash, key or signature."""
    cart, main = _compiled(tmp_path, chips=("esp32s3", "esp32p4"))
    world = _browser_world()
    try:
        ws = FakeWs(project=_CartProject(cart), pmem=FakePmem())
        run = world.mod.WasmRun(ws, make_ns(), None)
        assert not run.interp and run.interp_cause is None
        opens = [c for c in world.core.calls if c[0] == "wasm_open"]
        assert len(opens) == 1
        (_v, module, head, pages, sha, _cdir, _sw, _gate, _unknown, interp,
         _writable, _files) = opens[0]
        assert module == main and sha is None and interp is True
        assert open(main, "rb").read().startswith(head) and pages == 3
    finally:
        world.close()


def test_an_engine_with_no_compiled_tier_sizes_main_wasm_by_its_one_rule(tmp_path):
    cart, main = _compiled(tmp_path, chips=("esp32s3",))
    world = _browser_world()
    try:
        engine = _Engine(world)
        rt = world.mod.make_wasm_runtime(FakeWs())
        assert rt.footprint(cart) == (3 * 65536 + os.path.getsize(main), 3 * 65536)
        assert engine.interp_asked == [(3 * 65536, os.path.getsize(main))]
        assert engine.asked == []
    finally:
        world.close()


@pytest.mark.parametrize("make", [_wasm_world, _browser_world])
def test_a_cart_without_its_main_wasm_is_refused_by_name(tmp_path, make):
    """main.wasm is the cart. One that has gone between the shelf's scan
    (which lists no cart without its main) and the run is a plain refusal
    before the console is begun, not an error from inside the engine."""
    cart, main = _compiled(tmp_path, chips=())
    os.remove(main)
    world = make()
    try:
        with pytest.raises(RuntimeError) as e:
            world.mod.WasmRun(FakeWs(project=_CartProject(cart)), make_ns(), None)
        assert str(e.value) == "refused: this cart's main.wasm is not on this console"
        assert "run_begin" not in world.core.verbs()
    finally:
        world.close()


def test_a_module_this_firmware_cannot_link_reads_as_the_firmware(tmp_path):
    """A load that stops on a helper the module calls and this firmware's
    runtime does not register (#229: `__fixsfdi` on the P4) is retried on
    the interpreter like any non-tamper refusal, but the cause is the
    console's -- "firmware" -- so the Player's notice does not tell the
    player the cart needs an update."""
    cart, main = _compiled(tmp_path)
    world = _wasm_world()
    world.core.wasm_open_errs = [
        "load: AOT module load failed: resolve symbol __fixsfdi failed", None]
    try:
        ws = FakeWs(project=_CartProject(cart), pmem=FakePmem())
        run = world.mod.WasmRun(ws, make_ns(), None)
        assert run.interp and run.interp_cause == "firmware"
        opens = [c for c in world.core.calls if c[0] == "wasm_open"]
        assert [o[9] for o in opens] == [False, True]
        assert opens[1][1] == main
    finally:
        world.close()


def test_a_key_mismatch_that_retries_clean_reads_as_missing(tmp_path):
    """A corrupted or mismatched AOT file (rare: the name matched, the
    content did not) is retried on the interpreter same as an absent one,
    and reads the same cause -- "missing", never "unsigned" -- so the
    Player's notice says the cart needs an update, not that it isn't
    signed."""
    cart, main = _compiled(tmp_path)
    world = _wasm_world()
    world.core.wasm_open_errs = ["refused: key mismatch 'opt 2'", None]
    try:
        ws = FakeWs(project=_CartProject(cart), pmem=FakePmem())
        run = world.mod.WasmRun(ws, make_ns(), None)
        assert run.interp and run.interp_cause == "missing"
        opens = [c for c in world.core.calls if c[0] == "wasm_open"]
        assert opens[1][1] == main
    finally:
        world.close()


def test_a_key_mismatch_retries_then_a_trap_still_closes_the_console(tmp_path):
    """A corrupted or mismatched AOT file (rare: the name matched, the
    content did not) is retried on the interpreter same as an absent one; if
    THAT also fails, the failure is real and closes the console."""
    cart, _main = _compiled(tmp_path)
    world = _wasm_world()
    world.core.wasm_open_errs = ["refused: key mismatch 'opt 2'", "a trap in _init"]
    try:
        ws = FakeWs(project=_CartProject(cart))
        with pytest.raises(RuntimeError, match="a trap in _init"):
            world.mod.WasmRun(ws, make_ns(), None)
        assert world.core.closes == 1
    finally:
        world.close()


def test_a_bad_signature_never_retries(tmp_path):
    """Tamper evidence -- a signature present but wrong -- is the one AOT
    refusal that stays a hard refusal: no interpreter retry, straight to the
    ordinary error panel."""
    cart, _main = _compiled(tmp_path)
    world = _wasm_world()
    world.core.wasm_open_err = "refused: bad signature"
    try:
        ws = FakeWs(project=_CartProject(cart))
        with pytest.raises(RuntimeError, match="bad signature"):
            world.mod.WasmRun(ws, make_ns(), None)
        opens = [c for c in world.core.calls if c[0] == "wasm_open"]
        assert len(opens) == 1
        assert world.core.closes == 1
    finally:
        world.close()


def test_a_manifest_without_memory_is_refused_before_anything_loads(tmp_path):
    cart, _main = _compiled(tmp_path, memory=None)
    world = _wasm_world()
    try:
        ws = FakeWs(project=_CartProject(cart))
        with pytest.raises(RuntimeError, match="memory"):
            world.mod.WasmRun(ws, make_ns(), None)
        assert world.core.verbs() == []
    finally:
        world.close()


def test_the_runtimes_map_names_what_the_build_carries():
    world = _wasm_world()
    try:
        assert sorted(world.mod.make_runtimes(FakeWs())) == ["lua", "wasm"]
    finally:
        world.close()
    world = World()                    # moycore, no engine
    try:
        assert sorted(world.mod.make_runtimes(FakeWs())) == ["lua"]
    finally:
        world.close()
    world = World(moycore=False)
    try:
        assert world.mod.make_runtimes(FakeWs()) == {}
    finally:
        world.close()


# -- the fit check's report (Player: a cart too big for the board) ---------------


class _Engine:
    """moy_wasm's reports: `footprint`/`interp_footprint` record what each
    was asked -- the AOT and the interpreted rule are two different engine
    calls (device/moycore_glue.WasmRuntime.footprint picks between them)."""

    def __init__(self, world, free=(2_900_000, 1_900_000)):
        self.asked = []
        self.interp_asked = []
        self.free = free
        world.mod._moy_wasm.footprint = self._footprint
        world.mod._moy_wasm.interp_footprint = self._interp_footprint
        world.mod._moy_wasm.mem = lambda: (90_000, 50_000, 40_000) + self.free

    def _footprint(self, memory, module):
        self.asked.append((memory, module))
        return memory + module, memory

    def _interp_footprint(self, memory, module):
        self.interp_asked.append((memory, module))
        return memory + module, memory


def test_the_runtime_reports_the_carts_footprint_by_the_engines_rule(tmp_path):
    """The Player's fit check reads the engine's own sizing -- the manifest's
    memory in bytes and this chip's signed module as it sits in the store --
    through the store's gate, and the engine's PSRAM report for what the board
    can give. The arithmetic is the engine's (moy_wasm.footprint), never here."""
    cart, _main = _compiled(tmp_path, chips=("esp32s3", "esp32p4"))
    world = _wasm_world("esp32s3")
    try:
        engine = _Engine(world)
        gated = []
        ws = FakeWs(project=_CartProject(cart))
        ws._with_sd = lambda fn: gated.append(fn) or fn()
        rt = world.mod.make_wasm_runtime(ws)
        assert rt.footprint(cart) == (3 * 65536 + 3, 3 * 65536)
        assert engine.asked == [(3 * 65536, len(b"aot"))]
        assert len(gated) == 1
        assert rt.memory() == engine.free
        # and it is still the factory the Player calls to start the run
        assert isinstance(rt(make_ns(), None), world.mod.WasmRun)
    finally:
        world.close()


def test_a_declaration_past_any_board_is_asked_about_capped_not_overflowed(tmp_path):
    cart, _main = _compiled(tmp_path)
    cart["memory"] = 65536                 # 4 GiB: wasm32's whole space
    world = _wasm_world()
    try:
        engine = _Engine(world)
        world.mod.make_wasm_runtime(FakeWs()).footprint(cart)
        (memory, _module), = engine.asked
        assert 32 * 1024 * 1024 < memory < 2 ** 31
    finally:
        world.close()


def test_no_module_for_this_chip_sizes_against_main_wasm_instead(tmp_path):
    """No module for this chip: the cart plays on the interpreter instead of
    refusing (docs/wasm_tier_plan_2026-09.md, "A cart survives its
    firmware"), so the fit check sizes against main.wasm itself, through the
    INTERPRETED rule (a real file, a real report, never the AOT one) rather
    than giving up."""
    cart, main = _compiled(tmp_path, chips=("esp32p4",))
    world = _wasm_world("esp32s3")
    try:
        engine = _Engine(world)
        rt = world.mod.make_wasm_runtime(FakeWs())
        assert rt.footprint(cart) == (3 * 65536 + os.path.getsize(main), 3 * 65536)
        assert engine.interp_asked == [(3 * 65536, os.path.getsize(main))]
        assert engine.asked == []
    finally:
        world.close()


def test_nothing_to_measure_leaves_the_refusal_to_the_load(tmp_path):
    """No "memory" declared, or no cart path at all: genuinely nothing to
    size, so the load's own refusal is what the kid sees, by name."""
    cart, _main = _compiled(tmp_path, chips=("esp32p4",))
    world = _wasm_world("esp32s3")
    try:
        engine = _Engine(world)
        rt = world.mod.make_wasm_runtime(FakeWs())
        assert rt.footprint(dict(cart, memory=None)) is None
        assert rt.footprint({"memory": 3}) is None       # no "path"
        assert engine.asked == []
    finally:
        world.close()


def test_an_open_that_raises_closes_the_console(tmp_path):
    """The engine raises MemoryError when it cannot hold the module file. A
    run left open would refuse every later cart's run_begin ("a run is
    already open"), Lua carts included."""
    cart, _main = _compiled(tmp_path)
    world = _wasm_world()

    def _no_psram(*a):
        world.core._log("wasm_open", *a)
        raise MemoryError("no PSRAM for the module file")
    world.core.wasm_open = _no_psram
    try:
        ws = FakeWs(project=_CartProject(cart))
        with pytest.raises(MemoryError):
            world.mod.WasmRun(ws, make_ns(), None)
        assert world.core.closes == 1
    finally:
        world.close()


# -- a compiled cart's frame from its own memory (CartFrame) --------------------


class PresentingCanvas:
    presents_frames = True


class DirectColourCanvas:
    """A P4's system canvas: it shows blit565's frames, not blit's."""
    presents_frames = True
    presents_palette_frames = False


class FenceComp:
    def __init__(self, log):
        self.log = log

    def fold_fence(self):
        self.log.append(("fold_fence",))

    def snap_fence(self):
        self.log.append(("snap_fence",))

    def disarm_scale_fold(self):
        self.log.append(("disarm",))


def test_a_compiled_cart_takes_its_frames_where_the_canvas_shows_them(tmp_path):
    cart, _main = _compiled(tmp_path)
    world = _wasm_world()
    try:
        ws = FakeWs(project=_CartProject(cart))
        ws.sys_canvas = PresentingCanvas()
        run = world.mod.WasmRun(ws, make_ns(), None)
        assert ("take_frames", True, True) in world.core.calls
        assert world.core.verbs().index("take_frames") > world.core.verbs().index(
            "wasm_open")
        assert ws.cart_frame is run.frame
        assert (run.frame.w, run.frame.h) == (320, 240)
        assert len(bytes(run.frame.lut)) == 512
    finally:
        world.close()


def test_a_canvas_without_a_palette_resolve_takes_only_direct_colour(tmp_path):
    """A P4 shows blit565's frames from the cart's memory and leaves blit's
    to the blit: the binding is told which layouts it takes."""
    cart, _main = _compiled(tmp_path)
    world = _wasm_world()
    try:
        ws = FakeWs(project=_CartProject(cart))
        ws.sys_canvas = DirectColourCanvas()
        run = world.mod.WasmRun(ws, make_ns(), None)
        assert ("take_frames", True, False) in world.core.calls
        assert ws.cart_frame is run.frame
    finally:
        world.close()


def test_a_canvas_that_cannot_show_them_keeps_the_blit(tmp_path):
    cart, _main = _compiled(tmp_path)
    for canvas in (None, types.SimpleNamespace(presents_frames=False)):
        world = _wasm_world()
        try:
            ws = FakeWs(project=_CartProject(cart))
            if canvas is not None:
                ws.sys_canvas = canvas
            run = world.mod.WasmRun(ws, make_ns(), None)
            assert "take_frames" not in world.core.verbs()
            assert run.frame is None and getattr(ws, "cart_frame", None) is None
        finally:
            world.close()


def test_the_cart_frame_forwards_to_the_binding(tmp_path):
    world = _wasm_world()
    try:
        cf = world.mod.CartFrame(320, 240)
        world.core.owed_frame = view = memoryview(bytearray(320 * 240))
        world.core.owed_lut = bytes(range(256)) * 2
        assert cf.take() is view
        assert bytes(cf.lut) == bytes(range(256)) * 2
        cf.settle()
        kept = bytearray(8)
        cf.presented(kept, 5)
        assert world.core.calls[-2:] == [("frame_settle",),
                                         ("frame_presented", kept, 5)]
    finally:
        world.close()


def test_the_scratch_is_the_runs_and_freed_once_nothing_reads_it(tmp_path):
    """The frame fold's scratch is a BUF row of the glass (a SCRATCH, the
    kernel's): grown by replacing it, given back at the run's close only
    after every fence that could still be reading it."""
    import types
    world = _wasm_world()
    try:
        g = world.mod._glass
        cf = world.mod.CartFrame(320, 240)
        log = []
        cf.close(FenceComp(log))
        assert log == []                    # nothing taken: nothing to wait on
        a = cf.scratch(77440)
        ba = cf._scratch_b
        assert len(a) == 77440 and ba.live and ba.role == g.ROLE_SCRATCH
        assert cf.scratch(1000) is a        # big enough: the same buffer
        b = cf.scratch(154240)              # a blit565 frame wants more
        assert b is not a and len(b) == 154240 and not ba.live
        bb = cf._scratch_b
        del world.core.calls[:]
        cf.close(FenceComp(log))
        assert log == [("fold_fence",), ("snap_fence",), ("disarm",)]
        assert not bb.live
        # ...and the last frame shown went into the canvas before its copy
        # was let go: from here on the canvas is all that holds the game.
        assert world.core.calls == [("frame_settle",)]

        def refuse(n, role, owner=0):
            raise MemoryError
        world.mod._glass = types.SimpleNamespace(buf=refuse,
                                                 ROLE_SCRATCH=g.ROLE_SCRATCH,
                                                 ORIGIN_HEAP=g.ORIGIN_HEAP)
        assert cf.scratch(10) is None       # no PSRAM: the caller settles
        world.mod._glass = g
    finally:
        world.close()


def test_closing_the_run_lets_go_of_the_frame_before_the_console(tmp_path):
    cart, _main = _compiled(tmp_path)
    world = _wasm_world()
    try:
        ws = FakeWs(project=_CartProject(cart))
        ws.sys_canvas = PresentingCanvas()
        run = world.mod.WasmRun(ws, make_ns(), None)
        order = []
        run.frame.close = lambda comp: order.append(("frame", comp))
        ws.comp = "the compositor"
        real = world.core.close
        world.core.close = lambda: (order.append(("console",)), real())
        run.close()
        assert order == [("frame", "the compositor"), ("console",)]
        assert ws.cart_frame is None and run.frame is None
    finally:
        world.close()


def test_the_cart_frame_holds_the_rects_the_console_paints_over_it(tmp_path):
    world = _wasm_world()
    try:
        cf = world.mod.CartFrame(320, 240)
        for i in range(cf.MAX_PATCHES):
            assert cf.patch(10 * i, 5, 8, 4) is True
        assert cf.patch(0, 0, 1, 1) is False      # full: the painter settles
        assert list(cf.rects[:8]) == [0, 5, 8, 4, 10, 5, 8, 4]
        assert cf.nrects == cf.MAX_PATCHES
        cf.presented(bytearray(4), 0)
        assert cf.nrects == 0                     # a frame's rects are its own
    finally:
        world.close()


def test_a_settle_after_the_chip_keeps_the_chip(tmp_path):
    """The frame is written over the whole canvas; the rects the console had
    already painted over it keep their pixels."""
    world = _wasm_world()
    try:
        canvas = FakeCanvas(8, 4)
        canvas._buf[:] = bytes(range(64))
        written = bytes([0xEE] * 64)

        def settle():
            world.core.calls.append(("frame_settle",))
            canvas._buf[:] = written
        world.core.frame_settle = settle
        cf = world.mod.CartFrame(8, 4)
        cf.patch(6, 2, 4, 4)                       # clipped to the canvas
        cf.settle(canvas)
        want = bytearray(written)
        for row in (2, 3):
            a = 2 * (row * 8 + 6)
            want[a:a + 4] = bytes(range(64))[a:a + 4]
        assert bytes(canvas._buf) == bytes(want)
        assert cf.nrects == 0
        cf.settle(canvas)                          # no rects: a plain settle
        assert bytes(canvas._buf) == written
    finally:
        world.close()


def test_a_frame_the_cart_did_not_replace_is_shown_again_from_the_scratch(tmp_path):
    """No blit this frame, and the canvas still lacks the last frame shown:
    take() hands back that frame's copy in the scratch, so the flush shows it
    again rather than a canvas nothing wrote."""
    world = _wasm_world()
    try:
        cf = world.mod.CartFrame(4, 2)
        assert cf.take() is None                     # nothing owed, nothing shown
        world.core.owed_frame = memoryview(bytearray(16))
        assert len(cf.take()) == 16 and cf.fmt == 1  # a blit565 frame
        scr = cf.scratch(64)
        scr[5:21] = bytes(range(16))
        cf.presented(scr, 5)
        world.core.owed_frame = None
        world.core.kept = True
        again = cf.take()
        assert bytes(again) == bytes(range(16))
        world.core.kept = False                      # a verb wrote the canvas
        assert cf.take() is None
    finally:
        world.close()
