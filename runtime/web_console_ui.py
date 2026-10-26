"""The WEB CONSOLE connection screen (#197) -- the glass while wasm mode is on.

WASM MODE IS A SWITCH, NOT A SESSION (owner call, 2026-08-25). There is no
heartbeat, no presence detection, no timeout and no session object: while the
WEB CONSOLE toggle is ON the board parks HERE, and turning it off returns the
normal console. That decision is what makes the whole feature small -- the
alternative (detect a browser, hand the store over, hand it back) is a
distributed-state problem, and the two-writer collision it exists to prevent is
simply designed out instead: while a browser is editing this store, nothing on
the glass can be editing it too, because the glass is showing this screen.

The screen is a page (docs/look_2026-10.md step 33) and deliberately
nothing else:

  * THE QR CODE, which is the feature, on its white quiet zone. The address
    is `http://<ip>/?pin=NNNN` -- an IP and a pairing pin, ~30 characters a
    kid would otherwise read off a 320x240 panel and type into a phone.
    Scanned, it is one gesture and no transcription errors. The encoder is
    `runtime/moy_qr.py`.
  * "Open in a browser", the address in the code face and the pin large,
    always shown: the look's 12 px type fits them beside the code, where the
    8 px print face did not, so the address is no longer a tap away.
  * Turning it off, because the toggle that got here lives in Settings and
    Settings is behind this screen. A mode with no visible way out is a
    trap: back (K on the T-Deck, Esc on a keyboard) turns it off, and a
    touch or pointer glass adds Turn off as a button.

Dependency profile is UpdateUI's (the other parked fullscreen surface, and the
model this follows): everything reaches the console through `self.ws`, and
NAMES is injected at construction rather than imported back from console.py --
`WebConsole` (web_console.py) builds the one instance a Workstation holds, as
`ws.web.ui`. The rect hit-test is `ui.rect_in`, imported directly.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
_in = _ui.rect_in   # one hit-test (ui.rect_in)

try:
    import moy_qr as _qr
except ImportError:  # host: the runtime package
    from runtime import moy_qr as _qr

try:
    import layout_base as _lb
except ImportError:  # host: the runtime package
    from runtime import layout_base as _lb

_HEAD = {"face": "title"}
_SAY = {"tone": "dim", "wrap": True, "lines": 3}
_ADDRESS = {"face": "mono", "tone": "value"}
_PIN_CAP = {"tone": "dim", "face": "caption"}
_PIN = {"face": "result"}
_KEYS = ("back", "Turn off")


class WebConsoleUI:

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        # The encoded matrix, cached by the url it encodes. Re-encoding costs
        # a Reed-Solomon pass per frame otherwise, and this screen repaints on
        # every theme change.
        self._qr_for = None
        self._qr = None
        self.host = _ui_draw.AppHost()

    def on_enter(self):
        self.host.reset()

    # -- input ---------------------------------------------------------------

    def handle_input(self, i):
        if i.pressed("b") or i.pressed("home") or i.pressed("stop") \
                or getattr(i, "last_key", 0) == 27:
            # HOME turns the mode off rather than trying to leave it. Leaving is
            # what the mode forbids -- every other exit path re-parks here (see
            # Workstation.go_home) -- so a HOME that did nothing would read as a
            # frozen console.
            self.ws.web.stop()
        elif self.host.keys(i):
            self.ws._dirty = True
        return True

    def handle_pointer(self, px, py, click):
        if self.host.point(px, py, click, getattr(self.ws.pointer, "down", False)):
            self.ws._dirty = True
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

    def draw(self, dt):
        """The page under the bar: the code at the left, what to do at its
        right -- "Open in a browser", how, the address in the code face, the
        pin large -- and Turn off where a finger or a pointer is."""
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        fs = lay.fs
        desk = _lb.metric_class() == _lb.DESK
        cx, cy, cw, ch = lay.cx, lay.cy, lay.cw, lay.ch
        self.host.begin(cv, ws.theme_colors, (cx, cy, cw, ch), desk)
        _ui.page("")
        _ui.hints(_KEYS)
        url = ws.web.url()
        host, pin = _split(url)
        pad = (24 if desk else 14) * fs
        foot = (_ui_draw.metrics("value_row")[17 if desk else 16] * fs
                if _ui.input_kind() != "touch" else 0)
        room_h = ch - foot - 2 * pad
        side = min(room_h, (cw - 3 * pad) * 2 // 5)
        qr = (cx + pad, cy + pad + (room_h - side) // 2, side, side)
        self._draw_qr(cv, qr, url, self._NAMES)
        x = qr[0] + side + pad
        w = cx + cw - pad - x
        y = qr[1]
        y = self._line("Open in a browser", _HEAD, x, y, w, "title")
        y = self._line("Scan this with a phone, or on a computer go to", _SAY, x, y, w,
                       "ui", 2)
        y = self._line(host or "Waiting for an address", _ADDRESS if host else _SAY, x, y, w,
                       "mono")
        if pin:
            y += 6 * fs
            y = self._line("Pin", _PIN_CAP, x, y, w, "caption")
            y = self._line(pin, _PIN, x, y, w, "result")
        if _ui.input_kind() == "touch" or desk:
            _ui.at(x, y + 8 * fs, min(w, 120 * fs), 28 * fs)
            if _ui.button("Turn off", "danger"):
                ws.web.stop()
        ws.bar_layer._draw_status_strip("webconsole")
        if self.host.owed or self.host.tap == 2:
            ws.request_frame()               # a tap's release, next frame

    def _line(self, text, o, x, y, w, role, lines=1):
        """`text` stacked at (x, y) `w` wide in `role`'s face, `lines` lines
        at most: the y under it."""
        h = _ui_draw.font(role)[2] * self.ws.layout.fs * lines + 4 * self.ws.layout.fs
        _ui.at(x, y, w, h)
        _ui.label(text, o)
        return y + h

    def _draw_qr(self, cv, rect, url, NAMES):
        """The code, centered in `rect` at the largest whole-pixel module size
        that fits -- including the spec's four-module quiet zone, which is drawn
        as part of the light field rather than left to whatever is behind.

        BLACK ON WHITE from the palette, never from the theme: `ink` is white on
        every dark theme, and an inverted code is one most scanners refuse."""
        m = self.matrix(url) if url else None
        if not m:
            cv.print("NO ADDRESS", rect[0], rect[1], NAMES["red"], 1)
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
    """(the address, the pin) of the console's url, "" where it has none."""
    if not url:
        return "", ""
    host = url.replace("http://", "")
    pin = ""
    if "?pin=" in host:
        host, _sep, pin = host.partition("?pin=")
    return host.rstrip("/"), pin
