# Map (grep -n a name to jump there):
#   shown_name                      a cart folder as a row shows it
#   FilesLayout                    the places column, the list and its foot
#   FilesAppLayer.open_place        a place's rows, listed once
#   FilesAppLayer.door              the router's rules, as a pure function
#   FilesAppLayer._act              the verbs on the current row
#   FilesAppLayer.draw              the page: places, files, foot, dialog
#   FilesAppLayer._dialog           rename, empty the Trash, use in a game, used in
"""Files -- the kid file manager system app (#108), drawn with the public UI
API's verbs (docs/ui_api_2026-10.md) and nothing under them.

Places down the side (Projects, Notes, Art, Music, Themes once there are
any, and the Trash), and the place's files in columns: each file's picture
(an icon by what it is), its Name and its Kind (docs/media/look_2026-10/
DESIGN.md, Files). No paths and no extensions. The handheld shows the
places, then a place's files, one page at a time; the desk shows both. A
file's verbs ride the foot of the list: OPEN in the owning app (Enter, or a
second tap on the current row, does the same), RENAME in a dialog (Paint's
open picture follows it, `artwork.follow`), COPY, WALL / GAME / USE for a
drawing (`picture_copies.PictureCopies`), and DELETE, which moves it to the
restorable Trash and never destroys -- the Trash restores, and emptying it
is the one verb that asks first.

Presented exactly like Paint: a `.moy` cartridge identity on the launcher
(`moybyte.files.moy`) backed by a responsive SYSTEM process. Projects is a
place beside the file kinds but never a store kind, because a project is a
thing you edit and not a directory you browse; Themes are the store's theme
packages, whose rows open the Appearance app.
"""

try:
    from layout_base import bar_rows, content_of, metric_class, DESK
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import bar_rows, content_of, metric_class, DESK

try:
    from picture_copies import PictureCopies
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.picture_copies import PictureCopies

try:
    from app_shell import ListShellApp
except ImportError:  # pragma: no cover - direct host import
    from runtime.app_shell import ListShellApp

try:
    import text_modes as _modes
except ImportError:  # pragma: no cover - direct host import
    from runtime import text_modes as _modes

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui


# The carts root, as a place beside the user-files kinds. NOT a
# `moy_carts.FILE_KINDS` name and never passed to the files store: its items
# are `.moy` FOLDERS, each shown as ONE item and opened as a project.
PROJECTS = "projects"
# The store's theme packages: a theme is data the Appearance app picks,
# never a project the Editor opens (docs/theming_2026-09.md §9 phase 6).
THEMES = "themes"
ART = "art"
MUSIC = "music"
TRASH = "trash"

# The places, in the sidebar's order: key, label, icon, and the store kinds
# a place lists. THEMES shows once there are any; the rest always do.
PLACES = (
    (PROJECTS, "Projects", "projects", ()),
    ("docs", "Notes", "notes", ("docs",)),
    (ART, "Art", "paint", ("drawings", "sprites")),
    (MUSIC, "Music", "music", ("music", "recordings")),
    (THEMES, "Themes", "wallpaper", ()),
    (TRASH, "Trash", "trash", ()),
)

# What a store kind's file is called in the Kind column, and its picture.
KIND_OF = {
    "docs": ("Note", "notes"),
    "drawings": ("Drawing", "art"),
    "sprites": ("Sprites", "sprite"),
    "music": ("Song", "music"),
    "recordings": ("Recording", "music"),
}
# A project's, by its cart type.
CART_KIND = {
    "game": ("Game", "game"),
    "app": ("App", "app"),
    "tool": ("Tool", "app"),
    "wallpaper": ("Wallpaper", "wallpaper"),
    "theme": ("Theme", "wallpaper"),
}

# A verb's label at the foot, and its icon.
VERB = {
    "OPEN": ("Open", "enter"),
    "RUN": ("Run", "run"),
    "PLAY": ("Play", "play"),
    "NAME": ("Rename", "pencil"),
    "COPY": ("Copy", "dup"),
    "WALL": ("Wallpaper", "wallpaper"),
    "GAME": ("Use in game", "game"),
    "USE": ("Used in", "search"),
    "DEL": ("Delete", "del"),
    "RESTORE": ("Restore", "back"),
    "EMPTY": ("Empty", "trash"),
}


def _cap(word):
    """`word` with its first letter in capitals: MicroPython's str has no
    `capitalize`."""
    return word[:1].upper() + word[1:]


def _folder_of(cart):
    """A cart's `.moy` FOLDER name. The folder and not the title: the title
    is what a kid renamed the game to, the folder is the thing on the card."""
    path = (cart or {}).get("path") or ""
    cut = max(path.rfind("/"), path.rfind("\\"))
    return path[cut + 1:] if cut >= 0 else (path or (cart or {}).get("title") or "")


def shown_name(folder):
    """A cart folder as a row shows it: no author and no extension."""
    name = folder[:-4] if folder.endswith(".moy") else folder
    dot = name.find(".")
    return name[dot + 1:] if dot >= 0 else name


# The engine's table head and group caption heights (style.json's
# `value_row` `head` and `group` `caption`, handheld then desk; held to it
# by tests/test_files_app.py), which sit over a table's rows.
HEAD = (18, 34)
CAPTION = (19, 26)


class FilesLayout:
    """Files' rects over the content rect: on the desk the places beside the
    list, else one of them at a time; the verbs' band under the list."""

    MIN_W = 310
    MIN_H = 230
    SIDE = 160           # the desk's places column (the targets')

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = fs = max(1, int(fs))
        self.cs = max(fs, int(cs)) if cs else fs
        self.desk = metric_class() == DESK
        self.windowed = bool(windowed)     # its page lies in a window's body
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(self.cs))
        # style.json's `row` stack: the engine's row height in this size class
        self.row_h = (28 if self.desk else 20) * fs
        self.two = self.desk and self.cw >= 400 * fs
        content = (self.cx, self.cy, self.cw, self.ch)
        if self.two:
            self.side, rest = ui.cut_left(content, self.SIDE * fs)
            _rule, self.pane = ui.cut_left(rest, fs)
        else:
            self.side = self.pane = content
        pad = 8 if self.desk else 4 * fs
        self.foot, self.list = ui.cut_bottom(self.pane, self.row_h + 2 * pad)
        self.pad = pad
        # On the desk the list holds whole rows: under its caption (a single
        # pane names the place) and its columns' head, the rows' view is a
        # multiple of the row, and what is left over lies between it and the
        # verbs, so no row is cut by the strip's edge at any window size or
        # in any theme. The handheld keeps every pixel for rows, its last
        # one cut as a phone's list shows there is more.
        top = HEAD[self.desk] * fs + (0 if self.two else CAPTION[self.desk] * fs)
        x, y, w, h = self.list
        if self.desk and h > top + self.row_h:
            self.list = (x, y, w, h - (h - top) % self.row_h)
        # the handheld's places page: the places over the keycap hints
        self.hints, self.places = ui.cut_bottom(content, 20 * fs)


class FilesAppLayer(ListShellApp):
    id = "files"
    domain = "system"
    TITLE = "Files"
    APP_TITLE = "Files"
    APP_PERM = "files"
    APP_FOLDER = "moybyte.files.moy"
    # The shell roles this app uses (runtime/app_context.py). `nav` carries the
    # app-to-app jumps the router takes (a drawing opens in Paint, a note on
    # the text page, a project in the Editor) -- app_api_v1 called that a v1
    # non-goal and it shipped anyway, because it is a real product need. WALL,
    # GAME and USE copy a drawing into the backdrop and into a project
    # (`carts`, `wallpaper`: runtime/picture_copies.py), and `artwork` follows
    # a renamed drawing Paint has open.
    NEEDS = ("surface", "theme", "damage", "files", "nav", "artwork", "carts",
             "wallpaper")
    UI = True

    PICTURE_ACTIONS = ("OPEN", "NAME", "COPY", "WALL", "GAME", "USE", "DEL")
    DOC_ACTIONS = ("OPEN", "NAME", "COPY", "DEL")   # the kinds the router opens
    # A vault SCRIPT is a PROGRAM, so RUN leads (docs/text_editing_2026-09.md
    # step 6). OPEN stays beside it: a script is still text, and editing it is
    # the other thing you do with one.
    SCRIPT_ACTIONS = ("RUN", "OPEN", "NAME", "COPY", "DEL")
    PLAIN_ACTIONS = ("NAME", "COPY", "DEL")     # kinds with no opener/reuse yet
    PROJECT_ACTIONS = ("OPEN", "PLAY")
    THEME_ACTIONS = ("OPEN",)
    TRASH_ACTIONS = ("RESTORE", "EMPTY")

    def __init__(self, ctx, names):
        self.ctx = ctx
        # Roles bound ONCE (the hoist mandate, ui_refactor_2026-08 Section 2.4).
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._store = ctx.files       # ListShellApp's storage role
        self._nav = ctx.nav
        self._art = ctx.artwork
        self._carts = ctx.carts
        self._ui = ctx.ui
        self._copies = PictureCopies(ctx)
        self.names = names
        cv = ctx.surface.canvas()
        self.layout = FilesLayout(cv.w, cv.h, self._surf.font_scale(),
                                  self._surf.windowed(),
                                  self._surf.chrome_scale())
        self.place = PROJECTS
        self.level = "places"         # the handheld's page: places | files
        self.rows = ()                # (name, kind, kind label, icon, cart) per row
        self.cur = -1                 # the current row
        self.counts = {}
        self.trash = ()
        self.status = ""
        self._save_failed = False
        self.dialog = None            # None | rename | empty | game | used
        self.rename_text = ""
        self.project_names = ()       # GAME's targets
        self.target = -1
        self.used_rows = ()           # provenance "used in:" rows (#108 phase 2)
        self.used_name = None
        self.used_cur = -1
        self._focus = None            # the id focus moves to on the next frame
        self._focused = None          # the focused id as the last frame left it
        self._key = 0

    def relayout(self, w, h, fs, cs=None):
        self.layout = FilesLayout(w, h, fs, self._surf.windowed(), cs)

    # -- store ----------------------------------------------------------------

    def _refresh_counts(self):
        """Every kind's count and the trash listing: one store op each."""
        counts = {}
        for _key, _label, _icon, kinds in PLACES:
            for kind in kinds:
                n, err = self._store.count(kind)
                counts[kind] = n if err is None else 0
        trash, err = self._store.trash_list()
        self.trash = tuple(trash) if err is None else ()
        counts[PROJECTS] = len(self._nav.projects())
        counts[THEMES] = len(self._themes())
        counts[TRASH] = len(self.trash)
        self.counts = counts

    def _themes(self):
        """The store's theme packages, by folder."""
        return tuple(_folder_of(c) for c in self._carts.all()
                     if c.get("type") == "theme")

    def _count(self, key):
        for k, _label, _icon, kinds in PLACES:
            if k == key:
                if kinds:
                    return sum([self.counts.get(kind, 0) for kind in kinds])
                return self.counts.get(key, 0)
        return 0

    def shown_places(self):
        """The places the sidebar shows: THEMES once there are any."""
        return tuple(p for p in PLACES if p[0] != THEMES or self.counts.get(THEMES))

    def _list(self, key):
        """The rows of place `key`, newest first within each kind."""
        out = []
        if key == PROJECTS:
            for cart in self._nav.projects():
                label, icon = CART_KIND.get(cart.get("type") or "game",
                                            (_cap(cart.get("type") or "cart"), "game"))
                out.append((shown_name(_folder_of(cart)), PROJECTS, label, icon, cart))
        elif key == THEMES:
            for folder in self._themes():
                out.append((shown_name(folder), THEMES, "Theme", "wallpaper", folder))
        elif key == TRASH:
            for kind, name in self.trash:
                label, icon = KIND_OF.get(kind, (_cap(kind), "doc"))
                out.append((name, kind, label, icon, None))
        else:
            for k, _label, _icon, kinds in PLACES:
                if k != key:
                    continue
                for kind in kinds:
                    names, err = self._store.list(kind)
                    if err is not None:
                        continue
                    for name in names or ():
                        label, icon = KIND_OF.get(kind, (_cap(kind), "doc"))
                        if kind == "docs" and _modes.is_script(name):
                            label = "Script"
                            icon = "cart_lua" if name.endswith(".lua") else "cart_py"
                        out.append((name, kind, label, icon, None))
        return tuple(out)

    def open(self):
        self.dialog = None
        self.level = "places"
        self._refresh_counts()
        self.open_place(self.place)
        self._ui.reset()
        self._focus = "files" if self.rows else "Places"
        self._damage.all()

    # -- the place and its rows ---------------------------------------------------

    def open_place(self, key, keep=None):
        """Show place `key`: its rows listed once (never per frame), the row
        named `keep` current, else the first."""
        self.place = key
        self.rows = self._list(key)
        self.cur = 0 if self.rows else -1
        if keep is not None:
            self.select(keep)
        self._damage.all()

    def _refresh(self, keep=None):
        self._refresh_counts()
        self.open_place(self.place, keep)

    def names_shown(self):
        """The names the place lists, in its order."""
        return [r[0] for r in self.rows]

    def select(self, name):
        """Make the row named `name` current (None: none)."""
        self.cur = -1
        for i, r in enumerate(self.rows):
            if r[0] == name:
                self.cur = i
                break

    def _row(self):
        return self.rows[self.cur] if 0 <= self.cur < len(self.rows) else None

    def sel_name(self):
        r = self._row()
        return r[0] if r is not None else None

    def sel_kind(self):
        r = self._row()
        return r[1] if r is not None else None

    def _action_labels(self):
        """The verbs the current row takes (a place's when none is current)."""
        if self.place == PROJECTS:
            return self.PROJECT_ACTIONS
        if self.place == THEMES:
            return self.THEME_ACTIONS
        if self.place == TRASH:
            return self.TRASH_ACTIONS
        kind = self.sel_kind()
        if kind == "drawings":
            return self.PICTURE_ACTIONS
        if kind == "docs":
            if _modes.is_script(self.sel_name() or ""):
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
        if door == "appearance":
            if not self._nav.open_app("appearance"):
                self.status = "No Appearance app"
                return None
            return door
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
        if kind == THEMES:
            return "appearance", name
        fname = name if cart is not None else _modes.file_name(kind, name)
        if cart is not None and fname == cart.get("main", "main.py"):
            return "code", cart
        if _modes.is_image(fname):
            # A cart's own image (`images/*.moyimg`, its `cover.png`) is a
            # PICTURE like any other, so it takes the picture door -- Paint
            # opens it on the project kind and writes it back into the cart's
            # folder.
            return "paint", name
        return _modes.mode_for(fname), name

    def _project(self, name):
        """The cart a PROJECTS row names (its folder, or the folder as a row
        shows it), against the live roster rather than a stale dict."""
        for cart in self._nav.projects():
            folder = _folder_of(cart)
            if name in (folder, shown_name(folder)):
                return cart
        return None

    def _to_editor(self, cart, tab=None):
        if cart is None or not self._nav.edit(cart, tab):
            self.status = "Can't open"
            return None
        return "code" if tab else "editor"

    def _to_paint(self, name, kind=None, cart=None):
        """Open a picture in Paint -- a gallery drawing, or a cart's OWN image
        (`cart` set), which Paint reads and writes on that project's kind: the
        same file, in place, so an edited cover is the cover."""
        if not self._nav.open_image(name, None if cart is not None else kind,
                                    cart):
            self.status = "No Paint app"
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
                self.status = "Can't open"
                return None
            return mode
        if not self._nav.open_text(name, kind, mode):
            self.status = "No text app"
            return None
        return mode

    def _open_row(self, i):
        """The open gesture on row i: its door, or the Trash's restore."""
        if not 0 <= i < len(self.rows):
            return
        name, kind, _label, _icon, cart = self.rows[i]
        if self.place == TRASH:
            self._restore(i)
        elif kind == PROJECTS:
            self.route(name, PROJECTS, cart)
        elif kind == THEMES:
            self.route(cart, THEMES)
        else:
            self.route(name, kind=kind)

    # -- the verbs --------------------------------------------------------------

    def _act(self, verb, name=None):
        """Verb `verb` on the current row (`name`, where given, made current)."""
        if name is not None:
            self.select(name)
        row = self._row()
        art = self._copies
        if verb == "EMPTY":
            if self.trash:
                self.dialog = "empty"
            self._damage.all()
            return
        if row is None:
            return
        name, kind = row[0], row[1]
        if verb == "RUN":
            ok, why = self._nav.run_script(kind, name)
            if not ok:
                self.status = why
        elif verb == "OPEN":
            self._open_row(self.cur)
        elif verb == "PLAY":
            self._nav.play(row[4])
        elif verb == "RESTORE":
            self._restore(self.cur)
        elif verb == "NAME":
            self.rename_text = name[:self.RENAME_MAX]
            self.dialog = "rename"
        elif verb == "COPY":
            res = self._store.duplicate(kind, name)
            if self._persist(res):
                self.status = "Copied"
                self._refresh(keep=res[0] or name)
        elif verb == "WALL":
            self.status = "Wallpaper set" if art.set_wallpaper(name) else \
                (art.last_error or "Can't set")
        elif verb == "GAME":
            self.project_names = art.targets()
            self.target = 0 if self.project_names else -1
            self.dialog = "game"
        elif verb == "USE":
            # Provenance "used in:" list (#108 phase 2): the wallpaper and
            # every project background copied from this drawing, stale copies
            # flagged.
            self.used_name = name
            self.used_rows = tuple(art.usage(name))
            self.used_cur = 0 if self.used_rows else -1
            self.dialog = "used"
        elif verb == "DEL":
            if self._persist(self._store.delete(kind, name)):
                self.status = "Moved to the Trash"
                cur = self.cur
                self._refresh()
                self.cur = min(cur, len(self.rows) - 1)
        self._damage.all()

    def _rename_commit(self):
        row = self._row()
        self.dialog = None
        if row is not None and self.rename_text.strip():
            name, kind = row[0], row[1]
            res = self._store.rename(kind, name, self.rename_text)
            if self._persist(res):
                new = res[0] or name
                # A renamed open drawing keeps Paint pointed at it.
                self._art.follow(kind, name, new)
                self.status = "Renamed"
                self._refresh(keep=new)
        self._focus = "files"
        self._damage.all()

    def _restore(self, index):
        try:
            name, kind = self.rows[index][0], self.rows[index][1]
        except IndexError:
            return
        if self._persist(self._store.restore(kind, name)):
            self.status = "Restored " + name
            self._refresh()

    def _empty_trash(self):
        self.dialog = None
        if self._persist(self._store.empty_trash()):
            self.status = "The Trash is empty"
        self._refresh()
        self._focus = "files"

    def _resend(self, i):
        """Re-copy the drawing to one usage row -- the one-tap UPDATE / send-
        again (#108 phase 2). Stays in the list, re-scanned so the mark clears."""
        art = self._copies
        if 0 <= i < len(self.used_rows) and self.used_name:
            row = self.used_rows[i]
            if art.resend(row, self.used_name):
                self.status = "Sent to " + row["label"]
            else:
                self.status = art.last_error or "Can't send"
            self.used_rows = tuple(art.usage(self.used_name))
        self._damage.all()

    def _game_pick(self, i):
        art = self._copies
        name = self.sel_name()
        self.dialog = None
        if 0 <= i < len(self.project_names) and name:
            title = art.attach(i, name)
            self.status = ("Added to " + title) if title else \
                (art.last_error or "Can't add")
        self._focus = "files"
        self._damage.all()

    def _close_dialog(self):
        self.dialog = None
        self._focus = "files"
        self._damage.all()

    # -- input ----------------------------------------------------------------

    def handle_input(self, inp):
        k = getattr(inp, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        if k != self._key and k in (8, 127) and self.dialog is None \
                and self._focused == "files" and self._row() is not None \
                and "DEL" in self._action_labels():
            self._act("DEL")
        self._key = k
        if inp.pressed("b"):
            self.back()
        if self._ui.keys(inp):
            self._damage.all()
        return True

    def has_page(self):
        """Whether a page of the app is open, which `back` steps out of: a
        dialog, or a folder's list where one column shows it alone."""
        return self.dialog is not None or (not self.layout.two and self.level == "files")

    def back(self):
        """Back one level (B, Esc, the bar's chevron): a dialog closes, a
        one-column list goes back to the places. False on the places: the
        host leaves the app."""
        if not self.has_page():
            return False
        if self.dialog is not None:
            self._close_dialog()
        else:
            self.level = "places"
            self._focus = "Places"
        self._damage.all()
        return True

    def handle_pointer(self, px, py, click):
        if self._ui.point(px, py, click, bool(getattr(self._surf.pointer(), "down", False))):
            self._damage.all()
        return True

    # -- draw -----------------------------------------------------------------

    def draw(self, dt):
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        self._ui.begin(cv, th, (0, 0, cv.w, cv.h), lay.desk, lay.windowed)
        ui.page("")                   # the ground under the bar's band too
        was = (self.place, self.level, self.dialog, self.cur, len(self.rows))
        if self._focus is not None:
            ui.focus(self._focus)
            self._focus = None
        if lay.two:
            ui.at(*lay.side)
            ui.scroll("side")
            self._places()
            ui.scroll()
            x, y, w, h = lay.side
            cv.rect(x + w, y, lay.fs, h, th["border"])
            self._files()
        elif self.level == "places":
            self._places()
        else:
            self._files()
        self._dialog()
        self._focused = ui.focus()
        if (self.place, self.level, self.dialog, self.cur, len(self.rows)) != was:
            self._damage.all()        # an answer changed what the page shows

    def _places(self):
        """The places: a column on the desk, a page of their own else."""
        lay = self.layout
        shown = self.shown_places()
        keys = [p[0] for p in shown]
        at = keys.index(self.place) if self.place in keys else 0
        if lay.two:
            rows = [(p[2], p[1]) for p in shown]
            cur, opened = ui.table("Places", ("", ""), rows, at)
        else:
            ui.at(*lay.hints)
            ui.hints(("confirm", "Open"))
            ui.at(*lay.places)
            rows = [(p[2], p[1], str(self._count(p[0]))) for p in shown]
            cur, opened = ui.table("", ("", "", ""), rows, at, {"id": "Places"})
        if cur != at and 0 <= cur < len(keys):
            self.open_place(keys[cur])
            if not lay.two and not ui.keys_taken():
                opened = True             # a tap on a place opens it
        if opened and not lay.two:
            self.level = "files"
            self._focus = "files"

    def _files(self):
        lay = self.layout
        label = ""
        if not lay.two:
            for p in PLACES:
                if p[0] == self.place:
                    label = p[1]
        ui.at(*lay.list)
        cols = ("", "Name", "Kind")
        rows = [(r[3], r[0], r[2]) for r in self.rows]
        cur, opened = ui.table(label, cols, rows, self.cur, {"id": "files"})
        self.cur = cur
        if not self.rows:
            x, y, w, h = lay.list
            ui.at(x, y + 2 * lay.row_h, w, lay.row_h)
            ui.label("The Trash is empty" if self.place == TRASH else "Nothing here yet")
        if opened:
            self._open_row(cur)
        self._foot()

    def _foot(self):
        """The current row's verbs as buttons, and the status line after them."""
        lay = self.layout
        fs = lay.fs
        x, y, w, h = ui.inset(lay.foot, lay.pad, lay.pad)
        verbs = self._action_labels()
        none = self._row() is None
        gap = 6 if lay.desk else 3 * fs
        side = 16 * fs
        pad = (12 if lay.desk else 6 * fs)
        widths = [ui.text_size(VERB[v][0])[0] + side + 2 * fs + 2 * pad for v in verbs]
        labelled = sum(widths) + gap * (len(verbs) - 1) <= w
        if not labelled:
            widths = [side + 2 * pad for _v in verbs]
        bx = x
        for i, v in enumerate(verbs):
            label, icon = VERB[v]
            o = {"id": "verb " + v}
            if none and v != "EMPTY" or (v == "EMPTY" and not self.trash):
                o["disabled"] = True
            ui.at(bx, y, widths[i], h)
            ui.icon(icon)
            if ui.button(label if labelled else "", "danger" if v in ("DEL", "EMPTY") else "normal",
                         o):
                self._act(v)
            bx += widths[i] + gap
        if self.status and bx + gap < x + w:
            ui.at(bx + gap, y, x + w - bx - gap, h)
            ui.label(self.status)

    def _dialog(self):
        d = self.dialog
        if d == "rename":
            pick = ui.dialog("Rename", ("Cancel", "Rename"))
            self.rename_text, done = ui.field("Name", self.rename_text, self.RENAME_MAX)
            ui.dialog()
            if pick == 1 or done:
                self._rename_commit()
            elif pick == 0:
                self._close_dialog()
        elif d == "empty":
            pick = ui.dialog("Empty the Trash?", ("Cancel", "Empty"), "danger")
            n = len(self.trash)
            ui.label("%d %s deleted for good." % (n, "item is" if n == 1 else "items are"))
            ui.dialog()
            if pick == 1:
                self._empty_trash()
            elif pick == 0:
                self._close_dialog()
        elif d == "game":
            pick = ui.dialog("Use in a game", ("Cancel", "Add"))
            rows = [("game", n) for n in self.project_names]
            self.target, opened = ui.table("", ("", ""), rows, self.target, {"id": "targets"})
            ui.dialog()
            if pick == 1 or opened:
                self._game_pick(self.target)
            elif pick == 0:
                self._close_dialog()
        elif d == "used":
            pick = ui.dialog("Used in", ("Close", "Send again"))
            if not self.used_rows:
                ui.label("Not used yet.")
            rows = [("game", r["label"], "Changed" if r.get("stale") else "")
                    for r in self.used_rows]
            self.used_cur, opened = ui.table("", ("", "", ""), rows, self.used_cur,
                                             {"id": "uses"})
            ui.dialog()
            if pick == 1 or opened:
                self._resend(self.used_cur)
            elif pick == 0:
                self._close_dialog()
