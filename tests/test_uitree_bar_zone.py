"""The seam by which a page's row lent to the shell's bar reaches it
(L10): `BarLayer._zone_owner` answers with the owner's `bar_zone`, and the
zone (uitree_editor.BarZone) replays the lent row, takes its taps through
the engine and moves its generation only when what the row reads moved."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_reference as R  # noqa: E402


class _Damage:
    def all(self):
        pass


class _Look:
    """The host's cells, recorded: (what, rect, ...) a draw."""
    zone_gen = 0

    def __init__(self):
        self.drawn = []

    def zone_scale(self):
        return 1

    def tab_pad(self):
        return 3

    def tab_width(self, label, icon, h):
        return 16 + 4 + 6 * len(label) + 6

    def word_width(self, label, icon, h):
        return 16 + 4 + 6 * len(label) + 4

    def draw_tab(self, cv, r, cur, label, icon, labelled, every):
        self.drawn.append(("tab", r, cur, label if labelled else icon))

    def draw_icon(self, cv, r, icon, on, primary, marked=False):
        self.drawn.append(("icon", r, icon, on, marked))

    def draw_word(self, cv, r, label, icon, primary, marked=False):
        self.drawn.append(("word", r, label, marked))

    def draw_text(self, cv, r, text):
        self.drawn.append(("text", r, text))

    def draw_line(self, cv, x, zone):
        self.drawn.append(("line", x))


def _zone():
    """The fixture's page on the C engine (`uit_lent`, `uit_lent_place`)
    and the bar's zone over it."""
    import ui_draw
    import uitree_c
    import uitree_editor
    import uitree_bar_row as F
    page = uitree_c.CEditorPage(F.page, ui_draw.AppHost(), _Damage())
    page.open(hook="none")
    look = _Look()
    return F, page, look, uitree_editor.BarZone(page, look)


def test_the_bar_asks_an_owner_with_a_lent_row_its_zone():
    import bar_layer

    class App:
        zone_gen = 3

    class WS:
        editor_app = App()

    bl = bar_layer.BarLayer.__new__(bar_layer.BarLayer)
    bl.ws = WS()
    assert bl._zone_owner("menu") is WS.editor_app
    z = WS.editor_app.bar_zone = object()
    assert bl._zone_owner("menu") is z


def test_a_wide_zone_shows_the_labels_a_narrow_one_icons():
    _F, _page, look, z = _zone()
    z.draw_zone(None, (0, 0, 400, 20))
    assert [d[0] for d in look.drawn] == ["line", "tab", "tab", "icon", "word"]
    assert [d[3] for d in look.drawn if d[0] == "tab"] == ["One", "Two"]   # three not offered
    assert look.drawn[-1][1][0] + look.drawn[-1][1][2] == 400 - 6          # Play at the right end, END short
    look.drawn = []
    z.draw_zone(None, (0, 0, 140, 20))
    tabs = [d for d in look.drawn if d[0] == "tab"]
    assert [d[3] for d in tabs] == ["One", "code"]                         # the current one named
    assert [d[0] for d in look.drawn][-2:] == ["icon", "icon"]
    look.drawn = []
    z.draw_zone(None, (0, 0, 80, 20))
    assert [d[3] for d in look.drawn if d[0] == "tab"] == ["edit", "code"]


def test_between_the_two_the_buttons_keep_their_words_where_every_tab_holds_them():
    """Where not every label fits, the open tab is named and the buttons
    keep their words if that fits with ANY tab open, so a tab switch never
    takes Play's word away; else they are icons."""
    F, page, look, z = _zone()
    z.draw_zone(None, (0, 0, 160, 20))
    assert [d[3] for d in look.drawn if d[0] == "tab"] == ["One", "code"]
    assert look.drawn[-1][:3] == ("word", look.drawn[-1][1], "Play")
    page.run(setattr, F.s, "has_three", True)
    # "Three" is the widest name: with One open the words would fit at 172,
    # with Three open they would not, so they go on every tab
    look.drawn = []
    z.draw_zone(None, (0, 0, 172, 20))
    assert [d[3] for d in look.drawn if d[0] == "tab"] == ["One", "code", "paint"]
    assert look.drawn[-1][0] == "icon"
    look.drawn = []
    z.draw_zone(None, (0, 0, 180, 20))
    assert look.drawn[-1][2] == "Play"


def test_a_marked_button_is_drawn_marked():
    F, page, look, z = _zone()
    page.run(setattr, F.s, "playing", True)
    for owed in (False, True):
        page.run(setattr, F.s, "owed", owed)
        for w in (400, 120):
            look.drawn = []
            z.draw_zone(None, (0, 0, w, 20))
            assert look.drawn[-1][2] == ("Stop" if w == 400 else "stop")
            assert look.drawn[-1][-1] is owed


def test_a_tap_runs_through_the_engine_and_the_generation_follows():
    F, page, look, z = _zone()
    zone = (0, 0, 400, 20)
    g0 = z.zone_gen
    z.draw_zone(None, zone)
    z.draw_zone(None, zone)
    assert z.zone_gen == g0                       # nothing the row reads moved
    r = z.box("tab", "two", zone)
    assert z.zone_tap(r[0] + 1, r[1] + 1, zone)
    page._bind()
    assert (F.s.tab, F.s.opened) == ("two", 1) and z.zone_opened
    assert z.zone_gen != g0
    targets = z.zone_targets(zone)
    assert len(targets) == 4                       # One, Two, undo, Play
    u = targets[2]
    z.zone_tap(u[0] + 1, u[1] + 1, zone)
    page._bind()
    assert F.s.undone == 0                          # undo is off: nothing to take back
    page.run(setattr, F.s, "can_undo", True)       # the host's write
    z.zone_tap(u[0] + 1, u[1] + 1, zone)
    page._bind()
    assert F.s.undone == 1
    p = z.zone_targets(zone)[-1]
    z.zone_tap(p[0] + 1, p[1] + 1, zone)
    page._bind()
    assert F.s.playing
    look.drawn = []
    z.draw_zone(None, zone)
    assert look.drawn[-1][2] == "Stop"


def test_the_need_is_every_cell_an_icon():
    F, page, _look, z = _zone()
    # three cells of the tabs' two (the current one widened), undo, Play
    assert z.zone_need(20) == (20 + 2 * 3) + 20 + 20 + 20 + 2 * 2 + 8 + 6
    page.run(setattr, F.s, "has_three", True)
    assert z.zone_need(20) == (20 + 2 * 3) + 20 + 20 + 20 + 20 + 3 * 2 + 8 + 6


def _head():
    import ui_draw
    import uitree_c
    import uitree_editor
    import editor_ui
    page = uitree_c.CEditorPage(editor_ui.head, ui_draw.AppHost(), _Damage())
    page.open(hook="none")
    look = _Look()
    return editor_ui, page, look, uitree_editor.BarZone(page, look)


def test_the_editors_head_row_lays_out_as_the_reference_did():
    """The Editor's head row (editor_ui.head): each lender (handheld, theme
    mode, the desk's workspaces and its theme) in a wide, a middling and a
    narrow zone, the cells, hairlines and density (`uit_lent`,
    `uit_lent_place`) and so the strokes the reference engine recorded."""
    H, page, look, z = _head()
    out = []
    for theme, desk, playing, owed in ((False, False, False, False), (False, False, True, False),
                                       (True, False, False, False), (False, True, True, True),
                                       (True, True, False, False)):
        def put():
            H.s.theme, H.s.desk, H.s.playing, H.s.restart_owed = theme, desk, playing, owed
            H.s.theme_name = "Night"
            H.s.has_music = not desk
            H.s.can_undo = playing
        page.run(put)
        for w in (420, 200, 120):
            look.drawn = []
            z.draw_zone(None, (4, 2, w, 20))
            out.append((theme, desk, w, list(look.drawn), z.zone_need(20)))
    R.held(__file__, "head", out)
    assert any(d[0] == "text" for o in out for d in o[3])        # theme mode's name
