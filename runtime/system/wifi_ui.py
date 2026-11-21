"""Settings' Wi-Fi panel written as `uitree` pages, in its two places:

- Settings' page: the connection (joined or not, its network and address)
  and the panel's line, the networks the last scan found (a lock where one
  needs a password, Saved where this console keeps it), Scan again, and
  Forget for the network picked where it is saved.
- The bar's Wi-Fi popover: the connection on its head, then the networks
  as a menu (the joined one checked) and Scan again.

Choosing a network joins it, or asks for its password in a dialog over
the page (its field focused: typing goes in, Enter connects). Where
nothing in use can type, the dialog says so and offers Cancel alone.

The radio's half (the lease, the scan and the join, polled as they run)
is `SettingsLayer`'s: each frame the host runs `wifi_poll`, which reads
what that half holds into this page's state, and the handlers call it.
"""

import uitree as ui
import uitree_app

WIFI_PW_MAX = 64              # a Wi-Fi password's bytes (WPA2 takes 63)
NETS = 24                     # the networks the panel lists

s = ui.state(
    connected=False,
    ssid="",
    ip="",
    msg="",
    nets=ui.model(key="id", id="", name="", saved="", icon="", mark=""),
    sel=ui.row_of("nets"),
    forget_name="",
    pop=False,
    scroll=False,
    asking=False,
    can_type=False,
    title="",
    password="",
    no_keys="",
)


def poll(page):
    """The radio's half as this page shows it: written only where it moved,
    so an idle frame makes nothing."""
    sl = page.sl
    w = sl._wifi.station()
    c, ssid, ip = False, None, None
    if w is not None:
        try:
            c, ssid, ip = w.status()
        except Exception:  # noqa: BLE001 -- a radio that cannot say is not connected
            pass
    c = bool(c)
    if not c:
        ssid = ip = None
    s.pop = page.popover
    s.scroll = page.overflows
    s.connected = c
    if ssid is not page.ssid or ip is not page.ip:
        page.ssid, page.ip = ssid, ip
        s.ssid = str(ssid) if ssid else ""
        s.ip = str(ip) if ip else ""
        page.nets = None                    # the joined network's mark moves with it
    s.msg = sl.wifi_msg
    nets, known = sl.wifi_nets, sl.wifi_known
    if nets is not page.nets or known is not page.known:
        page.nets, page.known, page.sel = nets, known, -1
        rows = []
        for k in range(min(len(nets), NETS)):
            name, _sig, locked = nets[k]
            name = str(name)
            lock = "lock" if locked else "wifi"
            rows.append({"id": "net%d" % k, "name": name[:24], "saved": "Saved" if name in known else "",
                         "icon": lock, "mark": "check" if c and name == s.ssid else lock})
        s.nets = rows
    if sl.wifi_sel != page.sel:
        page.sel = sl.wifi_sel
        name = str(nets[page.sel % len(nets)][0]) if nets else None
        s.forget_name = name[:16] if name is not None and name in known else ""
    pick = sl.wifi_pick
    s.asking = pick is not None
    if pick is not page.pick:
        page.pick = pick
        if pick is not None:
            s.title = ("Password for " + str(pick))[:30]
            s.no_keys = sl.ws.no_keyboard_text
    s.can_type = bool(sl.ws.text.can_type())
    s.password = sl.wifi_pw


def join(row):
    sl = uitree_app.env()
    sl.wifi_sel = int(row.id[3:])
    sl._wifi_activate()


def picked(row):
    uitree_app.env().wifi_sel = int(row.id[3:])


def rescan():
    sl = uitree_app.env()
    sl._wifi_rescan()
    sl.ws.mark_dirty()


def forget():
    uitree_app.env()._wifi_forget()


def typed():
    uitree_app.env().wifi_pw = s.password


def connect():
    sl = uitree_app.env()
    sl.wifi_pw = s.password
    sl._wifi_connect(sl.wifi_pick, s.password)


def cancel():
    uitree_app.env()._wifi_cancel()


def leave():
    uitree_app.env()._wifi_back(s.pop)


ui.hook("wifi_poll", poll, takes="arg")
ui.hook("picked", picked, takes="row")
ui.hook("typed", typed)
ui.action("join", "Connect", join, takes="row")
ui.action("pick", "Join", join, takes="row")
ui.action("rescan", "Scan again", rescan)
ui.action("forget", "Forget", forget)
ui.action("connect", "Connect", connect)
ui.action("cancel", "Cancel", cancel)
ui.action("leave", "Back", leave, keys=("role:back",), hint="{pop?Close:Back}")


def _password():
    with ui.dialog("{title}", ["cancel", "connect"], shown="asking && can_type"):
        ui.field("Password", "password", max=WIFI_PW_MAX, on="connect", changed="typed")
    with ui.dialog("{title}", ["cancel"], shown="asking && !can_type"):
        ui.label("{no_keys}")


def wifi_page():
    with ui.page(""):
        with ui.group("Wi-Fi"):
            ui.info("{connected?Connected:Not connected}", "{ssid}", icon="{connected?wifi:wifi_off}")
            ui.info("Address", "{ip}", shown="ip")
            ui.label("{msg}", shown="msg")
        with ui.group("Networks"):
            ui.label("No networks found.", shown="!nets")
            with ui.rows("nets", cur="sel", changed="picked"):
                ui.link("join", "{.saved}", label="{.name}", icon="{.icon}")
        with ui.group("Options"):
            ui.link("rescan")
            ui.link("forget", "{forget_name}", shown="forget_name")
        _password()


def _networks(**p):
    with ui.menu("{nets?Networks:}", **p):
        with ui.rows("nets", cur="sel", changed="picked"):
            ui.item("pick", label="{.name}", icon="{.mark}")
        ui.item("rescan", icon="search")


def popover_page():
    with ui.page(""):
        ui.info("Wi-Fi", "{connected?{ssid}:Not connected}")
        with ui.scroll("nets", shown="scroll"):     # a card shorter than its rows: the networks scroll
            _networks()
        _networks(shown="!scroll")
        _password()


class WifiPage:
    """The panel on a host of `SettingsLayer` `sl`: Settings' page, or the
    bar's popover (`popover`). The host routes its keys and taps here and
    draws it into the frame it began."""

    def __init__(self, sl, host, damage, popover=False):
        self.sl, self.popover = sl, popover
        self.overflows = False          # the popover's card is shorter than its rows (its host says)
        self.engine = uitree_app.hosted_page(popover_page if popover else wifi_page, host, damage, env=sl)
        self.engine.bare = popover      # the popover's card is its ground
        self.nets = self.known = self.pick = self.ssid = self.ip = None
        self.sel = -1

    def open(self):
        self.nets = self.known = self.pick = self.ssid = self.ip = None
        self.engine.open()

    def close(self):
        self.engine.close()

    def draw(self, cv, th, rect, desk, dt):
        self.engine.band = desk or not self.popover     # a handheld's popover strip says its keys
        self.engine.run(poll, self, action="wifi_poll")
        ui.commit(self.engine)
        self.engine.paint(dt, cv, th, rect, desk)

    def keys(self, i):
        """In the password dialog its field takes the typed bytes and Esc
        or B cancels; on the networks B backs out of the panel."""
        sl, host = self.sl, self.engine._ui
        if sl.wifi_pick is not None:
            self.engine.keys(i, damage=False)
            if host.inp[5] == 27 or i.pressed("b") or i.pressed("stop"):
                host.inp[5] = 0
                sl._wifi_cancel()
            return True
        if i.pressed("b") or i.pressed("stop"):
            sl._wifi_back(self.popover)
            return True
        self.engine.keys(i, damage=False)
        return True

    def point(self, px, py, click):
        self.engine.point(px, py, click)
