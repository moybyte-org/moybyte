"""The icon sets (docs/look_2026-10.md step 16, docs/media/look_2026-10/GAPS.md
18 and 25): what a theme's `icons` names and how an icon of it draws.

A theme's look sets `icons.kind` (`pixel` or `smooth`), `icons.set` and, for
a pixel set, `icons.swap`. A theme that sets no kind draws the bar's own
IconSheet (`ws.look.icon_sheet`, Settings -> EDIT ICONS), as it always has.

* PIXEL: the 56-slot IconSheet tools/make_icons.py builds
  (`system_icons.moygfx`, its clear pixel 16, so Moy's index-0 outline
  draws), at whole scales. `icons.swap` is 16 hex digits, digit i the index
  that index i draws as (Bench takes the blue ramp down a step).
* SMOOTH: a set's masks, one font file per layer and size (2 bpp at 16 px,
  4 bpp at 24 px), drawn by `moy_ui.icon_mask` in the ink's colour; a
  `.fill` layer, where the set has one, under it in the set's fill colour.
  The largest size the box holds draws, centred. A slot the set lacks
  draws from the default set.
* Both: Moy is the pixel set's, in every theme; redo is undo's mirror
  (built so: make_icons mirrors the masks, the pixel set's art mirrors
  undo).

  spec(look) -> (kind, set, swap) | None      the live look's icon set
  draw(cv, kind, rect, ink, spec) -> bool     False: the set cannot draw it
  ink_span(kind, w, h, spec) -> (a, b) | None the columns its ink covers
  pixel_image(kind, swap) -> image | None     a pixel set tile, keyed clear
"""

try:
    import fontset as _fontset
    import ui_draw as _ui_draw
    from editors_sheet import IconSheet, _SheetSprite
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import fontset as _fontset
    from runtime import ui_draw as _ui_draw
    from runtime.editors_sheet import IconSheet, _SheetSprite

SLOTS = (
    "moy", "home", "wifi", "wifi_off", "battery", "search", "play", "projects",
    "paint", "storybook", "files", "calc", "getcarts", "settings", "music", "notes",
    "code", "sprite", "map", "blocks", "scene", "undo", "redo", "run",
    "save", "pencil", "bucket", "line", "rect", "oval", "select", "flip",
    "rotate", "game", "app", "art", "wallpaper", "doc", "folder", "trash",
    "close", "minimize", "maximize", "back", "forward", "down", "new", "dup",
    "del", "enter", "backspace", "cart_py", "cart_lua", "cart_wasm", "cart_p8", "mute",
)
SLOT = {n: i for i, n in enumerate(SLOTS)}
# The shell's own icon kinds (chrome._ICON's) by the slot they draw.
# ...and the shell's apps whose id is not their slot's name (the desk's
# tiles, a window's line icon and its chip draw an app by its id).
ALIASES = {"edit": "pencil", "gear": "settings", "batt": "battery", "artwork": "paint",
           "appearance": "settings"}
FIRST = 0x21                  # slot 0's byte in a mask file (tools/make_icons.py)
SIZES = (24, 20, 16)          # the mask sizes, largest first
KINDS = ("smooth", "pixel")
DEFAULT_SET = "default"
FILL_DEFAULT = "#7da7ea"
PIXEL_COLS, PIXEL_ROWS = 8, 7
_NONE = -2147483648

_FONTS = {}                   # mask file -> font id, 0 where it does not load
_PIXEL = []                   # [IconSheet or None], loaded once
_IMAGES = {}                  # (slot, swap) -> keyed tile
_FILLS = {}                   # set -> {slot: colour}


def slot_of(kind):
    """The slot icon `kind` draws in, or None."""
    return SLOT.get(ALIASES.get(kind, kind))


def spec(look):
    """(kind, set, swap) of a live look (`chrome.Live.look`), or None where
    the theme names no icon set."""
    leaves = look[0]
    k = leaves.get("icons.kind", _NONE)
    if not 0 <= k < len(KINDS):
        return None
    texts = look[4] if len(look) > 4 else ()
    s = leaves.get("icons.set", _NONE)
    w = leaves.get("icons.swap", _NONE)
    name = texts[s - 1] if 0 < s <= len(texts) else None
    swap = texts[w - 1] if 0 < w <= len(texts) else None
    return KINDS[k], name or (DEFAULT_SET if KINDS[k] == "smooth" else None), _swap(swap)


def _swap(text):
    if type(text) is not str or len(text) != 16:
        return None
    try:
        return bytes(int(ch, 16) for ch in text)
    except ValueError:
        return None


def pixel_sheet():
    """The pixel set's IconSheet, redo drawn as undo's mirror, or None where
    the image carries none."""
    if not _PIXEL:
        sheet = None
        src = _fontset.icon_file("system_icons.moygfx")
        if src is not None:
            try:
                sheet = IconSheet.from_hex(src.decode("ascii"), PIXEL_COLS, PIXEL_ROWS)
            except (ValueError, UnicodeError):
                sheet = None
        if sheet is not None:
            u, r = SLOT["undo"], SLOT["redo"]
            for y in range(16):
                for x in range(16):
                    sheet.tset(r, 15 - x, y, sheet.tget(u, x, y))
            sheet.dirty = False
        _PIXEL.append(sheet)
    return _PIXEL[0]


def pixel_image(kind, swap=None):
    """Pixel-set tile `kind`, its clear pixel keyed and its indices swapped
    by `swap` (16 bytes), or None."""
    slot = slot_of(kind)
    sheet = pixel_sheet() if slot is not None else None
    if sheet is None:
        return None
    key = (slot, swap)
    img = _IMAGES.get(key)
    if img is None:
        base = sheet.tile_image(slot, sheet.clear)
        if swap is not None:
            clear = sheet.clear
            pix = [p if p == clear else swap[p & 15] for p in base.pix]
            img = _SheetSprite(base.w, base.h, pix, clear)
        else:
            img = base
        _IMAGES[key] = img
    return img


def _font(name):
    fid = _FONTS.get(name)
    if fid and _ui_draw.font_info(fid) is not None:
        return fid
    src = _fontset.icon_file(name)
    fid = 0
    if src is not None:
        rc, got = _ui_draw.font_load(src)
        fid = got if rc == 0 and got is not None else 0
    _FONTS[name] = fid
    return fid


_PICK = {}                    # set -> {box: [ink font, size, fill font]}


def _pick(set_name, box):
    """[ink font id, size, fill font id] of the largest mask of `set_name` that
    `box` holds (the smallest where none does): ink 0 where the set has none,
    fill 0 where it has no fill layer at that size."""
    by = _PICK.get(set_name)
    if by is None:
        by = _PICK[set_name] = {}
    got = by.get(box)
    if got is not None and (not got[0] or _ui_draw.font_info(got[0]) is not None):
        return got
    got = [0, 0, 0]
    for size in SIZES:
        if size > box and size != SIZES[-1]:
            continue
        fid = _font("%s-%d.fnt" % (set_name, size))
        if fid:
            got = [fid, size, _font("%s.fill-%d.fnt" % (set_name, size))]
            break
    by[box] = got
    return got


_INKED = {}                   # font id -> bytearray: 1 where the file draws the slot


def _drawn(fid, slot):
    """Whether mask file `fid` draws `slot`: a slot it lacks reads the blank
    record 0. Read once per file from its index."""
    got = _INKED.get(fid)
    if got is None:
        got = _INKED[fid] = _inked(fid)
    return got[slot]


def _inked(fid):
    out = bytearray(len(SLOTS))
    src = None
    for name in _FONTS:
        if _FONTS[name] == fid:
            src = _fontset.icon_file(name)
            break
    if src is not None and len(src) >= 16 + 256:
        for slot in range(len(SLOTS)):
            out[slot] = 1 if src[16 + FIRST + slot] else 0
    return out


def _fill_colour(set_name, slot):
    fills = _FILLS.get(set_name)
    if fills is None:
        fills = {}
        meta = _fontset.icon_file("set.json")
        if meta is not None:
            try:
                import json
                fills = json.loads(meta).get("fills", {}).get(set_name, {})
            except (ValueError, ImportError):
                fills = {}
        _FILLS[set_name] = fills
    v = fills.get(SLOTS[slot], FILL_DEFAULT)
    return _ui_draw._C.theme_rgb(0x1000000 | int(v[1:], 16))[2]


def draw(cv, kind, rect, ink, spec_, fill_index=None):
    """Draw icon `kind` of icon set `spec_` centred in `rect` (x, y, w, h) in
    colour index `ink`, a two-layer set's fill in `fill_index` (default: the
    set's fill colour for the slot, nearest MOY64). False where the set
    cannot draw it (no such slot, or an image that carries no set): the
    caller draws its own."""
    slot = slot_of(kind)
    if slot is None or spec_ is None:
        return False
    x, y, w, h = rect
    box = min(w, h)
    kind_, set_name, swap = spec_
    if kind_ == "pixel" or SLOTS[slot] == "moy":
        img = pixel_image(SLOTS[slot], swap if kind_ == "pixel" else None)
        if img is None:
            return False
        s = max(1, box // 16)
        cv.spr(img, x + (w - 16 * s) // 2, y + (h - 16 * s) // 2, s)
        return True
    if _mask_draw(cv, set_name, slot, x, y, w, h, box, ink, fill_index):
        return True
    return set_name != DEFAULT_SET and _mask_draw(cv, DEFAULT_SET, slot, x, y, w, h, box,
                                                  ink, fill_index)


_SPANS = {}                   # (set, size, slot) -> (first column, width) of its ink


def ink_span(kind, w, h, spec_):
    """(first, last) column of icon `kind`'s ink, counted from the left of
    the `w` x `h` rect `draw` centres it in, from its mask's glyph record;
    None where the set draws it as pixel art or cannot draw it."""
    slot = slot_of(kind)
    if slot is None or spec_ is None:
        return None
    kind_, set_name, _swap = spec_
    if kind_ == "pixel" or SLOTS[slot] == "moy":
        return None
    box = min(w, h)
    for name in (set_name, DEFAULT_SET):
        fid, size, _ffid = _pick(name, box)
        if not fid or not _drawn(fid, slot):
            continue
        key = (name, size, slot)
        got = _SPANS.get(key)
        if got is None:
            src = _fontset.icon_file("%s-%d.fnt" % (name, size))
            got = (0, size)
            if src is not None and len(src) >= 16 + 256:
                rec = 16 + 256 + 8 * src[16 + FIRST + slot]
                if len(src) >= rec + 8 and src[rec + 3]:
                    got = (src[rec + 5], src[rec + 3])
            _SPANS[key] = got
        ox = (w - size) // 2 + got[0]
        return (ox, ox + got[1] - 1)
    return None


def _mask_draw(cv, name, slot, x, y, w, h, box, ink, fill_index):
    fid, size, ffid = _pick(name, box)
    if not fid or not _drawn(fid, slot):
        return False
    ox = x + (w - size) // 2
    oy = y + (h - size) // 2
    if ffid and _drawn(ffid, slot):
        c = fill_index if fill_index is not None else _fill_colour(name, slot)
        _ui_draw._C.icon_mask(cv, ffid, FIRST + slot, ox, oy, c)
    _ui_draw._C.icon_mask(cv, fid, FIRST + slot, ox, oy, ink)
    return True


def use_parts(spec_):
    """Install `spec_`'s masks for the parts the C draws (a depth frame's
    title buttons, `moy_ui.icons_use`): a smooth set's 16 and 24 px files,
    or none (a pixel set's or no set: the C draws its glyphs); and Moy's
    pixel tile, in every set (`moy_ui.moy_use`), so the engine's icons draw
    Moy where a part names it (the pause card's title)."""
    f16 = f24 = 0
    name = spec_[1] if spec_ is not None and spec_[0] == "smooth" else None
    if name is not None:
        for n in (name, DEFAULT_SET):
            f16 = f16 or _pick(n, 16)[0]
            got = _pick(n, 24)
            f24 = f24 or (got[0] if got[1] == 24 else 0)
    _ui_draw._C.icons_use(f16, f24, FIRST + SLOT["close"], FIRST + SLOT["maximize"],
                          FIRST + SLOT["minimize"])
    # Moy, pixel art in every set: its tile, so `ui.icon("moy")` draws it
    img = pixel_image("moy", spec_[2] if spec_ is not None and spec_[0] == "pixel" else None)
    if img is None:
        _ui_draw._C.moy_use(None, 0)
    else:
        _ui_draw._C.moy_use(bytes(p & 255 for p in img.pix), img.transparent & 255)


def reset():
    """Free the loaded masks and forget them and the tiles: the masks are font
    rows of their own, beside the theme's, in the process's slots."""
    _ui_draw._C.icons_use(0, 0, 0, 0, 0)
    for fid in _FONTS.values():
        if fid:
            _ui_draw.font_free(fid)
    _FONTS.clear()
    _PICK.clear()
    _INKED.clear()
    _IMAGES.clear()
    del _PIXEL[:]
