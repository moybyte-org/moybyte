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
            log.append(("extra" in eng.ids, eng.focus_key))
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
        assert eng._hints() == ("confirm", "Send", "back", "Cancel")
        eng.run_action(eng.actions["quiet"], (), "test")
        assert eng._hints() == ()
    finally:
        S._NOW.app = None
    assert eng.role("back") and R.s.left == 2           # a host routing its own keys
    assert fs[1].buf == fs[0].buf                        # the page drew nothing new


def test_two_engines_over_one_module_each_hold_their_own_state():
    """Two consoles in one process (a test's pair of desks) each open the
    module's page: each engine's handlers read and write its own values."""
    import uitree_app
    import uitree_focus as F
    a = uitree_app.Page(F.build, None, None)
    b = uitree_app.Page(F.build, None, None)
    a.run(lambda: setattr(F.s, "seen", 3), action="t")
    b.run(lambda: setattr(F.s, "seen", 5), action="t")
    got = []
    a.run(lambda: got.append(F.s.seen), action="t")
    b.run(lambda: got.append(F.s.seen), action="t")
    assert got == [3, 5]


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
