"""The icon sets (docs/look_2026-10.md step 16, docs/media/look_2026-10/GAPS.md
18 and 25): what a theme's `icons` names and how an icon of it draws.

A theme's look sets `icons.kind` (`pixel` or `smooth`), `icons.set` and, for
a pixel set, `icons.swap`. A theme that sets no kind draws the bar's own
IconSheet (`ws.look.icon_sheet`, Settings -> EDIT ICONS), as it always has,
and so would an image that carried none of the set's files (`carried`).
Every console image carries its tier's sets, the browser's the handheld's
and the desk's (tools/make_font.py's `module`).

* PIXEL: the IconSheet tools/make_icons.py builds, a tile a slot
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
  carried(spec) -> spec | None                that set, where the image holds it
  draw(cv, kind, rect, ink, spec) -> bool     False: the set cannot draw it
  ink_span(kind, w, h, spec) -> (a, b) | None the columns its ink covers
  pixel_image(kind, swap) -> image | None     a pixel set tile, keyed clear
  weight(spec) -> float | None                a smooth set's line weight
  plus(cv, rect, ink, spec, stepped)          the set's `new` at any size
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
    "arrow_left", "arrow_right", "arrow_up", "arrow_down", "star", "star_on",
    "eraser", "mirror", "flip_v", "folder_open", "more", "stamp",
    "power", "sleep", "sliders", "copy", "palette", "export", "check", "warn",
    "text", "shapes", "to_front", "to_back", "spray", "picker", "pan", "cut",
    "paste", "layout", "slides", "background", "bubble", "align_l", "align_c", "align_r",
    "trans_none", "trans_fade", "trans_swipe", "brush", "resize", "pointer", "grid",
    "volume", "loop", "trophy", "lock", "heart", "spark", "smile", "key", "secret",
)
SLOT = {n: i for i, n in enumerate(SLOTS)}
# The shell's own icon kinds (chrome._ICON's) by the slot they draw.
# ...and the shell's apps whose id is not their slot's name (the desk's
# tiles, a window's line icon and its chip draw an app by its id).
ALIASES = {"edit": "pencil", "gear": "settings", "batt": "battery", "artwork": "paint",
           "appearance": "palette", "myart": "art"}
FIRST = 0x21                  # slot n's byte in a mask file is FIRST + n (tools/make_icons.py)
SIZES = (24, 20, 16)          # the mask sizes, largest first
KINDS = ("smooth", "pixel")
DEFAULT_SET = "default"
FILL_DEFAULT = "#7da7ea"
PIXEL_COLS = 8
PIXEL_ROWS = -(-len(SLOTS) // PIXEL_COLS)   # pixel_icons.moygfx's tile rows
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


def carried(spec_):
    """`spec_` where this image carries the set, else None: a smooth set
    draws where a mask file of it or of the default set is in the image, a
    pixel set where its IconSheet is. An image that carried none would
    draw the shell's own icons, as under a theme that names no set, rather
    than a set's slots it cannot draw."""
    if spec_ is None:
        return None
    if spec_[0] == "pixel":
        return spec_ if pixel_sheet() is not None else None
    for n in (spec_[1], DEFAULT_SET):
        for size in SIZES:
            if _fontset.icon_file("%s-%d.fnt" % (n, size)) is not None:
                return spec_
    return None


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


def _pick(set_name, box, again=False):
    """[ink font id, size, fill font id] of the largest mask of `set_name` that
    `box` holds (the smallest where none does): ink 0 where the set has none,
    fill 0 where it has no fill layer at that size. `again`: a pick that
    found no mask looks again (the font slots may have been full then)."""
    by = _PICK.get(set_name)
    if by is None:
        by = _PICK[set_name] = {}
    got = by.get(box)
    if got is not None and (_ui_draw.font_info(got[0]) is not None if got[0]
                            else not again):
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


_WEIGHTS = {}                 # set -> its line weight in 24ths, 0 where unmeasured
PLUS_SPAN = 14                # the `new` plus's arms in every set, in 24ths (5..19)
PIXEL_WEIGHT = 3              # a pixel set's plus: its 16 px art's 2 px stroke, in 24ths


def weight(spec_):
    """The line weight of smooth icon set `spec_` in its art's 24 units,
    measured off its largest `new` mask: the coverage across the plus's four
    arms, half way out, averaged. What draws the set's plus at a size no mask
    has (`plus`). None for a pixel set, no set, or a set no mask carries."""
    if spec_ is None or spec_[0] != "smooth":
        return None
    name = spec_[1]
    got = _WEIGHTS.get(name)
    if got is None:
        got = _WEIGHTS[name] = _measure(name) or (
            0 if name == DEFAULT_SET else _measure(DEFAULT_SET)) or 0
    return got or None


def _measure(name):
    slot = SLOT["new"]
    for size in SIZES:
        src = _fontset.icon_file("%s-%d.fnt" % (name, size))
        if src is None or len(src) < 16 + 256:
            continue
        k = src[16 + FIRST + slot]
        if not k:
            return None
        rec = 16 + 256 + 8 * k
        off = src[rec] | src[rec + 1] << 8 | src[rec + 2] << 16
        w, h, bpp = src[rec + 3], src[rec + 4], src[5]
        if w < 4 or h < 4:
            return None
        bit0 = (16 + 256 + 8 * src[9] + 3 * (src[10] | src[11] << 8) + off) * 8
        top = (1 << bpp) - 1

        def level(x, y):
            bit = bit0 + (y * w + x) * bpp
            return (src[bit >> 3] >> (8 - bpp - (bit & 7))) & top

        n = 0
        for x in (w // 4, w - 1 - w // 4):
            n += sum(level(x, y) for y in range(h))
        for y in (h // 4, h - 1 - h // 4):
            n += sum(level(x, y) for x in range(w))
        return n * 24 / (4 * top * size)
    return None


def plus(cv, rect, ink, spec_, stepped=False):
    """Icon set `spec_`'s `new` -- a plus in every set -- across `rect`'s
    shorter side at any size (the masks stop at 24 px): two strokes of the
    set's line weight (`weight`), their ends round as the set's art draws
    them; square under a pixel set or none. `stepped`: the theme's stepped
    corners."""
    x, y, w, h = rect
    s = min(w, h)
    wt = weight(spec_)
    if wt is None:
        wt, cap = PIXEL_WEIGHT, False
    else:
        cap = True
    t = max(1, int(s * wt / (PLUS_SPAN + wt) + 0.5))
    if (s - t) & 1:
        s -= 1
    r = t // 2 if cap else 0
    cx, cy = x + (w - s) // 2, y + (h - s) // 2
    cv.rrect(cx, cy + (s - t) // 2, s, t, r, ink, stepped=stepped)
    cv.rrect(cx + (s - t) // 2, cy, t, s, r, ink, stepped=stepped)


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
            f16 = f16 or _pick(n, 16, True)[0]
            got = _pick(n, 24, True)
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
    _WEIGHTS.clear()
    _PICK.clear()
    _INKED.clear()
    _IMAGES.clear()
    del _PIXEL[:]
