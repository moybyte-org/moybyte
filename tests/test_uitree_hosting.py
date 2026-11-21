"""A `uitree` app inside today's shell: it opens, paints, takes input and
closes on every size the shell goldens draw, beside the apps on today's
verbs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402
import test_shell_goldens as G  # noqa: E402

# one configuration per size, scale and tier the goldens draw
SIZES = sorted({(c["sys_size"], c["font_scale"], c["windowed"]): name
                for name, c in sorted(G.CONFIGS.items(), reverse=True)}.values())


@pytest.mark.parametrize("config", SIZES)
def test_a_uitree_app_opens_paints_takes_input_and_closes(config, tmp_path):
    import uitree_counter as C
    cls = C.layer()
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)
        seen["ws"] = ws

    # Add one twice, Reset (now on) asks, Back keeps; then Details is pushed
    # and Back pops it
    script = ["k:a", "k:a", "k:down", "k:a", "back", "k:down", "k:down", "k:down", "k:a", "back"]
    fs = P.frames(config, opened, script, str(tmp_path / "carts"))
    eng = seen["app"].engine
    trace = eng.trace_text()
    assert C.s.count == 2 and not C.s.asking, trace
    assert "action  add" in trace and "action  reset_ask" in trace and "action  keep" in trace, trace
    assert "action  more" in trace and "page    back" in trace, trace
    assert len(eng.pages) == 1
    for i in (1, 2, 4, 5, 9, 10):               # each of these steps drew a new frame
        assert fs[i].buf != fs[i - 1].buf, (i, fs[i].step, trace)


def test_a_uitree_app_closes_and_reopens_on_its_opened_hook(tmp_path):
    import uitree_counter as C
    cls = C.layer()
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    P.frames("tdeck_320x240_fs1_dark", opened, ["k:a"], str(tmp_path / "carts"))
    assert C.s.count == 1
    app = seen["app"]
    app.open()
    assert C.s.count == 0 and len(app.engine.pages) == 1


@pytest.mark.parametrize("config", ["tdeck_320x240_fs1_dark", SIZES[-1]])
def test_focus_lands_in_the_frame_that_first_draws_its_node(config, tmp_path):
    """A handler's ui.focus(KEY) on a node its own write shows, and the
    focus Back returns to the node that pushed, each land in the first
    frame that draws the node: never a frame drawn with focus elsewhere."""
    import uitree_focus as F
    seen, log = {}, []

    def opened(ws):
        app = seen["app"] = P.host(ws, F.layer())
        eng = app.engine
        paint = eng.paint

        def logged(*a):
            paint(*a)
            log.append(("extra" in eng.stop_kinds(), eng.focus_key))
        eng.paint = logged

    def first_drawn():
        drawn = [f for d, f in log if d]
        return drawn[0] if drawn else None

    P.frames(config, opened, ["k:a"], str(tmp_path / "carts"))
    assert F.s.open and first_drawn() == "extra", log
    del log[:]
    P.frames(config, opened, ["k:a", "k:a", "back"], str(tmp_path / "carts2"))
    eng = seen["app"].engine
    assert F.s.seen == 1 and len(eng.pages) == 1, eng.trace_text()
    back = [i for i, (d, f) in enumerate(log) if not d]
    assert back, log                                  # the pushed page drew no "extra"
    after = log[back[-1] + 1:]
    assert after and after[0] == (True, "extra"), log


def test_a_page_with_no_stop_answers_confirm_and_back_through_its_role_actions(tmp_path):
    """A on a page where no node takes it runs the `role:confirm` action, B
    the `role:back` one before the shell's back; the band names their hints,
    templates over the state, and draws no band when both are ""."""
    import uitree as S
    import uitree_roles as R
    cls = R.layer()
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    fs = P.frames("tdeck_320x240_fs1_dark", opened, ["k:a", "back"], str(tmp_path / "carts"))
    eng = seen["app"].engine
    trace = eng.trace_text()
    assert (R.s.sent, R.s.left) == (1, 1), trace
    assert "from role confirm" in trace and "from role back" in trace, trace
    S._NOW.app = eng
    try:
        assert eng.hints() == ("confirm", "Send", "back", "Cancel")
        eng.run_action(eng.actions["quiet"], (), "test")
        assert eng.hints() == ()
    finally:
        S._NOW.app = None
    assert eng.role("back") and R.s.left == 2           # a host routing its own keys
    assert fs[1].buf == fs[0].buf                        # the page drew nothing new


def test_two_engines_over_one_module_each_hold_their_own_state():
    """Two consoles in one process (a test's pair of desks) each open the
    module's page: each engine's handlers read and write its own values, and
    reach their own console through `env()`."""
    import uitree_app
    import uitree_focus as F
    a = uitree_app.Page(F.build, None, None, env="console a")
    b = uitree_app.Page(F.build, None, None, env="console b")
    a.run(lambda: setattr(F.s, "seen", 3), action="t")
    b.run(lambda: setattr(F.s, "seen", 5), action="t")
    got = []
    a.run(lambda: got.append((F.s.seen, uitree_app.env())), action="t")
    b.run(lambda: got.append((F.s.seen, uitree_app.env())), action="t")
    assert got == [(3, "console a"), (5, "console b")]   # each handler reaches its own console
    import uitree as S
    with pytest.raises(S.UIError) as e:
        uitree_app.env()
    assert e.value.code == "A10"


def test_the_display_kinds_draw_and_letters_and_arrows_fire_their_actions(tmp_path):
    """§7.2's kinds draw through the toolkit (a face, word wrap, a divider, a
    note, a lines view, a count, a grid's pads); a letter in `keys=` fires its
    action, and an arrow whose role an action answers fires it, focus staying
    where it is (§13)."""
    import uitree_kinds as K
    cls = K.layer()
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    fs = P.frames("tdeck_320x240_fs1_dark", opened, ["t:m", "k:right", "k:right", "k:left", "k:down", "k:a"],
                  str(tmp_path / "carts"))
    eng = seen["app"].engine
    trace = eng.trace_text()
    assert K.s.said == "marked" and K.s.steps == 1, trace
    assert "from key 'm'" in trace and "from role right" in trace and "from role left" in trace, trace
    assert len(K.s.log) == 2, trace                     # down to the grid's second button, A
    assert "from button 'grow'" in trace, trace
    assert fs[1].buf != fs[0].buf and fs[2].buf != fs[1].buf, "the letter and the arrow drew"
    assert eng._page_id() == "1 build"


def test_a_lines_view_that_follows_opens_at_its_foot_and_scrolls_a_new_line_in(tmp_path):
    """§7.2 text_view follow=True: the first frame already shows the last
    line (the view is aimed before it draws), and a line that arrives moves
    the view by one line, the rest of it unchanged."""
    import uitree as S
    import uitree_app
    import uitree_kinds as K
    d = S.decl_of("uitree_kinds")
    was = d.actions["opened"].fn

    def opened():
        K.s.log = [{"n": i, "line": "line %d" % i} for i in range(40)]
    d.actions["opened"].fn = opened
    try:
        cls = uitree_app.app_layer("uitree_log", "Log", K.log_page)
        fs = P.frames("tdeck_320x240_fs1_dark", lambda ws: P.host(ws, cls), ["t:g", "f:30"],
                      str(tmp_path / "carts"))
    finally:
        d.actions["opened"].fn = was
    w = fs[0].w * 2
    rows = [[f.buf[y * w:(y + 1) * w] for y in range(fs[0].h)] for f in (fs[0], fs[-1])]
    moved = [y for y in range(1, 200) if rows[1][y] != rows[0][y]]
    assert moved, "a new line moves the view"
    step = next(d for d in range(8, 24) if all(rows[1][y] == rows[0][y + d] for y in range(30, 180)))
    assert step in (13, 14, 15, 16), step


def _split(tmp_path, config, script=("f:2",)):
    import uitree_app
    import uitree_kinds as K
    K.DRAWN.clear()
    cls = uitree_app.app_layer("uitree_split", "Split", K.split_page)
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)
    fs = P.frames(config, opened, list(script), str(tmp_path / "carts"))
    return K, fs, seen["app"].engine


def test_a_canvas_that_is_a_splits_cell_draws_into_that_cell(tmp_path):
    """§7 split: each kid is a cell; a canvas there is handed its cell's
    rect, not a row stacked at the page's top."""
    K, fs, _eng = _split(tmp_path, "tdeck_320x240_fs1_dark")
    x, y, w, h = K.DRAWN[-1]
    assert x < 20 and 100 < w < fs[0].w * 2 // 5 + 4, (x, w)
    assert h > 120, h


def test_a_canvas_that_only_draws_is_no_stop(tmp_path):
    """L6: a canvas with no pointer=, keys= or on= draws and is not a focus
    stop: it wears no ring and the first stop is the column's button."""
    K, fs, eng = _split(tmp_path, "tdeck_320x240_fs1_dark")
    assert eng._ui.box("pad") is None
    assert eng._ui.box("mark") is not None


def test_a_splits_column_centres_and_its_button_takes_its_natural_width(tmp_path):
    """L8: a row split's column shorter than its cell stands centred on the
    cross axis; a button in it is as wide as its label needs, not the
    column."""
    K, fs, eng = _split(tmp_path, "p4_1024x600_fs2_windowed")
    cx, cy, cw, ch = K.DRAWN[-1]                # the canvas's cell: the column's height beside it
    bx, by, bw, bh = eng._ui.box("mark")
    assert by > cy + ch // 3, (by, cy, ch)      # pushed down from the cell's top
    assert by + bh < cy + ch * 2 // 3, (by, cy, ch)
    assert bw < 200, bw


def test_a_splits_column_centres_in_the_frame_it_first_draws(tmp_path):
    """The column is measured before it draws (the toolkit's `measure`), so
    its first frame stands where every later one does: no frame centres it
    late."""
    import uitree_app
    import uitree_kinds as K
    cls = uitree_app.app_layer("uitree_split", "Split", K.split_page)
    tops = []

    def opened(ws):
        eng = P.host(ws, cls).engine
        paint = eng.paint

        def logged(*a):
            paint(*a)
            box = eng._ui.box("mark")           # the column's button, where this frame drew it
            if box is not None:
                tops.append(box[1])
        eng.paint = logged
    P.frames("p4_1024x600_fs2_windowed", opened, ["f:2"], str(tmp_path / "carts"))
    assert len(tops) > 1 and len(set(tops)) == 1, tops


def test_a_placed_page_on_the_desk_names_its_keys(tmp_path, monkeypatch):
    """A split page on the desk draws the band of what its keys do, as a
    page of rows does (the handheld cuts its band from the page)."""
    K, fs, eng = _split(tmp_path, "p4_1024x600_fs2_windowed")
    said = eng.hints()
    assert "back" in said, said
    x, y, w, h = eng.content
    f, row = fs[-1], 2 * fs[-1].w
    band = [f.buf[yy * row + 2 * x:yy * row + 2 * (x + w)] for yy in range(y + h - 16, y + h)]
    assert any(len(set(line[i:i + 2] for i in range(0, len(line), 2))) > 1 for line in band), \
        "no band drawn at the page's foot"


def _foot(tmp_path, config, script=("f:2",)):
    import uitree_app
    import uitree_foot as F
    cls = uitree_app.app_layer("uitree_foot", "Foot", F.foot_page)
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)
    fs = P.frames(config, opened, list(script), str(tmp_path / "carts"))
    return fs, seen["app"].engine


@pytest.mark.parametrize("config", ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed"))
def test_a_shared_foot_splits_its_width_under_its_line(tmp_path, config):
    """F-13 `foot look="share"`: its buttons are equally wide across the
    foot, its label on a line of its own above them; the keys band stays
    the content's last line, under the foot (on the desk the foot is the
    page's, docked above the band on the page's ground: `ui.dock`)."""
    fs, eng = _foot(tmp_path, config)
    a, b = eng._ui.box("mark"), eng._ui.box("drop")
    assert a and b and a[1] == b[1] and abs(a[2] - b[2]) <= 1, (a, b)
    x, y, w, h = eng.foot_r
    assert a[0] - x == x + w - (b[0] + b[2]) and b[0] + b[2] - a[0] > w * 3 // 4, (a, b, eng.foot_r)
    assert a[1] > y + h // 3, (a, eng.foot_r)          # the label's line above them
    if config.startswith("tdeck"):
        assert y + h <= eng.content[1] + eng.content[3] - 20, (eng.foot_r, eng.content)
    else:
        assert eng.rect[1] + eng.rect[3] <= y, (eng.rect, eng.foot_r)        # the rows end above it
        assert y + h + 20 <= eng.content[1] + eng.content[3], (eng.foot_r, eng.content)   # the band under it


def test_a_table_is_named_by_its_key_before_it_draws(tmp_path):
    """A table is one stop, its key its id: `ui.focus(TABLE)` and a cur=
    the opening writes resolve to it before its first frame, so the key
    pressed next lands on it (the task manager's Enter after it opens)."""
    fs, eng = _foot(tmp_path, "tdeck_320x240_fs1_dark", ("f:1",))
    assert eng.focus_key == "log", eng.focus_key
    stops = eng.stop_kinds()
    assert stops.get("log") == "table", stops                   # one stop, by its key
    assert not [k for k in stops if k.startswith("log/")], stops     # its rows are no stops of their own
