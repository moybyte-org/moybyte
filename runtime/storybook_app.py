# Map (grep -n a name to jump there):
#   story_list                  the stories a drawing can be put in (Paint's door)
#   add_picture_slide           a drawing as a story's new last slide, and its undo
#   StorybookLayout             the pages' rects on the handheld and the desk
#   Frames                      slides drawn once into layers, kept by what they show
#   StorybookAppLayer           the app: stories, a story's view, Present
#   StorybookAppLayer._scan     the stories in the store, each read strictly
#   StorybookAppLayer.draw      the frame: the page it is on
#   StorybookAppLayer._present  Present: the slide alone, its taps and transitions
"""Storybook -- the kids' slides app (docs/media/look_2026-10/APPS_RESTYLE.md,
Storybook): a story is slides of words, shapes and pictures, an app's own
document (runtime/story_doc.py), not a cart. One renderer draws a slide at
any zoom (runtime/story_render.py).

  STORIES   a card a story, its first slide: the handheld's at 1/8, two a
            row; the desk's at 1/4, three a row, New story first, Present
            and Open in the window's foot, M its card's menu (Rename,
            Duplicate, Delete, which asks).
  A STORY   on the handheld, a slide at 1/4 to look at, left and right step,
            P presents: a handheld shows stories and does not edit them, so
            the edit keys answer "Stories are edited on a desk". On the desk
            the sorter and the slide editor (runtime/story_edit.py).
  PRESENT   the slide alone at the largest zoom the glass takes (1:1 on the
            desk's 1024 x 600 glass, 5/16 on the handheld's 320 x 240), its
            objects appearing on taps, Fade and Swipe between slides, its
            number for a second after each change.

A story is the `stories` item `<name>` of the user files: `story.json` and
the pictures it uses. A story whose document does not read (another format,
an old story cart's deck) is not listed: it reads as absent.
"""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

try:
    from app_shell import ListShellApp
    from layout_base import bar_rows, content_of, metric_class, window_foot, DESK
    from ticks import _ticks_ms, _ticks_diff
    import story_doc as D
    import story_render as R
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.app_shell import ListShellApp
    from runtime.layout_base import (bar_rows, content_of, metric_class, window_foot,
                                     DESK)
    from runtime.ticks import _ticks_ms, _ticks_diff
    from runtime import story_doc as D
    from runtime import story_render as R

KIND = "stories"
DOC = "story.json"
TOAST_MS = 2000
NUMBER_MS = 1000
FADE_FRAMES = 6                 # each way, through black
SWIPE_FRAMES = 8
COMMIT_MS = 800                 # an edit is written this long after the last
PRESENT_ZOOMS = ((1, 1), (7, 8), (3, 4), (5, 8), (1, 2), (5, 16), (1, 4), (1, 8))
DESK_BUDGET = 8 << 20           # the pictures' pixels kept, desk and handheld
HAND_BUDGET = 384 << 10
EDIT_KEYS = (13, 10, 9, 127, 4, 26, 25, 3, 22, ord("["), ord("]"), ord("n"), ord("N"),
             ord("t"), ord("T"), ord("s"), ord("S"), ord("i"), ord("I"), ord("b"),
             ord("B"), ord("y"), ord("Y"), ord("m"), ord("M"), ord("r"), ord("R"),
             ord("g"), ord("G"))


def dot():
    return " · " if ui.has_symbols() else " - "


_DIM_CAPTION = {"tone": "dim", "face": "caption"}
_NEW_CARD = {"icon": "new", "tone": "value"}
_PRESENT = {"id": "Present"}
_PRESENT_OFF = {"disabled": True, "id": "Present"}
_OPEN = {"id": "Open"}
_OPEN_OFF = {"disabled": True, "id": "Open"}
_BACK = ("back", "Back")
_STORIES_HINTS = ("confirm", "Open", "P", "Present", "back", "Back")


class _Card:
    """A story's card as the stories page draws it, made once per story
    (`StorybookApp._cards`): its words, override table and picture's key."""

    def __init__(self, entry):
        doc = entry["doc"]
        k = len(doc["slides"])
        self.entry = entry
        self.doc = doc
        self.title_was = doc["title"]
        self.k = k
        self.title = doc["title"] or "Untitled"
        self.sub = "%d slide%s" % (k, "" if k == 1 else "s")
        self.status = None            # the desk foot's words, made when first focused
        self.o = {"id": "Story " + entry["name"]}
        self.doc_id = id(doc)
        self.key = None               # the picture's key in `cards`, at its size
        self.kw = self.kh = -1

    def holds(self, entry):
        doc = entry["doc"]
        return (entry is self.entry and doc is self.doc and doc["title"] is self.title_was
                and len(doc["slides"]) == self.k)


def zoom_fit(w, h):
    """The largest present zoom whose slide fits w x h: (num, den)."""
    for n, d in PRESENT_ZOOMS:
        if R.size(n, d)[0] <= w and R.size(n, d)[1] <= h:
            return (n, d)
    return PRESENT_ZOOMS[-1]


def zoom_word(n, d):
    words = {(1, 1): "1:1", (1, 2): "½", (1, 4): "¼", (1, 8): "⅛", (5, 8): "5/8",
             (5, 16): "5/16", (3, 4): "¾", (7, 8): "7/8"}
    w = words.get((n, d), "%d/%d" % (n, d))
    if not ui.has_symbols():
        w = {"½": "1/2", "¼": "1/4", "⅛": "1/8", "¾": "3/4"}.get(w, w)
    return w


# -- a drawing put in a story (Paint's and My Art's Put in a story) -------------------
#
# The one door another app takes into a story: the stories as cards, and a
# drawing added as a new last slide's background (story_doc.put_drawing),
# with its undo. Stories are user files: the files role reaches them.

def story_list(carts, files=None):
    """The stories a drawing can be put in, newest first: [{"title",
    "folder", "slides", "cover"}], `cover` None (the caller draws the
    Storybook's icon). `files` is the files role (`carts`, an older caller's
    first argument, is used where it is one)."""
    store = files if files is not None else carts
    if store is None or not hasattr(store, "list"):
        return []
    names, err = store.list(KIND)
    out = []
    for name in names or ():
        blob, err = store.load(KIND, name + "/" + DOC)
        doc = D.read(blob) if blob and err is None else None
        if doc is not None:
            out.append({"title": doc["title"] or "Untitled", "folder": name,
                        "slides": len(doc["slides"]), "cover": None})
    return out


def add_picture_slide(carts, files, story, name):
    """Drawing `name` (My Art's) as a new last slide of `story` (a row of
    `story_list`), its background: (title, slide number, undo), the number 0
    where it could not be added; `undo()` takes the slide out again while it
    is still the drawing's alone."""
    title = story["title"]
    i, pic, _t = D.put_drawing(files, story["folder"], name)
    if i is None:
        return (title, 0, None)

    def undo():
        return D.take_back(files, story["folder"], i, pic)
    return (title, i + 1, undo)


class StorybookLayout:
    """The pages' rects over the content rect: on the handheld the stories'
    grid, the story's slide and the hint band; on the desk the window's
    tool row, body and foot (its full-screen arrangement where the window
    fills the glass)."""

    FOOT = 44
    ROW = 50

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = fs = max(1, int(fs))
        self.cs = max(fs, int(cs)) if cs else fs
        self.windowed = bool(windowed)
        self.desk = metric_class() == DESK
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(self.cs))
        content = (self.cx, self.cy, self.cw, self.ch)
        self.content = content
        self.glass = (0, 0, self.w, self.h)
        self.full = self.desk and self.cw >= 1000 and self.ch >= 560
        if self.desk:
            under = window_foot() if self.windowed else 0
            self.foot, self.body = ui.cut_bottom(content, self.FOOT - under)
            x, y, w, h = content
            self.row = (x, y, w, self.ROW)
            self.row_rule = (x + 16, y + self.ROW, w - 32, 1)
            self.foot_rule = (x + 16, self.foot[1], w - 32, 1)
            self.caption = (x + 24, y + 8, w - 48, 30)
            self.card = (256, 150)
            self.cols = max(1, (w - 32 + 24) // (256 + 24))
            gw = self.cols * 256 + (self.cols - 1) * 24
            self.margin = max(16, (w - gw) // 2)
            self.stories_grid = (x, y + 28, w, self.foot[1] - y - 28)
            self.grid_o = {"cols": self.cols, "margin": self.margin, "foot": 44, "top": 4}
            self.deck_grid = (x, y + self.ROW + 1, w, self.foot[1] - y - self.ROW - 1)
            self._slide_rects()
        else:
            self.foot, self.body = ui.cut_bottom(content, 20 * fs)
            bx, by, bw, bh = self.body
            self.caption = (bx + 16 * fs, by + 2 * fs, bw - 32 * fs, 16 * fs)
            self.card = (128 * fs, 75 * fs)
            self.cols = 2
            self.margin = 16 * fs
            self.stories_grid = (bx, by + 16 * fs, bw, bh - 16 * fs)
            self.grid_o = {"cols": self.cols, "margin": self.margin, "foot": 32, "top": 4}
            sw, sh = R.size(fs, 4)
            self.view = (bx + (bw - sw) // 2, by + 4 * fs, sw, sh)
            self.stepper = (bx, by + 4 * fs + sh + 4 * fs, bw, 16 * fs)
            self.note = (bx, self.stepper[1] + 16 * fs, bw, 14 * fs)

    def _slide_rects(self):
        """The slide editor's: the slides column, the pasteboard and the
        slide on it, the style panel."""
        x, y, w, h = self.content
        top = y + (40 if self.full else self.ROW)
        bottom = self.foot[1]
        if self.full:
            self.edit_zoom = (5, 8)
            col_x, col_w = x + 8, 96
            pb = (x + 120, top + 8, 656, 500)
            self.row = (x, y, w, 40)
            self.row_rule = (x, y + 40, w, 1)
        else:
            self.edit_zoom = (1, 2)
            col_x, col_w = x + 12, 96
            pb = (x + 124, top + 8, 528, 384)
        sw, sh = R.size(self.edit_zoom[0], self.edit_zoom[1])
        bh = min(pb[3], bottom - 4 - pb[1])
        self.pasteboard = (pb[0], pb[1], pb[2], bh)
        self.slide = (pb[0] + (pb[2] - sw) // 2, pb[1] + max(8, (bh - sh) // 2), sw, sh)
        self.column = (col_x, top + 1, col_w, bottom - top - 2)
        self.column_rule = (col_x + col_w + 8, top + 1, 1, bottom - top - 2)
        px = pb[0] + pb[2] + 12
        self.panel = (px, top + 8, x + w - 16 - px, bottom - top - 16)


class Frames:
    """Slides drawn once into layers and placed from them: kept by what
    they show (the story's generation, the slide, the size, the tap), at
    most `n`, the least recently placed going first."""

    def __init__(self, renderer, n):
        self.renderer = renderer
        self.n = n
        self._kept = {}
        self._order = []

    def get(self, cv, key, slide, w, h, step=-1, edit=False, renderer=None):
        lay = self._kept.get(key)
        if lay is not None:
            if self._order[-1] != key:
                self._order.remove(key)
                self._order.append(key)
            return lay
        while len(self._order) >= self.n:
            self._drop(self._order[0])
        lay = cv.new_layer(w, h)
        # the slide covering w x h: its own zoom, or a lifted card's a little
        # more, the overhang cut evenly
        if w * R.SH >= h * R.SW:
            num, den = w, R.SW
        else:
            num, den = h, R.SH
        sw, sh = R.size(num, den)
        (renderer or self.renderer).draw(lay, slide, (w - sw) // 2, (h - sh) // 2, num, den,
                                         step, clip=(0, 0, w, h), edit=edit)
        self._kept[key] = lay
        self._order.append(key)
        return lay

    def has(self, key):
        return key in self._kept

    def place(self, cv, key, slide, x, y, w, h, clip=None, step=-1, edit=False,
              renderer=None):
        """The slide drawn into w x h at (x, y) of `cv`, inside `clip`."""
        lay = self.get(cv, key, slide, w, h, step, edit, renderer)
        cx, cy, cw, ch = clip if clip is not None else (x, y, w, h)
        x0, y0 = max(x, cx), max(y, cy)
        x1, y1 = min(x + w, cx + cw), min(y + h, cy + ch)
        if x1 > x0 and y1 > y0:
            cv.blit_strip_rect(lay, x, y, x0, y0, x1 - x0, y1 - y0)

    def _drop(self, key):
        lay = self._kept.pop(key)
        self._order.remove(key)
        lay.release()

    def drop(self, prefix=None):
        """Forget the frames (those whose key starts with `prefix`)."""
        for key in list(self._order):
            if prefix is None or key[0] == prefix:
                self._drop(key)


class StorybookAppLayer(ListShellApp):
    id = "storybook"
    domain = "system"
    TITLE = "Storybook"
    APP_TITLE = "Storybook"
    APP_PERM = "storybook"
    APP_FOLDER = "moybyte.storybook.moy"
    # `files` holds the stories and My Art's drawings; `nav` the keyboard's
    # text mode while a story's words are written.
    NEEDS = ("surface", "theme", "damage", "files", "nav")
    UI = True

    def __init__(self, ctx, names):
        self.ctx = ctx
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._files = ctx.files
        self._store = ctx.files
        self._nav = ctx.nav
        self._ui = ctx.ui
        self.names = names
        cv = self._surf.canvas()
        self.layout = StorybookLayout(cv.w, cv.h, self._surf.font_scale(),
                                      self._surf.windowed(), self._surf.chrome_scale())
        self.page = "stories"
        self.stories = []             # [{"name", "doc"}], newest first
        self.story = None             # the open story (story_doc.Story)
        self.cur = 0                  # the slide in view
        self.images = None
        self.renderer = None
        self.frames = None
        self.cards = None             # the stories' first slides, by story
        self.toast = None             # (text, icon, until ms)
        self.asking = None            # "delete", "rename": a dialog up
        self.menu = None              # an open menu's name
        self.typed = ""               # a dialog's field
        self.glass = False            # presenting: the app holds the whole glass
        self.show = None              # Present's state
        self.ed = None                # the desk's editor (runtime/story_edit.py)
        self._focus = None
        self._focused = None
        self._key = 0
        self._edited_at = None
        self._scanned = False
        self._rows = []               # the stories' cards (`_cards`)
        self._caption = (-1, "")      # (count, words) above the cards
        self._whole = (0, 0, 0, 0)    # the page's rect, the canvas's
        self._cap_lh = (None, 0)      # (theme generation, the caption face's line)

    # -- the app protocol (docs/app_api_v1.md) --------------------------------

    def open(self):
        self.page = "stories"
        self.asking = None
        self.menu = None
        self.show = None
        self.glass = False
        self.toast = None
        self._scan()
        if self.layout.desk and self.ed is None:
            try:
                from story_edit import Editor
            except ImportError:  # pragma: no cover - host fallback
                from runtime.story_edit import Editor
            self.ed = Editor(self)
        if self.stories:
            self._focus = self._card_id(self.stories[0]["name"])
        elif self.layout.desk:
            self._focus = "New story"
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.layout = StorybookLayout(w, h, fs, self._surf.windowed(), cs)
        if self.ed is not None:
            self.ed.relayout()

    def close(self):
        """Leaving, by any route: the open story is written, the pictures and
        frames given back."""
        self.commit()
        self._stop_writing()
        self.glass = False
        self.show = None
        if self.frames is not None:
            self.frames.drop()
        if self.cards is not None:
            self.cards.drop()
        if self.images is not None:
            self.images.forget()

    def commit(self):
        """Write the open story now (an exit path, docs/app_api_v1.md)."""
        st = self.story
        if st is None or not st.dirty:
            return
        st.dirty = False
        self._edited_at = None
        _v, err = self._files.save(KIND, st.name + "/" + DOC, D.write(st.doc))
        if err is not None:
            st.dirty = True
            self._say("This story can't be saved here", "warn")
        for s in self.stories:
            if s["name"] == st.name:
                s["doc"] = st.doc

    def page_title(self):
        """The bar's title (the window's on the desk): the open story's name,
        or the page of it that is open."""
        if self.page == "pictures":
            return "Add a picture"
        if self.story is not None and self.page != "stories":
            return self.story.title() or "Untitled"
        return None

    def has_page(self):
        return self.page != "stories" or self.asking is not None or self.menu is not None \
            or self.show is not None

    def back(self):
        """Back one level: Present stops, a dialog or a menu closes, the
        editor's pages step back to the sorter and the stories."""
        if self.show is not None:
            self._stop()
            return True
        if self.asking is not None or self.menu is not None:
            self.asking = None
            self.menu = None
            self._damage.all()
            return True
        if self.ed is not None and self.ed.back():
            return True
        if self.page == "stories":
            return False
        self._to_stories()
        return True

    # -- the store -------------------------------------------------------------------

    def _scan(self):
        """The stories in the store, newest first, each document read
        strictly: one that does not read is not listed."""
        names, err = self._files.list(KIND)
        out = []
        for name in names or ():
            blob, err = self._files.load(KIND, name + "/" + DOC)
            doc = D.read(blob) if blob else None
            if doc is not None:
                out.append({"name": name, "doc": doc})
        self.stories = out
        self._scanned = True

    def _load(self, name):
        """A picture of story `name`'s, its blob or None."""
        def load(image):
            blob, err = self._files.load(KIND, name + "/" + image + ".moyimg")
            return blob if err is None else None
        return load

    def _open_story(self, entry, page):
        if self.story is not None and self.story.name != entry["name"]:
            self.commit()
        if self.story is None or self.story.name != entry["name"]:
            self.story = D.Story(entry["name"], D.copy(entry["doc"]))
            budget = DESK_BUDGET if self.layout.desk else HAND_BUDGET
            if self.images is not None:
                self.images.forget()
            self.images = R.Images(self._load(entry["name"]), budget)
            self.renderer = R.Renderer(self.images)
            if self.frames is not None:
                self.frames.drop()
            self.frames = Frames(self.renderer, 24 if self.layout.desk else 4)
        self.cur = 0
        self.page = page
        self._damage.all()

    def _to_stories(self):
        self.commit()
        name = self.story.name if self.story is not None else None
        self._stop_writing()
        self.page = "stories"
        self._scan()
        if name is not None:
            self._focus = self._card_id(name)
        if self.cards is not None:
            self.cards.drop(name)
        self._damage.all()

    def edited(self):
        """An edit happened: write it once the edits pause."""
        self._edited_at = _ticks_ms()
        self._damage.all()

    def _stop_writing(self):
        if self.ed is not None:
            self.ed.stop_writing()

    # -- messages ---------------------------------------------------------------------

    def _say(self, text, icon="check"):
        self.toast = (text, icon, _ticks_ms() + TOAST_MS)
        self._damage.all()

    # -- input ------------------------------------------------------------------------

    def _typed(self, inp):
        """The key typed this frame (each press once), or 0."""
        k = getattr(inp, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        typed = k if k != 0 and k != self._key else 0
        self._key = k
        return typed

    def handle_input(self, inp):
        k = self._typed(inp)
        if self.toast is not None and (k or inp.pressed("a") or inp.pressed("b")):
            self.toast = None
            self._damage.all()
        if self.show is not None:
            self._present_keys(inp, k)
            return True
        if self.asking is not None or self.menu is not None:
            if k == 27:
                self.back()
                return True
            if self.asking == "rename":
                self._type_into(k)
            if self._ui.keys(inp):
                self._damage.all()
            return True
        if self.ed is not None and self.page != "stories":
            return self.ed.handle_input(inp, k)
        if self.page == "view":
            return self._view_keys(inp, k)
        return self._stories_keys(inp, k)

    def _type_into(self, k):
        """A dialog's field, typed into."""
        if k == 8:
            self.typed = self.typed[:-1]
        elif 32 <= k < 127 and len(self.typed) < D.MAX_TITLE:
            self.typed += chr(k)
        elif k in (13, 10):
            self._rename_done()
        else:
            return
        self._damage.all()

    def _stories_keys(self, inp, k):
        entry = self._focused_story()
        if (k == 112 or k == 80) and entry is not None:              # p, P
            self._open_story(entry, "view" if not self.layout.desk else "deck")
            self._start(0)
            return True
        if self.layout.desk:
            if (k == 109 or k == 77) and entry is not None:          # m, M
                self.menu = "story"
                self._damage.all()
                return True
            if k == 4 and entry is not None:
                self._duplicate_story(entry)
                return True
            if k == 127 and entry is not None:
                self.asking = "delete"
                self._damage.all()
                return True
            if k == 110 or k == 78:                                 # n, N
                self._new_story()
                return True
        if inp.pressed("b"):
            self.back()
            return True
        if self._ui.keys(inp):
            self._damage.all()
        return True

    def _view_keys(self, inp, k):
        n = len(self.story.slides)
        if inp.steps("left") or k == ord(","):
            self._step_view(-1)
        elif inp.steps("right") or k == ord("."):
            self._step_view(1)
        elif k == 112 or k == 80:                        # p, P
            self._start(self.cur)
        elif inp.pressed("b") or k == 27:
            self.back()
        elif inp.pressed("a") or k in EDIT_KEYS:
            self._say("Stories are edited on a desk", "warn")
        _ = n
        return True

    def _step_view(self, d):
        j = max(0, min(len(self.story.slides) - 1, self.cur + d))
        if j != self.cur:
            self.cur = j
            self._damage.all()

    def handle_pointer(self, px, py, click):
        down = bool(getattr(self._surf.pointer(), "down", False))
        if self.show is not None:
            self._present_pointer(px, py, click, down)
            return True
        if self.ed is not None and self.page != "stories" and self.asking is None \
                and self.menu is None:
            if self.ed.handle_pointer(px, py, click, down):
                return True
        if click and self.page == "view":
            for k, r in enumerate(self._arrows):
                if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                    self._step_view(-1 if k == 0 else 1)
                    return True
        if self._ui.point(px, py, click, down):
            self._damage.all()
        return True

    _arrows = ()

    # -- the frame --------------------------------------------------------------------

    def draw(self, dt):
        if self.toast is not None:
            if _ticks_diff(_ticks_ms(), self.toast[2]) >= 0:
                self.toast = None
            self._damage.again()
        if self._edited_at is not None:
            if _ticks_diff(_ticks_ms(), self._edited_at) >= COMMIT_MS:
                self.commit()
            else:
                self._damage.again()
        cv = self._surf.canvas()
        if self.show is not None:
            self._present(cv)
            return
        lay = self.layout
        th = self._theme.colors()
        keys_elsewhere = self.ed is not None and self.page != "stories" \
            and self.ed.keys_elsewhere()
        whole = self._whole
        if whole[2] != cv.w or whole[3] != cv.h:
            whole = self._whole = (0, 0, cv.w, cv.h)
        self._ui.begin(cv, th, whole, lay.desk, lay.windowed, elsewhere=keys_elsewhere)
        ui.page("")
        if self._focus is not None:
            ui.focus(self._focus)
            self._focus = None
        page = self.page
        asking = self.asking
        menu = self.menu
        title = self.page_title() or ""
        if self.page == "view":
            self._view(cv)
            ui.bar(title, ("play",), (), True)
        elif self.page != "stories" and self.ed is not None:
            self.ed.draw(cv, th)
            ui.bar(title, (), (), True)
        else:
            self._stories(cv)
            ui.bar("", (), (), False)
        self._dialogs()
        if self.toast is not None:
            self._toast()
        self._focused = ui.focus()
        if (self.page != page or self.asking != asking or self.menu != menu
                or self.show is not None):
            self._damage.again()

    # -- stories ----------------------------------------------------------------------

    def _card_id(self, name):
        return "Story " + name

    def _focused_story(self):
        f = self._focused
        if f is None:
            return None
        for row in self._cards():
            if row.o["id"] == f:
                return row.entry
        return None

    def _cards(self):
        """The stories' cards, made again for a story added, removed or
        changed; a frame where none did makes nothing."""
        rows = self._rows
        st = self.stories
        n = len(st)
        same = len(rows) == n
        if same:
            for i in range(n):
                if not rows[i].holds(st[i]):
                    same = False
                    break
        if not same:
            rows = self._rows = [_Card(e) for e in st]
        return rows

    def _card_pic(self, cv, row, x, y, w, h, clip):
        """A story's card: its first slide, drawn once at the card's size
        and kept; its pictures are read for that and let go."""
        if not w or not h:
            return
        if self.cards is None:
            self.cards = Frames(None, 12 if self.layout.desk else 6)
        entry = row.entry
        if row.kw != w or row.kh != h:
            row.key = (entry["name"], row.doc_id, w, h)
            row.kw = w
            row.kh = h
        key = row.key
        rend = None
        if not self.cards.has(key):
            rend = R.Renderer(R.Images(self._load(entry["name"]), 4 * w * h))
        self.cards.place(cv, key, row.doc["slides"][0], x, y, w, h, clip, renderer=rend)
        if rend is not None:
            rend.images.forget()

    def _stories(self, cv):
        lay = self.layout
        n = len(self.stories)
        if not n and not lay.desk:
            x, y, w, h = lay.body
            ui.at(x, y + 16 * lay.fs, w, h)
            ui.empty("storybook", "No stories yet",
                     ("Stories are made on a desk board", "or in the web build."))
            if ui.hints_width(_BACK) > 0:
                r = lay.foot
                ui.at(r[0], r[1], r[2], r[3])
                ui.hints(_BACK)
            return
        if self._caption[0] != n:
            self._caption = (n, "%d stor%s" % (n, "y" if n == 1 else "ies"))
        r = lay.caption
        ui.at(r[0], r[1], r[2], r[3])
        ui.label(self._caption[1], _DIM_CAPTION)
        grid = lay.stories_grid
        ui.at(grid[0], grid[1], grid[2], grid[3])
        ui.scroll("stories")
        cw, ch = lay.card
        ui.grid("stories", cw, ch, lay.grid_o)
        if lay.desk:
            r = ui.card("New story", "", _NEW_CARD)
            if r[4] & ui.ACTIVATED:
                self._new_story()
        for row in self._cards():
            r = ui.card(row.title, row.sub, row.o)
            self._card_pic(cv, row, r[0], r[1], r[2], r[3], grid)
            if r[4] & ui.ACTIVATED:
                self._open_story(row.entry, "deck" if lay.desk else "view")
                if self.ed is not None:
                    self.ed.opened()
        ui.grid()
        ui.end()
        ui.scroll()
        if lay.desk:
            self._stories_foot()
        elif ui.hints_width(_BACK) > 0:
            r = lay.foot
            ui.at(r[0], r[1], r[2], r[3])
            ui.hints(_STORIES_HINTS)

    def _stories_foot(self):
        lay = self.layout
        r = lay.foot_rule
        ui.at(r[0], r[1], r[2], r[3])
        ui.rule()
        fx, fy, fw, fh = lay.foot
        entry = None
        status = ""
        f = self._focused
        if f is not None:
            for row in self._rows:
                if row.o["id"] == f:
                    entry = row.entry
                    if row.status is None:
                        row.status = row.title + dot() + row.sub
                    status = row.status
                    break
        by = fy + max(1, (fh - 34) // 2)
        gen = self._theme.gen()
        if self._cap_lh[0] != gen:
            ui.face("caption")
            self._cap_lh = (gen, ui.text_size("Ag")[1])
            ui.face()
        lh = self._cap_lh[1]
        ui.at(fx + 26, fy + (fh - lh) // 2, fw - 300, lh + 2)
        ui.label(status, _DIM_CAPTION)
        ui.at(fx + fw - 16 - 86 - 8 - 100, by, 100, 32)
        ui.icon("play")
        if ui.button("Present", "normal", _PRESENT_OFF if entry is None else _PRESENT) \
                and entry is not None:
            self._open_story(entry, "deck")
            self._start(0)
        ui.at(fx + fw - 16 - 86, by, 86, 32)
        ui.icon("folder_open")
        if ui.button("Open", "primary", _OPEN_OFF if entry is None else _OPEN) \
                and entry is not None:
            self._open_story(entry, "deck")
            if self.ed is not None:
                self.ed.opened()

    # -- the stories' verbs (desk) ------------------------------------------------------

    def _new_story(self):
        """A new story: one Title slide, open in the editor with its title
        to be written. It is named from its title when it is first written."""
        name, err = self._files.new_name(KIND)
        if err is not None or not name:
            self._say("No room for a new story here", "warn")
            return
        doc = D.new_doc("")
        entry = {"name": name, "doc": doc}
        _v, err = self._files.save(KIND, name + "/" + DOC, D.write(doc))
        if err is not None:
            self._say("This story can't be saved here", "warn")
            return
        self.stories.insert(0, entry)
        self._open_story(entry, "slide")
        if self.ed is not None:
            self.ed.opened(new=True)

    def _duplicate_story(self, entry):
        name, err = self._files.duplicate(KIND, entry["name"])
        if err is not None:
            self._say("This story can't be copied here", "warn")
            return
        self._scan()
        for s in self.stories:
            if s["name"] == name:
                s["doc"]["title"] = (entry["doc"]["title"] + " 2")[:D.MAX_TITLE]
                self._files.save(KIND, name + "/" + DOC, D.write(s["doc"]))
        self._focus = self._card_id(name)
        self._say("Duplicated", "dup")

    def _delete_story(self, entry):
        _v, err = self._files.delete(KIND, entry["name"])
        if err is not None:
            self._say("This story can't be deleted here", "warn")
            return
        if self.story is not None and self.story.name == entry["name"]:
            self.story = None
        i = self.stories.index(entry)
        self.stories.pop(i)
        if self.stories:
            self._focus = self._card_id(self.stories[min(i, len(self.stories) - 1)]["name"])
        else:
            self._focus = "New story"
        self._say("Moved to the Trash", "trash")

    def _rename_done(self):
        entry = self._menu_story
        t = self.typed.strip()
        self.asking = None
        if entry is None or not t:
            return
        doc = entry["doc"]
        doc["title"] = t[:D.MAX_TITLE]
        self._files.save(KIND, entry["name"] + "/" + DOC, D.write(doc))
        if self.story is not None and self.story.name == entry["name"]:
            self.story.doc["title"] = doc["title"]
        if self.cards is not None:
            self.cards.drop(entry["name"])
        self._damage.all()

    _menu_story = None

    def _dialogs(self):
        lay = self.layout
        if self.menu == "story":
            entry = self._focused_story() or self._menu_story
            self._menu_story = entry
            if entry is None:
                self.menu = None
                return
            alive = ui.popover("Story menu", 260 if lay.desk else 180 * lay.fs, {"wash": True})
            a = ui.menu(entry["doc"]["title"] or "Untitled",
                        ["Rename…" if ui.has_symbols() else "Rename...", "Duplicate\tCtrl+D",
                         "Delete\tDel"], ["pencil", "dup", "trash"], kinds=("", "", "danger"))
            ui.popover()
            if not alive:
                self.menu = None
            if a == 0:
                self.menu = None
                self.typed = entry["doc"]["title"]
                self.asking = "rename"
            elif a == 1:
                self.menu = None
                self._duplicate_story(entry)
            elif a == 2:
                self.menu = None
                self.asking = "delete"
            return
        if self.asking == "delete":
            entry = self._focused_story() or self._menu_story
            if entry is None:
                self.asking = None
                return
            self._menu_story = entry
            pick = ui.dialog("Delete %s?" % (entry["doc"]["title"] or "this story"),
                             ("Cancel", "Delete"), "danger")
            ui.label("Its slides and pictures go to the Trash.", {"wrap": True, "lines": 3})
            ui.dialog()
            if pick == 1:
                self.asking = None
                self._delete_story(entry)
            elif pick == 0:
                self.asking = None
                self._damage.all()
        elif self.asking == "rename":
            self._menu_story = self._menu_story or self._focused_story()
            pick = ui.dialog("Rename the story", ("Cancel", "Rename"), "primary")
            self.typed, done = ui.field("Name", self.typed, D.MAX_TITLE,
                                        {"hint": "my story"})
            ui.dialog()
            if pick == 1 or done:
                self._rename_done()
            elif pick == 0:
                self.asking = None
                self._damage.all()

    def _toast(self):
        lay = self.layout
        text, icon, _until = self.toast
        if lay.desk:
            ui.at(lay.content[0], lay.content[1], lay.content[2],
                  lay.foot[1] - lay.content[1] - 12)
        else:
            x, y, w, h = lay.body
            ui.at(x, y, w, h - 6 * lay.fs)
        ui.toast(text, icon)

    # -- a story on the handheld: to look at ------------------------------------------------

    def _view(self, cv):
        lay = self.layout
        st = self.story
        n = len(st.slides)
        x, y, w, h = lay.view
        th = self._theme.colors()
        cv.shadow(x, y, w, h, 0, 1, 40, 6 * lay.fs, 0, 2 * lay.fs, (x, y, w, h, 0))
        key = ("view", st.gen, self.cur, w, h)
        self.frames.place(cv, key, st.slides[self.cur], x, y, w, h)
        cv.rectb(x - 1, y - 1, w + 2, h + 2, th["edge"])
        sx, sy, sw, sh = lay.stepper
        fs = lay.fs
        word = "%d / %d" % (self.cur + 1, n)
        tw = ui.text_size(word)[0]
        mid = sx + sw // 2
        self._arrows = ((mid - tw // 2 - 36 * fs, sy, 24 * fs, sh),
                        (mid + tw // 2 + 12 * fs, sy, 24 * fs, sh))
        for k, (ax, ay, aw, ah) in enumerate(self._arrows):
            ui.at(ax, ay, aw, ah)
            ui.icon("back" if k == 0 else "forward")
            ui.label("", {"tone": "dim"} if (k == 0 and self.cur == 0)
                     or (k == 1 and self.cur == n - 1) else None)
        ui.at(mid - tw // 2 - 4 * fs, sy, tw + 8 * fs, sh)
        ui.label(word, {"align": "center"})
        ui.at(*lay.note)
        ui.label("Edit stories on a desk board or the web", {"tone": "dim", "align": "center"})
        if ui.hints_width(("back", "Back")) > 0:
            fx, fy, fw, fh = lay.foot
            ui.face("caption")
            lw = ui.text_size("View only")[0]
            ui.face()
            ui.at(fx + 8 * fs, fy, lw + 4 * fs, fh)
            ui.label("View only", {"face": "caption"})
            ui.at(fx + lw + 14 * fs, fy, fw - lw - 14 * fs, fh)
            ui.hints(("change", "Slides", "P", "Present", "back", "Back"))
        t = ui.bar(self.story.title() or "Untitled", ("play",), (), True)
        if t == 0:
            self._start(self.cur)

    # -- Present ------------------------------------------------------------------------

    def _start(self, i):
        """Present from slide `i`: the slide alone on the whole glass."""
        if self.story is None:
            return
        self._stop_writing()
        self.commit()
        self.show = {"i": i, "step": 0, "from": None, "t": 0, "kind": "none",
                     "shown": _ticks_ms(), "was": self.page, "down": None}
        self.glass = True
        self._damage.all()

    def _stop(self):
        sh = self.show
        self.show = None
        self.glass = False
        if sh is not None:
            self.cur = sh["i"]
            if self.ed is not None:
                self.ed.presented(sh["i"])
        if self.frames is not None:
            self.frames.drop("show")
        self._damage.all()

    def _last_step(self, i):
        m = 0
        for o in self.story.slides[i]["objects"]:
            if o["step"] > m:
                m = o["step"]
        return m

    def _next(self):
        sh = self.show
        if sh["from"] is not None:
            return
        if sh["step"] < self._last_step(sh["i"]):
            sh["step"] += 1
            self._damage.all()
            return
        if sh["i"] + 1 >= len(self.story.slides):
            return
        self._go(sh["i"] + 1, 0)

    def _prev(self):
        sh = self.show
        if sh["from"] is not None or sh["i"] == 0:
            return
        self._go(sh["i"] - 1, self._last_step(sh["i"] - 1), back=True)

    def _go(self, j, step, back=False):
        sh = self.show
        kind = self.story.slides[j if not back else sh["i"]]["transition"]
        sh["from"] = (sh["i"], sh["step"]) if kind != "none" else None
        sh["kind"] = kind
        sh["back"] = back
        sh["t"] = 0
        sh["i"] = j
        sh["step"] = step
        sh["shown"] = _ticks_ms()
        self._damage.all()

    def _present_keys(self, inp, k):
        if inp.pressed("b") or k == 27:
            self._stop()
        elif inp.steps("right") or inp.pressed("a") or k in (13, 10, 32):
            self._next()
        elif inp.steps("left"):
            self._prev()

    def _present_pointer(self, px, py, click, down):
        sh = self.show
        if click:
            sh["down"] = (px, py)
            return
        if sh["down"] is not None and not down:
            x0, y0 = sh["down"]
            sh["down"] = None
            dx = px - x0
            if dx > 40:
                self._prev()
            else:
                self._next()

    def _present(self, cv):
        sh = self.show
        w, h = cv.w, cv.h
        n, d = zoom_fit(w, h)
        sw, sp = R.size(n, d)
        x, y = (w - sw) // 2, (h - sp) // 2
        cv.rect(0, 0, w, h, 0)
        clip = (x, y, sw, sp)
        frm = sh["from"]
        if frm is not None:
            sh["t"] += 1
            t = sh["t"]
            if sh["kind"] == "fade":
                total = 2 * FADE_FRAMES
                if t < FADE_FRAMES:
                    self._show_slide(cv, frm[0], frm[1], x, y, sw, sp, clip)
                    a = 255 * t // FADE_FRAMES
                else:
                    self._show_slide(cv, sh["i"], sh["step"], x, y, sw, sp, clip)
                    a = 255 * max(0, total - t) // FADE_FRAMES
                cv.shadow(x, y, sw, sp, 0, 0, min(255, a), 0)
            else:
                total = SWIPE_FRAMES
                off = sw * t // total
                if sh.get("back"):
                    self._show_slide(cv, frm[0], frm[1], x + off, y, sw, sp, clip)
                    self._show_slide(cv, sh["i"], sh["step"], x + off - sw, y, sw, sp, clip)
                else:
                    self._show_slide(cv, frm[0], frm[1], x - off, y, sw, sp, clip)
                    self._show_slide(cv, sh["i"], sh["step"], x - off + sw, y, sw, sp, clip)
            if t >= total:
                sh["from"] = None
                sh["shown"] = _ticks_ms()
            self._damage.again()
        else:
            self._show_slide(cv, sh["i"], sh["step"], x, y, sw, sp, clip)
        if _ticks_diff(_ticks_ms(), sh["shown"]) < NUMBER_MS or frm is not None:
            self._number(cv, x, y, sw, sp)
            self._damage.again()

    def _show_slide(self, cv, i, step, x, y, w, h, clip):
        key = ("show", self.story.gen, i, step, w, h)
        self.frames.place(cv, key, self.story.slides[i], x, y, w, h, clip,
                          step=step)

    def _number(self, cv, x, y, sw, sh):
        """The slide's number: in the black under the slide where there is
        room, else in a half-black pill on its corner."""
        word = "%d / %d" % (self.show["i"] + 1, len(self.story.slides))
        fs = self.layout.fs
        below = cv.h - (y + sh)
        size = 13 * fs if not self.layout.desk else 15
        tw = cv.face_width(1, size * 64, word) // 64
        if below >= size + 4:
            base = y + sh + (below + size * 7 // 10) // 2
            cv.face_text(1, size * 64, (x + sw - tw - 8 * fs) * 64, base * 64, word, 13)
            return
        pw, ph = tw + 20, size + 14
        px, py = x + sw - pw - 16, y + sh - ph - 16
        cv.shadow(px, py, pw, ph, ph // 2, 0, 128, 0)
        cv.face_text(1, size * 64, (px + 10) * 64, (py + 7 + size * 7 // 10) * 64, word, 7)
