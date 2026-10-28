"""tools/input_bench.py: the merged measuring tool of
docs/scroll_and_text_2026-10.md §14, its host-side numbers and its console
half, driven here on a host console under a clock this test moves.

The console half is the code the boards run: it plays a schedule on
wall-clock time from inside `ws.frame`, records each painted frame, clips
the content to an empty floor for the null-floor play, and its cleanup takes
away the scratch projects, the note and the script and every wrapper."""

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import input_bench as ib  # noqa: E402
from runtime import host_app, moy_loop  # noqa: E402


# -- the host-side numbers ---------------------------------------------------------

def _rec(**kw):
    f = dict((k, 0) for k in ib.FIELDS)
    f.update(probe=ib.NONE_PROBE, flushes=-1, span=-1)
    f.update(kw)
    return [f[k] for k in ib.FIELDS]


def test_a_plan_is_wall_clock_time_and_its_finger_moves_at_its_speed():
    s = ib.Surface("x", [])
    p = ib.build_plan("fling", s, (0, 0, 100, 200), matrix=False)
    (t0, kind, x0, y0, x1, y1, ms, edge), up = p.ev[0], p.ev[1]
    assert kind == 2 and edge and ms == 120 and (x0, x1) == (50, 50)
    assert up[1] == 5 and up[0] == t0 + 120
    assert p.finger(t0 + 60) == (50, (y0 + y1) / 2)
    assert p.finger(up[0] + 1) is None
    k = ib.build_plan("key_steps", s, (0, 0, 100, 200), matrix=False)
    downs = [e for e in k.ev if e[1] == 3 and e[3]]
    assert [e[0] - downs[0][0] for e in downs] == [i * 250 for i in range(8)]
    # a BLE keyboard's arrow is its button and its key event; the matrix's
    # the button alone, and a typed key a one-shot
    assert any(e[1] == 4 and e[2] == ib.ARROW["down"] for e in k.ev)
    m = ib.build_plan("key_steps", s, (0, 0, 100, 200), matrix=True)
    assert not any(e[1] == 4 for e in m.ev)
    t = ib.build_plan("type", s, (0, 0, 100, 200), matrix=True)
    assert all(e[1] == 0 for e in t.ev)


def test_presentation_is_the_switch_or_the_feeders_completion():
    # a P4: the show the device recorded (a deferred one at the next loop)
    fr = ib.frames_of(_rec(show=10000) + _rec(show=40000))
    assert ib.presented(fr) == [10.0, 40.0]
    # an S3: the kick plus the span the next kick reports, the last one's
    # from the counters read after the run
    fr = ib.frames_of(_rec(show=10000, flushes=5, span=9999)
                      + _rec(show=30000, flushes=6, span=13000))
    assert ib.presented(fr, tail=(2, 0, 0, 7, 12500)) == [23.0, 42.5]


def test_the_classes_the_first_three_and_the_press_latency():
    marks = [(100, "key", 160), (400, "key", 460)]
    raw = (_rec(t0=50000, t1=58000, show=60000, content=1, prev=40000)
           + _rec(t0=110000, t1=125000, show=130000, content=1, probe=0, prev=105000)
           + _rec(t0=127000, t1=148000, show=150000, content=1, probe=5, prev=125000)
           + _rec(t0=160000, t1=168000, show=170000, probe=5, prev=155000)
           + _rec(t0=420000, t1=490000, show=500000, content=1, probe=9, prev=410000))
    a = ib.analyse(raw, {"marks": marks, "segs": [], "releases": []})
    assert a["live"]["n"] == 2 and a["glide"]["n"] == 1 and a["settle"]["n"] == 1
    # back to back, a frame presents after the one before it (130 -> 150);
    # after idle loops, from its own loop's start -- its start less the
    # loop's share before a frame where frames ran back to back (2 ms) --
    # never from the idle loop's end; its latency from the press
    assert a["glide"]["max"] == 20.0
    assert a["live"]["max"] == 82.0 and a["first3"] == 82.0
    assert a["lat"]["max"] == 100.0
    assert a["copies"] == 1 and a["coalesced"] == 0


def test_the_finger_against_the_content():
    plan = {"marks": [(0, "touch", 100)], "segs": [(0, 50, 200, 50, 100, 100)],
            "releases": [(100, 1.0)], "p0": 0}
    raw = (_rec(t0=10000, show=50000, content=1, probe=40, prev=5000)
           + _rec(t0=60000, show=80000, content=1, probe=70, prev=55000)
           + _rec(t0=85000, show=100000, content=1, probe=100, prev=82000))
    a = ib.analyse(raw, plan, px=True)
    # at 50 ms the finger has gone 50 px up, the content 40: 10 behind
    assert a["lag"]["max"] == 10.0
    assert a["v_finger"] == 1.0 and a["v_content"] == 1.2


# -- the console half, on a host console -------------------------------------------

class _Clock:
    def __init__(self):
        self.us = 1_000_000

    def ticks_us(self):
        self.us += 20           # every read moves it on: a frame's parts take time
        return self.us

    def ticks_ms(self):
        return self.us // 1000


class HostBoard:
    """The P4Board verbs the bench uses, over a host console in this process:
    `drain` runs its frames on the test's clock."""

    def __init__(self, ws, clock):
        self.ws = ws
        self.clock = clock
        self.g = {"ws": ws}
        ws._g = self.g
        self.lines = []
        self.last_error = None
        self.frames = 0

    def pyexec(self, code, timeout=30, strict=False):
        # a one-line snippet is a `py` line: an expression, in a fresh namespace
        one = "\n" not in code.strip("\n")
        try:
            if one:
                eval(code, {"ws": self.ws, "wm": self.ws.wm})
            else:
                exec(code, self.g)
        except Exception as exc:  # noqa: BLE001 -- the board's answer
            self.last_error = repr(exc)
            if strict:
                raise
            return False
        return True

    def pyval(self, expr, timeout=30):
        try:
            return eval(expr, self.g)
        except Exception as exc:  # noqa: BLE001
            self.last_error = repr(exc)
            return None

    def drain(self, secs):
        """The kernel's loop step: the table merged and its pointer sample
        applied, the trackball when it owes a pass, then the console's three
        entries (native/moy_kernel/moy_loop.c)."""
        ws = self.ws
        dt = 1 / 60
        for _ in range(int(secs * 60)):
            self.clock.us += 16_667
            ws.input.begin_frame()
            f = ws.input.apply_pointer(ws.pointer)
            ws.pointer.click = bool(f & 4)          # moy_input.P_CLICK: the press edge
            if ws.ball_owed:
                ws.ball(0, 0, False)
            moy_loop.drive(ws.handle_input, ws.handle_pointer,
                           lambda _dt: (ws.frame(dt), ws._frames_drawn)[1], dt)
            self.frames += 1
        return []

    def cmd(self, text, wait_for=None, timeout=8):
        if text.startswith("run "):
            self.ws.launch_named(text[4:])      # the dev channel's `run`

    def state(self):
        return {"order": [], "wins": {}}

    def leave_cart(self):
        if self.ws.player._update is not None or self.ws.player._draw is not None:
            self.ws.exit()

    def close(self):
        pass


@pytest.fixture
def host(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(time, "ticks_us", clock.ticks_us, raising=False)
    monkeypatch.setattr(time, "ticks_ms", clock.ticks_ms, raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda a, b: a - b, raising=False)
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.pointer.visible = False
    for _ in range(3):
        ws._dirty = True
        ws.frame(1 / 60)
    return HostBoard(ws, clock)


def _surface(name):
    return [s for s in ib.surfaces() if s.name == name][0]


def test_rows_play_on_the_scratch_project_and_the_cleanup_takes_it_all_away(host, tmp_path):
    ws = host.ws
    carts0 = sorted(c["path"] for c in ws.carts.all)
    docs0 = sorted(ws.carts_store.list_files("docs", ws.carts_root) or ())
    star = [c for c in ws.carts.all if c["title"] == ib.STAR][0]
    src0 = open(os.path.join(star["path"], "main.py")).read()
    out = str(tmp_path / "rows.json")
    bench = ib.Bench(host, "host", out)
    bench.setup()
    assert [c for c in ws.carts.all if c["title"] == ib.SCRATCH]
    try:
        ib.run_surface(bench, _surface("Settings"), ("fling", "drag", "key_steps"))
        ib.run_surface(bench, _surface("Code"), ("type",))
    finally:
        bench.cleanup()
    rows = {(r["surface"], r["input"]): r for r in bench.rows}
    fling, drag = rows[("Settings", "fling")], rows[("Settings", "drag")]
    assert drag["painted"] > 10 and drag["live"]["n"] > 5
    assert fling["moved"] > 0 and drag["moved"] < 0, "the page's offset under the finger"
    assert "lag" in drag
    assert drag["null"]["raw"], "the row played again on the null floor"
    assert drag["expected"]["phase"] == "P1c"
    typed = rows[("Code", "type")]
    assert typed["gestures"] == len(ib.TEXT["type"][0]) and typed["live"]["n"] > 0
    # the typing landed in the scratch copy, never the seed cart
    assert open(os.path.join(star["path"], "main.py")).read() == src0
    # the cleanup: no copy, no note, no script, no wrapper
    assert sorted(c["path"] for c in ws.carts.all) == carts0
    assert sorted(ws.carts_store.list_files("docs", ws.carts_root) or ()) == docs0
    assert "frame" not in ws.__dict__ and "ball" not in ws.__dict__
    assert ws._ib is None and type(ws.comp).__name__ != "_CompProxy"
    assert not [o for o in ws.wm.visible_stack_rev() if "draw" in o.__dict__]
    assert ws.wm.top_kind() == "launcher"
    assert "| Settings | drag |" in ib.report(out)


def test_the_null_floor_draws_nothing_and_gives_the_canvas_back(host):
    ws = host.ws
    bench = ib.Bench(host, "host", None)
    host.pyexec("_ib_app('settings')")
    host.drain(0.5)
    cv = ws.canvas
    clip0 = (cv._clip_x0, cv._clip_y0, cv._clip_x1, cv._clip_y1)
    plan = ib.build_plan("drag", _surface("Settings"), (0, 0, bench.W, bench.H), False)
    got = bench.play(plan, None, null=True)
    fr = ib.frames_of(got["raw"])
    assert fr and all(f["content"] > 0 for f in fr if f["windows"] == 0)
    assert (cv._clip_x0, cv._clip_y0, cv._clip_x1, cv._clip_y1) == clip0
    assert "clip" not in cv.__dict__ and "cls" not in cv.__dict__
    host.pyexec(ib.UNINSTALL)


def test_my_art_rows_on_the_phase_table():
    assert ib.phase_of("My Art", "fling", "p4") == ("P1d", "shift")
    assert ib.phase_of("My Art", "drag", "tdeck") == ("P1c", "shift")
    assert ib.phase_of("Code", "type", "p4") == ("P4", "line")
    assert ib.phase_of("Code find", "find", "p4") == (None, None)
    assert ib.phase_of("home grid", "key_steps", "tdeck") == ("P6", "rows")
    assert ib.phase_of("Text console", "fling", "p4") == (None, None)


def test_every_surface_sets_up_plays_and_tears_down_on_the_host(host, capsys):
    """Each surface's setup, a row of each kind of input it takes and its
    teardown run clean on a host console; the cleanup leaves the theme's
    text, the console's name and the home as they were."""
    ws = host.ws
    theme0 = ws.look.file_text()
    name0 = ws.system.get("name", "")
    bench = ib.Bench(host, "host", None, null=False)
    bench.setup()
    try:
        for s in ib.surfaces():
            kinds = {}
            for inp in s.inputs:
                kinds.setdefault(inp.split("_")[0], inp)
            ib.run_surface(bench, s, tuple(kinds.values()))
    finally:
        bench.cleanup()
    out = capsys.readouterr().out
    if os.environ.get("IB_SHOW"):
        sys.__stdout__.write(out)
    assert "      ! " not in out, out
    assert "(no record)" not in out, out
    assert ws.look.file_text() == theme0 and ws.system.get("name", "") == name0
    assert not [c for c in ws.carts.all if c["title"].startswith(ib.SCRATCH)]
    assert not ws.carts_store.list_files("drawings", ws.carts_root)


def test_the_pickers_rows_drag_inside_the_pickers_body(host):
    """The Sprite pickers' finger lands where their body shows the sheet,
    so a drag scrolls it and the pickers stay up through the row."""
    ws = host.ws
    bench = ib.Bench(host, "host", None)
    bench.setup()
    surf = _surface("Sprite pickers")
    try:
        for code, wait in surf.setup:
            bench.ex(code, wait)
        rect = ib._rect(bench, surf)
        shown = ws.paint_layer.sheet_shown
        assert shown is not None
        assert (shown[0] <= rect[0] and shown[1] <= rect[1] and rect[0] + rect[2] <= shown[2]
                and rect[1] + rect[3] <= shown[3])
        got = bench.play(ib.build_plan("drag", surf, rect, False), None)
        assert ib.frames_of(got["raw"])
        assert ws.paint_layer._pop == "pickers"
    finally:
        host.pyexec(surf.teardown)
        bench.cleanup()
