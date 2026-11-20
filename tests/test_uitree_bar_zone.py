"""The seam by which a page's row lent to the shell's bar reaches it
(L10): `BarLayer._zone_owner` answers with the owner's `bar_zone`, and the
zone (uitree_editor.BarZone) replays the lent row, takes its taps through
the engine and moves its generation only when what the row reads moved."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))


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

    def draw_icon(self, cv, r, icon, on, primary):
        self.drawn.append(("icon", r, icon, on))

    def draw_word(self, cv, r, label, icon, primary):
        self.drawn.append(("word", r, label))

    def draw_text(self, cv, r, text):
        self.drawn.append(("text", r, text))

    def draw_line(self, cv, x, zone):
        self.drawn.append(("line", x))


def _zone():
    import ui_draw
    import uitree_editor
    import uitree_bar_row as F
    page = uitree_editor.Page(F.page, ui_draw.AppHost(), _Damage())
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
