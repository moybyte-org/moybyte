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
        assert isinstance(th["surface_light"], bool)


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


# -- Light/dark variants (owner ask 2026-07-23) -------------------------------

def test_every_theme_has_a_light_variant_resolving_every_role():
    """Each theme family ships DARK + LIGHT presentations of the same identity:
    the light set resolves every semantic role, is marked surface_light, and
    flips the chrome ink dark (light fields need dark text)."""
    from runtime.chrome import THEMES, THEME_LIGHT, theme_colors
    roles = ("panel", "edge", "title", "title_ink", "accent", "hilite", "dim",
             "desktop", "desktop_pattern", "surface", "surface_alt", "border",
             "ink", "ink_dim", "chrome_ink", "chrome_ink_dim", "selection",
             "focus", "play", "author", "danger",
             "title_active", "title_inactive")
    for name, _tokens in THEMES:
        assert name in THEME_LIGHT, name
        th = theme_colors(name, "light")
        for role in roles:
            assert role in th, (name, role)
            assert 0 <= th[role] <= 63
        assert th["surface_light"] is True
        assert th["ink"] == 0 and th["chrome_ink"] == 0
        # The signal verbs keep their Section 4.2 jobs in both variants.
        assert th["play"] == 11 and th["danger"] == 8 and th["focus"] == 10


def test_dark_variant_is_the_legacy_token_set():
    """theme_colors(name) == theme_colors(name, "dark") -- the one-arg call
    (every existing caller) keeps its exact pre-variant pixels, including the
    chrome ink statics (white / light-grey)."""
    from runtime.chrome import THEMES, theme_colors
    for name, _tokens in THEMES:
        th = theme_colors(name)
        assert th is theme_colors(name, "dark")
        if name != "machine":
            assert th["ink"] == 7 and th["ink_dim"] == 6
        assert th["chrome_ink"] == 7 and th["chrome_ink_dim"] == 6


def test_theme_variant_applies_and_persists(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    assert ws.theme_variant == "dark"
    ws.set_theme_variant("light")
    assert ws.theme_variant == "light"
    assert ws.theme_colors["surface_light"] is True
    assert ws.launcher.theme is ws.theme_colors
    assert ws.system.get("theme_variant") == "light"
    carts = ws.carts_root
    assert moy_carts.load_system(carts).get("theme_variant") == "light"
    # A theme pick keeps the variant; an unknown variant falls back to dark.
    ws.set_theme("berry")
    assert ws.theme_variant == "light" and ws.theme_colors["panel"] == 7
    ws.set_theme_variant("nonsense")
    assert ws.theme_variant == "dark"


def test_variant_survives_reboot(tmp_path):
    ws = _ws(tmp_path)
    ws.set_theme("forest")
    ws.set_theme_variant("light")
    ws2 = _ws(tmp_path)
    assert ws2.theme_name == "forest" and ws2.theme_variant == "light"
    assert ws2.theme_colors["surface_light"] is True


def test_base_tier_editors_follow_light_chrome(tmp_path):
    """The 320x240 _base editor branches keep their frozen literals ONLY in dark
    chrome; a light variant themes the base tier too (owner ask 2026-07-23 --
    'editors follow the styles')."""
    from runtime.chrome import NAMES
    ws = _ws(tmp_path)                      # 320x240 base tier
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and it.get("edit"))
    ws.change_selected()
    t = ws.cards_layer._tones()
    assert t["body"] == NAMES["dark_purple"]        # frozen dark baseline
    ws.set_theme_variant("light")
    t = ws.cards_layer._tones()
    assert t["body"] == ws.theme_colors["surface"]  # themed on light
    assert t["head"] == ws.theme_colors["ink"] == 0
    # The code editor gets exactly a light-and-dark pair on the base tier.
    tc = ws.code_layer._tones()
    assert tc["bg"] == ws.theme_colors["surface"]
    assert tc["hl"] is not None                     # the light syntax set


def test_bar_icons_get_plateless_light_variants(tmp_path):
    """LIGHT chrome derives per-icon light sprites: the sheet's 0 plate is keyed
    transparent and white strokes flip to ink-black, so wifi/batt never draw a
    black plate on the light bar (owner report 2026-07-23)."""
    ws = _ws(tmp_path)
    dark = ws._bar_image("wifi")
    assert dark.transparent == -1                   # dark bar: opaque tile
    ws.set_theme_variant("light")
    light = ws._bar_image("wifi")
    assert light is not dark
    assert light.transparent == 63                  # the plate is keyed out...
    assert 0 in light.pix and 7 not in light.pix    # ...and strokes DRAW as ink
    # (the key must never be 0: black strokes would erase themselves)
    moy = ws._bar_image("moy")
    if moy is not None:                             # mascot keeps its cream pixels
        assert moy.transparent == 0 and 7 in moy.pix


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
    panel whose grid SCROLLS left-right with one TALL featured slot (column 0,
    spanning both visible rows -- the pinned MAKE card); every other card
    stacks the columns marching right, two per column."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2)
    lay = ws.layout
    gx, gy, gw, gh = lay.lib_grid
    tall_h = 2 * lay.lib_card_h + lay.lib_gap
    assert lay.tile_rect(0, 0) == (gx, gy, lay.lib_card_w, tall_h)  # tall featured slot
    assert lay.tile_rect(1, 0)[3] == lay.lib_card_h              # ordinary card
    # The packing stacks each column top-to-bottom, then steps right.
    assert lay.tile_cell(1) == (0, 1)
    assert lay.tile_cell(2) == (1, 1)                            # below, same column
    assert lay.tile_cell(3) == (0, 2)                            # next column's top
    # Scrolling shifts every cell left; a half-scrolled card still returns its
    # (clipped) rect, and far enough the card leaves the viewport entirely.
    half = lay.lib_card_w // 2
    assert lay.tile_rect(1, half)[0] == gx + lay.lib_step - half
    assert lay.tile_rect(1, lay.lib_step + lay.lib_card_w) is None  # scrolled off
    px, py, pw, ph = lay.lib_panel                               # panel frames grid
    assert px <= gx and py <= gy and gx + gw <= px + pw and gy + gh <= py + ph
    # Vertical nav steps within a card column (the tall slot spans both rows).
    ws.launcher.sel = 1
    ws.launcher.nav2d(0, 1)
    assert ws.launcher.sel == 2
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


def _cover_sync(ws, cart, w, h):
    """Pump the TIME-SLICED cover build to completion -- one slice per call,
    exactly as successive frames would -- and return the finished cache entry
    (the image, or None for a definitive no-cover miss)."""
    key = (cart.get("path"), w, h)
    for _ in range(500):
        ws._cover_built = False           # what frame() resets each frame
        ws._cover_for(cart, w, h)
        if key in ws._cover_cache:
            return ws._cover_cache[key]
    raise AssertionError("cover build never finished")


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
    img = _cover_sync(ws, covered, 200, 150)
    assert img is not None and (img.w, img.h) == (200, 150)
    assert len(img.pix) == 200 * 150
    assert max(img.pix) < 64              # valid MOY64 indices only (Section 12)
    assert img._paint                     # native device + compact web bitmap paths
    assert ws._cover_for(covered, 200, 150) is img       # memoised
    if fallback is not None:
        assert _cover_sync(ws, fallback, 200, 150) is None  # deterministic fallback


def test_cover_builds_are_time_sliced_and_faithful(tmp_path):
    """#66: decoding one 320x240 cover in one go measured 0.5-1.7s on the
    T-Deck, so _cover_for runs a RESUMABLE job -- at most one ~8ms slice per
    frame -- and the finished pixels must equal the one-shot decode + crop."""
    from runtime import moy_carts
    ws = _ws(tmp_path, sys_size=(1024, 600))
    covered = next(it for it in ws.launcher.items
                   if it.get("path") and
                   moy_carts.load_image(it["path"], moy_carts.COVER_IMAGE))
    other = next(it for it in ws.launcher.items
                 if it.get("path") and it is not covered)
    key = (covered["path"], 200, 150)
    ws._cover_built = False
    ws._cover_for(covered, 200, 150)
    # The first ask either finished within its slice or left a job in flight
    # (with the redraw gate re-armed) -- it never blocks the frame open-ended.
    assert key in ws._cover_cache or key in ws._cover_jobs
    if key not in ws._cover_cache:
        assert ws._covers_deferred
        # The frame budget is ONE build slice: a second cart's ask this frame
        # defers without even starting its job.
        before = dict(ws._cover_jobs)
        assert ws._cover_for(other, 200, 150) is None
        assert list(ws._cover_jobs) == list(before)
    img = _cover_sync(ws, covered, 200, 150)
    # Reference: the one-shot decode + centered cover-crop (the pre-slicing
    # implementation, inlined).
    blob = moy_carts.load_image(covered["path"], moy_carts.COVER_IMAGE)
    sw, sh, pix = moy_carts.decode_moyimg(blob)
    w, h = 200, 150
    cw_ = min(sw, sh * w // h) or 1
    ch_ = min(sh, sw * h // w) or 1
    ox, oy = (sw - cw_) // 2, (sh - ch_) // 2
    want = bytearray(w * h)
    di = 0
    for dy in range(h):
        row = (oy + dy * ch_ // h) * sw + ox
        for dx in range(w):
            want[di] = pix[row + dx * cw_ // w]
            di += 1
    assert bytes(img.pix) == bytes(want)


def test_cover_cache_is_bounded_across_resize_variants(tmp_path):
    """Repeated Make-window resizes must not retain every derived indexed+RGB
    cover forever on the P4 heap."""
    from runtime import console, moy_carts, web_view
    ws = _ws(tmp_path, sys_size=(1024, 600))
    covered = next(it for it in ws.launcher.items
                   if it.get("path") and
                   moy_carts.load_image(it["path"], moy_carts.COVER_IMAGE))
    first = _cover_sync(ws, covered, 120, 90)
    rec = web_view.DrawRecorder(1024, 600)
    rec.self_contained = True
    rec.spr(first, 0, 0)
    # The paint fast wire, not the generic JSON pixel-list spr: covers are
    # serial-NAMED now (#113), so they ride the ship-once imgref lane (the
    # nameless inline "img" form remains the fallback for unnamed paint images).
    assert rec._cmds[0][0] == "imgref"

    for i in range(80):
        _cover_sync(ws, covered, 120 + i, 90 + i)
    assert len(ws._cover_cache) <= console._COVER_CACHE_MAX_ENTRIES
    assert ws._cover_cache_pixels <= console._COVER_CACHE_MAX_PIXELS
    assert len(ws._cover_cache_order) == len(ws._cover_cache)


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
