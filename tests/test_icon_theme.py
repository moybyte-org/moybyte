"""Stage 2 of the themeable top bar: a kid repaints the SYSTEM icon sheet in the
PAINT editor (Settings -> EDIT ICONS) and it PERSISTS, re-theming the bar live.

Driven through the SAME shared console the device runs (runtime.host_app +
ConsoleDriver: mouse == touch, arrows == trackball), so these assert host==device
behavior. The theme save uses the exact _with_sd wrapper the cart sprite save uses
(host: direct write; device: with_sd_live), so what passes here is what the device runs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)


from ws_helpers import build_ws as _build_ws, install_theme, use_theme


def _ws(tmp_path, **kw):
    """A workstation on the lookless `night` fixture (tests/fixtures/themes/)
    in its dark mode: the bar's mechanics are pinned on the pixel IconSheet
    and the pixel bar; a test of a look sets its theme."""
    ws = _build_ws(tmp_path, **kw)
    use_theme(ws, "night", "dark")
    return ws


def _center(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def _icons_row_index(ws):
    return [r[0] for r in ws.settings_layer._SETTINGS_ROWS].index("icons")


def _click_icons_row(ws, drv):
    """Focus the EDIT ICONS row (the page scrolls it into view, as a kid's
    d-pad does) and activate it."""
    ws.settings_layer.set_msel = _icons_row_index(ws)
    drv.frame(1 / 30)
    drv.press("a")


# -- the Settings entry point opens the PAINT editor on the icon sheet -------

def test_settings_has_edit_icons_action_row(tmp_path):
    ws = _ws(tmp_path)
    rows = ws.settings_layer._SETTINGS_ROWS
    icons = [r for r in rows if r[0] == "icons"]
    assert icons, "Settings must have an EDIT ICONS row"
    key, label, kind = icons[0]
    assert label == "EDIT ICONS"
    assert kind == "action"


def test_edit_icons_opens_paint_on_the_icon_sheet(tmp_path):
    """Tapping the EDIT ICONS row opens the PAINT editor targeting ws.look.icon_sheet
    (not a cart sheet), with the editing-icons flag set."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    _click_icons_row(ws, drv)
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "theme"
    assert ws._editing_icons is True
    assert ws.paint is not None
    assert ws.paint.sheet is ws.look.icon_sheet         # editing the SYSTEM theme, not a cart


def test_edit_icons_reachable_by_keyboard_a(tmp_path):
    """A (or RUN) on the EDIT ICONS row opens the theme editor too (device d-pad)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    ws.settings_layer.set_msel = _icons_row_index(ws)
    drv.frame(1 / 30)                 # the page draws, the row focused
    drv.press("a")
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "theme"


# -- paint a pixel + a hard commit persists + round-trips + re-themes the bar --
#
# There is no SAVE button (#111): ws.look.save_icons() is the hard-commit verb every
# real exit path (CLOSE/leave, a window/context-X, going home) now dispatches to
# automatically -- these tests call it directly to verify the persistence
# mechanism itself; test_close_auto_commits_the_icon_edit below (and the CLOSE/
# B-key/HOME tests further down) prove the AUTOMATIC dispatch.

def test_paint_and_save_persists_and_round_trips(tmp_path):
    """Paint a pixel in the theme editor, commit -> system_icons.moygfx exists,
    loads back non-None, round-trips the edit, and ws.look.icon_sheet reflects it."""
    from runtime import editors as _editors, host_app, console as C, moy_carts
    ws = _ws(tmp_path)
    carts_dir = ws.carts_root
    assert moy_carts.load_system_icons(carts_dir) is None     # nothing saved yet
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    pe = ws.paint
    pe.n = 0
    pe.color = 9                                              # a color that isn't index 0
    before = ws.look.icon_sheet.pget(0, 0)
    # Tap the top-left grid cell to paint pixel (0,0) of tile 0.
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    assert ws.look.icon_sheet.pget(0, 0) == 9 and before != 9      # painted in-RAM
    ws.look.save_icons()
    assert ws.cart_error is None       # invisible save: no failure
    # Persisted: the file now exists, loads non-None, and the edit round-trips.
    hexs = moy_carts.load_system_icons(carts_dir)
    assert hexs is not None
    reloaded = _editors.IconSheet.from_hex(hexs)
    assert reloaded.pget(0, 0) == 9
    # ws.look.icon_sheet reflects the edit and serializes identically to what was saved.
    assert ws.look.icon_sheet.to_hex() == hexs


def test_save_invalidates_bar_cache_and_bar_still_draws(tmp_path):
    """A theme commit drops the per-kind bar image cache so the next bar draw
    rebuilds its sprites from the new pixels -- and the next frame doesn't crash."""
    from runtime import host_app, console as C
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    # Warm the bar image cache (the running-cart bar draws icons via _bar_image).
    ws._bar_image("home")
    assert ("home", False) in ws._bar_img_cache   # (kind, light-chrome) key
    ws.paint.color = 11
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    ws.look.save_icons()
    assert ws._bar_img_cache == {}                            # cache invalidated by save
    # Back to a desktop bar so the changed pixels are actually re-blit; no crash.
    drv.click(*_center(ws.paint_layer.layout.close_btn))
    drv.frame(1 / 30)
    assert ws.screen == "settings"
    drv.frame(1 / 30)
    from runtime import host_app as _h  # noqa: F401
    assert probe.distinct_pixels_in(drv.rgb888(), 3) > 4                         # the Settings bar renders


def test_close_auto_commits_the_icon_edit(tmp_path):
    """#111 regression: CLOSE hard-commits the icon sheet with NO explicit SAVE
    call -- ThemeLayer.leave() persists on the way out. Paint a pixel, tap CLOSE
    immediately (no save_icons() call), and the edit must already be on disk."""
    from runtime import editors as _editors, host_app, console as C, moy_carts
    ws = _ws(tmp_path)
    carts_dir = ws.carts_root
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    ws.paint.n = 0
    ws.paint.color = 13
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    assert ws.look.icon_sheet.pget(0, 0) == 13                      # painted in-RAM only
    drv.click(*_center(ws.paint_layer.layout.close_btn))                        # CLOSE, no SAVE tap
    drv.frame(1 / 30)
    assert ws.screen == "settings"
    hexs = moy_carts.load_system_icons(carts_dir)
    assert hexs is not None, "CLOSE must hard-commit the icon edit (#111)"
    assert _editors.IconSheet.from_hex(hexs).pget(0, 0) == 13


# -- BACK returns to Settings (not a cart) ----------------------------------

def test_close_returns_to_settings(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    _click_icons_row(ws, drv)
    drv.frame(1 / 30)
    assert ws.screen == "menu" and ws.menu_view == "theme"
    drv.click(*_center(ws.paint_layer.layout.close_btn))
    drv.frame(1 / 30)
    assert ws.screen == "settings"                            # back to Settings, not a cart
    assert ws._editing_icons is False
    assert ws.paint is None


def test_back_key_returns_to_settings(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    drv.press("b")
    drv.frame(1 / 30)
    assert ws.screen == "settings"


def test_home_key_from_theme_clears_editing_flag(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    drv.press("home")
    drv.frame(1 / 30)
    assert ws.screen == "launcher"
    assert ws._editing_icons is False


def test_home_key_from_theme_auto_commits_the_icon_edit(tmp_path):
    """#111 regression: HOME bypasses ThemeLayer.leave() entirely (it goes
    straight to Workstation.go_home -- see _leave_or_home), so go_home() itself
    must hard-commit a dirty icon sheet, or a HOME tap right after painting
    would silently lose the edit."""
    from runtime import editors as _editors, host_app, console as C, moy_carts
    ws = _ws(tmp_path)
    carts_dir = ws.carts_root
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    ws.paint.n = 0
    ws.paint.color = 5
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    assert ws.look.icon_sheet.pget(0, 0) == 5                       # painted in-RAM only
    drv.press("home")                                          # no CLOSE, no save_icons()
    drv.frame(1 / 30)
    assert ws.screen == "launcher"
    hexs = moy_carts.load_system_icons(carts_dir)
    assert hexs is not None, "HOME must hard-commit the icon edit (#111)"
    assert _editors.IconSheet.from_hex(hexs).pget(0, 0) == 5


# -- the theme editor must NOT touch a cart's sheet --------------------------

def test_editing_icons_does_not_modify_a_cart_sheet(tmp_path):
    """Editing the system icon sheet leaves every cart's own sprites.moygfx untouched
    (the theme editor is a parallel PaintEditor target, not the cart's)."""
    from runtime import host_app, console as C, moy_carts
    ws = _ws(tmp_path)
    carts_dir = ws.carts_root
    # Snapshot each on-disk cart sheet before theming.
    before = {}
    for c in moy_carts.scan(carts_dir):
        before[c["path"]] = c.get("sprites")
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    ws.paint.color = 7
    drv.click(C._PG_X0 + 1, C._PG_Y0 + 1)
    drv.frame(1 / 30)
    ws.look.save_icons()
    # No cart sheet changed on disk.
    for c in moy_carts.scan(carts_dir):
        assert c.get("sprites") == before.get(c["path"]), c["path"]
    # And the system theme file is what got written instead.
    assert moy_carts.load_system_icons(carts_dir) is not None


def test_cart_paint_flow_still_targets_the_cart_sheet(tmp_path):
    """Don't-regress: opening PAINT for a running cart still edits the CART sheet
    (editing-icons flag stays off, paint.sheet is the cart sheet)."""
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.launcher.sel = 0
    ws.open()
    ws._open_paint()
    drv.frame(1 / 30)
    assert ws.menu_view == "paint"
    assert ws._editing_icons is False
    assert ws.paint.sheet is ws.sheet                        # the cart's own sheet


# -- the theme editor starts from the current (default) theme ----------------

def test_theme_editor_starts_from_default_when_no_file(tmp_path):
    """With no system_icons.moygfx yet, the editor opens on the baked default IconSheet
    (a real 16x16 themeable sheet), and the first save creates the file."""
    from runtime import host_app, moy_carts
    ws = _ws(tmp_path)
    assert moy_carts.load_system_icons(ws.carts_root) is None
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    assert ws.paint.sheet.TILE == 16 and ws.paint.sheet.count == 32
    assert not ws.paint.sheet.is_blank()                     # the baked default is painted


# -- per-icon reset-to-default (#90) -----------------------------------------
#
# The icon editor's RESET control repurposes the GET button's slot: the shared
# sprite sheet GET/PUT have no meaning on a 16x16 icon theme (they're hidden
# there, see _draw_paint), so that freed rect becomes the one icon-only control.

def test_reset_restores_the_selected_slot_to_its_default(tmp_path):
    from runtime import chrome, host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    n = chrome._ICON["home"]
    ws.paint.n = n
    ox, oy = ws.look.icon_sheet.tile_origin(n)
    default_tl = chrome.default_icon_pixels(n)[0]
    ws.paint.color = (default_tl + 1) % 16 or 1          # guaranteed to differ
    ws.paint.paint(0, 0)                                 # vandalize the top-left pixel
    assert ws.look.icon_sheet.pget(ox, oy) != default_tl
    drv.click(*_center(ws.paint_layer.layout.get_btn))                    # RESET, in icon-editing mode
    drv.frame(1 / 30)
    assert ws.look.icon_sheet.pget(ox, oy) == default_tl


def test_reset_is_undoable(tmp_path):
    from runtime import chrome, host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    n = chrome._ICON["home"]
    ws.paint.n = n
    ox, oy = ws.look.icon_sheet.tile_origin(n)
    default_tl = chrome.default_icon_pixels(n)[0]
    vandal = (default_tl + 1) % 16 or 1
    ws.paint.color = vandal
    ws.paint.paint(0, 0)
    drv.click(*_center(ws.paint_layer.layout.get_btn))
    drv.frame(1 / 30)
    assert ws.look.icon_sheet.pget(ox, oy) == default_tl
    assert ws.paint.undo() is True
    assert ws.look.icon_sheet.pget(ox, oy) == vandal     # back to the vandalized pixel


def test_reset_leaves_other_slots_alone(tmp_path):
    from runtime import chrome, host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    home_n = chrome._ICON["home"]
    edit_n = chrome._ICON["edit"]
    sheet = ws.look.icon_sheet
    hx, hy = sheet.tile_origin(home_n)
    ex, ey = sheet.tile_origin(edit_n)
    sheet.pset(hx, hy, 9)
    sheet.pset(ex, ey, 9)                                 # vandalize BOTH slots
    ws.paint.n = home_n
    ws.paint.reset_tile(home_n, chrome.default_icon_pixels(home_n))
    assert sheet.pget(hx, hy) == chrome.default_icon_pixels(home_n)[0]
    assert sheet.pget(ex, ey) == 9                        # the OTHER slot is untouched


def test_reset_persists_through_save_icons(tmp_path):
    from runtime import chrome, editors as _editors, host_app, moy_carts
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    n = chrome._ICON["home"]
    ws.paint.n = n
    sheet = ws.look.icon_sheet
    ox, oy = sheet.tile_origin(n)
    sheet.pset(ox, oy, 9)
    ws.paint.reset_tile(n, chrome.default_icon_pixels(n))
    ws.look.save_icons()                                  # the same hard-commit path
    hexs = moy_carts.load_system_icons(ws.carts_root)
    reloaded = _editors.IconSheet.from_hex(hexs)
    assert reloaded.pget(ox, oy) == chrome.default_icon_pixels(n)[0]


def test_reset_on_an_already_default_slot_is_a_noop(tmp_path):
    from runtime import chrome, host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    ws.open_theme()
    drv.frame(1 / 30)
    n = chrome._ICON["home"]
    ws.paint.n = n
    ws.paint.reset_tile(n, chrome.default_icon_pixels(n))  # already default
    assert ws.paint.can_undo() is False


# --- the icon sets (docs/look_2026-10.md step 16) ---------------------------------

import importlib  # noqa: E402
import json  # noqa: E402
import struct  # noqa: E402

import pytest  # noqa: E402

from runtime import chrome, iconsets  # noqa: E402
from runtime import ui_draw as _ui  # noqa: E402

ICONS = ROOT / "native" / "moy_ui" / "icons"


def _installed(look, name="iconpkg"):
    rc, _notes = _ui._C.theme_install(json.dumps(
        {"format": 2, "name": name, "inherits": "moy", "look": look}).encode())
    assert rc == 0
    return chrome.live_colors(name, "light")


def _glyph(src, byte):
    """(w, h, x, y, levels) of a mask file's glyph at `byte`, or None."""
    rec = src[16 + byte]
    if rec == 0:
        return None
    at = 16 + 256 + rec * 8
    off = src[at] | src[at + 1] << 8 | src[at + 2] << 16
    w, h, x, y = struct.unpack("<BBbb", src[at + 3:at + 7])
    bpp = src[5]
    base = 16 + 256 + src[9] * 8
    lv = []
    for i in range(w * h):
        bit = i * bpp
        b = src[base + off + (bit >> 3)]
        lv.append((b >> (8 - bpp - (bit & 7))) & ((1 << bpp) - 1))
    return w, h, x, y, [lv[r * w:(r + 1) * w] for r in range(h)]


def test_every_set_fills_the_slots_of_the_sources():
    names = [ln.split()[1] for ln in (ICONS / "pixel" / "slots.txt").read_text().splitlines()
             if ln.strip()]
    assert list(iconsets.SLOTS) == names
    meta = json.loads((ICONS / "set.json").read_text())
    assert meta["slots"] == names and meta["first"] == iconsets.FIRST
    assert not set(iconsets.ALIASES) & set(names), "an alias may not hide a slot"


@pytest.mark.parametrize("name", ["default", "coast", "luna"])
@pytest.mark.parametrize("size", [16, 20, 24])
def test_the_sprite_tools_icons_share_the_sets_grid(name, size):
    """The sprite row's line arrows stand on the set's grid, as large as
    the rotate icon beside them (R3-11: they were 2-4 px smaller), and the
    row's mirror, eraser and vertical flip are slots every set draws."""
    src = (ICONS / ("%s-%d.fnt" % (name, size))).read_bytes()

    def box(slot):
        g = _glyph(src, iconsets.FIRST + iconsets.SLOT[slot])
        assert g is not None, "%s-%d draws no %s" % (name, size, slot)
        w, h, _x, _y, lv = g
        cols = [c for c in range(w) if any(lv[r][c] for r in range(h))]
        rows = [r for r in range(h) if any(lv[r])]
        return cols[-1] - cols[0] + 1, rows[-1] - rows[0] + 1

    rw, rh = box("rotate")
    for arrow in ("arrow_left", "arrow_right", "arrow_up", "arrow_down"):
        aw, ah = box(arrow)
        assert max(aw, ah) >= max(rw, rh), "%s-%d: %s is %dx%d beside rotate's %dx%d" % (
            name, size, arrow, aw, ah, rw, rh)
    for slot in ("eraser", "mirror", "flip_v"):
        box(slot)


def test_masks_build_from_their_sources():
    """The committed masks and pixel set are what tools/make_icons.py builds from
    the SVG sources and pixel_icons.py: a derived artifact, compared byte for byte
    under the rasteriser that made them."""
    pytest.importorskip("resvg_py")
    pytest.importorskip("PIL")
    make_icons = importlib.import_module("tools.make_icons")
    meta = json.loads((ICONS / "set.json").read_text())
    if meta["built_with"] != make_icons.versions():
        pytest.skip("resvg %s, not the set's %s" % (make_icons.versions(), meta["built_with"]))
    files = make_icons.build_all()
    for name, data in files.items():
        assert (ICONS / name).read_bytes() == data, name + " differs from its sources"
    assert {p.name for p in ICONS.glob("*.fnt")} == {n for n in files if n.endswith(".fnt")}


def test_a_tier_freezes_the_seed_sets_at_its_sizes():
    make_icons = importlib.import_module("tools.make_icons")
    meta = json.loads((ICONS / "set.json").read_text())
    assert meta["tiers"]["handheld"] == ["default-16"]
    # the desk's tiles draw the 20-pixel size (docs/look_2026-10.md step 25)
    assert meta["tiers"]["desktop"] == ["default-16", "default-20", "default-24"]
    lines = "\n".join(make_icons.module_lines("handheld"))
    assert "'default-16.fnt'" in lines and "'system_icons.moygfx'" in lines
    assert "default-20" not in lines and "default-24" not in lines


def test_redo_is_undos_mirror_in_both_kinds():
    sheet = iconsets.pixel_sheet()
    u, r = iconsets.SLOT["undo"], iconsets.SLOT["redo"]
    for y in range(16):
        assert [sheet.tget(r, x, y) for x in range(16)] == \
            [sheet.tget(u, 15 - x, y) for x in range(16)]
    for f in ICONS.glob("*.fnt"):
        src = f.read_bytes()
        size = src[6]
        a = _glyph(src, iconsets.FIRST + u)
        b = _glyph(src, iconsets.FIRST + r)
        if a is None:                       # a layer with no undo art (Luna's fill)
            assert b is None, f.name
            continue
        assert (b[0], b[1], b[3]) == (a[0], a[1], a[3]) and b[2] == size - a[2] - a[0], f.name
        assert b[4] == [row[::-1] for row in a[4]], f.name


def test_moy_draws_its_outline():
    """The canonical Moy is outlined in index 0; the pixel set's clear pixel is 16,
    so the outline is drawn, and the sheet's file round-trips its header."""
    src = (ICONS / "pixel" / "system_icons.moygfx").read_text()
    assert src.startswith("clear 16\n")
    sheet = iconsets.pixel_sheet()
    assert sheet.clear == 16
    img = iconsets.pixel_image("moy")
    assert img.transparent == 16
    assert img.pix.count(0) > 30                       # the outline and the face
    assert img.pix[0] == 16 and img.pix[3 + 16] == 0   # clear corner, outlined top
    assert type(sheet).from_hex(src, iconsets.PIXEL_COLS,
                                iconsets.PIXEL_ROWS).to_hex() == src.rstrip("\n")


def test_the_sheet_loader_takes_the_clear_header():
    rows = "clear 16\n" + "\n".join(["." * 16 + "0123456789abcdef"] * 16)
    rc, n = _ui.sheet_check(rows.encode(), 64)
    assert (rc, n) == (0, 16)
    assert _ui.sheet_check(b"clear 17\n00\n", 64)[0] != 0
    assert _ui.sheet_check(b"clear x\n00\n", 64)[0] != 0


def test_the_pixel_set_takes_the_themes_swap():
    live = _installed({"icons": {"kind": "pixel", "swap": "0123456789abd1ef"}})
    try:
        sp = iconsets.spec(live.look)
        assert sp[0] == "pixel" and sp[2] == bytes([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11,
                                                    13, 1, 14, 15])
        plain = iconsets.pixel_image("wifi")
        swapped = iconsets.pixel_image("wifi", sp[2])
        assert 12 in plain.pix and 12 not in swapped.pix
        assert [sp[2][p] if p != 16 else 16 for p in plain.pix] == list(swapped.pix)
    finally:
        _ui._C.theme_uninstall()


def test_a_bad_swap_is_no_swap():
    live = _installed({"icons": {"kind": "pixel", "swap": "123"}}, "iconbad")
    try:
        sp = iconsets.spec(live.look)
        assert sp[0] == "pixel" and sp[2] is None
    finally:
        _ui._C.theme_uninstall()


def test_icons_names_a_set_and_a_theme_without_it_keeps_its_sheet():
    for name in ("moy", "contrast"):
        assert iconsets.spec(chrome.live_colors(name, "light").look) == ("smooth", "default", None)
    for name in ("night", "platinum", "win95"):
        install_theme(name)
        assert iconsets.spec(chrome.live_colors(name, "dark").look) is None, name


def _pixels(cv, rect):
    """The RGB565 words of `rect` on canvas `cv`, row by row."""
    x, y, w, h = rect
    buf, stride = bytes(cv._buf), cv._stride
    return [buf[2 * ((y + j) * stride + x):2 * ((y + j) * stride + x + w)] for j in range(h)]


@pytest.mark.parametrize("box", [16, 32])
def test_a_smooth_set_draws_every_slot_and_falls_back_to_the_default(tmp_path, box):
    ws = _ws(tmp_path)
    cv = ws.sys_canvas
    for set_name in ("default", "luna", "coast", "no_such_set"):
        for kind in iconsets.SLOTS:
            cv.rect(0, 0, 40, 40, 0)
            assert iconsets.draw(cv, kind, (0, 0, box, box), 7, ("smooth", set_name, None)), \
                (set_name, kind)
            assert len(set(b"".join(_pixels(cv, (0, 0, box, box))))) > 1, (set_name, kind)
    assert not iconsets.draw(cv, "teapot", (0, 0, 16, 16), 7, ("smooth", "default", None))
    iconsets.reset()                # the sets' masks back out of the font rows


def test_the_bar_draws_the_themes_set(tmp_path):
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    assert ws.icon_spec() == ("smooth", "default", None)
    cv = ws.sys_canvas
    cv.rect(0, 0, 40, 20, 0)
    ws._icon("wifi", 0, 0, cv, 1, ink=7)
    smooth = _pixels(cv, (0, 0, 16, 16))
    cv.rect(0, 0, 40, 20, 0)
    iconsets.draw(cv, "wifi", (0, 0, 16, 16), 7, ("smooth", "default", None))
    assert smooth == _pixels(cv, (0, 0, 16, 16))
    use_theme(ws, "night", "dark")
    assert ws.icon_spec() is None
    assert not ws.draw_icon("wifi", (0, 0, 16, 16), 7, cv)


def test_a_depth_frames_title_buttons_take_the_set(tmp_path):
    """moy_ui.icons_use hands the C its set: the title buttons of a depth frame
    draw the set's close/maximize/minimize masks, and its glyphs without one."""
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    cv = ws.sys_canvas
    rect, th = (0, 0, 300, 120), 44

    def frame():
        cv.rect(0, 0, 300, 120, 7)
        _ui.frame(cv, ws.theme_colors, rect, th, "T", True, True, 1)
        return _pixels(cv, (150, 0, 150, th))

    iconsets.use_parts(None)
    glyphs = frame()
    iconsets.use_parts(("smooth", "default", None))
    masks = frame()
    assert masks != glyphs
    assert ws.icon_spec() == ("smooth", "default", None)
    iconsets.use_parts(("pixel", None, None))
    assert frame() == glyphs
    iconsets.reset()
    use_theme(ws, "night", "dark")     # the depth is global


def test_one_icon_one_meaning_for_the_round_4_pairs():
    """The gear means Settings alone and `dup` Duplicate alone (D-22):
    Appearance wears the palette and the sprite tab's Copy the clipboard
    (design review R4-15, R4-16)."""
    from runtime import paint_layer
    assert iconsets.slot_of("appearance") == iconsets.SLOT["palette"]
    assert iconsets.slot_of("appearance") != iconsets.slot_of("settings")
    copy = [slot for oid, _label, slot in paint_layer._OPS if oid == "copy"]
    assert copy == ["copy"]


def test_load_and_to_files_wear_two_folders_that_say_their_direction():
    """The sprite map's Load and To Files are inverse operations on one
    object: Load the open folder, To Files the folder with an arrow out of
    it (m21, 2026-10-19), never the same folder twice."""
    import inspect
    from runtime import paint_layer
    src = inspect.getsource(paint_layer.PaintLayer._draw_look)
    assert '"To Files", ("export"' in src and '"Load", ("folder_open"' in src
    assert iconsets.SLOT["export"] != iconsets.SLOT["folder_open"]


@pytest.mark.parametrize("name", sorted(p.name for p in (ICONS / "smooth").iterdir()))
def test_a_sets_line_weight_is_read_off_its_own_mask(name):
    """iconsets.weight measures the set's `new` mask (what a board carries),
    and it agrees with the stroke its source draws: the New card's plus
    keeps each set's weight at any size."""
    import re
    src = (ICONS / "smooth" / name / "new.svg").read_text()
    stroke = float(re.findall(r'stroke-width="([0-9.]+)"', src)[0])
    got = iconsets.weight(("smooth", name, None))
    assert got is not None and abs(got - stroke) <= 0.2, (name, got, stroke)
    assert iconsets.weight(("pixel", None, None)) is None
    assert iconsets.weight(None) is None


@pytest.mark.parametrize("theme,mode", [("moy", "light"), ("moy", "dark"),
                                        ("blocks", "light"), ("contrast", "light"),
                                        ("contrast", "dark")])
def test_the_new_card_is_a_blank_page_with_the_sets_plus(tmp_path, theme, mode):
    """The "+ New" card draws the theme's surface and the plus in its accent
    ink, no IconSheet sprite: its centre is the ink, its corner-in the page."""
    from runtime.launcher_layer import draw_new_art
    ws = _ws(tmp_path)
    ws.look.set_theme(theme, persist=False, variant=mode)
    th = ws.theme_colors
    cv = ws.sys_canvas
    cv.rect(0, 0, 140, 140, th["ink"])
    draw_new_art(cv, th, (4, 4, 128, 128), 12, False, ws.icon_spec())

    def at(x, y):
        return _pixels(cv, (x, y, 1, 1))[0]

    def word(ci):
        cv.rect(0, 136, 1, 1, ci)
        return at(0, 136)

    assert at(68, 68) == word(th["ink_accent"])
    assert at(30, 30) == word(th["surface"])
    assert at(68, 30) == word(th["surface"])      # above the plus's arm


# -- the slots past 0x7f: an icon's glyph by its index, never decoded as text ----------

PAST = [n for n in iconsets.SLOTS if iconsets.FIRST + iconsets.SLOT[n] > 0x7f]
MASKS = sorted(p.name for p in ICONS.glob("*.fnt"))


def test_the_sets_hold_slots_past_0x7f():
    assert PAST and set(PAST) >= {"brush", "resize", "pointer", "grid", "volume", "loop"}
    assert iconsets.FIRST + len(iconsets.SLOTS) <= 256


@pytest.mark.parametrize("name", MASKS)
def test_a_slot_past_0x7f_draws_its_mask_by_index(name):
    """moy_ui.icon_mask draws the glyph at the slot's index byte, as the file
    holds it: full coverage in the ink, none untouched, a part blended between
    -- where a text draw would decode a lone byte past 0x7f as no codepoint."""
    from runtime.host_canvas import make_system_canvas
    import moy_ui
    src = (ICONS / name).read_bytes()
    size, top = src[6], (1 << src[5]) - 1
    rc, fid = moy_ui.font_load(src)
    assert rc == 0
    try:
        for slot in PAST:
            g = _glyph(src, iconsets.FIRST + iconsets.SLOT[slot])
            if g is None:
                assert name.startswith("luna.fill"), (name, slot)   # a line icon's fill
                continue
            w, h, gx, gy, lv = g
            cv = make_system_canvas(40, 40)
            cv.rect(0, 0, 40, 40, 0)
            cv.rect(0, 0, 1, 1, 7)
            ink = _pixels(cv, (0, 0, 1, 1))[0]
            cv.rect(0, 0, 1, 1, 0)
            bg = _pixels(cv, (1, 0, 1, 1))[0]
            _ui._C.icon_mask(cv, fid, iconsets.FIRST + iconsets.SLOT[slot], 4, 4, 7)
            got = _pixels(cv, (4 + gx, 4 + size - gy, w, h))
            for r in range(h):
                for c in range(w):
                    px = got[r][2 * c:2 * c + 2]
                    if lv[r][c] == top:
                        assert px == ink, (name, slot, c, r)
                    elif lv[r][c] == 0:
                        assert px == bg, (name, slot, c, r)
                    else:
                        assert px not in (ink, bg), (name, slot, c, r)
            inked = sum(row.count(ink) for row in [[row[i:i + 2] for i in range(0, len(row), 2)]
                                                   for row in _pixels(cv, (0, 0, 40, 40))])
            assert inked == sum(v == top for row in lv for v in row), (name, slot)
    finally:
        moy_ui.font_free(fid)


@pytest.mark.parametrize("box", [16, 20, 24, 32])
def test_every_set_and_kind_draws_the_slots_past_0x7f(tmp_path, box):
    ws = _ws(tmp_path)
    cv = ws.sys_canvas
    specs = [("smooth", s, None) for s in ("default", "coast", "luna")]
    specs += [("pixel", None, None), ("pixel", None, bytes(range(15, -1, -1)))]
    for spec in specs:
        for slot in PAST:
            cv.rect(0, 0, 40, 40, 0)
            assert iconsets.draw(cv, slot, (0, 0, box, box), 7, spec), (spec, slot)
            assert len(set(b"".join(_pixels(cv, (0, 0, box, box))))) > 1, (spec, slot)
            if spec[0] == "smooth":
                assert iconsets.ink_span(slot, box, box, spec) is not None, (spec, slot)
    iconsets.reset()


def test_the_title_buttons_draw_a_slot_past_0x7f(tmp_path):
    """moy_ui.icons_use takes a slot's byte past 0x7f for a title button and
    the C draws its mask by index."""
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    cv = ws.sys_canvas
    rect, th = (0, 0, 300, 120), 44

    def frame():
        cv.rect(0, 0, 300, 120, 7)
        _ui.frame(cv, ws.theme_colors, rect, th, "T", True, True, 1)
        return _pixels(cv, (150, 0, 150, th))

    iconsets.use_parts(("smooth", "default", None))
    masks = frame()
    f16 = iconsets._pick("default", 16, True)[0]
    f24 = iconsets._pick("default", 24, True)[0]
    codes = [iconsets.FIRST + iconsets.SLOT[n] for n in ("grid", "resize", "loop")]
    assert min(codes) > 0x7f
    _ui._C.icons_use(f16, f24, *codes)
    past = frame()
    _ui._C.icons_use(0, 0, 0, 0, 0)
    glyphs = frame()
    assert past != masks and past != glyphs
    iconsets.reset()
    use_theme(ws, "night", "dark")


# The boards' module: the same masks drawn by the desktop MicroPython's moy_ui.
_MP_ICONS = r'''import sys
sys.path.insert(0, @RUNTIME@)
sys.path.insert(0, @DEVICE@)
sys.path.insert(0, @STAGE@)
import binascii
import moy_ui
import device_canvas as dc


class Comp:
    def __init__(self, w, h):
        self._w, self._h = w, h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def gfx(self):
        import moy_gfx
        return moy_gfx


def make(w, h):
    return dc.SystemCanvas(Comp(w, h))

'''

ICON_CASES = r'''
def icon_cases(make, out, files, codes):
    for name in files:
        f = open(name, "rb")
        src = f.read()
        f.close()
        rc, fid = moy_ui.font_load(src)
        for code in codes:
            cv = make(40, 40)
            cv.rect(0, 0, 40, 40, 1)
            moy_ui.icon_mask(cv, fid, code, 4, 4, 7)
            cv.flush_batch()
            out(rc, code, bytes(cv._buf))
        moy_ui.font_free(fid)
'''


def test_the_boards_module_draws_the_slots_past_0x7f(tmp_path):
    import shutil
    import subprocess
    import zlib
    from unix_mp import require_unix_mp
    exe = require_unix_mp(
        "moy_ui", "moy_gfx", "moy_glass",
        why="Without it the boards' icon_mask, the one every board imports, "
            "never draws a slot past 0x7f on the host.")
    import moy_ui
    from runtime.host_canvas import make_system_canvas
    stage = tmp_path / "stage"
    stage.mkdir()
    shutil.copy(ROOT / "runtime" / "font.py", stage / "moy_font.py")
    shutil.copy(ROOT / "runtime" / "moy_image.py", stage / "moy_image.py")
    files = [str(ICONS / n) for n in MASKS]
    codes = [iconsets.FIRST + iconsets.SLOT[n] for n in PAST]
    script = tmp_path / "icons.py"
    script.write_text(_MP_ICONS.replace("@DEVICE@", repr(str(ROOT / "device")))
                      .replace("@RUNTIME@", repr(str(ROOT / "runtime")))
                      .replace("@STAGE@", repr(str(stage)))
                      + ICON_CASES
                      + "icon_cases(make, lambda rc, c, b: print(rc, c, binascii.crc32(b)), "
                        "%r, %r)\n" % (files, codes))
    run = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-2000:] or run.stdout[-2000:]
    got = [tuple(int(v) for v in ln.split()) for ln in run.stdout.splitlines()]
    want = []
    g = {"moy_ui": moy_ui}
    exec(ICON_CASES, g)
    g["icon_cases"](make_system_canvas,
                    lambda rc, c, b: want.append((rc, c, zlib.crc32(b))), files, codes)
    cv = make_system_canvas(40, 40)
    cv.rect(0, 0, 40, 40, 1)
    blank = zlib.crc32(bytes(cv._buf))
    assert len(got) == len(want) == len(files) * len(codes)
    assert got == want
    held = [_glyph((ICONS / n).read_bytes(), c) is not None for n in MASKS for c in codes]
    assert [w[0] for w in want] == [0] * len(want)
    assert [w[2] != blank for w in want] == held
