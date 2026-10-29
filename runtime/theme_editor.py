# Map (grep -n a name to jump there):
#   ThemeDoc              the theme package open in the Editor: theme.json and its tiles
#   ThemeDoc.commit       what either tab holds, written through the look
#   ThemeDoc.begin_edit   an edit of the open slot: a smooth one turns to pixels first
#   ThemeDoc.step         undo and redo, the session's alone
#   draw_zone             the bar's theme mode: ‹, the two tabs, the theme's name, undo, redo
#   ConfigLayout          the Config tab's geometry: the code view, its status line, the hints
#   ConfigLayer           the Config tab: theme.json in the Code tab's view, json mode
#   SpritesLayout         the Sprites tab's geometry, a handheld's and a desk pane's
#   SpritesLayer          the Sprites tab: the open slot at its zoom, the sheet, the palette
"""The Editor's theme mode (docs/look_2026-10.md step 33, GAPS 56): a theme
package opened in the console's Editor with two tabs and nothing a cart has
-- no Code, Blocks, Map, Music and no Play: an edit applies live, so there is
nothing to run. Appearance's Edit theme… opens it (`Appearance.edit_file`,
which makes `custom` from a seed first), `Workstation.edit_theme` hands it
to the Editor (`EditorApp.open_theme`), and Done (‹, K, Esc) brings the
Editor back to where it was opened from.

**Config** is the package's theme.json, opened indented, in the Code tab's
view (`ConfigLayer` is a `CodeLayer` whose language is JSON): the gutter,
the current line, long lines fading at the edge. Its saves are the look's
(`Appearance.file_check` gates, `file_write` writes, `file_saved` installs at
the next frame), never a project's: a theme package is flat data with no
`journal/`, so undo lives in the session and a save keeps no history.

**Sprites** is its icons as 16 x 16 tiles, one a slot (runtime/iconsets.py's
SLOTS), the open slot at its zoom with the tools and the palette. A slot of
the theme's smooth set is its mask, drawn at the zoom in solid ink on a plain
ground; painting it replaces the slot with a pixel tile in the package's
sheet (`icons.moygfx`, the pixel set's shape) and names it in the look's
`icons.pixel_slots`, and undo brings the smooth one back. The sheet shows on
the live look as it is painted (`Appearance.preview_pixels`); a save writes
the sheet and theme.json together.

Keys: the T-Deck's are its hint strip's -- in Sprites F and V flip, R
rotates, the trackball picks a tool, a colour or a tab and K is Done; in
Config, where letters type and the trackball is the caret's arrows, Mic
opens the menu on its Home row, which leaves saving as Done does, and the
tabs are a touch's. A keyboard steps the slot with Q and W (E on
the T-Deck, where W moves), moves the cursor with the arrows and paints
where it stands with Enter; Ctrl+Tab changes tab, Esc is Done. The desk shows
both tabs as panes side by side (runtime/editor_desk.py's `theme`
workspace), the tab following the focused one (F6).
"""

import json as _json

try:
    import iconsets as _iconsets
    import ui as _ui
    import ui_draw as _ui_draw
    import text_modes as _modes
    import layout_base as _lb
    from code_layer import CodeLayer, _status_line
    from chrome import CodeLayout
    from editors_code import CodeEditor
    from editors_paint_map import PaintEditor
    from editor_map import checker as _checker
    from moy_input import CTRL as _CTRL
    from theme_custom import dumps as _dumps
    from widgets import _in
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import iconsets as _iconsets
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import text_modes as _modes
    from runtime import layout_base as _lb
    from runtime.code_layer import CodeLayer, _status_line
    from runtime.chrome import CodeLayout
    from runtime.editors_code import CodeEditor
    from runtime.editors_paint_map import PaintEditor
    from runtime.editor_map import checker as _checker
    from runtime.moy_input import CTRL as _CTRL
    from runtime.theme_custom import dumps as _dumps
    from runtime.widgets import _in

TAB_CONFIG = "theme_config"
TAB_SPRITES = "theme_sprites"
# (tab, label, icon): the bar's two tabs, Config's the sliders as the regular
# Editor's Config is, Sprites' the face.
TABS = ((TAB_CONFIG, "Config", "sliders"), (TAB_SPRITES, "Sprites", "sprite"))
SHEET_FILE = "icons.moygfx"      # the package's sheet where its theme.json names none
INVALID = "INVALID"              # a refused save's badge (editor_handle's)
THEME_BYTES = 16384              # style.json's theme.bytes: the cap the status line names
FIRST_SLOT = 1                   # Sprites opens on Home
RED = 8                          # the pencil's first colour
# The slots' names as the sheet's captions say them.
_NAMES = {"wifi": "Wi-Fi", "wifi_off": "Wi-Fi off", "getcarts": "Get Carts",
          "cart_py": "Python cart", "cart_lua": "Lua cart", "cart_wasm": "Compiled cart",
          "cart_p8": "PICO-8 cart"}
# The tools under the zoom: (icon slot, glyph, PaintEditor tool).
TOOLS = (("pencil", "pencil", PaintEditor.PEN), ("bucket", "fill", PaintEditor.FILL),
         ("line", "line", PaintEditor.LINE), ("rect", "rect", PaintEditor.RECT),
         ("select", "select", PaintEditor.SELECT))


def slot_name(slot):
    """Icon slot `slot`'s caption ("Home", "Wi-Fi off")."""
    n = _iconsets.SLOTS[slot]
    got = _NAMES.get(n)
    if got is not None:
        return got
    n = n.replace("_", " ")
    return n[:1].upper() + n[1:]


def _dot():
    return " · " if _ui.has_symbols() else " - "


# -- the package ------------------------------------------------------------------


class _SlotOps:
    """The Sprites tab's undo: each step one slot's tile before and after
    and whether it drew from the sheet (a smooth slot painted is pixels
    after, smooth before). History's face (`can_undo`, `undo`, ...) so the
    bar's pair walks it as it walks a cart tab's."""

    DEPTH = 32

    def __init__(self, doc):
        self.doc = doc
        self._undo = []
        self._redo = []

    def record(self, op):
        self._undo.append(op)
        if len(self._undo) > self.DEPTH:
            self._undo.pop(0)
        self._redo = []

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return None
        op = self._undo.pop()
        self._redo.append(op)
        self.doc._put(op[0], op[1], op[3])
        return op

    def redo(self):
        if not self._redo:
            return None
        op = self._redo.pop()
        self._undo.append(op)
        self.doc._put(op[0], op[2], op[4])
        return op

    def brings_back(self, slot):
        """Whether undo brings slot `slot`'s smooth icon back: a step on
        the stack turned it to pixels."""
        for op in self._undo:
            if op[0] == slot and not op[3] and op[4]:
                return True
        return False


class ThemeDoc:
    """`custom` open in the Editor: its theme.json as Config's buffer, its
    pixel tiles and the slots drawn from them as the Sprites tab's, and what
    each tab holds unwritten. `made` is the theme it was copied from just
    now, which Config's status line names once."""

    def __init__(self, ws, made=None):
        self.ws = ws
        look = ws.look
        self.name = look.theme_name
        self.note = None
        if made is not None:
            how = "is built in" if made in _builtin_names() else "stays as it is"
            self.note = "%s %s: this is your copy, %s" % (
                made[:1].upper() + made[1:], how, self.name)
        self.max_chars = look.max_chars
        text = look.file_text() or ""
        self.ed = CodeEditor(_modes.pretty(_modes.JSON, text)[:self.max_chars], 32, 12,
                             clip=getattr(ws, "clipboard", None))
        self.badge = ""
        self.saved = True              # Config's text is the file's
        self._wgen = look.file_gen
        # Sprites: a working copy of the theme's tiles and slot flags, shown
        # on the live look while it is open.
        src = look.pixel_sheet
        self.sheet = _iconsets.blank_sheet()
        if src is not None:
            n = min(len(src.pix), len(self.sheet.pix))
            self.sheet.pix[:n] = src.pix[:n]
        self.sheet.dirty = False
        flags = look.pixel_slots
        self.flags = bytearray(flags) if flags is not None else bytearray(len(_iconsets.SLOTS))
        self.pe = PaintEditor(self.sheet)
        self.pe.color = RED
        self.slot = FIRST_SLOT
        self.pe.n = self.slot
        self.cur = [0, 0]
        self.hist = _SlotOps(self)
        self.sprites_dirty = False
        self._pre = None               # the open edit's (slot, tile, flag)
        self._look_gen = look.look_gen
        look.preview_pixels(self.sheet, self.flags)

    # -- the slot ---------------------------------------------------------------

    def select(self, slot):
        """Open slot `slot` (the sheet's, wrapping)."""
        n = len(_iconsets.SLOTS)
        self.slot = slot % n
        self.pe.n = self.slot
        self.pe.sel = None
        self.ws.mark_dirty()

    def is_pixels(self, slot):
        return bool(self.flags[slot])

    def tile(self, slot):
        """Slot `slot`'s 256 palette indices in the sheet."""
        ox, oy = self.sheet.tile_origin(slot)
        out = bytearray(256)
        get = self.sheet.pget
        for y in range(16):
            for x in range(16):
                out[y * 16 + x] = get(ox + x, oy + y)
        return out

    def _set_tile(self, slot, pix):
        ox, oy = self.sheet.tile_origin(slot)
        put = self.sheet.pset
        for y in range(16):
            for x in range(16):
                put(ox + x, oy + y, pix[y * 16 + x])

    def _put(self, slot, pix, flag):
        """Undo's and redo's write: slot `slot` as `pix`, drawn from the
        sheet where `flag`."""
        self._set_tile(slot, pix)
        self.flags[slot] = 1 if flag else 0
        self.sprites_dirty = True
        self.select(slot)
        self.ws.look.preview_pixels(self.sheet, self.flags)

    def seed(self, slot):
        """The tile a smooth slot turns into when it is painted: the pixel
        set's own tile where the image carries it, else the set's mask, each
        pixel it sets in the ink."""
        sheet = _iconsets.pixel_sheet()
        if sheet is not None and slot < sheet.count:
            ox, oy = sheet.tile_origin(slot)
            out = bytearray(256)
            for y in range(16):
                for x in range(16):
                    v = sheet.pget(ox + x, oy + y)
                    out[y * 16 + x] = v if v != sheet.clear else self.sheet.clear
            return out
        mask = self.mask(slot)
        ink = 0 if self.ws.theme_colors.get("surface_light", False) else 7
        out = bytearray(bytes((self.sheet.clear,)) * 256)
        if mask is not None:
            for i in range(256):
                if mask[i]:
                    out[i] = ink
        return out

    def mask(self, slot):
        """The live set's 16 px mask of slot `slot` as 256 flags (1 where the
        canvas sets the pixel), or None where the set has none."""
        spec = self.ws.icon_spec()
        name = spec[1] if spec is not None and spec[0] == "smooth" else _iconsets.DEFAULT_SET
        return _iconsets.mask16(name, slot)

    # -- an edit ----------------------------------------------------------------

    def begin_edit(self):
        """An edit of the open slot starts: a smooth slot becomes the tile it
        is painted from, in the same undo step as the edit."""
        if self._pre is not None:
            return
        s = self.slot
        self._pre = (s, self.tile(s), self.flags[s])
        if not self.flags[s]:
            self._set_tile(s, self.seed(s))
            self.flags[s] = 1

    def end_edit(self):
        """The edit closes: one undo step where it changed the slot."""
        pre = self._pre
        if pre is None:
            return
        self._pre = None
        s, before, was = pre
        after = self.tile(s)
        if after == before and was == self.flags[s]:
            return
        self.hist.record((s, before, after, was, self.flags[s]))
        self.sprites_dirty = True
        self.ws.look.preview_pixels(self.sheet, self.flags)
        self.ws.history.edit_ms = None
        self.ws.bar_layer.invalidate()

    def state(self):
        """The open slot's state line: "Home · smooth", "Home · pixels ·
        Undo brings it back"."""
        s = self.slot
        if not self.flags[s]:
            return slot_name(s) + _dot() + "smooth"
        if self.hist.brings_back(s):
            return slot_name(s) + _dot() + "pixels" + _dot() + "Undo brings it back"
        return slot_name(s) + _dot() + "pixels"

    # -- undo -------------------------------------------------------------------

    def history(self, tab, router):
        """The History the bar's pair walks on `tab`: Config's typing bursts
        (the router's, over this buffer), Sprites' slot steps."""
        if tab == TAB_SPRITES:
            return self.hist
        return router.code_op_history()

    def step(self, router, tab, redo):
        """Undo (or redo) one step of `tab`: True where one was taken."""
        if tab != TAB_SPRITES:
            router.close_code_burst()
        hist = self.history(tab, router)
        if hist is None:
            return False
        can = hist.can_redo() if redo else hist.can_undo()
        if not can or (hist.redo() if redo else hist.undo()) is None:
            return False
        if tab != TAB_SPRITES:
            self.ed.dirty = True
            self.saved = False
        self.ws.mark_dirty()
        self.ws.bar_layer.invalidate()
        return True

    def can(self, router, tab, redo):
        hist = self.history(tab, router)
        if hist is None:
            return False
        if redo:
            return hist.can_redo()
        return hist.can_undo() or (tab != TAB_SPRITES and router._code_burst_pending())

    # -- the saves --------------------------------------------------------------

    def clean(self, tab):
        if tab == TAB_SPRITES:
            return not self.sprites_dirty
        return not self.ed.dirty and self.saved

    def save_config(self, soft=False):
        """Write Config's text: (ok, badge). `soft` (the idle save) refuses a
        text the loader would not take and badges it; every other caller is
        an exit and writes it anyway, keeping the badge (a kid's text is
        theirs; the loader refuses it the next time the theme loads)."""
        look = self.ws.look
        self.ws.history.close_code_burst()
        body = self.ed.text()[:self.max_chars]
        good, why = _modes.check(_modes.JSON, body)
        out = body
        if good:
            good, why, out = look.file_check(body)
            if not good:
                out = body
        self.badge = why if good else (INVALID + " " + why).rstrip()
        if not good and soft:
            return (False, self.badge)
        err = look.file_write(out)
        if err is not None:
            return (False, str(err))
        self.ed.dirty = False
        self.saved = True
        self.note = None
        self._wgen = look.file_gen
        if good:
            look.file_saved(out)
        self.ws.mark_dirty()
        return (True, self.badge)

    def save_sprites(self):
        """Write the tiles and theme.json naming them and the slots drawn from
        them, in one write of the package: True when written."""
        if not self.sprites_dirty:
            return False
        look = self.ws.look
        if not self.clean(TAB_CONFIG):
            self.save_config()
        text = look.file_text()
        try:
            d = _json.loads(text) if text else None
        except ValueError:
            d = None
        if type(d) is not dict:
            return False
        name = d.get("icons")
        if type(name) is not str or not name.endswith(".moygfx"):
            name = SHEET_FILE
        d["icons"] = name
        lk = d.get("look")
        if type(lk) is not dict:
            lk = d["look"] = {}
        ic = lk.get("icons")
        if type(ic) is not dict:
            ic = lk["icons"] = {}
        slots = [_iconsets.SLOTS[i] for i in range(len(self.flags)) if self.flags[i]]
        if slots:
            ic["pixel_slots"] = slots
        else:
            ic.pop("pixel_slots", None)
        out = _dumps(d).decode()
        good, _why, body = look.file_check(out)
        if not good:
            return False
        err = look.file_write(body, {name: self.sheet.to_hex().encode()})
        if err is not None:
            self.badge = str(err)
            return False
        self.sprites_dirty = False
        self.sheet.dirty = False
        look.file_saved(body)
        look.file_gen += 1             # Config, with no edits, reads it again
        self.ws.mark_dirty()
        return True

    def save_tab(self, tab, soft=False):
        """Tab `tab`'s save: Config's (`soft`: the idle one, which refuses a
        text the loader would not take), Sprites'. True when it wrote."""
        if tab == TAB_SPRITES:
            return self.save_sprites()
        if self.clean(TAB_CONFIG):
            return False
        return self.save_config(soft)[0]

    def commit(self):
        """Every exit's hard commit: what either tab holds unwritten."""
        self.end_edit()
        if not self.clean(TAB_CONFIG):
            self.save_config()
        self.save_sprites()

    def follow(self):
        """A write made elsewhere (a Sprites save, the Appearance rows):
        Config with no edits reads the file again, and Sprites, with none,
        takes the look's slots again once it installs."""
        ws = self.ws
        look = ws.look
        if look.file_gen != self._wgen and self.clean(TAB_CONFIG):
            self._wgen = look.file_gen
            text = look.file_text()
            if text is not None:
                self.ed.set_text(_modes.pretty(_modes.JSON, text)[:self.max_chars])
                ws.history.drop_code_history()
        if look.look_gen != self._look_gen:
            self._look_gen = look.look_gen
            if not self.sprites_dirty and self._pre is None:
                flags = look.pixel_slots
                self.flags[:] = flags if flags is not None else bytes(len(self.flags))
                look.preview_pixels(self.sheet, self.flags)

    def status(self):
        """Config's status line: the copy's note once, else the file, its
        size against the cap and whether it is saved (or the badge)."""
        if self.note is not None:
            return self.note
        n = len(self.ed.text())
        kb = THEME_BYTES
        size = "%d.%d of %d KB" % (n // 1024, (n % 1024) * 10 // 1024, kb // 1024)
        tail = self.badge or ("Saved" if self.clean(TAB_CONFIG) else "Edited")
        return "theme.json" + _dot() + size + _dot() + tail


def _builtin_names():
    try:
        from chrome import THEME_NAMES
    except ImportError:  # pragma: no cover - host fallback when not yet aliased
        from runtime.chrome import THEME_NAMES
    return THEME_NAMES


# -- the bar ------------------------------------------------------------------------
#
# The bar's theme mode, by the regular Editor's rule (EditorApp.draw_zone):
# ‹ (Done) a 28 px target on every board, the open tab labelled on the
# current mark and the other an icon, a hairline, the theme's name, and undo
# and redo at the zone's right end; no Play.

_BACK_HIT = 28                   # ‹'s tap target on every board, at fs 1
_TAB_GAP = 2


def zone_parts(app, rect):
    """The zone's parts in bar coordinates: (back rect, [(tab, label, icon,
    rect, labelled)], hairline x, name x, undo rect, redo rect)."""
    fs = app._zone_scale()
    x, y, w, h = rect
    ic = 16 * fs
    touch = _floor(app)
    back = (x, y, (28 if touch else 20) * fs, h)
    xx = x + back[2]
    tabs = []
    for tab, label, icon in TABS:
        if tab == app.tab:
            got = _ui_draw.tab_row_rects((xx, y, w, h), [(tab, label)], fs)
            tw = got[0][1][2] if got and got[0][2] else ic + (12 if touch else 6) * fs
            if touch:
                tw += 8 * fs
            tabs.append((tab, label, icon, (xx, y, tw, h), bool(got and got[0][2])))
        else:
            tw = ic + (12 if touch else 6) * fs
            tabs.append((tab, label, icon, (xx, y, tw, h), False))
        xx += tw + _TAB_GAP * fs
    line = xx + 4 * fs
    redo = (x + w - ic - 6 * fs, y, ic + 6 * fs, h)
    undo = (redo[0] - ic - 6 * fs, y, ic + 6 * fs, h)
    return back, tabs, line, line + 6 * fs, undo, redo


def _floor(app):
    """Whether the bar takes the tap floor's targets: a touch input, or a
    chrome scale over the font's (the S3)."""
    lay = app.ws.layout
    return _ui_draw.input_kind() == "touch" or lay.cs > lay.fs


def zone_need(app, h):
    fs = app._zone_scale()
    return (20 + 2 * 22 + 60 + 2 * 22 + 40) * fs


def draw_zone(app, cv, rect):
    ws = app.ws
    th = ws.theme_colors
    fs = app._zone_scale()
    doc = app.theme
    back, tabs, line, name_x, undo, redo = zone_parts(app, rect)
    ic = 16 * fs
    ink = th["chrome_ink"]
    y = rect[1] + (rect[3] - ic) // 2
    bx = back[0] + (back[2] - ic) // 2 if _floor(app) else back[0] + 2 * fs
    if not ws.draw_icon("back", (bx, y, ic, ic), ink, cv):
        ws._glyph("arr_l", (bx, y, ic, ic), ink, cv, fs)
    sets = ws.icon_spec() is not None
    for tab, label, icon, r, labelled in tabs:
        if tab == app.tab:
            mark = app._open_mark(cv, r)
            tink = ink if mark is None else mark
            if labelled:
                _ui_draw.tab(cv, th, r, False, label, True, tink,
                             ws._icon_image_keyed(icon),
                             icon_draw=app._set_icon(cv, tink) if sets else None, kind=icon)
                continue
            _icon_in(ws, cv, icon, r, tink)
        else:
            _icon_in(ws, cv, icon, r, ink)
    top, bh = rect[1], rect[3]
    cut = 2 * fs if bh <= 20 * fs else 7 * fs     # the lent zone is an icon high
    cv.rect(line, top + cut, fs, bh - 2 * cut, th["border"])
    tf = _ui_draw.font("ui")
    was = _ui_draw.print_font(tf[0])
    try:
        room = max(0, undo[0] - 8 * fs - name_x)
        s = _ui_draw.print_cut(doc.name, room, fs)
        cv.print(s, name_x, top + (bh - tf[2] * fs) // 2, th["chrome_ink_dim"], 1)
    finally:
        _ui_draw.print_font(was)
    cv.rect(undo[0] - 6 * fs, top + cut, fs, bh - 2 * cut, th["border"])
    for glyph, r, can in (("undo", undo, ws.history.can_undo()),
                          ("redo", redo, ws.history.can_redo())):
        app._draw_history_icon(cv, glyph, r[0] + (r[2] - ic) // 2, y, ic, can)


def _icon_in(ws, cv, icon, r, ink):
    fs = max(1, ws.layout.fs)
    ic = 16 * fs
    x = r[0] + (r[2] - ic) // 2
    y = r[1] + (r[3] - ic) // 2
    if not ws.draw_icon(icon, (x, y, ic, ic), ink, cv):
        ws._icon(icon, x, y, cv, ink=ink)


def zone_targets(app, rect):
    """The zone's targets the keys walk on the bar (`BarLayer.focus_items`):
    Done, the two tabs, undo and redo."""
    back, tabs, _line, _nx, undo, redo = zone_parts(app, rect)
    return [back] + [t[3] for t in tabs] + [undo, redo]


def zone_tap(app, px, py, rect):
    back, tabs, _line, _nx, undo, redo = zone_parts(app, rect)
    fs = app._zone_scale()
    hit = (back[0] - 4 * fs, back[1], max(back[2], _BACK_HIT * fs), back[3])
    if hit[0] <= px < hit[0] + hit[2] and hit[1] <= py < hit[1] + hit[3]:
        app.ws.defer(app.close_theme)
        return True
    for tab, _l, _i, r, _on in tabs:
        if r[0] <= px < r[0] + r[2] + _TAB_GAP * fs and r[1] <= py < r[1] + r[3]:
            app.theme_tab(tab)
            app.zone_opened = True
            return True
    if undo[0] <= px < undo[0] + undo[2]:
        app.ws.history.undo()
        return True
    if redo[0] <= px < redo[0] + redo[2]:
        app.ws.history.redo()
        return True
    return False


def row_layout(app, rect):
    """The desk row's theme mode: the two tabs labelled from the left, undo
    and redo at the right end. ([(tab, label, icon, rect, labelled)], undo
    rect, redo rect)."""
    fs = app._zone_scale()
    x, y, w, h = rect
    ic = 16 * fs
    cell = max(ic, min(h, 32 * fs))
    redo = (x + w - 6 * fs - cell, y + (h - cell) // 2, cell, cell)
    undo = (redo[0] - cell - 4 * fs, redo[1], cell, cell)
    area = (x, y, max(0, undo[0] - 12 * fs - x), h)
    got = _ui_draw.tab_row_rects(area, [(t, l) for t, l, _i in TABS], fs, 8 * fs)
    out = []
    for (t, label, icon), (_t, r, on) in zip(TABS, got):
        out.append((t, label, icon, r, on))
    return out, undo, redo


def draw_row(app, cv, rect):
    ws = app.ws
    th = ws.theme_colors
    tabs, undo, redo = row_layout(app, rect)
    band_ink = th["ink"] if ws.bar_layer.zone_band_light("menu") else None
    sets = ws.icon_spec() is not None
    draw = None
    if sets:
        kind = next((i for t, _l, i, _r, _o in tabs if t == app.tab), None)
        draw = app._set_icon(cv, open_kind=kind, open_ink=_ui_draw.current_ink(cv, th),
                             lead=True)
    icon_for = getattr(ws, "_icon_image_keyed", None)
    for t, label, icon, r, on in tabs:
        _ui_draw.tab(cv, th, r, t == app.tab, label, on, band_ink,
                     icon_for(icon) if icon_for is not None else None,
                     icon_draw=draw, kind=icon)
    ic = 16 * app._zone_scale()
    for glyph, r, can in (("undo", undo, ws.history.can_undo()),
                          ("redo", redo, ws.history.can_redo())):
        app._draw_history_icon(cv, glyph, r[0] + (r[2] - ic) // 2,
                               r[1] + (r[3] - ic) // 2, ic, can)


def row_tap(app, px, py, rect):
    tabs, undo, redo = row_layout(app, rect)
    for t, _l, _i, r, _on in tabs:
        if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
            app.theme_tab(t)
            return True
    for r, fn in ((undo, app.ws.history.undo), (redo, app.ws.history.redo)):
        if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
            fn()
            return True
    return False


# -- the hint strips ----------------------------------------------------------------


def _hints_for(tab):
    """The tab's hint strip for the hint input (`ui_draw.hint_kind`): the
    T-Deck's are its own keys (moy_ui_parts.c's ACT_KEYS), a keyboard's its
    chords, none under touch alone."""
    inp = _ui_draw.hint_kind()
    if inp == "touch":
        return ()
    if tab == TAB_CONFIG:
        if inp == "tdeck":
            return ("menu", "Home")     # the menu opens on its Home row
        if inp == "pad":
            return ("back", "Done")
        return ("Ctrl Tab", "Sprites", "back", "Done")
    if inp == "tdeck":
        return ("flip", "Flip", "rotate", "Rotate", "ball", "Colour", "back", "Done")
    if inp == "pad":
        return ("confirm", "Paint", "back", "Done")
    return ("Q W", "Icon", "flip", "Flip", "rotate", "Rotate", "back", "Done")


def _draw_hints(cv, th, band, items, desk):
    if not items or band is None:
        return
    spans = _ui_draw.hints(cv, th, band, items, desk, draw=False)
    n = 0
    for _x0, x1 in spans:
        if x1 > band[0] + band[2]:
            break
        n += 1
    if n:
        _ui_draw.hints(cv, th, band, items[:2 * n], desk)


# -- Config -------------------------------------------------------------------------


class ConfigLayout(CodeLayout):
    """The Code tab's geometry for Config: no symbol keys, the status line
    over the hint strip where the input has keys."""

    def __init__(self, w, h, font_scale=1, chrome_scale=None, keys=True):
        CodeLayout.__init__(self, w, h, font_scale, chrome_scale, symbols=False)
        fs = self.fs
        desk = _lb.metric_class() == _lb.DESK
        self.keys = keys
        hint_h = (max(34 // _lb.render_scale(), 18 * fs) if desk else 22 * fs) \
            if keys and not desk else 0
        self.hints = (self.cx, self.cy + self.ch - hint_h, self.cw, hint_h) if hint_h else None
        band = self.status_band[3]
        y = self.cy + self.ch - hint_h - band
        self.status_band = (self.cx, y, self.cw, band)
        self.status_text = (self.status_text[0], y, self.status_text[2], band)
        self.rows = max(4, (y - self.y0) // self.lh)


class ConfigLayer(CodeLayer):
    """The Config tab: the package's theme.json in the Code tab's view, its
    language JSON, the theme's status line under it. The buffer is the open
    ThemeDoc's (`ws.editor` while the theme mode is open), its keys the Code
    tab's less what JSON has no use for (completion, Go to, the scripts)."""

    id = TAB_CONFIG

    def __init__(self, ws, names):
        CodeLayer.__init__(self, ws, names)
        self.layout = None
        self._args = None

    def relayout(self, w, h, fs, cs=None):
        self._args = (w, h, fs, cs)
        self.layout = ConfigLayout(w, h, fs, cs, keys=_ui_draw.hint_kind() != "touch")

    def _sync(self):
        """Lay the tab out where nothing has (a desk pane lays it out at the
        pane's size, `EditorDesk._in_pane`), and again on a handheld where the
        input moved between touch and keys (the hint strip)."""
        ws = self.ws
        if self.layout is None:
            sc = ws.sys_canvas
            self.relayout(sc.w, sc.h, ws.look.effective_font_scale(),
                          ws.look.effective_chrome_scale())
        elif (self._args is not None and ws.editor_app._desk() is None
              and self.layout.keys != (_ui_draw.hint_kind() != "touch")):
            self.relayout(*self._args)

    def _doc(self):
        return self.ws.editor_app.theme

    def _lang(self):
        return "json"

    def wants_symbols(self):
        return False

    def wants_tools(self):
        return False

    def _recheck_err(self):
        self.ws.crash_popup = None

    def _open_completion(self, ed):
        pass

    def _open_jump(self, ed):
        pass

    def open_scripts(self):
        pass

    def _with(self, fn, *args):
        """Run `fn` with the Code tab's layout handle on Config's and the
        buffer the open theme's: what every CodeLayer verb reads."""
        ws = self.ws
        doc = self._doc()
        if doc is None:
            return None
        self._sync()
        lay = self.layout
        ed = doc.ed
        if (ed.COLS, ed.ROWS) != (lay.cols, lay.rows):
            ed.set_view_size(lay.cols, lay.rows)
        old_lay, old_ed = ws.code_layout, ws.editor
        ws.code_layout, ws.editor = lay, ed
        try:
            return fn(*args)
        finally:
            ws.code_layout = old_lay
            if ws.editor is ed:
                ws.editor = old_ed

    def draw(self, dt):
        doc = self._doc()
        if doc is not None:
            doc.follow()
        self._with(CodeLayer.draw, self, dt)

    def _draw_status(self, cv, lay, ed):
        ws = self.ws
        th = ws.theme_colors
        doc = self._doc()
        fs = lay.fs
        _ui_draw.status_row(cv, th, lay.status_band, ())
        cv.rect(lay.status_band[0], lay.status_band[1], lay.status_band[2], fs, th["border"])
        text = doc.status() if doc is not None else ""
        tx, ty, tw, tbh = lay.status_text
        if doc is not None and doc.note is not None:
            ic = 12 * fs
            ink = th["value"] if not _contrast(th) else th["ink"]
            x0 = lay.x0 - 2 * fs
            if not ws.draw_icon("dup", (x0, ty + (tbh - ic) // 2, ic, ic), ink, cv):
                ws._glyph("dup", (x0, ty + (tbh - ic) // 2, ic, ic), ink, cv, fs)
            was = _ui_draw.print_font(_ui_draw.font("caption")[0])
            try:
                cv.print(_ui_draw.print_cut(text, max(0, tx + tw - x0 - ic - 4 * fs), fs),
                         x0 + ic + 4 * fs, ty + (tbh - _ui_draw.font("caption")[2] * fs) // 2,
                         ink, 1)
            finally:
                _ui_draw.print_font(was)
        else:
            _ui_draw.status_row(cv, th, lay.status_text, (_status_line(
                (text,), tw, fs),))
        _draw_hints(cv, th, lay.hints, _hints_for(TAB_CONFIG), False)

    def handle_input(self, i):
        doc = self._doc()
        if doc is None:
            return True
        k = getattr(i, "last_key", 0) or 0
        app = self.ws.editor_app
        if k and self._ekey.prev != k:
            mods = getattr(i, "mods", 0)
            if k == 0x09 and mods & _CTRL:
                self._ekey.hit(k)
                app.theme_tab(TAB_SPRITES)
                return True
            if k == 27 and not (self._find_open or self._cmp_open or self._jump_open):
                self._ekey.hit(k)
                self.ws.defer(app.close_theme)
                return True
        if _ui_draw.input_kind() == "pad" and i.pressed("b"):
            self.ws.defer(app.close_theme)          # a gamepad types nothing here
            return True
        typed = k and self._ekey.prev != k
        got = self._with(CodeLayer.handle_input, self, i)
        if typed and doc.ed.dirty:
            doc.saved = False
            doc.note = None
        return got if got is not None else True

    def handle_pointer(self, px, py, click):
        return self._with(CodeLayer.handle_pointer, self, px, py, click)

    def nav(self, dx, dy, select=None):
        doc = self._doc()
        if doc is not None:
            doc.ed.move(dy, dx, select)


def _contrast(th):
    return bool(th.get("contrast", False))


# -- Sprites ------------------------------------------------------------------------


class SpritesLayout:
    """The Sprites tab's geometry (the mockups' `theme_sprites` and
    `theme_desk`): the open slot at its zoom with the tools under it, the
    sheet beside it with the pixel-art legend, the slot's name, state and
    preview, the palette, and the hint strip. A handheld's is the T-Deck's
    and, under touch, the S3's (28 px slots, swatches and tools, the sheet
    paged); a desk pane's names every slot under its tile."""

    def __init__(self, w, h, font_scale=1, chrome_scale=None, keys=True):
        base = _lb.LayoutBase(w, h, font_scale, chrome_scale=chrome_scale)
        self.cx, self.cy, self.cw, self.ch = base.cx, base.cy, base.cw, base.ch
        fs = self.fs = base.fs
        self.keys = keys
        self.desk = desk = _lb.metric_class() == _lb.DESK
        # the tap floor's (the S3's chrome scale over its font scale) or a
        # touch input's: 28 px slots, swatches and tools, the sheet paged
        touch = not keys or base.cs > base.fs
        self.touch = touch
        cx, cy, cw, ch = self.cx, self.cy, self.cw, self.ch
        if desk:
            self.cell = 16
            self.grid = (cx + 16, cy + 14)
            span = 16 * self.cell
            ty = cy + 14 + span + 12
            self.tsz = 32
            self.tools = [(cx + 16 + i * 38, ty, 32, 32) for i in range(len(TOOLS))]
            self.sw, self.sw_gap = 14, 6
            self.sw_xy = (cx + 18, ty + 46)
            pvx = cx + 16 + 176
            self.prev1 = (pvx, ty + 48)
            self.prev2 = (pvx + 26, ty + 40)
            self.scale_text = (pvx + 76, ty + 52)
            sx = cx + 16 + span + 24
            self.tile, self.pitch, self.pitch_y = 32, 64, 56
            self.sheet_xy = (sx, cy + 12)
            self.cols = max(1, (cx + cw - sx - 8) // self.pitch)
            status_h = 26
            self.status = (cx, cy + ch - status_h, cw, status_h)
            avail = self.status[1] - (cy + 12) - 24
            self.rows = max(1, min(6, avail // self.pitch_y))
            self.legend = (sx, cy + 12 + self.rows * self.pitch_y + 4)
            self.hints = None
            self.name_xy = None
            self.page_xy = None
            return
        cell = (13 if touch else 9) * fs
        self.cell = cell
        gx, gy = cx + 8 * fs, cy + 8 * fs
        self.grid = (gx, gy)
        span = 16 * cell
        self.tsz = (30 if touch else 22) * fs
        ty = gy + span + 6 * fs
        self.tools = [(gx + i * (self.tsz + 4 * fs), ty, self.tsz, self.tsz)
                      for i in range(len(TOOLS))]
        rx = gx + span + 12 * fs
        step, tc = ((32, 28) if touch else (19, 16))
        self.pitch = self.pitch_y = step * fs
        self.tile = tc * fs
        self.sheet_xy = (rx, gy)
        self.cols = max(1, (cx + cw - rx - 4 * fs) // self.pitch)
        self.rows = 4
        yy = gy + self.rows * self.pitch + 2 * fs
        self.legend = (rx, yy)
        self.page_xy = (cx + cw - 8 * fs, yy) if touch else None
        yy += 16 * fs
        self.prev1 = (rx, yy + 2 * fs)
        self.name_xy = (rx + 22 * fs, yy)
        self.state_xy = (rx, yy + 20 * fs)
        self.sw = (26 if touch else 9) * fs
        self.sw_gap = (5 if touch else 3) * fs
        self.sw_xy = (rx + 2 * fs, yy + (44 if touch else 40) * fs)
        self.prev2 = None
        self.scale_text = None
        # the tap floor's tools reach the foot: no strip there (its keys are
        # the keyboard's, the trackball has none)
        hint_h = 22 * fs if keys and not touch else 0
        self.hints = (cx, cy + ch - hint_h, cw, hint_h) if hint_h else None
        self.status = None

    def per_page(self):
        return self.cols * self.rows

    def swatch(self, i):
        d, g = self.sw, self.sw_gap
        x0, y0 = self.sw_xy
        return (x0 + (i % 8) * (d + g), y0 + (i // 8) * (d + g), d, d)

    def slot_cell(self, k):
        """Where the k-th tile of the page shown starts, (x, y)."""
        x0, y0 = self.sheet_xy
        return (x0 + (k % self.cols) * self.pitch, y0 + (k // self.cols) * self.pitch_y)


class SpritesLayer:
    """The Sprites tab's content layer (system domain, as the sprite tab's)."""

    id = TAB_SPRITES
    domain = "system"
    ball_steps = False              # the trackball points at the icon and its colours

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        self.layout = None
        self._args = None
        self._drag = None
        self._shape0 = None
        self._kprev = 0

    def relayout(self, w, h, fs, cs=None):
        self._args = (w, h, fs, cs)
        self.layout = SpritesLayout(w, h, fs, cs, keys=_ui_draw.hint_kind() != "touch")

    def _sync(self):
        """Lay the tab out where nothing has (a desk pane lays it out at the
        pane's size, `EditorDesk._in_pane`), and again on a handheld where the
        input moved between touch and keys (the hint strip)."""
        ws = self.ws
        if self.layout is None:
            sc = ws.sys_canvas
            self.relayout(sc.w, sc.h, ws.look.effective_font_scale(),
                          ws.look.effective_chrome_scale())
        elif (self._args is not None and ws.editor_app._desk() is None
              and self.layout.keys != (_ui_draw.hint_kind() != "touch")):
            self.relayout(*self._args)

    def _doc(self):
        return self.ws.editor_app.theme

    # -- drawing -----------------------------------------------------------------

    def draw(self, dt):
        ws = self.ws
        doc = self._doc()
        self._sync()
        lay = self.layout
        cv = ws.sys_canvas
        th = ws.theme_colors
        cv.rect(lay.cx, lay.cy, lay.cw, lay.ch, th["panel"] if lay.desk else th["panel"])
        if lay.desk:
            cv.rect(lay.cx, lay.cy, lay.cw, lay.ch, th["surface"])
        if doc is not None:
            doc.follow()
            self._grid(cv, th, lay, doc)
            self._tools(cv, th, lay, doc)
            self._sheet(cv, th, lay, doc)
            self._side(cv, th, lay, doc)
            self._palette(cv, th, lay, doc)
            self._foot(cv, th, lay, doc)
        ws.bar_layer._draw_status_strip("menu")

    def _greys(self):
        return self.ws.look.live.fixed[:2]

    def _grid(self, cv, th, lay, doc):
        """The open slot at its zoom: in pixels its tile's colours on the grey
        checker; still smooth its mask in solid ink on a plain ground, a cell
        set where the canvas would set it. The centre lines, then the
        cursor's ring."""
        gx, gy = lay.grid
        c = lay.cell
        span = 16 * c
        s = doc.slot
        line = th["border"]
        if doc.is_pixels(s):
            g = self._greys()
            _checker(cv, gx, gy, span, span, c, g[0], g[1])
            ox, oy = doc.sheet.tile_origin(s)
            clear = doc.sheet.clear
            get = doc.sheet.pget
            for y in range(16):
                for x in range(16):
                    v = get(ox + x, oy + y)
                    if v != clear:
                        cv.rect(gx + x * c, gy + y * c, c, c, v)
        else:
            cv.rect(gx, gy, span, span, th["surface"])
            cv.rectb(gx, gy, span, span, line)
            mask = doc.mask(s)
            if mask is not None:
                ink = th["ink"]
                for y in range(16):
                    row = y * 16
                    for x in range(16):
                        if mask[row + x]:
                            cv.rect(gx + x * c, gy + y * c, c, c, ink)
        cv.rect(gx + 8 * c, gy, 1, span, line)
        cv.rect(gx, gy + 8 * c, span, 1, line)
        pe = doc.pe
        if pe.sel is not None:
            x0, y0, x1, y1 = pe.sel
            cv.rectb(gx + x0 * c, gy + y0 * c, (x1 - x0 + 1) * c, (y1 - y0 + 1) * c, th["value"])
        if lay.keys:
            keyline = self.ws.look.live.fixed[2]
            x, y = gx + doc.cur[0] * c, gy + doc.cur[1] * c
            box = (x - 1, y - 1, c + 2, c + 2)
            cv.rectb(box[0], box[1], box[2], box[3], keyline)
            cv.rectb(box[0] - 1, box[1] - 1, box[2] + 2, box[3] + 2, th["surface"])
            _ui_draw.focus(cv, th, (box[0] - 1, box[1] - 1, box[2] + 2, box[3] + 2),
                           "part", ring=True)

    def _tools(self, cv, th, lay, doc):
        pe = doc.pe
        r = 8 if lay.tsz >= 30 else 6
        for (slot, glyph, tool), rect in zip(TOOLS, lay.tools):
            ink = th["chrome_ink"]
            if pe.tool == tool:
                got = _ui_draw.current(cv, th, rect, r)
                ink = th["ink"] if got is None else got
            box = 16 if lay.tsz < 30 else 20
            box = min(box * (lay.fs if not lay.desk else 1), rect[2])
            ir = (rect[0] + (rect[2] - box) // 2, rect[1] + (rect[3] - box) // 2, box, box)
            if not self.ws.draw_icon(slot, ir, ink, cv):
                _ui_draw.glyph(cv, glyph, ir, ink)

    def _page(self, lay, doc):
        per = lay.per_page()
        if lay.desk:
            first = max(0, (doc.slot // lay.cols - lay.rows + 1)) * lay.cols
            return first, per
        return (doc.slot // per) * per, per

    def _sheet(self, cv, th, lay, doc):
        """The sheet: a smooth slot as the set draws it, a slot in pixels
        from the tiles with a `value` dot in its corner; the open one marked
        (a ring on the desk, every slot named under its tile)."""
        ws = self.ws
        first, per = self._page(lay, doc)
        n = len(_iconsets.SLOTS)
        t = lay.tile
        ink = th["ink"]
        dot = th["value"] if not _contrast(th) else th["ink"]
        cap = _ui_draw.font("caption")
        for k in range(per):
            s = first + k
            if s >= n:
                break
            x, y = lay.slot_cell(k)
            if lay.desk:
                x += (lay.pitch - t) // 2
            if s == doc.slot:
                if lay.desk:
                    _ui_draw.focus(cv, th, (x - 2, y - 2, t + 4, t + 4), "small", ring=True)
                else:
                    _ui_draw.focus(cv, th, (x - 2, y - 2, t + 4, t + 4), "small")
            kind = _iconsets.SLOTS[s]
            if doc.is_pixels(s):
                img = doc.sheet.tile_image(s, doc.sheet.clear)
                sc = max(1, t // 16)
                cv.spr(img, x + (t - 16 * sc) // 2, y + (t - 16 * sc) // 2, sc)
                d = 4 if t >= 28 else 3
                if lay.desk:
                    d = 5
                cv.rrect(x + t - d - (0 if lay.desk else 1), y, d, d, d // 2 + 1, dot)
            else:
                box = 16 * max(1, t // 16) if lay.desk else 16 * lay.fs
                ox = x + (t - box) // 2
                oy = y + (t - box) // 2
                if not ws.draw_icon(kind, (ox, oy, box, box), th["ink"], cv):
                    ws._icon(kind, ox, oy, cv, ink=th["ink"])
            if lay.desk:
                was = _ui_draw.print_font(cap[0])
                try:
                    name = _ui_draw.print_cut(slot_name(s), lay.pitch - 4, 1)
                    w = _ui_draw.print_width(name, 1)
                    cx = x - (lay.pitch - t) // 2 + (lay.pitch - w) // 2
                    cv.print(name, cx, y + t + 6, ink if s == doc.slot else th["ink_dim"], 1)
                finally:
                    _ui_draw.print_font(was)
        # the legend: a pixel tile's dot is "pixel art"
        lx, ly = lay.legend
        d = 5 if lay.desk else 4 * lay.fs
        f = 1 if lay.desk else lay.fs
        tx = lx + (14 if lay.desk else 8 * lay.fs)
        ty = ly + 6 * f - cap[3] * f // 2 - 1
        cv.rrect(lx + (4 if lay.desk else 0), ty + cap[3] * f // 2 - d // 2 + 2, d, d,
                 d // 2 + 1, dot)
        was = _ui_draw.print_font(cap[0])
        try:
            cv.print("pixel art", tx, ty, th["ink_dim"], 1)
            if lay.page_xy is not None:
                pages = -(-n // per)
                s = "%d of %d" % (first // per + 1, pages)
                ic = 12 * lay.fs
                w = _ui_draw.print_width(s, lay.fs)
                px, py = lay.page_xy
                cv.print(s, px - w - ic - 4 * lay.fs, ty, th["ink_dim"], 1)
                r = (px - ic, ty + (cap[2] * lay.fs - ic) // 2, ic, ic)
                if not self.ws.draw_icon("forward", r, th["ink_dim"], cv):
                    _ui_draw.glyph(cv, "arr_r", r, th["ink_dim"])
        finally:
            _ui_draw.print_font(was)

    def _side(self, cv, th, lay, doc):
        """The open slot as the shell draws it at 1x (and 2x on the desk), its
        name and its state."""
        s = doc.slot
        kind = _iconsets.SLOTS[s]
        x, y = lay.prev1
        self._slot_at(cv, th, doc, kind, x, y, 16)
        if lay.prev2 is not None:
            self._slot_at(cv, th, doc, kind, lay.prev2[0], lay.prev2[1], 32)
            cap = _ui_draw.font("caption")
            was = _ui_draw.print_font(cap[0])
            try:
                cv.print("1x" + _dot() + "2x", lay.scale_text[0], lay.scale_text[1],
                         th["ink_dim"], 1)
            finally:
                _ui_draw.print_font(was)
        if lay.name_xy is None:
            return
        f = _ui_draw.font("title")
        was = _ui_draw.print_font(f[0])
        try:
            nx, ny = lay.name_xy
            cv.print(slot_name(s), nx, ny + (16 * lay.fs + 2 - f[2] * lay.fs) // 2, th["ink"], 1)
        finally:
            _ui_draw.print_font(was)
        cap = _ui_draw.font("caption")
        was = _ui_draw.print_font(cap[0])
        try:
            st = doc.state()
            rest = st.split(_dot(), 1)[1] if _dot() in st else st
            sx, sy = lay.state_xy
            cv.print(_ui_draw.print_cut(rest, lay.cx + lay.cw - sx - 2, lay.fs), sx, sy,
                     th["ink_dim"], 1)
        finally:
            _ui_draw.print_font(was)

    def _slot_at(self, cv, th, doc, kind, x, y, box):
        ws = self.ws
        s = _iconsets.SLOT[kind]
        if doc.is_pixels(s):
            img = doc.sheet.tile_image(s, doc.sheet.clear)
            cv.spr(img, x, y, max(1, box // 16))
            return
        if not ws.draw_icon(kind, (x, y, box, box), th["ink"], cv):
            ws._icon(kind, x, y, cv, ink=th["ink"])

    def _palette(self, cv, th, lay, doc):
        keyline, white = self.ws.look.live.fixed[2:]
        cur = doc.pe.color & 15
        for i in range(16):
            x, y, d, _d = lay.swatch(i)
            cv.rrect(x, y, d, d, 3, i)
        x, y, d, _d = lay.swatch(cur)
        cv.rrect(x, y, d, d, 3, None, white, 2)
        cv.rrect(x - 2, y - 2, d + 4, d + 4, 4, None, keyline, 2)

    def _foot(self, cv, th, lay, doc):
        if lay.desk:
            _ui_draw.status_row(cv, th, lay.status, ())
            items = () if _ui_draw.hint_kind() == "touch" else ("Q W", "Icon", "F6", "Next pane")
            x, y, w, h = lay.status
            tx = x + 10
            spans = _ui_draw.hints(cv, th, (x, y, w - 8, h), items, True, draw=False) \
                if items else []
            right = (x + w - 8 - (spans[-1][1] - spans[0][0])) if spans else x + w - 8
            _ui_draw.status_row(cv, th, (tx, y, max(0, right - tx - 8), h),
                                (_status_line((doc.state(),), right - tx - 8, 1),))
            if items:
                band = (right, y, x + w - 8 - right, h)
                _ui_draw.hints(cv, th, band, items, True)
            return
        _draw_hints(cv, th, lay.hints, _hints_for(TAB_SPRITES), False)

    # -- input -------------------------------------------------------------------

    def handle_input(self, i):
        ws = self.ws
        doc = self._doc()
        if doc is None:
            return True
        app = ws.editor_app
        k = getattr(i, "last_key", 0) or 0
        if k != self._kprev:
            self._kprev = k
            if k:
                mods = getattr(i, "mods", 0)
                if k == 0x09 and mods & _CTRL:
                    app.theme_tab(TAB_CONFIG)
                    return True
                if k == 27:
                    ws.defer(app.close_theme)
                    return True
                if k == 0x1A:
                    ws.history.undo()
                    return True
                if k == 0x19:
                    ws.history.redo()
                    return True
                if k in (0x71, 0x51):
                    doc.select(doc.slot - 1)
                    return True
                if k in (0x77, 0x57, 0x65, 0x45):
                    doc.select(doc.slot + 1)
                    return True
                op = _KEY_OPS.get(k)
                if op is not None:
                    self._op(doc, op)
                    return True
        if i.pressed("b"):
            ws.defer(app.close_theme)
            return True
        dx = i.steps("right") - i.steps("left")
        dy = i.steps("down") - i.steps("up")
        if dx or dy:
            doc.cur[0] = max(0, min(15, doc.cur[0] + dx))
            doc.cur[1] = max(0, min(15, doc.cur[1] + dy))
            ws.mark_dirty()
        if i.pressed("a") or i.pressed("run"):
            self._press(doc, doc.cur[0], doc.cur[1])
            self._release(doc)
        return True

    def _op(self, doc, op):
        pe = doc.pe
        doc.begin_edit()
        if op == "fliph":
            pe.flip_h()
        elif op == "flipv":
            pe.flip_v()
        elif op == "rot":
            pe.rotate()
        doc.end_edit()
        self.ws.mark_dirty()

    def _cell(self, px, py):
        lay = self.layout
        gx, gy = lay.grid
        c = lay.cell
        x, y = (px - gx) // c, (py - gy) // c
        if 0 <= x < 16 and 0 <= y < 16:
            return x, y
        return None

    def _press(self, doc, x, y):
        pe = doc.pe
        doc.cur[0], doc.cur[1] = x, y
        doc.begin_edit()
        if pe.tool == PaintEditor.PEN:
            pe.paint(x, y)
            self._drag = (x, y)
        elif pe.tool == PaintEditor.FILL:
            pe.fill(x, y)
        else:
            self._shape0 = (x, y)
        self.ws.look.pixels_moved()

    def _move(self, doc, x, y):
        pe = doc.pe
        doc.cur[0], doc.cur[1] = x, y
        if pe.tool == PaintEditor.PEN and self._drag is not None:
            for px, py in _line(self._drag[0], self._drag[1], x, y):
                pe.paint(px, py)
            self._drag = (x, y)
            self.ws.look.pixels_moved()

    def _release(self, doc):
        pe = doc.pe
        if self._shape0 is not None:
            x0, y0 = self._shape0
            x1, y1 = doc.cur
            if pe.tool == PaintEditor.SELECT:
                pe.set_selection(x0, y0, x1, y1)
            else:
                pe.stamp_shape(x0, y0, x1, y1)
        self._drag = None
        self._shape0 = None
        doc.end_edit()

    def handle_pointer(self, px, py, click):
        ws = self.ws
        doc = self._doc()
        if doc is None:
            return True
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True
        self._sync()
        lay = self.layout
        down = bool(ws.pointer.down)
        if click:
            cell = self._cell(px, py)
            if cell is not None:
                self._press(doc, cell[0], cell[1])
                ws.mark_dirty()
                return True
            for (slot, _g, tool), r in zip(TOOLS, lay.tools):
                if _in(px, py, r):
                    doc.pe.set_tool(tool)
                    ws.mark_dirty()
                    return True
            for i in range(16):
                if _in(px, py, lay.swatch(i)):
                    doc.pe.color = i
                    ws.mark_dirty()
                    return True
            first, per = self._page(lay, doc)
            for k in range(per):
                s = first + k
                if s >= len(_iconsets.SLOTS):
                    break
                x, y = lay.slot_cell(k)
                if _in(px, py, (x, y, lay.pitch, lay.pitch_y)):
                    doc.select(s)
                    return True
            if lay.page_xy is not None and not lay.desk and py >= lay.legend[1] - 4 \
                    and py < lay.legend[1] + 16 and px >= lay.page_xy[0] - 60:
                nxt = first + per
                doc.select(nxt if nxt < len(_iconsets.SLOTS) else 0)   # the next page
                return True
            return True
        if down and (self._drag is not None or self._shape0 is not None):
            cell = self._cell(px, py)
            if cell is not None:
                self._move(doc, cell[0], cell[1])
                ws.mark_dirty()
        elif not down and (self._drag is not None or self._shape0 is not None):
            self._release(doc)
            ws.mark_dirty()
        return True


def _line(x0, y0, x1, y1):
    """The cells from (x0, y0) to (x1, y1), Bresenham's."""
    out = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        out.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return out
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


# PICO-8's sprite keys (paint_layer's): F and V flip, R rotates.
_KEY_OPS = {ord("f"): "fliph", ord("F"): "fliph", ord("v"): "flipv", ord("V"): "flipv",
            ord("r"): "rot", ord("R"): "rot"}
