"""The Appearance page (docs/look_2026-10.md step 20), drawn with the public
UI API's verbs: the theme cards and the rows, every row changed by keys
alone and by a tap, the wallpapers' page (tabs over cards), NAME's dialog,
the tap floor, the scroll to the focus, a settled page painting nothing, and
the rows' writes."""

import json
from pathlib import Path

import pytest

from runtime import appearance_app as page, host_app, ui_draw
from ws_helpers import select_on_home, use_theme  # noqa: E402


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


class T:
    """One typed byte (0 between bytes: the keyboard's edge)."""

    def __init__(self, k):
        self.last_key = k

    def pressed(self, n):
        return False


def _ws(tmp_path, **kw):
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def _frame(ws, n=2):
    for _ in range(n):
        ws.input.begin_frame()
        ws._dirty = True
        ws.frame(1 / 30)


def _open(ws):
    if select_on_home(ws, "Appearance") is None:
        # Not on the home: Appearance is one of the WM's HIDDEN_APPS.
        assert ws.open_app(ws.appearance_app)
    else:
        ws.open()
    _frame(ws, 3)
    assert ws.wm.top_kind() == "appearance"
    return ws.appearance_app


def _press(ws, app, *keys):
    for k in keys:
        app.handle_input(K(k))
        _frame(ws)


def _type(ws, app, text):
    for ch in text:
        app.handle_input(T(ord(ch) if isinstance(ch, str) else ch))
        _frame(ws, 1)
        app.handle_input(T(0))
        _frame(ws, 1)


def _focus(ws, app, sid):
    """Focus stop `sid` as of the next frame."""
    app._ui.focus(sid)
    _frame(ws)
    assert app._focused == sid


def _tap(ws, app, sid, dx=None):
    """A tap on stop `sid` where it drew, `dx` in from its left (else its middle)."""
    x, y, w, h = app._ui.box(sid)
    app.handle_pointer(x + (w // 2 if dx is None else dx), y + h // 2, True)
    _frame(ws, 3)


def _custom(tmp_path):
    p = tmp_path / "carts" / "custom.moy" / "theme.json"
    return json.loads(p.read_text()) if p.exists() else None


def _accent(name):
    """The change's value of accent or preset `name`."""
    return [a for a in ui_draw.theme_accents() + ui_draw.theme_presets()
            if a[0] == name][0][1]


def test_appearance_cart_is_versioned_system_app():
    folder = ROOT / "system_carts" / "moybyte.appearance.moy"
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert man["version"] >= 1
    assert man["moybyte"]["type"] == "app"
    assert "appearance" in man["moybyte"]["permissions"]
    compile((folder / "main.py").read_text(encoding="utf-8"),
            str(folder / "main.py"), "exec")


def test_the_cards_then_the_rows_on_the_handheld_and_the_desk(tmp_path):
    small = _open(_ws(tmp_path / "s"))
    rows = ["Mode", "Accent", "Tint", "Wallpaper", "Font", "Code font", "Theme file"]
    assert [k for k in small._kinds if not k.startswith("theme ")] == rows
    cards = [k[len("theme "):] for k in small._kinds if k.startswith("theme ")]
    assert cards == small._themes() == ["moy", "blocks", "contrast"]
    wide = _open(_ws(tmp_path / "w", **DESK))
    assert [k for k in wide._kinds if not k.startswith("theme ")] == [
        "Mode", "Accent", "Tint", "Wallpaper", "Window style", "Font", "Code font", "Chrome size",
        "Theme file"]
    sim = _open(_ws(tmp_path / "b", sys_size=(1024, 600), windowed=True))
    assert "Chrome size" in sim._kinds          # the simulator's desk takes two


def test_focus_opens_on_the_theme_in_use(tmp_path):
    ws = _ws(tmp_path)
    app = _open(ws)
    assert app._focused == "theme " + ws.look.theme_name


@pytest.mark.parametrize("kw", [{}, DESK], ids=["320x240", "desk"])
def test_every_row_changes_by_keys_alone(tmp_path, kw):
    ws = _ws(tmp_path, **kw)
    use_theme(ws, "night", "dark", store=True)
    app = _open(ws)
    look = ws.look
    # MODE: not a change to the theme.
    _focus(ws, app, "Mode")
    _press(ws, app, "left")
    assert look.theme_variant == "light" and look.theme_name == "night"
    _press(ws, app, "right")
    assert look.theme_variant == "dark" and _custom(tmp_path) is None
    # ACCENT: the theme's own first; the first change makes custom.
    _focus(ws, app, "Accent")
    _press(ws, app, "right")
    assert look.theme_name == "custom"
    assert look.custom_look()["accent"] == _accent(page.OFFERED[0])
    _press(ws, app, "right")
    assert look.custom_look()["accent"] == _accent(page.OFFERED[1])
    _press(ws, app, "left", "left")
    assert look.custom_look().get("accent") is None        # the source's own
    # TINT, WINDOW STYLE, FONT.
    _focus(ws, app, "Tint")
    _press(ws, app, "right")
    assert look.custom_look()["tint"] == ui_draw.theme_tints()[0][0]
    if app.layout.desk:
        _focus(ws, app, "Window style")
        _press(ws, app, "right")
        assert app.choices()["frame"] == "tab"
    _focus(ws, app, "Font")
    _press(ws, app, "right")
    assert app.choices()["font"] == "petme128" and ui_draw.font("ui")[0] == 0
    look.commit()
    pkg = _custom(tmp_path)
    assert pkg["fonts"]["ui"] == "petme128"
    if app.layout.desk:
        assert pkg["frame"] == {"style": "tab"}
    assert not (tmp_path / "carts" / "custom.moy" / "petme128").exists()   # reserved
    _press(ws, app, "left")
    assert app.choices()["font"] == "tiny5" and ui_draw.font("ui")[0] != 0
    # THEME: the arrows move between the cards, Enter uses one.
    names = app._themes()
    _focus(ws, app, "theme " + names[1])
    _press(ws, app, "right", "a")
    assert look.theme_name == names[2]


def test_code_font_sets_the_code_tabs_face_and_font_keeps_it(tmp_path):
    """CODE FONT (docs/look_2026-10.md step 23): the `mono` role's preset,
    JetBrains Mono where the image carries the font set; FONT changes the
    `ui` and `title` faces and keeps it."""
    ws = _ws(tmp_path)
    use_theme(ws, "night", "dark", store=True)
    app = _open(ws)
    look = ws.look
    assert app.choices()["code_fonts"] == ("petme128", "jbmono")
    assert app.choices()["code_font"] == "petme128"           # night's: petme128
    _focus(ws, app, "Code font")
    _press(ws, app, "right")
    assert app.choices()["code_font"] == "jbmono"
    assert ui_draw.font_info(ui_draw.font("mono")[0])[0] == 7  # the 7 px cell
    _focus(ws, app, "Font")
    _press(ws, app, "right")
    assert app.choices()["font"] == "petme128" and app.choices()["code_font"] == "jbmono"
    look.commit()
    pkg = _custom(tmp_path)
    assert pkg["fonts"] == {"ui": "petme128", "title": "petme128",
                            "mono": "jbmono-11-400-2.fnt"}
    assert (tmp_path / "carts" / "custom.moy" / "jbmono-11-400-2.fnt").exists()
    _focus(ws, app, "Code font")
    _press(ws, app, "left")
    assert app.choices()["code_font"] == "petme128" and ui_draw.font("mono")[0] == 0


def test_code_font_shows_only_where_the_image_draws_a_choice(tmp_path, monkeypatch):
    """An image whose `code_fonts` answers PetMe128 alone has one code font,
    and the row is not shown."""
    ws = _ws(tmp_path)
    monkeypatch.setattr(ws.look, "code_fonts", lambda: ("petme128",))
    app = _open(ws)
    _frame(ws)
    assert app.choices()["code_fonts"] == ("petme128",)
    assert "Code font" not in app._kinds and "Font" in app._kinds


def test_the_moy_seed_names_jetbrains_mono_for_the_code_tab(tmp_path):
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False)
    _frame(ws)
    assert dict(ui_draw.theme_fonts("moy"))["mono"] == "jbmono-11-400-2.fnt"
    assert ws.look.choices()["code_font"] == "jbmono"
    assert ui_draw.font_info(ui_draw.font("mono")[0])[:2] == (7, 14)


def _faces():
    """{role: the font file it draws in, None for petme128}, `hero` too."""
    from runtime import appearance
    files = {fid: f for (_pkg, f), fid in appearance._FONTS.items()}
    return {r: files.get(ui_draw.font(r)[0])
            for r in ("ui", "title", "caption", "mono", "hero")}


@pytest.mark.parametrize("kw, faces", [
    ({}, {"ui": "inter-12-500-2.fnt", "title": "inter-14-600-4.fnt",
          "caption": "inter-11-600-2.fnt", "hero": "inter-19-700-4.fnt"}),
    (DESK, {"ui": "inter-15-500-4.fnt", "title": "inter-15-600-4.fnt",
            "caption": "inter-12-600-4.fnt", "hero": "inter-15-600-4.fnt"})])
def test_moy_draws_inter_by_night_and_day_sans_by_day(tmp_path, kw, faces):
    """Moy's look sets `fam` per mode (docs/look_2026-10.md steps 26a and
    27): Night's type roles take Inter and Day's Day Sans (IBM Plex Sans,
    renamed), each at the metric class's sizes (fontset.ROLES; the desk
    home's title is the `title` face), and mono stays JetBrains Mono."""
    ws = _ws(tmp_path, **kw)
    ws.look.set_theme("moy", persist=False, variant="dark")
    _frame(ws)
    assert _faces() == dict(faces, mono="jbmono-11-400-2.fnt")
    assert ws.layout.line_h == ui_draw.font()[2] > 8
    ws.look.set_theme_variant("light", persist=False)
    _frame(ws)
    day = {r: f.replace("inter-", "daysans-") for r, f in faces.items()}
    assert _faces() == dict(day, mono="jbmono-11-400-2.fnt")
    assert ws.layout.line_h == ui_draw.font()[2] > 8


@pytest.mark.parametrize("kw, faces, calc", [
    ({}, {"ui": "atkinson-12-500-2.fnt", "title": "atkinson-14-700-4.fnt",
          "caption": "atkinson-11-700-2.fnt", "hero": "atkinson-19-800-4.fnt"},
     ("atkinson-16-500-4.fnt", "atkinson-17-500-4.fnt", "atkinson-28-700-4.fnt")),
    (DESK, {"ui": "atkinson-15-500-4.fnt", "title": "atkinson-15-700-4.fnt",
            "caption": "atkinson-12-700-4.fnt", "hero": "atkinson-15-700-4.fnt"},
     ("atkinson-22-500-4.fnt", "atkinson-24-500-4.fnt", "atkinson-44-700-4.fnt"))])
@pytest.mark.parametrize("variant", ["light", "dark"])
def test_blocks_draws_atkinson_at_its_own_weights(tmp_path, kw, faces, calc, variant):
    """Blocks' look sets `fam` and moves the weight steps (docs/look_2026-10.md
    step 26b: `w_semi` 700, `w_bold` 800), so its semibold roles take
    Atkinson Bold and the hero ExtraBold, in its one mode whichever the
    Mode row holds; Calc's keys and result take its Calc faces."""
    from runtime import appearance
    ws = _ws(tmp_path, **kw)
    ws.look.set_theme("blocks", persist=False, variant=variant)
    _frame(ws)
    assert _faces() == dict(faces, mono="jbmono-11-400-2.fnt")
    files = {fid: f for (_pkg, f), fid in appearance._FONTS.items()}
    assert tuple(files.get(ui_draw.font(r)[0]) for r in ("key", "op", "result")) == calc


@pytest.mark.parametrize("kw, faces, calc", [
    ({}, {"ui": "atkinson-12-600-2.fnt", "title": "atkinson-15-700-4.fnt",
          "caption": "atkinson-11-700-2.fnt", "hero": "atkinson-19-800-4.fnt"},
     ("atkinson-18-600-4.fnt", "atkinson-19-600-4.fnt", "atkinson-30-700-4.fnt")),
    (DESK, {"ui": "atkinson-15-600-4.fnt", "title": "atkinson-17-700-4.fnt",
            "caption": "atkinson-12-700-4.fnt", "hero": "atkinson-17-700-4.fnt"},
     ("atkinson-26-600-4.fnt", "atkinson-28-600-4.fnt", "atkinson-50-700-4.fnt"))])
@pytest.mark.parametrize("variant", ["light", "dark"])
def test_contrast_draws_atkinson_larger_and_heavier(tmp_path, kw, faces, calc, variant):
    """Contrast's look moves every weight step a step up (docs/look_2026-10.md
    step 26c: `w_med` 600, `w_semi` 700, `w_bold` 800) and sizes its titles
    and Calc's faces (`sizes.hh`, `sizes.dk`), in either mode."""
    from runtime import appearance
    ws = _ws(tmp_path, **kw)
    ws.look.set_theme("contrast", persist=False, variant=variant)
    _frame(ws)
    assert _faces() == dict(faces, mono="jbmono-11-400-2.fnt")
    files = {fid: f for (_pkg, f), fid in appearance._FONTS.items()}
    assert tuple(files.get(ui_draw.font(r)[0]) for r in ("key", "op", "result")) == calc


def test_a_font_preset_on_moy_night_overrides_its_family(tmp_path):
    """A custom made from Moy Night keeps its family; FONT on it names the
    package's own `ui`, so the family yields every type role to the chain."""
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="dark")
    app = _open(ws)
    assert app.choices()["font"] is None                   # the theme's own: Inter
    _focus(ws, app, "Accent")
    _press(ws, app, "right")
    ws.look.commit()
    _frame(ws)
    assert ws.look.theme_name == "custom" and _faces()["ui"] == "inter-12-500-2.fnt"
    _focus(ws, app, "Font")
    _press(ws, app, "right")
    assert app.choices()["font"] in ("tiny5", "petme128")
    got = _faces()
    assert not any(f and f.startswith("inter-") for f in got.values()), got


def test_the_wallpapers_page_is_tabs_over_cards_and_b_comes_back(tmp_path):
    ws = _ws(tmp_path)
    use_theme(ws, "night", "dark", store=True)
    app = _open(ws)
    look = ws.look
    _focus(ws, app, "Wallpaper")
    _press(ws, app, "a")
    assert app.view == "walls" and app.tab == 1            # night's is a cart
    on = app._focused
    assert on.startswith("Wallpaper ") and app._kinds[on] == "card"
    _press(ws, app, "b")
    assert app.view == "rows" and app._focused == "Wallpaper"
    assert look.theme_name == "night", "b changed nothing"
    _press(ws, app, "a")
    _focus(ws, app, "Wallpapers")
    _press(ws, app, "left")                                 # the tabs: Pictures
    assert app.tab == 0
    _focus(ws, app, "Wallpaper Backdrop")
    _press(ws, app, "a")
    assert app.view == "rows" and look.wallpaper_id is None
    assert app._focused == "Wallpaper" and look.theme_name == "custom"


@pytest.mark.parametrize("kw", [{}, DESK], ids=["320x240", "desk"])
def test_back_is_hinted_where_it_goes_back(tmp_path, monkeypatch, kw):
    """The navigation rule (owner, 2026-10-18): a window's root hints no
    back, its title strip's X closes it; a page in it hints Back. The
    handheld's root hints Back, the bar's chevron that goes home."""
    ws = _ws(tmp_path, **kw)
    app = _open(ws)
    hinted = []
    real = page.ui.hints
    monkeypatch.setattr(page.ui, "hints",
                        lambda items, o=None: (hinted.append(tuple(items)),
                                               real(items, o))[1])
    _frame(ws)
    root = hinted[-1]
    if kw:
        assert "back" not in root and "Close" not in root
    else:
        assert root[-2:] == ("back", "Back")
    _focus(ws, app, "Wallpaper")
    _press(ws, app, "a")
    assert app.view == "walls"
    assert hinted[-1][-2:] == ("back", "Back")


@pytest.mark.parametrize("kw", [{}, DESK], ids=["320x240", "desk"])
def test_every_row_answers_a_tap(tmp_path, kw):
    ws = _ws(tmp_path, **kw)
    use_theme(ws, "night", "dark", store=True)
    app = _open(ws)
    look = ws.look
    _tap(ws, app, "Mode", dx=app._ui.box("Mode")[2] - 60)   # the Light option
    assert look.theme_variant == "light"
    _tap(ws, app, "Accent", dx=app._ui.box("Accent")[2] - 12)  # the last dot
    assert look.custom_look()["accent"] == _accent(page.OFFERED[-1])
    _tap(ws, app, "Tint")
    assert look.custom_look()["tint"] == ui_draw.theme_tints()[0][0]
    _tap(ws, app, "Font")
    assert app.choices()["font"] == "petme128"
    _tap(ws, app, "Wallpaper")
    assert app.view == "walls"
    _tap(ws, app, "Wallpapers", dx=24)                      # the Pictures tab
    assert app.tab == 0
    _tap(ws, app, "Wallpaper Backdrop")
    assert app.view == "rows" and look.wallpaper_id is None
    app._ui.focus("theme blocks")                           # scrolled into view
    _frame(ws)
    _tap(ws, app, "theme blocks")
    assert look.theme_name == "blocks"
    app._ui.focus("Theme file")
    _frame(ws)
    _tap(ws, app, "Theme file")
    assert look._editing is not None            # the text app holds the file


def test_the_tap_floor_holds_at_320x240(tmp_path):
    """#203: every stop a finger takes is at least the floor's 16 logical
    pixels at the chrome scale."""
    ws = _ws(tmp_path)
    app = _open(ws)
    floor = 16 * app.layout.cs
    for sid in app._kinds:
        box = app._ui.box(sid)
        if box is not None:
            assert box[3] >= floor, (sid, box)


def test_rows_that_do_not_fit_scroll_to_the_focus(tmp_path):
    ws = _ws(tmp_path, sys_size=(800, 480), font_scale=3)
    app = _open(ws)
    _focus(ws, app, "Theme file")
    _frame(ws)
    x, y, w, h = app._ui.box("Theme file")
    bx, by, bw, bh = app.layout.body
    assert by <= y and y + h <= by + bh


def test_chrome_size_steps_live_and_persists(tmp_path):
    ws = _ws(tmp_path, sys_size=(1024, 600), windowed=True)
    app = _open(ws)
    assert ws.look.chrome_scale == 0 and ws.layout.cs == 1
    _focus(ws, app, "Chrome size")
    _press(ws, app, "right")
    assert ws.layout.cs == 2 and ws.system.get("chrome_scale") == 2
    assert ws.look.theme_name == "moy"          # not a change to the theme
    again = _ws(tmp_path, sys_size=(1024, 600), windowed=True)
    assert again.look.chrome_scale == 2 and again.layout.cs == 2


def test_the_page_is_idle_once_drawn(tmp_path):
    ws = _ws(tmp_path)
    app = _open(ws)
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
    app = _open(ws)
    _focus(ws, app, "Accent")
    _press(ws, app, "right")
    assert _custom(tmp_path) is None            # the debounce has not run
    ws.wm.close_window_kind("appearance")
    assert _custom(tmp_path) is not None


# -- the theme's file: the editor beside the rows ------------------------------------

def _edit(ws, app):
    _focus(ws, app, "Theme file")
    _press(ws, app, "a")
    look = ws.look
    assert look._editing is not None
    h = look.file_watch(*look._editing)
    assert h is look
    return look


def _handle(ws):
    """The text app's editor handle on custom's theme.json."""
    hs = [h for h in ws.player._editors if h._watch is ws.look]
    assert hs, "the theme's file opened no watched editor"
    return hs[-1]


def test_an_editor_save_writes_no_journal_into_the_package(tmp_path):
    """A theme package stays flat data: the editor's saves land through the
    look (`file_write`), never the project-file save and its journal."""
    ws = _ws(tmp_path)
    app = _open(ws)
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
                          "tiny5.fnt", "tiny5.txt",
                          "jbmono-11-400-2.fnt", "jbmono-11-400-2.txt"}, names


def test_a_clean_buffer_follows_a_change_and_an_edited_one_keeps_its_text(tmp_path):
    """The rule for a change made beside an open theme file: a buffer with no
    edits reloads (the look moved, `file_gen`); one with edits keeps them,
    and its save is the last write."""
    ws = _ws(tmp_path)
    app = _open(ws)
    look = _edit(ws, app)
    _frame(ws)
    h = _handle(ws)
    start = h.text()
    app = _open(ws)
    _focus(ws, app, "Accent")
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
    from runtime.appearance import Appearance
    assert page.FRAMES == Appearance.FRAMES and page.FONTS == Appearance.FONTS
    assert page.PICTURE == Appearance.PICTURE and page.THEIRS == Appearance.THEIRS
    assert len(page.FRAME_NAMES) == len(page.FRAMES)
    assert len(page.FONT_NAMES) == len(page.FONTS)
    names = [a[0] for a in ui_draw.theme_accents() + ui_draw.theme_presets()]
    assert set(page.OFFERED) <= set(names) and len(set(page.OFFERED)) == 7


def test_the_accent_row_is_eight_distinct_swatches(tmp_path):
    """The theme's own and seven (owner, 2026-10-18), none the twin of
    another in either mode: no two swatches within 48 in RGB (the closest
    pair, Night's own lavender and slate's grey, is about 53 apart; the
    `indigo` preset dropped was 26 from Day's own)."""
    for variant in ("light", "dark"):
        (tmp_path / variant).mkdir()
        ws = _ws(tmp_path / variant)
        ws.look.set_theme("moy", persist=False, variant=variant)
        app = _open(ws)
        side = 3 if variant == "light" else 2
        own = page._rgb(app._own_accent(app.choices()))
        cols = [own] + [page._rgb(a[side]) for a in app._accents]
        assert len(cols) == 8
        rgb = [((c >> 16) & 255, (c >> 8) & 255, c & 255) for c in cols]
        for i in range(8):
            for j in range(i):
                d = sum((p - q) ** 2 for p, q in zip(rgb[i], rgb[j])) ** 0.5
                assert d > 48, (variant, i, j, cols[i], cols[j])


def test_a_settled_page_shows_the_wallpaper_picture_of_a_store_theme(tmp_path):
    """A theme shipping its own picture shows it as the theme's picture,
    never My art, on the Pictures tab."""
    ws = _ws(tmp_path)
    look = ws.look
    raw = bytearray(512 * 300)
    assert ws.artwork.save(raw, 512, 300) and ws.artwork.set_wallpaper()
    look.tick(0.0)
    look.commit()
    src = json.loads((tmp_path / "carts" / "custom.moy" / "theme.json").read_text())
    assert src["images"] == {"desk": "desk.moyimg"}
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
    app = _open(ws)
    assert app.choices()["picture"] == "theme"
    assert app._wall_kind(app.choices()) == (0, "Theme's picture")
    assert [w[2] for w in app._walls(0)][:2] == ["backdrop", "theirs"]


# -- NAME ------------------------------------------------------------------------

def _to_custom(ws, app):
    _focus(ws, app, "Accent")
    _press(ws, app, "right")
    assert ws.look.theme_name == "custom"


def _name(ws, app):
    _focus(ws, app, "Name")
    _press(ws, app, "a")
    assert app.naming and ws.input.text_mode
    assert app._focused == "Theme name"


@pytest.mark.parametrize("kw", [{}, DESK], ids=["320x240", "desk"])
def test_name_shows_on_custom_only_and_keeps_it_by_keys(tmp_path, kw):
    ws = _ws(tmp_path, **kw)
    app = _open(ws)
    assert "Name" not in app._kinds
    _to_custom(ws, app)
    _frame(ws)
    assert list(app._kinds)[-1] == "Name"
    _name(ws, app)
    _type(ws, app, "BubbleGum!")                # capitals and ! are not the grammar
    assert app.name_text == "ubbleum"
    _type(ws, app, [8] * 7 + list("bubblegum"))
    assert app.name_text == "bubblegum"
    _type(ws, app, [13])
    assert not app.naming and not ws.input.text_mode
    assert ws.look.theme_name == "bubblegum"
    _frame(ws)
    assert "Name" not in app._kinds and app._focused == "theme bubblegum"
    assert app._themes()[0] == "bubblegum" and "custom" not in app._themes()
    assert (tmp_path / "carts" / "bubblegum.moy" / "theme.json").exists()
    assert not (tmp_path / "carts" / "custom.moy").exists()


def test_a_theme_set_elsewhere_closes_name(tmp_path):
    """custom left by another hand (the gen moves under the page) takes
    NAME with it."""
    ws = _ws(tmp_path, **DESK)
    app = _open(ws)
    _to_custom(ws, app)
    _name(ws, app)
    ws.look.set_theme("moy")
    _frame(ws)
    assert not app.naming and not ws.input.text_mode
    assert "Name" not in app._kinds


def test_name_refuses_with_the_reason_and_arms_replace(tmp_path):
    ws = _ws(tmp_path)
    app = _open(ws)
    _to_custom(ws, app)
    _name(ws, app)
    _type(ws, app, "moy\r")
    assert app.naming and app.why == "seed"
    _type(ws, app, [8] * 5 + list("mine\r"))
    assert ws.look.theme_name == "mine"
    _focus(ws, app, "Tint")
    _press(ws, app, "right")                    # a change on mine: a new custom
    assert ws.look.theme_name == "custom"
    _name(ws, app)
    _type(ws, app, "mine\r")
    assert app.naming and app.armed == "mine"
    assert ws.look.theme_name == "custom"
    _type(ws, app, "\r")
    assert not app.naming and ws.look.theme_name == "mine"
    assert ws.look.choices()["tint"] is None    # off custom: the package's own


def test_b_and_cancel_back_out_of_name(tmp_path):
    ws = _ws(tmp_path, **DESK)
    app = _open(ws)
    _to_custom(ws, app)
    _name(ws, app)
    _type(ws, app, "ab")
    _press(ws, app, "b")
    assert not app.naming and not ws.input.text_mode
    assert ws.look.theme_name == "custom"
    _name(ws, app)
    _press(ws, app, "down", "a")                 # the field, then its first button
    assert not app.naming and not ws.input.text_mode


def test_a_single_mode_theme_offers_no_mode(tmp_path):
    """Blocks' dark copies its light: its card names one mode and its page
    shows no Mode row; Moy and Contrast keep both."""
    ws = _ws(tmp_path)
    ws.look.set_theme("blocks", persist=False, variant="light")
    app = _open(ws)
    assert app._modes("blocks") == ("Light",)
    assert app._modes("moy") == ("Day", "Night")
    assert app._modes("contrast") == ("Light", "Dark")
    assert "Mode" not in app._kinds and "Accent" in app._kinds
    assert app._status(app.choices()).startswith("Blocks Light")
