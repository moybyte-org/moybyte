"""The home's card verbs are ONE list (`LauncherHomeLayer.card_verbs`) that
every input reaches (design review R3-01, R3-16, R3-18, R3-19, R3-28):

* a keyboard types each verb's letter, and its strip names them;
* touch long-presses a card (the phone home screens' idiom) and a gamepad
  presses X, and both open the card's verbs as a menu;
* every input reaches the system menu, whose first group over the home is
  the selected card's.

Driven on the shell the goldens build, through the real pointer feed."""

import pytest

import test_shell_goldens as g
from runtime import launcher_layer, moy_input, ui_draw
from ws_helpers import make_drv

HANDHELD = "tdeck_320x240_fs1_dark"
CONSOLE = "p4_1024x600_cs2_fs1_console"


@pytest.fixture(autouse=True)
def _clean():
    yield
    import moy_ui
    moy_ui.theme_uninstall()
    ui_draw.input_kind("keys")


def _ws(tmp_path, config=HANDHELD):
    import moy_ui
    moy_ui.theme_uninstall()
    cfg = dict(g.CONFIGS[config], variant="dark", theme="moy")
    ws = g._build(cfg, tmp_path / "carts")
    ws.go_home()
    if getattr(ws.wm, "mode", None) is not None:
        ws.open_library()
    return ws


def _frames(drv, n=2):
    for _ in range(n):
        drv.frame(1 / 30)


def _select(ws, tab, which):
    """Select on `tab` the "+ New" card (`which` "new"), a real cart, or a
    cart of the kid's own ("own"; on Mine always: a copy of a game, as a
    fresh store holds none of its own)."""
    from runtime.moy_store_base import builtin_name
    if tab == "mine" or which == "own":
        _select(ws, "games", "cart")
        ws.carts.dup(ws.launcher.selected())
    ws.set_home_tab(tab)
    items = ws.launcher.items
    for i, it in enumerate(items):
        if which == "own" and not (it.get("path") and builtin_name(it["path"]) is None):
            continue
        if (it.get("type") == "new") == (which == "new") and (which == "new" or it.get("path")):
            ws.launcher.sel = i
            return it
    pytest.skip("no %s card on %s" % (which, tab))


def _menu_verbs(ws):
    """The verbs of the open menu's rows: a card row's, a favourite's."""
    home = ws.launcher_layer
    out = set()
    for it in ws.sysmenu.items:
        if it[0] != "item":
            continue
        act = it[2]
        if getattr(act, "verb", None) is not None:
            out.add(act.verb)
        elif act == home.toggle_favourite:
            out.add("favourite")
    return out


def _card_centre(ws):
    gr = ws.launcher
    r = gr.tile_rect(gr.sel)
    return r[0] + r[2] // 2, r[1] + r[3] // 2


def _long_press(ws, drv, at=None, frames=20):
    x, y = at or _card_centre(ws)
    drv.touch(x, y)
    _frames(drv, frames)
    drv.touch_up()
    _frames(drv, 1)


class _Pad:
    """A frame's input with one gamepad button pressed (the boards'
    fifteen names), asserting the home asks for no name the input
    vocabulary lacks."""

    def __init__(self, name):
        self.name = name
        self.asked = set()

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, name):
        self.asked.add(name)
        return name == self.name


def _reached(ws, drv, kind, monkeypatch):
    """The verbs input `kind` reaches on the selected card: the confirm's,
    the strip's letters (typed), the card menu its gesture opens, and the
    system menu's card group."""
    home = ws.launcher_layer
    zone = home.zone_verbs()
    got = set()
    tap = home._confirm_verb(zone)
    if tap:
        got.add(tap)                          # a tap, A or enter
    ui_draw.input_kind(kind)
    if kind in launcher_layer._LETTER_INPUTS:
        done = []
        monkeypatch.setattr(home, "_do_verb", lambda v: done.append(v))
        monkeypatch.setattr(home, "toggle_favourite", lambda: done.append("favourite") or True)
        for ch in "cnyx/f":
            drv.type_char(ord(ch))
            _frames(drv, 1)
            drv.frame(1 / 30)
        monkeypatch.undo()
        got.update(done)
    elif kind == "pad":
        pad = _Pad("x")
        home.handle_input(pad)
        assert pad.asked <= set(moy_input.NAMES), \
            "the home asks for a button no input has: %s" % (pad.asked - set(moy_input.NAMES))
        got |= _menu_verbs(ws) if ws.sysmenu.open else set()
        ws.sysmenu.close()
    else:
        _long_press(ws, drv)
        got |= _menu_verbs(ws) if ws.sysmenu.open else set()
        ws.sysmenu.close()
    ui_draw.input_kind(kind)
    ws.toggle_sysmenu()                       # every input opens the system menu
    got |= _menu_verbs(ws)
    if any(it[0] == "item" and it[4] == "Search" for it in ws.sysmenu.items):
        got.add("search")
    ws.sysmenu.close()
    return got


@pytest.mark.parametrize("config", [HANDHELD, CONSOLE])
@pytest.mark.parametrize("kind", ui_draw.INPUTS)
@pytest.mark.parametrize("tab,which", [("games", "cart"), ("mine", "cart"), ("apps", "cart"),
                                       ("make", "cart"), ("make", "own"), ("make", "new")])
def test_every_card_verb_is_reachable_under_every_input(tmp_path, monkeypatch, config, kind,
                                                        tab, which):
    ws = _ws(tmp_path, config)
    drv = make_drv(ws)
    _frames(drv)
    _select(ws, tab, which)
    _frames(drv)
    home = ws.launcher_layer
    want = set(home.zone_verbs())
    want |= set(home.card_verbs())
    if which in ("cart", "own"):
        want.discard("new")                   # the "+ New" card's tap (make/new)
    got = _reached(ws, drv, kind, monkeypatch)
    assert want <= got, "%s %s/%s under %s: no route to %s" % (
        config, tab, which, kind, sorted(want - got))


@pytest.mark.parametrize("config", [HANDHELD, CONSOLE])
def test_a_long_press_opens_the_cards_verbs_and_its_release_is_no_tap(tmp_path, config):
    ws = _ws(tmp_path, config)
    drv = make_drv(ws)
    _frames(drv)
    it = _select(ws, "make", "own")
    ui_draw.input_kind("touch")
    _frames(drv)
    _long_press(ws, drv)
    assert ws.screen == "launcher", "the release launched the card"
    assert ws.sysmenu.open and ws.sysmenu.widget == "card"
    items = ws.sysmenu.items
    assert items[0][0] == "header" and items[0][4] == it.get("title")
    assert [r[4] for r in items if r[0] == "item"] == [
        "Add favourite", "Change", "Duplicate", "Delete"]
    # a row acts on the card: Copy makes one more cart
    n = len(ws.carts.all)
    row = next(i for i, r in enumerate(items) if r[0] == "item" and r[4] == "Duplicate")
    ws.sysmenu.sel = row
    ws.sysmenu.activate()
    assert len(ws.carts.all) == n + 1


def test_a_short_press_or_a_drag_opens_no_menu(tmp_path):
    ws = _ws(tmp_path)
    drv = make_drv(ws)
    _frames(drv)
    _select(ws, "games", "cart")
    ui_draw.input_kind("touch")
    _frames(drv)
    held = launcher_layer.LONG_PRESS_MS * 30 // 1000
    _long_press(ws, drv, frames=held - 3)     # released before the hold
    assert not (ws.sysmenu.open and ws.sysmenu.widget == "card")
    ws.go_home()
    _frames(drv)
    x, y = _card_centre(ws)
    drv.touch(x, y)
    _frames(drv, 1)
    drv.touch_drag(x + 30, y)                 # a drag is a scroll, never a hold
    _frames(drv, held + 5)
    drv.touch_up()
    _frames(drv, 1)
    assert not ws.sysmenu.open


def test_the_system_menu_opens_on_the_selected_cards_group(tmp_path):
    """Over the home, the card's group stands first, where an open cart's
    does (R3-18), and names its verbs."""
    ws = _ws(tmp_path)
    it = _select(ws, "games", "cart")
    ws.toggle_sysmenu()
    items = ws.sysmenu.items
    assert items[0][:2] == ("header", it["title"].upper())
    labels = [r[4] for r in items if r[0] in ("header", "item")]
    assert labels.index("System") > labels.index("Duplicate")



def test_the_projects_windows_card_menu_is_its_own_cards(tmp_path):
    """The desk's projects window holds its own card model: a gamepad's X
    there opens the window's card's verbs, not the fullscreen home's."""
    from ws_helpers import build_desktop_ws
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    ws.open_projects()
    _frames(drv)
    g = ws.projects_grid
    real = [i for i, it in enumerate(g.items) if it.get("path")]
    g.sel = real[-1]
    ws.launcher.sel = 0
    ws.projects_layer.handle_input(_Pad("x"))
    assert ws.sysmenu.open
    assert ws.sysmenu.items[0][4] == g.items[real[-1]]["title"]


@pytest.mark.parametrize("kind", ["keys", "tdeck"])
def test_the_star_word_follows_its_state(tmp_path, kind):
    """A keyboard's strip names what F does to this card (R3-19)."""
    ws = _ws(tmp_path, CONSOLE)
    it = _select(ws, "games", "cart")
    home = ws.launcher_layer
    ui_draw.input_kind(kind)
    items, verbs = home._hint_groups()
    assert items[verbs.index("favourite") * 2 + 1] == "Add favourite"
    ws.carts.toggle_favorite(it)
    items, verbs = home._hint_groups()
    assert items[verbs.index("favourite") * 2 + 1] == "Remove favourite"


def test_y_is_copy_and_nothing_else(tmp_path):
    """Y is Copy on a keyboard, and a gamepad's Y names nothing on the
    home (R3-19): the pad reaches the star through X's menu. A letter
    names one verb, and none is a T-Deck game key (moy_kbd.c's w a s d l
    k)."""
    ws = _ws(tmp_path)
    it = _select(ws, "games", "cart")
    home = ws.launcher_layer
    ui_draw.input_kind("pad")
    items, _verbs = home._hint_groups()
    assert "Y" not in items[::2]
    home.handle_input(_Pad("y"))
    assert not ws.carts.is_favorite(it)
    assert launcher_layer._VERB_KEY["y"] == "copy"
    caps = {}
    for v, (k, _w) in launcher_layer._VERB_HINT.items():
        if k in ("confirm", "X"):
            continue                          # the tap's; Delete's and a pad's Options
        assert caps.setdefault(k, v) == v, "%s names %s and %s" % (k, caps[k], v)
        assert k.lower() not in "wasdlk"


def test_the_desk_strip_names_its_buttons_verbs(tmp_path):
    """The desk home's strip names every button's verb, then Search and
    the star (R3-28)."""
    ws = _ws(tmp_path, CONSOLE)
    _select(ws, "games", "cart")
    home = ws.launcher_layer
    ui_draw.input_kind("keys")
    _items, verbs = home._hint_groups()
    buttons = [v for v, _r, _k in home._desk_buttons()]
    assert verbs[:len(buttons)] == buttons
    assert verbs[len(buttons):] == ["search", "favourite"]


def test_the_desk_says_when_last_played_once(tmp_path, monkeypatch):
    """The desk's details pane names the last play in its facts, so its
    metadata line does not say it again (R3-28)."""
    ws = _ws(tmp_path, CONSOLE)
    it = _select(ws, "games", "cart")
    y, m, d = g._DATE
    ws.system.set("plays", {it["path"]: [12, y * 10000 + m * 100 + d]})
    said = []
    for kind in {type(ws.sys_canvas), type(ws.glass_canvas)}:
        real = kind.print_scaled

        def print_scaled(cv, s, *a, _real=real, **kw):
            said.append(s)
            return _real(cv, s, *a, **kw)

        monkeypatch.setattr(kind, "print_scaled", print_scaled)
    ws.launcher_layer._lib_unsupported = True
    for _ in range(2):
        g._quiesce(ws)
        ws._dirty = True
        ws.frame(g._DT)
    assert "Last" in said and "Today" in said
    assert not any(s.startswith("Played ") for s in said), said


def test_the_star_rows_wear_the_icon_sets_star(tmp_path):
    """The card group's favourite row wears the icon sets' star, hollow to
    add and filled to remove, never New's plus or Delete's trash (R3-13);
    every icon a card row names is a slot every set draws."""
    from runtime import iconsets
    ws = _ws(tmp_path)
    it = _select(ws, "games", "cart")
    for icon in ("star", "star_on"):
        ws.toggle_sysmenu()
        rows = [r for r in ws.sysmenu.items if r[0] == "item"]
        assert rows[0][3] == icon, "the star row wears %r" % rows[0][3]
        for r in rows:
            assert r[3] in iconsets.SLOT, "%s's icon %r is no slot" % (r[4], r[3])
        ws.sysmenu.close()
        ws.carts.toggle_favorite(it)


def _marks(ws):
    """The focus marks one full home frame draws."""
    ui_draw.marks(None, True)
    g._quiesce(ws)
    ws._dirty = True
    ws.frame(g._DT)
    return ui_draw.marks(None, False)[1]


def test_up_from_the_desk_shelf_reaches_its_buttons(tmp_path):
    """On the desk home the keys and a gamepad reach the details pane's
    buttons (R3-16): up from the shelf puts the focus mark on the first,
    left and right move along them, confirm presses one; up again goes on
    to the tabs, down comes back to the shelf. One focus mark a screen."""
    ws = _ws(tmp_path, CONSOLE)
    ws.launcher_layer._lib_unsupported = True
    _select(ws, "games", "cart")
    home = ws.launcher_layer
    verbs = [v for v, _r, _k in home._desk_buttons()]
    assert verbs[:2] == ["play", "change"]
    assert _marks(ws) == 1
    home.handle_input(_Pad("up"))
    assert home._button_focus == 0 and not home._tab_focus
    assert _marks(ws) == 1, "the button's mark, the card's gone"
    home.handle_input(_Pad("right"))
    assert home._button_focus == 1
    for kind in ("pad", "keys"):
        ui_draw.input_kind(kind)
        items, _verbs = home._hint_groups()
        assert items[:2] == ["confirm", "Change"], "%s: confirm names the button" % kind
    home.handle_input(_Pad("up"))
    assert home._tab_focus and home._button_focus is None
    home.handle_input(_Pad("down"))
    home.handle_input(_Pad("up"))
    home.handle_input(_Pad("right"))
    home.handle_input(_Pad("down"))
    assert home._button_focus is None and not home._tab_focus
    assert _marks(ws) == 1, "the card's mark back"
    home.handle_input(_Pad("up"))
    home.handle_input(_Pad("right"))
    done = []
    home._do_verb = done.append
    home.handle_input(_Pad("a"))
    assert done == ["change"]

@pytest.mark.parametrize("config", [HANDHELD, CONSOLE])
@pytest.mark.parametrize("kind", ui_draw.INPUTS)
def test_make_offers_no_delete_on_a_built_in(tmp_path, monkeypatch, config, kind):
    """A built-in is copied, never deleted: Make's verbs on a `moybyte.*`
    card hold no Delete under any input, the open-cart menu's own test
    (`builtin_name`), and a card of the kid's own keeps it (R4-14)."""
    from runtime.moy_store_base import builtin_name
    ws = _ws(tmp_path, config)
    drv = make_drv(ws)
    _frames(drv)
    ws.set_home_tab("make")
    home = ws.launcher_layer
    built = [i for i, it in enumerate(ws.launcher.items)
             if it.get("path") and builtin_name(it["path"]) is not None]
    assert built, "no built-in on Make"
    ws.launcher.sel = built[0]
    _frames(drv)
    assert "delete" not in home.zone_verbs() and "delete" not in home.card_verbs()
    assert "copy" in home.zone_verbs()
    assert "delete" not in _reached(ws, drv, kind, monkeypatch)
    ws.carts.dup(ws.launcher.items[built[0]])
    ws.set_home_tab("make")
    own = next(i for i, it in enumerate(ws.launcher.items)
               if it.get("path") and builtin_name(it["path"]) is None)
    ws.launcher.sel = own
    assert "delete" in home.zone_verbs()


@pytest.mark.parametrize("kind", ["keys", "tdeck", "pad"])
def test_the_make_strip_keeps_its_tab_keys(tmp_path, kind):
    """On a project of the kid's own every verb wants the strip: where it
    runs short Duplicate goes (X and the card's menu keep it), never the
    tab keys (R4-22)."""
    from runtime.moy_store_base import builtin_name
    ws = _ws(tmp_path)
    drv = make_drv(ws)
    _frames(drv)
    ws.set_home_tab("games")
    ws.carts.dup(next(it for it in ws.launcher.items if it.get("path")))
    ws.set_home_tab("make")
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and builtin_name(it["path"]) is None)
    ui_draw.input_kind(kind)
    spans, _rect = ws.launcher_layer._hint_spans(ws.sys_canvas)
    got = [v for v, _a, _b in spans]
    assert got[-1] == "tabs", got
    assert "change" in got or kind == "pad", got
