"""Visual identity v1 "Open Machine" (docs/visual_identity_v1.md) -- Phase 1/2
vertical slice:

  * semantic theme roles (Section 4.3): theme_colors() resolves every role for
    every theme, the "machine" theme is opt-in, and "night" stays byte-identical;
  * the Library verbs (Sections 1.2-1.3): the selected card exposes PLAY and
    CHANGE, primary activation still always plays, CHANGE opens the SAME project
    in the Editor landing on Config;
  * the acceptance journey (Section 10 Phase 2): Library -> PLAY -> exit ->
    Library, Library -> CHANGE -> Studio/Config, Studio PLAY -> same tab.

All driven through the same shared console the device runs (runtime.host_app),
so these assert host==device behavior."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ws(tmp_path, **kwargs):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"), **kwargs)


def _home_layer(ws):
    return ws.launcher_layer


# -- Section 4.3: semantic theme roles ---------------------------------------

def test_every_theme_resolves_every_semantic_role():
    from runtime.chrome import THEMES, theme_colors
    roles = ("desktop", "desktop_pattern", "surface", "surface_alt", "border",
             "ink", "ink_dim", "selection", "focus", "play", "author", "danger")
    for name, _tokens in THEMES:
        th = theme_colors(name)
        for role in roles:
            assert role in th, (name, role)
            assert 0 <= th[role] <= 63


def test_night_base_tokens_are_byte_identical():
    """The shipped default keeps today's exact values (Section 4.3: Open Machine
    is an additional theme, never an in-place mutation of "night")."""
    from runtime.chrome import theme_colors
    th = theme_colors("night")
    assert {k: th[k] for k in ("panel", "edge", "title", "title_ink",
                               "accent", "hilite", "dim")} == {
        "panel": 60, "edge": 13, "title": 13, "title_ink": 0,
        "accent": 10, "hilite": 13, "dim": 1}
    # The semantic fallbacks ARE today's hardcoded literals.
    assert th["ink"] == 7 and th["ink_dim"] == 6
    assert th["focus"] == th["accent"] == 10
    assert th["play"] == 11 and th["danger"] == 8
    assert th["author"] == 10          # the frozen Make-tile yellow


def test_machine_theme_is_optional_and_selectable(tmp_path):
    """Open Machine ships as an opt-in Settings theme with the Section 4.2 jobs:
    dark-blue field, signal verbs (play green / author orange / focus yellow)."""
    from runtime.chrome import THEMES, DEFAULT_THEME, theme_colors
    assert DEFAULT_THEME == "night"
    assert any(n == "machine" for n, _t in THEMES)
    th = theme_colors("machine")
    assert th["panel"] == th["desktop"] == 1     # dark blue construction field
    assert th["play"] == 11 and th["author"] == 9 and th["focus"] == 10
    assert th["danger"] == 8
    ws = _ws(tmp_path)
    ws.set_theme("machine")
    assert ws.theme_name == "machine"
    assert ws.launcher.theme is ws.theme_colors
    # Persisted like any theme choice.
    assert ws.system.get("theme") == "machine"


def test_unknown_theme_falls_back_to_default():
    from runtime.chrome import theme_colors
    assert theme_colors("no-such-theme") == theme_colors("night")


# -- Sections 1.2/6.1: the Library card's PLAY / CHANGE verbs ----------------

def _select_real_cart(ws):
    """Move the launcher selection onto the first real (non-pseudo) cart."""
    for i, it in enumerate(ws.launcher.items):
        if it.get("path"):
            ws.launcher.sel = i
            return it
    raise AssertionError("no real cart in the launcher grid")


def test_change_selected_opens_editor_on_config(tmp_path):
    ws = _ws(tmp_path)
    cart = _select_real_cart(ws)
    ws.change_selected()
    assert ws.screen == "menu"                    # the Editor app
    assert ws.cart["path"] == cart["path"]        # the SAME project, in place
    # Config-first (Section 1.3): the cards tab when the cart has an edit
    # schema, else the deterministic gentlest fallback (code).
    assert ws.editor_app.tab == ("cards" if ws.cart.get("edit") else "code")


def test_change_selected_ignores_the_make_tile(tmp_path):
    ws = _ws(tmp_path)
    ws.launcher.sel = 0                           # the pinned Make pseudo tile
    assert ws.launcher.selected().get("path") is None
    ws.change_selected()
    assert ws.screen == "launcher"                # no-op: Make has one verb, its tap


def test_primary_activation_still_always_plays(tmp_path):
    """Section 1.2: a tap/confirm on the card has ONE predictable meaning."""
    ws = _ws(tmp_path)
    _select_real_cart(ws)
    ws.launch_selected()
    assert ws.screen == "desktop"                 # the Player owns the screen


def test_player_exit_returns_to_library(tmp_path):
    ws = _ws(tmp_path)
    _select_real_cart(ws)
    ws.launch_selected()
    assert ws.screen == "desktop"
    ws._exit_to_caller()
    assert ws.screen == "launcher"                # Library is Home (Section 1.1)


def test_studio_play_returns_to_same_tab(tmp_path):
    """Section 1.4: a playtest launched from Studio returns to the same project
    and tab."""
    ws = _ws(tmp_path)
    cart = _select_real_cart(ws)
    ws.change_selected()
    ws.menu_view = "code"
    ws.run_code()
    assert ws.screen == "desktop"
    ws._exit_to_caller()
    assert ws.screen == "menu"
    assert ws.editor_app.tab == "code"            # the SAME tab
    assert ws.cart["path"] == cart["path"]


def test_action_rects_only_on_desktop_density(tmp_path):
    """320x240 baseline: no on-card buttons (the zoned bar carries the verbs);
    desktop density: the selected real card exposes both rects."""
    ws = _ws(tmp_path)
    _select_real_cart(ws)
    assert ws.launcher.action_rects() is None     # base tier
    ws2 = _ws(tmp_path, sys_size=(1024, 600), font_scale=2)
    _select_real_cart(ws2)
    ar = ws2.launcher.action_rects()
    assert ar is not None and set(ar) == {"play", "change"}
    tile = ws2.launcher.tile_rect(ws2.launcher.sel)
    for x, y, w, h in ar.values():
        assert y >= tile[1] and y + h <= tile[1] + tile[3]   # inside the card row
    # The pinned Make tile exposes no PLAY/CHANGE row (one verb: its tap).
    ws2.launcher.sel = 0
    assert ws2.launcher.action_rects() is None
    # The picker grid never grows the row (a pick has one meaning there).
    assert ws2.picker.action_rects() is None


def test_desktop_card_buttons_dispatch(tmp_path):
    """Clicking the selected card's PLAY / CHANGE rects dispatches the verbs."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2)
    _select_real_cart(ws)
    ar = ws.launcher.action_rects()
    home = _home_layer(ws)
    x, y, w, h = ar["change"]
    home.handle_pointer(x + w // 2, y + h // 2, True)
    assert ws.screen == "menu"                    # CHANGE -> Studio/Editor
    # Back out, then PLAY via the button.
    ws.go_home()
    _select_real_cart(ws)
    ar = ws.launcher.action_rects()
    x, y, w, h = ar["play"]
    home.handle_pointer(x + w // 2, y + h // 2, True)
    assert ws.screen == "desktop"                 # PLAY -> Player


def test_base_tier_zone_chips_dispatch(tmp_path):
    """On the 320x240 baseline the lent bar zone carries the PLAY / CHANGE chips."""
    ws = _ws(tmp_path)
    _select_real_cart(ws)
    home = _home_layer(ws)
    chips = home._zone_action_rects(ws.layout.zone_left)
    assert chips is not None
    x, y, w, h = chips["change"]
    assert home.zone_tap(x + 1, y + 1, ws.layout.zone_left)
    assert ws.screen == "menu"
    ws.go_home()
    _select_real_cart(ws)
    x, y, w, h = chips["play"]
    assert home.zone_tap(x + 1, y + 1, ws.layout.zone_left)
    assert ws.screen == "desktop"
    # With the Make tile selected there are no chips (nothing to claim).
    ws._exit_to_caller()
    ws.launcher.sel = 0
    assert home._zone_action_rects(ws.layout.zone_left) is None
    assert not home.zone_tap(x + 1, y + 1, ws.layout.zone_left)


def test_library_shelf_geometry(tmp_path):
    """The desktop-density Library shelf (the library-concept mockup): a framed
    panel whose grid has one TALL featured slot (column 0, both rows -- the pinned
    MAKE card) plus rows x (cols-1) cartridge cards per page."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2)
    lay = ws.layout
    assert lay.page == lay.rows * (lay.cols - 1) + 1
    gx, gy, gw, gh = lay.lib_grid
    assert lay.tile_rect(0, 0) == (gx, gy, lay.lib_card_w, gh)   # tall featured slot
    assert lay.tile_rect(1, 0)[3] == lay.lib_card_h              # ordinary card
    px, py, pw, ph = lay.lib_panel                               # panel frames grid
    assert px <= gx and py <= gy and gx + gw <= px + pw and gy + gh <= py + ph
    # Vertical nav hops between the two card rows (the tall slot spans both).
    ws.launcher.sel = 1
    ws.launcher.nav2d(0, 1)
    assert ws.launcher.sel == 1 + (lay.cols - 1)
    ws.launcher.nav2d(0, -1)
    assert ws.launcher.sel == 1


def test_library_shelf_panel_paints_surface(tmp_path):
    """The machine theme's Library panel is the warm-light tool surface (cream)
    over the dark construction field."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2)
    ws.set_theme("machine")
    ws.frame(1 / 30)
    px, py, pw, ph = ws.layout.lib_panel
    th = ws.theme_colors
    assert th["surface"] == 7
    # A point in the panel header band (left of the LIBRARY heading's start).
    assert ws.sys_canvas.pix(px + 2, py + 2) == th["surface"]


def test_cover_art_contract(tmp_path):
    """Section 11.4: a cart's images/cover.moyimg is its static Library cover,
    cover-cropped to the exact card size; carts without one fall back (None ->
    sprite/glyph). Cached per (path, size)."""
    ws = _ws(tmp_path, sys_size=(1024, 600))
    covered = fallback = None
    from runtime import moy_carts
    for it in ws.launcher.items:
        if not it.get("path"):
            continue
        has = moy_carts.load_image(it["path"], moy_carts.COVER_IMAGE)
        if has and covered is None:
            covered = it
        elif not has and fallback is None:
            fallback = it
    assert covered is not None            # the seed games ship covers now
    img = ws._cover_for(covered, 200, 150)
    assert img is not None and (img.w, img.h) == (200, 150)
    assert len(img.pix) == 200 * 150
    assert max(img.pix) < 64              # valid MOY64 indices only (Section 12)
    assert ws._cover_for(covered, 200, 150) is img       # memoised
    if fallback is not None:
        assert ws._cover_for(fallback, 200, 150) is None  # deterministic fallback


def test_home_draw_includes_action_row_desktop(tmp_path):
    """The desktop-density home frame actually paints the PLAY row (signal green
    is reserved for PLAY, so its presence is a faithful marker)."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2)
    _select_real_cart(ws)
    ws.frame(1 / 30)
    ar = ws.launcher.action_rects()
    x, y, w, h = ar["play"]
    th = ws.theme_colors
    assert ws.sys_canvas.pix(x + 2, y + 2) == th["play"]
    # CHANGE is the mockup's warm-light button (cream field, dark ink).
    x, y, w, h = ar["change"]
    assert ws.sys_canvas.pix(x + 2, y + 2) == 7
