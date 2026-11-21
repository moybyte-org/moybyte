"""The bridge (`ui.legacy`, spec §7.7) and the panes on the desk Editor's
rules (runtime/uitree/uitree_editor.py), over the fixture
tests/uitree_fixtures/uitree_legacy_app.py on every size the shell goldens
draw: a hosted surface drawn in its own verbs pass BEFORE the page's, its
pixels kept under the page's pass and under a popover's, its pointer, keys,
hints, focus and gen; the panes' held sizes, double tap and Ctrl+0 resets,
the Ctrl+Alt+arrow nudge, the minimum's flash, pane focus and grow."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import uitree_parity as P  # noqa: E402
import test_shell_goldens as G  # noqa: E402

SIZES = sorted({(c["sys_size"], c["font_scale"], c["windowed"]): name
                for name, c in sorted(G.CONFIGS.items(), reverse=True)}.values())
DESK = [c for c in SIZES if G.CONFIGS[c]["windowed"]]


class Run:
    """One console of golden configuration `config` with the fixture open,
    driven a step at a time, each step's settled glass kept."""

    def __init__(self, config, tmp_path):
        import uitree_legacy_app as E
        from runtime.host_api import ConsoleDriver
        self.E = E
        self.ws = G._build(G.CONFIGS[config], str(tmp_path / "carts"))
        self.app = P.host(self.ws, E.layer())
        self.eng = self.app.engine
        self.driver = ConsoleDriver(self.ws)
        self.frames = [self.settle()]
        self.surf = E._SURF[id(self.eng.env)]

    def settle(self):
        return P._settle(self.ws, G, self.driver)

    def close(self):
        self.E._SURF.pop(id(self.eng.env), None)
        self.ws.home.go_home()
        from runtime import scheduler
        scheduler.SCHED.reset()
        G._ui().theme_uninstall()

    def origin(self):
        win = getattr(self.ws.wm, "windows", {}).get(self.app.id)
        return win.content_rect()[:2] if win is not None else (0, 0)

    def step(self, fn, *a):
        fn(*a)
        self.frames.append(self.settle())

    def key(self, spec):
        self.step(P._key, self.driver, spec)

    def type(self, ch):
        def go():
            self.driver.type_char(ord(ch))
            self.driver.frame(P.DT)
        self.step(go)

    def tap(self, x, y, twice=False):
        ox, oy = self.origin()

        def go():
            for _ in range(2 if twice else 1):
                self.driver.touch(ox + x, oy + y)
                self.driver.frame(P.DT)
                self.driver.touch_up()
                self.driver.frame(P.DT)
        self.step(go)

    def drag(self, x, y, dx, dy, hold=None):
        """A press at (x, y), four moves to (x+dx, y+dy); `hold` runs while
        it is held there, then the release."""
        ox, oy = self.origin()
        gx, gy = ox + x, oy + y

        def go():
            d = self.driver
            d.touch(gx, gy)
            d.frame(P.DT)
            for k in range(1, 5):
                d.touch_drag(gx + dx * k // 4, gy + dy * k // 4)
                d.frame(P.DT)
            if hold is not None:
                hold()
            d.touch_up()
            d.frame(P.DT)
        self.step(go)

    def px(self, frame, x, y):
        ox, oy = self.origin()
        w = self.ws.glass_canvas.w
        i = 2 * ((oy + y) * w + ox + x)
        return frame[i] | frame[i + 1] << 8

    def pane(self, key):
        return next(r for p, r in self.eng.pane_order if p.key == key)


@pytest.fixture
def run(request, tmp_path):
    made = []

    def make(config):
        r = Run(config, tmp_path)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


@pytest.mark.parametrize("config", SIZES)
def test_a_hosted_surface_draws_first_and_the_pass_keeps_its_pixels(config, run):
    """The hosted surface draws once a frame, in the rect the page lays out
    for it, before the page's verbs pass; neither the page's ground nor its
    stop's well is drawn over it: its 4-row mark stands on the glass."""
    r = run(config)
    eng, surf = r.eng, r.surf
    rect = eng.legacies["list"][1]
    assert surf.rects[-1] == rect and surf.draws >= 2
    assert all(p[0].key == "list" and p[2] == rect for p in eng.placed)
    x, y, w, _h = rect
    f = r.frames[-1]
    # (its first row may wear the ring of the head row's focused button)
    mark = {r.px(f, x + dx, y + dy) for dx in (w // 4, w // 2, 3 * w // 4) for dy in (1, 3)}
    assert len(mark) == 1, mark
    assert r.px(f, x + w // 2, y + 6) not in mark      # its own page's ground, under the mark
    if G.CONFIGS[config]["windowed"]:
        lp = r.pane("list_pane")
        u = eng.du
        assert rect == (lp[0], lp[1] + 32 * u, lp[2], lp[3] - 32 * u)   # the pane's body: under its header


@pytest.mark.parametrize("config", SIZES)
def test_a_popover_lies_over_the_hosted_body(config, run):
    r = run(config)
    x, y, w, h = r.eng.legacies["list"][1]
    r.key("C-u")
    assert r.E.s.menu and r.eng.popover_shown()
    before, after = r.frames[-2], r.frames[-1]
    covered = [(cx, cy) for cx in range(x, x + w, 4) for cy in range(y, y + h, 4)
               if r.px(before, cx, cy) != r.px(after, cx, cy)]
    assert covered, "the popover drew nothing over the hosted body"
    assert r.surf.draws >= 3          # the body under it still drawn, the popover over it
    r.step(r.driver.escape)
    assert not r.E.s.menu


@pytest.mark.parametrize("config", SIZES)
def test_a_press_focuses_the_hosted_stop_and_its_keys_and_hints_are_its_own(config, run):
    """A press on the hosted body hands it the press in its own coordinates
    and gives its stop focus; typed keys then go to it, not to the page's
    letters; the page's band shows its hints; a key it declines moves focus
    on as from any stop."""
    r = run(config)
    eng, surf, E = r.eng, r.surf, r.E
    x, y, w, h = eng.legacies["list"][1]
    r.tap(x + 10, y + 20)
    assert surf.presses[0] == (10, 20, True), surf.presses
    assert eng.focused is not None and eng.focused[0].key == "list"
    assert surf.focused[-1] is True
    said = E.s.said
    r.type("q")
    assert surf.typed == ["q"] and E.s.said == said
    assert eng._hints() == ("confirm", "Pick")
    if G.CONFIGS[config]["windowed"]:
        assert E.s.on == "list_pane"
    if not G.CONFIGS[config]["windowed"]:
        r.step(r.driver.press, "up")            # the head row's Say, above it
        assert eng.focused is not None and eng.focused[0].key == "say"


@pytest.mark.parametrize("config", SIZES[:1] + DESK[:1])
def test_a_hosted_surface_repaints_on_its_gen_and_not_otherwise(config, run):
    r = run(config)
    eng, surf = r.eng, r.surf
    assert not eng.legacy_moved() and not eng.idle_owed
    surf.gen += 1
    assert eng.legacy_moved() and eng.idle_owed
    n = surf.draws
    r.step(lambda: None)
    assert surf.draws > n and not eng.legacy_moved() and not eng.idle_owed


@pytest.mark.parametrize("config", DESK)
def test_panes_hold_sizes_at_the_unit_and_reset(config, run):
    """The desk Editor's rules: the gutter at the unit; a divider's drag
    writes the held size on let-go; a double tap and Ctrl+0 put it back;
    Ctrl+Alt+Right moves the focused pane's right divider KEY_STEP units."""
    r = run(config)
    eng, E = r.eng, r.E
    u = eng.du
    lp, tools = r.pane("list_pane"), r.pane("tools")
    assert tools[0] - (lp[0] + lp[2]) == 6 * u
    assert E.s.side_w is None
    cx, cy = eng.divider_at(0, "split:list_pane/right")
    r.drag(cx, cy, -30, 0)
    assert E.s.side_w == 200 * u + 30, eng.trace_text()
    assert r.pane("tools")[2] == 200 * u + 30
    r.tap(cx - 30, cy, twice=True)
    assert E.s.side_w is None, eng.trace_text()
    r.drag(cx, cy, -30, 0)
    assert E.s.side_w is not None
    r.key("C-0")
    assert E.s.side_w is None and E.s.run_h is None
    r.tap(lp[0] + 10, lp[1] + 40)
    assert E.s.on == "list_pane"
    r.key("C-A-Left")
    assert E.s.side_w == 200 * u + 8 * u, eng.trace_text()


@pytest.mark.parametrize("config", DESK)
def test_a_divider_held_at_a_minimum_flashes(config, run):
    r = run(config)
    eng = r.eng
    cx, cy = eng.divider_at(0, "split:list_pane/right")
    seen = []
    r.drag(cx, cy, 400, 0, hold=lambda: (r.driver.frame(P.DT), seen.append(eng.flash)))
    assert seen[0] is not None and seen[0][0].key == "tools", seen      # the column's first pane
    assert "stops at its minimum, %d wide" % (160 * eng.du) in seen[0][1], seen
    assert eng.flash is None


@pytest.mark.parametrize("config", DESK)
def test_pane_focus_steps_and_a_pane_grows(config, run):
    r = run(config)
    eng, E = r.eng, r.E
    assert E.s.on == "list_pane"
    r.key("F6")
    assert E.s.on == "tools" and eng.focused[0].key == "say", eng.trace_text()
    r.key("F6")
    assert E.s.on == "notes"
    r.key("S-F6")
    r.key("S-F6")
    assert E.s.on == "list_pane" and eng.focused[0].key == "list"
    r.key("C-S-Enter")
    assert E.s.big == "list_pane"
    assert [p.key for p, _r in eng.pane_order] == ["list_pane"]
    assert eng.legacies["list"][1][2] > r.surf.rects[0][2]       # the body over the whole area
    r.step(r.driver.escape)
    assert E.s.big is None and len(eng.pane_order) == 3


def test_fit_gives_the_growing_child_room_last_first():
    import uitree_editor
    assert uitree_editor.fit([100, None, 100], [50, 80, 50], 230) == [100, 80, 50]
    assert uitree_editor.fit([100, None, 100], [50, 80, 50], 180) == [50, 80, 50]
    assert uitree_editor.fit([30, None], [50, 80], 300) == [50, 250]


BAD = {
    "in_row": ("N03", 'def build():\n    with ui.page("P"):\n        with ui.row("r"):\n'
                      '            ui.legacy("x", draw)\n', 10),
    "no_fn": ("N01", 'def build():\n    with ui.page("P"):\n        ui.legacy("x", 3)\n', 9),
    "nested_focus": ("N03", 'def build():\n    with ui.page("P"):\n        with ui.panes("row", "a"):\n'
                            '            with ui.pane("p", grow=True):\n                pass\n'
                            '            with ui.panes("col", "b", size="w", start=10, focused="on"):\n'
                            '                with ui.pane("q", grow=True):\n                    pass\n', 12),
    # a start= naming state names an int one
    "start_text": ("T03", 'def build():\n    with ui.page("P"):\n        with ui.panes("row", "a"):\n'
                          '            ui.pane("p", grow=True)\n'
                          '            ui.pane("q", size="w", start="on")\n', 11),
    # min= and offered= by tab need tabs=, and name its values
    "min_no_tabs": ("N01", 'def build():\n    with ui.page("P"):\n        with ui.panes("row", "a"):\n'
                           '            ui.pane("p", grow=True, min={"x": (1, 1)})\n', 10),
    "offered_extra": ("N01", 'def build():\n    with ui.page("P"):\n        with ui.panes("row", "a"):\n'
                             '            ui.pane("p", grow=True, tabs="t", labels="T", offered={"z": "big"})\n', 10),
    "head_word": ("N01", 'def build():\n    with ui.page("P"):\n        with ui.panes("row", "a"):\n'
                         '            ui.pane("p", grow=True, head="no")\n', 10),
    # two that grow, neither shown by a condition
    "two_grow": ("N07", 'def build():\n    with ui.page("P"):\n        with ui.panes("row", "a"):\n'
                        '            ui.pane("p", grow=True)\n            ui.pane("q", grow=True, shown="big")\n', 9),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_the_bridge_and_the_panes_refuse_their_mistakes(case, tmp_path):
    import uitree_check
    code, src, line = BAD[case]
    path = tmp_path / ("legacy_%s.py" % case)
    path.write_text('import uitree as ui\ns = ui.state(w=ui.opt(0), on=ui.opt(""), big=True,\n'
                    '                 t=ui.enum("x", "y", init="x"))\nui.labels("T", {"x": "X", "y": "Y"})\n'
                    'def draw(cv, th, x, y, w, h, focused, dt):\n    pass\n' + src)
    res = uitree_check.check(str(path))
    got = [(e.code, e.where[1] if e.where else None) for e in res.errors]
    assert (code, line) in got, (got, [str(e) for e in res.errors])


def test_a_legacy_node_needs_the_editor_replay_and_a_hosted_phase(tmp_path):
    """The toolkit-laid replay refuses one (N03: it places nothing before
    its pass); the Editor's refuses one whose host ran no hosted phase."""
    import types
    import uitree as S
    import uitree_app
    import uitree_editor
    import uitree_legacy_app as E
    base = uitree_app.Engine(S.decl_of(S.module_of(E.build)), E.build, None, None, None)
    with pytest.raises(S.UIError) as e:
        base._r_legacy(types.SimpleNamespace(key="list", where=None), None, (0, 0, 1, 1))
    assert e.value.code == "N03"
    ed = uitree_editor.Engine(S.decl_of(S.module_of(E.build)), E.build, None, None, None)
    ed.frame_no = 3
    with pytest.raises(S.UIError) as e:
        ed._r_legacy(types.SimpleNamespace(key="list", where=None), None, (0, 0, 1, 1))
    assert e.value.code == "N03" and "hosted" in str(e.value)
