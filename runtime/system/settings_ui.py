"""Settings' pages written as `uitree` builds, which `SettingsLayer` hosts
in its sub page's frame (`SettingsPages`):

- Time zone: the zones west to east, each beside its standard offset; the
  one in force wears the current mark and the page opens on it; choosing
  one sets it and goes back.
- Paired browsers: each browser this console paired, by its name and the
  day it was last seen; choosing one forgets it (its key, its open
  sessions), and with two or more Remove all forgets every one. Under them
  the SSH host key's fingerprint, for checking a first SSH connection
  reached this console.

The console's services reach the handlers through `SettingsPages`, which
the hosting console makes.
"""

import uitree as ui
import uitree_app
import wallclock as _wc
import ssh_service as _ssh

NONE_PAIRED = "None. Scan the code on Web console to pair one."
NO_HOST_KEY = "Made at SSH's first start (set a password: passwd)"

s = ui.state(
    zones=ui.model(key="tz", tz="", place="", off=""),
    zone="",
    sel=ui.row_of("zones"),
    browsers=ui.model(key="kid", kid="", name="", seen=""),
    many=False,
    fp="",
)


def _day(t):
    """`t` (Unix seconds, as wallclock takes them) as the day it fell on."""
    y, m, d = _wc.localtime(t)[:3]
    return "%d-%02d-%02d" % (y, m, d)


# -- Time zone ------------------------------------------------------------------

def tz_opened():
    s.zones = [{"tz": tz, "place": place, "off": _wc.utc_label(_wc.parse(tz)[1])}
               for place, tz in _wc.ZONES]
    s.zone = _wc.zone()
    s.sel = s.zone if any(tz == s.zone for _p, tz in _wc.ZONES) else ""


def use(row):
    uitree_app.env().toggles.set_time_zone(row.tz)
    s.zone = row.tz
    ui.pop()


ui.hook("tz_opened", tz_opened)
ui.action("use", "Use", use, takes="row")


def tz_page():
    with ui.page(""):
        with ui.group("Time zone"):
            with ui.rows("zones", cur="sel", current="zone"):
                ui.link("use", "{.off}", label="{.place}")


# -- Paired browsers ------------------------------------------------------------

def read_paired():
    """The paired browsers and the host key's fingerprint, as the page shows them."""
    ws = uitree_app.env()
    web = getattr(ws, "webhost", None)
    rows = []
    if web is not None:
        try:
            rows = [{"kid": kid, "name": name or "A browser", "seen": _day(seen)}
                    for kid, _created, seen, name in web.browsers()]
        except Exception as exc:  # noqa: BLE001 -- a list is not worth a crash
            print("SETTINGS paired browsers:", exc)
    s.browsers = rows
    s.many = len(rows) > 1
    s.fp = _ssh.fingerprint(ws) or ""


def _forget(kid):
    web = getattr(uitree_app.env(), "webhost", None)
    if web is not None:
        web.forget(kid)
    read_paired()


def forget(row):
    _forget(row.kid)


def forget_all():
    _forget("all")


ui.hook("paired_opened", read_paired)
ui.action("forget", "Remove", forget, takes="row")
ui.action("forget_all", "Remove all", forget_all, hint="Remove")


def paired_page():
    with ui.page(""):
        with ui.group("Paired browsers"):
            ui.label(NONE_PAIRED, shown="!browsers")
            with ui.rows("browsers"):
                ui.link("forget", "Seen {.seen}", label="{.name}")
            ui.button("forget_all", "danger", shown="many")
        with ui.group("SSH host key"):
            ui.label(NO_HOST_KEY, shown="!fp")
            ui.label("{fp}", wrap="char", shown="fp")


class SettingsPages:
    """The pages as Settings hosts them, each on its sub page's host: opened
    on each visit (its hook), drawn into the sub page's frame, closed by its
    own `ui.pop()` through `close(name)`."""

    def __init__(self, ws, host, damage, close):
        self.pages = {
            "tz": (uitree_app.Page(tz_page, host, damage, close=lambda: close("tz"), env=ws), "tz_opened"),
            "paired": (uitree_app.Page(paired_page, host, damage, close=lambda: close("paired"), env=ws),
                       "paired_opened"),
        }

    def open(self, name):
        page, hook = self.pages[name]
        page.open(hook=hook)

    def draw(self, name, cv, th, rect, desk, dt):
        self.pages[name][0].paint(dt, cv, th, rect, desk)
