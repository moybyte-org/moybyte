"""App status items (docs/theme_and_shell_2026-11.md §8.6): what an app puts
in the bar with `moybyte.status`, the menu-extra model.

  item = moybyte.status(icon, section, order=0)
                    a status item of the calling process, None where the
                    bar holds CAP already: `icon` a name of the console's
                    icon set, `section` what its menu draws, `order` where it
                    stands among the others (lower first, ties by age)
  item.show(state=ON)   in the bar, its icon in the state's ink: ON the
                    accent, PAUSED the paused ink, ERROR the danger ink
  item.changed()    what the section draws moved: the bar and an open menu
                    draw it again
  item.remove()     out of the bar, its menu closed

A SECTION is the app's part of the shell's surfaces, drawn with the public
UI verbs (`docs/ui_api_2026-10.md`) inside a frame the shell began:

  section.height(w, desk)  the height its rows take at width `w`, pixels
  section.draw(desk)       its rows, stacked in the flow the shell opened
                    (on the desk the menu's card, on a handheld its card in
                    the control center); a button it answers True for acts
                    there and then. The control center lays every app's
                    section in one page, so a row's id is its app's own
                    (`o.id`, prefixed with the app's name), never a bare
                    label another app's section may share

On the desk the item is a cell in the bar's right zone and its section the
menu the cell opens (a popover hung from it); on a handheld the items' icons
join the status cluster, and their sections are cards in the control center
under their app's name. Both run the section AS ITS PROCESS (`Items.draw`), so
what its buttons do is the app's, under the app's grants.

Every item is a preallocated record (CAP of them): showing, a state change
and a draw allocate nothing, and the bar reads `shown` and `gen`, two
attributes. The process's end sweeps its items (`moybyte.ENDS`).

An item with no section has no menu: its tap opens its app (on the desk
the cell's tap, on a handheld its card's one Open row).

A COMPILED program (a wasm app, a WASI program, an ELF module) never runs
Python in the shell, so its item's section is the shell's: a `Panel` of
the rows the program describes (`moybyte.status_panel`, served by
`runtime/shell/shell_servers.py`'s ConsoleCalls), drawn with the same
verbs. Its handle is the slot's number from 1. A description is lines of
text, one row each, at most ROWS_MAX:

  text <label>[TAB<value>]               a row that shows, no stop
  button <id> <label>                    a row that acts
  toggle <id> <label> <0|1>              a two-state row
  slider <id> <label> <min> <max> <v>    a value from min to max

An id is the program's own word (at most ID_MAX bytes); a label runs to
the numbers that end its line (at most LABEL_MAX characters). A press, a
toggle's change or a slider's step posts the item's process the system's
message STATUS, [item, id, value] (a button's value 1), and the row keeps
the value it moved to until the program describes the panel again. An
empty description is no panel: the item opens its app.
"""

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    try:
        from runtime import moy_log as _L
    except ImportError:
        _L = None
try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # host: the runtime package
    try:
        from runtime import ui as _ui
        from runtime import ui_draw as _ui_draw
    except ImportError:  # pragma: no cover - a headless image: nothing draws
        _ui = _ui_draw = None

ON, PAUSED, ERROR = 0, 1, 2
STATES = (ON, PAUSED, ERROR)
CAP = 4
SHELL = 1

STATUS = "moybyte.status"       # the message a panel's press posts its process
TEXT, BUTTON, TOGGLE, SLIDER = 0, 1, 2, 3
_KINDS = ("text", "button", "toggle", "slider")
_NUMS = (0, 0, 1, 3)            # the numbers that end a kind's line
ROWS_MAX = 8
ID_MAX = 16
LABEL_MAX = 40
ICON_MAX = 24


class Item:
    """One status item: its process, icon, section, order and state."""

    def __init__(self, owner, k):
        self._owner = owner
        self.k = k
        self.pid = 0
        self.icon = None
        self.section = None
        self.order = 0
        self.age = 0
        self.state = ON
        self.visible = False
        self.app = ""               # the process's title, its caption

    def show(self, state=ON):
        if self.pid == 0:
            return
        state = state if state in STATES else ON
        if self.visible and self.state == state:
            return
        moved = not self.visible
        self.visible = True
        self.state = state
        self._owner._moved(moved)

    def changed(self):
        if self.pid:
            self._owner._moved(False)

    def remove(self):
        if self.pid:
            self._owner._free(self)


def _row_h(desk):
    """A stacked row's height and the gap after it, at the frame's scale."""
    m = _ui_draw.metrics("row")
    if desk:
        return m[6] + m[7]
    cv = _ui_draw.UI_TARGET[1]
    fs = getattr(cv, "font_scale", 1) or 1
    return (m[5] + m[7]) * fs


def parse(text):
    """Description `text` as rows (kind, id, label, value, v, lo, hi), or
    None where a line is not one of the four kinds' or there are more than
    ROWS_MAX."""
    out = []
    if not text:
        return out
    for line in text.split("\n"):
        if not line.strip():
            continue
        if len(out) == ROWS_MAX:
            return None
        head = line.split(" ", 1)
        kind = _KINDS.index(head[0]) if head[0] in _KINDS else -1
        if kind < 0 or len(head) < 2:
            return None
        rest = head[1]
        rid = ""
        if kind != TEXT:
            sp = rest.split(" ", 1)
            if len(sp) < 2 or not sp[0] or len(sp[0]) > ID_MAX:
                return None
            rid, rest = sp[0], sp[1]
        nums = []
        n = _NUMS[kind]
        if n:
            sp = rest.rsplit(" ", n)
            if len(sp) != n + 1:
                return None
            try:
                nums = [int(t) for t in sp[1:]]
            except ValueError:
                return None
            rest = sp[0]
        value = ""
        if kind == TEXT and "\t" in rest:
            rest, value = rest.split("\t", 1)
            value = value.strip()[:LABEL_MAX]
        label = rest.strip()[:LABEL_MAX]
        if not label:
            return None
        v, lo, hi = 0, 0, 0
        if kind == BUTTON:
            v = 1
        elif kind == TOGGLE:
            if nums[0] not in (0, 1):
                return None
            v = nums[0]
        elif kind == SLIDER:
            lo, hi, v = nums
            if lo >= hi:
                return None
            v = lo if v < lo else hi if v > hi else v
        out.append((kind, rid, label, value, v, lo, hi))
    return out


class Panel:
    """A compiled program's item's section: the rows it described, drawn
    with the public UI verbs as its process. One a slot, its rows' records
    preallocated: a draw allocates nothing, a press its message."""

    def __init__(self, owner, k):
        self._owner = owner
        self.k = k
        self.n = 0
        self.kind = [TEXT] * ROWS_MAX
        self.rid = [""] * ROWS_MAX
        self.label = [""] * ROWS_MAX
        self.value = [""] * ROWS_MAX
        self.v = [0] * ROWS_MAX
        self.lo = [0] * ROWS_MAX
        self.hi = [0] * ROWS_MAX
        # each row's options: an id no other section's row shares
        self.o = [{"id": "status%d.%d" % (k, r)} for r in range(ROWS_MAX)]

    def set(self, rows):
        n = len(rows)
        for r in range(n):
            (self.kind[r], self.rid[r], self.label[r], self.value[r], self.v[r],
             self.lo[r], self.hi[r]) = rows[r]
        for r in range(n, ROWS_MAX):
            self.rid[r] = self.label[r] = self.value[r] = ""
        self.n = n

    def height(self, w, desk):
        return self.n * _row_h(desk)

    def draw(self, desk):
        kind = self.kind
        for r in range(self.n):
            k = kind[r]
            if k == TEXT:
                _ui.info(self.label[r], self.value[r])
            elif k == BUTTON:
                if _ui.link(self.label[r], "", self.o[r]):
                    self._press(r, 1)
            elif k == TOGGLE:
                cur = self.v[r]
                got = 1 if _ui.toggle(self.label[r], cur == 1, self.o[r]) else 0
                if got != cur:
                    self._press(r, got)
            else:
                cur = self.v[r]
                got = _ui.slider(self.label[r], cur, self.lo[r], self.hi[r], self.o[r])
                if got != cur:
                    self._press(r, got)

    def _press(self, r, v):
        """Row `r` moved to `v`: the item's process hears it."""
        self.v[r] = v
        it = self._owner.slots[self.k]
        post = self._owner.post
        if post is not None and it.pid:
            post(it.pid, STATUS, (self.k + 1, self.rid[r], v))


class Items:
    """The bar's status items: CAP records, the ones shown in order."""

    def __init__(self):
        self.slots = [Item(self, k) for k in range(CAP)]
        self.panels = [Panel(self, k) for k in range(CAP)]
        self._open = [{"id": "status%d.open" % k} for k in range(CAP)]
        self.opening = None         # the item whose Open row was pressed
        # fn(pid, name, args) the moybyte module sets: a message posted to a
        # process from the system (moybyte.WAKES.post)
        self.post = None
        self.shown = []             # the visible items, by order then age
        self.gen = 0                # moves with anything an item shows
        self.layout_gen = 0         # ...and with how many are shown
        self._age = 0
        # fn(item) the console sets: an item's menu closes with it
        self.closed = None
        # fn(pid) -> the process's title, the console's
        self.title_of = None

    def add(self, pid, icon, section, order=0):
        for it in self.slots:
            if it.pid == 0:
                self._age += 1
                it.pid = pid
                it.icon = icon
                it.section = section
                it.order = order
                it.age = self._age
                it.state = ON
                it.visible = False
                t = self.title_of
                it.app = (t(pid) if t is not None else "") or ""
                return it
        return None

    def _order(self):
        out = self.shown
        del out[:]
        for it in self.slots:
            if it.pid and it.visible:
                j = len(out)
                while j > 0 and (out[j - 1].order, out[j - 1].age) > (it.order, it.age):
                    j -= 1
                out.insert(j, it)

    def _moved(self, count):
        self.gen = (self.gen + 1) & 0x3fffffff
        if count:
            self._order()
            self.layout_gen = (self.layout_gen + 1) & 0x3fffffff

    def _free(self, it):
        was = it.visible
        cb = self.closed
        if cb is not None:
            cb(it)
        it.pid = 0
        it.icon = None
        it.section = None
        it.visible = False
        self._moved(was)

    def sweep(self, pid):
        """Process `pid` ended: its items leave the bar."""
        for it in self.slots:
            if it.pid and it.pid == pid:
                self._free(it)

    def count(self):
        return len(self.shown)

    def at(self, i):
        sh = self.shown
        return sh[i] if 0 <= i < len(sh) else None

    def bare(self, it):
        """Whether item `it` has no menu: a section-less item, or a compiled
        program's with no panel described. Its tap opens its app."""
        sec = it.section
        return sec is None or (type(sec) is Panel and sec.n == 0)

    def height(self, it, w, desk):
        if self.bare(it):
            return 0 if desk else _row_h(False)
        sec = it.section
        if _L is None or not it.pid:
            return int(sec.height(w, desk) or 0)
        was = _L.current()
        _L.current(it.pid)
        try:
            return int(sec.height(w, desk) or 0)
        finally:
            _L.current(was)

    def draw(self, it, desk):
        """Item `it`'s section, as its process; a bare item's one row, Open
        (`opening` names it the frame it is pressed)."""
        if self.bare(it):
            if _ui.link("Open", "", self._open[it.k]):
                self.opening = it
            return
        sec = it.section
        if _L is None or not it.pid:
            sec.draw(desk)
            return
        was = _L.current()
        _L.current(it.pid)
        try:
            sec.draw(desk)
        finally:
            _L.current(was)

    # -- a compiled program's items (ConsoleCalls' status rows) --------------

    def of(self, pid, h):
        """Process `pid`'s compiled item of handle `h`, or None."""
        if type(h) is not int or not 1 <= h <= CAP:
            return None
        it = self.slots[h - 1]
        if it.pid != pid or it.section is not self.panels[h - 1]:
            return None
        return it

    def show_compiled(self, pid, icon, state, order):
        """A compiled program's item shown in `state`, no panel yet: its
        handle, 0 where the bar holds CAP already."""
        for it in self.slots:
            if it.pid == 0:
                p = self.panels[it.k]
                p.set(())
                self.add(pid, icon, p, order).show(state)
                return it.k + 1
        return 0


ITEMS = Items()
