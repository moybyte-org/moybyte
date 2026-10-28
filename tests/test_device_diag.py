"""`device/device_diag.py`, EXECUTED (#208, the single-consumer list).

A module whose only executable coverage was `_diag_pump` (through
`tests/test_banded_panel.py`); everything else was pinned as SOURCE STRINGS in
the T-Deck spike suite. A substring cannot tell `us / 1000.0` from
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
  * the VALUES behind `esp32.idf_heap_info` and `diag.flush_to_sd`'s actual SD
    write are hardware. They arrive here as doubles installed at the IMPORT
    boundary, so the real body runs and only the numbers are ours.
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


def esp32_with(*regions):
    """`idf_heap_info(HEAP_DATA)` -> ((total, free, largest, ...), ...)."""
    return Obj(HEAP_DATA=4, idf_heap_info=lambda _cap: regions)


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
        ("DRAWBRK", lambda: dd._diag_drawbrk(
            diag, Obj(perf_breakdown=raises, **running))),
        ("DRAW2", lambda: dd._diag_draw2(
            diag, Obj(canvas=Obj(gate_counts=raises), **running))),
        ("WEBHOST", lambda: dd._diag_webhost(diag, Obj(webhost=Exploding()))),
        ("I2CSTAT", lambda: dd._diag_i2cstat(diag, Exploding(), Exploding())),
    )
    for _name, call in calls:
        call()                                     # must not raise
    assert len(calls) == 4
    assert diag.lines == []
