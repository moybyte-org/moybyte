"""The task manager (docs/os_2026-11/processes.md §6): every running game
and app and every background service, each row its state, its CPU and its
memory, and what can be done with it.

The rows are `Procs.rows` (runtime/shell/procs.py): the front run, the
running set, the services. CPU is the share of a core its `update`, `draw`
and callbacks took over the last two seconds (`Procs.account`); memory is
what its start allocated, approximate for Python and shown with a `~`
(`scheduler.allocated`); a figure a tier cannot measure is `-`. Under the
table, the current row's verbs: Switch to (a run not already in front),
Close (a run, a stopped service's row), Stop (a service), Details (a
crashed or suspended row: its log, `Workstation.open_log`), and on a
handheld Home.

Its second tab, Notifications, is the list of what apps posted and nobody
opened yet (runtime/system/notifications.py), newest first, each row the
app and its summary, the current one's body under the table. Its verbs:
Open (the notification's process comes to the front and is told,
`Notifications.act`), each action the app named, and Clear. The console's
key held while a banner is up opens the task manager on this tab with that
notification current, and takes the banner down.

Reached from the system menu and by holding the console's key
(`Workstation.open_tasks`); a kernel kind ("tasks"), a window on the desk
and the surface on top on a handheld, where it holds the front game while
it shows.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
    import layout_base as _lb
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _lb

try:
    from layers import Layer
    from notifications import NOTES
except ImportError:  # pragma: no cover - host fallback
    from runtime.layers import Layer
    from runtime.notifications import NOTES

TITLE = "Tasks"
COLUMNS = ("Name", "State", "CPU", "Mem")
STATES = {"running": "Running", "throttled": "Behind", "minimised": "Minimised",
          "paused": "Paused", "background": "Service", "suspended": "Suspended",
          "stopping": "Stopping", "crashed": "Crashed"}
SWITCH, CLOSE, STOP, DETAILS, HOME = "Switch to", "Close", "Stop", "Details", "Home"
OPEN, CLEAR = "Open", "Clear"
RUNNING, NOTES_TAB = 0, 1
TABS = ("Running", "Notifications")
NOTE_COLUMNS = ("From", "Notification")
NOTE_LABELS = 2                 # the actions a row offers by name, at most
_HINTS = ["confirm", SWITCH, "back", "Back"]
_ROWS = {"id": "rows"}          # the tables' id: the focus a page opens on
_NOTE_ROWS = {"id": "notes"}


def cpu_text(pm):
    """Per mille of a core as a percent, `-` where unmeasured."""
    if pm is None:
        return "-"
    return "%d%%" % ((pm + 5) // 10)


def mem_text(n):
    """Bytes as `~` and K or M (approximate), `-` where unmeasured."""
    if n is None:
        return "-"
    if n >= 1 << 20:
        return "~%d.%dM" % (n >> 20, ((n & 0xFFFFF) * 10) >> 20)
    return "~%dK" % ((n + 1023) >> 10)


def note_verbs(n):
    """The verbs notification `n` offers: Open, its named actions, Clear."""
    out = [OPEN]
    for a in n.labels()[:NOTE_LABELS]:
        out.append(a[1])
    out.append(CLEAR)
    return out


def verbs(row):
    """The verbs row `row` offers (a handheld's Home follows them)."""
    _key, _name, kind, state, _cpu, _mem, _pid = row
    out = []
    if kind == "service":
        if state == "crashed":
            out.append(DETAILS)
            out.append(CLOSE)
        else:
            if state == "suspended":
                out.append(DETAILS)
            out.append(STOP)
        return out
    if state == "crashed":
        out.append(DETAILS)
    else:
        out.append(SWITCH)
    out.append(CLOSE)
    return out


class TasksLayer(Layer):
    """The task manager's content layer, kind "tasks"."""

    id = "tasks"
    domain = "system"
    back_title = TITLE              # the bar's chevron and title (BarLayer)
    zone_gen = 0                    # ...whose cache key: the title never moves

    def __init__(self, ws):
        self.ws = ws
        self.host = _ui_draw.AppHost()
        self.cur = 0
        self.rows = []
        self.cells = []
        self._gen = -1               # the running set's gen the rows were read at
        self._rect = (0, 0, 0, 0)
        self.held = False            # it paused the front game when it opened
        self.tab = RUNNING
        self._focus = False
        self.note_cur = 0
        self.notes = []              # the list's notifications as the rows show them
        self.note_cells = []
        self._note_gen = -1

    # -- the rows -----------------------------------------------------------

    def moved(self):
        """The rows changed since they were drawn: a sample, a state, a run,
        a notification."""
        return self.ws.procs.gen != self._gen or NOTES.gen != self._note_gen

    def _read_notes(self):
        self._note_gen = NOTES.gen
        self.notes = list(NOTES.list)
        self.note_cells = [(n.app, n.summary) for n in self.notes]
        if self.note_cur >= len(self.notes):
            self.note_cur = len(self.notes) - 1 if self.notes else 0

    def _read(self):
        procs = self.ws.procs
        self._gen = procs.gen
        rows = procs.rows()
        self.rows = rows
        cells = []
        for r in rows:
            cells.append((r[1], STATES.get(r[3], r[3]), cpu_text(r[4]), mem_text(r[5])))
        self.cells = cells
        if self.cur >= len(rows):
            self.cur = len(rows) - 1 if rows else 0

    # -- the app protocol ----------------------------------------------------

    def open(self, note=None):
        """Open on the running set, or on the Notifications tab with
        notification `note` current."""
        self.host.reset()
        self.cur = 0
        self._gen = -1
        self._note_gen = -1
        self.tab = RUNNING
        self.note_cur = 0
        if note is not None:
            self.tab = NOTES_TAB
            self._read_notes()
            if note in self.notes:
                self.note_cur = self.notes.index(note)
        self._focus = True           # the first frame puts the focus on the list

    def back(self):
        self.ws.close_tasks()
        return True

    def draw_zone(self, cv, rect):
        return None

    def zone_tap(self, px, py, rect=None):
        return False

    def handle_input(self, i):
        ws = self.ws
        if i.pressed("home"):
            ws.close_tasks()
            ws.home_key()
            return True
        if i.pressed("b") or i.pressed("stop"):
            self.back()
            return True
        if self.host.keys(i):
            ws._dirty = True
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if click and ws.bar_layer.handle_bar_tap("tasks", px, py):
            return True
        if self.host.point(px, py, click, bool(getattr(ws.pointer, "down", False))):
            ws._dirty = True
        return True

    # -- draw ---------------------------------------------------------------

    def _content(self):
        """The layout's content rect; in a desk window the window's whole
        content, under its title strip (the task manager lends no bar)."""
        ws = self.ws
        if ws.windowed_chrome:
            cv = ws.sys_canvas
            x, y, w, h = 0, 0, cv.w, cv.h
        else:
            lay = ws.layout
            x, y, w, h = lay.cx, lay.cy, lay.cw, lay.ch
        r = self._rect
        if r[0] != x or r[1] != y or r[2] != w or r[3] != h:
            r = self._rect = (x, y, w, h)
        return r

    def draw(self, dt):
        ws = self.ws
        cv = ws.sys_canvas
        x, y, w, h = self._content()
        g = ws.theme_colors["panel"]
        if y > 0:
            cv.rect(0, 0, cv.w, y, g)
        if y + h < cv.h:
            cv.rect(0, y + h, cv.w, cv.h - y - h, g)
        if x > 0:
            cv.rect(0, y, x, h, g)
        if x + w < cv.w:
            cv.rect(x + w, y, cv.w - x - w, h, g)
        self._page()
        if not ws.windowed_chrome:
            ws.bar_layer._draw_status_strip("tasks")

    def _page(self):
        ws = self.ws
        if ws.procs.gen != self._gen:
            self._read()
        if NOTES.gen != self._note_gen:
            self._read_notes()
        desk = _lb.metric_class() == _lb.DESK
        hand = ws.procs.handheld()
        rect = self._content()
        self.host.begin(ws.sys_canvas, ws.theme_colors, rect, desk, ws.windowed_chrome)
        page = _ui.page("")
        notes = self.tab == NOTES_TAB
        if notes:
            n = self.notes[self.note_cur] if self.notes else None
            acts = note_verbs(n) if n is not None else []
        else:
            row = self.rows[self.cur] if self.rows else None
            acts = verbs(row) if row is not None else []
        if hand:
            acts = acts + [HOME]
        hints = _HINTS
        hints[1] = acts[0] if acts else HOME
        if not desk and _ui.hints_width(hints) > 0:
            band, page = _ui.cut_bottom(page, 20 * ws.layout.fs)
            _ui.at(*band)
            _ui.hints(hints)
        bh = _ui_draw.metrics("value_row")[17 if desk else 16] * ws.layout.fs
        pad = 8 if desk else 0
        band, page = _ui.cut_top(page, bh + 2 * pad)
        if pad:
            band = _ui.inset(band, 2 * pad, pad)
        _ui.at(*band)
        tab = _ui.tabs("Tasks", TABS, self.tab)
        if tab != self.tab:
            self.tab = tab
            ws.request_frame()          # the page follows the tab
        foot, body = _ui.cut_bottom(page, bh + 2 * pad)
        if pad:
            foot = _ui.inset(foot, 2 * pad, pad)
        if notes:
            self._notes_page(body, foot, acts)
            return
        if row is None:
            _ui.at(*body)
            _ui.empty("layout", "Nothing running", ("Games and apps you open show here.",))
            if acts:
                self._buttons(foot, acts, None)
            return
        _ui.at(*body)
        cur, opened = _ui.table("", COLUMNS, self.cells, self.cur, _ROWS)
        if self._focus:
            self._focus = False
            _ui.focus("rows")
        if 0 <= cur < len(self.rows) and cur != self.cur:
            self.cur = cur
            ws.request_frame()          # the verbs follow the current row
        if opened and acts:
            self._do(acts[0], row)
            return
        self._buttons(foot, acts, row)

    def _notes_page(self, body, foot, acts):
        ws = self.ws
        n = self.notes[self.note_cur] if self.notes else None
        if n is None:
            _ui.at(*body)
            _ui.empty("bubble", "No notifications", ("What apps post shows here.",))
            if acts:
                self._buttons(foot, acts, None)
            return
        if n.body:
            lh = _ui.text_size("Ag")[1] + 4 * ws.layout.fs
            line, body = _ui.cut_bottom(body, lh)
            _ui.at(*_ui.inset(line, 4 * ws.layout.fs, 0))
            _ui.label(n.body)
        _ui.at(*body)
        cur, opened = _ui.table("", NOTE_COLUMNS, self.note_cells, self.note_cur,
                                 _NOTE_ROWS)
        if self._focus:
            self._focus = False
            _ui.focus("notes")
        if 0 <= cur < len(self.notes) and cur != self.note_cur:
            self.note_cur = cur
            ws.request_frame()
        if opened and acts:
            self._do(acts[0], n)
            return
        self._buttons(foot, acts, n)

    def _buttons(self, rect, acts, row):
        n = len(acts)
        cells = _ui.hsplit(rect, n, 4 * self.ws.layout.fs)
        for k in range(n):
            _ui.at(*cells[k])
            if _ui.button(acts[k], "danger" if acts[k] in (CLOSE, STOP) else "normal"):
                self._do(acts[k], row)

    # -- the verbs ----------------------------------------------------------

    def _do(self, verb, row):
        ws = self.ws
        ws._dirty = True
        if verb == HOME:
            ws.close_tasks(home=True)
            return
        if self.tab == NOTES_TAB:
            self._do_note(verb, row)
            return
        key, _name, kind, _state, _cpu, _mem, pid = row
        procs = ws.procs
        if verb == DETAILS:
            ws.open_log(pid)
        elif verb == SWITCH:
            ws.tasks_switch(key)
        elif verb == STOP:
            procs.stop_service(pid)
        elif verb == CLOSE:
            if kind == "service":
                procs.forget(pid)
            else:
                ws.tasks_close(key)
        self._gen = -1

    def _do_note(self, verb, n):
        self._note_gen = -1
        if verb == CLEAR:
            NOTES.clear(n.id)
        elif verb == OPEN:
            NOTES.act(n.id)
        else:
            for a in n.labels():
                if a[1] == verb:
                    NOTES.act(n.id, a[0])
                    break
