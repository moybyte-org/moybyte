"""Sheets -- the kid spreadsheet system app (#78, the Desk Lab trio's third tile).

Presented like Writer/Paint: a `.moy` cartridge identity on the launcher
(`sheets.moy`, so it seeds/versions/exports like every cart) backed by a
responsive SYSTEM process the console spawns instead of the Player -- a grid app
must reflow to a P4/web window, while Player is the fixed 320x240 contract.

A sheet is a tiny grid of cells. A cell holds a literal (`3`, `hello`) or a
formula that starts with `=` (`=A1+B1*2`, `=sum(A1:A5)`). Formulas are compiled by
the hand-rolled engine in `runtime/formula.py` (the portable subset bans
eval/exec) and reuse the block-operator vocabulary (#48: mod/round/abs/min/max) so
Sheets and Blocks teach the same words. A reference cycle shows `#LOOP`, a bad
formula `#ERR` -- never a crash.

Each sheet is a USER FILE (#108): a named `files/tables/*.moysheet` item in the
store, auto-named (`table_1`, ...), browsed through the SHARED `FileGridView`
picker (the exact widget Paint's OPEN mode + the Files app use) and AUTOSAVED on
every cell commit + view change. The legacy single-file `sheets.json` workbook
is migrated once (`moy_carts.migrate_tables`) into one file per sheet. A sheet's
`moysheet-v1` blob is exactly what `table(name)` reads (#78), so copying it into
a cart works unchanged."""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui

import json

try:
    from formula import Sheet, index_to_col, ERR, LOOP
except ImportError:  # pragma: no cover - direct host import
    from runtime.formula import Sheet, index_to_col, ERR, LOOP

try:
    from file_widgets import FileGridView
except ImportError:  # pragma: no cover - direct host import
    from runtime.file_widgets import FileGridView

try:
    from app_shell import ListShellLayout, ListShellApp
except ImportError:  # pragma: no cover - direct host import
    from runtime.app_shell import ListShellLayout, ListShellApp


DEFAULT_COLS = 8
DEFAULT_ROWS = 20
CELL_MAX = 48            # chars of raw formula/text per cell
MAX_NAME = 24            # rename entry cap
_ERRORS = (ERR, LOOP)


def _fmt(v, width):
    """A computed value as a short display string that fits `width` chars."""
    if isinstance(v, float):
        if v == int(v):
            v = int(v)
        else:
            v = round(v, 3)
    s = str(v)
    if len(s) > width:
        s = s[:width]
    return s


class SheetsLayout(ListShellLayout):
    """Responsive geometry: a toolbar band, a formula-entry row, then the grid
    (column headers + row headers + cells). Reflows to any window size (#39)."""

    MIN_W = 200
    MIN_H = 200

    def __init__(self, w, h, fs=1, windowed=False, cols=DEFAULT_COLS):
        self._init_frame(w, h, fs, windowed)
        fs = self.fs
        self.cols = int(cols)
        self.toolbar_h = 24 * fs
        x = 6 * fs
        y = self.bar_h + 3 * fs
        bh = self.toolbar_h - 6 * fs
        # Grid-view toolbar: SHEETS (back), CLEAR (cell), RENAME, TRASH.
        self.back_btn = (x, y, 50 * fs, bh)
        self.clr_btn = (x + 54 * fs, y, 46 * fs, bh)
        self.name_btn = (x + 104 * fs, y, 52 * fs, bh)
        self.del_btn = (x + 160 * fs, y, 46 * fs, bh)
        # List-view: + NEW.
        self.new_btn = (x, y, 62 * fs, bh)
        self.status_x = x + 210 * fs
        self.status_y = y + max(0, (bh - 8 * fs) // 2)
        # Formula entry row: the raw text of the selected cell, edited in place.
        self.entry_h = 14 * fs
        self.entry = (x - 2 * fs, self.bar_h + self.toolbar_h,
                      self.w - 2 * (x - 2 * fs), self.entry_h)
        # The grid.
        self.rowhdr_w = 20 * fs
        self.colhdr_h = 10 * fs
        self.gx = 2 * fs
        self.gy = self.bar_h + self.toolbar_h + self.entry_h + 2 * fs + self.colhdr_h
        avail_w = self.w - self.gx - self.rowhdr_w - 2 * fs
        self.cell_w = max(24 * fs, avail_w // max(1, self.cols))
        self.cell_h = 12 * fs
        self.vis_cols = max(1, min(self.cols, avail_w // self.cell_w))
        self.vis_rows = max(1, (self.h - self.gy - 2 * fs) // self.cell_h)
        # The picker (list view): the shared thumbnail grid fills the body.
        gy_body = self.bar_h + self.toolbar_h + 2 * fs
        self.body = (4 * fs, gy_body, self.w - 8 * fs,
                     max(40, self.h - gy_body - 4 * fs))
        self._init_list(self.bar_h + self.toolbar_h)

    def cell_rect(self, ci, ri, left, top):
        """Screen rect for grid column `ci`, row `ri` given the scroll origin."""
        x = self.gx + self.rowhdr_w + (ci - left) * self.cell_w
        y = self.gy + (ri - top) * self.cell_h
        return (x, y, self.cell_w - 1, self.cell_h - 1)


class SheetsAppLayer(ListShellApp):
    """A sheet picker (shared FileGridView) -> one sheet's grid, over the
    formula.Sheet model on named files/tables/*.moysheet user files (#108)."""

    id = "sheets"
    domain = "system"
    # The shipped identity (ListShellApp.is_app gates on these).
    APP_TITLE = "Sheets"
    APP_PERM = "sheets"
    APP_FOLDER = "sheets.moy"
    TITLE = "SHEETS"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self.names = names
        self._in = in_rect
        self.layout = SheetsLayout(ws.sys_canvas.w, ws.sys_canvas.h,
                                   ws._effective_font_scale(), ws.windowed_chrome)
        self.mode = "list"            # list | grid | rename
        self.grid = FileGridView(ws, "tables")
        self.sheet = None             # the open formula.Sheet
        self.sheet_name = None        # its files/tables/<name> file name
        self.cur_col = 0              # grid selection
        self.cur_row = 0
        self.left = 0                 # grid scroll origin
        self.gtop = 0
        self.editing = False          # the formula entry is open for typing
        self.edit_buf = ""            # the raw text being typed
        self.status = "MY SHEETS"
        self._ekey_prev = 0
        self._unsaved = False         # a change since the last flush
        self.rename_text = ""
        self._pending_open = None     # a name to open on the next open() (Files jump)
        self._save_failed = False

    # -- store ---------------------------------------------------------------
    # (is_app / _store_ready / _load_blob / _persist / _edge_key: ListShellApp)

    def open_named(self, name):
        """Point Sheets at a named sheet to open on its next open() -- the Files
        app's OPEN verb (tables open in Sheets)."""
        self._pending_open = name

    def flush(self, force=False):
        """Persist the open sheet to its files/tables/<name>.moysheet file. The
        autosave verb: commits any open edit, then writes when something changed
        (or force). Cheap to call, no-ops on an unchanged sheet."""
        self._commit_edit()
        if self.sheet is None or self.sheet_name is None:
            return True
        if not (self._unsaved or force):
            return True
        if not self._store_ready():
            self._save_failed = True
            self.status = "CAN'T SAVE HERE"
            return False
        name = self.sheet_name
        blob = json.dumps(self.sheet.to_dict())
        ok = self._persist(lambda: self.ws.carts_store.save_file(
            "tables", name, blob, self.ws.carts_root))
        if ok:
            self._unsaved = False
            self.grid.invalidate(name)
        return ok

    # -- lifecycle -----------------------------------------------------------

    def relayout(self, w, h, fs):
        cols = self.sheet.cols if self.sheet is not None else DEFAULT_COLS
        self.layout = SheetsLayout(w, h, fs, self.ws.windowed_chrome, cols)
        self._scroll_grid()

    def open(self):
        if self._store_ready():
            try:
                self.ws._with_sd(
                    lambda: self.ws.carts_store.migrate_tables(self.ws.carts_root))
            except Exception:  # noqa: BLE001 -- migration is best-effort
                pass
        self.grid.refresh()
        pending = self._pending_open
        self._pending_open = None
        if pending and pending in self.grid.names:
            self._open_file(pending)
        else:
            self.mode = "list"
            self.sheet = None
            self.sheet_name = None
            self.status = "MY SHEETS"
        self.editing = False
        self.edit_buf = ""
        self._ekey_prev = 0
        self.ws._dirty = True

    # -- sheet verbs ---------------------------------------------------------

    def _enter_grid(self, sheet, name):
        self.flush()
        self.sheet = sheet
        self.sheet_name = name
        self.mode = "grid"
        self.cur_col = 0
        self.cur_row = 0
        self.left = 0
        self.gtop = 0
        self.editing = False
        self.edit_buf = ""
        self._ekey_prev = 0
        self.layout = SheetsLayout(self.layout.w, self.layout.h, self.layout.fs,
                                   self.ws.windowed_chrome, sheet.cols)
        self.status = name.upper()
        self.ws._dirty = True

    def _open_file(self, name):
        blob = None
        if self._store_ready():
            try:
                blob = self.ws._with_sd(lambda: self.ws.carts_store.load_file(
                    "tables", name, self.ws.carts_root))
            except Exception:  # noqa: BLE001
                blob = None
        data = None
        if blob:
            try:
                data = json.loads(blob)
            except (ValueError, TypeError):
                data = None
        sheet = Sheet.from_dict(data) if isinstance(data, dict) else Sheet()
        self._enter_grid(sheet, name)

    def _new_sheet(self):
        self.flush()
        name = None
        if self._store_ready():
            try:
                name = self.ws._with_sd(lambda: self.ws.carts_store.new_file_name(
                    "tables", self.ws.carts_root))
            except Exception:  # noqa: BLE001
                name = None
        name = name or "table_1"
        sheet = Sheet(name, DEFAULT_ROWS, DEFAULT_COLS)
        self._enter_grid(sheet, name)
        self._unsaved = True            # a new sheet writes its first file eagerly
        self.flush()

    def _back_to_list(self):
        self.flush(force=True)
        keep = self.sheet_name
        self.mode = "list"
        self.sheet = None
        self.sheet_name = None
        self.editing = False
        self.grid.refresh()
        self.grid.select(keep)
        self.status = "MY SHEETS"
        self.ws._dirty = True

    def _delete_current(self):
        if self.sheet_name is None:
            return
        name = self.sheet_name
        if self._persist(lambda: self.ws.carts_store.delete_file(
                "tables", name, self.ws.carts_root)):
            self.grid.invalidate(name)
            self.status = "IN TRASH"
        self.sheet = None
        self.sheet_name = None
        self._unsaved = False
        self.mode = "list"
        self.grid.refresh()
        self.grid.select(None)
        self.ws._dirty = True

    def _begin_rename(self):
        if self.sheet_name is None:
            return
        self.rename_text = self.sheet_name[:MAX_NAME]
        self._ekey_prev = 0
        self.mode = "rename"
        self.status = "TYPE A NAME"
        self.ws._dirty = True

    def _rename_commit(self):
        name = self.sheet_name
        text = self.rename_text
        new = [name]

        def _do():
            new[0] = self.ws.carts_store.rename_file(
                "tables", name, text, self.ws.carts_root)

        if name and self._persist(_do):
            self.grid.invalidate(name)
            self.sheet_name = new[0]
            if self.sheet is not None:
                self.sheet.name = new[0]
            self.status = new[0].upper()
        self.mode = "grid"
        self.ws._dirty = True

    # -- cell editing --------------------------------------------------------

    def _cur_raw(self):
        if self.sheet is None:
            return ""
        return self.sheet.raw_at(self.cur_col, self.cur_row)

    def _begin_edit(self, seed=None):
        self.editing = True
        self.edit_buf = self._cur_raw() if seed is None else seed
        self.ws._dirty = True

    def _commit_edit(self):
        if not self.editing or self.sheet is None:
            return
        before = self._cur_raw()
        self.sheet.set_cell(self.cur_col, self.cur_row, self.edit_buf)
        if self.edit_buf != before:
            self._unsaved = True
        self.editing = False
        self.edit_buf = ""

    def _move(self, dc, dr):
        if self.editing:
            self._commit_edit()
        if self.sheet is None:
            return
        self.cur_col = max(0, min(self.sheet.cols - 1, self.cur_col + dc))
        self.cur_row = max(0, min(self.sheet.rows - 1, self.cur_row + dr))
        self._scroll_grid()
        self.status = index_to_col(self.cur_col) + str(self.cur_row + 1)
        self.ws._dirty = True

    def _scroll_grid(self):
        lay = self.layout
        if self.cur_col < self.left:
            self.left = self.cur_col
        elif self.cur_col >= self.left + lay.vis_cols:
            self.left = self.cur_col - lay.vis_cols + 1
        if self.cur_row < self.gtop:
            self.gtop = self.cur_row
        elif self.cur_row >= self.gtop + lay.vis_rows:
            self.gtop = self.cur_row - lay.vis_rows + 1

    def _clear_cell(self):
        if self.sheet is None:
            return
        self.editing = False
        self.edit_buf = ""
        if self._cur_raw() != "":
            self._unsaved = True
        self.sheet.set_cell(self.cur_col, self.cur_row, "")
        self.status = index_to_col(self.cur_col) + str(self.cur_row + 1)
        self.ws._dirty = True

    # -- input ---------------------------------------------------------------

    def handle_input(self, inp):
        if self.mode == "list":
            hit = self.grid.nav(inp)
            if hit and hit[0] == "pick":
                self._open_file(hit[1])
            elif hit:
                self.status = hit[1].upper()
            return True
        if self.mode == "rename":
            self._typed_name(inp)
            return True
        # -- grid: trackball arrows move the selection, keys type into a cell ----
        if inp.pressed("left"):
            self._move(-1, 0)
        elif inp.pressed("right"):
            self._move(1, 0)
        elif inp.pressed("up"):
            self._move(0, -1)
        elif inp.pressed("down"):
            self._move(0, 1)
        elif inp.pressed("a"):
            if self.editing:
                self._commit_edit()
            else:
                self._begin_edit()
        self._typed_keys(inp)
        return True

    def _typed_keys(self, inp):
        if self.sheet is None:
            return
        k = inp.last_key
        if k and k != self._ekey_prev:
            if self.editing:
                if k in (0x0D, 0x0A):            # Enter: commit + step down
                    self._commit_edit()
                    self._move(0, 1)
                elif k in (0x08, 0x7F):          # Backspace
                    self.edit_buf = self.edit_buf[:-1]
                    self.ws._dirty = True
                elif 0x20 <= k <= 0x7E and len(self.edit_buf) < CELL_MAX:
                    self.edit_buf += chr(k)
                    self.ws._dirty = True
            else:
                if k in (0x0D, 0x0A):            # Enter: open the cell for editing
                    self._begin_edit()
                elif k in (0x08, 0x7F):          # Backspace: clear the cell
                    self._clear_cell()
                elif 0x20 <= k <= 0x7E:          # a keystroke starts a fresh entry
                    self._begin_edit("")
                    self.edit_buf = chr(k)
        self._ekey_prev = k

    def _typed_name(self, inp):
        k = self._edge_key(inp)
        if not k:
            return
        if k in (0x0D, 0x0A):
            self._rename_commit()
        elif k in (0x08, 0x7F):
            self.rename_text = self.rename_text[:-1]
        elif 0x20 <= k < 0x7F and len(self.rename_text) < MAX_NAME:
            self.rename_text += chr(k)
        self.ws._dirty = True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        lay = self.layout
        if click and not ws.windowed_chrome and py < lay.bar_h:
            self.flush(force=True)              # the X must never lose data
            return bool(ws.bar_layer.handle_bar_tap("tool", px, py))
        if self.mode == "list":
            if not click:
                return True
            if self._in(px, py, lay.new_btn):
                self._new_sheet()
                return True
            hit = self.grid.tap(px, py)
            if hit and hit[0] in ("pick", "sel"):
                self._open_file(hit[1])
            return True
        if self.mode == "rename":
            if click and self._in(px, py, lay.del_btn):
                self._rename_commit()
            return True
        # -- grid view -----------------------------------------------------------
        if not click:
            return True
        if self._in(px, py, lay.back_btn):
            self._back_to_list()
            return True
        if self._in(px, py, lay.clr_btn):
            self._clear_cell()
            return True
        if self._in(px, py, lay.name_btn):
            self._begin_rename()
            return True
        if self._in(px, py, lay.del_btn):
            self._delete_current()
            return True
        # A tap inside the grid selects that cell (and commits any open edit).
        if self.sheet is not None:
            for ri in range(self.gtop, min(self.gtop + lay.vis_rows,
                                           self.sheet.rows)):
                for ci in range(self.left, min(self.left + lay.vis_cols,
                                               self.sheet.cols)):
                    if self._in(px, py, lay.cell_rect(ci, ri, self.left, self.gtop)):
                        if self.editing:
                            self._commit_edit()
                        self.cur_col = ci
                        self.cur_row = ri
                        self.status = index_to_col(ci) + str(ri + 1)
                        self.ws._dirty = True
                        return True
        return True

    # -- draw ----------------------------------------------------------------

    def _button(self, cv, label, r, hot=False):
        _ui.chip(cv, self.ws.theme_colors, r, label, hot=hot, fs=self.layout.fs)

    def draw(self, dt):
        cv = self.ws.sys_canvas
        lay = self.layout
        th = self.ws.theme_colors
        cv.cls(th["panel"])
        _ui.toolbar(cv, th, (0, lay.bar_h, lay.w, lay.toolbar_h))
        if self.mode == "grid":
            self._button(cv, "SHEETS", lay.back_btn)
            self._button(cv, "CLEAR", lay.clr_btn)
            self._button(cv, "RENAME", lay.name_btn)
            self._button(cv, "TRASH", lay.del_btn)
            label = self.status[:max(1, (lay.w - lay.status_x) // (8 * lay.fs) - 1)]
            cv.print(label, lay.status_x, lay.bar_h + 8 * lay.fs, th["title_ink"], 1)
            self._draw_grid(cv)
        elif self.mode == "rename":
            self._button(cv, "OK", lay.del_btn, hot=True)
            self._draw_rename(cv)
        else:
            self._button(cv, "+ NEW", lay.new_btn)
            cv.print(self.status[:max(1, lay.w // (8 * lay.fs) - 2)],
                     lay.new_btn[0] + lay.new_btn[2] + 8 * lay.fs,
                     lay.bar_h + 8 * lay.fs, th["title_ink"], 1)
            self.grid.set_rect(lay.body, lay.fs)
            self.grid.draw(cv, th)
        if not self.ws.windowed_chrome:
            self.ws.bar_layer._draw_status_strip("tool")

    def _draw_rename(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        ex, ey, ew, eh = lay.entry
        cv.rect(ex, ey, ew, eh, self.names["white"])
        cv.rectb(ex, ey, ew, eh, th.get("accent", 10))
        cv.print(self.rename_text + "_", ex + 4 * fs, ey + (eh - 8 * fs) // 2,
                 self.names["black"], 1)

    def _draw_grid(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        sheet = self.sheet
        if sheet is None:
            return
        # Formula entry row: the selected cell's raw text (editable in place).
        ex, ey, ew, eh = lay.entry
        cv.rect(ex, ey, ew, eh, self.names["black"])
        cv.rectb(ex, ey, ew, eh, th["accent"] if self.editing else th["dim"])
        ref = index_to_col(self.cur_col) + str(self.cur_row + 1)
        shown = self.edit_buf if self.editing else self._cur_raw()
        cv.print((ref + ": " + shown)[:max(1, ew // (8 * fs) - 1)],
                 ex + 3 * fs, ey + (eh - 8 * fs) // 2,
                 self.names["green"] if self.editing else self.names["white"], 1)
        # Column headers (A B C ...).
        colw = max(1, lay.cell_w // (8 * fs))
        for ci in range(self.left, min(self.left + lay.vis_cols, sheet.cols)):
            cx = lay.gx + lay.rowhdr_w + (ci - self.left) * lay.cell_w
            hot = ci == self.cur_col
            cv.print(index_to_col(ci)[:colw], cx + 2 * fs,
                     lay.gy - lay.colhdr_h, th["accent"] if hot else th["dim"], 1)
        # Row headers + cells.
        for ri in range(self.gtop, min(self.gtop + lay.vis_rows, sheet.rows)):
            ry = lay.gy + (ri - self.gtop) * lay.cell_h
            hot_r = ri == self.cur_row
            cv.print(str(ri + 1), lay.gx + 2 * fs, ry + 2 * fs,
                     th["accent"] if hot_r else th["dim"], 1)
            for ci in range(self.left, min(self.left + lay.vis_cols, sheet.cols)):
                x, y, w, h = lay.cell_rect(ci, ri, self.left, self.gtop)
                selected = ci == self.cur_col and ri == self.cur_row
                val = sheet.value_at(ci, ri)
                if val in _ERRORS:
                    cv.rect(x, y, w, h, self.names["red"])
                    fg = self.names["white"]
                else:
                    cv.rect(x, y, w, h, 7 if not selected else th["hilite"])
                    fg = 0
                cv.rectb(x, y, w, h, th["accent"] if selected else th["dim"])
                if val != "":
                    cv.print(_fmt(val, max(1, w // (8 * fs) - 1)),
                             x + 2 * fs, y + 2 * fs, fg, 1)
