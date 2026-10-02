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

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


from ws_helpers import build_ws as _ws


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
    ws.look.set_theme("machine")
    assert ws.look.theme_name == "machine"
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
    assert ws.look.theme_variant == "dark"
    ws.look.set_theme_variant("light")
    assert ws.look.theme_variant == "light"
    assert ws.theme_colors["surface_light"] is True
    assert ws.launcher.theme is ws.theme_colors
    assert ws.system.get("theme_variant") == "light"
    carts = ws.carts_root
    assert moy_carts.load_system(carts).get("theme_variant") == "light"
    # A theme pick keeps the variant; an unknown variant falls back to dark.
    ws.look.set_theme("berry")
    assert ws.look.theme_variant == "light" and ws.theme_colors["panel"] == 7
    ws.look.set_theme_variant("nonsense")
    assert ws.look.theme_variant == "dark"


def test_variant_survives_reboot(tmp_path):
    ws = _ws(tmp_path)
    ws.look.set_theme("forest")
    ws.look.set_theme_variant("light")
    ws2 = _ws(tmp_path)
    assert ws2.look.theme_name == "forest" and ws2.look.theme_variant == "light"
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
    ws.look.set_theme_variant("light")
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
    """Clicking the selected card's PLAY / CHANGE rects dispatches the verbs
    (#184: a tap SCHEDULES the transition; the next frame lands it behind the
    LOADING paint, so the assert follows a frame pump)."""
    ws = _ws(tmp_path, sys_size=(1024, 600), font_scale=2)
    _select_real_cart(ws)
    ar = ws.launcher.action_rects()
    home = _home_layer(ws)
    x, y, w, h = ar["change"]
    home.handle_pointer(x + w // 2, y + h // 2, True)
    ws.frame(1 / 30)
    assert ws.screen == "menu"                    # CHANGE -> Studio/Editor
    # Back out, then PLAY via the button.
    ws.go_home()
    _select_real_cart(ws)
    ar = ws.launcher.action_rects()
    x, y, w, h = ar["play"]
    home.handle_pointer(x + w // 2, y + h // 2, True)
    ws.frame(1 / 30)
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
    ws.frame(1 / 30)                  # #184: the tap scheduled; a frame lands it
    assert ws.screen == "menu"
    ws.go_home()
    _select_real_cart(ws)
    x, y, w, h = chips["play"]
    assert home.zone_tap(x + 1, y + 1, ws.layout.zone_left)
    ws.frame(1 / 30)
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
    ws.look.set_theme("machine")
    ws.frame(1 / 30)
    px, py, pw, ph = ws.layout.lib_panel
    th = ws.theme_colors
    assert th["surface"] == 7
    # A point in the panel header band (left of the LIBRARY heading's start).
    assert ws.sys_canvas.pix(px + 2, py + 2) == th["surface"]


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
    divs = sorted(k[1] for k in ws.covers._cache if k[0] == covered["path"])
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


def _drawn_cover(slot, outer):
    """A 128x128 picture of distinct words drawn by the shelf's cover draw
    into `slot` on a 320x300 canvas whose cards clip to `outer`: the canvas's
    words, the picture's, and the clip it was left with."""
    from runtime import host_canvas
    from runtime.cover_cache import _CoverImage
    from runtime.launcher_layer import _draw_cover
    cv = host_canvas.make_canvas(320, 300)
    cv.cls(3)
    words = [((i * 2654435761) >> 9) & 0xFFFF for i in range(128 * 128)]
    pix = bytearray()
    for w_ in words:
        pix += bytes((w_ & 255, w_ >> 8))
    cv.clip(*outer)
    _draw_cover(cv, _CoverImage(128, 128, pix), *slot, outer)
    left = (cv._clip_x0, cv._clip_y0, cv._clip_x1, cv._clip_y1)
    cv.clip()
    cv.flush_batch()
    return list(memoryview(cv._buf).cast("H")), words, left


def test_a_near_fit_cover_is_cropped_to_its_slot_centred():
    """2x of a 128 cover in the Guition P4's 258x245 slot is 256x256: one
    column of slot either side, 5 rows cropped off the top and 6 off the
    bottom, nothing drawn outside the slot -- and the cards' clip is what the
    draw leaves behind."""
    sx, sy, sw, sh = 20, 30, 258, 245
    got, words, left = _drawn_cover((sx, sy, sw, sh), (10, 25, 290, 270))
    bg = got[0]
    ox, oy = sx + 1, sy - 5
    for y in range(300):
        for x in range(320):
            if sx <= x < sx + sw and sy <= y < sy + sh and ox <= x < ox + 256:
                want = words[((y - oy) // 2) * 128 + (x - ox) // 2]
            else:
                want = bg
            assert got[y * 320 + x] == want, (x, y)
    assert left == (10, 25, 300, 295)


def test_a_cover_its_card_clip_cuts_stays_inside_both():
    """A card scrolled half out of the shelf: the crop is the slot inside the
    cards' clip, so the cover never draws past the shelf's edge."""
    got, _words, left = _drawn_cover((20, 30, 258, 245), (10, 25, 100, 270))
    bg = got[0]
    assert all(got[y * 320 + x] == bg for y in range(300) for x in range(110, 320))
    assert got[40 * 320 + 60] != bg
    assert left == (10, 25, 110, 295)


def test_a_cover_that_fits_draws_centred_and_leaves_the_clip_alone():
    """The Waveshare's 206x173 slot takes 1x: the cover centred, the clip
    never touched."""
    got, words, left = _drawn_cover((20, 30, 206, 173), (10, 25, 290, 270))
    ox, oy = 20 + (206 - 128) // 2, 30 + (173 - 128) // 2
    assert all(got[(oy + y) * 320 + ox + x] == words[y * 128 + x]
               for y in range(128) for x in range(128))
    assert left == (10, 25, 300, 295)


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
