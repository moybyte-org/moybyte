"""The Appearance page (docs/theming_2026-09.md §9 phase 6 step 7): one page
of rows, every row reached and changed by keys alone and by a tap, the
pickers' undo, the tap floor, and the rows' writes."""

import json
from pathlib import Path

import pytest

from runtime import host_app, ui_draw
from ws_helpers import select_on_home  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
DESK = dict(sys_size=(1024, 600), font_scale=1, windowed=True,
            panel_diagonal_in=7.0)


class K:
    """One key press, as the page's handler reads it."""
    last_key = 0

    def __init__(self, n):
        self.n = n

    def pressed(self, n):
        return n == self.n


def _open_appearance(ws):
    if select_on_home(ws, "Appearance") is None:
        # Not on the home: Appearance is one of the WM's HIDDEN_APPS.
        assert ws.open_app(ws.appearance_app)
    else:
        ws.open()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_kind() == "appearance"
    return _app(ws)


def _app(ws):
    return ws.appearance_app


def _frame(ws, n=2):
    for _ in range(n):
        ws.input.begin_frame()
        ws._dirty = True
        ws.frame(1 / 30)


def _press(ws, app, *keys):
    for k in keys:
        app.handle_input(K(k))
        _frame(ws, 1)
    _frame(ws)


def _ws(tmp_path, **kw):
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def _focus(app, key):
    app.zone = "rows"
    app.row = app._rows().index(key)


def _custom(tmp_path):
    p = tmp_path / "carts" / "custom.moy" / "theme.json"
    return json.loads(p.read_text()) if p.exists() else None


def test_appearance_cart_is_versioned_system_app():
    folder = ROOT / "system_carts" / "moybyte.appearance.moy"
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert man["version"] >= 1
    assert man["moybyte"]["type"] == "app"
    assert "appearance" in man["moybyte"]["permissions"]
    compile((folder / "main.py").read_text(encoding="utf-8"),
            str(folder / "main.py"), "exec")


def test_the_rows_are_the_rulings_on_a_small_and_a_wide_page(tmp_path):
    small = _open_appearance(_ws(tmp_path / "s"))
    assert not small.layout.wide
    assert small._rows() == ["theme", "mode", "accent", "tint", "wallpaper",
                             "frame", "font", "file"]
    wide = _open_appearance(_ws(tmp_path / "w", **DESK))
    assert wide.layout.wide                  # THEME is the grid above the rows
    assert wide._rows()[0] == "mode" and "theme" not in wide._rows()
    assert "chrome" in wide._rows()           # the P4's desk takes two sizes
    sim = _open_appearance(_ws(tmp_path / "b", sys_size=(1024, 600),
                               windowed=True))
    assert "chrome" in sim._rows()            # the simulator's desk takes two


@pytest.mark.parametrize("kw", [{}, DESK], ids=["320x240", "desk"])
def test_every_row_changes_by_keys_alone(tmp_path, kw):
    ws = _ws(tmp_path, **kw)
    app = _open_appearance(ws)
    look = ws.look
    # MODE: not a change to the theme.
    _focus(app, "mode")
    _press(ws, app, "right")
    assert look.theme_variant == "light" and look.theme_name == "night"
    _press(ws, app, "left")
    assert look.theme_variant == "dark" and _custom(tmp_path) is None
    # ACCENT: the first change makes custom.
    _focus(app, "accent")
    _press(ws, app, "right")
    assert look.theme_name == "custom"
    a = look.custom_look()["accent"]
    assert a == ui_draw.theme_accents()[1][1]      # night's own is yellow, the first
    _press(ws, app, "left")
    assert look.custom_look()["accent"] == ui_draw.theme_accents()[0][1]
    # TINT, WALLPAPER, WINDOW STYLE, FONT.
    _focus(app, "tint")
    _press(ws, app, "right")
    assert look.custom_look()["tint"] == ui_draw.theme_tints()[0][0]
    _focus(app, "wallpaper")
    was = look.wallpaper_id
    _press(ws, app, "right")
    assert look.wallpaper_id != was
    _focus(app, "frame")
    _press(ws, app, "right")
    assert app.choices()["frame"] == "tab"
    _focus(app, "font")
    _press(ws, app, "right")
    assert app.choices()["font"] == "petme128" and ui_draw.font("ui")[0] == 0
    look.commit()
    pkg = _custom(tmp_path)
    assert pkg["frame"] == {"style": "tab"}
    assert pkg["fonts"]["ui"] == "petme128"
    folder = tmp_path / "carts" / "custom.moy"
    assert not (folder / "petme128").exists()   # a reserved name, no file
    _press(ws, app, "left")
    assert app.choices()["font"] == "tiny5" and ui_draw.font("ui")[0] != 0
    # THEME: ←→ steps the theme in place (a row on the small page, the grid
    # on the wide one).
    if not app.layout.wide:
        _focus(app, "theme")
        _press(ws, app, "right")
        assert look.theme_name == app._themes()[1]
    else:
        _focus(app, "mode")
        _press(ws, app, "up", "right", "a")
        assert look.theme_name == app._themes()[app.card]


def test_every_picker_opens_tries_and_keeps_by_keys(tmp_path):
    ws = _ws(tmp_path)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "a")
    assert app.pick == "accent" and app.cur == 0
    _press(ws, app, "right", "down")            # tried live
    assert ws.look.custom_look()["accent"] == ui_draw.theme_accents()[9][1]
    _press(ws, app, "a")                        # kept
    assert app.pick is None and ws.look.theme_name == "custom"
    _focus(app, "tint")
    _press(ws, app, "a", "right", "right", "a")
    assert ws.look.custom_look()["tint"] == ui_draw.theme_tints()[1][0]
    _focus(app, "wallpaper")
    _press(ws, app, "a")
    walls = app._walls()
    _press(ws, app, "right", "a")
    assert ws.look.wallpaper_id == walls[1][0]
    _focus(app, "theme")
    _press(ws, app, "a")
    assert app.pick == "theme"
    names = app._themes()
    _press(ws, app, "right")
    assert ws.look.theme_name == "custom"       # the grid's cursor tries nothing
    _press(ws, app, "a")
    assert ws.look.theme_name == names[(names.index("custom") + 1) % len(names)]


def test_a_pickers_undo_restores_what_was_there(tmp_path):
    ws = _ws(tmp_path)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "a", "right", "right")
    assert ws.look.theme_name == "custom"
    _press(ws, app, "b")                        # the try made custom of night
    assert app.pick is None and ws.look.theme_name == "night"
    # On custom, the tried value is changed back.
    _press(ws, app, "right")
    a = ws.look.custom_look()["accent"]
    _press(ws, app, "a", "right", "down")
    assert ws.look.custom_look()["accent"] != a
    _press(ws, app, "b")
    assert ws.look.theme_name == "custom" and ws.look.custom_look()["accent"] == a
    _focus(app, "tint")
    _press(ws, app, "a", "right", "b")
    assert ws.look.custom_look().get("tint") is None
    _focus(app, "wallpaper")
    was = ws.look.wallpaper_id
    _press(ws, app, "a", "right", "b")
    assert ws.look.wallpaper_id == was


def test_the_wallpaper_undo_puts_a_store_themes_picture_back(tmp_path):
    """A theme shipping its own picture shows it as THE THEME'S PICTURE,
    never My Art, and a try's undo on custom puts it back (THEIRS)."""
    ws = _ws(tmp_path)
    look = ws.look
    raw = bytearray(512 * 300)
    assert ws.artwork.save(raw, 512, 300) and ws.artwork.set_wallpaper()
    look.tick(0.0)
    look.commit()
    src = json.loads((tmp_path / "carts" / "custom.moy" / "theme.json").read_text())
    assert src["images"] == {"desk": "desk.moyimg"}
    # Make that package a store theme of its own, then a custom from it.
    folder = tmp_path / "carts" / "pic.moy"
    folder.mkdir()
    src["name"] = "pic"
    (folder / "theme.json").write_text(json.dumps(src))
    (folder / "desk.moyimg").write_bytes(
        (tmp_path / "carts" / "custom.moy" / "desk.moyimg").read_bytes())
    man = json.loads((tmp_path / "carts" / "custom.moy" / "manifest.json").read_text())
    man["moybyte"].pop("look", None)
    (folder / "manifest.json").write_text(json.dumps(man))
    ws.carts.adopt(str(folder))
    look.set_theme("pic")
    look.tick(0.0)
    app = _open_appearance(ws)
    assert app.choices()["picture"] == "theme"
    assert app._wall_title(app.choices()) == "THEME'S PICTURE"
    assert app._walls()[0][2] == "theirs"
    _focus(app, "accent")
    _press(ws, app, "right")                     # custom from pic
    assert look.theme_name == "custom" and app.choices()["picture"] == "theme"
    _focus(app, "wallpaper")
    _press(ws, app, "a", "right")
    assert look.wallpaper_id != look.PICTURE
    _press(ws, app, "b")
    assert look.wallpaper_id == look.PICTURE and app.choices()["picture"] == "theme"


@pytest.mark.parametrize("kw", [{}, DESK], ids=["320x240", "desk"])
def test_every_row_answers_a_tap(tmp_path, kw):
    ws = _ws(tmp_path, **kw)
    app = _open_appearance(ws)
    _frame(ws)

    def tap(key, dx=None):
        r = dict(app._row_rects())[key]
        x = r[0] + r[2] - 12 if dx is None else r[0] + dx
        app.handle_pointer(x, r[1] + r[3] // 2, True)
        _frame(ws)

    light = dict(app._mode_chips(dict(app._row_rects())["mode"]))["light"]
    app.handle_pointer(light[0] + 2, light[1] + 2, True)
    _frame(ws)
    assert ws.look.theme_variant == "light"
    for key in ("accent", "tint", "wallpaper"):
        tap(key)
        assert app.pick == key
        r = app._pick_rects()[2]
        app.handle_pointer(r[0] + 2, r[1] + 2, True)   # ENTER on the swatch
        _frame(ws)
        assert app.pick is None
    assert ws.look.custom_look()["accent"] == ui_draw.theme_accents()[2][1]
    assert ws.look.custom_look()["tint"] == ui_draw.theme_tints()[1][0]
    tap("frame")
    assert app.choices()["frame"] == "tab"
    r = dict(app._row_rects())["frame"]
    app.handle_pointer(app._cycle_x(r) + 2, r[1] + r[3] // 2, True)   # the "<"
    _frame(ws)
    assert app.choices()["frame"] == "classic"
    tap("font")
    assert app.choices()["font"] == "petme128"
    if not app.layout.wide:
        tap("theme")
        assert app.pick == "theme"
        r = app._pick_rects()[app._themes().index("berry")]
        app.handle_pointer(r[0] + 2, r[1] + 2, True)
        _frame(ws)
        assert ws.look.theme_name == "berry"
    else:
        r = app.layout.card_rect(app._themes().index("berry"), app.top)
        app.handle_pointer(r[0] + 2, r[1] + 2, True)
        _frame(ws)
        assert ws.look.theme_name == "berry"
    tap("file")
    assert ws.look._editing is not None         # the text app holds the file


def test_a_tap_outside_a_popover_closes_it_keeping_the_try(tmp_path):
    ws = _ws(tmp_path, **DESK)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "a", "right")
    a = ws.look.custom_look()["accent"]
    x, y, w, h = app._pick_frame()
    app.handle_pointer(app.layout.cx + 4, app.layout.strip_y - 4, True)
    _frame(ws)
    assert app.pick is None and ws.look.custom_look()["accent"] == a


def test_the_tap_floor_holds_at_320x240(tmp_path):
    """#203: every row and every picker item a finger takes is at least the
    floor's 16 logical pixels at the chrome scale."""
    ws = _ws(tmp_path)
    app = _open_appearance(ws)
    floor = 16 * app.layout.cs
    for _key, r in app._row_rects():
        assert r[3] >= floor
    for key in ("theme", "accent", "tint", "wallpaper"):
        _focus(app, key)
        _press(ws, app, "a")
        for r in app._pick_rects():
            if r is not None:
                assert r[2] >= floor and r[3] >= floor, (key, r)
        _press(ws, app, "b")


def test_rows_that_do_not_fit_scroll_to_the_focus(tmp_path):
    ws = _ws(tmp_path, sys_size=(800, 480), font_scale=3)
    app = _open_appearance(ws)
    _focus(app, "file")
    _frame(ws)
    keys = [k for k, _r in app._row_rects()]
    assert "file" in keys and app.scroll > 0
    r = dict(app._row_rects())["file"]
    assert r[1] + r[3] <= app.layout.strip_y


def test_chrome_size_steps_live_and_persists(tmp_path):
    ws = _ws(tmp_path, sys_size=(1024, 600), windowed=True)
    app = _open_appearance(ws)
    assert ws.look.chrome_scale == 0 and ws.layout.cs == 1
    _focus(app, "chrome")
    _press(ws, app, "right")
    assert ws.layout.cs == 2 and ws.system.get("chrome_scale") == 2
    assert ws.look.theme_name == "night"        # not a change to the theme
    again = _ws(tmp_path, sys_size=(1024, 600), windowed=True)
    assert again.look.chrome_scale == 2 and again.layout.cs == 2


def test_the_page_is_idle_once_drawn(tmp_path):
    ws = _ws(tmp_path)
    app = _open_appearance(ws)
    _frame(ws)
    ws._toast_until = 0
    before = bytes(ws.sys_canvas._buf)
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert bytes(ws.sys_canvas._buf) == before
    assert ws._animating(1 / 30) is False
    calls = []
    real = app._theme.choices
    app._theme.choices = lambda *a: calls.append(1) or real(*a)
    for _ in range(3):
        ws.input.begin_frame()
        ws._dirty = True
        ws.frame(1 / 30)
    assert not calls                            # read once per generation


def test_a_window_close_writes_a_change_still_settling(tmp_path):
    ws = _ws(tmp_path, **DESK)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "right")
    assert _custom(tmp_path) is None            # the debounce has not run
    ws.wm.close_window_kind("appearance")
    assert _custom(tmp_path) is not None


# -- EDIT FILE: the editor beside the rows -----------------------------------------

def _edit(ws, app):
    _focus(app, "file")
    _press(ws, app, "a")
    look = ws.look
    assert look._editing is not None
    h = look.file_watch(*look._editing)
    assert h is look
    return look


def _handle(ws):
    """The text app's editor handle on custom's theme.json."""
    hs = [h for h in ws.player._editors if h._watch is ws.look]
    assert hs, "EDIT FILE opened no watched editor"
    return hs[-1]


def test_an_editor_save_writes_no_journal_into_the_package(tmp_path):
    """A theme package stays flat data: the editor's saves land through the
    look (`file_write`), never the project-file save and its journal."""
    ws = _ws(tmp_path)
    app = _open_appearance(ws)
    _edit(ws, app)
    _frame(ws)
    h = _handle(ws)
    folder = tmp_path / "carts" / "custom.moy"
    d = json.loads(h.text())
    d["colors"]["dark"]["panel"] = 3
    h.set_text(json.dumps(d, indent=1))
    ok, _badge = h.save()
    assert ok
    assert json.loads((folder / "theme.json").read_text())["colors"]["dark"]["panel"] == 3
    h.set_text("{ not json")
    h.save()                                    # the hard exit writes anyway
    names = sorted(p.name for p in folder.iterdir())
    assert "journal" not in names and "journal.jsonl" not in names, names
    assert set(names) <= {"manifest.json", "theme.json", "desk.moyimg",
                          "tiny5.fnt", "tiny5.txt"}, names


def test_a_clean_buffer_follows_a_change_and_an_edited_one_keeps_its_text(tmp_path):
    """The rule for a change made beside an open EDIT FILE: a buffer with no
    edits reloads (the look moved, `file_gen`); one with edits keeps them,
    and its save is the last write."""
    ws = _ws(tmp_path)
    app = _open_appearance(ws)
    look = _edit(ws, app)
    _frame(ws)
    h = _handle(ws)
    start = h.text()
    ws.open_app(ws.appearance_app)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "right")
    h.draw(0, 0, 200, 100)
    assert h.text() != start
    assert json.loads(h.text()) == json.loads(look.file_text())
    assert not h.dirty()
    h.ed.insert(" ")
    mine = h.text()
    _press(ws, app, "right")
    h.draw(0, 0, 200, 100)
    assert h.text() == mine                    # an edited buffer keeps its text
    h.save()
    assert json.loads((tmp_path / "carts" / "custom.moy" / "theme.json")
                      .read_text()) == json.loads(mine)


def test_the_pages_copies_of_the_looks_names_agree():
    """The page cannot import the look (a shell module), so it carries the
    names it passes: held to the look's here."""
    from runtime import appearance_app as page
    from runtime.appearance import Appearance
    assert page.FRAMES == Appearance.FRAMES and page.FONTS == Appearance.FONTS
    assert page.PICTURE == Appearance.PICTURE and page.THEIRS == Appearance.THEIRS
    assert set(page.FRAME_NAMES) == set(page.FRAMES)
    assert set(page.FONT_NAMES) == set(page.FONTS)


# -- NAME… (phase 6 step 8) -----------------------------------------------------

class T:
    """One typed byte (0 between bytes: the keyboard's edge)."""

    def __init__(self, k):
        self.last_key = k

    def pressed(self, n):
        return False


def _type(ws, app, text):
    for ch in text:
        app.handle_input(T(ord(ch) if isinstance(ch, str) else ch))
        app.handle_input(T(0))
    _frame(ws)


@pytest.mark.parametrize("kw", [{}, DESK], ids=["320x240", "desk"])
def test_name_shows_on_custom_only_and_keeps_it_by_keys(tmp_path, kw):
    ws = _ws(tmp_path, **kw)
    app = _open_appearance(ws)
    assert "name" not in app._rows()
    _focus(app, "accent")
    _press(ws, app, "right")
    assert app._rows()[-1] == "name"
    _focus(app, "name")
    app.handle_input(T(13))                     # held from the ENTER that opens
    _press(ws, app, "a")
    assert app.pick == "name" and ws.input.text_mode
    app.handle_input(T(0))
    _type(ws, app, "BubbleGum!")                # capitals and ! are not the grammar
    assert app.entry.text == "ubbleum"
    _type(ws, app, [8] * 7 + list("bubblegum"))
    assert app.entry.text == "bubblegum"
    _type(ws, app, [13])
    assert app.pick is None and not ws.input.text_mode
    assert ws.look.theme_name == "bubblegum"
    assert "name" not in app._rows()
    assert app._themes()[0] == "bubblegum" and "custom" not in app._themes()
    assert (tmp_path / "carts" / "bubblegum.moy" / "theme.json").exists()
    assert not (tmp_path / "carts" / "custom.moy").exists()


@pytest.mark.parametrize("kw", [{}, DESK], ids=["320x240", "desk"])
def test_the_frame_after_name_draws_without_its_row(tmp_path, kw):
    """The ENTER that names custom is the last event before the next draw
    (a phone's keyboard sends no key-up after it): NAME…'s row is gone
    then, and the focus is on a row that exists."""
    ws = _ws(tmp_path, **kw)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "right")
    _focus(app, "name")
    _press(ws, app, "a")
    app.handle_input(T(0))
    _type(ws, app, "nikola")
    app.handle_input(T(13))
    _frame(ws)
    assert ws.look.theme_name == "nikola"
    assert app._rows()[app.row] == "file"


def test_a_theme_set_elsewhere_moves_the_focus_off_name(tmp_path):
    """custom left by another hand (the gen moves under the page) takes
    NAME…'s row with it; the next draw focuses the row above."""
    ws = _ws(tmp_path, **DESK)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "right")
    _focus(app, "name")
    ws.look.set_theme("night")
    _frame(ws)
    assert app._rows()[app.row] == "file"


def test_name_refuses_with_the_reason_and_arms_replace(tmp_path):
    ws = _ws(tmp_path)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "right")
    _focus(app, "name")
    _press(ws, app, "a")
    app.handle_input(T(0))
    _type(ws, app, "night\r")
    assert app.pick == "name" and app.why == "seed"
    _type(ws, app, [8] * 5 + list("mine\r"))
    assert ws.look.theme_name == "mine"
    _focus(app, "tint")
    _press(ws, app, "right")                    # a change on mine: a new custom
    assert ws.look.theme_name == "custom"
    _focus(app, "name")
    _press(ws, app, "a")
    app.handle_input(T(0))
    _type(ws, app, "mine\r")
    assert app.pick == "name" and app.armed == "mine"
    assert ws.look.theme_name == "custom"
    _type(ws, app, "\r")
    assert app.pick is None and ws.look.theme_name == "mine"
    assert ws.look.choices()["tint"] is None    # off custom: the package's own


def test_esc_and_a_tap_outside_back_out_of_name(tmp_path):
    ws = _ws(tmp_path, **DESK)
    app = _open_appearance(ws)
    _focus(app, "accent")
    _press(ws, app, "right")
    _focus(app, "name")
    _press(ws, app, "a")
    app.handle_input(T(0))
    _type(ws, app, "ab\x1b")
    assert app.pick is None and not ws.input.text_mode
    assert ws.look.theme_name == "custom"
    _press(ws, app, "a")
    assert app.pick == "name"
    app.handle_pointer(app.layout.cx + 4, app.layout.strip_y - 4, True)
    _frame(ws)
    assert app.pick is None and not ws.input.text_mode
