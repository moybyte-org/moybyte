"""The C engine on the boards' MicroPython (the board model `make
unix-micropython` builds, `moy_uitree` compiled in): the four conformance
fixtures driven by their scripts through the reference engine and through the
C engine in one booted console, held to the same trace and the same pixels;
then what a frame of each allocates and costs, and what opening a long page
of standard rows costs, printed for the record (`-s`).
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_frame_alloc as FA  # noqa: E402
import uitree_conf as C  # noqa: E402

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


def _use(tag):
    """Engines made after this: "reference", "c" (the C engine, its build
    direct: C compiles every template) or "c_checked" (the reference's
    checked build, lowered)."""
    import uitree_app
    import uitree_c
    uitree_c.BUILD = "checked" if tag == "c_checked" else "direct"
    uitree_app.use_engine("reference" if tag == "reference" else "c")


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
    import uitree_c
    ws = loop.ws
    _steps(loop, 20)
    cv, th = ws.sys_canvas, ws.theme_colors
    box = (0, 0, cv.w, cv.h)
    out = {"fixtures": {}, "cost": {}}
    S.TRACING = True
    for name, src, script in FIXTURES:
        runs = {}
        for tag, cls in (("py", uitree_app.Engine), ("c", uitree_c.CEngine)):
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
                                 "frames": len(runs["py"][1]), "same": len(same)}
        runs = None
        gc.collect()
    S.TRACING = False
    for name, src in (("long", LONG_SRC), ("counter", FIXTURES[0][1])):
        for tag, cls in (("py", uitree_app.Engine), ("c", uitree_c.CEngine), ("c_checked", uitree_c.CEngine)):
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


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    more = (DRIVER.replace("@FIXTURES@", repr(_fixtures())) + "\nLONG_SRC = %r\n" % LONG
            + "SETTINGS_KEYS = %r\nSETTINGS_RUNS = %r\n" % (SETTINGS_KEYS, SETTINGS_RUNS))
    info, text = FA._run(tmp_path_factory.mktemp("uitree_c"), entry="conf", more=more,
                         why="the C engine runs on the boards' MicroPython: this is its check there")
    return info, text


def test_the_c_engine_on_the_boards_micropython_draws_the_reference(result):
    info, text = result
    for name, f in info["fixtures"].items():
        assert C.norm_trace(f["trace_c"]) == C.norm_trace(f["trace_py"]), name
        assert f["same"] == f["frames"], (name, f["same"], f["frames"])
    for k, v in sorted(info["cost"].items()):
        print(k, json.dumps(v))
    # A still frame of a page on the C engine allocates nothing, in either build.
    for name in ("long", "counter"):
        for tag in ("c", "c_checked"):
            assert info["cost"][name + "_" + tag]["paint_alloc_median"] == 0, info["cost"][name + "_" + tag]


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
    assert c["shift_median"] <= ref["shift_median"], (c["shift_median"], ref["shift_median"])

