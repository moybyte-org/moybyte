"""A story: Storybook's document (docs/media/look_2026-10/APPS_RESTYLE.md,
Storybook, "The document"), its strict reader and its edits with undo.

A story is a folder item of the user files' `stories` kind: its document
`<item>/story.json` and the pictures it uses, `<item>/<image>.moyimg`
(copied in from My Art when placed; a picture at half its size reads the
half baked beside it, `<image>_h`). The document, `moystory-2`:

    {"format": "moystory-2", "title": "The Brave Dragon",
     "slides": [{"bg": 15, "transition": "fade", "objects": [...]}]}

- `title` is the story's name as its kid wrote it (at most 40 characters;
  a document without one reads as untitled).

- `bg` a colour (0-15, the console's sixteen) or a picture's name;
  `transition` none, fade or swipe.
- An object's keys, all of them and no more, by its `type`:
  text     x y w text font size colour align strip step
  shape    shape x y w h flip fill line width text font size colour align step
  picture  x y image scale clear step (and w h where `image` is null: a
           placeholder a layout leaves, which the editor alone draws)
  sprite   x y tile tw th scale step
- Coordinates are the 1024 x 600 slide's. `font` is sans, sans_bold or
  mono, `size` its em in slide pixels (8 to 300); `align` left, centre or
  right; `strip` a colour behind the words or null; a shape is rect,
  round, ellipse, line, arrow, star or bubble, `flip` 0-3 (bit 1 across,
  bit 2 up and down), `fill` and `line` a colour or null, `width` 4 or 8; a
  picture's `scale` one of 0.5, 1, 2, 3, 4; `step` 0 appears with its
  slide, 1, 2, ... on taps in that order.
- Limits: 16 slides, 12 objects a slide, 200 characters a text, 24 images
  a story, an image at most 1024 x 600.

`read` takes this format alone: anything else -- an old story cart's
`moydeck-v1`, a key too many, a value out of its range -- reads as absent
(None), by the repo's rule (no store migrations before there are users).

`Story` holds an open story and its edits. Undo keeps what an edit
changed, as the document's own values: a slide's index and its objects
(with its background and transition), or the whole list of slides for an
edit of the list; objects carry no ids, so z is the list's order.
"""

import json

FORMAT = "moystory-2"
STORIES = "stories"             # the user files' kind a story is an item of
DOC_NAME = "story.json"
SW, SH = 1024, 600
MAX_SLIDES = 16
MAX_OBJECTS = 12
MAX_TEXT = 200
MAX_IMAGES = 24
MAX_TITLE = 40
IMAGE_W, IMAGE_H = 1024, 600
UNDO_MAX = 32

FONTS = ("sans", "sans_bold", "mono")
FONT_NAMES = ("Sans", "Sans bold", "Mono")
ALIGNS = ("left", "centre", "right")
SHAPES = ("rect", "round", "ellipse", "line", "arrow", "star", "bubble")
SHAPE_NAMES = ("Rectangle", "Rounded", "Ellipse", "Line", "Arrow", "Star", "Bubble")
TRANSITIONS = ("none", "fade", "swipe")
SCALES = (0.5, 1, 2, 3, 4)
WIDTHS = (4, 8)
SIZES = (("S", 32), ("M", 48), ("L", 64), ("XL", 96))
SIZE_MIN, SIZE_MAX = 8, 300
LAYOUTS = ("title", "picture", "two", "blank")
LAYOUT_NAMES = ("Title", "Picture", "Two pictures", "Blank")

# The console's sixteen colours (MOY64's first, runtime/palette.py), by name.
COLOURS = (0x000000, 0x1D2B53, 0x7E2553, 0x008751, 0xAB5236, 0x5F574F, 0xC2C3C7,
           0xFFF1E8, 0xFF004D, 0xFFA300, 0xFFEC27, 0x00E436, 0x29ADFF, 0x83769C,
           0xFF77A8, 0xFFCCAA)
COLOUR_NAMES = ("Black", "Dark blue", "Plum", "Dark green", "Brown", "Dark grey",
                "Light grey", "White", "Red", "Orange", "Yellow", "Green", "Blue",
                "Lavender", "Pink", "Peach")

_KEYS = {
    "text": ("type", "x", "y", "w", "text", "font", "size", "colour", "align", "strip",
             "step"),
    "shape": ("type", "shape", "x", "y", "w", "h", "flip", "fill", "line", "width", "text",
              "font", "size", "colour", "align", "step"),
    "picture": ("type", "x", "y", "image", "scale", "clear", "step"),
    "sprite": ("type", "x", "y", "tile", "tw", "th", "scale", "step"),
}
_SPAN = 4 * SW              # how far off the slide a coordinate may lie
_NAME_OK = "abcdefghijklmnopqrstuvwxyz0123456789_"


# -- the reader -------------------------------------------------------------------

def _int(v, lo, hi):
    return type(v) is int and lo <= v <= hi


def _colour(v, none=False):
    return (none and v is None) or _int(v, 0, 15)


def image_name_ok(v):
    if not isinstance(v, str) or not 0 < len(v) <= 24:
        return False
    for c in v:
        if c not in _NAME_OK:
            return False
    return True


def _keys(o, want):
    if len(o) != len(want):
        return False
    for k in want:
        if k not in o:
            return False
    return True


def _words(o):
    t = o["text"]
    return (isinstance(t, str) and len(t) <= MAX_TEXT and o["font"] in FONTS
            and _int(o["size"], SIZE_MIN, SIZE_MAX) and _int(o["colour"], 0, 15)
            and o["align"] in ALIGNS)


def _object(o):
    if type(o) is not dict:
        return False
    kind = o.get("type")
    want = _KEYS.get(kind) if isinstance(kind, str) else None
    if want is None:
        return False
    if kind == "picture" and o.get("image", 1) is None:
        want = want + ("w", "h")
    if not _keys(o, want):
        return False
    if not (_int(o["x"], -_SPAN, _SPAN) and _int(o["y"], -_SPAN, _SPAN)
            and _int(o["step"], 0, MAX_OBJECTS)):
        return False
    if kind == "text":
        return _int(o["w"], 1, _SPAN) and _words(o) and _colour(o["strip"], True)
    if kind == "shape":
        return (o["shape"] in SHAPES and _int(o["w"], 0, _SPAN) and _int(o["h"], 0, _SPAN)
                and _int(o["flip"], 0, 3) and _colour(o["fill"], True)
                and _colour(o["line"], True) and o["width"] in WIDTHS
                and type(o["width"]) is int and _words(o))
    if kind == "picture":
        if o["image"] is None:
            ok = _int(o["w"], 1, _SPAN) and _int(o["h"], 1, _SPAN)
        else:
            ok = image_name_ok(o["image"])
        s = o["scale"]
        return (ok and type(o["clear"]) is bool and type(s) in (int, float)
                and type(s) is not bool and s in SCALES)
    return (_int(o["tile"], 0, 255) and _int(o["tw"], 1, 16) and _int(o["th"], 1, 16)
            and _int(o["scale"], 1, 16))


def _slide(s):
    if type(s) is not dict or not _keys(s, ("bg", "transition", "objects")):
        return False
    bg = s["bg"]
    if not (_colour(bg) or image_name_ok(bg)):
        return False
    if s["transition"] not in TRANSITIONS:
        return False
    objs = s["objects"]
    if type(objs) is not list or len(objs) > MAX_OBJECTS:
        return False
    for o in objs:
        if not _object(o):
            return False
    return True


def images_of(doc):
    """The picture names a story's slides use, each once, in order."""
    out = []
    for s in doc["slides"]:
        if isinstance(s["bg"], str) and s["bg"] not in out:
            out.append(s["bg"])
        for o in s["objects"]:
            if o["type"] == "picture" and o["image"] is not None and o["image"] not in out:
                out.append(o["image"])
    return out


def read(text):
    """The story document in `text`, or None for anything that is not one."""
    try:
        doc = json.loads(text)
    except Exception:  # noqa: BLE001 -- not JSON: not a story
        return None
    if type(doc) is not dict or not (_keys(doc, ("format", "slides"))
                                     or _keys(doc, ("format", "title", "slides"))):
        return None
    if doc["format"] != FORMAT:
        return None
    t = doc.get("title", "")
    if not isinstance(t, str) or len(t) > MAX_TITLE:
        return None
    doc["title"] = t
    slides = doc["slides"]
    if type(slides) is not list or not 1 <= len(slides) <= MAX_SLIDES:
        return None
    for s in slides:
        if not _slide(s):
            return None
    if len(images_of(doc)) > MAX_IMAGES:
        return None
    return doc


def write(doc):
    return json.dumps(doc)


# -- making objects and slides ----------------------------------------------------

def text_obj(t, x, y, w, size, colour, font="sans_bold", align="centre", strip=None,
             step=0):
    return {"type": "text", "x": x, "y": y, "w": w, "text": t, "font": font, "size": size,
            "colour": colour, "align": align, "strip": strip, "step": step}


def shape_obj(shape, x, y, w, h, fill, line=None, width=4, text="", size=48, colour=0,
              font="sans_bold", align="centre", flip=0, step=0):
    return {"type": "shape", "shape": shape, "x": x, "y": y, "w": w, "h": h, "flip": flip,
            "fill": fill, "line": line, "width": width, "text": text, "font": font,
            "size": size, "colour": colour, "align": align, "step": step}


def picture_obj(image, x, y, scale=1, clear=False, step=0):
    return {"type": "picture", "x": x, "y": y, "image": image, "scale": scale,
            "clear": clear, "step": step}


def placeholder(x, y, w, h):
    o = picture_obj(None, x, y)
    o["w"] = w
    o["h"] = h
    return o


def ink_on(bg):
    """The words' colour that reads on background colour `bg`: white on the
    dark ones, dark blue on the light."""
    if not isinstance(bg, int):
        return 7
    rgb = COLOURS[bg]
    lum = ((rgb >> 16) * 3 + ((rgb >> 8) & 255) * 6 + (rgb & 255)) // 10
    return 1 if lum > 140 else 7


def layout_slide(kind, bg=1):
    """A new slide of layout `kind` on background `bg`."""
    ink = ink_on(bg)
    objs = []
    if kind == "title":
        objs = [text_obj("", 64, 196, 896, 96, ink),
                text_obj("", 64, 350, 896, 48, ink, font="sans")]
    elif kind == "picture":
        objs = [placeholder(64, 64, 512, 300), text_obj("", 64, 416, 896, 72, ink)]
    elif kind == "two":
        objs = [placeholder(0, 40, 512, 300), placeholder(512, 40, 512, 300),
                text_obj("", 64, 416, 896, 72, ink)]
    return {"bg": bg, "transition": "none", "objects": objs}


def new_doc(title=""):
    """A new story: one Title slide, its title `title`."""
    s = layout_slide("title", 1)
    s["objects"][0]["text"] = title
    return {"format": FORMAT, "title": title, "slides": [s]}


def copy(v):
    """A deep copy of a document value (dicts, lists and scalars)."""
    if type(v) is dict:
        return {k: copy(x) for k, x in v.items()}
    if type(v) is list:
        return [copy(x) for x in v]
    return v


def bounds(o, size_of=None):
    """(x, y, w, h) an object covers on the slide. A picture's is its
    source's size at its scale (`size_of(image)` -> (w, h)); a text box's
    height is the caller's (`o["_h"]` where the renderer measured it)."""
    t = o["type"]
    if t == "shape":
        return (o["x"], o["y"], o["w"], o["h"])
    if t == "text":
        return (o["x"], o["y"], o["w"], o.get("_h") or o["size"] * 14 // 10)
    if t == "picture":
        if o["image"] is None:
            return (o["x"], o["y"], o["w"], o["h"])
        w, h = size_of(o["image"]) if size_of is not None else (0, 0)
        s = o["scale"]
        if s == 0.5:
            return (o["x"], o["y"], w // 2, h // 2)
        return (o["x"], o["y"], w * s, h * s)
    return (o["x"], o["y"], o["tw"] * 8 * o["scale"], o["th"] * 8 * o["scale"])


# -- an open story and its edits ---------------------------------------------------

class Story:
    """An open story: `doc`, its item's name, and undo and redo. Every edit
    is a method that records what it changes before it changes it, answers
    whether it did, and marks the story dirty for the app's commit."""

    def __init__(self, name, doc):
        self.name = name
        self.doc = doc
        self.undos = []
        self.redos = []
        self.dirty = False
        self.gen = 0                    # moves with every change (the renderer's caches)
        self._open = None               # the undo entry a drag holds open
        self.slides = doc["slides"]     # the document's own list, rebound with it

    def title(self):
        return self.doc["title"]

    # -- undo -----------------------------------------------------------------

    def _snap(self, i):
        if i is None:
            return ("deck", copy(self.slides))
        return ("slide", i, copy(self.slides[i]))

    def _restore(self, e):
        if e[0] == "deck":
            was = ("deck", copy(self.slides))
            self.doc["slides"] = self.slides = e[1]
            return was
        was = ("slide", e[1], copy(self.slides[e[1]]))
        self.slides[e[1]] = e[2]
        return was

    def record(self, i=None):
        """Keep what an edit of slide `i` (None: the list of slides) is about
        to change."""
        self.undos.append(self._snap(i))
        if len(self.undos) > UNDO_MAX:
            self.undos.pop(0)
        self.redos = []

    def begin(self, i):
        """Hold one undo entry open across a drag of slide `i`'s objects."""
        if self._open is None:
            self.record(i)
            self._open = i

    def end(self):
        self._open = None

    def _changed(self):
        self.dirty = True
        self.gen += 1

    def can_undo(self):
        return bool(self.undos)

    def can_redo(self):
        return bool(self.redos)

    def undo(self):
        """Undo the last edit: the slide it touched (None: the list), or False."""
        self._open = None
        if not self.undos:
            return False
        e = self.undos.pop()
        self.redos.append(self._restore(e))
        self._changed()
        return e[1] if e[0] == "slide" else None

    def redo(self):
        self._open = None
        if not self.redos:
            return False
        e = self.redos.pop()
        self.undos.append(self._restore(e))
        self._changed()
        return e[1] if e[0] == "slide" else None

    # -- the story ------------------------------------------------------------

    def set_title(self, t):
        t = t[:MAX_TITLE]
        if t == self.doc["title"]:
            return False
        self.doc["title"] = t
        self._changed()
        return True

    # -- slides ---------------------------------------------------------------

    def add_slide(self, after, kind):
        """A new slide of layout `kind` after slide `after`: its index, or
        None at the limit."""
        if len(self.slides) >= MAX_SLIDES:
            return None
        bg = self.slides[after]["bg"] if 0 <= after < len(self.slides) else 1
        if not isinstance(bg, int):
            bg = 1
        self.record()
        self.slides.insert(after + 1, layout_slide(kind, bg))
        self._changed()
        return after + 1

    def duplicate_slide(self, i):
        if len(self.slides) >= MAX_SLIDES:
            return None
        self.record()
        self.slides.insert(i + 1, copy(self.slides[i]))
        self._changed()
        return i + 1

    def delete_slide(self, i):
        """Delete slide `i` (never the last one left): the slide focus goes
        to, or None."""
        if len(self.slides) <= 1:
            return None
        self.record()
        self.slides.pop(i)
        self._changed()
        return min(i, len(self.slides) - 1)

    def move_slide(self, i, d):
        """Move slide `i` by `d` places: its new index, or None."""
        j = i + d
        if not 0 <= j < len(self.slides) or d == 0:
            return None
        self.record()
        s = self.slides.pop(i)
        self.slides.insert(j, s)
        self._changed()
        return j

    def set_slide(self, i, key, value):
        """A slide's `bg` or `transition`."""
        if self.slides[i][key] == value:
            return False
        self.record(i)
        self.slides[i][key] = value
        self._changed()
        return True

    # -- objects --------------------------------------------------------------

    def objects(self, i):
        return self.slides[i]["objects"]

    def add(self, i, obj, at=None):
        """Put `obj` on slide `i` (on top, or in place `at`): its index, or
        None at the limit."""
        objs = self.objects(i)
        if at is None and len(objs) >= MAX_OBJECTS:
            return None
        self.record(i)
        if at is None:
            objs.append(obj)
            at = len(objs) - 1
        else:
            objs[at] = obj
        self._changed()
        return at

    def delete(self, i, j):
        self.record(i)
        self.objects(i).pop(j)
        self._changed()

    def duplicate(self, i, j):
        objs = self.objects(i)
        if len(objs) >= MAX_OBJECTS:
            return None
        o = copy(objs[j])
        o["x"] += 16
        o["y"] += 16
        self.record(i)
        objs.append(o)
        self._changed()
        return len(objs) - 1

    def set(self, i, j, key, value, hold=False):
        """Object j's `key` to `value` (`hold`: inside a drag's one entry)."""
        o = self.objects(i)[j]
        if o.get(key) == value and type(o.get(key)) is type(value):
            return False
        if hold:
            self.begin(i)
        else:
            self.record(i)
        o[key] = value
        self._changed()
        return True

    def place(self, i, j, x, y, w=None, h=None, hold=True):
        """Object j's position (and its box's size where it has one)."""
        o = self.objects(i)[j]
        same = o["x"] == x and o["y"] == y and (w is None or o.get("w") == w) \
            and (h is None or o.get("h") == h)
        if same:
            return False
        if hold:
            self.begin(i)
        else:
            self.record(i)
        o["x"] = x
        o["y"] = y
        if w is not None:
            o["w"] = w
        if h is not None:
            o["h"] = h
        self._changed()
        return True

    def to_front(self, i, j):
        """Object j on top of the others: its new index."""
        objs = self.objects(i)
        if j == len(objs) - 1:
            return j
        self.record(i)
        objs.append(objs.pop(j))
        self._changed()
        return len(objs) - 1

    def to_back(self, i, j):
        objs = self.objects(i)
        if j == 0:
            return 0
        self.record(i)
        objs.insert(0, objs.pop(j))
        self._changed()
        return 0

    def images(self):
        return images_of(self.doc)


# -- a drawing put in a story from outside it (Paint's "Put in a story") -----------

def image_name(base, used):
    """A picture name for drawing `base` in a story using `used`: the
    drawing's own, else with _2, _3, ..."""
    b = "".join(c for c in base.lower() if c in _NAME_OK)[:18] or "pic"
    cand, n = b, 2
    while cand in used:
        cand = "%s_%d" % (b, n)
        n += 1
    return cand


def put_drawing(files, item, drawing):
    """Drawing `drawing` (My Art's, through the files role `files`) as story
    `item`'s new last slide's background, the picture copied into the story:
    (slide index, picture name, title), or (None, why, None)."""
    blob, err = files.load(STORIES, item + "/" + DOC_NAME)
    doc = read(blob) if blob and err is None else None
    if doc is None:
        return (None, "That story can't be read", None)
    if len(doc["slides"]) >= MAX_SLIDES:
        return (None, "A story holds %d slides" % MAX_SLIDES, None)
    used = images_of(doc)
    if len(used) >= MAX_IMAGES:
        return (None, "A story holds %d pictures" % MAX_IMAGES, None)
    pic, err = files.load("drawings", drawing)
    if not pic or err is not None:
        return (None, "That drawing can't be read", None)
    name = image_name(drawing, used)
    _v, err = files.save(STORIES, item + "/" + name + ".moyimg", pic)
    if err is None:
        doc["slides"].append({"bg": name, "transition": "none", "objects": []})
        _v, err = files.save(STORIES, item + "/" + DOC_NAME, write(doc))
    if err is not None:
        return (None, "The story can't be saved here", None)
    return (len(doc["slides"]) - 1, name, doc["title"])


def take_back(files, item, index, name):
    """Undo put_drawing: slide `index` goes (while it is still that drawing's
    slide alone) and its picture with it. Whether it went."""
    blob, err = files.load(STORIES, item + "/" + DOC_NAME)
    doc = read(blob) if blob and err is None else None
    if doc is None or index >= len(doc["slides"]) or len(doc["slides"]) < 2:
        return False
    if doc["slides"][index] != {"bg": name, "transition": "none", "objects": []}:
        return False
    doc["slides"].pop(index)
    _v, err = files.save(STORIES, item + "/" + DOC_NAME, write(doc))
    if err is not None:
        return False
    if name not in images_of(doc):
        files.delete(STORIES, item + "/" + name + ".moyimg")
    return True
