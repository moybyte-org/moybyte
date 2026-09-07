"""Files -- the kid file manager system app (#108).

A gallery, not an explorer: kind tiles (DRAWINGS first; other kinds appear as
they gain content) open a thumbnail grid -- newest first, names always visible
under the art, one breadcrumb row planting "things live in places". Item verbs
ride a selection bar: OPEN (in the owning app -- a second tap on the selected
tile does the same), RENAME (optional -- names are auto-given), COPY,
WALL / GAME (the copy-on-use reuse actions, via the ArtworkService), and
DELETE -- which moves to the restorable trash, never destroys (trash trains
recovery; confirms train click-through).

Presented exactly like Paint: a `.moy` cartridge identity on the
launcher (`files.moy`) backed by a responsive SYSTEM process. The grid itself
is the shared `file_widgets.FileGridView` -- the same widget Paint's OPEN mode
embeds, so browsing your stuff is one learned gesture everywhere. Selection
state lives ON the grid (`grid.sel_name()`), never mirrored here.
"""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui

try:
    from file_widgets import FileGridView
except ImportError:  # pragma: no cover - direct host import
    from runtime.file_widgets import FileGridView

try:
    from app_shell import ListShellLayout, ListShellApp
except ImportError:  # pragma: no cover - direct host import
    from runtime.app_shell import ListShellLayout, ListShellApp

try:
    import text_modes as _modes
except ImportError:  # pragma: no cover - direct host import
    from runtime import text_modes as _modes


# The carts root, as a tile beside the user-files kinds. NOT a
# `moy_carts.FILE_KINDS` name and never passed to the files store: its items
# are `.moy` FOLDERS, each shown as ONE item and opened as a project, because
# a project is a thing you edit and not a directory you browse.
PROJECTS = "projects"

# Root tiles, in shelf order. The three a person actually touches come first
# (docs/text_editing_2026-09.md): PROJECTS, NOTES (the flat Markdown vault) and
# DRAWINGS, which are always shown; the remaining kinds appear once they have
# content, so the top level stays a few big friendly tiles and not an empty
# directory tree.
KIND_LABELS = (
    (PROJECTS, "PROJECTS"),
    ("docs", "NOTES"),
    ("drawings", "DRAWINGS"),
    ("sprites", "SPRITES"),
    ("music", "MUSIC"),
    ("recordings", "RECORDINGS"),
)

# The roots that are always on the shelf, empty or not.
ALWAYS_SHOWN = (PROJECTS, "docs", "drawings")


def _folder_of(cart):
    """A cart's `.moy` FOLDER name -- what the PROJECTS root shows as one
    item. The folder and not the title: the title is what a kid renamed the
    game to, the folder is the thing on the card."""
    path = (cart or {}).get("path") or ""
    cut = max(path.rfind("/"), path.rfind("\\"))
    return path[cut + 1:] if cut >= 0 else (path or (cart or {}).get("title") or "")


class FilesLayout(ListShellLayout):
    MIN_W = 310
    MIN_H = 230

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self._init_frame(w, h, fs, windowed, cs)
        fs = self.fs
        self.top_h = 24 * fs
        self.head = (4 * fs, self.bar_h + 2 * fs, 58 * fs, self.top_h - 4 * fs)
        self.head2 = (self.head[0] + self.head[2] + 4 * fs, self.head[1],
                      58 * fs, self.head[3])
        body_y = self.bar_h + self.top_h + 2 * fs
        self.action_h = 20 * fs
        self.status_h = 14 * fs
        self.action_y = self.h - self.status_h - self.action_h - 2 * fs
        self.body = (4 * fs, body_y, self.w - 8 * fs,
                     max(40, self.action_y - body_y - 2 * fs))
        # Kind tiles: two columns of big buttons in the body.
        cols = 2
        tw = (self.body[2] - 6 * fs) // cols
        th = 34 * fs
        self.tiles = []
        for i in range(len(KIND_LABELS) + 1):          # +1 = the TRASH tile
            c, r = i % cols, i // cols
            self.tiles.append((self.body[0] + c * (tw + 6 * fs),
                               self.body[1] + 4 * fs + r * (th + 6 * fs), tw, th))
        self._init_list(body_y + 4 * fs)               # trash/project rows

    def action_rects(self, labels):
        fs = self.fs
        out = []
        x = 4 * fs
        for label in labels:
            w = (len(label) * 8 + 12) * fs
            out.append((x, self.action_y, w, self.action_h))
            x += w + 3 * fs
        return out


class FilesAppLayer(ListShellApp):
    id = "files"
    domain = "system"
    TITLE = "FILES"
    APP_TITLE = "Files"
    APP_PERM = "files"
    APP_FOLDER = "files.moy"
    # The shell roles this app uses (runtime/app_context.py). `nav` carries the
    # app-to-app jumps the router takes (a drawing opens in Paint, a note on
    # the text page, a project in the Editor) --
    # app_api_v1 called that a v1 non-goal and it shipped anyway, because it is
    # a real product need; `shell` is only the FileGridView duck-type.
    NEEDS = ("surface", "theme", "damage", "files", "nav", "artwork", "shell")

    GRID_ACTIONS = ("OPEN", "NAME", "COPY", "WALL", "GAME", "USE", "DEL")
    DOC_ACTIONS = ("OPEN", "NAME", "COPY", "DEL")   # the kinds the router opens
    # A vault SCRIPT is a PROGRAM, so RUN leads (docs/text_editing_2026-09.md
    # step 6). OPEN stays beside it: a script is still text, and editing it is
    # the other thing you do with one.
    SCRIPT_ACTIONS = ("RUN", "OPEN", "NAME", "COPY", "DEL")
    PLAIN_ACTIONS = ("NAME", "COPY", "DEL")     # kinds with no opener/reuse yet

    def __init__(self, ctx, names, in_rect):
        self.ctx = ctx
        # Roles bound ONCE (the hoist mandate, ui_refactor_2026-08 Section 2.4).
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._store = ctx.files       # ListShellApp's storage role
        self._nav = ctx.nav
        self._art = ctx.artwork
        self._shell = ctx.shell       # FileGridView's duck-type only
        self.names = names
        self._in = in_rect
        cv = ctx.surface.canvas()
        self.layout = FilesLayout(cv.w, cv.h, self._surf.font_scale(),
                                  self._surf.windowed(),
                                  self._surf.chrome_scale())
        self.mode = "kinds"           # kinds | grid | projects | trash | rename | game
        self.grid = FileGridView(ctx.shell, "drawings")
        self.counts = {}
        self.trash = ()
        self.status = "MY FILES"
        self.sel = 0                  # row-list selection (ListShellApp nav)
        self.top = 0
        self._rows = ()               # the active row-list mode's labels
        self._rows_empty = ""
        self._save_failed = False
        self.rename_text = ""
        self._ekey_prev = 0
        self.project_names = ()
        self.project_carts = ()       # the PROJECTS root's .moy folders
        self._rows_scroll = None      # the row list's touch model (ListShellApp)
        self._rows_taps = None
        self.used_rows = ()           # provenance "used in:" rows (#108 phase 2)
        self.used_name = None

    def relayout(self, w, h, fs, cs=None):
        self.layout = FilesLayout(w, h, fs, self._surf.windowed(), cs)

    # -- store ----------------------------------------------------------------

    def _refresh_counts(self):
        """Every kind's count + the trash listing in ONE storage session -- the
        SD mount is the expensive part, which is what ctx.files.batch is for."""
        self.counts = {}
        self.trash = ()

        def _list(f):
            counts = {}
            for kind, _label in KIND_LABELS:
                if kind != PROJECTS:          # not a files kind -- see above
                    counts[kind] = f.count(kind)
            return counts, tuple(f.trash_list())

        got, err = self._store.batch(_list)
        if err is None and got is not None:   # an unreadable store lists nothing
            self.counts, self.trash = got
        self.counts[PROJECTS] = len(self._nav.projects())

    def open(self):
        self.mode = "kinds"
        self._refresh_counts()
        self.status = "MY FILES"
        self._damage.all()

    # -- verbs ----------------------------------------------------------------

    def _enter_kind(self, kind):
        if self.grid.kind != kind:
            self.grid = FileGridView(self._shell, kind)
        self.grid.refresh()
        self.grid.select(None)
        self.mode = "grid"
        self.status = kind.upper()
        self._damage.all()

    def _enter_rows(self, mode):
        """Open one of the two row-list modes; their labels are built ONCE
        here (never per frame) and consumed by nav/tap/draw alike."""
        self.mode = mode
        self.sel = 0
        self.top = 0
        if mode == "trash":
            self._rows = tuple(n + "  (" + k + ")" for k, n in self.trash)
            self._rows_empty = "TRASH IS EMPTY"
        elif mode == PROJECTS:
            # One row per `.moy` FOLDER -- a project opens, it never expands.
            self.project_carts = tuple(self._nav.projects())
            self._rows = tuple(_folder_of(c) for c in self.project_carts)
            self._rows_empty = "NO PROJECTS"
        elif mode == "used":
            # A drawing's consumers (#108 phase 2): a "*" marks a stale copy
            # (the drawing changed since) -- tapping it re-sends (UPDATE).
            self._rows = tuple(
                (r["label"] + (" *" if r.get("stale") else "")) for r in self.used_rows)
            self._rows_empty = "NOT USED YET"
        else:
            self._rows = tuple(self.project_names)
            self._rows_empty = "NO PROJECTS"
        if self._rows_scroll is not None:
            self._rows_scroll.invalidate()   # a different list under the pixels
        self._damage.all()

    def _action_labels(self):
        kind = self.grid.kind
        if kind == "drawings":
            return self.GRID_ACTIONS
        if kind == "docs":
            if _modes.is_script(self.grid.sel_name() or ""):
                return self.SCRIPT_ACTIONS
            return self.DOC_ACTIONS
        return self.PLAIN_ACTIONS

    # -- the router -----------------------------------------------------------

    def route(self, name, kind=None, cart=None):
        """Open `name` by WHAT IT IS, in the order
        docs/text_editing_2026-09.md fixes:

            a `.moy` folder      the project Editor, never a listing
            a cart's main file   that cart's Editor, on the Code tab
            an image             Paint
            any other text file  the shell's text page, in its mode

        `kind` is the user-files kind an item came from (its extension is the
        kind's, asked of the mode table); `cart` is the project a project file
        came from. Returns the door taken -- "editor" / "code" / "paint" /
        a mode name -- or None when nothing here can open it, which is what
        the status line reports.

        Every door goes through ctx.nav by REGISTERED ID, so Files holds no
        reference to another app's class and a build without one degrades to
        the status line instead of an AttributeError."""
        door, subject = self.door(name, kind, cart)
        if door == "editor":
            return self._to_editor(subject)
        if door == "code" and cart is not None:
            # The cart-main door, NOT the `code` MODE a standalone `.py` edits
            # in -- `text_modes.CODE` is the same string, and only a project
            # file reaches the Editor.
            return self._to_editor(subject, tab="code")
        if door == "paint":
            return self._to_paint(subject, kind, cart)
        return self._to_text(name, kind, door, cart)

    def door(self, name, kind=None, cart=None):
        """WHICH door `name` takes, as `(door, subject)` -- the router's rules
        as a pure function, so the order above is one readable ladder and a
        test can ask what a file IS without opening anything. `door` is
        "editor" / "code" / "paint" or a `text_modes` mode name; `subject` is
        what that door opens."""
        if kind == PROJECTS:
            return "editor", (cart or self._project(name))
        fname = name if cart is not None else _modes.file_name(kind, name)
        if cart is not None and fname == cart.get("main", "main.py"):
            return "code", cart
        if _modes.is_image(fname):
            # A cart's own image (`images/cover.moyimg`) is a PICTURE like any
            # other, so it takes the picture door -- Paint opens it on the
            # project kind and writes it back into the cart's folder.
            return "paint", name
        return _modes.mode_for(fname), name

    def _project(self, folder):
        """The cart whose `.moy` folder is `folder` (the PROJECTS root's rows
        carry folder names, so a re-scan between listing and tap resolves
        against the live roster rather than a stale dict)."""
        for cart in self._nav.projects():
            if _folder_of(cart) == folder:
                return cart
        return None

    def _to_editor(self, cart, tab=None):
        if cart is None or not self._nav.edit(cart, tab):
            self.status = "CAN'T OPEN"
            return None
        return "code" if tab else "editor"

    def _to_paint(self, name, kind=None, cart=None):
        """Open a picture in Paint -- a gallery drawing, or a cart's OWN image
        (`cart` set), which Paint reads and writes on that project's kind: the
        same file, in place, so an edited cover is the cover."""
        if not self._nav.open_image(name, None if cart is not None else kind,
                                    cart):
            self.status = "NO PAINT APP"
            return None
        return "paint"

    def _to_text(self, name, kind, mode, cart=None):
        if kind is None:
            # A project's own text file. It opens through that project's
            # EDITOR (`nav.edit_file`), never straight into the handle: the
            # Config tab's ADVANCED row is the same door, and going through it
            # is what keeps the loader in the loop -- the return re-reads the
            # folder, so a manifest JSON mode was allowed to write invalid is
            # re-validated where the kid can see it.
            if not self._nav.edit_file(cart, name, mode):
                self.status = "CAN'T OPEN"
                return None
            return mode
        if not self._nav.open_text(name, kind, mode):
            self.status = "NO TEXT APP"
            return None
        return mode

    def _pick(self, name):
        """The grid's open gesture (second tap / A on the selection)."""
        self.route(name, kind=self.grid.kind)

    def _act(self, verb, name):
        art = self._art
        if verb == "RUN":
            ok, why = self._nav.run_script(self.grid.kind, name)
            if not ok:
                self.status = why
                self._damage.all()
            return
        if verb == "OPEN":
            self._pick(name)
            return
        if verb == "NAME":
            self.rename_text = name[:self.RENAME_MAX]
            self._ekey_prev = 0
            self.mode = "rename"
            self.status = "TYPE A NAME"
            return
        if verb == "COPY":
            if self._persist(self._store.duplicate(self.grid.kind, name)):
                self.status = "COPIED"
                self.grid.refresh()
        elif verb == "WALL":
            if art.set_wallpaper(name):
                self.status = "WALLPAPER SET"
            else:
                self.status = art.last_error or "CAN'T SET"
        elif verb == "GAME":
            self.project_names = art.targets()
            self._enter_rows("game")
            self.status = "PICK A PROJECT"
        elif verb == "USE":
            # Provenance "used in:" list (#108 phase 2): the wallpaper + every
            # project bg copied from this drawing, stale copies flagged.
            self.used_name = name
            self.used_rows = tuple(art.usage(name))
            self._enter_rows("used")
            self.status = ("USED IN " + str(len(self.used_rows))) \
                if self.used_rows else "NOT USED YET"
        elif verb == "DEL":
            if self._persist(self._store.delete(self.grid.kind, name)):
                self.status = "IN TRASH"
                self.grid.invalidate(name)
                self.grid.refresh()
                self.grid.select(None)
        self._damage.all()

    def _rename_commit(self):
        name = self.grid.sel_name()
        if name:
            res = self._store.rename(self.grid.kind, name, self.rename_text)
            if self._persist(res):
                new = res[0] or name
                art = self._art
                # A renamed open drawing keeps Paint pointed at it.
                if self.grid.kind == "drawings" and art.doc_name() == name:
                    art.open_named(new)
                self.grid.invalidate(name)
                self.grid.refresh()
                self.grid.select(new)
                self.status = new.upper()
        self.mode = "grid"
        self._damage.all()

    def _restore(self, index):
        try:
            kind, name = self.trash[index]
        except IndexError:
            return
        if self._persist(self._store.restore(kind, name)):
            self.status = "RESTORED " + name.upper()[:14]
            self._refresh_counts()
            self._enter_rows("trash")

    def _tap_row(self, i):
        if self.mode == PROJECTS:
            if 0 <= i < len(self.project_carts):
                # `kind` positionally: tests/test_skin.py reads a constant
                # reaching a `kind=` keyword as a widget-catalog kind.
                self.route(self._rows[i], PROJECTS, self.project_carts[i])
        elif self.mode == "trash":
            self._restore(i)
        elif self.mode == "game":
            self._game_pick(i)
        elif self.mode == "used":
            self._resend(i)

    def _resend(self, i):
        """Re-copy the drawing to one usage row -- the one-tap UPDATE / send-
        again (#108 phase 2). Stays in the list, re-scanned so the '*' clears."""
        art = self._art
        if 0 <= i < len(self.used_rows) and self.used_name:
            row = self.used_rows[i]
            if art.resend(row, self.used_name):
                self.status = "SENT TO " + row["label"].upper()[:14]
            else:
                self.status = art.last_error or "CAN'T SEND"
            self.used_rows = tuple(art.usage(self.used_name))
            self._enter_rows("used")
        self._damage.all()

    def _game_pick(self, i):
        art = self._art
        name = self.grid.sel_name()
        if 0 <= i < len(self.project_names) and name:
            title = art.attach(i, name)
            self.status = ("BG ADDED TO " + title) if title else \
                (art.last_error or "CAN'T ADD")
        self.mode = "grid"
        self._damage.all()

    # -- input ----------------------------------------------------------------

    def handle_input(self, inp):
        if self.mode == "rename":
            self._typed_rename(inp)
            return True
        if self.mode in ("trash", "game", "used", PROJECTS):
            # B leads, because a row list that swallows it is a room with no
            # door on a keyboard board -- the `<` chip was the only way out.
            if inp.pressed("b"):
                self._back()
            elif self._rows:
                return self._list_nav(inp, len(self._rows))
            return True
        if self.mode == "grid":
            hit = self.grid.nav(inp)
            if hit:
                if hit[0] == "pick":
                    self._pick(hit[1])
                else:
                    self.status = hit[1].upper()
                self._damage.all()
                return True
        if inp.pressed("b"):
            self._back()
        return True


    def _back(self):
        if self.mode == "kinds":
            return
        if self.mode in ("rename", "game", "used"):
            self.mode = "grid"
        elif self.mode == "grid" and self.grid.sel_name():
            self.grid.select(None)
        else:
            self.mode = "kinds"
            self._refresh_counts()
        self._damage.all()

    def handle_pointer(self, px, py, click):
        lay = self.layout
        # The grid's hover/pressed pump runs on EVERY sample, not just clicks --
        # a press cue that only appeared on the click frame would never be seen.
        if self.mode in ("grid", "rename") and self.grid.pointer_frame(
                px, py, self._surf.pointer()):
            self._damage.all()
        # ...and so does the row list's drag: a scroll is made of samples that
        # are not clicks, and the tap it may end in fires on the RELEASE.
        if self.mode in ("trash", "game", "used", PROJECTS) \
                and not self._in(px, py, lay.head) and not self._in(px, py, lay.head2):
            row = self._rows_pointer(px, py, click, len(self._rows))
            if row is not None:
                self._tap_row(row)
                self._damage.all()
            return True
        if not click:
            return True
        if self._in(px, py, lay.head):
            self._back()
            return True
        if self.mode == "kinds":
            self._kinds_tap(px, py)
        elif self.mode == "grid":
            self._grid_tap(px, py)
        elif self.mode == "trash":
            if self._in(px, py, lay.head2) \
                    and self._persist(self._store.empty_trash()):
                self.status = "TRASH EMPTY"
                self._refresh_counts()
                self._enter_rows("trash")
        elif self.mode == "rename":
            if self._in(px, py, lay.head2):
                self._rename_commit()
        self._damage.all()
        return True

    def _kinds_tap(self, px, py):
        shown = self._shown_kinds()
        for i, (kind, _label) in enumerate(shown):
            if self._in(px, py, self.layout.tiles[i]):
                if kind == PROJECTS:
                    self._enter_rows(PROJECTS)
                    self.status = "PROJECTS"
                else:
                    self._enter_kind(kind)
                return
        if self._in(px, py, self.layout.tiles[len(shown)]):
            self._enter_rows("trash")
            self.status = "TRASH"

    def _grid_tap(self, px, py):
        if self.grid.sel_name():
            labels = self._action_labels()
            for i, r in enumerate(self.layout.action_rects(labels)):
                if self._in(px, py, r):
                    self._act(labels[i], self.grid.sel_name())
                    return
        hit = self.grid.tap(px, py)
        if hit is None:
            self.grid.select(None)
        elif hit[0] == "pick":
            self._pick(hit[1])
        elif hit[0] == "sel":
            self.status = hit[1].upper()

    # -- draw -----------------------------------------------------------------

    def _shown_kinds(self):
        out = []
        for kind, label in KIND_LABELS:
            if kind in ALWAYS_SHOWN or self.counts.get(kind):
                out.append((kind, label))
        return out

    def _chip(self, cv, label, r, on=False, hot=False):
        _ui.chip(cv, self._theme.colors(), r, label, on=on, hot=hot,
                 fs=self.layout.fs)

    def draw(self, dt):
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        fs = lay.fs
        cv.cls(th["panel"])
        _ui.toolbar(cv, th, (0, lay.bar_h, lay.w, lay.top_h))
        crumb = "FILES"
        if self.mode in ("grid", "rename", "game", "used"):
            crumb = "FILES > " + self.grid.kind.upper()
            if self.mode == "used":
                crumb = "FILES > USED IN"
        elif self.mode == "trash":
            crumb = "FILES > TRASH"
        elif self.mode == PROJECTS:
            crumb = "FILES > PROJECTS"
        self._chip(cv, "<" if self.mode != "kinds" else "FILES", lay.head,
                   self.mode != "kinds")
        cv.print(crumb, lay.head[0] + lay.head[2] + 8 * fs,
                 lay.bar_h + 8 * fs, th["title_ink"], 1)
        if self.mode == "trash":
            self._chip(cv, "EMPTY", lay.head2, hot=bool(self.trash))
        if self.mode == "rename":
            self._chip(cv, "OK", lay.head2, True)

        if self.mode == "kinds":
            self._draw_kinds(cv)
        elif self.mode in ("grid", "rename"):
            self.grid.set_rect(lay.body, fs)
            self.grid.draw(cv, th)
            if self.mode == "rename":
                self._draw_rename(cv)
            else:
                self._draw_actions(cv)
        else:                          # the row-list modes: projects | trash | game
            self._draw_rows(cv)

        cv.rect(0, lay.h - lay.status_h, lay.w, lay.status_h, self.names["black"])
        cv.print(self.status[:max(1, lay.w // (8 * fs) - 1)], 4 * fs,
                 lay.h - lay.status_h + 3 * fs, self.names["yellow"], 1)

    def _draw_kinds(self, cv):
        shown = self._shown_kinds()
        for i, (kind, label) in enumerate(shown):
            self._chip(cv, label + " " + str(self.counts.get(kind, 0)),
                       self.layout.tiles[i])
        self._chip(cv, "TRASH " + str(len(self.trash)),
                   self.layout.tiles[len(shown)], hot=bool(self.trash))

    def _draw_actions(self, cv):
        if not self.grid.sel_name():
            return
        labels = self._action_labels()
        for i, r in enumerate(self.layout.action_rects(labels)):
            self._chip(cv, labels[i], r, hot=labels[i] == "DEL")

    def _draw_rename(self, cv):
        lay = self.layout
        th = self._theme.colors()
        fs = lay.fs
        r = (lay.body[0], lay.action_y, lay.body[2], lay.action_h)
        cv.rect(r[0], r[1], r[2], r[3], self.names["white"])
        cv.rectb(r[0], r[1], r[2], r[3], th.get("accent", 10))
        cv.print(self.rename_text + "_", r[0] + 4 * fs, r[1] + 6 * fs,
                 self.names["black"], 1)

    def _draw_rows(self, cv):
        lay = self.layout
        th = self._theme.colors()
        fs = lay.fs
        if not self._rows:
            cv.print(self._rows_empty, lay.body[0] + 8 * fs,
                     lay.body[1] + 12 * fs, th.get("dim", 1), 1)
            return
        for row in range(lay.list_rows):
            i = self.top + row
            if i >= len(self._rows):
                break
            _ui.row(cv, th, lay.row_rect(row), self._rows[i], on=(i == self.sel),
                    pad=4 * fs, text_dy=6 * fs, fs=fs)
        self._rows_bar(cv, th, len(self._rows))
