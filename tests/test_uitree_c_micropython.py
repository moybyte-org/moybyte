"""The C engine on the boards' MicroPython (the board model `make
unix-micropython` builds, `moy_uitree` compiled in): the four conformance
fixtures driven by their scripts through the reference engine and through the
C engine in one booted console, held to the same trace and the same pixels;
then what a frame of each allocates and costs, and what opening a long page
of standard rows costs, printed for the record (`-s`).
"""

import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_frame_alloc as FA  # noqa: E402
import uitree_conf as C  # noqa: E402
import uitree_reference as R  # noqa: E402

LONG = '''
import uitree as ui

s = ui.state(wifi=False, n=0, name="Moybyte")


def add():
    s.n += 1


ui.action("add", "Add one", add)


def build():
    with ui.page("Long"):
        for g in range(8):
            with ui.group("Group %d" % g):
                ui.info("Name", "{name}")
                ui.toggle("Wi-Fi", "wifi")
                ui.link("add", value="{n}")
                ui.info("Count", "{n|plural tap,taps}")
                ui.label("{wifi?On:Off} at {n}")
'''

DRIVER = r'''
FIXTURES = @FIXTURES@


class Inp:
    typed = 0

    def __init__(self, k):
        self.k = k

    def pressed(self, n):
        return n == self.k


class Damage:
    def all(self):
        pass


def _steps(loop, n):
    for _ in range(n):
        loop.step()


def _load(name, src):
    with open(ROOT + "/fakes/" + name + ".py", "w") as f:
        f.write(src)
    return __import__(name)


def _engine(cls, mod):
    import uitree as S
    import ui_draw
    host = ui_draw.AppHost()
    return cls(S.decl_of(mod.__name__), mod.build, host, None, Damage()), host


class BarLook:
    """A lent row's look (the bar's), recorded: what each draw was."""
    zone_gen = 0

    def __init__(self):
        self.drawn = []

    def zone_scale(self):
        return 1

    def tab_pad(self):
        return 3

    def tab_width(self, label, icon, h):
        return 26 + 6 * len(label)

    def word_width(self, label, icon, h):
        return 24 + 6 * len(label)

    def draw_tab(self, cv, r, cur, label, icon, labelled, every):
        self.drawn.append(["tab", list(r), cur, label if labelled else icon])

    def draw_icon(self, cv, r, icon, on, primary, marked=False):
        self.drawn.append(["icon", list(r), icon, on, marked])

    def draw_word(self, cv, r, label, icon, primary, marked=False):
        self.drawn.append(["word", list(r), label, marked])

    def draw_text(self, cv, r, text):
        self.drawn.append(["text", list(r), text])

    def draw_line(self, cv, x, zone):
        self.drawn.append(["line", x])


def bar(out):
    """The row a page lends the bar (tests/uitree_fixtures/uitree_bar_row.py)
    in three zones, on both engines: `uit_lent` and `uit_lent_place` on the
    boards' MicroPython."""
    import uitree as S
    import ui_draw
    import uitree_c
    import uitree_editor
    import uitree_ref_editor
    out["bar"] = {}
    for tag, cls in (("py", uitree_ref_editor.Engine), ("c", uitree_c.CEditorEngine)):
        mod = _load("bar_%s" % tag, BAR_SRC)
        eng = cls(S.decl_of(mod.__name__), mod.page, ui_draw.AppHost(), None, Damage())
        eng.open(hook="none")
        look = BarLook()
        z = uitree_editor.BarZone(eng, look)
        for w in (400, 160, 80):
            z.draw_zone(None, (0, 0, w, 20))
        eng.run(setattr, mod.s, "playing", True)
        z.draw_zone(None, (0, 0, 400, 20))
        out["bar"][tag] = look.drawn + [z.zone_need(20)]


def _use(tag):
    """Engines made after this: "reference", "c" (the C engine, its build
    direct: C compiles every template) or "c_checked" (the reference's
    checked build, lowered)."""
    import uitree_app
    import uitree_c
    uitree_c.BUILD = "checked" if tag == "c_checked" else "direct"
    uitree_app.use_engine("reference" if tag == "reference" else "c")


def _digest(buf):
    import binascii
    import hashlib
    return binascii.hexlify(hashlib.sha256(buf).digest()).decode()


def _frame(eng, host, cv, th, box):
    import ui
    host.begin(cv, th, box)
    eng.paint(0.033, cv, th, box, False)
    ui.end()


def conf(loop):
    import gc
    import time
    import uitree as S
    import uitree_app
    import uitree_ref
    import uitree_c
    ws = loop.ws
    _steps(loop, 20)
    cv, th = ws.sys_canvas, ws.theme_colors
    box = (0, 0, cv.w, cv.h)
    out = {"fixtures": {}, "cost": {}}
    S.TRACING = True
    import uitree_ref_editor
    pairs = (uitree_ref.Engine, uitree_c.CEngine)
    for name, src, script, pair in [f + (pairs,) for f in FIXTURES] + [
            ("placed", PLACED_SRC, PLACED_SCRIPT, (uitree_ref_editor.Engine, uitree_c.CEditorEngine)),
            ("text_editor", TE_SRC, TE_SCRIPT, (uitree_ref_editor.Engine, uitree_c.CEditorEngine)),
            ("across", ACROSS_SRC, ACROSS_SCRIPT, (uitree_ref_editor.Engine, uitree_c.CEditorEngine)),
            ("across_bar", ACROSS_BAR_SRC, ACROSS_BAR_SCRIPT, (uitree_ref_editor.Engine, uitree_c.CEditorEngine)),
            ("bar_look", BAR_LOOK_SRC, BAR_LOOK_SCRIPT, (uitree_ref_editor.Engine, uitree_c.CEditorEngine)),
            ("grid", GRID_SRC, GRID_SCRIPT, pairs),
            ("model_row", MROW_SRC, MROW_SCRIPT, (uitree_ref_editor.Engine, uitree_c.CEditorEngine))]:
        runs = {}
        for tag, cls in zip(("py", "c"), pair):
            mod = _load("conf_%s_%s" % (name, tag), src)
            eng, host = _engine(cls, mod)
            eng.open()
            frames = []
            _frame(eng, host, cv, th, box)
            frames.append(bytes(cv._buf))
            for step in script:
                if step == "back":
                    eng.back()
                else:
                    eng.keys(Inp(step[2:]))
                _frame(eng, host, cv, th, box)
                frames.append(bytes(cv._buf))
            runs[tag] = (list(eng.trace), frames)
        same = [i for i in range(len(runs["py"][1])) if runs["py"][1][i] == runs["c"][1][i]]
        out["fixtures"][name] = {"trace_py": runs["py"][0], "trace_c": runs["c"][0],
                                 "frames": len(runs["py"][1]), "same": len(same),
                                 "digests_py": [_digest(b) for b in runs["py"][1]],
                                 "digests_c": [_digest(b) for b in runs["c"][1]]}
        runs = None
        gc.collect()
    S.TRACING = False
    for name, src in (("long", LONG_SRC), ("counter", FIXTURES[0][1])):
        for tag, cls in (("py", uitree_ref.Engine), ("c", uitree_c.CEngine), ("c_checked", uitree_c.CEngine)):
            _use(tag)
            gc.collect()
            mod = _load("cost_%s_%s" % (name, tag), src)
            m0 = gc.mem_alloc()
            t0 = time.ticks_us()
            eng, host = _engine(cls, mod)
            eng.open()
            t1 = time.ticks_us()
            _frame(eng, host, cv, th, box)
            first = time.ticks_diff(time.ticks_us(), t1)
            opened = time.ticks_diff(t1, t0)
            _use("reference")
            gc.collect()
            held = gc.mem_alloc() - m0
            for _ in range(5):
                _frame(eng, host, cv, th, box)
            us, alloc = [], []
            for _ in range(40):
                host.begin(cv, th, box)
                a0 = gc.mem_alloc()
                t0 = time.ticks_us()
                eng.paint(0.033, cv, th, box, False)
                us.append(time.ticks_diff(time.ticks_us(), t0))
                alloc.append(gc.mem_alloc() - a0)
                import ui
                ui.end()
            us.sort()
            out["cost"]["%s_%s" % (name, tag)] = {
                "open_us": opened, "first_frame_us": first, "open_first_frame_us": opened + first,
                "held_bytes": held, "paint_us_median": us[len(us) // 2],
                "paint_alloc_max": max(alloc), "paint_alloc_median": sorted(alloc)[len(alloc) // 2],
                "stats": eng.stats() if tag != "py" else None}
            eng = mod = None
    bar(out)
    out["settings"] = settings(loop)
    print("RESULT " + json.dumps(out))


class Station:
    """A Wi-Fi station whose answers are kept: reading them allocates nothing."""
    def __init__(self):
        self._st = (True, "MoyNet", "192.168.1.155")
        self._kn = ["MoyNet"]
        self._sc = [("MoyNet", -40, True), ("Guest", -60, False)]

    def status(self):
        return self._st

    def known(self):
        return self._kn

    def scan(self):
        return self._sc

    def connect(self, ssid, pw):
        return True

    def forget(self, ssid):
        pass


def _rows_frames(ws, sl, settings_rows, uitree_app, tag):
    """The rows page on its own host, painted into the console's canvas: its
    frame after opening and after each key of the script."""
    import gc
    import time
    import ui
    import ui_draw
    host = ui_draw.AppHost()
    cv, th = ws.sys_canvas, ws.theme_colors
    box = (0, 0, cv.w, cv.h)
    for _ in range(2):              # the second: what opening the page costs, measured
        gc.collect()
        m0 = gc.mem_alloc()
        t0 = time.ticks_us()
        _use(tag)
        page = settings_rows.RowsPage(sl, host)
        _use("reference")
        page.open()
        t1 = time.ticks_us()
        host.begin(cv, th, box)
        page.draw(cv, th, box, False, 0.033)
        ui.end()
        t2 = time.ticks_us()
        gc.collect()
        r = {"engine": type(page.engine).__name__, "open_us": time.ticks_diff(t1, t0),
             "open_first_frame_us": time.ticks_diff(t2, t0), "held_bytes": gc.mem_alloc() - m0}
        if _ == 0:
            page = None

    def frame():
        for _ in range(2):
            host.begin(cv, th, box)
            page.draw(cv, th, box, False, 0.033)
            ui.end()
        return bytes(cv._buf)
    out = [frame()]
    for k in SETTINGS_KEYS:
        page.keys(Inp(k))
        out.append(frame())
    page.leave()
    return out, r


def settings(loop):
    """Settings' rows page on each engine: what opening it and its first
    frame cost and its frames through a script of keys (each change undone),
    on a host of its own; then in the console, the bytes a drag's frames
    allocate (the Settings drag pin's protocol), idle frames and a toggle
    every fourth frame."""
    import gc
    import moy_loop
    import settings_rows
    import uitree_app
    ws = loop.ws
    ws.wifi = Station()
    sl = ws.settings_layer
    W, H = ws.sys_canvas.w, ws.sys_canvas.h
    out = {}
    first = None
    for tag in SETTINGS_RUNS:
        frames, r = _rows_frames(ws, sl, settings_rows, uitree_app, tag)
        _use(tag)
        sl.rows = settings_rows.RowsPage(sl, sl._host)
        _use("reference")
        ws.open_settings()
        _steps(loop, 20)
        r["digests"] = [_digest(b) for b in frames]
        if first is None:
            first = frames
        else:
            r["same"] = [i for i in range(len(frames)) if frames[i] == first[i]]
            r["frames"] = len(frames)
        frames = None
        drag = []
        for rep in range(4):
            y0, y1 = (H * 3 // 4, H // 4) if rep % 2 == 0 else (H // 4, H * 3 // 4)
            moy_loop.swipe(W // 2, y0, W // 2, y1, 25)
            gc.collect()
            gc.disable()
            for _ in range(30):
                d0 = ws.frame_state.frames_drawn
                m = gc.mem_alloc()
                loop.step()
                if rep:
                    drag.append((gc.mem_alloc() - m, ws.frame_state.frames_drawn - d0,
                                 ws.frame_change == "shift"))
            gc.enable()
        r["drag"] = drag
        for _ in range(300):
            if not sl._input_host().moving:
                break
            loop.step()
        fps = ws.show_fps
        for name in ("idle", "toggle"):
            _steps(loop, 20)
            gc.collect()
            gc.disable()
            got = []
            for i in range(60):
                if name == "toggle" and i % 4 == 0:
                    ws.show_fps = not ws.show_fps
                    ws.mark_dirty()
                m = gc.mem_alloc()
                loop.step()
                got.append(gc.mem_alloc() - m)
            gc.enable()
            r[name] = got
        ws.show_fps = fps
        ws.exit_settings()
        _steps(loop, 10)
        out[tag] = r
    first = None
    return out
'''

SETTINGS_RUNS = ("reference", "c", "c_checked")

# each a step of Settings' rows on the T-Deck, every change undone (Volume starts at its top)
SETTINGS_KEYS = ["down", "down", "right", "left", "down", "right", "left", "down", "left", "left",
                 "right", "right", "down", "down", "down", "up", "up", "up"]


def _fixtures():
    out = []
    for name in ("counter", "list", "pages", "gates"):
        app_py, _ = C.fixture(name)
        with open(app_py) as f:
            src = f.read()
        with open(Path(C.FIX) / name / "script.json") as f:
            script = json.load(f)
        out.append((name, src, script))
    return out


# the placed page (tests/uitree_fixtures/uitree_placed.py) on both engines: the row, the switch's
# other case and the tools
PLACED_SCRIPT = ["k:right", "k:a", "k:right", "k:a", "k:down", "k:a", "k:down", "k:up"]
# the text editor (tests/uitree_fixtures/uitree_text_editor.py): the caret walked, the gutter
# turned off and on by its focused button
TE_SCRIPT = ["k:down", "k:down", "k:right", "k:a", "k:down", "k:a", "k:up"]
# the rule along x (tests/uitree_fixtures/uitree_across.py): the bar walked, the switch flipped
ACROSS_SCRIPT = ["k:right", "k:a", "k:right", "k:a", "k:down", "k:a", "k:up"]
# a bar's pieces (tests/uitree_fixtures/uitree_across_bar.py): Bump widens a w= and a gap= state
ACROSS_BAR_SCRIPT = ["k:a", "k:right", "k:a", "k:down", "k:left", "k:a"]
# the bar's look (tests/uitree_fixtures/uitree_bar_look.py): marked, walked, the clock widened
BAR_LOOK_SCRIPT = ["k:a", "k:right", "k:right", "k:right", "k:right", "k:a", "k:left", "k:a"]
# a grid (tests/uitree_fixtures/uitree_grid.py): walked, the wide key pressed, hidden, walked
GRID_SCRIPT = ["k:down", "k:right", "k:a", "k:left", "k:a", "k:down", "k:up", "k:a"]
# a row from a model (tests/uitree_fixtures/uitree_model_row.py): grown past its room, walked, marked
MROW_SCRIPT = ["k:a", "k:a", "k:right", "k:a", "k:right", "k:a", "k:left", "k:a"]


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    placed = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_placed.py").read_text()
    bar_row = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_bar_row.py").read_text()
    te = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_text_editor.py").read_text()
    across = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_across.py").read_text()
    across_bar = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_across_bar.py").read_text()
    bar_look = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_bar_look.py").read_text()
    grid = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_grid.py").read_text()
    mrow = (Path(__file__).resolve().parent / "uitree_fixtures" / "uitree_model_row.py").read_text()
    more = (DRIVER.replace("@FIXTURES@", repr(_fixtures())) + "\nLONG_SRC = %r\n" % LONG
            + "PLACED_SRC = %r\nPLACED_SCRIPT = %r\nBAR_SRC = %r\n" % (placed, PLACED_SCRIPT, bar_row)
            + "TE_SRC = %r\nTE_SCRIPT = %r\n" % (te, TE_SCRIPT)
            + "ACROSS_SRC = %r\nACROSS_SCRIPT = %r\n" % (across, ACROSS_SCRIPT)
            + "ACROSS_BAR_SRC = %r\nACROSS_BAR_SCRIPT = %r\n" % (across_bar, ACROSS_BAR_SCRIPT)
            + "BAR_LOOK_SRC = %r\nBAR_LOOK_SCRIPT = %r\n" % (bar_look, BAR_LOOK_SCRIPT)
            + "GRID_SRC = %r\nGRID_SCRIPT = %r\n" % (grid, GRID_SCRIPT)
            + "MROW_SRC = %r\nMROW_SCRIPT = %r\n" % (mrow, MROW_SCRIPT)
            + "SETTINGS_KEYS = %r\nSETTINGS_RUNS = %r\n" % (SETTINGS_KEYS, SETTINGS_RUNS))
    info, text = FA._run(tmp_path_factory.mktemp("uitree_c"), entry="conf", more=more,
                         why="the C engine runs on the boards' MicroPython: this is its check there")
    return info, text


def _latin(lines):
    """MicroPython's repr escapes a str's characters 0x80-0xff (`\\xb7`), which
    the C engine's trace writes as CPython's repr does: read back as they are."""
    return [re.sub(r"\\x([89a-f][0-9a-f])", lambda m: chr(int(m.group(1), 16)), t) for t in lines]


def test_the_c_engine_on_the_boards_micropython_draws_the_reference(result):
    info, text = result
    for name, f in info["fixtures"].items():
        assert C.norm_trace(f["trace_c"]) == C.norm_trace(_latin(f["trace_py"])), name
        R.held(__file__, "conf/" + name, [C.norm_trace(_latin(f["trace_py"])), f["digests_py"]])
        assert f["same"] == f["frames"], (name, f["same"], f["frames"])
    for k, v in sorted(info["cost"].items()):
        print(k, json.dumps(v))
    # A still frame of a page on the C engine allocates nothing, in either build.
    for name in ("long", "counter"):
        for tag in ("c", "c_checked"):
            assert info["cost"][name + "_" + tag]["paint_alloc_median"] == 0, info["cost"][name + "_" + tag]


def test_a_lent_row_lays_out_alike_on_both_engines_on_the_boards_micropython(result):
    info, _text = result
    R.held(__file__, "bar", info["bar"]["py"])
    assert info["bar"]["c"] == info["bar"]["py"]
    assert len(info["bar"]["c"]) > 10


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def test_settings_rows_on_the_c_engine_draw_the_reference_and_shift_cheaper(result):
    """Settings' rows page on the C engine in the booted console: every frame
    of a script of keys is the reference's, its drag shifts as the
    reference's does, and a shift frame allocates less than the Settings drag
    pin allows (test_frame_alloc's SETTINGS_FRAME) and less than the
    reference's. Each engine's numbers are printed for the record (`-s`)."""
    info, text = result
    st = info["settings"]
    ref, c = st["reference"], st["c"]
    assert c["engine"] == "CPage" and st["c_checked"]["engine"] == "CPage" and ref["engine"] == "Page"
    R.held(__file__, "settings_rows", ref["digests"])
    for r in (c, st["c_checked"]):
        assert len(r["same"]) == r["frames"], ("frames that differ", sorted(set(range(r["frames"])) - set(r["same"])))
    for tag, r in (("reference", ref), ("c", c), ("c_checked", st["c_checked"])):
        drawn = [d for d in r["drag"] if d[1]]
        shifted = [d[0] for d in drawn if d[2]]
        r["shift_median"] = _median(shifted)
        print(tag, json.dumps({"drawn": len(drawn), "shifted": len(shifted), "shift_median": r["shift_median"],
                               "idle_sum": sum(r["idle"]), "idle_median": _median(r["idle"]),
                               "toggle_sum": sum(r["toggle"]), "toggle_max": max(r["toggle"]),
                               "open_us": r["open_us"], "open_first_frame_us": r["open_first_frame_us"],
                               "held_bytes": r["held_bytes"]}))
        assert len(drawn) > 30 and len(shifted) > len(drawn) // 2, (tag, len(shifted), len(drawn))
    assert c["shift_median"] < FA.SETTINGS_FRAME, c["shift_median"]
    R.held(__file__, "settings_shift_median", ref["shift_median"])
    assert c["shift_median"] <= ref["shift_median"], (c["shift_median"], ref["shift_median"])

