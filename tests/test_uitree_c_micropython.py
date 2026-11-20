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
        for tag, cls in (("py", uitree_app.Engine), ("c", uitree_c.CEngine)):
            gc.collect()
            mod = _load("cost_%s_%s" % (name, tag), src)
            m0 = gc.mem_alloc()
            eng, host = _engine(cls, mod)
            eng.open()
            t0 = time.ticks_us()
            _frame(eng, host, cv, th, box)
            first = time.ticks_diff(time.ticks_us(), t0)
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
                "open_first_frame_us": first, "held_bytes": held, "paint_us_median": us[len(us) // 2],
                "paint_alloc_max": max(alloc), "paint_alloc_median": sorted(alloc)[len(alloc) // 2],
                "stats": eng.stats() if tag == "c" else None}
            eng = mod = None
    print("RESULT " + json.dumps(out))
'''


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


def _result(tmp_path):
    more = (DRIVER.replace("@FIXTURES@", repr(_fixtures())) + "\nLONG_SRC = %r\n" % LONG)
    info, text = FA._run(tmp_path, entry="conf", more=more,
                         why="the C engine runs on the boards' MicroPython: this is its check there")
    return info, text


def test_the_c_engine_on_the_boards_micropython_draws_the_reference(tmp_path):
    info, text = _result(tmp_path)
    for name, f in info["fixtures"].items():
        assert C.norm_trace(f["trace_c"]) == C.norm_trace(f["trace_py"]), name
        assert f["same"] == f["frames"], (name, f["same"], f["frames"])
    for k, v in sorted(info["cost"].items()):
        print(k, json.dumps(v))
    # A still frame of a page on the C engine allocates nothing.
    for name in ("long", "counter"):
        assert info["cost"][name + "_c"]["paint_alloc_median"] == 0, info["cost"][name + "_c"]

