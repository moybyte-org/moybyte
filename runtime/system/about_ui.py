# Map (grep -n a name to jump there):
#   version_words       the firmware's version and channel, as About says them
#   list_page           Settings' Licences: the parts and their licences (a desk: and the text)
#   LicencesPage        the page as Settings hosts it
"""About on the console (docs/history/look_2026-10.md step 32): Settings'
Licences page, a `uitree` build, and the words About says (the version, the
board) that the system menu's About card (`shell_cards.AboutCard`) and
Settings' About rows share.

* Licences is a page of Settings (its About group's last row): Moybyte's
  licence in a line, then a group of link rows per kind of part --
  Moybyte's parts, and what it is made with (`runtime/system/licences.py`,
  generated from the texts THIRD_PARTY.md names, the parts this board's
  image holds). On a handheld a row opens its licence's text, paragraphs
  kept, as a page pushed over the list; on the desk the list stands at the
  left and the focused part's text at the right. The text is a text view
  of prose (`face="ui"`), its own scroll stop: the arrows scroll it a line
  once it is focused, PgUp and PgDn a page.

The page reaches the shell through its AppContext (`LicencesPage.NEEDS`).
"""

import ui as _ui
import uitree as ui
import uitree_app
import layout_base as _lb
import licences as _lic

_CHANNELS = {"stable": "Stable", "unstable": "Beta"}
TITLE = "Licences"

s = ui.state(parts=ui.model(key="id", id="", lic="", group=""), sel=ui.row_of("parts"),
             name="", title="", text="")


def version_words(updater, channel):
    """The firmware's version and its update `channel` as About says them
    ("0.8 · Stable"), None where the console has no `updater` (the host)."""
    if updater is None:
        return None
    try:
        v = str(updater.version_label())
    except Exception:  # noqa: BLE001
        return None
    ch = _CHANNELS.get(channel)
    return v + " · " + ch if ch else v


def board_words(name):
    """A board's name as the ui face can draw it: its inch mark (″) a
    straight quote where the face has none."""
    if name and "″" in name and not _ui.face("ui", name):
        return name.replace("″", '"')
    _ui.face()
    return name


def board_of():
    """This image's board (`[board] ota`), None off a board."""
    try:
        import moy_ota
    except ImportError:
        return None
    return getattr(moy_ota, "BOARD", None)


def _entry(pid):
    for _cap, entries in _lic.GROUPS:
        for e in entries:
            if e[0] == pid:
                return e
    return None


def _show(pid):
    e = _entry(pid)
    if e is not None:
        s.name, s.title, s.text = e[0], e[2], "\n".join(_lic.text(e))


def opened():
    """The parts this image holds, grouped as THIRD_PARTY.md groups them;
    the first's text shown (the desk's right side)."""
    board = board_of()
    s.parts = [{"id": e[0], "lic": e[1], "group": cap}
               for cap, entries in _lic.GROUPS for e in entries if _lic.shown(e, board)]
    if s.parts and not any(r.id == s.sel for r in s.parts):
        s.sel = s.parts[0].id
    _show(s.sel)


def show(row):
    """Focus moved to `row`: its text is the one beside the list (the desk)."""
    _show(row.id)


def read(row):
    """A row opened: its text, pushed over the list on a handheld; on the
    desk, where the focused row's text stands beside the list already, the
    row takes focus (PgUp and PgDn page the text from the list)."""
    s.sel = row.id
    _show(row.id)
    if _lb.metric_class() == _lb.DESK:
        ui.focus(row.id + "/read")
    else:
        ui.push(text_page)


ui.hook("opened", opened)
ui.hook("show", show, takes="row")
ui.action("read", "Read", read, takes="row")


def _parts():
    with ui.rows("parts", cur="sel", group="{.group}", changed="show"):
        ui.link("read", "{.lic}", label="{.id}")


def list_page():
    with ui.page(""):
        ui.label(_lic.LEAD, tone="dim", wrap="word", lines=3)
        _parts()


def desk_page():
    with ui.page(""):
        with ui.split("row", sizes=(2, 3), align="start"):
            with ui.group(""):
                ui.label(_lic.LEAD, tone="dim", wrap="word", lines=3)
                _parts()
            with ui.group(""):
                ui.label("{name}", face="title")
                ui.label("{title}", tone="dim", wrap="word")
                ui.text_view("text", face="ui", wrap="word")


def text_page():
    with ui.page("{name}"):
        ui.label("{title}", face="caption", tone="dim")
        ui.text_view("text", face="ui", wrap="word")


class LicencesPage:
    """Settings' Licences page (`SettingsLayer` draws it on its sub page's
    host): the list, or on the desk the list and the focused part's text."""

    NEEDS = ("surface", "theme", "damage")

    def __init__(self, ctx, host=None):
        self._ctx = ctx
        self._host = host
        self.page = None

    def _page(self):
        desk = _lb.metric_class() == _lb.DESK
        if self.page is None or self.page.desk_build != desk:
            self.page = uitree_app.Page(desk_page if desk else list_page, self._host,
                                        self._ctx.damage, env=self._ctx)
            self.page.desk_build = desk
        return self.page

    def open(self):
        self._page().open()

    def text_open(self):
        """A handheld's text page is up over the list."""
        return self.page is not None and len(self.page.pages) > 1

    def title(self):
        if self.text_open():
            return self.page.s._v["name"]
        return TITLE

    def back(self):
        """Esc, B or the bar's way back: an open text closes onto its row.
        False where none was open."""
        if not self.text_open():
            return False
        self.page.back()
        self._ctx.damage.all()
        return True

    def keys(self, i):
        return self._page().keys(i, damage=False)

    def read(self, pid):
        """Open part `pid`'s text, as its row's Enter does."""
        pg = self._page()
        row = next(r for r in pg.s._v["parts"] if r._f["id"] == pid)
        pg.run(read, row, action="read")
        ui.commit(pg)

    def draw(self, rect, dt=0.0):
        pg = self._page()
        cv, th = self._ctx.surface.canvas(), self._ctx.theme.colors()
        pg.paint(dt, cv, th, rect, _lb.metric_class() == _lb.DESK)
