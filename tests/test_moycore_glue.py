"""`device/moycore_glue.py`, EXECUTED (#208's closing residue).

The largest untested SHARED body left in the tree: staged to all three boards
AND the wasm head, and until this file its host coverage was zero -- coverage
reported the module "was never imported". The one lane that ran it,
`tests/test_semantic_traces.py`, drives it inside a desktop MicroPython
subprocess, so it needs `make unix-micropython`, it SKIPS without one, and
nothing it proves is visible to a host coverage sweep. That is the #208 shape:
a body promoted so four consumers can share it, guarded by greps
(`test_micropython_spike`, `test_moy_button_order`, `test_streaming_sunset`)
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
  this file's -- pinned by `test_micropython_spike`'s boot-path check.
* the frame COST the docstrings quote (~1ms of per-frame `_refresh` on the S3).
  Timing is glass work; the structure that bought it -- one `button_masks`
  call instead of sixteen, slot numbers bound once, no per-frame import -- is
  observable here and is what these tests assert.

Mutation-checked per #208: 69 perturbations of the glue and 13 of the shared
`runtime/lua_ext.py` beside it (whose handle registry these tests are the only
host execution of), 82 red, no survivors.
"""

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GLUE_SRC = ROOT / "device" / "moycore_glue.py"
C_SRC = ROOT / "native" / "moycore" / "modmoycore.c"


# -- the C side, parsed --------------------------------------------------------


def _c_text():
    src = C_SRC.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _c_enum(first):
    """The enum block that starts with `first`, as {name: value}."""
    text = _c_text()
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
    raise AssertionError("no enum containing %s in %s" % (first, C_SRC))


def _c_define(name):
    m = re.search(r"^#define\s+%s\s+(\d+)" % name, _c_text(), flags=re.M)
    assert m, "%s is not #defined in %s" % (name, C_SRC)
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


C_CONSTS = dict(_c_enum("SNAP_BTN"))
C_CONSTS.update(_c_enum("AQ_SFX"))
C_CONSTS["AQ_SLOTS"] = _c_define("AQ_SLOTS")
C_CONSTS["AQ_MAX"] = _c_define("AQ_MAX")
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
        for verb in ("run_begin", "register", "exec", "load", "tick",
                     "tick_split", "pmem_image", "retarget", "close",
                     "active", "view", "set_sram_floor", "alloc_stats",
                     "get_global"):
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

    def _exec(self, src, chunk):
        self._log("exec", src, chunk)
        return self.exec_err

    def _load(self, src, chunk):
        self._log("load", src, chunk)
        return self.load_err

    def _tick(self, dt):
        self._log("tick", dt)
        return self.tick_err

    def _tick_split(self):
        self._log("tick_split")
        return self.split

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


class Clock:
    """`device_util`'s tick pair, injected -- no wall clock anywhere."""

    def __init__(self, ms=0):
        self.ms = ms

    def ticks_ms(self):
        return self.ms

    def diff(self, a, b):
        return a - b


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
    def __init__(self, sheet=None, tilemap=None):
        self.sheet = sheet
        self.tilemap = tilemap


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

    def button_masks(self, order):
        self.mask_calls.append(order)
        h = p = 0
        for i, name in enumerate(order):
            if name in self._held:
                h |= 1 << i
            if name in self._pressed:
                p |= 1 << i
        return h, p

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in self._pressed

    def touch_state(self):
        if self.touch_error is not None:
            raise self.touch_error
        return self.touch


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
        "text": lambda *a: log.append(("text",) + a),
        "make_layer": lambda w, h: FakeLayer(w, h, log),
        "draw_layer": lambda lay, cx, cy: log.append(("draw_layer", lay, cx, cy)),
        "image": lambda name: ("img", name) if name != "missing" else None,
        "Image": FakeLayer,
        "table": lambda name: log.append(("table", name)),
        "_moy_cfg": {"speed": 3},
    }
    ns.update(extra)
    ns["_log"] = log
    return ns


class FakeLayer:
    def __init__(self, w, h, log=None):
        self.w = w
        self.h = h
        self.log = [] if log is None else log

    def spr(self, *a):
        self.log.append(("layer.spr", self) + a)

    def cls(self, c):
        self.log.append(("layer.cls", self, c))


LUA_SRC = "function _update() end"


class World:
    """A freshly executed `moycore_glue` over a fresh fake `moycore`.

    Re-loaded per test because `_moycore`, `_ticks_ms` and `_ticks_diff` are
    MODULE globals bound at import: a leaked module would make the second test
    in a file exercise the first one's board.
    """

    NAMES = ("moycore", "device_util", "device_canvas", "lua_ext")

    def __init__(self, moycore=True, device_util=True, flat_lua_ext=True,
                 wire_fallback=b"\1" * 128):
        self.saved = {n: sys.modules.get(n, KeyError) for n in self.NAMES}
        if not flat_lua_ext:
            sys.modules["lua_ext"] = None      # no frozen flat name: the host
        self.clock = Clock()
        self.core = FakeMoycore() if moycore else None
        if moycore:
            sys.modules["moycore"] = self.core
        else:
            sys.modules["moycore"] = None      # PEP 328: raises ImportError
        if device_util:
            du = types.ModuleType("device_util")
            du._ticks_ms = self.clock.ticks_ms
            du._ticks_diff = self.clock.diff
            sys.modules["device_util"] = du
        else:
            sys.modules["device_util"] = None
        if wire_fallback is not None:
            dc = types.ModuleType("device_canvas")
            dc._PAL565_WIRE_BUF = wire_fallback
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
    """`device_boot.lua_runtime` prints "lua runtime ABSENT" off this None and
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


def test_the_cart_config_crosses_from_the_namespace(w):
    ns = make_ns()
    w.run(ns=ns)
    assert w.core.rb("cfg") is ns["_moy_cfg"]


def test_the_snapshot_array_is_sized_from_the_c_layout(w):
    run = w.run()
    assert run.snap.typecode == "i"
    assert len(run.snap) == C_CONSTS["SNAP_LEN"]
    assert run.snap.buffer_info()[1] * run.snap.itemsize >= C_CONSTS["SNAP_LEN"] * 4
    assert w.core.rb("snap") is run.snap


def test_the_audio_queue_mirrors_the_c_cap_rather_than_merely_being_big(w):
    """`AUDIO_MAX` is a copy of the C's `AQ_MAX`; the queue is one header slot
    plus `AQ_SLOTS` int16 per command."""
    run = w.run()
    assert run.AUDIO_MAX == C_CONSTS["AQ_MAX"]
    assert run.aq.typecode == "h"
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
    for shared in ("scene", "text"):
        assert shared in w.core.registered


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


def test_the_table_verb_goes_in_under_its_own_name(w):
    """#164: registering the bare name sets the GLOBAL `table` and clobbers
    Lua's library, which a ported cart's p8 shim needs for table.remove."""
    ns = make_ns()
    w.run(ns=ns)
    assert "table" not in w.core.registered
    assert w.core.registered["moy_table_verb"] is ns["table"]


def test_a_namespace_without_a_table_verb_registers_no_alias(w):
    ns = make_ns()
    del ns["table"]
    w.run(ns=ns)
    assert "moy_table_verb" not in w.core.registered


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
    # `__layer_new` and its five siblings into locals and then nils the
    # globals, so a handle registered after the exec is captured as nil and
    # `make_layer` dies on "attempt to call a nil value".
    last_register = max(i for i, v in enumerate(verbs) if v == "register")
    assert last_register < verbs.index("exec")


def test_the_prelude_is_the_shared_source_and_omits_the_fastmath_half(w):
    """`PRELUDE_FASTMATH` is moy_lua's alone: shadowing libmoy's C `rnd` with
    a Lua one is a pessimisation AND a semantic change -- libmoy's draws from
    the console rng the C seeds, which is the sequence SPEC.md 9 pins."""
    from runtime.lua_ext import (PRELUDE_TABLE, PRELUDE_HANDLES,
                                 PRELUDE_FASTMATH)

    w.run()
    src = [c[1] for c in w.core.calls if c[0] == "exec"][0]
    assert src == PRELUDE_TABLE + PRELUDE_HANDLES
    assert PRELUDE_FASTMATH not in src
    assert "function rnd(" not in src


def test_every_handle_the_prelude_consumes_is_registered(w):
    """The two halves are one source (`lua_ext`) precisely because a rename on
    one side is a layer cart dying on "index a nil value"."""
    from runtime.lua_ext import PRELUDE_HANDLES

    w.run()
    wanted = set(re.findall(r"__\w+", PRELUDE_HANDLES)) - {"__id", "__img"}
    assert wanted == {n for n in w.core.registered if n.startswith("__")}


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
    reg["__layer_cls"](0.0, 3.0)
    reg["__layer_spr"](0.0, 7.0, 1.0, 2.0, -1.0, 1.0, 0.0)
    reg["__draw_layer"](lid, 8, 9)
    assert ("layer.cls", lay, 3) in w.ns["_log"]
    assert ("layer.spr", lay, 7, 1, 2, -1, 1, 0) in w.ns["_log"]
    assert ("draw_layer", lay, 8, 9) in w.ns["_log"]
    # By TYPE, not by value: `7.0 == 7`, so a comparison alone cannot see the
    # coercion being dropped.
    spr = [c for c in w.ns["_log"] if c[0] == "layer.spr"][0]
    assert all(isinstance(v, int) for v in spr[2:]), spr


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
    w.core.register_error = ("scene", ValueError("bad verb"))
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
    assert load[1] == LUA_SRC
    assert load[2] == "@cart"


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
    """`runtime/input.py` and the boards' `device/moybyte/input.py` differ in
    BUTTONS length AND order; the snapshot must not."""
    from runtime.input import InputState as HostInput

    spec = importlib.util.spec_from_file_location(
        "_dev_input_for_glue_test", ROOT / "device" / "moybyte" / "input.py")
    device_input = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(device_input)
    snaps = []
    for cls in (HostInput, device_input.InputState):
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


def test_a_tier_without_device_util_skips_the_time_slot_entirely():
    """The host and the web runner have no `device_util`; libmoy adds the
    intra-tick elapsed term itself (`h_time_ms`)."""
    world = World(device_util=False)
    try:
        assert world.mod._ticks_ms is None and world.mod._ticks_diff is None
        run = world.run(ws=FakeWs(inp=FakeInput(cart_start_ms=10)))
        run._refresh()
        assert run.snap[C_CONSTS["SNAP_TIME_MS"]] == 0
    finally:
        world.close()


def test_the_import_of_the_clock_is_hoisted_out_of_the_frame(w):
    """It was an `import` statement executed once per frame."""
    assert w.mod._ticks_ms == w.clock.ticks_ms
    sys.modules["device_util"] = None            # gone mid-run: still fine
    run = w.run(ws=FakeWs(inp=FakeInput(cart_start_ms=0)))
    w.clock.ms = 42
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TIME_MS"]] == 42


def test_the_pointer_crosses_in_the_carts_own_coordinates(w):
    inp = FakeInput(touch=(11, 22, True, 300))
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_X"]] == 11
    assert run.snap[C_CONSTS["SNAP_TOUCH_Y"]] == 22
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == 1
    assert run.snap[C_CONSTS["SNAP_TOUCH_MS"]] == 300


def test_a_lifted_pointer_reads_down_zero_which_is_touch_returning_nil(w):
    """SPEC.md 7.3: 0 means no pointer at all."""
    inp = FakeInput(touch=(11, 22, False, 0))
    run = w.run(ws=FakeWs(inp=inp))
    run._refresh()
    assert run.snap[C_CONSTS["SNAP_TOUCH_DOWN"]] == 0


def test_a_pointer_read_that_raises_reports_no_pointer_rather_than_dying(w):
    inp = FakeInput(touch=(5, 6, True, 9))
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


def test_every_op_reaches_the_same_make_api_closure_a_python_cart_uses(w):
    """Deliberate: bank sync, the volume model the Settings surface reads and
    the diag triggers stay in one place. What the crossing deleted is the
    per-CALL trip, not the behaviour."""
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
    assert run.frame_split() == (4.2, 8.1)


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
    """`test_micropython_spike` keeps the ROUTING greps (a board still calls
    `make_moycore_runtime`); the body assertions are executed above, and both
    are only looking at the same file for as long as this holds."""
    world = World()
    try:
        assert world.mod.__file__ == str(GLUE_SRC)
    finally:
        world.close()
    for board in ("lilygo_t_deck_plus_mainline", "guition_jc3248w535",
                  "esp32_p4_wifi6_touch_lcd_7b", "web_runner"):
        toml = (ROOT / "firmware" / board / "board.toml").read_text(
            encoding="utf-8")
        assert "moycore_glue.py" in toml, board
