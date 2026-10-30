# Map (grep -n a name to jump there):
#   _Prompt                  one open dialog and what OK does
#   CardsLayout              the Config tab's content rect under the bar
#   CardsLayer               the Config tab: its pages, input, prompts
#   CardsLayer._draw_cards   the page the tab shows: the files, a broken manifest, the cards
#   CardsLayer._draw_files   the project's files, a placed `ui.table`
"""The Editor's Config tab ("Make it mine", #3/#15) as its own Layer.

It shows one of three pages on the public UI verbs, each in the content
rect under the bar:

- the cards, `editor_settings.SettingsPage`: the cart's `edit` fields, then
  This project's doors, Cart info and Files;
- a cart whose manifest did not parse (`moy_carts.load` keeps it on the
  shelf with its reason in `cart["broken"]`): `ui.empty` with the reason, and
  Files, the door to the file that broke it;
- the project's files (Files, from either page): a placed `ui.table` that
  ROUTES each file to what opens it.

CART STATE lives on the open Project: the tab reads `ws.project.config` /
`ws.project.cart` / `ws.project.sheet`, mutates the config in place through
the page (`Project.record_config` keeps the undo), and PLAY in the bar re-runs
the cart. Two of the cards' helpers stay here for the page: `_validate_field`
(a field that fails it stays off the page, #94) and `draw_choice` (a picker
tile's picture). `NAMES` (the palette) and `_err_text` are injected at
construction, the circular-import dodge the other extracted UIs use.

draw() calls ws.bar_layer._draw_status_strip("menu") LAST (chrome over
content), so the Editor's lent top-bar zone (the tab ladder + PLAY,
EditorApp.draw_zone) shows on this tab; handle_pointer routes a tap through
ws.bar_layer.handle_bar_tap("menu", ...) FIRST.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw

try:
    import layout_base as _layout_base
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import layout_base as _layout_base

try:
    import text_modes as _modes
except ImportError:  # pragma: no cover - host fallback
    from runtime import text_modes as _modes

try:
    from layout_base import LayoutBase
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import LayoutBase

try:
    from widgets import arm_prompt as _arm_prompt
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import arm_prompt as _arm_prompt


class _Prompt:
    """One open dialog (`ui.dialog` with captioned `ui.field`s, GAPS 52):
    `kind` names what its last button does, `texts` are its fields' text,
    `field` the focused one (the tests' typing door), `msg` the line under
    them that says why it stayed open. `fields` is built from (label,
    placeholder, cap) triples; `sub` is the line under its title, `ok` its
    last button's word."""

    def __init__(self, kind, title, fields, sub, ok):
        self.kind = kind
        self.title = title
        self.sub = sub
        self.ok = ok
        self.labels = tuple(f[0] for f in fields)
        self.hints = tuple(f[1] for f in fields)
        self.caps = tuple(f[2] for f in fields)
        self.texts = ["" for _f in fields]
        self.field = 0
        self.msg = None
        self.host = _ui_draw.AppHost()
        # the page under it washed already (held), and what it draws besides
        # its fields' text a function of `gen` (its line, `say`)
        self.o = {"wash": False, "gen": 0}

    def types(self, ch):
        """Whether typed byte `ch` changes the focused field's text."""
        t = self.texts[self.field]
        return (ch == 8 and t != "") or (32 <= ch < 127 and len(t) < self.caps[self.field])

    def say(self, msg):
        """Set the line under the fields that says why it stayed open."""
        if msg != self.msg:
            self.msg = msg
            self.o["gen"] += 1

    def open(self, texts, seed):
        for i in range(len(self.texts)):
            self.texts[i] = str(texts[i])[:self.caps[i]]
        self.host.key = seed           # the byte that opened it types nothing

    def key(self, ch):
        """One typed byte: Tab moves the focus, Enter commits, Esc cancels,
        anything else goes to the focused field. "commit", "cancel" or None."""
        if ch == 9:
            self.field = (self.field + 1) % len(self.texts)
            self.host.focus(self.labels[self.field])
            return None
        if ch in (10, 13):
            return "commit"
        if ch == 27:
            return "cancel"
        t = self.texts[self.field]
        if ch == 8:                 # Delete: nothing lies after the caret
            self.texts[self.field] = t[:-1]
        elif 32 <= ch < 127 and len(t) < self.caps[self.field]:
            self.texts[self.field] = t + chr(ch)
        self.say(None)
        return None



# The project's files (step 5 of docs/text_editing_2026-09.md) are reached from
# the Config tab rather than the Files app because the loader has to stay in the
# loop -- what it opens is reached through the Editor, which re-reads the folder
# on the way back (Workstation.open_project_file).
#
# A project file that is an ASSET is opened by the TAB that owns it, never as
# text -- the list is a router, not a viewer. `images/` is deliberately absent: a
# cart's image assets are copies the WALL/GAME flow made from a drawing and no
# tab edits one in place, so a picture takes the picture door instead.
_ASSET_TABS = {
    "sprites.moygfx": "paint",
    "map.moymap": "map",
    "sounds.json": "music",
    "blocks.json": "blocks",
}
_ASSET_SUBDIR_TABS = {"scenes": "scene"}

# The last code row of that list on a cart whose runtime runs more than one
# script: the ONE door that adds a file to a cart (#89). A sentinel rather than a
# real name, because the list's rows come from the store and a real file could be
# called anything -- including this.
_NEW_ROW = "New script"

# The Files page's words for what a file is and the tab it opens in.
_FILE_WORDS = {"sprites.moygfx": "Sprites", "map.moymap": "Map", "sounds.json": "Sounds",
               "blocks.json": "Blocks"}
_TAB_WORDS = {"code": "Code", "paint": "Art", "map": "World", "music": "Sound",
              "blocks": "Blocks", "scene": "World"}
_TAB_ICONS = {"code": "code", "paint": "sprite", "map": "map", "music": "music",
              "blocks": "blocks", "scene": "scene"}
_FILES_HINTS = ("confirm", "Open", "back", "Back")
# The file table's columns: a picture column (each row's icon), the file's
# name, the tab it opens in -- no header row.
_FILE_COLS = ("", "", "")
# A broken manifest's page: its one door, and its keys.
_FIX_HINTS = ("confirm", "Files", "back", "Back")
# The dialogs' lines: the one under the title, and why one stayed open.
_PROMPT_SUB = {"tone": "dim", "wrap": True, "lines": 2}
_PROMPT_MSG = {"tone": "danger", "wrap": True, "lines": 2}
# The file list's line saying why a row did not open.
_FILES_MSG = {"tone": "danger"}


class CardsLayout(LayoutBase):
    """The Config tab's geometry: the content rect under the bar (`cx`,
    `cy`, `cw`, `ch`) its pages draw in, from the SYSTEM canvas size and
    font scale, the T-Deck's 320x240 by the same formulas as every canvas."""


class CardsLayer:
    """The Config tab's content Layer (SYSTEM domain): its page fills the
    content rect under the bar on the reflowed system canvas, and
    handle_input/handle_pointer hand the frame's input to the page on show,
    hit-testing in SYSTEM coords."""

    id = "cards"
    domain = "system"

    # A card field MAY carry an optional `display` hint -- "gauge" | "count" |
    # "choice-icons" | "sprite-tiles" | "bg-thumbs" -- that draws the VALUE as a
    # picture a kid who can't read can recognize (the cell ones as a row of
    # tiles on the page, `SettingsPage._tiles_row`).
    _DISPLAYS = ("gauge", "count", "choice-icons", "sprite-tiles", "bg-thumbs")
    _CELL_DISPLAYS = ("choice-icons", "sprite-tiles", "bg-thumbs")

    def __init__(self, ws, names, err_text):
        self.ws = ws
        self._NAMES = names
        self._err_text = err_text
        # a desk pane's word for its page (EditorDesk._tab_pane): 2 where the
        # buffer `shift_last` reads holds it as the last draw left it, 1 a
        # window's frame drawn whole, 0 none (the handheld)
        self.vouch = 0
        self._broken_host = None      # the UI context of a page that failed to draw
        self._fix_host = _ui_draw.AppHost()     # a broken manifest's page's
        self._why = None              # (its reason, the width, the reason's lines)
        self._files_host = _ui_draw.AppHost()   # the Files page's
        self._files_o = {"id": "files"}
        # The open dialog (CART INFO or NEW SCRIPT), a `_Prompt`, or None.
        self.prompt = None
        # the page under it (`_draw_prompt`); `_rows` is the desk Editor's
        # name for it (its rows frames)
        self._hold = self._rows = _ui_draw.Hold()
        # The project's file list: None when closed, else `_listing`'s
        # {"rows", "msg", "names", "cells", "cur"} -- see _open_files.
        self.files = None
        self._page = None             # the cards on the verbs (editor_settings.py)
        sc = ws.sys_canvas
        self.layout = CardsLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def relayout(self, w, h, fs, cs=None):
        """Rebuild the geometry (#39 step 3) -- called by ws._relayout on a
        font-scale change."""
        self.layout = CardsLayout(w, h, fs, chrome_scale=cs)

    def reset(self):
        """Close the file list and any dialog (called by ws.open on a fresh
        cart)."""
        self.files = None
        if self.prompt is not None:
            # never leak an open dialog across a cart switch
            self.prompt = None
            self.ws._set_text_mode(False)

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        ws = self.ws
        ws._reset_canvas_state()          # game-canvas hygiene (degradation shares it)
        p = self.prompt
        hold = self._hold
        if p is not None and self.prompt_rows(dt):
            return
        how = hold.under(ws, None if p is None else self._held_key(p), self._page_rect(), dt)
        if how == _ui_draw.HOLD_PUT:
            ws.bar_layer._draw_status_strip("menu")
            self._draw_prompt(True, dt)
            hold.note()
            return
        if how == _ui_draw.HOLD_DRAW:
            try:
                self._draw_cards()
            except Exception as exc:  # noqa: BLE001
                # A malformed card (e.g. a bad tiles/choices entry) must NOT escape the
                # frame loop -- the device would hang silently with no error surface.
                # Fall back to a readable page (on the SYSTEM canvas, where this layer
                # lives); the unified bar (drawn below) keeps the context X reachable
                # so the kid can exit.
                ws.cart_error = self._err_text(exc)
                print("Moybyte cards error:", exc)
                self._draw_broken(ws.cart_error)
        # The Editor's lent top-bar zone (Stage 4, #46 zoned bar): the tab ladder +
        # PLAY, replacing the old pause-only tool switcher for this tab. Drawn LAST
        # (chrome over content), byte-identical cost to the #43 strip cache.
        ws.bar_layer._draw_status_strip("menu")
        # An open dialog draws OVER the bar too -- same order as the block
        # editor's blk_kbd prompt (chrome, then any modal on top).
        if self.prompt is not None:
            self._draw_prompt(False, dt)
            hold.note()
        else:
            hold.shown(ws, ws.sys_canvas, self._page_still(), dt)

    def prompt_host(self):
        """The open dialog's host (`ui_draw.AppHost`), None where none is up."""
        return None if self.prompt is None else self.prompt.host

    def prompt_rows(self, dt=0):
        """Begin the open dialog's frame ahead of the page under it, once a
        painted frame: where it is a dialog's rows frame (a field typed
        into, its focus moved, the page held as the frames before left it:
        `ui_draw.Hold.rows_frame`), draw its changed rows alone and answer
        True; else the frame draws the page and the dialog as ever."""
        p = self.prompt
        ws = self.ws
        if p is None or not self._hold.rows_frame(
                ws, self._held_key(p), p.o["gen"], p.host, self._page_rect(),
                _layout_base.metric_class() == _layout_base.DESK, dt):
            return False
        ws.bar_layer._draw_status_strip("menu")
        self._draw_prompt(True, dt)
        self._hold.note()
        return True

    def _page_rect(self):
        lay = self.layout
        return (lay.cx, lay.cy, lay.cw, lay.ch)

    def _held_key(self, p):
        """What the page under dialog `p` is a function of while it is up."""
        ws = self.ws
        return (p, ws.sys_canvas, self._page_rect(), ws.layout.fs, ws.look.look_gen)

    def _page_still(self):
        """Whether the page drawn last draws the same next frame."""
        if self.files is not None:
            return self._files_host.still()
        if self._broken():
            return self._fix_host.still()
        return self._page is None or self._page.host.still()

    def _draw_broken(self, text):
        """What a card that failed to draw leaves: the error, exact, under
        the bar."""
        ws = self.ws
        host = self._broken_host
        if host is None:
            host = self._broken_host = _ui_draw.AppHost()
        lay = self.layout
        host.begin(ws.sys_canvas, ws.theme_colors, (lay.cx, lay.cy, lay.cw, lay.ch))
        _ui.page("")
        _ui.empty("warn", "This page didn't draw", (str(text or ""),))

    def handle_input(self, i):
        ws = self.ws
        if self.prompt is not None:
            return self._prompt_input(i)
        if self.files is not None:
            return self._files_input(i)
        if self._broken():
            return self._fix_input(i)
        page = self.page()
        if (i.pressed("run") or i.pressed("a")) and not page.on_door():
            ws.defer(ws._leave_menu)   # Enter PLAYs (a device's Enter is "a")
            return True
        if page.open_door(i):
            if self.prompt is not None:
                self._hold.opened(ws)  # Cart info over the page as it was
            return True
        if page.keys(i):
            ws._dirty = True
        ws._leave_or_home(ws.exit)
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if self.prompt is not None:
            return self._prompt_pointer(px, py, click)
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        if self.files is not None:
            return self._files_pointer(px, py, click)
        if self._broken():
            if self._fix_host.point(px, py, click, ws.pointer.down):
                ws._dirty = True
            return True
        if self.page().point(px, py, click, ws.pointer.down):
            ws._dirty = True
        return True

    # -- the cards' helpers: a field's check, a picker's pictures -------------

    def _resolve_tiles(self, f):
        """For a `sprite-tiles` field, the list of sprite tile ids its choices map
        to. `choices` may be ints (tile ids directly) or names paired with a
        parallel `tiles` list. Returns ints; non-resolvable entries become 0."""
        src = f.get("tiles")
        if not src:
            src = f.get("choices", [])
        out = []
        for c in src:                          # guard BOTH branches: a non-numeric
            try:                               # tiles/choices entry must not escape
                out.append(int(c))             # _draw_cards -> device hang (#15).
            except (TypeError, ValueError):
                out.append(0)
        return out

    def _validate_field(self, f):
        """Sanity-check ONE `edit` field definition: returns a short human
        reason it can't be drawn/stepped, or None when it's fine. The Settings
        page (`SettingsPage._fields`) leaves a field that fails it off the
        page, so a bad hand-edited manifest.json/config.json costs one card,
        never the Config tab -- draw()'s try/except stays the net for a
        genuinely UNFORESEEN crash; this catches the KNOWN-bad shapes (missing
        key/type, min>max, a zero step, empty/missing choices, an unknown or
        type-mismatched `display`)."""
        if not isinstance(f, dict):
            return "not a card"
        key = f.get("key")
        if not key or not isinstance(key, str):
            return "missing key"
        t = f.get("type")
        if t not in ("int", "choice"):
            return "bad type %r" % (t,)
        if t == "int":
            lo, hi = f.get("min"), f.get("max")
            if lo is not None and hi is not None:
                try:
                    if float(lo) > float(hi):
                        return "min > max"
                except (TypeError, ValueError):
                    return "bad min/max"
            step = f.get("step", 1)
            try:
                if float(step) == 0:
                    return "step is 0"
            except (TypeError, ValueError):
                return "bad step"
        else:                                             # "choice"
            ch = f.get("choices")
            if not isinstance(ch, list) or not ch:
                return "no choices"
        disp = f.get("display")
        if disp is not None and disp not in self._DISPLAYS:
            return "bad display %r" % (disp,)
        if disp in ("gauge", "count") and t != "int":
            return "display needs type int"
        if disp in self._CELL_DISPLAYS and t != "choice":
            return "display needs type choice"
        return None

    # -- the pages -----------------------------------------------------------

    def page(self):
        """The Settings page on the verbs, built at its first draw."""
        if self._page is None:
            try:
                from editor_settings import SettingsPage
            except ImportError:  # pragma: no cover - host fallback
                from runtime.editor_settings import SettingsPage
            self._page = SettingsPage(self.ws, _ui_draw.AppHost("editor_settings"),
                                      (self._open_meta, self._open_files))
        return self._page

    def _broken(self):
        """The reason this cart's manifest would not parse, or "" -- what
        moy_carts.load left behind instead of dropping the project."""
        cart = self.ws.project.cart
        return (cart.get("broken") or "") if cart else ""

    def _draw_cards(self):
        """The page the tab shows: the file list where it is open, else a
        broken manifest's, else the cards."""
        ws = self.ws
        rect = self._page_rect()
        desk = _layout_base.metric_class() == _layout_base.DESK
        if self.files is not None:
            self._draw_files(rect, desk)
            return
        why = self._broken()
        if why:
            self._draw_manifest(why, rect, desk)
            return
        if self.vouch:
            _ui_draw.window_frame(self.vouch)
        try:
            self.page().draw(ws.sys_canvas, ws.theme_colors, rect, desk)
        finally:
            if self.vouch:
                _ui_draw.window_frame(0)

    def _draw_manifest(self, why, rect, desk):
        """A cart whose manifest did not parse: the reason, exact, and Files,
        the door to the file that broke it (the cart has no `edit` fields
        and no title to edit until it parses)."""
        ws = self.ws
        host = self._fix_host
        fs = self.layout.fs
        host.begin(ws.sys_canvas, ws.theme_colors, rect, desk)
        x, y, w, h = _ui.page("")
        _ui.hints(_FIX_HINTS)
        lines = self._why
        if lines is None or lines[0] != why or lines[1] != w:
            lines = self._why = (why, w, tuple(_ui_draw.wrap_px(why, w - 32 * fs, fs)[:3]))
        _ui.at(x, y + (h // 6 if desk else 10 * fs), w, h)
        at = _ui.empty("warn", "The manifest didn't read", lines[2])
        bw, bh = (180, 40) if desk else (124 * fs, 26 * fs)
        _ui.at(x + (w - bw) // 2, at + (6 if desk else 3 * fs), bw, bh)
        if _ui.button("Files", "primary", {"id": "Files"}):
            self._open_files()
        if host.owed or host.tap == 2:
            ws.request_frame()

    def _fix_input(self, i):
        """A broken manifest's page's keys: A (or Enter) opens Files, its one
        door; B leaves and HOME backs out."""
        ws = self.ws
        if i.pressed("a") or i.pressed("run"):
            self._open_files()
            return True
        if self._fix_host.keys(i):
            ws._dirty = True
        ws._leave_or_home(ws.exit)
        return True

    def draw_choice(self, f, k, rect):
        """Choice `k`'s picture of a cell card `f` in `rect` on the system
        canvas, for the Settings page on the verbs (editor_settings.py): its
        sprite at the largest whole zoom the rect holds, its glyph, or its
        background's preview."""
        ws = self.ws
        cv = ws.sys_canvas
        x, y, w, h = rect
        disp = f.get("display")
        if disp == "sprite-tiles":
            tiles = self._resolve_tiles(f)
            sheet = ws.project.sheet
            if sheet is None:
                return
            img = sheet.tile_image(tiles[k] if k < len(tiles) else 0, 0)
            z = max(1, min(w, h) * 3 // 4 // 8)
            cv.spr(img, x + (w - 8 * z) // 2, y + (h - 8 * z) // 2, z)
        elif disp == "bg-thumbs":
            ch = f.get("choices") or []
            m = max(2, min(w, h) // 8)
            self._draw_bg_thumb(str(ch[k]) if k < len(ch) else "", (x + m, y + m, w - 2 * m,
                                                                    h - 2 * m))
        else:
            icons = f.get("icons") or []
            side = max(12, min(w, h) // 2)
            ws._glyph(icons[k] if k < len(icons) else "dot",
                      (x + (w - side) // 2, y + (h - side) // 2, side, side),
                      ws.theme_colors["ink"], cv)

    # Each bg-thumbs choice is drawn as a tiny "what the screen will look like"
    # preview. A cart reads the chosen name in cfg("bg") and paints to match.
    # "night"/"stripes" get a patterned thumbnail; any other name renders as a
    # solid swatch via NAMES.get, so arbitrary palette colors (e.g. "indigo")
    # just work -- no preset list to keep in sync.

    def _draw_bg_thumb(self, name, rect):
        """Paint a small preview of background preset `name` inside `rect`."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        fs = self.layout.fs
        x, y, w, h = rect
        if name == "night":                              # starfield
            cv.rect(x, y, w, h, NAMES["black"])
            for sx, sy in ((4, 4), (14, 9), (24, 5), (30, 15), (9, 17), (20, 12)):
                if fs <= 1:
                    cv.pix(x + sx, y + sy, NAMES["white"])
                else:
                    cv.rect(x + sx * fs, y + sy * fs, fs, fs, NAMES["white"])
        elif name == "stripes":
            for i in range(0, w, 6 * fs):
                cv.rect(x + i, y, 3 * fs, h, NAMES["indigo"])
                cv.rect(x + i + 3 * fs, y, 3 * fs, h, NAMES["dark_blue"])
        else:                                            # a solid color swatch
            cv.rect(x, y, w, h, NAMES.get(name, NAMES["black"]))

    # -- Files: the project's own files (step 5) -----------------------------
    #
    # A ROUTER over one project folder, not a viewer: a text-shaped file (by
    # `text_modes`) opens in the shell's editor handle through the SAME request
    # door Notes uses, the cart's scripts open the Code tab, and an asset
    # opens the tab that owns it. Everything it lists comes from the store's
    # `list_files` on the project KIND (moy_carts.PROJECT_KIND), so the page
    # never spells a cart's layout itself.

    def _open_files(self):
        ws = self.ws
        cart = ws.project.cart
        path = (cart or {}).get("path")
        if not path:
            return
        rows, msg = (), None
        try:
            kind = ws.carts_store.project_kind(path)
            rows = tuple(ws._with_sd(
                lambda: ws.carts_store.list_files(kind, ws.carts_root)))
        except Exception as exc:  # noqa: BLE001 -- an unreadable folder lists none
            msg = self._err_text(exc)
        if self._can_add_source(cart):
            rows = tuple(rows) + (_NEW_ROW,)
        self.files = self._listing(rows, msg)
        self._files_host.reset()
        ws._dirty = True

    def _listing(self, rows, msg):
        """The file list over the store's `rows`: `names` in the order the
        table shows them (the cart's scripts, New script last, then the
        rest), `cells` each one's icon, name and the tab it opens in, `cur`
        the current row, `msg` the line that says why a row did not open."""
        ws = self.ws
        cart = ws.project.cart if ws.project is not None else None
        sources = ws.carts_store.cart_sources(cart) if cart and ws.carts_store else ()
        code = [r for r in rows if r in sources or r == _NEW_ROW]
        names = tuple(code + [r for r in rows if r not in code])
        cells = tuple(self._file_cells(n, n in code) for n in names)
        return {"rows": rows, "msg": msg, "names": names, "cells": cells, "cur": 0}

    def _file_cells(self, name, code):
        """A file's row: its icon, its name, and the tab it opens in."""
        if name == _NEW_ROW:
            return ("new", _NEW_ROW, "")
        tab = "code" if code else self._asset_tab(name)
        if tab is not None:
            return (_TAB_ICONS.get(tab, "doc"), _FILE_WORDS.get(name, name),
                    _TAB_WORDS.get(tab, ""))
        if _modes.is_image(name):
            return ("art", name, "Paint")
        return ("doc", name, "Text")

    def _asset_tab(self, name):
        """The tab that owns asset `name`, or None."""
        tab = _ASSET_TABS.get(name)
        if tab is None and "/" in name:
            tab = _ASSET_SUBDIR_TABS.get(name.split("/")[0])
        return tab

    def _can_add_source(self, cart):
        """Whether this folder may gain another SCRIPT: a writable cart whose
        runtime actually loads more than main (moy_carts.multi_script). The
        gate is the store's, not a runtime string spelled here."""
        store = self.ws.carts_store
        return bool(self.ws.can_manage and store is not None
                    and store.multi_script(cart))

    def _close_files(self):
        self.files = None
        if self._page is not None:
            self._page.recount()
        self.ws._dirty = True

    def _files_open(self, name):
        """Route one project file. A script is the Code tab (that is where
        PLAY, the journal and crash-to-code live); an asset is its own tab; a
        text file goes through the request door and comes BACK here."""
        ws = self.ws
        cart = ws.project.cart
        if cart is None:
            return
        if name == _NEW_ROW:
            self._open_newf()
            return
        # ANY of the cart's scripts, not only main (SPEC.md 4, #89): the Code
        # tab holds a file, and it is the tab that has PLAY, the journal and
        # crash-to-code (a crash at `p8.lua:412:` lands there).
        if name in ws.carts_store.cart_sources(cart):
            self._close_files()
            ws.editor_app.set_tab("code")
            ws.open_code_file(name)
            return
        tab = self._asset_tab(name)
        if tab is not None:
            self._close_files()
            ws.editor_app.set_tab(tab)
            return
        if _modes.is_image(name):
            # A cart's OWN image (`images/*.moyimg`, `cover.png`) is a picture, so it
            # takes the picture door -- the same `ws.open_image` the Files
            # router takes, opening it in Paint on this project's kind and
            # writing it back in place. A picture is never refused for its
            # shape: one Paint cannot edit opens read-only.
            self._close_files()
            if not ws.open_image(name, cart=cart):
                self.files = self._listing((), "There's no Paint app to open it in.")
            return
        if "/" in name:
            # Anything else in a subfolder no tab claims. Saying so beats
            # opening a blob as text.
            self.files["msg"] = "Nothing on this console opens that file."
            ws._dirty = True
            return
        mode = _modes.mode_for(name)
        self._close_files()
        if not ws.open_project_file(cart, name, mode):
            self.files = self._listing((), "That file didn't open.")

    def _files_input(self, i):
        ws = self.ws
        if i.pressed("b") or i.pressed("home"):
            self._close_files()
            if i.pressed("home"):
                ws.home_key()
            return True
        if self._files_host.keys(i):
            ws._dirty = True
        return True

    def _files_pointer(self, px, py, click):
        if self._files_host.point(px, py, click, self.ws.pointer.down):
            self.ws._dirty = True
        return True

    def _draw_files(self, rect, desk):
        """The project's files as a page of the tab (GAPS 52): a placed table,
        each file's icon, its name and the tab it opens in -- the cart's
        scripts first, New script last among them, then its art and sound. A
        tap on a row, or Enter on the current one, opens it."""
        ws = self.ws
        f = self.files
        host = self._files_host
        host.begin(ws.sys_canvas, ws.theme_colors, rect, desk)
        room = _ui.page("Files")
        if _ui_draw.hint_kind() != "touch":
            band, room = _ui.cut_bottom(
                room, _ui_draw.metrics("value_row")[17 if desk else 16] * self.layout.fs)
            _ui.at(*band)
            _ui.hints(_FILES_HINTS)
        names = f["names"]
        msg = f["msg"]
        if not names:
            _ui.at(*room)
            _ui.empty("doc", "No files", (msg,) if msg else ())
        else:
            if msg:
                line, room = _ui.cut_bottom(room, _ui.text_size(msg)[1] + 8 * self.layout.fs)
                _ui.at(*line)
                _ui.label(msg, _FILES_MSG)
            _ui.at(*room)
            cur, opened = _ui.table("", _FILE_COLS, f["cells"], f["cur"], self._files_o)
            if cur != f["cur"]:
                f["cur"] = cur
                if not _ui.keys_taken():
                    opened = True         # a tap opens the row it lands on
            if opened and 0 <= cur < len(names):
                self._files_open(names[cur])
        if host.owed or host.tap == 2:
            ws.request_frame()

    # -- the two dialogs: CART INFO (#94) and NEW SCRIPT (#89) ---------------
    #
    # One prompt state machine (`_Prompt`), parameterised by its fields and by
    # what OK means. CART INFO edits the manifest's title/author through
    # Project.commit_manifest (`permissions` stays read-only: see the comment
    # over moy_carts.save_manifest_meta). NEW SCRIPT is the ONE door that adds
    # a file to a cart, the file list's last script row (and the Code tab's
    # scripts menu) and nowhere else, because a console for eight-year-olds
    # does not want a New File button two taps from every cart, and a cart
    # that is one file should stay one file unless somebody went looking.
    #
    # Both are `ui.dialog` with captioned `ui.field`s (GAPS 52) over the page
    # dimmed (D-01), on their own UI context: the keys type (text mode, which
    # `widgets.arm_prompt` takes), up/down, Tab or the trackball move between
    # fields, Enter is the last button and Esc or the T-Deck's Mic (a
    # field's cancel, `Workstation.cancel_field`) closes it. The T-Deck's
    # dialog has no row of Cancel and OK -- its keys are the hint strip's --
    # where a touch glass and the desk add them.

    def _open_prompt(self, kind, title, fields, texts, sub, ok):
        ws = self.ws
        _arm_prompt(ws)
        self.prompt = _Prompt(kind, title, fields, sub, ok)
        self.prompt.open(texts, getattr(ws.input, "last_key", 0) or 0)
        self.prompt.host.held = self._hold.ok
        ws._dirty = True

    def _open_meta(self):
        cart = self.ws.project.cart
        if not cart:
            return
        self._open_prompt("meta", "Cart info",
                          (("Title", "", 24), ("Made by", "Your name (you can leave it)", 24)),
                          (str(cart.get("title") or ""),
                           str(cart.get("author") or "")), None, "Save")

    def _open_newf(self):
        cart = self.ws.project.cart or {}
        main = cart.get("main", "main.py")
        lang = "Lua" if main.endswith(".lua") else "Python"
        self._open_prompt("newf", "New script", (("Name", "helpers", 24),), ("",),
                          "A %s file beside %s" % (lang, main), "Add")

    def _close_prompt(self):
        self.prompt = None
        self.ws._set_text_mode(False)
        self.ws._dirty = True

    def _commit_prompt(self):
        p = self.prompt
        if p is None:
            return
        if p.kind == "meta":
            self._commit_meta(p)
        else:
            self._commit_newf(p)

    def _commit_meta(self, p):
        ws = self.ws
        title = p.texts[0].strip()
        author = p.texts[1].strip()
        if not title:
            p.say("Give it a title.")
            ws._dirty = True
            return                          # stay open: never persist a blank title
        if not ws.project.commit_manifest(title=title, author=author):
            p.say("It didn't save. Try again.")
            ws._dirty = True
            return
        self._close_prompt()

    def _newf_filename(self, typed):
        """The file a typed name lands on: slugged to letters, digits and
        underscore, under the cart's own runtime extension. `free_file_name`'s
        rule for a project file is "keep it verbatim" (a FORMAT chose the name),
        which is right for manifest.json and wrong for something a person is
        typing now, so the slug is here."""
        cart = self.ws.project.cart or {}
        mainf = cart.get("main", "main.py")
        cut = mainf.rfind(".")
        ext = mainf[cut:] if cut > 0 else ".lua"
        stem = typed.strip()
        if stem.lower().endswith(ext):
            stem = stem[:-len(ext)]
        out = ""
        last = "_"
        for ch in stem.lower():
            if ch == "_" or ch.isalpha() or ch.isdigit():
                out += ch
            elif last != "_":
                out += "_"
            last = out[-1:] or "_"
        out = out.strip("_")[:24]
        if not out or not out[0].isalpha():
            return None
        return out + ext

    def _commit_newf(self, p):
        ws = self.ws
        cart = ws.project.cart
        name = self._newf_filename(p.texts[0])
        if name is None:
            p.say("Start its name with a letter.")
            ws._dirty = True
            return
        made = ws._with_sd(lambda: ws.carts_store.add_source(cart, name,
                                                             "-- " + name + "\n"))
        if made is None:
            p.say("There's a file with that name.")
            ws._dirty = True
            return
        self._close_prompt()
        self._close_files()
        ws.editor_app.set_tab("code")
        ws.open_code_file(made)

    def _prompt_event(self, ev):
        if ev == "commit":
            self._commit_prompt()
        elif ev == "cancel":
            self._close_prompt()

    def _prompt_key(self, ch):
        """One typed byte into the open prompt (the tests' door)."""
        self._prompt_event(self.prompt.key(ch))

    def _prompt_input(self, i):
        p = self.prompt
        if p.host.keys(i):
            self.ws._dirty = True
        ch = p.host.inp[5]
        if ch == 27 or i.pressed("stop"):
            p.host.inp[5] = 0
            self._close_prompt()
        elif p.msg is not None and p.types(ch or (p.host.inp[_ui_draw.UI_IN_LEN - 1] & 0xFFFF)):
            p.say(None)                 # the text it will change clears the line, before the frame
        return True

    def _prompt_pointer(self, px, py, click):
        if self.prompt.host.point(px, py, click, self.ws.pointer.down):
            self.ws._dirty = True
        return True

    def _draw_prompt(self, held, dt):
        """The open dialog over the page, dimmed: its title, the line under
        it, a captioned field each, why it stayed open, and on a touch glass
        or the desk Cancel and its last button. `held`: the page under it
        was put back washed (`ui_draw.Hold`), else it was drawn this frame and
        is washed and held here."""
        ws = self.ws
        p = self.prompt
        lay = self.layout
        host = p.host
        desk = _layout_base.metric_class() == _layout_base.DESK
        self._hold.dialog(host, ws.sys_canvas, ws.theme_colors, self._page_rect(), desk)
        tdeck = _ui.input_kind() == "tdeck"
        keys = (("scroll", "Field") if len(p.texts) > 1 else ()) + (
            "confirm", p.ok, "cancel", "Cancel")
        if _ui_draw.hint_kind() != "touch":
            b = _ui_draw.metrics("value_row")[17 if desk else 16] * lay.fs
            ws.sys_canvas.rect(lay.cx, lay.cy + lay.ch - b, lay.cw, b, ws.theme_colors["panel"])
            _ui.at(lay.cx, lay.cy + lay.ch - b, lay.cw, b)
            _ui.hints(keys)
        if not held:
            self._hold.wash(ws, ws.sys_canvas, self._page_rect(), self._held_key(p),
                            self._page_still(), dt)
        pick = _ui.dialog(p.title, () if tdeck else ("Cancel", p.ok), "primary", p.o)
        if p.sub:
            _ui.label(p.sub, _PROMPT_SUB)
        done = False
        for i in range(len(p.texts)):
            o = {"id": p.labels[i]}
            if p.hints[i]:
                o["hint"] = p.hints[i]
            got, enter = _ui.field(p.labels[i], p.texts[i], p.caps[i], o)
            if got != p.texts[i]:
                p.texts[i] = got
                p.say(None)
            done = done or enter
        if p.msg:
            _ui.label(p.msg, _PROMPT_MSG)
        _ui.dialog()
        f = _ui.focus()
        if f in p.labels:
            p.field = p.labels.index(f)
        if pick == 1 or done:
            self._commit_prompt()
        elif pick == 0:
            self._close_prompt()
        if host.owed or host.tap == 2:
            ws.request_frame()
