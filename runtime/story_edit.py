# Map (grep -n a name to jump there):
#   Editor                 the desk's story editor: the sorter, a slide, Add a picture
#   Editor.draw            the page it is on
#   Editor._deck           the sorter: a card a slide, its verbs
#   Editor._slide          a slide: the tool row, the slides column, the slide, the panel
#   Editor._panel          the style panel: the chosen object's, else the slide's
#   Editor._slide_keys     the keys a slide takes: choose, move, resize, write, add
#   Editor.handle_pointer  a pointer on the slide: choose, drag, resize, snap
#   Editor._pictures       Add a picture: My Art's drawings, copied into the story
"""Storybook's editor, on the desk boards and in the web build
(docs/media/look_2026-10/APPS_RESTYLE.md, Storybook: Screens, Desk, Inputs).
A handheld does not edit a story; runtime/storybook_app.py makes this only
where the console is a desk.

  SLIDES    the sorter: a card a slide at 1/4, three a row, its number and
            its transition's icon; New slide last, which opens the layouts;
            N adds after the focused slide, [ and ] move it, Ctrl+D
            duplicates it, Del deletes it, M its menu.
  A SLIDE   at 1/2 in the window, 5/8 where the window fills the glass: the
            slides column at its left, the style panel at its right. The
            keys are the slide's until Y (the panel) or Esc with nothing
            chosen (the parts).
  ADD A PICTURE  My Art's drawings, each copied into the story when it is
            placed (copy-on-use); a picture at half its size reads the half
            baked beside it then.
"""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

try:
    import story_doc as D
    import story_render as R
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import story_doc as D
    from runtime import story_render as R
    from runtime.ticks import _ticks_ms, _ticks_diff

KIND = "stories"
STEP, GRID = 2, 16
SNAP_POINTER, SNAP_TOUCH = 6, 12
HANDLE, HANDLE_POINTER, HANDLE_TOUCH = 8, 16, 32
GUIDE = 0x4C3FD6                # the snap guide, in every theme
DOUBLE_MS = 400
LONG_MS = 600
TRANS_ICONS = ("trans_none", "trans_fade", "trans_swipe")
TRANS_NAMES = ("None", "Fade", "Swipe")
SCALE_WORDS = ("½", "1", "2", "3", "4")
LAYOUT_ICONS = ("text", "background", "slides", "layout")
SHAPE_TITLES = ("Rectangle", "Rounded rectangle", "Ellipse", "Line", "Arrow", "Star",
                "Speech bubble")


def _w565(rgb):
    return ((rgb >> 19) & 31) << 11 | ((rgb >> 10) & 63) << 5 | ((rgb >> 3) & 31)


GUIDE_565 = _w565(GUIDE)
WHITE_565 = 0xFFFF


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


class Editor:
    """The desk's editing pages of the app `app` (StorybookAppLayer): the
    story is the app's (`app.story`), so are the renderer, the frames and
    the files."""

    def __init__(self, app):
        self.app = app
        self.chosen = None            # the chosen object's index on the slide
        self.writing = False
        self.on_slide = True          # the keys are the slide's
        self.step = STEP
        self.resizing = False
        self.snap = True
        self.clip = None              # the copied object
        self.layout_kind = "picture"
        self.recent = [15, 7, 10, 1]  # the backgrounds last used
        self.menu = None              # add, layouts, more, slide, colours, bgcolours
        self.colour_key = None        # what the colours popover sets
        self.pic_mode = "add"         # pictures: "add" a picture, or the "bg"
        self.pic_tab = 0
        self.pics = None              # [(name, w, h)] My Art's drawings
        self.thumbs = {}              # name -> _Pic at its card's size
        self.added = []               # pictures copied in this session
        self.drag = None
        self.guides = ()
        self.say = ""                 # the foot's status while a drag lines up
        self._click = None            # (ms, x, y) of the last press: a double click
        self._press = None            # (ms, x, y): a press held, for a long press
        self._focus = None
        self._new = False

    # -- what the app asks -------------------------------------------------------------

    def relayout(self):
        self.app.frames.drop() if self.app.frames is not None else None

    def opened(self, new=False):
        self.chosen = None
        self.writing = False
        self.on_slide = True
        self.menu = None
        self.resizing = False
        self._new = new
        app = self.app
        if new:
            app.page = "slide"
            self.chosen = 0
            self._write()
        app._damage.all()

    def presented(self, i):
        self.app.cur = i

    def keys_elsewhere(self):
        return self.app.page == "slide" and self.on_slide and self.menu is None

    def back(self):
        app = self.app
        if self.menu is not None:
            self.menu = None
            app._damage.all()
            return True
        if app.page == "pictures":
            app.page = "slide"
            self._drop_thumbs()
            app._damage.all()
            return True
        if app.page == "slide":
            if self.writing:
                self.stop_writing()
                return True
            if self.chosen is not None:
                self.chosen = None
                app._damage.all()
                return True
            if not self.on_slide:
                self.on_slide = True
                app._damage.all()
                return True
            app.page = "deck"
            app._focus = "Slide %d" % app.cur
            app._damage.all()
            return True
        if app.page == "deck":
            self._prune()
            return False
        return False

    # -- the story's pictures ------------------------------------------------------------

    def _size_of(self, name):
        return self.app.images.size(name) or (0, 0)

    def _prune(self):
        """The pictures copied in this session that no slide uses go."""
        st = self.app.story
        if st is None:
            return
        used = st.images()
        for name in self.added:
            if name not in used:
                self.app._files.delete(KIND, st.name + "/" + name + ".moyimg")
                self.app._files.delete(KIND, st.name + "/" + name + "_h.moyimg")
        self.added = []

    # -- writing words -----------------------------------------------------------------

    def _write(self):
        """Write the chosen object's words (a text box's, a shape's)."""
        o = self._obj()
        if o is None or o["type"] not in ("text", "shape"):
            return
        st = self.app.story
        st.record(self.app.cur)
        self.writing = True
        self.app._nav.text_mode(True)
        self.app._damage.all()

    def stop_writing(self):
        if not self.writing:
            return
        self.writing = False
        self.app._nav.text_mode(False)
        st = self.app.story
        if st is not None and self._new and self.app.cur == 0:
            objs = st.objects(0)
            t = objs[0]["text"].strip() if objs and objs[0]["type"] == "text" else ""
            if t and not st.title():
                st.set_title(t)
                self._rename_item(t)
        self._new = False
        self.app.edited()

    def _rename_item(self, title):
        """A new story's item takes its title's name."""
        app = self.app
        st = app.story
        app.commit()
        name, err = app._files.rename(KIND, st.name, title)
        if err is None and name:
            for s in app.stories:
                if s["name"] == st.name:
                    s["name"] = name
            st.name = name
            app.images.load = app._load(name)

    def _type(self, k):
        app = self.app
        st = app.story
        o = self._obj()
        if o is None:
            self.writing = False
            return
        t = o["text"]
        if k == 8:
            t = t[:-1]
        elif k in (13, 10, 27):
            self.stop_writing()
            return
        elif 32 <= k < 127 and len(t) < D.MAX_TEXT:
            t += chr(k)
        else:
            return
        o["text"] = t
        st.dirty = True
        st.gen += 1
        app.edited()

    # -- the chosen object ------------------------------------------------------------------

    def _slide_doc(self):
        return self.app.story.slides[self.app.cur]

    def _obj(self):
        objs = self._slide_doc()["objects"]
        if self.chosen is None or not 0 <= self.chosen < len(objs):
            self.chosen = None
            return None
        return objs[self.chosen]

    def _bounds(self, o):
        """(x, y, w, h) an object covers, in slide pixels."""
        if o["type"] == "text":
            cv = self.app._surf.canvas()
            return (o["x"], o["y"], o["w"], self.app.renderer.box_h(cv, o))
        return D.bounds(o, self._size_of)

    def _to_screen(self, b):
        lay = self.app.layout
        n, d = lay.edit_zoom
        sx, sy = lay.slide[0], lay.slide[1]
        return (sx + b[0] * n // d, sy + b[1] * n // d, b[2] * n // d, b[3] * n // d)

    def _to_slide(self, px, py):
        lay = self.app.layout
        n, d = lay.edit_zoom
        return ((px - lay.slide[0]) * d // n, (py - lay.slide[1]) * d // n)

    def _choose(self, j):
        if self.writing:
            self.stop_writing()
        self.chosen = j
        self.resizing = False
        self.app._damage.all()

    # -- edits -----------------------------------------------------------------------------

    def _edited(self):
        self.app.edited()

    def _add(self, kind):
        app = self.app
        st = app.story
        i = app.cur
        if len(st.objects(i)) >= D.MAX_OBJECTS:
            app._say("A slide holds %d things" % D.MAX_OBJECTS, "warn")
            return
        ink = D.ink_on(st.slides[i]["bg"])
        if kind == "text":
            o = D.text_obj("", 64, 260, 896, 64, ink)
        else:
            o = D.shape_obj("round", 312, 180, 400, 220, 7, 0, 8, "", 64, 0)
        j = st.add(i, o)
        self._choose(j)
        self.on_slide = True
        if kind == "text":
            self._write()
        self._edited()

    def _delete(self):
        o = self._obj()
        if o is None:
            return
        self.app.story.delete(self.app.cur, self.chosen)
        self.chosen = None
        self.app._say("Deleted" + D_DOT() + "Ctrl+Z brings it back", "trash")
        self._edited()

    def _duplicate(self):
        if self._obj() is None:
            return
        j = self.app.story.duplicate(self.app.cur, self.chosen)
        if j is None:
            self.app._say("A slide holds %d things" % D.MAX_OBJECTS, "warn")
            return
        self._choose(j)
        self._edited()

    def _copy(self):
        o = self._obj()
        if o is None:
            return
        self.clip = (self.app.story.name, D.copy(o))
        self.app._say("Copied", "copy")

    def _paste(self):
        if self.clip is None:
            return
        name, o = self.clip
        o = D.copy(o)
        if o["type"] == "picture" and o["image"] is not None and name != self.app.story.name:
            self.app._say("A picture pastes into its own story", "warn")
            return
        st = self.app.story
        j = st.add(self.app.cur, o)
        if j is None:
            self.app._say("A slide holds %d things" % D.MAX_OBJECTS, "warn")
            return
        self._choose(j)
        self._edited()

    def _front(self, back=False):
        if self._obj() is None:
            return
        st = self.app.story
        self.chosen = st.to_back(self.app.cur, self.chosen) if back \
            else st.to_front(self.app.cur, self.chosen)
        self._edited()

    def _undo(self, redo=False):
        st = self.app.story
        got = st.redo() if redo else st.undo()
        if got is False:
            return
        if got is not None:
            self.app.cur = got
        self.app.cur = min(self.app.cur, len(st.slides) - 1)
        self.chosen = None
        self.writing = False
        self._edited()

    def _move(self, dx, dy):
        o = self._obj()
        if o is None:
            return
        st = self.app.story
        if self.resizing:
            self._resize_by(o, dx, dy)
            return
        st.place(self.app.cur, self.chosen, o["x"] + dx, o["y"] + dy, hold=False)
        self._edited()

    def _resize_by(self, o, dx, dy, keep=False):
        st = self.app.story
        i, j = self.app.cur, self.chosen
        if o["type"] == "picture":
            if o["image"] is None:
                st.place(i, j, o["x"], o["y"], max(16, o["w"] + dx), max(16, o["h"] + dy),
                         hold=False)
            else:
                k = D.SCALES.index(o["scale"])
                k = max(0, min(len(D.SCALES) - 1, k + (1 if dx + dy > 0 else -1)))
                st.set(i, j, "scale", D.SCALES[k])
        elif o["type"] == "text":
            if keep:
                st.set(i, j, "size", max(D.SIZE_MIN, min(D.SIZE_MAX, o["size"] + dx)))
            else:
                st.place(i, j, o["x"], o["y"], max(32, o["w"] + dx), None, hold=False)
        elif o["type"] == "shape":
            w, h = o["w"], o["h"]
            if keep and w:
                nw = max(8, w + dx)
                st.place(i, j, o["x"], o["y"], nw, max(8, h * nw // w), hold=False)
            else:
                st.place(i, j, o["x"], o["y"], max(0, w + dx), max(0, h + dy), hold=False)
        self._edited()

    def _centre(self):
        o = self._obj()
        if o is None:
            return
        b = self._bounds(o)
        self.app.story.place(self.app.cur, self.chosen, (D.SW - b[2]) // 2,
                             (D.SH - b[3]) // 2, hold=False)
        self._edited()

    def _goto(self, i):
        app = self.app
        i = max(0, min(len(app.story.slides) - 1, i))
        if i != app.cur:
            self.stop_writing()
            app.cur = i
            self.chosen = None
            app._damage.all()

    def _new_slide(self, after, kind=None):
        app = self.app
        kind = kind or self.layout_kind
        self.layout_kind = kind
        j = app.story.add_slide(after, kind)
        if j is None:
            app._say("A story holds %d slides" % D.MAX_SLIDES, "warn")
            return None
        app.cur = j
        app._focus = "Slide %d" % j
        self._edited()
        return j

    def _set_slide(self, key, value):
        if self.app.story.set_slide(self.app.cur, key, value):
            if key == "bg" and isinstance(value, int):
                if value in self.recent:
                    self.recent.remove(value)
                self.recent.insert(0, value)
                self.recent = self.recent[:4]
            self._edited()

    def _set(self, key, value):
        if self._obj() is None:
            return
        if self.app.story.set(self.app.cur, self.chosen, key, value):
            self._edited()

    # -- input --------------------------------------------------------------------------

    def handle_input(self, inp, k):
        app = self.app
        if self.menu is not None:
            if k == 27 or inp.pressed("b"):
                self.back()
                return True
            if app._ui.keys(inp):
                app._damage.all()
            return True
        if k in (ord("p"), ord("P")) and not self.writing:
            app._start(app.cur)
            return True
        if app.page == "slide" and self.on_slide:
            return self._slide_keys(inp, k)
        if app.page == "deck" and self._deck_keys(k):
            return True
        if k == 26:
            self._undo()
            return True
        if k == 25:
            self._undo(redo=True)
            return True
        if app.page == "slide" and k in (27, ord("y"), ord("Y")) and not self.on_slide:
            self.on_slide = True
            app._damage.all()
            return True
        if inp.pressed("b") or k == 27:
            app.back()
            return True
        if app._ui.keys(inp):
            app._damage.all()
        return True

    def _deck_keys(self, k):
        app = self.app
        st = app.story
        f = app._focused or ""
        i = int(f[6:]) if f.startswith("Slide ") and f[6:].isdigit() else None
        if i is None or i >= len(st.slides):
            if k in (ord("n"), ord("N")):
                self._new_slide(len(st.slides) - 1)
                return True
            return False
        if k in (ord("n"), ord("N")):
            self._new_slide(i)
        elif k in (ord("["), ord("]")):
            j = st.move_slide(i, -1 if k == ord("[") else 1)
            if j is not None:
                app.cur = j
                app._focus = "Slide %d" % j
                self._edited()
        elif k == 4:
            j = st.duplicate_slide(i)
            if j is None:
                app._say("A story holds %d slides" % D.MAX_SLIDES, "warn")
            else:
                app._focus = "Slide %d" % j
                self._edited()
        elif k == 127:
            self._delete_slide(i)
        elif k in (ord("m"), ord("M")):
            app.cur = i
            self.menu = "slide"
            app._damage.all()
        else:
            return False
        return True

    def _delete_slide(self, i):
        app = self.app
        j = app.story.delete_slide(i)
        if j is None:
            app._say("A story keeps one slide", "warn")
            return
        app.cur = j
        app._focus = "Slide %d" % j
        app._say("Slide deleted" + D_DOT() + "Ctrl+Z brings it back", "trash")
        self._edited()

    def _slide_keys(self, inp, k):
        app = self.app
        if self.writing:
            if k:
                self._type(k)
            return True
        step = self.step
        if inp.pressed("left"):
            self._move(-step, 0)
        elif inp.pressed("right"):
            self._move(step, 0)
        elif inp.pressed("up"):
            self._move(0, -step)
        elif inp.pressed("down"):
            self._move(0, step)
        elif inp.pressed("a") or k in (13, 10):
            o = self._obj()
            if o is not None and o["type"] == "picture" and o["image"] is None:
                self._open_pictures("add")
            else:
                self._write()
        elif k == 9:
            n = len(self._slide_doc()["objects"])
            if n:
                self._choose(0 if self.chosen is None else (self.chosen + 1) % n)
        elif inp.pressed("b") or k == 27:
            if self.chosen is not None:
                self._choose(None)
            else:
                self.on_slide = False
                app._damage.all()
        elif k in (ord("t"), ord("T")):
            self._add("text")
        elif k in (ord("s"), ord("S")):
            self._add("shape")
        elif k in (ord("i"), ord("I")):
            self._open_pictures("add")
        elif k in (ord("b"), ord("B")):
            self._open_pictures("bg")
        elif k in (ord("y"), ord("Y")):
            self.on_slide = False
            app._damage.all()
        elif k in (ord("m"), ord("M")):
            self.menu = "more"
            app._damage.all()
        elif k in (ord("r"), ord("R")):
            self.resizing = not self.resizing and self.chosen is not None
            app._damage.all()
        elif k in (ord("+"), ord("=")) and self._obj() is not None:
            self._resize_by(self._obj(), 8, 8, keep=True)
        elif k in (ord("-"), ord("_")) and self._obj() is not None:
            self._resize_by(self._obj(), -8, -8, keep=True)
        elif k in (ord("g"), ord("G")):
            self.step = GRID if self.step == STEP else STEP
            app._damage.all()
        elif k == ord("]"):
            self._front()
        elif k == ord("["):
            self._front(back=True)
        elif k == 3:
            self._copy()
        elif k == 22:
            self._paste()
        elif k == 4:
            self._duplicate()
        elif k in (127, 8):
            self._delete()
        elif k == 26:
            self._undo()
        elif k == 25:
            self._undo(redo=True)
        elif k == ord("."):
            self._goto(app.cur + 1)
        elif k == ord(","):
            self._goto(app.cur - 1)
        elif k in (ord("n"), ord("N")):
            self._new_slide(app.cur)
            self.chosen = None
        return True

    # -- a pointer -----------------------------------------------------------------------

    def handle_pointer(self, px, py, click, down):
        """A pointer sample on the slide: True where the slide took it."""
        app = self.app
        if app.page != "slide" or self.menu is not None:
            return False
        lay = app.layout
        sx, sy, sw, sh = lay.slide
        inside = sx <= px < sx + sw and sy <= py < sy + sh
        now = _ticks_ms()
        if click:
            if not inside and self._handle_at(px, py) is None:
                return False
            self.on_slide = True
            twice = self._click is not None and _ticks_diff(now, self._click[0]) < DOUBLE_MS \
                and abs(px - self._click[1]) < 6 and abs(py - self._click[2]) < 6
            self._click = (now, px, py)
            self._press = (now, px, py)
            h = self._handle_at(px, py)
            if h is not None:
                o = self._obj()
                self.drag = ("size", h, px, py, D.copy(o))
                return True
            j = self._hit(px, py)
            if twice and j is not None and j == self.chosen:
                self._write()
                return True
            if j is None:
                self._choose(None)
                self.drag = None
                return True
            if j == self.chosen and not twice:
                under = self._hit(px, py, below=j)
                self.drag = ("maybe", j, px, py, under)
            else:
                self._choose(j)
                self.drag = ("move", j, px, py, D.copy(self._obj()))
            return True
        if self.drag is None:
            return False
        if down:
            if self._press is not None and _ticks_diff(now, self._press[0]) > LONG_MS \
                    and abs(px - self._press[1]) < 6 and abs(py - self._press[2]) < 6 \
                    and ui.input_kind() == "touch":
                self._press = None
                self.drag = None
                self.menu = "more"
                app._damage.all()
                return True
            self._drag_to(px, py)
            return True
        # released
        d = self.drag
        self.drag = None
        self._press = None
        self.guides = ()
        self.say = ""
        if d[0] == "maybe":
            if d[4] is not None:
                self._choose(d[4])      # a tap again: the one beneath
        app.story.end()
        app._damage.all()
        return True

    def _hit(self, px, py, below=None):
        """The object under the screen point, topmost first (under `below`)."""
        objs = self._slide_doc()["objects"]
        top = len(objs) - 1 if below is None else below - 1
        for j in range(top, -1, -1):
            x, y, w, h = self._to_screen(self._bounds(objs[j]))
            if x <= px < x + max(w, 4) and y <= py < y + max(h, 4):
                return j
        return None

    def _handles(self, rect):
        x, y, w, h = rect
        return ((x, y), (x + w // 2, y), (x + w, y), (x + w, y + h // 2), (x + w, y + h),
                (x + w // 2, y + h), (x, y + h), (x, y + h // 2))

    def _handle_at(self, px, py):
        o = self._obj()
        if o is None:
            return None
        reach = (HANDLE_TOUCH if ui.input_kind() == "touch" else HANDLE_POINTER) // 2
        for k, (hx, hy) in enumerate(self._handles(self._to_screen(self._bounds(o)))):
            if abs(px - hx) <= reach and abs(py - hy) <= reach:
                return k
        return None

    def _drag_to(self, px, py):
        app = self.app
        lay = app.layout
        n, d = lay.edit_zoom
        kind = self.drag[0]
        if kind == "maybe":
            if abs(px - self.drag[2]) < 4 and abs(py - self.drag[3]) < 4:
                return
            j = self.drag[1]
            self.drag = ("move", j, self.drag[2], self.drag[3], D.copy(self._obj()))
            kind = "move"
        _k, j, x0, y0, was = self.drag
        dx = (px - x0) * d // n
        dy = (py - y0) * d // n
        st = app.story
        if kind == "move":
            b = self._bounds(was)
            nx, ny = was["x"] + dx, was["y"] + dy
            nx, ny = self._snapped(nx, ny, b[2], b[3], j)
            st.place(app.cur, j, nx, ny)
        else:
            self._size_drag(was, j, dx, dy)
        app._damage.all()

    def _size_drag(self, was, j, dx, dy):
        app = self.app
        st = app.story
        h = self.drag[1]
        x, y = was["x"], was["y"]
        if was["type"] == "picture" and was["image"] is not None:
            w0, h0 = self._bounds(was)[2:]
            grow = dx if h in (2, 3, 4) else -dx
            target = max(8, w0 + grow)
            src = self._size_of(was["image"])[0] or 1
            best = min(D.SCALES, key=lambda s: abs(src * s - target))
            st.set(app.cur, j, "scale", best, hold=True)
            return
        w = was.get("w", 0)
        hh = was.get("h", 0)
        if h in (0, 6, 7):
            x, w = was["x"] + dx, w - dx
        if h in (2, 3, 4):
            w = w + dx
        if was["type"] != "text":
            if h in (0, 1, 2):
                y, hh = was["y"] + dy, hh - dy
            if h in (4, 5, 6):
                hh = hh + dy
        w = max(16, w)
        hh = max(8, hh)
        st.place(app.cur, j, x, y, w, None if was["type"] == "text" else hh)

    def _snapped(self, x, y, w, h, j):
        """(x, y) moved onto a guide within the snap's reach: the slide's
        edges and centre lines, the others' edges and centres."""
        self.guides = ()
        self.say = ""
        if not self.snap:
            return x, y
        lay = self.app.layout
        n, d = lay.edit_zoom
        reach = (SNAP_TOUCH if ui.input_kind() == "touch" else SNAP_POINTER) * d // n
        xs = [(0, "the slide's edge"), (D.SW // 2, "the slide's middle"),
              (D.SW, "the slide's edge")]
        ys = [(0, "the slide's edge"), (D.SH // 2, "the slide's middle"),
              (D.SH, "the slide's edge")]
        objs = self._slide_doc()["objects"]
        for k, o in enumerate(objs):
            if k == j:
                continue
            b = self._bounds(o)
            name = self._name_of(o)
            xs += [(b[0], name), (b[0] + b[2] // 2, name), (b[0] + b[2], name)]
            ys += [(b[1], name), (b[1] + b[3] // 2, name), (b[1] + b[3], name)]
        guides = []
        said = []
        best = None
        for v, name in xs:
            for off in (0, w // 2, w):
                gap = v - (x + off)
                if abs(gap) <= reach and (best is None or abs(gap) < abs(best[0])):
                    best = (gap, v, name)
        if best is not None:
            x += best[0]
            guides.append(("v", best[1]))
            said.append(best[2])
        best = None
        for v, name in ys:
            for off in (0, h // 2, h):
                gap = v - (y + off)
                if abs(gap) <= reach and (best is None or abs(gap) < abs(best[0])):
                    best = (gap, v, name)
        if best is not None:
            y += best[0]
            guides.append(("h", best[1]))
            if best[2] not in said:
                said.append(best[2])
        self.guides = tuple(guides)
        if said:
            self.say = "moving: lined up with " + " and ".join(said)
        return x, y

    def _name_of(self, o):
        t = o["type"]
        if t == "picture":
            return "the picture" if o["image"] is None else "the " + o["image"]
        if t == "text":
            return "the words"
        return "the " + SHAPE_TITLES[D.SHAPES.index(o["shape"])].lower()

    # -- the frame ------------------------------------------------------------------------

    def draw(self, cv, th):
        app = self.app
        page = app.page
        if page == "deck":
            self._deck(cv, th)
        elif page == "pictures":
            self._pictures(cv, th)
        else:
            self._slide(cv, th)
        self._menus()

    # -- the sorter -------------------------------------------------------------------------

    def _deck(self, cv, th):
        app = self.app
        lay = app.layout
        st = app.story
        x, y, w, _h = lay.row
        by = y + (lay.ROW - 34) // 2
        bx = x + 16
        f = app._focused or ""
        i = int(f[6:]) if f.startswith("Slide ") and f[6:].isdigit() else None
        has = i is not None and i < len(st.slides)
        for label, icon, kind, wd, act in (
                ("New slide", "layout", "normal", 116, "new"),
                ("Duplicate", "dup", "normal", 112, "dup"),
                ("Delete", "trash", "danger", 92, "del")):
            ui.at(bx, by, wd, 32)
            ui.icon(icon)
            o = {"id": label}
            if act != "new" and not has:
                o["disabled"] = True
            if ui.button(label, kind, o):
                if act == "new":
                    self.menu = "layouts"
                elif act == "dup" and has:
                    self._deck_keys(4)
                elif act == "del" and has:
                    self._delete_slide(i)
            bx += wd + 8
        bx += 2
        ui.at(bx, by, 1, 32)
        ui.rule()
        bx += 12
        bx = self._undo_tools(bx, by)
        self._present_button(x + w - 16 - 124, by)
        ui.at(*lay.row_rule)
        ui.rule()
        grid = lay.deck_grid
        ui.at(*grid)
        ui.scroll("slides")
        ui.grid("slides", 256, 150, {"cols": lay.cols, "margin": lay.margin, "foot": 30,
                                     "top": 12})
        for k, s in enumerate(st.slides):
            cx, cy, cw, ch, state = ui.card("", "", {"id": "Slide %d" % k})
            if cw:
                key = ("card", st.gen, k, cw, ch)
                app.frames.place(cv, key, s, cx, cy, cw, ch, grid)
                ui.at(cx, cy + ch + 4, 80, 20)
                ui.icon(TRANS_ICONS[D.TRANSITIONS.index(s["transition"])])
                ui.label(str(k + 1), {"tone": "dim", "face": "caption"})
            if state & ui.ACTIVATED:
                app.cur = k
                app.page = "slide"
                self.chosen = None
                self.on_slide = True
                app._damage.all()
        if len(st.slides) < D.MAX_SLIDES:
            _x, _y, _w, _h2, state = ui.card("New slide", "", {"icon": "new",
                                                              "tone": "value"})
            if state & ui.ACTIVATED:
                self.menu = "layouts"
        ui.grid()
        ui.end()
        ui.scroll()
        status = ""
        if has:
            status = "Slide %d" % (i + 1)
            t = self._slide_words(st.slides[i])
            if t:
                status += app_dot() + t
            status += app_dot() + "the slides at " + _zw(1, 4)
        self._foot(status, ("confirm", "Edit", "[ ]", "Move", "Ctrl D", "Duplicate", "Del",
                            "Delete"))

    def _slide_words(self, s):
        for o in s["objects"]:
            if o["type"] in ("text", "shape") and o["text"]:
                return o["text"]
        return ""

    def _undo_tools(self, bx, by):
        st = self.app.story
        ui.at(bx, by - 1, 36, 36)
        if ui.tool("undo", "Undo\tCtrl+Z", {"disabled": True} if not st.can_undo() else None):
            self._undo()
        ui.at(bx + 40, by - 1, 36, 36)
        if ui.tool("redo", "Redo\tCtrl+Y", {"disabled": True} if not st.can_redo() else None):
            self._undo(redo=True)
        return bx + 80

    def _present_button(self, x, y):
        ui.at(x, y, 124, 34)
        ui.icon("play")
        if ui.button("Present", "primary", {"id": "Present"}):
            self.app._start(self.app.cur)

    def _foot(self, status, hints):
        lay = self.app.layout
        ui.at(*lay.foot_rule)
        ui.rule()
        fx, fy, fw, fh = lay.foot
        hw = ui.hints_width(hints)
        ui.face("caption")
        lh = ui.text_size("Ag")[1]
        ui.face()
        ui.at(fx + 26, fy + (fh - lh) // 2, max(0, fw - 26 - hw - 40), lh + 2)
        ui.label(status, {"tone": "dim", "face": "caption"})
        if hw > 0:
            ui.at(fx + fw - 16 - hw, fy, hw, fh)
            ui.hints(hints)

    # -- a slide ---------------------------------------------------------------------------

    def _slide(self, cv, th):
        app = self.app
        lay = app.layout
        st = app.story
        self._tool_row()
        self._column(cv)
        ui.at(*lay.column_rule)
        ui.rule()
        px, py, pw, ph = lay.pasteboard
        ui.at(px, py, pw, ph)
        ui.part("Pasteboard", pw, ph)
        cv.rrect(px, py, pw, ph, 10, th["surface_alt"])
        sx, sy, sw, sh = lay.slide
        ui.at(sx, sy, sw, sh)
        ui.part("Slide", sw, sh)
        key = ("edit", st.gen, app.cur, sw, sh)
        app.frames.place(cv, key, st.slides[app.cur], sx, sy, sw, sh, edit=True)
        cv.rectb(sx - 1, sy - 1, sw + 2, sh + 2, th["edge"])
        if self.step == GRID and self.on_slide:
            self._grid(cv, sx, sy, sw, sh)
        self._chosen(cv, th)
        for g in self.guides:
            self._guide(cv, g)
        self._panel()
        self._slide_foot()

    def _tool_row(self):
        app = self.app
        lay = app.layout
        st = app.story
        x, y, w, _h = lay.row
        rh = 34 if not lay.full else 32
        by = y + ((lay.ROW if not lay.full else 40) - rh) // 2
        bx = x + 16
        if lay.full:
            ui.at(bx, by, 28, rh)
            if ui.tool("back", "Back\tEsc"):
                app.back()
            bx += 34
            ui.face("title")
            tw = ui.text_size(st.title() or "Untitled")[0]
            ui.face()
            ui.at(bx, by, tw + 8, rh)
            ui.label(st.title() or "Untitled", {"face": "title"})
            bx += tw + 24
        ui.at(bx, by, 92, rh)
        ui.icon("new")
        if ui.button("Add", "normal", {"id": "Add"}):
            self.menu = "add"
        bx += 100
        ui.at(bx, by, 1, rh)
        ui.rule()
        bx += 12
        has = self._obj() is not None
        off = {"disabled": True} if not has else {}
        for label, icon, act, wd in (("Front", "to_front", "front", 76),
                                     ("Back", "to_back", "back", 72)):
            ui.at(bx, by, wd, rh)
            ui.icon(icon)
            if ui.button(label, "normal", dict(off, id=label)):
                self._front(back=act == "back")
            bx += wd + 4
        ui.at(bx + 4, by, 1, rh)
        ui.rule()
        bx += 16
        ui.at(bx, by, 72, rh)
        ui.icon("copy")
        if ui.button("Copy", "normal", dict(off, id="Copy")):
            self._copy()
        bx += 76
        ui.at(bx, by, 76, rh)
        ui.icon("paste")
        if ui.button("Paste", "normal", {"id": "Paste", "disabled": True} if self.clip is None
                     else {"id": "Paste"}):
            self._paste()
        bx += 84
        ui.at(bx, by, 1, rh)
        ui.rule()
        bx += 12
        bx = self._undo_tools(bx, by)
        right = x + w - 16 - 124
        if not lay.full:
            n = len(st.slides)
            sx = right - 12 - 104
            ui.at(sx, by, 28, rh)
            if ui.tool("back", "Previous slide\t,", {"disabled": True} if app.cur == 0
                       else None):
                self._goto(app.cur - 1)
            ui.at(sx + 28, by, 48, rh)
            ui.label("%d/%d" % (app.cur + 1, n), {"align": "center"})
            ui.at(sx + 76, by, 28, rh)
            if ui.tool("forward", "Next slide\t.", {"disabled": True} if app.cur >= n - 1
                       else None):
                self._goto(app.cur + 1)
        self._present_button(right, by)
        ui.at(*lay.row_rule)
        ui.rule()

    def _column(self, cv):
        """The slides column: each slide at 5/64 (80 x 46) a 76 px pitch,
        its number and its transition's icon under it."""
        app = self.app
        lay = app.layout
        st = app.story
        x, y, w, h = lay.column
        ui.at(x, y, w, h)
        ui.scroll("column")
        for k, s in enumerate(st.slides):
            ui.at(x, y + 4 + k * 76, w, 72)
            px, py, pw, ph, state = ui.part("Column %d" % k, w, 72,
                                            {"current": k == app.cur})
            if pw:
                fx, fy = px + (pw - 80) // 2, py + 4
                app.frames.place(cv, ("col", st.gen, k, 80, 46), s, fx, fy, 80, 46,
                                 (x, y, w, h))
                ui.at(fx - 2, fy + 48, 60, 18)
                ui.icon(TRANS_ICONS[D.TRANSITIONS.index(s["transition"])])
                ui.label(str(k + 1), {"tone": "dim", "face": "caption"})
            if state & ui.ACTIVATED:
                self._goto(k)
                self.on_slide = True
        ui.scroll()

    def _grid(self, cv, sx, sy, sw, sh):
        lay = self.app.layout
        n, d = lay.edit_zoom
        step = GRID * 4
        for gx in range(step, D.SW, step):
            cv.fill565(sx + gx * n // d, sy, 1, sh, 0xC618)
        for gy in range(step, D.SH, step):
            cv.fill565(sx, sy + gy * n // d, sw, 1, 0xC618)

    def _chosen(self, cv, th):
        """The chosen object's outline in its dark keyline, and its handles
        (8 x 8, white in a dark border); while writing, the caret."""
        o = self._obj()
        if o is None:
            return
        x, y, w, h = self._to_screen(self._bounds(o))
        cv.rectb(x - 2, y - 2, w + 4, h + 4, 0)
        cv.rectb(x - 1, y - 1, w + 2, h + 2, th["value"])
        cv.rectb(x, y, w, h, th["value"])
        if self.writing:
            self._caret(cv, o, th)
        elif not self.on_slide:
            return
        hs = HANDLE
        for hx, hy in self._handles((x, y, w, h)):
            cv.rect(hx - hs // 2, hy - hs // 2, hs, hs, 0)
            cv.rect(hx - hs // 2 + 1, hy - hs // 2 + 1, hs - 2, hs - 2, 7)

    def _caret(self, cv, o, th):
        app = self.app
        lay = app.layout
        n, d = lay.edit_zoom
        rend = app.renderer
        if o["type"] == "text":
            bx, by, bw = o["x"], o["y"], o["w"]
            top = by * 64
        else:
            bx, by, bw, bh = R._body_box(o)
            lines = rend.words.lines(cv, o["text"], o["font"], o["size"], bw)
            top = by * 64 + (bh * 64 - len(lines) * R.line_h(o["size"])) // 2
        lines = rend.words.lines(cv, o["text"], o["font"], o["size"], bw)
        last, lw = lines[-1]
        if o["align"] == "centre":
            dx = (bw * 64 - lw) // 2
        elif o["align"] == "right":
            dx = bw * 64 - lw
        else:
            dx = 0
        cx = lay.slide[0] + (bx * 64 + dx + lw) * n // d // 64 + 2
        cy = lay.slide[1] + (top + (len(lines) - 1) * R.line_h(o["size"])) * n // d // 64
        cv.rect(cx, cy + o["size"] * n // d // 8, 2, o["size"] * n // d, th["value"])

    def _guide(self, cv, g):
        """A snap guide across the slide: indigo dashes on a white keyline."""
        lay = self.app.layout
        n, d = lay.edit_zoom
        sx, sy, sw, sh = lay.slide
        if g[0] == "v":
            gx = sx + g[1] * n // d
            cv.fill565(gx - 1, sy, 3, sh, WHITE_565)
            for t in range(0, sh, 8):
                cv.fill565(gx, sy + t, 1, 5, GUIDE_565)
        else:
            gy = sy + g[1] * n // d
            cv.fill565(sx, gy - 1, sw, 3, WHITE_565)
            for t in range(0, sw, 8):
                cv.fill565(sx + t, gy, 5, 1, GUIDE_565)

    def _slide_foot(self):
        app = self.app
        lay = app.layout
        st = app.story
        status = "Slide %d of %d" % (app.cur + 1, len(st.slides))
        o = self._obj()
        if self.writing and o is not None:
            status += app_dot() + "writing" + app_dot() + "%d of %d letters" % (
                len(o["text"]), D.MAX_TEXT)
        elif self.say:
            status += app_dot() + self.say
        elif o is None:
            status += app_dot() + "nothing chosen"
        else:
            status += app_dot() + self._describe(o)
        n, d = lay.edit_zoom
        status += app_dot() + "shown at " + _zw(n, d)
        if self.writing:
            hints = ("back", "Done")
        else:
            hints = ("Ctrl D", "Duplicate", "Del", "Delete")
        self._foot(status, hints)

    def _describe(self, o):
        t = o["type"]
        if t == "picture":
            if o["image"] is None:
                return "a place for a picture"
            s = {0.5: "half its size", 1: "its own size", 2: "twice its size"}.get(
                o["scale"], "%d times its size" % o["scale"])
            return _shown(o["image"]) + ", " + s + (", its corner colour clear"
                                                            if o["clear"] else "")
        if t == "text":
            return "words in " + D.FONT_NAMES[D.FONTS.index(o["font"])] + " " + str(o["size"])
        return SHAPE_TITLES[D.SHAPES.index(o["shape"])].lower()

    # -- the style panel ---------------------------------------------------------------------
    #
    # Rows of 28 placed one under the next, a rule under each: the chosen
    # object's style, else the slide's.

    def _panel(self):
        app = self.app
        x, y, w, h = app.layout.panel
        self._px, self._py, self._pw = x, y, w
        self._cv = app._surf.canvas()
        o = self._obj()
        if o is None:
            self._slide_panel()
        elif o["type"] == "picture":
            self._picture_panel(o)
        elif o["type"] == "text":
            self._text_panel(o)
        elif o["type"] == "shape":
            self._shape_panel(o)
        else:
            self._head("sprite", "Sprite")
            self._appears(o)

    def _row(self, h=28, rule=True):
        x, y, w = self._px, self._py, self._pw
        self._py += h
        if rule:
            ui.at(x, y + h - 1, w, 1)
            ui.rule()
        ui.at(x, y, w, h - (1 if rule else 0))
        return (x, y, w, h)

    def _head(self, icon, title):
        self._py += 6
        self._row(34, rule=False)
        ui.icon(icon)
        ui.label(title, {"face": "title"})

    def _caption(self, text):
        self._row(22, rule=False)
        ui.label(text, {"tone": "dim", "face": "caption"})

    def _slide_panel(self):
        app = self.app
        s = self._slide_doc()
        self._head("layout", "Slide %d" % (app.cur + 1))
        bg = s["bg"]
        recent = list(self.recent)
        if isinstance(bg, int) and bg not in recent:
            recent = [bg] + recent[:3]
        cur = recent.index(bg) if bg in recent else -1
        x, y, w, h = self._row()
        ui.at(x, y, w - 14, h - 1)
        got = ui.swatches("Background", [D.COLOURS[c] for c in recent], cur)
        if got != cur and 0 <= got < len(recent):
            self._set_slide("bg", recent[got])
        ui.at(x + w - 16, y + 4, 16, h - 9)
        if ui.tool("forward", "All colours", {"id": "All colours"}):
            self.menu = "bgcolours"
        self._row()
        if ui.link("Picture", bg if isinstance(bg, str) else "None"):
            self._open_pictures("bg")
        self._caption("Transition")
        self._row(32, rule=False)
        t = D.TRANSITIONS.index(s["transition"])
        got = ui.seg("", TRANS_NAMES, t, {"id": "Transition"})
        if got != t:
            self._set_slide("transition", D.TRANSITIONS[got])

    def _appears(self, o):
        self._py += 8
        self._row(rule=False)
        cur = 0 if o["step"] == 0 else 1
        got = ui.seg("Appears", ("With slide", "On tap"), cur)
        if got != cur:
            if got == 0:
                self._set("step", 0)
            else:
                m = 0
                for p in self._slide_doc()["objects"]:
                    m = max(m, p["step"])
                self._set("step", min(D.MAX_OBJECTS, m + 1))

    def _colour_row(self, label, key, value, none=False):
        x, y, w, h = self._row()
        name = "None" if value is None else D.COLOUR_NAMES[value]
        if ui.link(label, name, {"id": label}):
            self.menu = "colours"
            self.colour_key = (key, none)
        if value is not None:
            tw = ui.text_size(name)[0]
            sx, sy = x + w - 18 - tw - 22, y + (h - 14) // 2
            th = self.app._theme.colors()
            self._cv.rect(sx, sy, 14, 14, th["edge"])
            self._cv.rect(sx + 1, sy + 1, 12, 12, value)

    def _words_rows(self, o):
        self._row()
        f = D.FONTS.index(o["font"])
        got = ui.cycle("Font", D.FONT_NAMES, f)
        if got != f:
            self._set("font", D.FONTS[got])
        x, y, w, h = self._row()
        ui.at(x, y + 2, 96, h - 5)
        text, _done = ui.field("Size", str(o["size"]), 3, {"id": "Size in pixels"})
        if text.isdigit() and text != str(o["size"]):
            v = int(text)
            if D.SIZE_MIN <= v <= D.SIZE_MAX:
                self._set("size", v)
        sizes = [v for _n, v in D.SIZES]
        cur = sizes.index(o["size"]) if o["size"] in sizes else -1
        ui.at(x + 100, y, w - 100, h - 1)
        got = ui.seg("", [n for n, _v in D.SIZES], cur, {"id": "Size steps"})
        if got != cur and got >= 0:
            self._set("size", sizes[got])
        self._colour_row("Colour", "colour", o["colour"])
        self._row()
        a = D.ALIGNS.index(o["align"])
        got = ui.seg("Align", ("Left", "Centre", "Right"), a)
        if got != a:
            self._set("align", D.ALIGNS[got])

    def _text_panel(self, o):
        self._head("text", "Text")
        self._words_rows(o)
        self._row()
        strips = ["None"] + list(D.COLOUR_NAMES)
        cur = 0 if o["strip"] is None else o["strip"] + 1
        got = ui.cycle("Strip behind", strips, cur)
        if got != cur:
            self._set("strip", None if got == 0 else got - 1)
        self._appears(o)

    def _shape_panel(self, o):
        k = D.SHAPES.index(o["shape"])
        self._head("bubble" if o["shape"] == "bubble" else "shapes", SHAPE_TITLES[k])
        self._row()
        got = ui.cycle("Shape", D.SHAPE_NAMES, k)
        if got != k:
            self._set("shape", D.SHAPES[got])
        if o["shape"] not in ("line", "arrow"):
            self._colour_row("Fill", "fill", o["fill"], True)
        self._colour_row("Line", "line", o["line"], True)
        self._row()
        wcur = D.WIDTHS.index(o["width"])
        got = ui.seg("Width", ("Thin", "Thick"), wcur)
        if got != wcur:
            self._set("width", D.WIDTHS[got])
        if o["shape"] in ("line", "arrow"):
            self._row()
            got = ui.cycle("Points", ("Right", "Left", "Up right", "Up left"), o["flip"])
            if got != o["flip"]:
                self._set("flip", got)
        if o["shape"] not in ("line", "arrow"):
            self._caption("Text")
            self._words_rows(o)
        self._appears(o)

    def _picture_panel(self, o):
        name = o["image"]
        self._head("art", "Picture" if name is None else "Picture" + app_dot() + _shown(name))
        if name is None:
            self._row()
            if ui.link("Add a picture", ""):
                self._open_pictures("add")
            return
        self._row()
        k = D.SCALES.index(o["scale"])
        words = SCALE_WORDS if ui.has_symbols() else ("1/2",) + SCALE_WORDS[1:]
        got = ui.seg("Size", words, k)
        if got != k:
            self._set("scale", D.SCALES[got])
        self._row()
        got = ui.toggle("Clear background", o["clear"])
        if got != o["clear"]:
            self._set("clear", got)
        self._appears(o)

    # -- menus and popovers -------------------------------------------------------------------

    def _menus(self):
        app = self.app
        m = self.menu
        if m is None:
            return
        lay = app.layout
        if m == "add":
            box = app._ui.box("Add")
            if box is not None:
                ui.at(*box)
            alive = ui.popover("Add menu", 236)
            dots = "…" if ui.has_symbols() else "..."
            a = ui.menu("", ["Text\tT", "Shape\tS", "Picture" + dots + "\tI",
                             "Background" + dots + "\tB"],
                        ["text", "shapes", "art", "background"])
            ui.popover()
            if not alive:
                self.menu = None
            if a >= 0:
                self.menu = None
                if a == 0:
                    self._add("text")
                elif a == 1:
                    self._add("shape")
                elif a == 2:
                    self._open_pictures("add")
                else:
                    self._open_pictures("bg")
        elif m == "layouts":
            box = app._ui.box("New slide")
            if box is not None:
                ui.at(*box)
            alive = ui.popover("Layouts", 236)
            k = D.LAYOUTS.index(self.layout_kind)
            items = [n + ("\tEnter" if i == k else "") for i, n in enumerate(D.LAYOUT_NAMES)]
            a = ui.menu("New slide", items, LAYOUT_ICONS)
            ui.popover()
            if not alive:
                self.menu = None
            if a >= 0:
                self.menu = None
                self._new_slide(len(app.story.slides) - 1 if app.page == "deck"
                                and not (app._focused or "").startswith("Slide ")
                                else app.cur, D.LAYOUTS[a])
        elif m == "more":
            alive = ui.popover("More", 260, {"wash": True})
            items = ["Front\t]", "Back\t[", "Copy\tCtrl+C", "Paste\tCtrl+V",
                     "Centre on the slide", "Snap" + ("\ton" if self.snap else "\toff"),
                     "Duplicate\tCtrl+D", "Delete\tDel"]
            a = ui.menu(self._describe(self._obj()) if self._obj() is not None else "Slide",
                        items, ["to_front", "to_back", "copy", "paste", "align_c", "check",
                                "dup", "trash"],
                        kinds=("", "", "", "", "", "", "", "danger"))
            ui.popover()
            if not alive:
                self.menu = None
            if a >= 0:
                self.menu = None
                (self._front, lambda: self._front(back=True), self._copy, self._paste,
                 self._centre, self._toggle_snap, self._duplicate, self._delete)[a]()
        elif m == "slide":
            alive = ui.popover("Slide menu", 260, {"wash": True})
            s = app.story.slides[app.cur]
            t = D.TRANSITIONS.index(s["transition"])
            a = ui.menu("Slide %d" % (app.cur + 1),
                        ["Transition\t" + TRANS_NAMES[t], "Duplicate\tCtrl+D",
                         "Move earlier\t[", "Move later\t]", "Delete\tDel"],
                        ["trans_fade", "dup", "arrow_left", "arrow_right", "trash"],
                        kinds=("", "", "", "", "danger"))
            ui.popover()
            if not alive:
                self.menu = None
            if a >= 0:
                self.menu = None
                if a == 0:
                    self._set_slide("transition", D.TRANSITIONS[(t + 1) % 3])
                    self.menu = "slide"
                elif a == 1:
                    self._deck_keys(4)
                elif a in (2, 3):
                    j = app.story.move_slide(app.cur, -1 if a == 2 else 1)
                    if j is not None:
                        app.cur = j
                        app._focus = "Slide %d" % j
                        self._edited()
                else:
                    self._delete_slide(app.cur)
        elif m in ("colours", "bgcolours"):
            alive = ui.popover("Colours", 300)
            if m == "colours":
                key, none = self.colour_key
                o = self._obj()
                cur = o.get(key) if o is not None else None
            else:
                key, none = "bg", False
                cur = self._slide_doc()["bg"]
            cur = cur if isinstance(cur, int) else -1
            a = ui.swatches("Colours", [D.COLOURS[c] for c in range(8)],
                            cur if 0 <= cur < 8 else -1, {"id": "Colours 1"})
            b = ui.swatches(" ", [D.COLOURS[c] for c in range(8, 16)],
                            cur - 8 if cur >= 8 else -1, {"id": "Colours 2"})
            pick = None
            if a != (cur if 0 <= cur < 8 else -1) and a >= 0:
                pick = a
            if b != (cur - 8 if cur >= 8 else -1) and b >= 0:
                pick = b + 8
            if none and ui.button("None", "normal", {"id": "No colour"}):
                pick = -1
            ui.popover()
            if not alive:
                self.menu = None
            if pick is not None:
                self.menu = None
                if m == "bgcolours":
                    self._set_slide("bg", pick)
                else:
                    self._set(key, None if pick < 0 else pick)
        _ = lay

    def _toggle_snap(self):
        self.snap = not self.snap
        self.app._say("Snap " + ("on" if self.snap else "off"), "check")

    # -- Add a picture ---------------------------------------------------------------------------

    def _open_pictures(self, mode):
        app = self.app
        self.stop_writing()
        self.pic_mode = mode
        self.pics = None
        app.page = "pictures"
        app._focus = None
        app._damage.all()

    def _scan_pictures(self):
        files = self.app._files
        names, err = files.list("drawings")
        out = []
        for name in names or ():
            blob, err = files.load("drawings", name)
            size = R._blob_size(blob) if blob else None
            if size is not None:
                out.append((name, size[0], size[1]))
        self.pics = out

    def _drop_thumbs(self):
        for pic in self.thumbs.values():
            R._moybuf.free(pic.rgb)
        self.thumbs = {}

    def _thumb(self, cv, name, w, h):
        pic = self.thumbs.get(name)
        if pic is None or pic.w != w or pic.h != h:
            if pic is not None:
                R._moybuf.free(pic.rgb)
            blob, err = self.app._files.load("drawings", name)
            pic = R.read_picture(cv, blob, w, h) if blob else None
            if pic is None:
                return None
            self.thumbs[name] = pic
        return pic

    def _pictures(self, cv, th):
        app = self.app
        lay = app.layout
        if self.pics is None:
            self._scan_pictures()
        x, y, w, _h = lay.row
        ui.at(x + 16, y + 10, 300, 32)
        tab = ui.tabs("Pictures", ("My Art", "Sprites"), self.pic_tab)
        if tab != self.pic_tab:
            self.pic_tab = tab
            app._damage.all()
        ui.at(*lay.row_rule)
        ui.rule()
        body = (x, y + lay.ROW + 1, w, lay.foot[1] - y - lay.ROW - 1)
        f = app._focused or ""
        pick = None
        if self.pic_tab == 1:
            ui.at(*body)
            ui.empty("sprite", "No sprites here yet",
                     ("A story takes My Art's drawings;", "sprites come later."))
        elif not self.pics:
            ui.at(*body)
            ui.empty("art", "No drawings yet", ("Draw one in Paint, then put it",
                                               "in your story from here."))
        else:
            ui.at(*body)
            ui.scroll("pictures")
            ui.grid("drawings", 152, 112, {"cols": 5, "margin": 20, "foot": 44, "top": 14})
            for name, pw, ph in self.pics:
                size = "%d × %d" % (pw, ph) if ui.has_symbols() else "%dx%d" % (pw, ph)
                cx, cy, cw, ch, state = ui.card(_shown(name), size,
                                                {"id": "Pic " + name})
                if cw:
                    fw = cw
                    fh = cw * ph // pw
                    if fh > ch:
                        fh = ch
                        fw = ch * pw // ph
                    pic = self._thumb(cv, name, fw, fh)
                    if pic is not None:
                        cv.clip(*body)
                        cv.blit565(pic.rgb, fw, fh, cx + (cw - fw) // 2, cy + (ch - fh) // 2)
                        cv.clip()
                if state & ui.ACTIVATED:
                    pick = (name, pw, ph)
            ui.grid()
            ui.end()
            ui.scroll()
        chosen = None
        if f.startswith("Pic "):
            for p in self.pics or ():
                if p[0] == f[4:]:
                    chosen = p
        ui.at(*lay.foot_rule)
        ui.rule()
        fx, fy, fw, fh = lay.foot
        status = ""
        if chosen is not None:
            where = ("the background of slide %d" if self.pic_mode == "bg"
                     else "it goes on slide %d, chosen") % (app.cur + 1)
            status = _shown(chosen[0]) + app_dot() + where
        ui.face("caption")
        lh = ui.text_size("Ag")[1]
        ui.face()
        ui.at(fx + 26, fy + (fh - lh) // 2, fw - 200, lh + 2)
        ui.label(status, {"tone": "dim", "face": "caption"})
        ui.at(fx + fw - 16 - 76, fy + (fh - 32) // 2, 76, 32)
        ui.icon("new")
        if ui.button("Add", "primary", {"id": "Add picture", "disabled": chosen is None}) \
                and chosen is not None:
            pick = chosen
        if pick is not None:
            self._place(pick)

    def _place(self, pick):
        """Copy drawing `pick` into the story and put it on the slide (or
        behind it), chosen; a layout's next empty frame is chosen after."""
        app = self.app
        st = app.story
        files = app._files
        name, pw, ph = pick
        blob, err = files.load("drawings", name)
        if not blob:
            app._say("That drawing can't be read", "warn")
            return
        used = st.images()
        if len(used) >= D.MAX_IMAGES:
            app._say("A story holds %d pictures" % D.MAX_IMAGES, "warn")
            return
        img = self._image_name(name, used)
        _v, err = files.save(KIND, st.name + "/" + img + ".moyimg", blob)
        if err is None:
            err = self._bake_half(st.name, img, blob)
        if err is not None:
            app._say("This picture can't be saved here", "warn")
            return
        self.added.append(img)
        app.images.forget(img)
        app.page = "slide"
        self._drop_thumbs()
        if self.pic_mode == "bg":
            self._set_slide("bg", img)
            app._damage.all()
            return
        o = self._obj()
        i = app.cur
        if o is not None and o["type"] == "picture" and o["image"] is None:
            scale = 0.5
            for s in D.SCALES:
                if pw * s <= o["w"] and ph * s <= o["h"]:
                    scale = s
            new = D.picture_obj(img, o["x"], o["y"], scale, False, o["step"])
            j = st.add(i, new, at=self.chosen)
        else:
            scale = 0.5 if pw > 512 or ph > 300 else 1
            new = D.picture_obj(img, (D.SW - int(pw * scale)) // 2,
                                (D.SH - int(ph * scale)) // 2, scale)
            j = st.add(i, new)
        if j is None:
            app._say("A slide holds %d things" % D.MAX_OBJECTS, "warn")
            return
        self.chosen = j
        objs = st.objects(i)
        for k in range(len(objs)):
            o = objs[(j + 1 + k) % len(objs)]
            if o["type"] == "picture" and o["image"] is None:
                self.chosen = (j + 1 + k) % len(objs)
                break
        self.on_slide = True
        self._edited()

    def _image_name(self, name, used):
        base = "".join(c for c in name.lower() if c in "abcdefghijklmnopqrstuvwxyz0123456789_")
        base = (base or "pic")[:18]
        cand, n = base, 2
        while cand in used or cand in self.added:
            cand = "%s_%d" % (base, n)
            n += 1
        return cand

    def _bake_half(self, item, img, blob):
        """The picture's half, baked so a 1 px line survives (story_render.half)."""
        files = self.app._files
        got = files.decode_image(blob)
        if got is None:
            return "unreadable"
        w, h, pix = got
        half = R.half(pix, w, h)
        hb = files.encode_image(max(1, w // 2), max(1, h // 2), half)
        _v, err = files.save(KIND, item + "/" + img + "_h.moyimg", hb)
        return err


def _shown(name):
    """A file's name as words: "fish_tank" is "Fish tank"."""
    t = name.replace("_", " ")
    return t[:1].upper() + t[1:]


def app_dot():
    return " · " if ui.has_symbols() else " - "


def D_DOT():
    return app_dot()


def _zw(n, d):
    try:
        from storybook_app import zoom_word
    except ImportError:  # pragma: no cover - host fallback
        from runtime.storybook_app import zoom_word
    return zoom_word(n, d)
