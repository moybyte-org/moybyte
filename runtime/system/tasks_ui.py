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

The screen is a `uitree` page (`tasks_page`) on the layer's own AppHost:
the tab strip, then per tab its table and a foot whose buttons share its
width, each verb an action on the current row (`cur=`) that hides where
the row does not offer it. The layer reads the rows when the running set
or the list moves (`_fill`) and keeps the rows as they came by key, which
the handlers act on (`_do`).
"""

import ui_draw as _ui_draw
import layout_base as _lb
import uitree as ui
import uitree_app
from layers import Layer
from notifications import NOTES

TITLE = "Tasks"
COLUMNS = ("Name", "State", "CPU", "Mem")
STATES = {"running": "Running", "throttled": "Behind", "minimised": "Minimised",
          "paused": "Paused", "background": "Service", "suspended": "Suspended",
          "stopping": "Stopping", "crashed": "Crashed"}
SWITCH, CLOSE, STOP, DETAILS, HOME = "Switch to", "Close", "Stop", "Details", "Home"
OPEN, CLEAR = "Open", "Clear"
NOTE_COLUMNS = ("From", "Notification")
NOTE_LABELS = 2                 # the actions a row offers by name, at most


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


# -- the page -------------------------------------------------------------------

s = ui.state(
    tab=ui.enum("running", "notes", init="running"),
    procs=ui.model(key="id", id="", name="", st="", cpu="", mem="", first="",
                   can_details=False, can_switch=False, can_stop=False, can_close=False),
    cur=ui.row_of("procs"),
    any_procs=False,
    notes=ui.model(key="id", id="", app="", summary="", body="", l1="", l2=""),
    note=ui.row_of("notes"),
    any_notes=False,
    hand=False)
ui.labels("TABS", {"running": "Running", "notes": "Notifications"})


def _fill(layer):
    """The rows as the running set and the list hold them now."""
    procs = layer.ws.procs
    layer._gen = procs.gen
    layer._note_gen = NOTES.gen
    rows = procs.rows()
    layer.raw = dict((r[0], r) for r in rows)
    out = []
    for r in rows:
        vs = verbs(r)
        out.append({"id": r[0], "name": r[1], "st": STATES.get(r[3], r[3]), "cpu": cpu_text(r[4]),
                    "mem": mem_text(r[5]), "first": vs[0], "can_details": DETAILS in vs,
                    "can_switch": SWITCH in vs, "can_stop": STOP in vs, "can_close": CLOSE in vs})
    s.procs = out
    s.any_procs = bool(out)
    layer.notes = list(NOTES.list)
    notes = []
    for n in layer.notes:
        named = n.labels()[:NOTE_LABELS] + [("", ""), ("", "")]
        notes.append({"id": str(n.id), "app": str(n.app), "summary": n.summary, "body": n.body or "",
                      "l1": named[0][1], "l2": named[1][1]})
    s.notes = notes
    s.any_notes = bool(notes)
    s.hand = procs.handheld()


def opened(note):
    """Open on the running set, or on the Notifications tab with
    notification `note` current; focus on the tab's list."""
    layer = uitree_app.env()
    _fill(layer)
    s.cur = ""
    s.note = ""
    if note is not None and note in layer.notes:
        s.tab = "notes"
        s.note = str(note.id)
        ui.focus("notes")
    else:
        s.tab = "notes" if note is not None else "running"
        ui.focus("notes" if note is not None else "procs")


def _proc(row):
    return uitree_app.env().raw.get(row.id)


def _note(row):
    for n in uitree_app.env().notes:
        if str(n.id) == row.id:
            return n
    return None


def _verb(verb):
    def run(row):
        r = _proc(row)
        if r is not None:
            uitree_app.env()._do(verb, r)
    return run


def _note_verb(which):
    def run(row):
        n = _note(row)
        if n is None:
            return
        if which in (OPEN, CLEAR):
            uitree_app.env()._do(which, n)
            return
        named = n.labels()[:NOTE_LABELS]
        if which < len(named):
            uitree_app.env()._do(named[which][1], n)
    return run


def open_row(row):
    """Enter on a row: its first verb."""
    r = _proc(row)
    if r is not None:
        uitree_app.env()._do(verbs(r)[0], r)


def go_home():
    uitree_app.env()._do(HOME, None)


ui.action("details", DETAILS, _verb(DETAILS), takes="row", row="cur", when="cur.can_details", off="hide")
ui.action("switch_to", SWITCH, _verb(SWITCH), takes="row", row="cur", when="cur.can_switch", off="hide")
ui.action("stop", STOP, _verb(STOP), takes="row", row="cur", when="cur.can_stop", off="hide")
ui.action("close", CLOSE, _verb(CLOSE), takes="row", row="cur", when="cur.can_close", off="hide")
ui.action("home", HOME, go_home, when="hand", off="hide")
ui.action("open_note", OPEN, _note_verb(OPEN), takes="row", row="note", off="hide")
ui.action("named_one", "Act", _note_verb(0), takes="row", row="note", when="note.l1", off="hide",
          hint="{note.l1}")
ui.action("named_two", "Act", _note_verb(1), takes="row", row="note", when="note.l2", off="hide",
          hint="{note.l2}")
ui.action("clear", CLEAR, _note_verb(CLEAR), takes="row", row="note", off="hide")
ui.hook("opened", opened, takes="arg")
ui.hook("open_row", open_row, takes="row")
ui.hook("open_note_row", _note_verb(OPEN), takes="row")


def running():
    with ui.table("procs", cols=COLUMNS, cur="cur", on="open_row", hint="{cur.first}",
                  shown="any_procs"):
        ui.col("{.name}")
        ui.col("{.st}")
        ui.col("{.cpu}")
        ui.col("{.mem}")
    ui.empty("Nothing running", icon="layout", lines=["Games and apps you open show here."],
             shown="!any_procs")
    with ui.foot(look="share"):
        ui.button("details")
        ui.button("switch_to")
        ui.button("stop", "danger")
        ui.button("close", "danger")
        ui.button("home")


def notifications():
    with ui.table("notes", cols=NOTE_COLUMNS, cur="note", on="open_note_row", hint=OPEN,
                  shown="any_notes"):
        ui.col("{.app}")
        ui.col("{.summary}")
    ui.empty("No notifications", icon="bubble", lines=["What apps post shows here."],
             shown="!any_notes")
    with ui.foot(look="share"):
        ui.label("{note.body}", shown="any_notes")
        ui.button("open_note")
        ui.button("named_one", label="{note.l1}")
        ui.button("named_two", label="{note.l2}")
        ui.button("clear")
        ui.button("home")


def tasks_page():
    with ui.page(""):
        ui.tabs("tab", labels="TABS")
        ui.switch("tab", {"running": running, "notes": notifications})


class _Damage:
    def __init__(self, ws):
        self.ws = ws

    def all(self):
        self.ws.mark_dirty()


class TasksLayer(Layer):
    """The task manager's content layer, kind "tasks"."""

    id = "tasks"
    domain = "system"
    TITLE = TITLE                   # its window's and its chip's (the app table)
    BAR = None                      # its bar has no keys
    back_title = TITLE              # the bar's chevron and title (BarLayer)
    zone_gen = 0                    # ...whose cache key: the title never moves

    def __init__(self, ws):
        self.ws = ws
        self.host = _ui_draw.AppHost()
        self.raw = {}                # a row's key -> the row as Procs.rows gave it
        self.notes = []              # the list's notifications as the rows show them
        self._gen = -1               # the running set's gen the rows were read at
        self._note_gen = -1
        self._rect = (0, 0, 0, 0)
        self.held = False            # it paused the front game when it opened
        self._pg = None

    def page(self):
        if self._pg is None:
            self._pg = uitree_app.Page(tasks_page, self.host, _Damage(self.ws), env=self)
        return self._pg

    # -- the rows -----------------------------------------------------------

    def moved(self):
        """The rows changed since they were drawn: a sample, a state, a run,
        a notification."""
        return self.ws.procs.gen != self._gen or NOTES.gen != self._note_gen

    def current(self):
        """(the tab, its current row as the running set or the list holds it)."""
        pg = self.page()
        pg._bind()
        if s.tab == "notes":
            return "notes", next((n for n in self.notes if str(n.id) == s.note), None)
        return "running", self.raw.get(s.cur)

    def select(self, i):
        """Row `i` of the running set is current, the focus on it."""
        def pick():
            s.cur = s.procs[i].id
        pg = self.page()
        if self.moved():
            pg.run(_fill, self, action="fill")
        pg.run(pick, action="select")
        ui.commit(pg)

    # -- the app protocol ----------------------------------------------------

    def open(self, note=None):
        """Open on the running set, or on the Notifications tab with
        notification `note` current."""
        self.host.reset()
        self.page().open(note)
        self._gen = -1               # read again at the frame: opening may pause the game in front
        self.ws.mark_dirty()

    B_BACK = True

    def is_app(self, cart):
        """No cart is the task manager: it opens from the system menu."""
        return False

    def leave(self):
        """Back where it was opened from (`Workstation.close_tasks`)."""
        self.ws.close_tasks()
        return True

    def ui_host(self):
        return self.host

    def draw_zone(self, cv, rect):
        return None

    def zone_tap(self, px, py, rect=None):
        return False

    def handle_input(self, i):
        ws = self.ws
        if i.pressed("home"):
            ws.close_tasks()
            ws.home.home_key()
            return True
        if i.pressed("b") or i.pressed("stop"):
            ws.input_policy.back(leave=True)
            return True
        return self.page().keys(i)

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if click and ws.bar_layer.handle_bar_tap("tasks", px, py):
            return True
        if self.host.point(px, py, click, bool(getattr(ws.pointer, "down", False))):
            ws.mark_dirty()
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
        th = ws.theme_colors
        g = th["panel"]
        if y > 0:
            cv.rect(0, 0, cv.w, y, g)
        if y + h < cv.h:
            cv.rect(0, y + h, cv.w, cv.h - y - h, g)
        if x > 0:
            cv.rect(0, y, x, h, g)
        if x + w < cv.w:
            cv.rect(x + w, y, cv.w - x - w, h, g)
        pg = self.page()
        if self.moved():
            pg.run(_fill, self, action="fill")
            ui.commit(pg)
        desk = _lb.metric_class() == _lb.DESK
        rect = self._content()
        self.host.begin(cv, th, rect, desk, ws.windowed_chrome)
        pg.paint(dt, cv, th, rect, desk)

    # -- the verbs ----------------------------------------------------------

    def _do(self, verb, row):
        """Verb `verb` on `row`: a row of the running set, or a
        notification of the list."""
        ws = self.ws
        ws.mark_dirty()
        if verb == HOME:
            ws.close_tasks(home=True)
            return
        if not isinstance(row, tuple):
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
