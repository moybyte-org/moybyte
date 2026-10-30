# Map (grep -n a name to jump there):
#   CONTENTS        what a pane can show: its title, icon, Editor tab and minimum
#   WORKSPACES      the five workspaces the app's row switches between
#   tree            a workspace's starting arrangement at a panes-area size
#   Arrangement     the dividers of one workspace: place, drag, reset, remember
"""The desk Editor's workspaces (docs/media/look_2026-10/EDITOR_LAYOUT.md,
concept B, and MAXIMISE_VARIANTS.md's Resize, R-01): pure geometry, no
drawing.

A workspace is a tree of splits whose leaves are panes. A split lays its
children along one axis ("h": left to right, "v": top to bottom), 6 px
gutters between them; each child but one starts at a fixed size (the
pictures' pane sizes at 1024 x 600 and 800 x 480) and the one marked None
takes what is left, so a bigger window gives the scrolling pane (Code, the
Reference, Art's Map) the difference and the canvas and the game keep
their zoom. A divider moves to any pixel and stops at its neighbour's
minimum; the minimums are the T-Deck's (docs/studio_2026-09.md §4.2): the
game at 1x under its header, a canvas the T-Deck's page under its bar plus
the header. A pane that scrolls has the floor that keeps its header.

A pane is a tuple of content keys (`CONTENTS`): one is a pane with a title,
several a tabbed pane. Sizes are in glass pixels at chrome size 1 and are
multiplied by `unit` (the desk's chrome size over the render scale)."""

GUTTER = 6                  # between panes and round the panes area
HEADER = 32                 # a pane's header row
EDGE = 3                    # the focused pane's `value` edge along its top
RADIUS = 8                  # a pane's card
HANDLE = (6, 36)            # a divider's handle, drawn
HANDLE_HIT = (16, 48)       # ...and the touch it takes
DRAG_SLOP = 8               # a touch drag's travel before it takes
KEY_STEP = 8                # Ctrl+Alt+arrow moves a divider this far

GAME_MIN = (320, 240 + HEADER)          # the game at 1x under its header
CANVAS_MIN = (320, 222 + HEADER)        # the T-Deck's page under its bar
FLOOR = (64, HEADER + 16)               # a scrolling pane keeps its header

# content -> (label, icon, Editor tab or None, minimum)
CONTENTS = {
    "cards": ("Settings", "sliders", "cards", FLOOR),
    "code": ("Code", "code", "code", FLOOR),
    "blocks": ("Blocks", "blocks", "blocks", FLOOR),
    "paint": ("Sprite", "sprite", "paint", CANVAS_MIN),
    "map": ("Map", "map", "map", CANVAS_MIN),
    "scene": ("Scene", "scene", "scene", CANVAS_MIN),
    "music": ("Sound", "music", "music", CANVAS_MIN),
    "game": ("Game", "game", None, GAME_MIN),
    "sheet": ("Sheet", "sprite", None, FLOOR),
    "sprites": ("Sprites", "sprite", None, FLOOR),
    "sounds": ("Sounds", "music", None, FLOOR),
    "maps": ("Map", "map", None, FLOOR),
    "log": ("Log", "doc", None, FLOOR),
    "song": ("Song", "music", None, FLOOR),
    "actor": ("Actor", "scene", None, FLOOR),
    "stamps": ("Stamps", "stamp", None, FLOOR),
    "palette": ("Block palette", "blocks", None, FLOOR),
    "made": ("Code", "lock", None, FLOOR),
    # the Editor's theme mode (runtime/theme_editor.py): its two tabs
    "theme_config": ("Config", "sliders", "theme_config", FLOOR),
    "theme_sprites": ("Sprites", "sprite", "theme_sprites", (420, 330 + HEADER)),
}

# (key, label, icon) in the app's row's order; Ctrl+1 ... 5 is this order.
WORKSPACES = (
    ("settings", "Settings", "sliders"),
    ("code", "Code", "code"),
    ("art", "Art", "sprite"),
    ("world", "World", "scene"),
    ("sound", "Sound", "music"),
)
KEYS = tuple(k for k, _l, _i in WORKSPACES)
# The theme mode's one workspace: Config beside Sprites. Not on the app's row,
# which shows the two tabs instead.
THEME = "theme"

# The Editor tab each workspace stands for where one tab is asked of it
# (the handheld ladder's, the history's): its first pane's.
HOME_TAB = {"settings": "cards", "code": "code", "art": "paint",
            "world": "scene", "sound": "music"}


def wide(w):
    """Whether a panes area `w` wide takes the 1024 arrangement (a Run slot
    over a context pane); narrower, one tabbed pane at the right."""
    return w >= 960


def tree(key, w, h, unit=1, blocks=False):
    """Workspace `key`'s starting arrangement for a panes area `w` x `h`:
    a split ("h" | "v", children, starting sizes) whose children are splits
    or panes, one starting size None (it takes the rest). A blocks cart's
    Code workspace is the Blocks one."""
    u = unit
    big = wide(w)
    rw = (660 if w >= 1240 * u and h >= 760 * u else 340) * u     # the right column
    rh = (512 if rw == 660 * u else 282) * u                      # its Run slot
    if key == THEME:
        return ("h", (("theme_config",), ("theme_sprites",)), ((352 if big else 260) * u, None))
    if key == "settings":
        return ("h", (("cards",), ("game",)), ((360 if big else 300) * u, None))
    if key == "code":
        if blocks:
            if big:
                return ("h", (("palette",), ("blocks",),
                              ("v", (("scene", "game"), ("made",)), (rh, None))),
                        (200 * u, None, rw))
            return ("h", (("blocks",), ("scene", "game", "palette", "made")), (None, rw))
        if big:
            return ("h", (("code",), ("v", (("game",), ("sprites", "sounds", "maps", "log")),
                                       (rh, None))), (None, rw))
        return ("h", (("code",), ("game", "sprites", "sounds", "maps", "log")), (None, rw))
    if key == "art":
        if big:
            return ("h", (("paint",), ("v", (("map", "game"), ("sheet",)), (None, 162 * u))),
                    (400 * u, None))
        return ("h", (("paint",), ("map", "game")), ((330 if h >= 420 * u else 320) * u, None))
    if key == "world":
        if big:
            return ("h", (("scene",), ("v", (("game",), ("actor", "stamps")), (rh, None))),
                    (None, rw))
        return ("h", (("scene",), ("actor", "stamps", "game")), (None, rw))
    if key == "sound":
        if big:
            return ("h", (("sounds",), ("music",), ("v", (("game",), ("song",)), (rh, None))),
                    (200 * u, None, rw))
        return ("h", (("music",), ("game", "song")), (None, rw))
    raise ValueError("workspace: " + str(key))


def is_pane(node):
    return node[0] not in ("h", "v")


def pane_min(pane, unit=1):
    """The least (w, h) a pane may take: the largest of its tabs'."""
    w = h = 0
    for c in pane:
        mw, mh = CONTENTS[c][3]
        w = max(w, mw * unit)
        h = max(h, mh * unit)
    return w, h


def node_min(node, unit=1):
    """The least (w, h) a node may take, its gutters included."""
    if is_pane(node):
        return pane_min(node, unit)
    axis, kids, _start = node
    g = GUTTER * unit
    ws = [node_min(k, unit) for k in kids]
    if axis == "h":
        return (sum(m[0] for m in ws) + g * (len(ws) - 1), max(m[1] for m in ws))
    return (max(m[0] for m in ws), sum(m[1] for m in ws) + g * (len(ws) - 1))


def _fit(start, mins, total):
    """The children's sizes along a split `total` long (gutters taken off):
    each fixed child at its starting size, at least its minimum, and the
    flexible one the rest; where the rest falls under its minimum the fixed
    children give it room, last first, down to their own."""
    sizes = [0 if s is None else max(s, m) for s, m in zip(start, mins)]
    flex = start.index(None) if None in start else len(start) - 1
    rest = total - sum(sizes[i] for i in range(len(sizes)) if i != flex)
    need = mins[flex] - rest
    for i in range(len(sizes) - 1, -1, -1):
        if need <= 0:
            break
        if i == flex:
            continue
        give = min(need, sizes[i] - mins[i])
        if give > 0:
            sizes[i] -= give
            rest += give
            need -= give
    sizes[flex] = max(0, rest)
    return sizes


class Arrangement:
    """One workspace's panes at one panes-area size: the starting tree and
    the sizes the dividers hold, per split (keyed by its path, the child
    indices from the root). `place` lays it out; `drag` moves a divider;
    `reset` puts one (or all) back; `state`/`restore` are what the board
    remembers (per project, per workspace and per screen size, never in the
    cart)."""

    def __init__(self, key, area, unit=1, blocks=False):
        self.key = key
        self.area = tuple(area)
        self.unit = unit
        self.root = tree(key, area[2], area[3], unit, blocks)
        self.sizes = {}
        self._start = {}
        self._min = None
        self._fill(self.root, (), area[2], area[3])

    def _fill(self, node, path, w, h):
        if is_pane(node):
            return
        axis, kids, start = node
        g = GUTTER * self.unit
        total = (w if axis == "h" else h) - g * (len(kids) - 1)
        mins = [node_min(k, self.unit)[0 if axis == "h" else 1] for k in kids]
        sizes = _fit(list(start), mins, total)
        self.sizes[path] = sizes
        self._start[path] = tuple(sizes)
        for i, k in enumerate(kids):
            if axis == "h":
                self._fill(k, path + (i,), sizes[i], h)
            else:
                self._fill(k, path + (i,), w, sizes[i])

    def node(self, path):
        n = self.root
        for i in path:
            n = n[1][i]
        return n

    # -- layout -------------------------------------------------------------

    def place(self):
        """(panes, dividers): each pane as (pane, rect, path) in reading
        order, each divider as (path, index, axis, rect) -- the gutter
        between child `index` and the next of the split at `path`."""
        panes = []
        divs = []
        self._place(self.root, (), self.area, panes, divs)
        return panes, divs

    def _place(self, node, path, rect, panes, divs):
        if is_pane(node):
            panes.append((node, rect, path))
            return
        axis, kids, _start = node
        x, y, w, h = rect
        g = GUTTER * self.unit
        sizes = self.sizes[path]
        for i, k in enumerate(kids):
            s = sizes[i]
            if i == len(kids) - 1:
                s = (x + w if axis == "h" else y + h) - (x if axis == "h" else y)
            r = (x, y, s, h) if axis == "h" else (x, y, w, s)
            self._place(k, path + (i,), r, panes, divs)
            if i < len(kids) - 1:
                if axis == "h":
                    divs.append((path, i, axis, (x + s, y, g, h)))
                    x += s + g
                    w -= s + g
                else:
                    divs.append((path, i, axis, (x, y + s, w, g)))
                    y += s + g
                    h -= s + g

    def splits(self):
        """Each split as (path, axis, rect, mins): what the desk lays out as
        a `ui.split`, its children's minimums along its axis."""
        out = []
        for node, rect, path in self._rects():
            if not is_pane(node):
                k = 0 if node[0] == "h" else 1
                out.append((path, node[0], rect, [node_min(c, self.unit)[k] for c in node[1]]))
        return out

    def minimum(self):
        """The least panes area this workspace holds, (w, h): where a
        restored window stops shrinking."""
        m = self._min
        if m is None:
            m = self._min = node_min(self.root, self.unit)
        return m

    # -- the dividers ---------------------------------------------------------

    def limits(self, path, i):
        """(lo, hi) child `i`'s size may take: the pair's sum less the next
        one's minimum, and its own minimum."""
        node = self.node(path)
        axis = node[0]
        k = 0 if axis == "h" else 1
        sizes = self.sizes[path]
        lo = node_min(node[1][i], self.unit)[k]
        hi = sizes[i] + sizes[i + 1] - node_min(node[1][i + 1], self.unit)[k]
        return lo, max(lo, hi)

    def drag(self, path, i, size):
        """Move the divider after child `i` of split `path` so the child is
        `size` long: the two beside it, and only they, change. The side it
        stopped at, -1 none, 0 child `i` at its minimum, 1 the next."""
        sizes = self.sizes[path]
        lo, hi = self.limits(path, i)
        pair = sizes[i] + sizes[i + 1]
        stop = -1
        if size <= lo:
            size, stop = lo, 0
        elif size >= hi:
            size, stop = hi, 1
        old = sizes[i]
        sizes[i] = size
        sizes[i + 1] = pair - size
        if size != old:
            self._refit(path, i)
            self._refit(path, i + 1)
        return stop

    def _refit(self, path, i):
        """Child `i` of `path` changed size: its own splits re-fit along it,
        their fixed children kept, the flexible one taking the difference."""
        node = self.node(path)
        child = node[1][i]
        if is_pane(child):
            return
        rect = self.child_rect(path, i)
        self._refill(child, path + (i,), rect[2], rect[3])

    def _refill(self, node, path, w, h):
        if is_pane(node):
            return
        axis, kids, start = node
        g = GUTTER * self.unit
        total = (w if axis == "h" else h) - g * (len(kids) - 1)
        mins = [node_min(k, self.unit)[0 if axis == "h" else 1] for k in kids]
        flex = start.index(None) if None in start else len(start) - 1
        held = [None if j == flex else s for j, s in enumerate(self.sizes[path])]
        sizes = _fit(held, mins, total)
        self.sizes[path] = sizes
        for j, k in enumerate(kids):
            if axis == "h":
                self._refill(k, path + (j,), sizes[j], h)
            else:
                self._refill(k, path + (j,), w, sizes[j])

    def child_rect(self, path, i):
        for _p, r, p in self._rects():
            if p == path + (i,):
                return r
        return None

    def _rects(self):
        out = []
        self._walk(self.root, (), self.area, out)
        return out

    def _walk(self, node, path, rect, out):
        out.append((node, rect, path))
        if is_pane(node):
            return
        axis, kids, _s = node
        x, y, w, h = rect
        g = GUTTER * self.unit
        sizes = self.sizes[path]
        for i, k in enumerate(kids):
            s = sizes[i] if i < len(kids) - 1 else ((x + w - x) if axis == "h" else (y + h - y))
            r = (x, y, s, h) if axis == "h" else (x, y, w, s)
            self._walk(k, path + (i,), r, out)
            if axis == "h":
                x += s + g
                w -= s + g
            else:
                y += s + g
                h -= s + g

    def reset(self, path=None, i=None):
        """Put the divider after child `i` of split `path` back where the
        workspace starts it (a double tap on its handle), or every divider
        (Ctrl+0)."""
        if path is None:
            for p, s in self._start.items():
                self.sizes[p] = list(s)
            return
        s = self._start[path]
        sizes = self.sizes[path]
        pair = sizes[i] + sizes[i + 1]
        sizes[i] = s[i]
        sizes[i + 1] = pair - s[i]
        self._refit(path, i)
        self._refit(path, i + 1)

    def edges(self, pane_path):
        """The dividers on a pane's right and its bottom, [(path, i, axis)]:
        what Ctrl+Alt+arrows move with that pane focused. A pane's right
        edge is the nearest split along "h" where it (or the node holding
        it) is not the last child; its bottom likewise along "v"."""
        out = []
        for axis in ("h", "v"):
            p = tuple(pane_path)
            while p:
                parent, i = p[:-1], p[-1]
                node = self.node(parent)
                if node[0] == axis and i < len(node[1]) - 1:
                    out.append((parent, i, axis))
                    break
                p = parent
        return out

    def state(self):
        """What the board keeps: [[path, sizes], ...]."""
        return [[list(p), list(s)] for p, s in sorted(self.sizes.items())]

    def restore(self, saved):
        """Take back a `state()`: each split's sizes where it still fits the
        tree, re-fitted to this area (a remembered layout of another tree
        or a hand-edited one reads as absent)."""
        if not isinstance(saved, list):
            return False
        got = {}
        for item in saved:
            if not (isinstance(item, list) and len(item) == 2):
                return False
            p, s = tuple(item[0]), item[1]
            if p not in self.sizes or not isinstance(s, list) \
                    or len(s) != len(self.sizes[p]) \
                    or not all(isinstance(v, int) and v >= 0 for v in s):
                return False
            got[p] = s
        for p, s in got.items():
            self.sizes[p] = list(s)
        self._refill(self.root, (), self.area[2], self.area[3])
        return True
