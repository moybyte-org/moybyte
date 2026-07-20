"""Writer -- the kid notebook system app (notes, stories, project docs).

Presented exactly like Paint/Appearance: a `.moy` cartridge identity on the
launcher (`writer.moy`, so it seeds/versions/exports like every cart) backed by
a responsive SYSTEM process the console spawns instead of the Player -- a text
app must reflow to a P4/web window, while Player is the fixed 320x240 contract.

Notes are plain text. The buffer/caret core is the shared `CodeEditor` (the
same editing behavior a kid already knows from the Code tab), drawn here as a
ruled paper page instead of a dark code screen. The whole notebook persists as
ONE crash-safe `notes.json` beside Paint's `artwork.moyimg` (moy_carts owns the
path + atomic write), and the app AUTOSAVES -- on every view change, on the
exit tap, and every few dozen keystrokes -- because a kid never presses save.

A note's title is its first non-empty line (real-notebook rule): renaming is
just editing the first line, so there is no rename UI to learn."""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui


import json

try:
    from editors import CodeEditor
except ImportError:  # pragma: no cover - direct host import
    from runtime.editors import CodeEditor

try:
    from app_shell import ListShellLayout, ListShellApp
except ImportError:  # pragma: no cover - direct host import
    from runtime.app_shell import ListShellLayout, ListShellApp


MAX_NOTES = 12          # a kid notebook, not a filesystem; the list stays tappable
MAX_CHARS = 8000        # per note -- bounds the SD write + device memory
AUTOSAVE_KEYS = 24      # flush after this many buffered keystrokes
PAPER = 7               # white -- same paper index Paint uses
INK = 0                 # black


def _title_of(body):
    for ln in str(body).split("\n"):
        ln = ln.strip()
        if ln:
            return ln[:40]
    return "EMPTY PAGE"


class WriterLayout(ListShellLayout):
    def __init__(self, w, h, fs=1, windowed=False):
        self._init_frame(w, h, fs, windowed)
        fs = self.fs
        self.toolbar_h = 24 * fs
        self.cell = 8 * fs
        self.lh = 10 * fs
        x = 6 * fs
        y = self.bar_h + 3 * fs
        bh = self.toolbar_h - 6 * fs
        self.back_btn = (x, y, 58 * fs, bh)
        self.del_btn = (x + 62 * fs, y, 66 * fs, bh)
        self.status_x = x + 134 * fs
        self.status_y = y + max(0, (bh - 8 * fs) // 2)
        # The page (edit view): a text grid sized to the window, like CodeLayout.
        self.tx = 8 * fs
        self.ty = self.bar_h + self.toolbar_h + 3 * fs
        self.cols = max(1, (self.w - 2 * self.tx) // self.cell)
        self.rows = max(1, (self.h - self.ty - 4 * fs) // self.lh)
        self.text_area = (self.tx - 2 * fs, self.ty - 2 * fs,
                          self.cols * self.cell + 4 * fs,
                          self.rows * self.lh + 4 * fs)
        # The notebook (list view): one row per note below the title band --
        # a reading list, not an icon grid (geometry + row_rect: ListShellLayout).
        self._init_list(self.bar_h + self.toolbar_h)


class WriterAppLayer(ListShellApp):
    """Notebook list + a ruled-paper text page over the shared CodeEditor core."""

    id = "writer"
    domain = "system"
    TITLE = "WRITER"
    # The shipped identity (ListShellApp.is_app gates on these).
    APP_TITLE = "Writer"
    APP_PERM = "notebook"
    APP_FOLDER = "writer.moy"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self.names = names
        self._in = in_rect
        self.layout = WriterLayout(ws.sys_canvas.w, ws.sys_canvas.h,
                                   ws._effective_font_scale(), ws.windowed_chrome)
        self.mode = "list"            # list | edit
        self.notes = []               # [{"title": str, "body": str}, ...]
        self.active = -1              # index of the note open in the editor
        self.sel = 0                  # list selection (0 = the NEW row)
        self.top = 0                  # first visible list row
        self.editor = None            # CodeEditor while mode == "edit"
        self.status = "MY NOTEBOOK"
        self.del_armed = False        # two-tap DELETE confirm
        self._ekey_prev = 0           # typed-key edge tracker (code_layer idiom)
        self._drag = None             # page drag-scroll anchor
        self._pending_keys = 0        # keystrokes since the last flush (autosave)
        self._loaded = False
        self._save_failed = False

    # -- store ---------------------------------------------------------------
    # (is_app / _store_ready / _load_blob / _persist: ListShellApp)

    def _load(self):
        self.notes = []
        data = self._load_blob(
            lambda: self.ws.carts_store.load_notes(self.ws.carts_root))
        if isinstance(data, dict):
            for n in (data.get("notes") or [])[:MAX_NOTES]:
                body = str((n or {}).get("body", ""))[:MAX_CHARS]
                self.notes.append({"title": _title_of(body), "body": body})
        self._loaded = True

    def flush(self, force=False):
        """Sync the open editor into its note and persist the whole notebook.
        The autosave verb: cheap to call, no-ops when nothing changed."""
        changed = False
        ed = self.editor
        if ed is not None and 0 <= self.active < len(self.notes):
            body = ed.text()[:MAX_CHARS]
            if ed.dirty or body != self.notes[self.active]["body"]:
                self.notes[self.active] = {"title": _title_of(body), "body": body}
                ed.dirty = False
                changed = True
        if not (changed or force):
            return True
        self._pending_keys = 0
        blob = json.dumps({"format": "moynotes-v1",
                           "notes": self.notes})
        return self._persist(lambda: self.ws.carts_store.save_notes(
            blob, self.ws.carts_root))

    # -- lifecycle -----------------------------------------------------------

    def relayout(self, w, h, fs):
        self.layout = WriterLayout(w, h, fs, self.ws.windowed_chrome)
        if self.editor is not None:
            self.editor.set_view_size(self.layout.cols, self.layout.rows)

    def open(self):
        self._load()
        self.mode = "list"
        self.editor = None
        self.active = -1
        self.sel = 0
        self.top = 0
        self.del_armed = False
        self._ekey_prev = 0
        self._pending_keys = 0
        self.status = "MY NOTEBOOK"
        self.ws._dirty = True

    # -- note verbs ------------------------------------------------------------

    def _open_note(self, index):
        if not (0 <= index < len(self.notes)):
            return
        self.flush()
        self.active = index
        lay = self.layout
        self.editor = CodeEditor(self.notes[index]["body"], lay.cols, lay.rows)
        self.mode = "edit"
        self.del_armed = False
        self._ekey_prev = 0
        self.status = self.notes[index]["title"]
        self.ws._dirty = True

    def _new_note(self):
        if len(self.notes) >= MAX_NOTES:
            self.status = "NOTEBOOK FULL"
            self.ws._dirty = True
            return
        self.notes.append({"title": "EMPTY PAGE", "body": ""})
        self._open_note(len(self.notes) - 1)

    def _back_to_list(self):
        self.flush(force=True)
        self.sel = self.active + 1 if self.active >= 0 else 0
        self.mode = "list"
        self.editor = None
        self.active = -1
        self.del_armed = False
        self.status = "MY NOTEBOOK"
        self.ws._dirty = True

    def _delete_active(self):
        if not (0 <= self.active < len(self.notes)):
            return
        del self.notes[self.active]
        self.editor = None
        self.active = -1
        self.mode = "list"
        self.sel = 0
        self.del_armed = False
        self.status = "PAGE TORN OUT"
        self.flush(force=True)
        self.ws._dirty = True

    # -- input -----------------------------------------------------------------

    def handle_input(self, inp):
        if self.mode == "list":
            # the NEW row + notes (nav + scroll window: ListShellApp)
            return self._list_nav(inp, len(self.notes) + 1)
        self._typed_keys(inp)
        return True

    def _typed_keys(self, inp):
        ed = self.editor
        if ed is None:
            return
        k = self._edge_key(inp)
        if not k:
            return
        if len(ed.text()) >= MAX_CHARS and k not in (0x08, 0x7F):
            self.status = "PAGE FULL"
        elif ed.key(k):
            self._pending_keys += 1
            self.status = _title_of(ed.text())
            if self._pending_keys >= AUTOSAVE_KEYS:
                self.flush()

    def handle_pointer(self, px, py, click):
        ws = self.ws
        lay = self.layout
        if click and not ws.windowed_chrome and py < lay.bar_h:
            # The OS bar (context-X exits): flush FIRST so an exit never loses text.
            self.flush(force=True)
            return bool(ws.bar_layer.handle_bar_tap("tool", px, py))
        if self.mode == "list":
            if not click:
                return True
            for i in range(self.top, min(self.top + lay.list_rows,
                                         len(self.notes) + 1)):
                if self._in(px, py, lay.row_rect(i - self.top)):
                    self.sel = i
                    self._tap_row(i)
                    return True
            return True
        # -- edit view ---------------------------------------------------------
        self._page_drag(px, py)
        if not click:
            return True
        if self._in(px, py, lay.back_btn):
            self._back_to_list()
            return True
        if self._in(px, py, lay.del_btn):
            if self.del_armed:
                self._delete_active()
            else:
                self.del_armed = True
                self.status = "TAP AGAIN TO TEAR OUT"
                self.ws._dirty = True
            return True
        self.del_armed = False
        if self.editor is not None and self._in(px, py, lay.text_area):
            self.editor.place((px - lay.tx) // lay.cell, (py - lay.ty) // lay.lh)
            self.ws._dirty = True
        return True

    def _tap_row(self, i):
        if i == 0:
            self._new_note()
        else:
            self._open_note(i - 1)

    def _page_drag(self, px, py):
        # Touch drag inside the page pans the text (content follows the finger).
        ed = self.editor
        lay = self.layout
        if ed is None or not self.ws.pointer.down \
                or not self._in(px, py, lay.text_area):
            self._drag = None
            return
        if self._drag is None:
            self._drag = (px, py)
            return
        drows = (py - self._drag[1]) // lay.lh
        dcols = (px - self._drag[0]) // lay.cell
        if drows or dcols:
            ed.scroll(-drows, -dcols)
            self._drag = (px, py)
            self.ws._dirty = True

    # -- draw --------------------------------------------------------------------

    def _button(self, cv, label, r, hot=False):
        # One shared implementation now (ui.chip) -- pixel-identical delegate.
        _ui.chip(cv, self.ws.theme_colors, r, label, hot=hot, fs=self.layout.fs)

    def draw(self, dt):
        cv = self.ws.sys_canvas
        lay = self.layout
        th = self.ws.theme_colors
        cv.cls(th["panel"])
        _ui.toolbar(cv, th, (0, lay.bar_h, lay.w, lay.toolbar_h))
        if self.mode == "edit":
            self._button(cv, "NOTES", lay.back_btn)
            self._button(cv, "TEAR OUT", lay.del_btn, hot=self.del_armed)
        label = self.status[:max(1, (lay.w - lay.status_x) // (8 * lay.fs) - 1)]
        sx = lay.status_x if self.mode == "edit" else 6 * lay.fs
        cv.print(label, sx, lay.bar_h + 8 * lay.fs, th["title_ink"], 1)
        if self.mode == "list":
            self._draw_list(cv)
        else:
            self._draw_page(cv)
        if not self.ws.windowed_chrome:
            self.ws.bar_layer._draw_status_strip("tool")

    def _draw_list(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        for i in range(self.top, min(self.top + lay.list_rows,
                                     len(self.notes) + 1)):
            x, y, w, h = lay.row_rect(i - self.top)
            selected = i == self.sel
            if i == 0:
                cv.rect(x, y, w, h, th["accent"] if selected else th["hilite"])
                cv.print("+ NEW PAGE", x + 6 * fs,
                         y + (h - 8 * fs) // 2, self.names["black"], 1)
            else:
                note = self.notes[i - 1]
                cv.rect(x, y, w, h, PAPER)
                cv.rect(x, y + h - fs, w, fs, self.names["light_grey"])
                cv.print(note["title"][:max(1, w // (8 * fs) - 2)],
                         x + 6 * fs, y + (h - 8 * fs) // 2, INK, 1)
            cv.rectb(x, y, w, h, th["accent"] if selected else th["dim"])

    def _draw_page(self, cv):
        lay = self.layout
        ed = self.editor
        fs = lay.fs
        ax, ay, aw, ah = lay.text_area
        cv.rect(ax, ay, aw, ah, PAPER)
        cv.rectb(ax, ay, aw, ah, self.names["light_grey"])
        if ed is None:
            return
        # Ruled lines under each text row + a red margin line: it reads as paper.
        for r in range(lay.rows):
            cv.rect(ax + 2 * fs, lay.ty + (r + 1) * lay.lh - 2 * fs,
                    aw - 4 * fs, 1, self.names["light_grey"])
        cv.rect(ax + 4 * fs, ay + fs, fs, ah - 2 * fs, self.names["pink"])
        for r, line in enumerate(ed.visible_lines()):
            seg = line[ed.left:ed.left + lay.cols]
            if seg:
                cv.print(seg, lay.tx, lay.ty + r * lay.lh, INK, 1)
        # A solid caret (no blink -- keeps the idle page free under the redraw gate).
        crow = ed.row - ed.top
        ccol = ed.col - ed.left
        if 0 <= crow < lay.rows and 0 <= ccol <= lay.cols:
            cv.rect(lay.tx + ccol * lay.cell, lay.ty + crow * lay.lh + 8 * fs,
                    lay.cell, max(1, 2 * fs), self.names["blue"])
