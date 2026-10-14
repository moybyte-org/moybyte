"""`Appearance` direct (#209 landing D, docs/history/console_architecture_2026-08.md).

The LOOK cluster is the one the pixel goldens cover best -- 97 whole-screen
hashes x 5 configs plus 298 sub-surface hashes are, between them, a very
thorough test of "does the shell still draw the night colorway at 1x". What
they cannot see is everything below, and it is most of what this object does:

  * **a switch moves `look_gen`.** Every cache whose pixels read the look
    folds that one integer (docs/theming_2026-09.md §6), so a switch that
    forgot to publish would leave the launcher painting the old colorway until
    something else happened to dirty it, which no single-frame hash would
    catch. tests/test_theme_look_gen.py walks every cache it reaches.

  * **the wallpaper is the theme's (D6).** Showing one coordinates the roster
    (rehydrate/re-slim, with the #66 diet's fat-cart exception) and the
    backdrop component (clear/compile); a pick is a change to the theme,
    landing in `custom`, and only a pick earns "Home Decorator".

  * **`custom` (phase 6 step 5).** The first change on a seed or a package
    makes it, a change on it re-derives from its source, a change from another
    theme replaces it, it carries its fonts and picture, and it is written
    once the changes settle or at an exit -- never per press, never idle.

  * **EDIT FILE (phase 6 step 6).** It opens custom's theme.json in the
    text app (a seed made into `custom` first); a save the loader takes is
    shown at the next frame and a hand-edited colour drops `from`; a refused
    one is badged with the loader's note and moves nothing.

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

import pytest

ROOT = Path(__file__).resolve().parent.parent

from runtime import appearance, moy_carts, ui  # noqa: E402
from runtime.chrome import THEME_NAMES, theme_colors  # noqa: E402
from runtime.theme_custom import CUSTOM  # noqa: E402
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
    """Five attributes moved and none was left as a second copy. A mirror here
    would be worse than in landing C's roster: these are written by SETTERS a
    caller can reach directly, so two copies would diverge on the first pick."""
    ws = build_ws(tmp_path)
    for name in ("theme_name", "theme_variant", "font_scale",
                 "wallpaper_id", "icon_sheet"):
        assert not hasattr(ws, name), name
        assert getattr(ws.look, name) is not None or name in ("icon_sheet",)
    src = ROOT.joinpath("runtime/console.py").read_text(encoding="utf-8")
    for gone in ("self.theme_name", "self.theme_variant",
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
    gen = ws.look.look_gen
    key = ws.bar_layer._cart_bar_key("home")
    ws.look.set_theme("berry")
    assert ws.look.theme_name == "berry"
    assert ws.theme_colors == theme_colors("berry", "dark")
    assert ws.launcher.theme is ws.theme_colors          # the selection accent
    assert ws.look.look_gen > gen                        # the look moved
    assert ws.bar_layer._cart_bar_key("home") != key     # the cached strip repaints
    assert ws._dirty
    assert _stored(ws)["theme"] == "berry"


def test_a_switch_moves_the_shelf_key_through_look_gen(tmp_path):
    """Both home layers' statics keys (one shared base, `_statics_base`) fold
    `look_gen`, and no key in the shell reads a theme dict's or an icon
    sheet's identity (docs/theming_2026-09.md §6: a freed object's id() is
    reused)."""
    ws = build_ws(tmp_path)
    for f in sorted(ROOT.joinpath("runtime").glob("*.py")):
        src = f.read_text(encoding="utf-8")
        assert "id(ws.theme_colors)" not in src and "id(ws.look." not in src, f.name
    cv = ws.sys_canvas
    ws.projects_grid = ws._home_grid("grid", [])
    before = (ws.launcher_layer._statics_key(cv), ws.projects_layer._statics_key(cv))
    ws.look.set_theme("forest", persist=False)
    assert ws.launcher_layer._statics_key(cv) != before[0]
    assert ws.projects_layer._statics_key(cv) != before[1]


def test_an_unknown_theme_falls_back_and_an_unknown_variant_too(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.set_theme("no-such-theme", persist=False)
    assert ws.look.theme_name == THEME_NAMES[0]
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
    names = [n for n in THEME_NAMES]
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
        ws.look.set_font_scale(2, persist=False)
        ws.look.select_wallpaper(None, persist=False)
        assert _stored(ws) == {}
    finally:
        ui.use_shape(None)          # process-wide state: never leak it


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


# -- wallpaper: the theme's (D6, docs/theming_2026-09.md §4.9) ---------------

def test_wallpaper_options_are_the_wallpaper_carts(tmp_path):
    ws = build_ws(tmp_path)
    opts = ws.look.wallpaper_options()
    assert opts == [ws.look.wp_id_for(c) for c in ws.look.wallpaper_carts()]
    assert not hasattr(ws.look, "FILL_WALLPAPERS")
    assert ws.look.PICTURE in opts               # My Art stands for the picture
    for wp_id in opts:
        assert ws.look.wp_cart_by_id(wp_id) is not None
    assert ws.look.wp_cart_by_id("no-such-wallpaper") is None


def test_a_seed_brings_the_default_wallpaper(tmp_path):
    """No seed names a wallpaper, so the desk shows the first wallpaper cart,
    as an absent setting did -- which is why no golden moves."""
    ws = build_ws(tmp_path)
    assert ws.look.theme_name in THEME_NAMES
    assert ws.look.wallpaper_id == ws.look.wallpaper_options()[0]
    assert ws.look.wallpaper_id != ws.look.PICTURE


def test_select_wallpaper_falls_back_on_an_unknown_id(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.select_wallpaper("no-such-wallpaper", persist=False)
    assert ws.look.wallpaper_id == ws.look.wallpaper_options()[0]
    ws.look.select_wallpaper(None, persist=False)
    assert ws.look.wallpaper_id is None          # NONE: the theme's fill


def test_select_wallpaper_rehydrates_for_the_compile_then_re_slims(tmp_path):
    """The #66 live-set diet round trip: the backdrop compile bakes src+sheet
    into the wallpaper's own namespace, so the cart is fat for exactly that
    call and slim again afterwards."""
    ws = build_ws(tmp_path)
    cart = next(c for c in ws.look.wallpaper_carts()
                if c.get("path") and ws.look.wp_id_for(c) != ws.look.PICTURE)
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
    cart = next(c for c in ws.look.wallpaper_carts()
                if c.get("path") and ws.look.wp_id_for(c) != ws.look.PICTURE)
    ws._open_workspace(cart)
    assert ws._fat_cart is cart
    ws.look.select_wallpaper(ws.look.wp_id_for(cart), persist=False)
    assert cart.get("lazy") is not True and "src" in cart


def test_none_clears_the_backdrop_and_compiles_nothing(tmp_path):
    ws = build_ws(tmp_path)
    seen = []
    ws.wallpaper.compile = lambda c: seen.append(c)
    ws.look.select_wallpaper(None, persist=False)
    assert seen == []
    assert ws.look.wallpaper_id is None


def test_only_a_pick_earns_home_decorator_and_it_lands_in_the_theme(tmp_path):
    """The boot shows the theme's wallpaper with nothing noted; a pick is a
    change to the theme, noted, and written into `custom` -- never into a
    setting of its own."""
    ws = build_ws(tmp_path)
    seen = _notes(ws)
    ws.look.select_wallpaper(None, persist=False)
    assert seen == []
    assert ws.look.select_wallpaper(None) == 0
    _settle(ws)
    assert seen == ["wallpaper_change"]
    assert ws.look.theme_name == CUSTOM and ws.look.wallpaper_id is None
    assert "wallpaper" not in _stored(ws)
    assert _package(ws)["wallpapers"] == {}


def test_cycle_wallpaper_steps_the_carts_and_none(tmp_path):
    ws = build_ws(tmp_path)
    opts = ws.look.wallpaper_options()
    ws.look.select_wallpaper(opts[-1], persist=False)
    ws.look.cycle_wallpaper(1)
    _settle(ws)
    assert ws.look.wallpaper_id is None
    ws.look.cycle_wallpaper(1)
    _settle(ws)
    assert ws.look.wallpaper_id == opts[0]
    assert _package(ws)["wallpapers"] == {"desk": opts[0]}


def test_a_stored_wallpaper_row_reads_as_nothing(tmp_path):
    """The `wallpaper` row is gone with its reader (CLAUDE.md, no store
    migrations): a store that still carries one boots the theme's."""
    ws = build_ws(tmp_path)
    moy_carts.save_system({"wallpaper": "moybyte.sakura"}, ws.carts_root)
    ws2 = build_ws(tmp_path)
    assert ws2.look.wallpaper_id == ws2.look.wallpaper_options()[0]
    assert ws2.look.wallpaper_id != "moybyte.sakura"
    src = inspect.getsource(appearance) + inspect.getsource(
        type(ws2).load_system)
    assert '"wallpaper")' not in src and "'wallpaper')" not in src


def test_picking_a_theme_brings_its_wallpaper(tmp_path):
    """The wallpaper travels with the theme: a package naming a cart shows it,
    one naming a cart that is not installed falls back to its picture, then to
    the default; switching back to a seed brings the seed's."""
    ws = build_ws(tmp_path)
    opts = ws.look.wallpaper_options()
    _store_package(ws, "sunset", {"wallpapers": {"desk": opts[-1]}})
    _store_package(ws, "gone", {"wallpapers": {"desk": "nobody.no_such"}})
    ws.look.set_theme("sunset")
    assert ws.look.wallpaper_id == opts[-1]
    ws.look.set_theme("gone")
    assert ws.look.wallpaper_id == opts[0]
    ws.look.set_theme("night")
    assert ws.look.wallpaper_id == opts[0]


def test_a_picture_is_copied_into_the_package_and_drawn(tmp_path):
    """My Art is Paint's picture: picking it copies the wallpaper copy into
    `custom` as desk.moyimg, the theme names it in `images`, and the desk
    draws it from the package."""
    ws = build_ws(tmp_path)
    blob = _blob(8, 6)
    moy_carts.save_copy(blob, ws.carts_root)
    assert ws.look.select_wallpaper(ws.look.PICTURE) == 0
    _settle(ws)
    pkg = Path(ws.carts_root) / "custom.moy"
    assert (pkg / "desk.moyimg").read_bytes() == blob
    assert _package(ws)["images"] == {"desk": "desk.moyimg"}
    assert "wallpapers" not in _package(ws)
    assert ws.look.wallpaper_id == ws.look.PICTURE
    assert ws.look.picture() == blob
    assert ws.wallpaper.draw_picture(ws.sys_canvas) is True
    # A cart picked after drops the picture from the package.
    ws.look.select_wallpaper(ws.look.wallpaper_options()[0])
    _settle(ws)
    assert not (pkg / "desk.moyimg").exists()
    assert "images" not in _package(ws)


def test_no_picture_yet_refuses_the_pick(tmp_path):
    ws = build_ws(tmp_path)
    assert ws.look.select_wallpaper(ws.look.PICTURE) == ws.look.NO_FILE
    assert ws.look.theme_name == "night"


# -- custom: a change to the theme (phase 6 step 5) -----------------------------

def _settle(ws, dt=0.0):
    """One frame's apply, then the debounce run out: the change shown and
    written, as a kid's would be once their hands leave the keys."""
    ws.look.tick(dt)
    ws.look.tick(ws.look.AUTOSAVE_S)


def _package(ws, name=CUSTOM):
    import json
    return json.loads((Path(ws.carts_root) / (name + ".moy") / "theme.json").read_bytes())


def _look(ws):
    import json
    return json.loads((Path(ws.carts_root) / "custom.moy" / "manifest.json")
                      .read_bytes())["moybyte"]["look"]


def _store_package(ws, name, keys):
    import json
    d = Path(ws.carts_root) / (name + ".moy")
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": name.title(), "runtime": "theme",
         "main": "theme.json", "moybyte": {"type": "theme"}}))
    pkg = {"format": 1, "name": name, "colors": {"dark": {"panel": 2}}}
    pkg.update(keys)
    (d / "theme.json").write_text(json.dumps(pkg))
    ws.carts.rescan()
    return d


def _blob(w, h):
    b = moy_carts.encode_image(w, h, bytes(i % 60 for i in range(w * h)))
    return b.encode() if isinstance(b, str) else b


def _accent(i):
    return ui.theme_accents()[i][1]


def _writes(ws, monkeypatch):
    """Count the store's writes: custom's package and system.json."""
    n = {"package": 0, "system": 0}
    real_pkg, real_sys = moy_carts.save_package, moy_carts.save_system

    def pkg(*a):
        n["package"] += 1
        return real_pkg(*a)

    def sys_(*a):
        n["system"] += 1
        return real_sys(*a)
    monkeypatch.setattr(moy_carts, "save_package", pkg)
    monkeypatch.setattr(moy_carts, "save_system", sys_)
    return n


def test_the_first_change_on_a_seed_makes_custom_and_switches_to_it(tmp_path):
    ws = build_ws(tmp_path)
    night = theme_colors("night")
    assert ws.look.change("accent", _accent(3)) == 0
    assert ws.look.theme_name == "night"          # shown at the next frame
    ws.look.tick(0.0)
    assert ws.look.theme_name == CUSTOM == ui.theme_installed()
    assert ws.theme_colors["accent"] == _accent(3)
    assert theme_colors("night") == night, "the seed moved"
    assert not (Path(ws.carts_root) / "custom.moy").exists(), "written per change"
    ws.look.tick(ws.look.AUTOSAVE_S)
    assert _look(ws) == {"from": "night", "accent": _accent(3)}
    assert _package(ws)["name"] == CUSTOM and "inherits" not in _package(ws)
    assert CUSTOM in ws.look.theme_names()
    assert _stored(ws)["theme"] == CUSTOM
    ws2 = build_ws(tmp_path)
    assert ws2.look.theme_name == CUSTOM
    assert ws2.theme_colors["accent"] == _accent(3)


def test_theme_mode_and_chrome_size_are_no_change(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.look.set_theme("forest")
    ws.look.set_theme_variant("light")
    scales = ws.chrome_scales
    if len(scales) > 1:
        ws.look.set_chrome_scale(scales[-1])
    _settle(ws)
    assert ws.look.theme_name == "forest"
    assert not (Path(ws.carts_root) / "custom.moy").exists()
    assert ws.look._cust is None


def test_a_change_on_custom_edits_it_in_place_from_its_source(tmp_path):
    """Each change re-derives from `from`'s rows, never from the last result:
    a tint quantises, and quantising a quantised row drifts."""
    ws = build_ws(tmp_path)
    ws.look.change("tint", "plum")
    _settle(ws)
    once = _package(ws)
    ws.look.change("tint", "sand")
    ws.look.change("tint", "plum")
    _settle(ws)
    assert _package(ws) == once
    assert _look(ws) == {"from": "night", "tint": "plum"}
    rc, want = ui.theme_custom("night", None, "plum")
    assert rc == 0 and _package(ws) == __import__("json").loads(want)


def test_a_change_from_another_theme_replaces_custom(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(1))
    _settle(ws)
    pkg = Path(ws.carts_root) / "custom.moy"
    assert (pkg / "tiny5.fnt").exists()            # night names its fonts
    ws.look.set_theme("forest")
    ws.look.change("tint", "grey")
    _settle(ws)
    assert _look(ws) == {"from": "forest", "tint": "grey"}
    assert ws.theme_colors["accent"] == theme_colors("forest")["accent"]
    # forest takes its fonts through the default seed, as custom does now:
    # nothing of the custom before it is left in the folder.
    assert "fonts" not in _package(ws) and not (pkg / "tiny5.fnt").exists()
    assert ui.theme_fonts(CUSTOM) == ui.theme_fonts("forest")


def test_custom_carries_the_font_files_its_fonts_name(tmp_path):
    """A theme's fonts come only from its own folder (§4.7): every file
    custom's `fonts` names is copied in with its licence -- a seed's from the
    image, a package's from its folder -- and a file it stops naming goes."""
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(2))
    _settle(ws)
    pkg = Path(ws.carts_root) / "custom.moy"
    names = {f for _r, f in ui.theme_fonts("night")}
    assert names and _package(ws)["fonts"]
    for f in names:
        assert (pkg / f).read_bytes() == ui.font_file(f)
        lic = f[:-4] + ".txt"
        assert (pkg / lic).read_bytes() == ui.font_file(lic)
    d = _store_package(ws, "pixel", {"fonts": {"ui": "pixel.fnt"}})
    (d / "pixel.fnt").write_bytes(ui.font_file("tiny5.fnt"))
    ws.look.set_theme("pixel")
    ws.look.change("tint", "sand")
    _settle(ws)
    assert (pkg / "pixel.fnt").exists() and not (pkg / "tiny5.fnt").exists()
    ws.look.change("font", {"ui": "tiny5.fnt", "title": "tiny5.fnt"})
    _settle(ws)
    assert not (pkg / "pixel.fnt").exists()
    assert (pkg / "tiny5.fnt").exists() and (pkg / "tiny5.txt").exists()
    assert _package(ws)["fonts"] == {"ui": "tiny5.fnt", "title": "tiny5.fnt"}
    assert ws.look.change("font", {"ui": "nowhere.fnt"}) == ws.look.NO_FILE


def test_a_change_from_a_store_package_copies_its_files(tmp_path):
    from runtime.theme_custom import PICTURE_FILE
    ws = build_ws(tmp_path)
    d = _store_package(ws, "sunset", {"fonts": {"ui": "tiny5.fnt"},
                                      "images": {"desk": PICTURE_FILE}})
    blob = _blob(4, 4)
    (d / "tiny5.fnt").write_bytes(ui.font_file("tiny5.fnt"))
    (d / PICTURE_FILE).write_bytes(blob)
    ws.look.set_theme("sunset")
    assert ws.look.wallpaper_id == ws.look.PICTURE
    ws.look.change("accent", _accent(5))
    _settle(ws)
    pkg = Path(ws.carts_root) / "custom.moy"
    assert (pkg / "tiny5.fnt").exists() and (pkg / PICTURE_FILE).read_bytes() == blob
    assert _look(ws) == {"from": "sunset", "accent": _accent(5)}
    assert (d / "theme.json").exists(), "the source is never written"
    assert ws.look.wallpaper_id == ws.look.PICTURE


def test_ten_changes_land_where_one_would(tmp_path, monkeypatch):
    """A held key: a change per press, one install per frame, no store write
    while it is held, and exactly one once it settles."""
    ws = build_ws(tmp_path)
    n = _writes(ws, monkeypatch)
    installs = []
    real = ui.theme_install
    monkeypatch.setattr(ui, "theme_install", lambda b: installs.append(1) or real(b))
    for i in range(10):
        ws.look.change("accent", _accent(i))
        ws.look.change("accent", _accent(i + 1))
        ws.look.tick(1.0 / 30)
    assert len(installs) == 10, "one install a frame"
    assert n == {"package": 0, "system": 0}, "a write while held"
    for _ in range(int(ws.look.AUTOSAVE_S * 30) + 2):
        ws.look.tick(1.0 / 30)
    assert n["package"] == 1 and n["system"] == 1
    assert _look(ws)["accent"] == _accent(10)


def test_idle_frames_write_nothing(tmp_path, monkeypatch):
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(4))
    _settle(ws)
    for _ in range(100):
        ws.frame(1.0 / 30)                     # the wallpaper guard heals
    n = _writes(ws, monkeypatch)
    for _ in range(200):
        ws.frame(1.0 / 30)
    assert n == {"package": 0, "system": 0}


def test_an_exit_writes_a_change_still_settling(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(6))
    ws.look.tick(0.0)
    assert not (Path(ws.carts_root) / "custom.moy").exists()
    ws.go_home()
    assert _look(ws)["accent"] == _accent(6)


def test_a_theme_switch_writes_custom_before_it_leaves(tmp_path):
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(7))
    ws.look.tick(0.0)
    ws.look.set_theme("berry")
    assert _look(ws)["accent"] == _accent(7)
    ws.look.set_theme(CUSTOM)
    assert ws.theme_colors["accent"] == _accent(7)
    assert ws.look.custom_look() == {"from": "night", "accent": _accent(7)}


# -- EDIT FILE (phase 6 step 6) -----------------------------------------------

def _edit(ws):
    """EDIT FILE, and the frame the text app opens its document on: the
    handle over custom's theme.json."""
    assert ws.look.edit_file()
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.wm.top_is_player() and ws.cart.get("title") == "Notes"
    return ws.player.ns["ed"]


def _hand_edit(ed, **dark):
    import json
    d = json.loads(ed.text())
    d["colors"]["dark"].update(dark)
    ed.set_text(json.dumps(d, indent=2))
    return d


def _custom_files(ws):
    d = Path(ws.carts_root) / "custom.moy"
    return {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}


def test_edit_file_on_a_seed_makes_custom_and_opens_its_file(tmp_path):
    """A seed is firmware: EDIT FILE copies it to `custom`, switches to it
    and opens custom's theme.json -- in the text app, not as a project."""
    import json
    ws = build_ws(tmp_path)
    ws.look.set_theme("forest")
    forest = theme_colors("forest")
    ed = _edit(ws)
    assert ws.look.theme_name == CUSTOM == ui.theme_installed()
    assert theme_colors("forest") == forest, "the seed moved"
    assert _look(ws) == {"from": "forest"}
    assert _stored(ws)["theme"] == CUSTOM
    assert (ed.name(), ed.mode()) == ("theme.json", "json")
    assert ed.kind == moy_carts.project_kind(str(Path(ws.carts_root) / "custom.moy"))
    assert json.loads(ed.text()) == _package(ws)
    assert ws.project.cart is not None and ws.project.cart.get("title") == "Notes"
    ws._exit_to_caller()
    assert ws.wm.top_kind() != "menu", "the package opened as a project"


def test_edit_file_on_custom_writes_its_change_first(tmp_path):
    import json
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(5))
    ws.look.tick(0.0)
    ed = _edit(ws)
    assert _look(ws) == {"from": "night", "accent": _accent(5)}
    assert json.loads(ed.text())["colors"]["dark"]["accent"] == _accent(5)


def test_a_saved_role_moves_the_look_and_drops_from(tmp_path):
    """A colour saved by hand is custom from then on: shown at the next
    frame, `from` (and the accent and tint with it) dropped, and a later
    TINT derived from the file as saved, not from the old source."""
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(2))
    ws.look.tick(0.0)
    ed = _edit(ws)
    panel = (ws.theme_colors["panel"] + 5) % 64
    _hand_edit(ed, panel=panel)
    gen = ws.look.look_gen
    assert ed.save(soft=True) == (True, "")
    ws.frame(1 / 30)
    assert ws.look.look_gen > gen
    assert ws.theme_colors["panel"] == panel
    assert _look(ws) == {}
    saved = (Path(ws.carts_root) / "custom.moy" / "theme.json").read_bytes()
    assert saved == ed.text().encode()
    assert ws.look.custom_look() == {}

    ws._exit_to_caller()
    assert ws.look.change("tint", "plum") == 0
    _settle(ws)
    rc, want = ui.theme_custom(saved, None, "plum")
    assert rc == 0 and _package(ws) == __import__("json").loads(want)
    assert _look(ws) == {"tint": "plum"}


def test_a_save_of_a_key_over_the_source_keeps_it(tmp_path):
    """`frame`, `fonts`, `wallpapers` and `images` are set over the source,
    so a hand edit of one alone keeps `from`, and a later ACCENT keeps it."""
    import json
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(9))
    ws.look.tick(0.0)
    ed = _edit(ws)
    d = json.loads(ed.text())
    d["frame"] = {"style": "platinum"}
    ed.set_text(json.dumps(d, indent=2))
    assert ed.save(soft=True)[0]
    ws.frame(1 / 30)
    assert _look(ws) == {"from": "night", "accent": _accent(9)}
    ws._exit_to_caller()
    assert ws.look.change("accent", _accent(1)) == 0
    _settle(ws)
    assert _package(ws)["frame"] == {"style": "platinum"}
    assert _look(ws) == {"from": "night", "accent": _accent(1)}


def test_a_save_of_a_metric_drops_the_source(tmp_path):
    import json
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(9))
    ws.look.tick(0.0)
    ed = _edit(ws)
    d = json.loads(ed.text())
    d.setdefault("metrics", {})["radius"] = 3
    ed.set_text(json.dumps(d, indent=2))
    assert ed.save(soft=True)[0]
    ws.frame(1 / 30)
    assert _look(ws) == {}


def test_a_file_over_the_cap_as_typed_is_written_minified(tmp_path):
    import json
    ws = build_ws(tmp_path)
    ed = _edit(ws)
    d = json.loads(ed.text())
    ed.set_text(json.dumps(d, indent=6))
    assert 4096 < len(ed.text()) < 8000         # the handle's MAX_CHARS
    assert ed.save(soft=True) == (True, "")
    saved = (Path(ws.carts_root) / "custom.moy" / "theme.json").read_bytes()
    assert len(saved) <= 4096 and json.loads(saved) == d


def _big(d):
    d["notes"] = "x" * 4200
    return d


def _inherits(d):
    d["inherits"] = "night"
    return d


def _renamed(d):
    d["name"] = "mine"
    return d


@pytest.mark.parametrize("edit,note", [
    (None, None),
    (_big, "theme.why_big"),
    (_inherits, "theme.why_chain"),
    (_renamed, "theme.why_name"),
], ids=["bad-json", "over-4096", "inherits", "bad-name"])
def test_a_refused_save_shows_the_note_and_changes_nothing(tmp_path, edit, note):
    """The soft save refuses and badges; the hard exit writes the text as
    typed (#154) and still leaves the running look, its `custom` and the
    manifest as they were."""
    import json
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(4))
    ws.look.tick(0.0)
    ed = _edit(ws)
    before = _custom_files(ws)
    colors, gen = dict(ws.theme_colors), ws.look.look_gen
    held = ws.look._cust.theme
    text = "{" + ed.text() if edit is None else json.dumps(edit(json.loads(ed.text())))
    ed.set_text(text)
    ok, badge = ed.save(soft=True)
    assert not ok and badge.startswith("INVALID ")
    if note is not None:
        assert badge == "INVALID " + ui.s(note)
    assert ed.badge() == badge
    ws.frame(1 / 30)
    assert _custom_files(ws) == before
    assert ed.save() == (True, badge)            # the hard exit writes it
    ws.frame(1 / 30)
    after = _custom_files(ws)
    assert after["theme.json"] == text.encode()
    assert after["manifest.json"] == before["manifest.json"]
    assert ws.theme_colors == colors and ws.look.look_gen == gen
    assert ws.look._cust.theme == held and ui.theme_installed() == CUSTOM


def test_an_unreadable_pair_is_saved_and_noted(tmp_path):
    """A pair under the contrast floor never refuses (§7): the save lands
    and the badge says so."""
    ws = build_ws(tmp_path)
    ed = _edit(ws)
    ink = ws.theme_colors["panel"]
    _hand_edit(ed, ink=ink)
    assert ed.save(soft=True) == (True, appearance.UNREADABLE)
    ws.frame(1 / 30)
    assert ws.theme_colors["ink"] == ink


def test_idle_frames_after_a_hand_edit_write_nothing(tmp_path, monkeypatch):
    ws = build_ws(tmp_path)
    ed = _edit(ws)
    _hand_edit(ed, panel=(ws.theme_colors["panel"] + 3) % 64)
    assert ed.save(soft=True)[0]
    for _ in range(100):
        ws.frame(1 / 30)                     # the wallpaper guard heals
    n = _writes(ws, monkeypatch)
    for _ in range(200):
        ws.frame(1 / 30)
    assert n == {"package": 0, "system": 0}


# -- the icon sheet -----------------------------------------------------------

# -- NAME… (phase 6 step 8) -----------------------------------------------------

def _named(ws, name, accent=6):
    """`custom` made from night with one accent, then NAME… `name`."""
    assert ws.look.change("accent", _accent(accent)) == 0
    ws.look.tick(0.0)
    assert ws.look.name_custom(name) is None
    return Path(ws.carts_root) / (name + ".moy")


def test_name_renames_custom_into_a_theme_of_its_own(tmp_path):
    import json
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(6))
    ws.look.tick(0.0)
    colors = dict(ws.theme_colors)
    d = _named(ws, "bubblegum")
    assert not (Path(ws.carts_root) / "custom.moy").exists()
    assert _package(ws, "bubblegum")["name"] == "bubblegum"
    man = json.loads((d / "manifest.json").read_bytes())
    assert man["moybyte"] == {"type": "theme",
                              "look": {"from": "night", "accent": _accent(6)}}
    assert man["title"] == "bubblegum"
    assert ws.look.theme_name == "bubblegum" == ui.theme_installed()
    assert ws.theme_colors == colors, "naming moved the look"
    assert _stored(ws)["theme"] == "bubblegum"
    names = ws.look.theme_names()
    assert "bubblegum" in names and CUSTOM not in names
    paths = [c.get("path") for c in ws.carts.all]
    assert str(d) in paths and not any(
        str(p).endswith("/custom.moy") for p in paths)
    ch = ws.look.choices()
    assert (ch["theme"], ch["store"], ch["from"]) == ("bubblegum", True, None)


def test_a_change_on_a_named_theme_makes_a_new_custom(tmp_path):
    ws = build_ws(tmp_path)
    _named(ws, "bubblegum")
    assert ws.look.change("tint", "plum") == 0
    _settle(ws)
    assert ws.look.theme_name == CUSTOM
    assert _look(ws) == {"from": "bubblegum", "tint": "plum"}
    assert _package(ws, "bubblegum")["name"] == "bubblegum"
    assert set(ws.look.theme_names()) >= {"bubblegum", CUSTOM}


def test_a_named_theme_survives_a_reboot(tmp_path):
    ws = build_ws(tmp_path)
    _named(ws, "bubblegum")
    colors = dict(ws.theme_colors)
    ws2 = build_ws(tmp_path)
    assert ws2.look.theme_name == "bubblegum"
    assert ws2.theme_colors == colors


@pytest.mark.parametrize("name,why", [
    ("", "grammar"), ("Bubble", "grammar"), ("_x", "grammar"),
    ("a" * 25, "grammar"), ("a b", "grammar"), ("night", "seed"),
    ("custom", "custom"), ("moybyte.calc", "grammar")])
def test_a_name_the_grammar_or_a_seed_refuses(tmp_path, name, why):
    ws = build_ws(tmp_path)
    ws.look.change("accent", _accent(3))
    _settle(ws)
    before = _package(ws)
    assert ws.look.name_custom(name) == why
    assert ws.look.theme_name == CUSTOM and _package(ws) == before


def test_a_cart_folders_stem_refuses(tmp_path):
    import json
    ws = build_ws(tmp_path)
    d = Path(ws.carts_root) / "pong.moy"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": "Pong", "main": "main.py"}))
    (d / "main.py").write_text("def _draw():\n    pass\n")
    ws.carts.rescan()
    ws.look.change("accent", _accent(3))
    _settle(ws)
    assert ws.look.name_custom("pong") == "cart"
    assert ws.look.theme_name == CUSTOM


def test_a_name_off_custom_is_refused(tmp_path):
    ws = build_ws(tmp_path)
    assert ws.look.name_custom("bubblegum") == "none"


def test_an_installed_name_arms_replace(tmp_path):
    ws = build_ws(tmp_path)
    _store_package(ws, "sunset", {})
    ws.look.change("accent", _accent(9))
    _settle(ws)
    assert ws.look.name_check("sunset") == "taken"
    assert ws.look.name_custom("sunset") == "taken"
    assert _package(ws, "sunset")["colors"] == {"dark": {"panel": 2}}
    assert ws.look.name_custom("sunset", replace=True) is None
    assert ws.look.theme_name == "sunset"
    assert ws.theme_colors["accent"] == _accent(9)
    assert _package(ws, "sunset")["name"] == "sunset"
    assert not (Path(ws.carts_root) / "custom.moy").exists()
    assert [c for c in ws.carts.all
            if str(c.get("path")).endswith("/sunset.moy")].__len__() == 1


def test_a_change_on_a_named_theme_kept_by_replace(tmp_path):
    """The owner's ruling: a named theme copies like any other, and NAME…
    -> REPLACE keeps the change under its name."""
    ws = build_ws(tmp_path)
    _named(ws, "bubblegum")
    assert ws.look.change("tint", "plum") == 0
    _settle(ws)
    tinted = dict(ws.theme_colors)
    assert ws.look.name_custom("bubblegum", replace=True) is None
    assert ws.look.theme_name == "bubblegum"
    assert ws.theme_colors == tinted


def test_named_fonts_and_picture_travel_with_the_folder(tmp_path):
    ws = build_ws(tmp_path)
    assert ws.look.change("font", ws.look.preset("font", "tiny5")) == 0
    _settle(ws)
    d = _named(ws, "tiny")
    assert (d / "tiny5.fnt").exists()
    assert ws.look.choices()["font"] == "tiny5"


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
    use. `load_system` names `self.look` and `self.set_*` never."""
    src = inspect.getsource(type(build_ws(tmp_path)).load_system)
    for verb in ("set_font_scale", "set_theme"):
        assert ("self.look." + verb) in src, verb
        assert ("self." + verb) not in src.replace("self.look." + verb, "")


def test_a_reboot_re_applies_every_persisted_look_setting(tmp_path):
    """End to end through the store, which is what `load_system`'s cascade is
    for -- and the only test here that would catch the cascade calling a verb
    that no longer exists."""
    ws = build_ws(tmp_path)
    ws.look.set_theme("forest", variant="light")
    ws.look.set_font_scale(2)
    try:
        ws2 = build_ws(tmp_path)
        assert (ws2.look.theme_name, ws2.look.theme_variant) == ("forest", "light")
        assert ws2.look.font_scale == 2
        assert ws2.look.wallpaper_id == ws2.look.wallpaper_options()[0]
        assert ws2.theme_colors == theme_colors("forest", "light")
    finally:
        ui.use_shape(None)


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


def test_an_archived_theme_imports_under_its_own_name(tmp_path):
    """The store's archive import lands a theme as `<name>.moy` (the loader
    takes it from no other folder), a taken name as the next `_2` stem with
    theme.json's `name` rewritten: the browser's rule (moy_store.mjs)."""
    import json
    ws = build_ws(tmp_path)
    d = _named(ws, "bubblegum")
    arc = str(tmp_path / "bubblegum.moy.zip")
    moy_carts.pack(str(d), arc)
    other = tmp_path / "other"
    (other / "carts").mkdir(parents=True)
    got = moy_carts.import_archive(arc, str(other / "carts"))
    assert got.endswith("/bubblegum.moy")
    assert json.loads(Path(got, "theme.json").read_bytes())["name"] == "bubblegum"
    again = moy_carts.import_archive(arc, str(other / "carts"))
    assert again.endswith("/bubblegum_2.moy")
    assert json.loads(Path(again, "theme.json").read_bytes())["name"] == "bubblegum_2"
    assert ui.theme_resolve(Path(again, "theme.json").read_bytes())[0] == 0
