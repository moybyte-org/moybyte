"""The WEB CONSOLE connection screen (#197) -- the glass while wasm mode is on.

WASM MODE IS A SWITCH, NOT A SESSION (owner call, 2026-08-25). There is no
heartbeat, no presence detection, no timeout and no session object: while the
WEB CONSOLE toggle is ON the board parks HERE, and turning it off returns the
normal console. That decision is what makes the whole feature small -- the
alternative (detect a browser, hand the store over, hand it back) is a
distributed-state problem, and the two-writer collision it exists to prevent is
simply designed out instead: while a browser is editing this store, nothing on
the glass can be editing it too, because the glass is showing this screen.

The screen is three things and deliberately nothing else:

  * THE QR CODE, which is the feature. The address is
    `http://<ip>:8080/?pin=NNNN` -- an IP, a port and a pairing pin, ~35
    characters a kid would otherwise read off a 320x240 panel and type into a
    phone. Scanned, it is one gesture and no transcription errors. The encoder
    is `runtime/moy_qr.py`.
  * SHOW ADDRESS, because the QR is useless to a laptop and the address does
    not fit the panel at 1x text alongside everything else. Tap-to-reveal, so
    the common case stays uncluttered and the fallback is one tap away.
  * TURN OFF, because the toggle that got here lives in Settings and Settings
    is behind this screen. A mode with no visible way out is a trap.

Layout note: the address band costs the QR its space only WHILE it is revealed,
and the QR is re-fitted to what is left. Reserving the band unconditionally was
the other option and it is the wrong one: hidden is the SCANNING state, and at
font scale 3 a permanently reserved band halved the module size (148px to 74px
on an 800x480 panel) to keep steady a layout nobody is looking at -- a kid who
taps SHOW ADDRESS has already decided not to scan.

Dependency profile is UpdateUI's (the other parked fullscreen surface, and the
model this follows): everything reaches the console through `self.ws`, and
NAMES / in_rect are injected at construction rather than imported back from
console.py -- `WebConsole` (web_console.py) builds the one instance a
Workstation holds, as `ws.web.ui`.
"""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui

try:
    import moy_qr as _qr
except ImportError:  # host: the runtime package
    from runtime import moy_qr as _qr

# The Library shelf's DISPLAY type, for the one heading here. Not `cv.print`'s
# `scale` argument -- that has been IGNORED on every tier since #39 (system-UI
# scaling is the font_scale path), so passing 2 there is a no-op that reads in
# the source like a larger title and renders like a smaller one.
try:
    from chrome import _print_scaled
except ImportError:  # host fallback when not yet aliased
    from runtime.chrome import _print_scaled


class WebConsoleUI:

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        # Revealed by the SHOW ADDRESS button; reset on every entry, so a kid
        # who left the pin on screen does not find it there tomorrow.
        self.show_address = False
        # The encoded matrix, cached by the url it encodes. Re-encoding costs
        # a Reed-Solomon pass per frame otherwise, and this screen repaints on
        # every theme/reveal change.
        self._qr_for = None
        self._qr = None

    def on_enter(self):
        self.show_address = False

    # -- geometry (pure: the tap handler hit-tests the rects the draw used) ---

    def rects(self):
        """(qr, address band, SHOW ADDRESS, TURN OFF) for the current layout.

        Pure, and shared by the draw and the tap handler -- the `action_rects`
        pattern this repo's surfaces use, so a button is hit-tested at exactly
        the pixels it was painted at. The panel is Settings' own
        (`lay.settings_panel`), because the toggle that gets here lives on that
        screen and the family resemblance is the whole navigational cue."""
        lay = self.ws.layout
        fs = lay.fs
        px, py, pw, ph = lay.settings_panel
        inset = 8 * fs
        gap = 4 * fs
        btn_h = 16 * fs
        btn_y = py + ph - btn_h - gap
        floor = btn_y - gap
        addr_h = 20 * fs if self.show_address else 0   # two 8*fs lines + lead
        addr = (px + inset, floor - addr_h, pw - 2 * inset, addr_h)
        if addr_h:
            floor = addr[1] - gap
        # Below the display-type heading (16*fs) and the subtitle line (8*fs),
        # with room to breathe -- anything less and the QR's white quiet zone
        # runs into the subtitle.
        qr_top = py + 34 * fs
        qr = (px + inset, qr_top, pw - 2 * inset, max(0, floor - qr_top))
        bw = (pw - 2 * inset - 2 * gap) // 2
        return (qr, addr,
                (px + inset, btn_y, bw, btn_h),
                (px + inset + bw + 2 * gap, btn_y, bw, btn_h))

    # -- input ---------------------------------------------------------------

    def handle_input(self, i):
        if i.pressed("a"):
            self.show_address = not self.show_address
            self.ws._dirty = True
        elif i.pressed("b") or i.pressed("home") or i.pressed("stop"):
            # HOME turns the mode off rather than trying to leave it. Leaving is
            # what the mode forbids -- every other exit path re-parks here (see
            # Workstation.go_home) -- so a HOME that did nothing would read as a
            # frozen console.
            self.ws.web.stop()
        return True

    def handle_pointer(self, px, py, click):
        if not click:
            return True
        _qrr, _addr, show, off = self.rects()
        if self._in(px, py, show):
            self.show_address = not self.show_address
            self.ws._dirty = True
        elif self._in(px, py, off):
            self.ws.web.stop()
        return True

    # -- draw ----------------------------------------------------------------

    def matrix(self, url):
        if url != self._qr_for:
            try:
                self._qr = _qr.encode(url)
            except Exception:      # noqa: BLE001 -- a url too long for v4, or worse
                self._qr = None
            self._qr_for = url
        return self._qr

    def draw(self, dt):
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        lay = ws.layout
        fs = lay.fs
        NAMES = self._NAMES
        cv.rect(0, 0, lay.w, lay.h, th["bar"])
        px, py, pw, ph = lay.settings_panel
        _ui.dialog(cv, (px, py, pw, ph), ring=th["edge"], fill=th["surface"])
        ws._glyph("wifi", (px + 6 * fs, py + 2 * fs, 16 * fs, 16 * fs),
                  th["accent"], cv)
        _print_scaled(cv, "WEB CONSOLE", px + 26 * fs, py + 4 * fs, th["ink"], 2)
        cv.print("SCAN THIS WITH A PHONE", px + 10 * fs, py + 24 * fs,
                 th["ink_dim"], 1)
        qr_rect, addr, show, off = self.rects()
        url = ws.web.url()
        self._draw_qr(cv, qr_rect, url, NAMES)
        if self.show_address:
            self._draw_address(cv, th, addr, url, fs)
        _ui.button(cv, th, show,
                   "HIDE ADDRESS" if self.show_address else "SHOW ADDRESS",
                   on=self.show_address)
        _ui.button(cv, th, off, "TURN OFF", kind="danger")

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

    def _draw_address(self, cv, th, rect, url, fs):
        """The url as text, for a laptop or a phone that will not scan.

        Split into the address and the pin on two lines: the whole url is ~35
        characters and the panel holds ~35 at 1x with nothing to spare, so one
        line would clip exactly where the digits are -- and the digits are the
        half nobody can guess.

        The pin's ink comes off the theme's own presentation class rather than a
        constant: `accent` is signal yellow, which on a dark panel is the loudest
        thing on the screen and on a LIGHT one is yellow on cream."""
        x, y, _w, _h = rect
        host = url.replace("http://", "")
        pin = ""
        if "?pin=" in host:
            host, _sep, pin = host.partition("?pin=")
        cv.print(host.rstrip("/") or "?", x, y, th["ink"], 1)
        if pin:
            cv.print("PIN " + pin, x, y + 10 * fs,
                     th["ink"] if _ui.is_light(th) else th["accent"], 1)
