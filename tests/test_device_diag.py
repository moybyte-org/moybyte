"""`device/device_diag.py`, EXECUTED (#208, the single-consumer list).

678 lines whose only executable coverage was `_diag_pump` (through
`tests/test_banded_panel.py`); everything else was pinned as SOURCE STRINGS in
`tests/test_micropython_spike.py`. A substring cannot tell `us / 1000.0` from
`us / 100.0`, cannot notice a bucket wired to the wrong tuple index, cannot see
a cart gate that stopped gating, and cannot see a guard that stopped guarding --
which is exactly the shape that let `fold=0` print for weeks under a comment
calling it the on-glass proof.

So this suite aims at what a READER of the line gets: the tag, the field set,
the arithmetic (us->ms divisors, deltas, the `- base` subtractions), the
ordering, the cart gates, and the never-break-the-frame guards. Lines are
TOKENISED (`fields()`) rather than compared to literals, because the token is
what a human or a tool reads; a reformatting that keeps every field is meant to
stay green, and a field that moved to the wrong source is meant to go red.

There is no host parser for these lines to pin them against -- `runtime/
perf_line.py` owns the PERF contract and none of these -- so the reader is
modelled here.

WHAT THIS SUITE CANNOT REACH ON A HOST, stated rather than left as silence:
  * the VALUES behind `esp32.idf_heap_info`, MicroPython's `gc.mem_alloc` /
    `mem_free`, `moycore.alloc_stats` and `diag.flush_to_sd`'s actual SD write
    are hardware. They arrive here as doubles installed at the IMPORT boundary,
    so the real body runs and only the numbers are ours.
  * `_diag_calib`'s numbers are a device's interpreter cost model and mean
    nothing on a desktop CPython. Its clock is scripted instead, which pins the
    ORDER of the five benchmarks and the `- base` subtraction -- the parts that
    are code -- and pins nothing about the magnitudes, which are not.
  * `_diag_pump` is deliberately absent: `tests/test_banded_panel.py` already
    drives it against a real `BandedCompositor`.
"""

import contextlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device"


def _load_device_diag():
    """Load the REAL `device/device_diag.py`, resolving its sibling import the
    way the frozen device tree does (`from device_util import ...`, flat).

    A FRESH module object per test on purpose: `_CALIB_DONE`, `_GC_TICK` and
    `_GC_BASE` are module-level one-shots, and a shared module would make the
    cadence tests order-dependent.
    """
    if "device_util" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "device_util", DEVICE / "device_util.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["device_util"] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location(
        "device_diag", DEVICE / "device_diag.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def dd():
    return _load_device_diag()


def test_the_module_under_test_is_the_shared_body_and_not_a_staged_copy(dd):
    """Three boards stage a copy of this file at build time. A suite that
    drifted onto one of those would be testing an artifact."""
    assert dd.__file__ == str(DEVICE / "device_diag.py")


# -- the doubles ---------------------------------------------------------------


class Obj:
    """Anything the diag functions read. Every field is a getattr with a
    default, so ABSENCE is a case the body handles and `Obj()` expresses it."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeDiag:
    def __init__(self):
        self.lines = []

    def log(self, tag, msg):
        self.lines.append((tag, msg))

    def tags(self):
        return [t for t, _m in self.lines]

    def line(self, tag):
        for t, m in self.lines:
            if t == tag:
                return m
        return None

    def one(self, tag):
        m = self.line(tag)
        assert m is not None, "no %s line: %r" % (tag, self.lines)
        return fields(m)


_GROUP = re.compile(r"^(\w*)\((.*)\)$")


def split_top(msg):
    """Whitespace tokens, except inside parens -- which is what a reader does
    with `raw(logic=.. render=..)`."""
    out, depth, cur = [], 0, []
    for ch in msg:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == " " and depth == 0:
            if cur:
                out.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def fields(msg):
    """A diag line body -> {name: value}, `name(...)` groups nested, bare words
    under `_bare`. An anonymous parenthesised group (DRAWBRK's `(bg=..)`, which
    sits INSIDE render on purpose) lands under `_paren`."""
    out = {"_bare": []}
    for tok in split_top(msg):
        m = _GROUP.match(tok)
        if m:
            out[m.group(1) or "_paren"] = fields(m.group(2))
        elif "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
        else:
            out["_bare"].append(tok)
    return out


@contextlib.contextmanager
def modules(**mods):
    """Install fakes at the IMPORT boundary and take them back out again.

    `device_diag` imports `moycore` / `moy_lua` / `esp32` / `gc` lazily inside
    the functions that need them, so this is the only seam where a host can
    supply them -- and it is process-wide, so the teardown is the point.
    """
    saved = {k: sys.modules.get(k, KeyError) for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is KeyError:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def clock_ms(dd, *values):
    """Script `_ticks_ms` for the module under test. `_ticks_diff` stays real."""
    seq = iter(values)
    dd._ticks_ms = lambda: next(seq)


def clock_us(dd, *values):
    seq = iter(values)
    dd._ticks_us = lambda: next(seq)


# == _diag_flush ===============================================================


class FlushDiag(FakeDiag):
    def __init__(self, boom=False):
        FakeDiag.__init__(self)
        self.flushed = []
        self.boom = boom

    def flush_to_sd(self, with_sd):
        self.flushed.append(with_sd)
        if self.boom:
            raise OSError("no card")
        return True


def test_a_flush_with_no_diag_ring_costs_the_caller_nothing(dd):
    assert dd._diag_flush(None, Obj(can_manage=True)) == 0


def test_the_flush_is_SKIPPED_where_there_is_no_writable_store(dd):
    """`can_manage` is False on the embedded-carts fallback (carts_root None).
    Writing there is not a slow no-op, it is a write with nowhere to go."""
    diag = FlushDiag()
    assert dd._diag_flush(diag, Obj(can_manage=False)) == 0
    assert diag.flushed == []
    assert dd._diag_flush(diag, Obj()) == 0          # absent reads as False
    assert diag.flushed == []


def test_the_flush_hands_over_the_workstations_LIVE_sd_wrapper(dd):
    """The wrapper is the whole payload: `with_sd_live` mounts on the native
    single-bus path and keeps the card resident. `moybyte_diag.flush_to_sd`
    returns False rather than writing when it is None."""
    wrapper = object()
    diag = FlushDiag()
    dd._diag_flush(diag, Obj(can_manage=True, _with_sd=wrapper))
    assert diag.flushed == [wrapper]

    diag = FlushDiag()
    dd._diag_flush(diag, Obj(can_manage=True))       # no wrapper on this board
    assert diag.flushed == [None]


def test_the_flush_returns_its_own_elapsed_ms_so_the_caller_neednt_time_it(dd):
    clock_ms(dd, 1000, 1075)
    assert dd._diag_flush(FlushDiag(), Obj(can_manage=True, _with_sd=1)) == 75


def test_a_failing_flush_reports_zero_ms_and_never_reaches_the_frame_loop(dd):
    """`_t["sd"]` feeds LOOP/HITCH. A raise here would take the frame with it."""
    wrapper = object()
    diag = FlushDiag(boom=True)
    assert dd._diag_flush(diag, Obj(can_manage=True, _with_sd=wrapper)) == 0
    assert diag.flushed == [wrapper]               # it was attempted, then ate it


# == _diag_hitch ===============================================================


def hitch_ws(**kw):
    base = dict(
        perf_sample=lambda: ("cart", 30, 5.0, 9.0),
        perf_breakdown=lambda: (1.5, 2.5, 3.5, 4.5),
        perf_breakdown_raw=lambda: (1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        canvas=Obj(_lcopy_trips=7),
    )
    base.update(kw)
    return Obj(**base)


def hitch(dd, diag, ws, comp=Obj(pump_last_us=2500), **kw):
    args = dict(elapsed=150, kbd_ms=11, inp_ms=12, sb_ms=13, ws_ms=140,
                diag_ms=14, sd_ms=15, web_ms=16)
    args.update(kw)
    return dd._diag_hitch(diag, ws, comp, args["elapsed"], args["kbd_ms"],
                          args["inp_ms"], args["sb_ms"], args["ws_ms"],
                          args["diag_ms"], args["sd_ms"], args["web_ms"],
                          *([args["hi_ms"], args["hp_ms"]]
                            if "hi_ms" in args else []))


def test_the_hitch_line_names_every_loop_stage_from_its_own_argument(dd):
    """Every value distinct, so a transposed pair cannot pass by coincidence.
    `frame=` carries a `ms` suffix the other stages do not."""
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws())
    f = diag.one("HITCH")
    assert f["frame"] == "150ms"
    assert (f["kbd"], f["inp"], f["sb"], f["ws"]) == ("11", "12", "13", "140")
    assert (f["diag"], f["sdflush"], f["web"]) == ("14", "15", "16")


def test_the_hitch_pump_is_microseconds_and_the_trip_count_is_the_canvass(dd):
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(), comp=Obj(pump_last_us=2500))
    f = diag.one("HITCH")
    assert f["pump"] == "2.5"
    assert f["lw"] == "7"


def test_a_board_with_no_flush_meters_reports_the_never_measured_sentinel(dd):
    """`lw=-1` is "this canvas has no async layer copy", which is NOT the same
    as zero trips -- the #208 doctrine, and the reason the default is -1."""
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(canvas=None), comp=Obj())
    f = diag.one("HITCH")
    assert f["pump"] == "0.0"
    assert f["lw"] == "-1"


def test_the_hitch_raw_split_is_the_unsmoothed_frame_and_drops_its_sixth(dd):
    """`perf_breakdown_raw` returns (upd, cart, audio, chrome, flush, draw);
    the line prints five of them under the names logic/render/audio/chrome/
    flush. The trailing `draw` is deliberately not on the line."""
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws())
    raw = diag.one("HITCH")["raw"]
    assert raw == {"_bare": [], "logic": "1.0", "render": "2.0",
                   "audio": "3.0", "chrome": "4.0", "flush": "5.0"}


def test_a_console_with_no_raw_split_falls_back_to_the_EMA_line(dd):
    """The v2 shape: `flush=` off perf_sample()[2] and an `ema(...)` of
    perf_breakdown's logic/render/chrome -- index 2 (audio) is skipped, so a
    naive b[0..2] would read render into chrome."""
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(perf_breakdown_raw=None))
    f = diag.one("HITCH")
    assert "raw" not in f
    assert f["flush"] == "5.0"                     # perf_sample()[2]
    assert f["ema"] == {"_bare": [], "logic": "1.5", "render": "2.5",
                        "chrome": "4.5"}


def test_the_ema_line_says_minus_one_when_there_is_no_sample_to_read(dd):
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(perf_breakdown_raw=None, perf_sample=lambda: None))
    assert diag.one("HITCH")["flush"] == "-1.0"


def test_the_ws_lump_is_split_into_handle_input_pointer_and_frame(dd):
    """#183: `frm` is the REMAINDER -- ws minus hi minus hp -- which is the
    whole point (a 37s stall read ws=37156 with raw() summing to 20ms)."""
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(), hi_ms=30, hp_ms=20)
    assert diag.one("HITCH")["ws"] == {"_bare": [], "hi": "30", "hp": "20",
                                       "frm": "90"}


def test_a_caller_that_measured_no_phases_gets_no_ws_split_at_all(dd):
    """hi_ms defaults to -1 = "not measured". Printing hi=-1 hp=-1 frm=142
    would be three numbers that are not true."""
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws())                    # the two defaults
    f = diag.one("HITCH")
    assert f["ws"] == "140"                        # still the plain lump
    assert not isinstance(f["ws"], dict)


def test_the_pointer_split_rides_only_a_frame_that_spent_time_in_the_pointer(dd):
    """#184: hp( ) is the split of hp=, so hp_ms must be positive for it to
    mean anything -- and the probe must have something to report."""
    probe = lambda: (9.5, 1.5, 4.5, "wm", "home", 3)

    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(perf_pointer=probe), hi_ms=30, hp_ms=20)
    assert diag.one("HITCH")["hp"] == {
        "_bare": [], "tot": "9.5", "pre": "1.5", "worst": "4.5@wm",
        "claim": "home", "n": "3"}

    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(perf_pointer=probe), hi_ms=30, hp_ms=0)
    f = diag.one("HITCH")
    assert "hp" not in f                           # no group of its own...
    assert f["ws"]["hp"] == "0"                    # ...only the stage inside ws(

    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(perf_pointer=lambda: None), hi_ms=30, hp_ms=20)
    f = diag.one("HITCH")
    assert "hp" not in f
    assert f["ws"]["hp"] == "20"


def test_a_launcher_hitch_carries_the_home_split_and_a_cart_one_does_not(dd):
    """DRAWBRK/CHROMEBRK are cart-gated, so `_pf_home` is the ONE split a
    launcher hitch gets. It is None while a cart runs."""
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(_pf_home=(21, 22, 23)))
    assert diag.one("HITCH")["home"] == {"_bare": [], "wp": "21", "grid": "22",
                                         "bar": "23"}

    diag = FakeDiag()
    hitch(dd, diag, hitch_ws())
    assert "home" not in diag.one("HITCH")


def test_the_three_optional_groups_keep_their_order_on_the_line(dd):
    """ws( ) then hp( ) then home( ): hp splits the hp= inside ws(, so it reads
    as a drill-down; home is the frame's other anatomy and comes last."""
    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(_pf_home=(21, 22, 23),
                             perf_pointer=lambda: (9.5, 1.5, 4.5, "wm", "h", 3)),
          hi_ms=30, hp_ms=20)
    msg = diag.line("HITCH")
    assert msg.index(" ws(") < msg.index(" hp(") < msg.index(" home(")


def test_a_hitch_on_a_broken_console_stays_silent_instead_of_raising(dd):
    """This runs from `FrameLoop.account`, after the frame is already late.
    A diagnostic that throws there turns a slow frame into a dead console."""
    def boom():
        raise RuntimeError("perf gone")

    diag = FakeDiag()
    hitch(dd, diag, hitch_ws(perf_sample=boom))
    assert diag.lines == []


# == _diag_drawbrk =============================================================


def brk_ws(**kw):
    base = dict(perf_sample=lambda: ("cart", 30, 5.0, 9.0),
                perf_breakdown=lambda: (1.25, 2.5, 3.75, 4.0))
    base.update(kw)
    return Obj(**base)


def test_drawbrk_needs_a_diag_ring_and_a_running_cart(dd):
    diag = FakeDiag()
    dd._diag_drawbrk(None, brk_ws())
    dd._diag_drawbrk(diag, brk_ws(perf_sample=lambda: None))
    assert diag.lines == []


def test_drawbrk_splits_the_draw_into_logic_render_audio_and_chrome(dd):
    diag = FakeDiag()
    dd._diag_drawbrk(diag, brk_ws())
    assert diag.one("DRAWBRK") == {"_bare": [], "logic": "1.25",
                                   "render": "2.50", "audio": "3.75",
                                   "chrome": "4.00"}


def test_the_backdrop_share_is_printed_INSIDE_render_not_beside_it(dd):
    """#172: `bg` is the cart's own drawing, a SUB-slice of render. Shown as a
    peer it re-creates the reading that sent #172 hunting the shell."""
    diag = FakeDiag()
    dd._diag_drawbrk(diag, brk_ws(perf_backdrop=lambda: 0.375))
    msg = diag.line("DRAWBRK")
    assert msg.index("render=") < msg.index("(bg=") < msg.index("audio=")
    assert fields(msg)["_paren"]["bg"] == "0.38"


def test_a_console_with_no_backdrop_meter_prints_no_bg_term(dd):
    diag = FakeDiag()
    dd._diag_drawbrk(diag, brk_ws())
    assert "bg=" not in diag.line("DRAWBRK")


def test_the_batch_line_follows_the_drawbrk_and_proves_the_coalesce(dd):
    """#63: flushes=1/maxrun=N means the cart's N-sprite loop became ONE native
    blit_batch; flushes=N/maxrun=1 means it did not."""
    diag = FakeDiag()
    # sprites != maxrun, or a transposed pair reads as agreement.
    dd._diag_drawbrk(diag, brk_ws(perf_batch=lambda: (1, 120, 96)))
    assert diag.tags() == ["DRAWBRK", "BATCH"]
    assert diag.one("BATCH") == {"_bare": [], "flushes": "1", "sprites": "120",
                                 "maxrun": "96"}


def test_a_console_with_no_batch_profiler_prints_no_batch_line(dd):
    diag = FakeDiag()
    dd._diag_drawbrk(diag, brk_ws())
    assert diag.tags() == ["DRAWBRK"]


# == _diag_draw2 ===============================================================


def canvas(**kw):
    base = dict(_t_layer_us=1000, _t_batch_us=2000, _t_map_us=3000,
                _t_text_us=4000, _t_fill_us=5000)
    base.update(kw)
    return Obj(**base)


def test_draw2_is_cart_gated_and_needs_a_canvas(dd):
    diag = FakeDiag()
    dd._diag_draw2(diag, Obj(canvas=None, perf_sample=lambda: ("c",)))
    dd._diag_draw2(diag, Obj(canvas=canvas(), perf_sample=lambda: None))
    dd._diag_draw2(None, Obj(canvas=canvas(), perf_sample=lambda: ("c",)))
    assert diag.lines == []


def test_draw2_reports_each_native_op_in_milliseconds(dd):
    diag = FakeDiag()
    dd._diag_draw2(diag, Obj(canvas=canvas(), perf_sample=lambda: ("c",)))
    f = diag.one("DRAW2")
    assert (f["layer"], f["batch"], f["map"]) == ("1.00ms", "2.00ms", "3.00ms")
    assert (f["text"], f["fill"]) == ("4.00ms", "5.00ms")


def test_the_gated_microseconds_are_FOLDED_into_the_bucket_they_belong_to(dd):
    """A #155 gated rect never enters the Python method holding `_t_fill_us`,
    so zoomed celeste read fill=0.00 with 20.6ms in no bucket at all.
    `gate_counts()` is (fills, texts, fill_us, text_us) -- the COUNTS go to
    `gated(...)` and the MICROSECONDS into text=/fill=. Transposing the pairs
    is the mistake this pins."""
    # The two sums must DIFFER, or folding gf into text and gt into fill reads
    # as agreement.
    cv = canvas(gate_counts=lambda: (17, 23, 6000, 9000))
    diag = FakeDiag()
    dd._diag_draw2(diag, Obj(canvas=cv, perf_sample=lambda: ("c",)))
    f = diag.one("DRAW2")
    assert f["fill"] == "11.00ms"                  # 5000 + 6000 us
    assert f["text"] == "13.00ms"                  # 4000 + 9000 us
    assert f["gated"] == {"_bare": [], "fill": "17", "text": "23"}


def test_a_canvas_with_no_gates_still_prints_the_whole_line(dd):
    diag = FakeDiag()
    dd._diag_draw2(diag, Obj(canvas=canvas(), perf_sample=lambda: ("c",)))
    assert diag.one("DRAW2")["gated"] == {"_bare": [], "fill": "0", "text": "0"}


# == _diag_draw3 ===============================================================


def test_draw3_names_the_rest_of_render_and_the_residual_after_it(dd):
    """`named` is the sum of all EIGHT us buckets (DRAW2's five plus spr/shape/
    img); `resid` is the DRAWBRK render EMA minus that -- what is genuinely
    interpreter dispatch."""
    cv = canvas(_t_spr_us=6000, _t_shape_us=7000, _t_img_us=8000,
                _n_spr=42, _n_shape=9)
    diag = FakeDiag()
    dd._diag_draw3(diag, Obj(canvas=cv, perf_sample=lambda: ("c",),
                             perf_breakdown=lambda: (0.0, 40.0, 0.0, 0.0)))
    f = diag.one("DRAW3")
    assert (f["spr"], f["shape"], f["img"]) == ("6.00ms", "7.00ms", "8.00ms")
    assert (f["nspr"], f["nshape"]) == ("42", "9")
    assert f["named"] == "36.00ms"                 # 1+2+3+4+5+6+7+8 ms
    assert f["resid"] == "4.00ms"                  # render 40 - named 36


def test_a_residual_can_go_NEGATIVE_and_must_print_that_honestly(dd):
    """render is an EMA and the buckets are last-frame, so the two disagree
    frame to frame. A clamp at zero would hide the disagreement it exists to
    show -- it is the TREND that answers the question."""
    cv = canvas(_t_spr_us=0, _t_shape_us=0, _t_img_us=0)
    diag = FakeDiag()
    dd._diag_draw3(diag, Obj(canvas=cv, perf_sample=lambda: ("c",),
                             perf_breakdown=lambda: (0.0, 5.0, 0.0, 0.0)))
    assert diag.one("DRAW3")["resid"] == "-10.00ms"


def test_draw3_is_cart_gated_and_needs_a_canvas(dd):
    diag = FakeDiag()
    dd._diag_draw3(diag, Obj(canvas=None, perf_sample=lambda: ("c",)))
    dd._diag_draw3(diag, Obj(canvas=canvas(), perf_sample=lambda: None))
    dd._diag_draw3(None, Obj(canvas=canvas(), perf_sample=lambda: ("c",)))
    assert diag.lines == []


# == _diag_luamem ==============================================================


class FakeMoycore:
    def __init__(self, active, stats):
        self._active = active
        self._stats = stats

    def active(self):
        return self._active

    def alloc_stats(self):
        return self._stats


def esp32_with(*regions):
    """`idf_heap_info(HEAP_DATA)` -> ((total, free, largest, ...), ...)."""
    return Obj(HEAP_DATA=4, idf_heap_info=lambda _cap: regions)


def test_luamem_prints_moycores_seven_fields_and_says_which_core_it_is(dd):
    """moycore's alloc_stats stops at seven; a short tuple must print a short
    line rather than index off the end of it. The last three are the
    small-object pool -- live/capacity and the chunk count -- and the gap
    between them is PSRAM the VM holds that `psram` alone does not show."""
    core = FakeMoycore(True, (2048, 4096, 8192, 5, 10240, 32768, 3))
    diag = FakeDiag()
    with modules(moycore=core):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: ("c",)))
    f = diag.one("LUAMEM")
    assert (f["sram"], f["psram"], f["peak"]) == ("2.0KB", "4.0KB", "8.0KB")
    assert f["denied"] == "5"
    assert f["pool"] == "10.0/32.0KB"
    assert f["ch"] == "3"
    assert f["core"] == "1"


def test_luamem_falls_through_to_the_old_runtime_when_moycore_is_idle(dd):
    """A 16-field tuple takes the long line: `denied` becomes KILOBYTES (st[7]),
    the size classes come from st[8..15] and the call counts from st[3]/st[4] --
    a completely different index map from the short line's."""
    st = (1024, 2048, 3072, 11, 22, 0, 0, 4096,
          5120, 6144, 7168, 8192, 9216, 10240, 11264, 12288)
    diag = FakeDiag()
    with modules(moycore=FakeMoycore(False, None),
                 moy_lua=Obj(alloc_stats=lambda: st)):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: ("c",)))
    f = diag.one("LUAMEM")
    assert f["denied"] == "4KB"                    # st[7]/1024, not st[3]
    assert f["sc"] == "5.0/6.0/7.0/8.0"
    assert f["pc"] == "9.0/10.0/11.0/12.0"
    assert f["n"] == "11/22"
    assert "core" not in f


def test_luamem_is_silent_when_no_lua_vm_is_holding_anything(dd):
    """`live == 0` means no cart VM. The guard is st[0] + st[1], so a VM living
    entirely in PSRAM still reports."""
    diag = FakeDiag()
    with modules(moycore=FakeMoycore(True, (0, 0, 8192, 0, 0, 0, 0))):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: ("c",)))
    assert diag.lines == []

    with modules(moycore=FakeMoycore(True, (0, 4096, 8192, 0, 0, 32768, 1))):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: ("c",)))
    assert diag.line("LUAMEM") is not None


def test_luamem_counts_only_the_INTERNAL_heap_regions(dd):
    """>=1MB regions are PSRAM (device_util.sram_census uses the same rule).
    `int` is free/largest in KB, and the largest is a MAX across regions, not
    the last one seen."""
    diag = FakeDiag()
    with modules(moycore=FakeMoycore(True, (2048, 4096, 8192, 0, 1024, 32768, 1)),
                 # the biggest block is NOT the last region seen, or a lost
                 # max() reads as agreement
                 esp32=esp32_with((65536, 51200, 61440),
                                  (32768, 20480, 40960),
                                  (8 * 1024 * 1024, 7 * 1024 * 1024, 999999))):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: ("c",)))
    assert diag.one("LUAMEM")["int"] == "70/60k"   # 71680//1024, 61440//1024


def test_luamem_still_prints_where_the_heap_probe_is_unavailable(dd):
    """A board with no `esp32` module is not a reason to lose the Lua numbers;
    the census rides along, guarded separately."""
    diag = FakeDiag()
    with modules(moycore=FakeMoycore(True, (2048, 4096, 8192, 0, 1024, 32768, 1)),
                 esp32=None):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: ("c",)))
    assert diag.one("LUAMEM")["int"] == "0/0k"


def test_luamem_is_cart_gated_and_survives_a_runtime_with_no_stats(dd):
    diag = FakeDiag()
    with modules(moycore=FakeMoycore(True, (2048, 4096, 8192, 0, 1024, 32768, 1))):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: None))
    dd._diag_luamem(None, Obj(perf_sample=lambda: ("c",)))
    assert diag.lines == []
    # moycore idle and no moy_lua at all -- the shipped shape since the old Lua
    # runtime was deleted. The ImportError must reach the outer guard.
    with modules(moycore=FakeMoycore(False, None), moy_lua=None):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: ("c",)))
    assert diag.lines == []


def test_a_build_with_no_moycore_at_all_still_asks_the_other_runtime(dd):
    """The `except ImportError` around the moycore probe is what makes the
    fallback reachable on a board that ships one runtime and not the other."""
    diag = FakeDiag()
    # Seven fields, i.e. the short form: what is under test is that the
    # ImportError reaches the fallback at all, and the only Lua runtime left in
    # the tree is moycore's, so its tuple is the one shape to fall back with.
    with modules(moycore=None,
                 moy_lua=Obj(alloc_stats=lambda: (1024, 0, 0, 0, 0, 0, 0)),
                 esp32=None):
        dd._diag_luamem(diag, Obj(perf_sample=lambda: ("c",)))
    assert diag.one("LUAMEM")["sram"] == "1.0KB"


# == _diag_chromebrk ===========================================================


def test_chromebrk_splits_the_chrome_remainder_five_ways(dd):
    diag = FakeDiag()
    dd._diag_chromebrk(diag, Obj(perf_sample=lambda: ("c",),
                                 perf_chrome=lambda: (1.0, 2.0, 3.0, 4.0, 5.0)))
    assert diag.one("CHROMEBRK") == {"_bare": [], "bar": "1.00", "cmp": "2.00",
                                     "cur": "3.00", "stk": "4.00",
                                     "other": "5.00"}


def test_a_console_older_than_the_stack_bucket_prints_the_four_field_line(dd):
    """`other` used to be a residual of a residual; the short line is what a
    pre-2026-08-14 console still emits, and index 3 is `other` there."""
    diag = FakeDiag()
    dd._diag_chromebrk(diag, Obj(perf_sample=lambda: ("c",),
                                 perf_chrome=lambda: (1.0, 2.0, 3.0, 9.0)))
    f = diag.one("CHROMEBRK")
    assert "stk" not in f
    assert f["other"] == "9.00"


def test_chromebrk_needs_a_running_cart_and_a_chrome_probe(dd):
    diag = FakeDiag()
    dd._diag_chromebrk(diag, Obj(perf_sample=lambda: None,
                                 perf_chrome=lambda: (1.0,) * 5))
    dd._diag_chromebrk(diag, Obj(perf_sample=lambda: ("c",)))
    dd._diag_chromebrk(None, Obj(perf_sample=lambda: ("c",)))
    assert diag.lines == []


def test_a_throwing_chrome_probe_never_breaks_the_diag_tick(dd):
    def boom():
        raise ValueError("no")

    diag = FakeDiag()
    dd._diag_chromebrk(diag, Obj(perf_sample=lambda: ("c",), perf_chrome=boom))
    assert diag.lines == []


# == _diag_layerbrk ============================================================


def test_layerbrk_names_the_stack_walk_per_layer_in_the_order_given(dd):
    """`perf_layers` sorts dearest first; this line must not re-order it."""
    rows = (("wall", 3.0), ("shelf", 2.0), ("bar", 1.0))
    diag = FakeDiag()
    dd._diag_layerbrk(diag, Obj(perf_layers=lambda: rows))
    msg = diag.line("LAYERBRK")
    f = fields(msg)
    assert f["n"] == "3"
    assert f["sum"] == "6.00"
    assert msg.index("wall=") < msg.index("shelf=") < msg.index("bar=")
    assert (f["wall"], f["shelf"], f["bar"]) == ("3.00", "2.00", "1.00")


def test_the_sum_covers_every_layer_even_where_the_tail_is_truncated(dd):
    """Six rows are printed and the rest are counted; a `sum` over the printed
    head only would be read as covering fewer layers than it does -- which is
    why the truncation is NAMED."""
    rows = tuple(("l%d" % i, float(i)) for i in range(8))    # 0..7, sum 28
    diag = FakeDiag()
    dd._diag_layerbrk(diag, Obj(perf_layers=lambda: rows))
    msg = diag.line("LAYERBRK")
    assert fields(msg)["sum"] == "28.00"
    assert msg.endswith(" +2 more")
    assert "l6=" not in msg and "l7=" not in msg
    assert "l5=5.00" in msg


def test_exactly_six_layers_are_printed_whole_with_no_more_suffix(dd):
    rows = tuple(("l%d" % i, 1.0) for i in range(6))
    diag = FakeDiag()
    dd._diag_layerbrk(diag, Obj(perf_layers=lambda: rows))
    assert "more" not in diag.line("LAYERBRK")


def test_the_frame_edges_lead_the_layer_line_when_the_console_measures_them(dd):
    """pre + sum + flush + post should account for the loop's whole `frm`, so
    the edges belong at the FRONT of the anatomy, before n=."""
    diag = FakeDiag()
    dd._diag_layerbrk(diag, Obj(perf_layers=lambda: (("a", 1.0),),
                                perf_frame_edges=lambda: (0.5, 0.25)))
    msg = diag.line("LAYERBRK")
    assert msg.startswith("pre=0.50 post=0.25 n=1 ")

    diag = FakeDiag()
    dd._diag_layerbrk(diag, Obj(perf_layers=lambda: (("a", 1.0),)))
    assert diag.line("LAYERBRK").startswith("n=1 ")


def test_layerbrk_is_NOT_cart_gated_but_is_silent_with_nothing_to_report(dd):
    """Deliberately unlike DRAWBRK/CHROMEBRK: the launcher and editor walks
    have no other instrument at all, so this ws double has no perf_sample."""
    diag = FakeDiag()
    dd._diag_layerbrk(diag, Obj(perf_layers=lambda: (("a", 1.0),)))
    assert diag.line("LAYERBRK") is not None

    diag = FakeDiag()
    dd._diag_layerbrk(diag, Obj(perf_layers=lambda: ()))
    dd._diag_layerbrk(diag, Obj())
    dd._diag_layerbrk(None, Obj(perf_layers=lambda: (("a", 1.0),)))
    assert diag.lines == []


# == _diag_homebrk =============================================================


def test_homebrk_prints_the_launcher_frames_three_sections(dd):
    diag = FakeDiag()
    dd._diag_homebrk(diag, Obj(_pf_home=(7, 8, 9)))
    assert diag.one("HOMEBRK") == {"_bare": [], "wp": "7", "grid": "8",
                                   "bar": "9"}


def test_homebrk_is_silent_unless_the_LAST_frame_drew_the_home_screen(dd):
    """`_pf_home` is None while idle or inside a cart/app; a stale line would
    read as the launcher having repainted when it did not.

    An EQUIVALENT MUTANT lives here: weakening `if home:` to something always
    true is unobservable, because `"wp=%d..." % None` then raises into the
    body's own blanket except and the line is dropped anyway. The guard is what
    makes the silence deliberate rather than incidental, and no test can tell
    those apart from the outside -- do not "simplify" it away expecting one to.
    """
    diag = FakeDiag()
    dd._diag_homebrk(diag, Obj(_pf_home=None))
    dd._diag_homebrk(diag, Obj())
    dd._diag_homebrk(None, Obj(_pf_home=(1, 2, 3)))
    assert diag.lines == []


# == _diag_loop ================================================================
#
# acc is [n, frame, kbd, inp, sb, ws, web, diag, sd, sleep, hi, hp] in ms
# (moy_runtime.run_desktop's _account); the docstring here still names the
# pre-#183 ten-element shape.


def acc_of(n=2, frame=40, kbd=2, inp=4, sb=6, ws=20, web=8, diag=10, sd=12,
           sleep=0, hi=6, hp=4):
    return [n, frame, kbd, inp, sb, ws, web, diag, sd, sleep, hi, hp]


def test_every_loop_stage_is_a_MEAN_over_the_window_not_a_total(dd):
    """n= is the frame count and every other field divides by it; a lost
    divisor turns a 2ms stage into a window total nobody would question."""
    diag = FakeDiag()
    dd._diag_loop(diag, Obj(), acc_of(n=2))
    f = diag.one("LOOP")
    assert f["n"] == "2"
    assert (f["frame"], f["kbd"], f["inp"], f["sb"]) == ("20.0", "1.0", "2.0",
                                                         "3.0")
    assert (f["ws"], f["web"], f["diag"], f["sd"]) == ("10.0", "4.0", "5.0",
                                                       "6.0")


def test_the_loop_line_splits_ws_the_same_way_the_hitch_line_does(dd):
    """frm is the remainder ws - hi - hp, averaged; the same #183 split."""
    diag = FakeDiag()
    dd._diag_loop(diag, Obj(), acc_of(n=2, ws=20, hi=6, hp=4))
    assert diag.one("LOOP")["_paren"] == {"_bare": [], "hi": "3.0",
                                          "hp": "2.0", "frm": "5.0"}


def test_other_is_the_frame_left_over_after_every_named_stage(dd):
    """The number the 2026-07-29 hunt needed: ~4.6ms per frame going somewhere
    no counter watched. Driven with sleep=0 so it pins the seven work terms
    regardless of how the sleep term below is resolved."""
    diag = FakeDiag()
    dd._diag_loop(diag, Obj(), acc_of(n=1, frame=100, kbd=1, inp=2, sb=3,
                                      ws=20, web=4, diag=5, sd=6, sleep=0))
    assert diag.one("LOOP")["other"] == "59.0"     # 100 - (1+2+3+20+4+5+6)


@pytest.mark.xfail(strict=True, reason=(
    "device/device_diag.py:400 -- `stages` includes acc[9] (the pacing sleep) "
    "while acc[1] (`frame`) is accumulated BEFORE the sleep and excludes it "
    "(moy_runtime._account, and device_boot.FrameLoop.step measures elapsed "
    "before pace()). So `other` = unaccounted work MINUS deliberate idle, and "
    "reads NEGATIVE on any paced loop -- the ordinary desk. The line's own "
    "commit message says sleep is 'carried separately so a paced loop cannot "
    "read as a slow one'. #208 says report, do not fix."))
def test_the_pacing_sleep_is_carried_beside_other_and_not_subtracted_from_it(dd):
    diag = FakeDiag()
    dd._diag_loop(diag, Obj(), acc_of(n=1, frame=10, kbd=1, inp=1, sb=1, ws=2,
                                      web=1, diag=1, sd=1, sleep=6))
    f = diag.one("LOOP")
    assert f["sleep"] == "6.0"
    assert f["other"] == "2.0"                     # 10 - (1+1+1+2+1+1+1)


def test_the_frameskip_gate_is_printed_beside_the_numbers_it_redefines(dd):
    """#77: with it on, logic ticks every loop frame but render/composite/flush
    run every second one, so a cart reads far higher fps. #66's last full-roster
    T-Deck session could not be compared with a later run because no log said
    which way the toggle sat."""
    diag = FakeDiag()
    dd._diag_loop(diag, Obj(frameskip=True), acc_of())
    assert diag.one("LOOP")["skip"] == "1"

    diag = FakeDiag()
    dd._diag_loop(diag, Obj(frameskip=False), acc_of())
    dd._diag_loop(diag, Obj(), acc_of())
    assert [fields(m)["skip"] for _t, m in diag.lines] == ["0", "0"]


def test_an_empty_window_prints_nothing_rather_than_dividing_by_zero(dd):
    """Same equivalent-mutant shape as HOMEBRK: weakening `acc[0] <= 0` only
    moves the silence from a return to a ZeroDivisionError inside the blanket
    except. The guard says the intent out loud."""
    diag = FakeDiag()
    dd._diag_loop(diag, Obj(), acc_of(n=0))
    dd._diag_loop(diag, Obj(), [])
    dd._diag_loop(None, Obj(), acc_of())
    assert diag.lines == []


# == _diag_webhost =============================================================


def test_the_webhost_line_reports_the_listener_STATE_not_the_symptom(dd):
    """Written because a board served one page then refused every connection
    and `web=0.0` could not tell "dead" from "idle"."""
    wh = Obj(sock=object(), serving=True, error=None, url=lambda: "http://x/")
    diag = FakeDiag()
    with modules(esp32=esp32_with((65536, 51200, 40960))):
        dd._diag_webhost(diag, Obj(webhost=wh))
    f = diag.one("WEBHOST")
    assert f["sock"] == "open"
    assert f["serving"] == "True"
    assert f["err"] == "-"
    assert f["url"] == "http://x/"
    assert f["sram"] == "50k"                      # 51200 // 1024


def test_a_missing_listener_is_the_interesting_case_and_says_none(dd):
    wh = Obj(sock=None, serving=False, error="ENOMEM", url=lambda: None)
    diag = FakeDiag()
    with modules(esp32=None, gc=None):
        dd._diag_webhost(diag, Obj(webhost=wh))
    f = diag.one("WEBHOST")
    assert f["sock"] == "none"
    assert f["err"] == "ENOMEM"
    assert f["url"] == "-"


def test_the_line_is_printed_even_when_the_row_is_switched_OFF(dd):
    """The first version returned early in exactly that case, and then "is it
    off, or is the diagnostic not running?" became the question the diagnostic
    existed to answer."""
    diag = FakeDiag()
    with modules(esp32=None, gc=None):
        dd._diag_webhost(diag, Obj(webhost=Obj(sock=None, serving=False)))
    assert diag.one("WEBHOST")["serving"] == "False"


def test_the_free_memory_is_INTERNAL_sram_and_not_the_gc_heap(dd):
    """The first version printed `gc.mem_free()` and reported 6045k while
    nothing could connect -- the MicroPython heap in PSRAM, which is not the
    pool lwIP allocates a listening PCB from. The esp32 census SUMS every
    region's free; the gc heap is only the fallback."""
    wh = Obj(sock=None, serving=False, url=lambda: "")
    diag = FakeDiag()
    with modules(esp32=esp32_with((65536, 40960, 1), (32768, 10240, 1)),
                 gc=Obj(mem_free=lambda: 6045 * 1024)):
        dd._diag_webhost(diag, Obj(webhost=wh))
    assert diag.one("WEBHOST")["sram"] == "50k"    # (40960+10240)//1024

    diag = FakeDiag()
    with modules(esp32=None, gc=Obj(mem_free=lambda: 6045 * 1024)):
        dd._diag_webhost(diag, Obj(webhost=wh))
    assert diag.one("WEBHOST")["sram"] == "6045k"


def test_a_webhost_whose_url_throws_still_reports_the_socket(dd):
    """The url is the least important field on the line and must not be able
    to suppress the one the line was written for."""
    def boom():
        raise OSError("no ip")

    diag = FakeDiag()
    with modules(esp32=None, gc=None):
        dd._diag_webhost(diag, Obj(webhost=Obj(sock=object(), url=boom)))
    f = diag.one("WEBHOST")
    assert f["url"] == "?"
    assert f["sock"] == "open"


def test_a_board_with_no_web_console_prints_no_webhost_line(dd):
    diag = FakeDiag()
    dd._diag_webhost(diag, Obj())
    dd._diag_webhost(diag, Obj(webhost=None))
    dd._diag_webhost(None, Obj(webhost=Obj()))
    assert diag.lines == []


# == _diag_i2cstat =============================================================


def kbd_stats(**kw):
    base = dict(stat_n=100, stat_max_us=13500, stat_max_raw=False,
                stat_over5=7, stat_over20=3, stat_timeouts=2)
    base.update(kw)
    return Obj(**base)


def touch_stats(**kw):
    base = dict(stat_n=200, stat_max_us=60200, stat_over5=11, stat_over20=5,
                stat_int_edges=900, stat_skipped=450)
    base.update(kw)
    return Obj(**base)


def test_the_i2c_line_keeps_the_two_peripherals_apart(dd):
    """Both share I2C0 and both stall; a field read off the wrong device is the
    failure this line exists to rule out, so every number is distinct."""
    diag = FakeDiag()
    dd._diag_i2cstat(diag, kbd_stats(), touch_stats())
    f = diag.one("I2CSTAT")
    assert f["kbd"] == {"_bare": [], "n": "100", "max": "13.5ms", ">5": "7",
                        ">20": "3", "to": "2"}
    assert f["touch"] == {"_bare": [], "n": "200", "max": "60.2ms", ">5": "11",
                          ">20": "5", "int": "900", "skip": "450"}


def test_the_keyboard_max_is_tagged_with_the_MODE_it_happened_in(dd):
    """Raw-matrix mode streams the whole matrix; ASCII mode reads 5 bytes. A
    worst-case is not comparable across the two without the tag."""
    diag = FakeDiag()
    dd._diag_i2cstat(diag, kbd_stats(stat_max_raw=True), touch_stats())
    assert diag.one("I2CSTAT")["kbd"]["_bare"] == ["raw"]

    diag = FakeDiag()
    dd._diag_i2cstat(diag, kbd_stats(stat_max_raw=False), touch_stats())
    assert diag.one("I2CSTAT")["kbd"]["_bare"] == []


def test_the_first_big_stall_fingerprint_rides_the_line_when_there_is_one(dd):
    """#74: boot ms, which phase of read_raw ate it, the status byte and how
    many reads preceded it -- the answer to "boot wake or steady state"."""
    diag = FakeDiag()
    dd._diag_i2cstat(diag, kbd_stats(),
                     touch_stats(stat_first_big=(1840, "point", None, 26)))
    assert diag.one("I2CSTAT")["tfirst"] == {
        "_bare": ["point"], "t": "1840ms", "st": "None", "n": "26"}
    assert diag.line("I2CSTAT").endswith(
        " tfirst(t=1840ms point st=None n=26)")

    diag = FakeDiag()
    dd._diag_i2cstat(diag, kbd_stats(), touch_stats())
    assert "tfirst" not in diag.line("I2CSTAT")


def test_a_board_polling_neither_peripheral_reports_zeroes_not_a_crash(dd):
    """The P4 has no keyboard C3 at all; every field is a getattr default."""
    diag = FakeDiag()
    dd._diag_i2cstat(diag, Obj(), Obj())
    f = diag.one("I2CSTAT")
    assert f["kbd"]["n"] == "0" and f["kbd"]["max"] == "0.0ms"
    assert f["touch"]["int"] == "0"
    dd._diag_i2cstat(None, kbd_stats(), touch_stats())
    assert len(diag.lines) == 1


# == _diag_calib ===============================================================
#
# The MAGNITUDES here are a device's interpreter cost model and are meaningless
# on a desktop CPython, so the clock is scripted: what is pinned is the ORDER of
# the five benchmarks and the `- base` subtraction, which are code.

CALIB_CLOCK = (0, 100,      # base: the empty loop
               0, 500,      # call4
               0, 900,      # spill
               0, 300,      # tup
               0, 700,      # arr
               0, 1100)     # flt


def test_the_calib_benchmarks_all_subtract_the_empty_loop_baseline(dd):
    """Without `- base` every number carries the interpreter's loop overhead,
    which is the thing the model is trying to price the OTHER ops against."""
    clock_us(dd, *CALIB_CLOCK)
    diag = FakeDiag()
    dd._diag_calib(diag)
    assert diag.one("CALIB") == {"_bare": ["us/100"], "call4": "400",
                                 "spill": "800", "tup": "200", "arr": "600",
                                 "flt": "1000"}


def test_calib_runs_ONCE_per_boot_and_never_again(dd):
    """It is timed ~3s into the first cart so it reflects the real runtime
    heap; a second run would price a different heap under the same name."""
    clock_us(dd, *CALIB_CLOCK)
    diag = FakeDiag()
    dd._diag_calib(diag)
    dd._diag_calib(diag)
    dd._diag_calib(diag)
    assert diag.tags() == ["CALIB"]
    assert dd._CALIB_DONE == [True]


def test_a_calib_that_throws_does_not_retry_on_every_later_sample(dd):
    """The latch is set BEFORE the try on purpose: a benchmark that cannot run
    on this board must cost one attempt, not one per diag tick forever."""
    dd._ticks_us = None                            # TypeError inside the body
    diag = FakeDiag()
    dd._diag_calib(diag)
    assert diag.lines == []
    clock_us(dd, *CALIB_CLOCK)
    dd._diag_calib(diag)
    assert diag.lines == []

    dd._diag_calib(None)                           # and it declines a None ring


# == _diag_gc ==================================================================


class FakeGC:
    """MicroPython's `gc`: `mem_alloc`/`mem_free` walk the heap and do not
    exist on CPython. Everything else delegates to the real module, because
    this sits in `sys.modules` for the duration of the call."""

    def __init__(self, allocs, free):
        import gc as _real
        self._real = _real
        self._allocs = list(allocs)
        self._free = free
        self.calls = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def mem_alloc(self):
        self.calls.append("mem_alloc")
        return self._allocs.pop(0)

    def mem_free(self):
        self.calls.append("mem_free")
        return self._free

    def collect(self):
        self.calls.append("collect")


def test_the_gc_sample_measures_the_collect_it_forced(dd):
    """The pause an auto-GC costs when it lands mid-frame -- the render-time
    variance. It is timed around `collect()`, so the collect has to be between
    the two reads."""
    fake = FakeGC([300000, 200000], 1500000)
    clock_ms(dd, 5000, 5130)
    diag = FakeDiag()
    with modules(gc=fake):
        dd._diag_gc(diag)
    assert fake.calls == ["mem_alloc", "collect", "mem_free", "mem_alloc"]
    f = diag.one("GC")
    assert f["collect"] == "130ms"
    assert f["free"] == "1464k"                    # 1500000 >> 10
    assert f["live"] == "195k"                     # the POST-collect read


def test_churn_is_measured_from_the_last_samples_LIVE_set(dd):
    """Bytes allocated since the last sample -- the pressure that sets how
    often auto-GC fires. The baseline is the post-collect live set, not the
    pre-collect read, or every sample would report ~0 churn."""
    fake = FakeGC([300000, 200000], 1500000)
    clock_ms(dd, 0, 0)
    diag = FakeDiag()
    with modules(gc=fake):
        dd._diag_gc(diag)
        assert diag.one("GC")["churn"] == "292k"   # 300000 - 0
        assert dd._GC_BASE == [200000]

        for _ in range(9):                         # the skipped samples
            dd._diag_gc(diag)
        assert len(diag.lines) == 1

        fake._allocs = [250000, 210000]
        clock_ms(dd, 0, 0)
        dd._diag_gc(diag)
    assert len(diag.lines) == 2
    assert fields(diag.lines[1][1])["churn"] == "48k"   # 250000 - 200000


def test_the_forced_collect_runs_one_sample_in_ten_and_never_per_frame(dd):
    """It costs ~130ms on a cart-sized live set and mem_alloc/mem_free walk the
    heap on top -- running it every 3s sample was itself a visible hitch. The
    FIRST sample of a run must still take it."""
    fake = FakeGC(list(range(200)), 0)
    clock_ms(dd, *([0] * 200))
    diag = FakeDiag()
    with modules(gc=fake):
        for _ in range(31):
            dd._diag_gc(diag)
    assert len(diag.lines) == 4                    # ticks 0, 10, 20, 30
    assert dd._GC_TICK == [31]


def test_the_gc_tick_advances_even_where_there_is_no_ring_to_log_to(dd):
    """`diag is None` returns before the counter, so the cadence is not a
    function of whether anyone was listening -- pinned so it stays deliberate."""
    dd._diag_gc(None)
    assert dd._GC_TICK == [0]


def test_a_port_without_the_heap_verbs_stays_silent_instead_of_raising(dd):
    """CPython's `gc` has no mem_alloc; so does a MicroPython build without
    MICROPY_PY_GC. The AttributeError must not reach the frame loop."""
    diag = FakeDiag()
    dd._diag_gc(diag)                              # the REAL gc module
    assert diag.lines == []


# == the never-break-the-frame guard, on every line ============================


class Exploding:
    """A source that has gone away mid-session -- a peripheral off the bus, a
    probe on a half-torn-down console. `getattr(x, n, default)` swallows only
    AttributeError, so this reaches the body's own guard or nothing does."""

    def __getattr__(self, name):
        raise OSError("bus gone")


def raises(*_a, **_k):
    raise OSError("gone")


def test_no_diag_line_can_break_the_frame_it_is_measuring(dd):
    """Every logging verb, fed something that raises INSIDE its body. These run
    between frames on a board that is already behind, so a diagnostic that
    throws there turns a slow frame into a dead console -- and a half-formed
    line is worse than none."""
    diag = FakeDiag()
    running = dict(perf_sample=lambda: ("c",))
    calls = (
        ("HITCH", lambda: hitch(dd, diag, Exploding())),
        ("DRAWBRK", lambda: dd._diag_drawbrk(
            diag, Obj(perf_breakdown=raises, **running))),
        ("DRAW2", lambda: dd._diag_draw2(
            diag, Obj(canvas=Obj(gate_counts=raises), **running))),
        ("DRAW3", lambda: dd._diag_draw3(
            diag, Obj(canvas=canvas(), perf_breakdown=raises, **running))),
        ("LUAMEM", lambda: dd._diag_luamem(diag, Exploding())),
        ("CHROMEBRK", lambda: dd._diag_chromebrk(diag, Exploding())),
        ("LAYERBRK", lambda: dd._diag_layerbrk(diag, Exploding())),
        # a two-element split against a three-field format
        ("HOMEBRK", lambda: dd._diag_homebrk(diag, Obj(_pf_home=(1, 2)))),
        ("LOOP", lambda: dd._diag_loop(diag, Obj(), [1, 2])),
        ("WEBHOST", lambda: dd._diag_webhost(diag, Obj(webhost=Exploding()))),
        ("I2CSTAT", lambda: dd._diag_i2cstat(diag, Exploding(), Exploding())),
    )
    for _name, call in calls:
        call()                                     # must not raise
    assert len(calls) == 11
    assert diag.lines == []
