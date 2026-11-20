"""The WEB CONSOLE connection screen (#197) -- the glass while wasm mode is on.

WASM MODE IS A SWITCH, NOT A SESSION (owner call, 2026-08-25). There is no
heartbeat, no presence detection, no timeout and no session object: while the
WEB CONSOLE toggle is ON the board parks HERE, and turning it off returns the
normal console. That decision is what makes the whole feature small -- the
alternative (detect a browser, hand the store over, hand it back) is a
distributed-state problem, and the two-writer collision it exists to prevent is
simply designed out instead: while a browser is editing this store, nothing on
the glass can be editing it too, because the glass is showing this screen.

The screen is a `uitree` page (`web_page`: the code a canvas cell of a split,
what to do the column beside it), hosted on the screen's own AppHost, and
deliberately nothing else:

  * THE QR CODE, which is the feature, on its white quiet zone. The link is
    `http://<ip>/#t=TOKEN&b=ID`: the address and, after its `#`, a single-use
    pairing token (web_console.WebConsole.url). Scanned, it pairs the phone
    in one gesture: the token is the first sign-in, and the browser keeps a
    key of its own from then on. The encoder is `runtime/system/moy_qr.py`.
  * "Open in a browser", the address in the code face, always shown, and
    how a browser that cannot scan signs in: the console's password where
    one is set (`passwd`), else the code is the only way in.
  * Whether the page fits this firmware: the browser console is an add-on
    cart (runtime/carts/web_addon.py), and after a firmware update the one
    installed fits the old image until the one that fits arrives. The line
    says so, or how far its update is, and Update it starts one.
  * Turning it off, because the toggle that got here lives in Settings and
    Settings is behind this screen. A mode with no visible way out is a
    trap: back (K on the T-Deck, Esc on a keyboard) turns it off, and a
    touch or pointer glass adds Turn off as a button.

The screen reaches the shell through its AppContext (`NEEDS`: the surface it
draws on, the live tokens, the damage flags) and through the `WebConsole`
that owns it (web_console.py builds the one instance, as its `ui`, and hands
itself in): the url and the stop. Its layer (`layers._WebConsoleLayer`) hands
it the content rect and draws the bar's status strip over it. NAMES is
injected at construction rather than imported back from console.py.
"""

import ui as _ui
import ui_draw as _ui_draw
import uitree as ui
import uitree_app
import moy_qr as _qr
import layout_base as _lb

s = ui.state(address="", sign_in="", note="", stale=False)


def web_poll(web):
    """What the page says, read from the WebConsole each frame."""
    s.address = _split(web.url())
    s.sign_in = web.sign_in_hint() if s.address else ""
    s.note = web.page_note() or ""
    s.stale = bool(web.page_stale())


def update_it():
    uitree_app.env()._web.update_page()


def turn_off():
    uitree_app.env()._web.stop()


ui.action("update_it", "Update it", update_it)
ui.action("turn_off", "Turn off", turn_off, keys=("role:back",))


def draw_qr(cv, x, y, w, h):
    uitree_app.env().draw_qr(cv, (x, y, w, h))


def web_page():
    with ui.page(""):
        with ui.split("row", sizes=(1, 2)):
            ui.canvas("qr", draw_qr)
            with ui.group(""):
                ui.label("Open in a browser", face="title", wrap="word")
                ui.label("Scan this with a phone, or on a computer go to", tone="dim", wrap="word")
                ui.label("{address}", face="mono", tone="value", shown="address")
                ui.label("Waiting for an address", tone="dim", shown="!address")
                ui.label("{sign_in}", face="caption", tone="dim", wrap="word", shown="address")
                ui.label("{note}", face="caption", tone="dim", wrap="word", shown="note")
                ui.button("update_it", shown="stale")
                ui.button("turn_off", "danger", shown="@touch|@desk")


class _Damage:
    def __init__(self, damage):
        self.d = damage

    def all(self):
        self.d.all()


class WebConsoleUI:
    """The connection screen's host: the page (`web_page`) on its own
    AppHost, its keys and pointer, and the code's matrix."""

    NEEDS = ("surface", "theme", "damage")

    def __init__(self, ctx, web, names):
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._web = web
        self._NAMES = names
        # The encoded matrix, cached by the url it encodes. Re-encoding costs
        # a Reed-Solomon pass per frame otherwise, and this screen repaints on
        # every theme change.
        self._qr_for = None
        self._qr = None
        self.host = _ui_draw.AppHost()
        self._pg = None

    def page(self):
        if self._pg is None:
            self._pg = uitree_app.hosted_page(web_page, self.host, _Damage(self._damage), env=self)
            self._pg.open()
        return self._pg

    def on_enter(self):
        self.host.reset()

    # -- input ---------------------------------------------------------------

    def handle_input(self, i):
        if i.pressed("b") or i.pressed("home") or i.pressed("stop") \
                or getattr(i, "last_key", 0) == 27:
            # HOME turns the mode off rather than trying to leave it. Leaving is
            # what the mode forbids -- every other exit path re-parks here (see
            # the Workstation's go_home) -- so a HOME that did nothing would
            # read as a frozen console.
            self.page().role("back")
        elif self.host.keys(i):
            self._damage.all()
        return True

    def handle_pointer(self, px, py, click):
        p = self._surf.pointer()
        if self.host.point(px, py, click, p is not None and p.down):
            self._damage.all()
        return True

    # -- draw ----------------------------------------------------------------

    def matrix(self, url):
        if url != self._qr_for:
            try:
                self._qr = _qr.encode(url)
            except Exception as exc:  # noqa: BLE001 -- too long for v4, or worse
                # Swallowing is right -- a url that will not encode must not
                # take the console down -- but swallowing SILENTLY is what let a
                # MicroPython-only TypeError inside the encoder present as
                # nothing but the words NO ADDRESS, on every board, for the
                # whole life of the feature. Once per url, say which url and why.
                self._qr = None
                print("Moybyte QR encode failed:", url, exc)
            self._qr_for = url
        return self._qr

    def draw(self, rect, dt=0.0):
        """The page over `rect`, the content under the bar: the code at the
        left, what to do at its right, and Turn off where a finger or a
        pointer is."""
        cv = self._surf.canvas()
        th = self._theme.colors()
        desk = _lb.metric_class() == _lb.DESK
        self.host.begin(cv, th, rect, desk)
        pg = self.page()
        pg.run(web_poll, self._web, action="web_poll")
        ui.commit(pg)
        pg.paint(dt, cv, th, rect, desk)
        if s.note:
            self._damage.again()           # the update's progress moves on its own
        if self.host.owed or self.host.tap == 2:
            self._damage.again()             # a tap's release, next frame

    def draw_qr(self, cv, rect):
        """The code, centered in `rect` at the largest whole-pixel module size
        that fits -- including the spec's four-module quiet zone, which is drawn
        as part of the light field rather than left to whatever is behind --
        or, while there is no address, NO ADDRESS centred in its place.
        BLACK ON WHITE from the palette, never from the theme: `ink` is white on
        every dark theme, and an inverted code is one most scanners refuse."""
        NAMES = self._NAMES
        url = self._web.url()
        m = self.matrix(url) if url else None
        if not m:
            _ui_draw.label(cv, rect, "NO ADDRESS", NAMES["red"], _ui_draw.ALIGN_CENTER)
            return
        n = len(m) + 2 * _qr.QUIET
        scale = rect[2] // n
        if rect[3] // n < scale:
            scale = rect[3] // n
        if scale < 1:
            return                    # no room for even a 1px module: draw none
        side = n * scale
        ox = rect[0] + (rect[2] - side) // 2
        oy = rect[1] + (rect[3] - side) // 2
        cv.rect(ox, oy, side, side, NAMES["white"])
        dark = NAMES["black"]
        ox += _qr.QUIET * scale
        oy += _qr.QUIET * scale
        for r in range(len(m)):
            row = m[r]
            y = oy + r * scale
            c = 0
            width = len(row)
            while c < width:
                if not row[c]:
                    c += 1
                    continue
                run = c
                while run < width and row[run]:
                    run += 1
                cv.rect(ox + c * scale, y, (run - c) * scale, scale, dark)
                c = run


def _split(url):
    """The address of the console's url, its pairing token cut off; "" with
    none."""
    if not url:
        return ""
    return url.replace("http://", "").split("#", 1)[0].rstrip("/")
