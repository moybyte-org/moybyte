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
under their app's name. Both run the section AS ITS PROCESS (`run_as`), so
what its buttons do is the app's, under the app's grants.

Every item is a preallocated record (CAP of them): showing, a state change
and a draw allocate nothing, and the bar reads `shown` and `gen`, two
attributes. The process's end sweeps its items (`moybyte.ENDS`).
"""

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    try:
        from runtime import moy_log as _L
    except ImportError:
        _L = None

ON, PAUSED, ERROR = 0, 1, 2
STATES = (ON, PAUSED, ERROR)
CAP = 4
SHELL = 1


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


class Items:
    """The bar's status items: CAP records, the ones shown in order."""

    def __init__(self):
        self.slots = [Item(self, k) for k in range(CAP)]
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

    def run_as(self, it, fn, *args):
        """`fn(*args)` as item `it`'s process (its section's draw, an action
        it takes), the shell's again after."""
        if _L is None or not it.pid:
            return fn(*args)
        was = _L.current()
        _L.current(it.pid)
        try:
            return fn(*args)
        finally:
            _L.current(was)

    def height(self, it, w, desk):
        sec = it.section
        if sec is None:
            return 0
        return int(self.run_as(it, sec.height, w, desk) or 0)

    def draw(self, it, desk):
        sec = it.section
        if sec is not None:
            self.run_as(it, sec.draw, desk)


ITEMS = Items()
