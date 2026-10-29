"""Visual identity v1 "Open Machine" (docs/visual_identity_v1.md) -- Phase 1/2
vertical slice:

  * semantic theme roles (Section 4.3): theme_colors() resolves every role for
    every theme, and the default is Moy in its Day mode (docs/look_2026-10.md
    step 27, which deleted `night` and the opt-in `machine`);
  * the Library verbs (Sections 1.2-1.3): the selected card exposes PLAY and
    CHANGE, primary activation still always plays, CHANGE opens the SAME project
    in the Editor landing on Config;
  * the acceptance journey (Section 10 Phase 2): Library -> PLAY -> exit ->
    Library, Library -> CHANGE -> Studio/Config, Studio PLAY -> same tab.

All driven through the same shared console the device runs (runtime.host_app),
so these assert host==device behavior."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_ws as _build_ws, use_theme


def _ws(tmp_path, **kw):
    """A workstation on the `night` fixture (tests/fixtures/themes/) in its
    dark mode, the palette theme these surfaces were pinned on: no look of
    its own, so the default's, flat."""
    ws = _build_ws(tmp_path, **kw)
    use_theme(ws, "night", "dark")
    return ws


def _home_layer(ws):
    return ws.launcher_layer


# -- Section 4.3: semantic theme roles ---------------------------------------

def test_every_theme_resolves_every_semantic_role():
    from runtime.chrome import THEME_NAMES, theme_colors, RGB
    roles = ("desktop", "desktop_pattern", "surface", "surface_alt", "border",
             "ink", "ink_dim", "selection", "focus", "play", "author", "danger")
    for name in THEME_NAMES:
        th = theme_colors(name)
        for role in roles:
            assert role in th, (name, role)
            assert 0 <= th[role] <= 63 or th[role] >= RGB
        assert isinstance(th["surface_light"], bool)


def test_the_default_is_moy_in_its_day_mode(tmp_path):
    """The seed set is Moy, Blocks and Contrast, Moy's Day the default
    (docs/look_2026-10.md step 27); a deleted seed's name is no theme."""
    from runtime.chrome import THEME_NAMES, DEFAULT_THEME, DEFAULT_VARIANT
    assert (DEFAULT_THEME, DEFAULT_VARIANT) == ("moy", "light")
    assert THEME_NAMES == ("moy", "blocks", "contrast")
    ws = _build_ws(tmp_path)
    assert (ws.look.theme_name, ws.look.theme_variant) == ("moy", "light")


def test_unknown_theme_falls_back_to_default():
    from runtime.chrome import theme_colors
    assert theme_colors("no-such-theme") == theme_colors("moy")
    assert theme_colors("night") == theme_colors("moy")


# -- Light/dark variants (owner ask 2026-07-23) -------------------------------

def test_the_one_arg_call_is_the_default_variant():
    """theme_colors(name) is theme_colors(name, DEFAULT_VARIANT): the one-arg
    call answers the mode the console boots in."""
    from runtime.chrome import THEME_NAMES, DEFAULT_VARIANT, theme_colors
    for name in THEME_NAMES:
        assert theme_colors(name) is theme_colors(name, DEFAULT_VARIANT)


def test_theme_variant_applies_and_persists(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    assert ws.look.theme_variant == "dark"
    ws.look.set_theme_variant("light")
    assert ws.look.theme_variant == "light"
    assert ws.theme_colors["surface_light"] is True
    assert ws.launcher.theme is ws.theme_colors
    assert ws.system.get("theme_variant") == "light"
    carts = ws.carts_root
    assert moy_carts.load_system(carts).get("theme_variant") == "light"
    # A theme pick keeps the variant; an unknown variant falls back to the
    # default's.
    ws.look.set_theme("blocks")
    assert ws.look.theme_variant == "light"
    ws.look.set_theme_variant("dark")
    ws.look.set_theme_variant("nonsense")
    assert ws.look.theme_variant == "light"


def test_variant_survives_reboot(tmp_path):
    ws = _build_ws(tmp_path)
    ws.look.set_theme("contrast")
    ws.look.set_theme_variant("dark")
    ws2 = _build_ws(tmp_path)
    assert ws2.look.theme_name == "contrast" and ws2.look.theme_variant == "dark"
    assert ws2.theme_colors["surface_light"] is False


def test_base_tier_editors_follow_the_theme(tmp_path):
    """The 320x240 tier's editors read the theme's tokens in both variants
    (owner ask 2026-07-23 -- 'editors follow the styles'; the frozen dark
    literals retired with theming's phase 2b)."""
    ws = _ws(tmp_path)                      # 320x240 base tier
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path") and it.get("edit"))
    ws.change_selected()
    t = ws.cards_layer._tones()
    assert t["body"] == ws.theme_colors["surface"]  # themed in dark too
    assert ws.code_layer._tones()["bg"] == ws.theme_colors["surface"]
    ws.look.set_theme_variant("light")
    t = ws.cards_layer._tones()
    assert t["body"] == ws.theme_colors["surface"]  # themed on light
    assert t["head"] == ws.theme_colors["ink"] == 0
    # The code editor gets exactly a light-and-dark pair on the base tier.
    tc = ws.code_layer._tones()
    assert tc["bg"] == ws.theme_colors["surface"]
    assert ws.look.content_colors()["code_keyword"] is not None   # the light syntax set


def test_bar_icons_get_plateless_light_variants(tmp_path):
    """LIGHT chrome derives per-icon light sprites: the sheet's 0 plate is keyed
    transparent and white strokes flip to ink-black, so wifi/batt never draw a
    black plate on the light bar (owner report 2026-07-23)."""
    ws = _ws(tmp_path)
    dark = ws._bar_image("wifi")
    assert dark.transparent == -1                   # dark bar: opaque tile
    ws.look.set_theme_variant("light")
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
    """Move the launcher selection onto the first real (non-pseudo) game."""
    for i, it in enumerate(ws.launcher.items):
        if it.get("path") and it.get("type") == "game":
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


def test_change_selected_ignores_the_new_card(tmp_path):
    ws = _ws(tmp_path)
    ws.set_home_tab("make")
    ws.launcher.sel = 0                           # Make's "+ New" pseudo card
    assert ws.launcher.selected().get("path") is None
    ws.change_selected()
    assert ws.screen == "launcher"                # no-op: "+ New" has one verb, its tap


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


def test_the_zone_carries_the_active_tabs_verbs_on_every_tier(tmp_path):
    """docs/shell_layout_2026-10.md §12: no card carries a verb row; the lent bar
    zone carries the active tab's verbs on the selected card, on every tier."""
    for ws in (_ws(tmp_path / "a"), _ws(tmp_path / "b", sys_size=(1024, 600),
                                         font_scale=2)):
        home = _home_layer(ws)
        _select_real_cart(ws)
        assert ws.launcher.action_rects() is None
        assert home.zone_verbs() == ("play", "change", "search")
        ws.set_home_tab("mine")                   # nothing made yet: "+ New"
        assert home.zone_verbs() == ("new",)
        ws.set_home_tab("apps")
        assert home.zone_verbs() == ("open",)
        ws.set_home_tab("make")
        assert home.zone_verbs() == ("new",)      # "+ New" first
        ws.launcher.sel = 1
        assert home.zone_verbs() == ("new", "change", "copy")     # a built-in
        ws.carts.dup(ws.launcher.selected())      # a project of the kid's own
        ws.set_home_tab("make")
        ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                               if (it.get("title") or "").endswith(" copy"))
        assert home.zone_verbs() == ("new", "change", "copy", "delete")
        chips = home._zone_action_rects(ws.layout.zone_left)
        # ...and the selected cart's favourite star where the strip has room
        assert set(chips) - {"favourite"} == {"new", "change", "copy", "delete"}


def test_base_tier_zone_chips_dispatch(tmp_path):
    """The lent bar zone's PLAY / CHANGE chips dispatch their verbs."""
    ws = _ws(tmp_path)
    _select_real_cart(ws)
    home = _home_layer(ws)
    chips = home._zone_action_rects(ws.layout.zone_left)
    assert chips is not None
    x, y, w, h = chips["change"]
    assert home.zone_tap(x + 1, y + 1, ws.layout.zone_left)
    ws.frame(1 / 30)                  # #184: the tap scheduled; a frame lands it
    assert ws.screen == "menu"
    ws.go_home()
    _select_real_cart(ws)
    x, y, w, h = chips["play"]
    assert home.zone_tap(x + 1, y + 1, ws.layout.zone_left)
    ws.frame(1 / 30)
    assert ws.screen == "desktop"
    # On Make's "+ New" card the one chip is NEW: no PLAY to tap.
    ws._exit_to_caller()
    ws.set_home_tab("make")
    ws.launcher.sel = 0
    assert set(home._zone_action_rects(ws.layout.zone_left)) == {"new"}


def test_the_home_shows_its_wallpaper_behind_the_cards(tmp_path):
    """The carousel's band has no backdrop of its own (D7: the home has its
    own wallpaper slot): beside the cards the wallpaper shows, never the
    theme's surface."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2)
    use_theme(ws, "machine", "dark")
    ws.frame(1 / 30)
    bx, by, bw, bh = ws.launcher.band_rect()
    th = ws.theme_colors
    assert th["surface"] == 7
    cv = ws.sys_canvas
    # The band's left edge: card 0 rests at the centre, nothing left of it.
    assert all(cv.pix(bx + 2, y) != th["surface"] for y in range(by, by + bh, 7))


def _cover_sync(ws, cart, div=1):
    """Pump the TIME-SLICED cover decode to completion -- one slice per call,
    exactly as successive frames would -- and return the finished cache entry
    (the picture, or None for a definitive no-cover miss)."""
    key = (cart.get("path"), div)
    for _ in range(500):
        ws.covers._built = False           # what frame() resets each frame
        ws.covers._ms = 0
        ws.covers.cover_for(cart, div)
        if key in ws.covers._cache:
            return ws.covers._cache[key]
    raise AssertionError("cover decode never finished")


def _covered_and_bare(ws):
    from runtime import moy_carts
    covered = fallback = None
    for it in ws.launcher.items:
        if not it.get("path"):
            continue
        has = moy_carts.load_cover(it["path"])
        if has and covered is None:
            covered = it
        elif not has and fallback is None:
            fallback = it
    return covered, fallback


def test_cover_art_contract(tmp_path):
    """Section 11.4 / SPEC.md 3.6: a cart's cover.png is its Library cover,
    decoded ONCE into a 128x128 base in the system canvas's 565 byte order;
    carts without one fall back (None -> icon/glyph). Cached per cart."""
    from runtime import cover_png, moy_carts
    ws = _ws(tmp_path, sys_size=(1024, 600))
    covered, fallback = _covered_and_bare(ws)
    assert covered is not None            # the seed games ship covers
    img = _cover_sync(ws, covered)
    assert img is not None and (img.w, img.h) == (128, 128)
    order = (cover_png.RGB565_SW if ws.sys_canvas.swapped565
             else cover_png.RGB565)
    assert bytes(img.pix) == cover_png.decode(
        moy_carts.load_cover(covered["path"]), 1, order)
    assert ws.covers.cover_for(covered) is img       # memoised
    if fallback is not None:
        assert _cover_sync(ws, fallback) is None   # deterministic fallback


def test_cover_decodes_are_time_sliced_and_faithful(tmp_path, monkeypatch):
    """#66: a decode a frame cannot afford runs as a RESUMABLE job, a slice
    per frame, and the finished pixels equal the one-shot decode."""
    from runtime import cover_cache, cover_png, moy_carts
    ws = _ws(tmp_path, sys_size=(1024, 600))
    covered, _bare = _covered_and_bare(ws)
    monkeypatch.setattr(cover_cache, "_COVER_SLICE_MS", 0)
    monkeypatch.setattr(cover_cache, "_COVER_ROWS", 8)
    key = (covered["path"], 1)
    ws.covers._built = False
    ws.covers.cover_for(covered)
    # The first ask left a job in flight with the redraw gate re-armed -- it
    # never holds the frame open-ended.
    assert key in ws.covers._jobs and ws.covers._deferred
    steps = 0
    while key not in ws.covers._cache:
        ws.covers._built = False
        ws.covers._ms = 0
        ws.covers.cover_for(covered)
        steps += 1
    assert steps >= 128 // 8 - 1
    order = (cover_png.RGB565_SW if ws.sys_canvas.swapped565
             else cover_png.RGB565)
    assert bytes(ws.covers._cache[key].pix) == cover_png.decode(
        moy_carts.load_cover(covered["path"]), 1, order)


def test_a_relayout_keeps_one_base_per_cover(tmp_path):
    """No per-size variants (docs/theming_2026-09.md, P8): every layout of
    every grid draws the same cached base, so a relayout adds nothing -- but
    the grid's interim HALF, for a card too small for 128."""
    ws = _ws(tmp_path, sys_size=(1024, 600))
    covered, _bare = _covered_and_bare(ws)
    first = _cover_sync(ws, covered)
    for scale in (2, 1, 3, 1):              # each one a relayout of both grids
        ws.look.set_font_scale(scale, persist=False)
        ws._dirty = True
        ws.frame(1 / 30)
        assert _cover_sync(ws, covered) is first
    divs = sorted(k[1] for k in ws.covers._cache
                  if k[0] == covered["path"] and len(k) == 2)      # not a dimmed tint
    assert divs in ([1], [1, 2])


def test_a_cover_takes_the_next_scale_up_when_it_overflows_by_a_tenth_at_most():
    """The one rule that picks a cover's scale (`_cover_scale`): the largest
    whole number that fits the art slot, or the next one up when that
    overflows the slot by at most 10% each way. The Guition P4's 258x245 card
    takes 2x (11 px over); the Waveshare's 206x173 stays at 1x (2x would be
    48% over)."""
    from runtime.launcher_layer import _cover_scale
    assert _cover_scale(128, 258, 245) == 2
    assert _cover_scale(128, 206, 173) == 1
    assert _cover_scale(128, 256, 256) == 2
    assert _cover_scale(128, 233, 233) == 2      # 256 is 9.9% over 233
    assert _cover_scale(128, 232, 400) == 1      # ... and 10.3% over 232
    assert _cover_scale(128, 400, 232) == 1
    assert _cover_scale(128, 120, 120) == 1      # 1x near-fits too
    assert _cover_scale(128, 116, 300) == 0
    assert _cover_scale(64, 97, 75) == 1
    assert _cover_scale(64, 85, 49) == 0
    assert _cover_scale(128, 0, 0) == 0
    assert _cover_scale(128, 300, -4) == 0
