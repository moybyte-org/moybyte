"""`Appearance` direct (#209 landing D, docs/console_architecture_2026-08.md).

The LOOK cluster is the one the pixel goldens cover best -- 97 whole-screen
hashes x 5 configs plus 298 sub-surface hashes are, between them, a very
thorough test of "does the shell still draw the night colorway at 1x". What
they cannot see is everything below, and it is most of what this object does:

  * **the token dict is REBOUND, never mutated in place.** `ws.system` could
    stay a plain alias through landing B because `SystemStore.load()` clears and
    updates one dict; `ws.theme_colors` is the opposite case and must stay so --
    `launcher_layer._statics_key` and `_pseudo_key` fold `id(ws.theme_colors)`,
    so the NEW dict IS the shelf's invalidation. A tidy-minded in-place update
    would leave the launcher painting the old colorway until something else
    happened to dirty it, which no single-frame hash would catch.

  * **`select_wallpaper` coordinates four owners.** Roster (rehydrate/re-slim),
    backdrop component (clear/compile), settings funnel (persist) and
    achievements (note) in nine lines -- including the #66 diet's fat-cart
    exception and the rule that only a PERSISTED pick earns "Home Decorator".

  * **`set_font_scale` must land on the ROOT canvas**, not on whatever window
    buffer is installed while Settings draws (2026-07-26: "changing it while
    running messes it up, if I change and reboot it looks great").

  * **`set_icon_sheet` clears a cache it does not own** -- `ws._bar_img_cache`
    stays kernel because it backs the shared draw toolkit, so the one write
    across that line is pinned here.

  * **persistence is per-verb.** Every setter takes `persist=`, and `False` must
    write NOTHING: the boot cascade calls all of them that way, and a setter
    that persisted anyway would re-write the store on every boot (and, for the
    wallpaper, hand out an achievement for booting).
"""

import ast
import inspect
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import appearance, moy_carts, skin, ui  # noqa: E402
from runtime.chrome import THEMES, theme_colors  # noqa: E402
from ws_helpers import build_desktop_ws, build_ws  # noqa: E402


def _notes(ws):
    """Record every achievement the shell notes, without the store."""
    seen = []
    ws.ach.note = lambda name: seen.append(name)
    return seen


def _stored(ws):
    return moy_carts.load_system(ws.carts_root)


# -- the state moved, with no mirror behind it --------------------------------

def test_the_kernel_keeps_no_look_state(tmp_path):
    """Six attributes moved and none was left as a second copy. A mirror here
    would be worse than in landing C's roster: these are written by SETTERS a
    caller can reach directly, so two copies would diverge on the first pick."""
    ws = build_ws(tmp_path)
    for name in ("theme_name", "theme_variant", "skin_name", "font_scale",
                 "wallpaper_id", "icon_sheet"):
        assert not hasattr(ws, name), name
        assert getattr(ws.look, name) is not None or name in ("icon_sheet",)
    src = ROOT.joinpath("runtime/console.py").read_text(encoding="utf-8")
    for gone in ("self.theme_name", "self.theme_variant", "self.skin_name",
                 "self.wallpaper_id", "self.icon_sheet"):
        assert gone not in src, gone


def test_the_tokens_stay_a_flat_kernel_attribute_with_one_author(tmp_path):
    """3e's temperature row: ~70 surface sites read `ws.theme_colors` per draw,
    so it stays a plain attribute on the kernel -- and `look.set_theme` is the
    only thing in the tree that writes it."""
    ws = build_ws(tmp_path)
    assert isinstance(ws.theme_colors, dict)
    assert not isinstance(type(ws).theme_colors if hasattr(type(ws), "theme_colors")
                          else None, property)
    writers = set()
    for path in sorted(ROOT.joinpath("runtime").glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").split("\n")):
            if ".theme_colors =" in line or "self.theme_colors=" in line:
                writers.add(path.name)
    assert writers == {"appearance.py"}, sorted(writers)


# -- theme --------------------------------------------------------------------

def test_set_theme_swaps_the_tokens_pushes_the_grids_and_persists(tmp_path):
    ws = build_ws(tmp_path)
    before = ws.theme_colors
    ws.bar_layer._bar_cache_gen = 0
    ws.look.set_theme("berry")
    assert ws.look.theme_name == "berry"
    assert ws.theme_colors is not before                 # REBOUND, see below
    assert ws.theme_colors == theme_colors("berry", "dark")
    assert ws.launcher.theme is ws.theme_colors          # the selection accent
    assert ws.picker.theme is ws.theme_colors
    assert ws.bar_layer._bar_cache_gen != 0              # the cached strip repaints
    assert ws._dirty
    assert _stored(ws)["theme"] == "berry"


def test_the_token_dict_is_rebound_so_the_shelf_key_changes(tmp_path):
    """The invariant an in-place update would silently break: the launcher's
    statics + pseudo-card keys fold `id(ws.theme_colors)`, so a theme swap
    invalidates them BECAUSE the dict is a new object."""
    ws = build_ws(tmp_path)
    keyed = [ln for ln in ROOT.joinpath("runtime/launcher_layer.py").read_text(
        encoding="utf-8").split("\n") if "id(ws.theme_colors)" in ln]
    assert len(keyed) == 2, keyed
    was = id(ws.theme_colors)
    ws.look.set_theme("forest", persist=False)
    assert id(ws.theme_colors) != was


def test_an_unknown_theme_falls_back_and_an_unknown_variant_too(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.set_theme("no-such-theme", persist=False)
    assert ws.look.theme_name == THEMES[0][0]
    ws.look.set_theme_variant("chartreuse", persist=False)
    assert ws.look.theme_variant == "dark"


def test_set_theme_variant_keeps_the_family_and_persists(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.set_theme("berry", persist=False)
    ws.look.set_theme_variant("light")
    assert (ws.look.theme_name, ws.look.theme_variant) == ("berry", "light")
    assert ws.theme_colors == theme_colors("berry", "light")
    assert _stored(ws)["theme"] == "berry"
    assert _stored(ws)["theme_variant"] == "light"


def test_set_theme_with_no_variant_keeps_the_current_one(tmp_path):
    """The `variant=None` contract every name-only caller relies on."""
    ws = build_ws(tmp_path)
    ws.look.set_theme("berry", persist=False, variant="light")
    ws.look.set_theme("forest", persist=False)
    assert ws.look.theme_variant == "light"


def test_cycle_theme_steps_the_catalog_and_wraps(tmp_path):
    ws = build_ws(tmp_path)
    names = [n for n, _t in THEMES]
    ws.look.set_theme(names[-1], persist=False)
    ws.look.cycle_theme(1)
    assert ws.look.theme_name == names[0]
    ws.look.cycle_theme(-1)
    assert ws.look.theme_name == names[-1]
    assert _stored(ws)["theme"] == names[-1]             # cycle always persists


def test_persist_false_writes_nothing(tmp_path):
    """The boot cascade's contract: loading a setting must not re-write it."""
    ws = build_ws(tmp_path)
    moy_carts.save_system({}, ws.carts_root)
    try:
        ws.look.set_theme("berry", persist=False)
        ws.look.set_theme_variant("light", persist=False)
        ws.look.set_skin("outline", persist=False)
        ws.look.set_font_scale(2, persist=False)
        ws.look.select_wallpaper("fill:black", persist=False)
        assert _stored(ws) == {}
    finally:
        skin.use(skin.DEFAULT)      # process-wide state: never leak it


# -- skin ---------------------------------------------------------------------

def test_set_skin_installs_into_ui_and_stores_the_RESOLVED_name(tmp_path):
    """Storing the name and forgetting to install it would look identical from
    the settings dict, so this asserts through `ui` as well."""
    ws = build_ws(tmp_path)
    th = theme_colors("night")
    plain = ui.state_colors(th, "row", ui.REST)
    try:
        ws.look.set_skin("outline")
        assert ws.look.skin_name == "outline" and skin.active() == "outline"
        assert ui.state_colors(th, "row", ui.REST) != plain
        assert _stored(ws)["skin"] == "outline"
        ws.look.set_skin("no-such-skin")
        assert ws.look.skin_name == skin.DEFAULT
        assert _stored(ws)["skin"] == skin.DEFAULT
    finally:
        skin.use(skin.DEFAULT)


def test_skin_names_is_the_catalog_order(tmp_path):
    ws = build_ws(tmp_path)
    assert list(ws.look.skin_names()) == list(skin.names())


def test_appearance_is_the_one_module_that_installs_a_skin():
    """The owner half of tests/test_skin.py's two-way ratchet, from this side:
    `skin.use` is called here and nowhere else, and console.py -- the owner
    until this landing -- no longer imports the catalog at all."""
    src = ROOT.joinpath("runtime/appearance.py").read_text(encoding="utf-8")
    assert "_skin.use(" in src
    console_src = ROOT.joinpath("runtime/console.py").read_text(encoding="utf-8")
    assert "import skin" not in console_src


# -- font scale ---------------------------------------------------------------

def test_set_font_scale_clamps_relays_and_relayouts(tmp_path):
    ws = build_desktop_ws(tmp_path)
    was = ws.layout
    ws.look.set_font_scale(3)
    assert ws.look.font_scale == 3
    assert ws.sys_canvas.font_scale == 3
    assert ws.layout is not was and ws.layout.fs == 3    # the kernel cascade ran
    assert _stored(ws)["font_scale"] == 3
    ws.look.set_font_scale(9, persist=False)             # not in FONT_SCALES
    assert ws.look.font_scale == 1
    ws.look.set_font_scale("two", persist=False)         # not an int at all
    assert ws.look.font_scale == 1


def test_cycle_font_scale_wraps_through_the_ladder_and_persists(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.look.set_font_scale(3, persist=False)
    ws.look.cycle_font_scale(1)
    assert ws.look.font_scale == 1
    assert _stored(ws)["font_scale"] == 1


def test_the_effective_scale_degrades_on_a_shared_canvas(tmp_path):
    """A T-Deck-shaped console remembers the choice and applies 1, so the chrome
    geometry matches the 8px text framebuf actually draws."""
    ws = build_ws(tmp_path)                               # 320x240, one canvas
    assert ws._sys_canvas is None
    ws.look.set_font_scale(3, persist=False)
    assert ws.look.font_scale == 3                        # remembered...
    assert ws.look.effective_font_scale() == 1            # ...not applied
    assert ws.layout.fs == 1


def test_the_font_scale_lands_on_the_root_canvas_not_a_window_buffer(tmp_path):
    """2026-07-26, owner: "changing it while running messes it up, if I change
    and reboot it looks great". On the windowed tier `ws._sys_canvas` is
    whatever WINDOW BUFFER is installed while that window's content draws or
    handles input -- and changing the font size from an open Settings window is
    exactly that. The scale then landed on a buffer `_relayout` immediately
    threw away, leaving the layout reflowed to the new size with the real
    canvas (and every future window buffer, which clones font_scale from the
    root in new_layer) still rendering at the old one."""
    ws = build_desktop_ws(tmp_path)
    root = ws.wm._root_canvas
    buf = root.new_layer(200, 120)
    assert buf is not root
    ws._sys_canvas = buf                        # a window's content is installed
    try:
        ws.look.set_font_scale(3, persist=False)
    finally:
        ws._sys_canvas = root
    assert root.font_scale == 3, "the scale went to the window buffer"
    assert ws.look.effective_font_scale() == 3


# -- wallpaper ----------------------------------------------------------------

def test_wallpaper_options_are_the_carts_then_the_built_in_fills(tmp_path):
    ws = build_ws(tmp_path)
    opts = ws.look.wallpaper_options()
    assert opts[-len(ws.look.FILL_WALLPAPERS):] == list(ws.look.FILL_WALLPAPERS)
    carts = {ws.look.wp_id_for(c) for c in ws.look.wallpaper_carts()}
    assert carts and carts == set(opts[:-len(ws.look.FILL_WALLPAPERS)])
    for wp_id in carts:
        assert ws.look.wp_cart_by_id(wp_id) is not None
    assert ws.look.wp_cart_by_id("no-such-wallpaper") is None


def test_select_wallpaper_falls_back_on_an_unknown_id(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.select_wallpaper("no-such-wallpaper", persist=False)
    assert ws.look.wallpaper_id == ws.look.wallpaper_options()[0]
    ws.look.select_wallpaper(None, persist=False)
    assert ws.look.wallpaper_id == ws.look.wallpaper_options()[0]


def test_select_wallpaper_rehydrates_for_the_compile_then_re_slims(tmp_path):
    """The #66 live-set diet round trip: the backdrop compile bakes src+sheet
    into the wallpaper's own namespace, so the cart is fat for exactly that
    call and slim again afterwards."""
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.look.wallpaper_carts() if c.get("path"))
    ws.carts.reslim(cart)
    assert cart.get("lazy") is True and "src" not in cart
    seen = []
    real = ws.wallpaper.compile
    ws.wallpaper.compile = lambda c: (seen.append("src" in c), real(c))
    ws.look.select_wallpaper(ws.look.wp_id_for(cart), persist=False)
    assert seen == [True], "the compile ran against a SLIMMED cart"
    assert cart.get("lazy") is True and "src" not in cart


def test_select_wallpaper_leaves_the_open_projects_cart_fat(tmp_path):
    """The exception in the diet: re-slimming the cart the Editor has open would
    empty the workspace under it."""
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.look.wallpaper_carts() if c.get("path"))
    ws._open_workspace(cart)
    assert ws._fat_cart is cart
    ws.look.select_wallpaper(ws.look.wp_id_for(cart), persist=False)
    assert cart.get("lazy") is not True and "src" in cart


def test_a_fill_choice_clears_the_backdrop_and_compiles_nothing(tmp_path):
    ws = build_ws(tmp_path)
    seen = []
    ws.wallpaper.compile = lambda c: seen.append(c)
    ws.look.select_wallpaper("fill:indigo", persist=False)
    assert seen == []
    assert ws.look.wallpaper_id == "fill:indigo"


def test_only_a_persisted_pick_earns_home_decorator(tmp_path):
    """`load_system` restores the saved wallpaper with persist=False on every
    boot; noting there would hand out the achievement for switching the console
    on."""
    ws = build_ws(tmp_path)
    seen = _notes(ws)
    ws.look.select_wallpaper("fill:black", persist=False)
    assert seen == []
    ws.look.select_wallpaper("fill:indigo", persist=True)
    assert seen == ["wallpaper_change"]
    assert _stored(ws)["wallpaper"] == "fill:indigo"


def test_cycle_wallpaper_steps_the_options_and_persists(tmp_path):
    ws = build_ws(tmp_path)
    opts = ws.look.wallpaper_options()
    ws.look.select_wallpaper(opts[-1], persist=False)
    ws.look.cycle_wallpaper(1)
    assert ws.look.wallpaper_id == opts[0]
    assert _stored(ws)["wallpaper"] == opts[0]


# -- the icon sheet -----------------------------------------------------------

def test_set_icon_sheet_clears_the_kernels_bar_image_cache(tmp_path):
    """`_bar_img_cache` stays on the Workstation (it backs the shared draw
    toolkit `ws._icon`), so this is the one kernel field the look writes -- and
    a stale entry would keep blitting the previous theme's pixels."""
    ws = build_ws(tmp_path)
    assert ws._bar_image("home") is not None
    assert ws._bar_img_cache
    sheet = ws.look.icon_sheet
    ws.look.set_icon_sheet(sheet)
    assert ws._bar_img_cache == {}
    assert ws.look.icon_sheet is sheet


def test_a_none_sheet_reverts_the_bar_to_the_glyph_fallback(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.set_icon_sheet(None)
    assert ws.look.icon_sheet is None
    assert ws._bar_image("home") is None
    assert ws._icon_image_keyed("home") is None


def test_load_icon_sheet_bakes_the_default_when_the_store_has_none(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.set_icon_sheet(None)
    ws.look.load_icon_sheet()
    assert ws.look.icon_sheet is not None
    assert ws.look.icon_sheet.TILE == 16


# -- the per-draw gate --------------------------------------------------------

def test_light_chrome_tracks_the_live_variant(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.set_theme("night", persist=False, variant="dark")
    assert ws.look.light_chrome() is False
    ws.look.set_theme_variant("light", persist=False)
    assert ws.look.light_chrome() is True


def test_light_chrome_stays_one_flat_comparison():
    """3e: five editor surfaces and the bar call this per DRAW. It may grow no
    branches, no caching and no second call -- the body is one return."""
    src = textwrap.dedent(inspect.getsource(appearance.Appearance.light_chrome))
    body = [n for n in ast.parse(src).body[0].body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    assert len(body) == 1 and isinstance(body[0], ast.Return)
    calls = [n for n in ast.walk(body[0]) if isinstance(n, ast.Call)]
    assert len(calls) == 1


def test_no_look_member_is_a_property():
    """The app-context convention, measured on this codebase: a plain hop is
    +0.5us, a property forward +5.1us."""
    for name, member in vars(appearance.Appearance).items():
        assert not isinstance(member, property), name


# -- the wiring ---------------------------------------------------------------

def test_the_boot_cascade_calls_the_collaborator_directly(tmp_path):
    """3b: the kernel calls collaborators, it does not keep forwards for its own
    use. `load_system` names `self.look` five times and `self.set_*` never."""
    src = inspect.getsource(type(build_ws(tmp_path)).load_system)
    for verb in ("set_font_scale", "select_wallpaper", "set_theme", "set_skin"):
        assert ("self.look." + verb) in src, verb
        assert ("self." + verb) not in src.replace("self.look." + verb, "")


def test_a_reboot_re_applies_every_persisted_look_setting(tmp_path):
    """End to end through the store, which is what `load_system`'s cascade is
    for -- and the only test here that would catch the cascade calling a verb
    that no longer exists."""
    ws = build_ws(tmp_path)
    ws.look.set_theme("forest", variant="light")
    ws.look.set_font_scale(2)
    ws.look.select_wallpaper("fill:indigo")
    try:
        ws.look.set_skin("outline")
        ws2 = build_ws(tmp_path)
        assert (ws2.look.theme_name, ws2.look.theme_variant) == ("forest", "light")
        assert ws2.look.font_scale == 2
        assert ws2.look.wallpaper_id == "fill:indigo"
        assert ws2.look.skin_name == "outline"
        assert ws2.theme_colors == theme_colors("forest", "light")
    finally:
        skin.use(skin.DEFAULT)


def test_the_look_is_built_before_anything_reads_it(tmp_path):
    """3c's wiring-order trap. `Appearance` is constructed in `_init_canvases`,
    because the two lines under it -- the system canvas's font scale and the
    responsive `Layout` -- already ask it for one."""
    src = ROOT.joinpath("runtime/console.py").read_text(encoding="utf-8")
    built = src.index("self.look = Appearance(")
    for reader in ("self._sys_canvas.set_font_scale(self.look.font_scale)",
                   "self.look.effective_font_scale()"):
        assert src.index(reader) > built, reader
    ws = build_ws(tmp_path)
    assert ws.look.ws is ws
