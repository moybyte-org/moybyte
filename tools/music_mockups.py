#!/usr/bin/env python3
"""The Music app's mockups (#110), and the shell's status item and control
center it plays through: each screen drawn with the public UI API's verbs on
the goldens' workstation (`tests/test_shell_goldens.py`), at the real sizes,
in Moy, written as PNGs.

usage: .venv/bin/python tools/music_mockups.py OUT [FILTER ...] [--theme NAME]

A scene is a page of `MusicMock` (a stand-in for the app with the media
face's answers faked) or the shell with a stand-in component open in its
own popover (`toggle_popover`). Each is drawn in the modes its entry names,
with the T-Deck's keys on a handheld unless it names its input (`pad`,
`touch`), and written as THEME-MODE_SCENE.png (and @3x for 320x240).

The media card (`MediaCard`) is one component at three densities -- the
strip, compact (the control center, the desk menu, the desk window's foot)
and full (Now playing) -- in four states: playing, paused, connecting and
failed. What the toolkit and the shell do not draw yet is drawn here the way
the build is to draw it (docs/media/music_2026-11/README.md lists each).
"""

import argparse
import fnmatch
import gc
import os
import sys
from array import array

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import look_shots as L  # noqa: E402  -- the review kit's renderer

GUITION_S3 = "guition_tapfloor_dark"

# The curated stations: name, style, address (each checked to answer 200
# with audio/mpeg MP3 over plain http to the media client's request).
STATIONS = (
    ("Groove Salad", "Ambient beats", "http://ice1.somafm.com/groovesalad-128-mp3"),
    ("Drone Zone", "Ambient", "http://ice1.somafm.com/dronezone-128-mp3"),
    ("Deep Space One", "Space ambient", "http://ice1.somafm.com/deepspaceone-128-mp3"),
    ("Space Station Soma", "Electronica", "http://ice1.somafm.com/spacestation-128-mp3"),
    ("Mission Control", "Space", "http://ice1.somafm.com/missioncontrol-128-mp3"),
    ("Secret Agent", "Lounge", "http://ice1.somafm.com/secretagent-128-mp3"),
    ("Indie Pop Rocks!", "Indie pop", "http://ice1.somafm.com/indiepop-128-mp3"),
    ("Underground 80s", "Synthpop", "http://ice1.somafm.com/u80s-128-mp3"),
    ("Left Coast 70s", "70s rock", "http://ice1.somafm.com/seventies-128-mp3"),
    ("Sonic Universe", "Jazz", "http://ice1.somafm.com/sonicuniverse-128-mp3"),
    ("Folk Forward", "Folk", "http://ice1.somafm.com/folkfwd-128-mp3"),
    ("Thistle Radio", "Celtic", "http://ice1.somafm.com/thistle-128-mp3"),
    ("Radio Paradise", "Eclectic", "http://stream.radioparadise.com/mp3-128"),
    ("Radio Swiss Jazz", "Jazz", "http://stream.srg-ssr.ch/m/rsj/mp3_128"),
)

# A station's art: a colour field, two MOY64 colours top to bottom.
ART = {"Groove Salad": (58, 40), "Drone Zone": (60, 22), "Deep Space One": (61, 43),
       "Space Station Soma": (59, 42), "Mission Control": (54, 12),
       "Secret Agent": (56, 36), "Road trip mix": (57, 37)}

# The music folder: kind, name, length (a playlist's: its songs).
FILES = (
    ("song", "Beach trip", "3:41"),
    ("list", "Road trip mix", "12 songs"),
    ("song", "Birthday song", "2:05"),
    ("song", "Dragon theme", "4:12"),
    ("list", "Sleepy time", "8 songs"),
    ("song", "Grandma's piano", "1:58"),
    ("song", "Space walk", "5:20"),
)

# What `moybyte.media.now()` answers in a scene, trimmed to what draws, and
# for a file the place and length the build adds to it. One moment's volume
# is one level everywhere it shows.
RADIO = {"state": "playing", "paused": False, "station": "Groove Salad",
         "title": "Boards of Canada - Dayvan Cowboy", "entry": 0, "entries": 1,
         "volume": 60, "kind": "radio"}
RADIO_PAUSED = dict(RADIO, paused=True)
SONG = {"state": "playing", "paused": True, "station": "Road trip mix",
        "title": "Sunny day drive", "entry": 2, "entries": 12, "volume": 40, "kind": "list",
        "at": 72, "len": 221}
BUFFERING = dict(RADIO, state="opening", title="", station="Drone Zone")
FAILED = dict(RADIO, state="failed", title="", station="Secret Agent")

TABS = ("Radio", "My music")
# How `ui.field` stands its box inside its rect: x inset, then the box's y
# offset, height lost, radius (the danger border is drawn on it).
FIELD_INSET = 4
FIELD_BOX = (0, 2, 4, 4)
MODE = ["dark"]
# A focused row's accent bar, in px at the scale (it carries focus alone,
# with no fill: a fill is selection's).
BAR_W = [4]
# An id nothing has: the toolkit then draws no focus mark of today's look.
NO_FOCUS = "-"


def _artist_title(text):
    """An ICY StreamTitle's "Artist - Title" as (title, artist)."""
    if " - " in text:
        a, t = text.split(" - ", 1)
        return t, a
    return text, ""


def _clock(s):
    return "%d:%02d" % (s // 60, s % 60)


def _lines(now):
    """The media card's two lines, in one order everywhere: the title, then
    the artist and the station (or the list and its place)."""
    if now["state"] == "opening":
        return now["station"], "Connecting..."
    if now["state"] == "failed":
        return now["station"], "Not answering"
    title, artist = _artist_title(now["title"])
    if now["kind"] == "list":
        return title, "%s · %d of %d" % (now["station"], now["entry"] + 1, now["entries"])
    return title, (artist + " · " if artist else "") + now["station"]


def _fit(meta, w, now):
    """A line too wide for `w` drops a part at its separator, never cut
    inside one: a station keeps its name, a list its own."""
    from runtime import ui
    if " · " not in meta or ui.text_size(meta)[0] <= w:
        return meta
    parts = meta.split(" · ")
    return parts[-1] if now["kind"] == "radio" else parts[0]


def _state(now, name, kind):
    """None, "playing" or "paused": the row `name` of `kind` against what plays."""
    if not now or now["station"] != name or now["kind"] != kind:
        return None
    if now["state"] != "playing":
        return "opening" if now["state"] == "opening" else "paused"
    return "paused" if now["paused"] else "playing"


def rgb(i):
    """Palette index `i` as the glass shows it (RGB565, widened)."""
    from device.device_canvas import MOY64_RGB
    r, g, b = MOY64_RGB[3 * i:3 * i + 3]
    return (r >> 3) * 255 // 31, (g >> 2) * 255 // 63, (b >> 3) * 255 // 31


def _ink(ws, i):
    """Theme colour `i` as the glass shows it, read back off the glass."""
    cv = ws.glass_canvas
    keep = bytes(cv._buf[:2])
    cv.rect(0, 0, 1, 1, i)
    v = (cv._buf[0] << 8) | cv._buf[1]
    cv._buf[:2] = keep
    return (v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31


def index_of(want):
    """The MOY64 index nearest `want`."""
    return min(range(64), key=lambda i: sum((a - b) ** 2 for a, b in zip(rgb(i), want)))


def paused_ink(th):
    """The paused state's ink: the true dim between the text and the ground
    (a theme role the build adds; MOY64's 50 in the dark, ink_dim in the light)."""
    return 50 if MODE[0] == "dark" else th["ink_dim"]


# -- glyphs and art: the icon set's missing slots, drawn as the build adds them -----

def glyph(cv, name, rect, c, fs, size=12, bg=None):
    """A glyph centred in `rect`, `size` px tall at scale 1: the transport's
    (play, pause, prev, next, stop, retry), the volume's (minus, plus,
    speaker), a list's, the chevron."""
    x, y, w, h = rect
    s = size * fs
    cx, cy = x + w // 2, y + h // 2
    t = cy - s // 2
    if name == "pause":
        b = max(2 * fs, s // 3)
        cv.rect(cx - b - fs, t, b, s, c)
        cv.rect(cx + fs, t, b, s, c)
    elif name == "play":
        tw = s * 5 // 6
        x0 = cx - tw // 2 + fs
        cv.tri(x0, t, x0, t + s - 1, x0 + tw, cy, c)
    elif name == "stop":
        cv.rect(cx - s // 2 + fs, t + fs, s - 2 * fs, s - 2 * fs, c)
    elif name in ("next", "prev"):
        d = 1 if name == "next" else -1
        tw = s * 2 // 3
        x0 = cx - d * (tw // 2 + fs)
        cv.tri(x0, t, x0, t + s - 1, x0 + d * tw, cy, c)
        cv.rect(x0 + d * tw + (0 if d > 0 else -2 * fs), t, 2 * fs, s, c)
    elif name in ("minus", "plus"):
        cv.rect(cx - s // 2, cy - fs, s, 2 * fs, c)
        if name == "plus":
            cv.rect(cx - fs, cy - s // 2, 2 * fs, s, c)
    elif name == "list":
        for k in range(3):
            ly = t + fs + k * (s // 3 + fs)
            cv.rect(cx - s // 2, ly, 2 * fs, 2 * fs, c)
            cv.rect(cx - s // 2 + 4 * fs, ly, s - 4 * fs, 2 * fs, c)
    elif name == "retry":
        r = s // 2
        cv.circ(cx, cy, r, c)
        cv.circ(cx, cy, r - 2 * fs, bg if bg is not None else 0)
        cv.rect(cx, t - fs, r + fs, r, bg if bg is not None else 0)
        cv.tri(cx - fs, t - 2 * fs, cx - fs, t + 4 * fs, cx + 4 * fs, t + fs, c)


def chevron(cv, x, y, h, c, fs, desk=False):
    """The toolkit's one chevron (a value row's, `moy_ui_parts.c`), right
    pointing, its column at x, centred on a line at y `h` tall; its width."""
    from runtime import ui_draw
    w = ui_draw.metrics("value_row")[3 if desk else 2] * (1 if desk else fs)
    half = w // fs - 1
    rows = 2 * half + 1
    y0 = y + (h - rows * fs) // 2
    for i in range(rows):
        o = i if i <= half else rows - 1 - i
        cv.rect(x + o * fs, y0 + i * fs, fs, fs, c)
    return w


def eq(cv, rect, c, fs, lively=True, size=12):
    """The equalizer: the state of what plays. The same four bars on one
    baseline: lively while it plays, 2 px and still while paused."""
    x, y, w, h = rect
    s = size * fs
    bw = max(2 * fs, s // 4)
    gap = max(fs, s // 8)
    heights = (s * 6 // 10, s, s * 4 // 10, s * 8 // 10) if lively else (2 * fs,) * 4
    total = 4 * bw + 3 * gap
    x0 = x + (w - total) // 2
    base = y + h // 2 + s // 2
    for k in range(4):
        cv.rect(x0 + k * (bw + gap), base - heights[k], bw, heights[k], c)


def pulse(cv, rect, on, off, fs):
    """Connecting: three 2 px dots centred on the line, the middle one lit
    (the build steps the lit one along)."""
    x, y, w, h = rect
    d = 2 * fs
    x0 = x + (w - 5 * d) // 2
    cy = y + (h - d) // 2
    for k in range(3):
        cv.rect(x0 + 2 * k * d, cy, d, d, on if k == 1 else off)


def art(cv, th, rect, now, fs, r=8):
    """What plays, as art: the station's (or list's) colour field. It
    carries no state; the lines and the buttons do."""
    x, y, w, h = rect
    a, b = ART.get(now["station"], (60, 22))
    cv.rrect(x, y, w, h, r * fs, (0, 0, ((0, a), (1000, b))))
    cv.circ(x + w * 3 // 4, y + h // 3, max(3 * fs, w // 7), b)


def bar(cv, rect, th, fs):
    """The focused row's accent bar down `rect`'s left edge."""
    x, y, w, h = rect
    cv.rect(x, y + 2 * fs, BAR_W[0] * fs, h - 4 * fs, th["accent"])


def ring(cv, rect, c, fs, r=8):
    """The one focus mark of a button: a 2 px ring 2 px out from it."""
    x, y, w, h = rect
    g = 2 * fs
    cv.rrect(x - 2 * g, y - 2 * g, w + 4 * g, h + 4 * g, (r + 4) * fs, None, ring=c,
             line=2 * fs)


class Kit:
    """One frame's drawing helpers: buttons that wear the one ring, rows
    that wear the row's mark, and the rings drawn last."""

    def __init__(self, cv, th, fs, desk, focus, touch):
        self.cv, self.th, self.fs, self.desk = cv, th, fs, desk
        self.focus = None if touch else focus
        self.rings = []

    def button(self, rect, rid, kind="normal", label="", g=None, gsize=12, dim=False,
               disabled=False):
        """A button; a disabled one is the surface with its border and its
        label in ink_dim (at least 3:1 on its fill, where the toolkit's
        disabled primary washes the accent out)."""
        from runtime import ui
        th = self.th
        if disabled:
            x, y, w, h = rect
            self.cv.rrect(x, y, w, h, 6 * self.fs, th["surface"], ring=th["border"],
                          line=self.fs)
            tw, tl = ui.text_size(label)
            ui.at(x + (w - tw) // 2, y + (h - tl) // 2 + self.fs, tw + self.fs, tl)
            ui.label(label, {"tone": "dim"})
            return False
        ui.at(*rect)
        got = ui.button(label, kind, {"id": rid, "mark": False})
        if g is not None:
            ink = th["value_ink"] if kind == "primary" else \
                th["ink_dim"] if dim or disabled else th["ink"]
            glyph(self.cv, g, rect, ink, self.fs, gsize,
                  bg=th["value"] if kind == "primary" else th["surface_alt"])
        if rid == self.focus:
            self.rings.append(rect)
        return got

    def row_mark(self, rect, rid):
        """A focused row's mark where `rid` holds focus: the accent bar at
        its left, no fill."""
        if rid != self.focus:
            return False
        bar(self.cv, rect, self.th, self.fs)
        return True

    def stop(self, rect, rid, ground):
        """A stop with no face of its own (a card's head, a meter): it takes
        the keys and taps, draws nothing over its `ground` until it has the
        focus, and then wears the row's mark."""
        from runtime import ui
        ui.at(*rect)
        ui.button("", "normal", {"id": rid, "mark": False, "fill": ground, "border": ground})
        self.row_mark(rect, rid)

    def draw_rings(self):
        for r in self.rings:
            ring(self.cv, r, self.th["accent"], self.fs)
        self.rings = []


class MediaCard:
    """What plays, one component at three densities, the same lines, state
    and transport in each: `strip` (a band: art, title over station, a
    chevron, play/pause), `compact` (a card: art, title, the state and the
    artist and station, a chevron over the transport; wide, one row) and
    `full` (Now playing: large art, LIVE or the file's bar, the transport on
    its grid)."""

    def __init__(self, kit, now):
        self.k = kit
        self.now = now

    def _main(self):
        n = self.now
        if n["state"] == "failed":
            return "retry", "Try again"
        return ("play", "Play") if n["paused"] else ("pause", "Pause")

    def transport(self, x, y, w, h, gap):
        """Previous, the main button (play, pause or retry), next, and stop
        after a double gap, on a 1:2:1 + 1 grid across `w`."""
        unit = (w - 4 * gap) // 5
        x += (w - 5 * unit - 4 * gap) // 2
        g, rid = self._main()
        cells = ((unit, "prev", "Previous", "normal", False),
                 (2 * unit, g, rid, "primary", False),
                 (unit, "next", "Next", "normal", False),
                 (unit, "stop", "Stop", "normal", False))
        for k, (cw, gl, i, kind, dim) in enumerate(cells):
            if k == 3:
                x += gap
            self.k.button((x, y, cw, h), i, kind, g=gl, gsize=10 if h < 30 * self.k.fs else 12,
                          dim=dim)
            x += cw + gap

    def state_line(self, x, y, w, size=8, meta=None):
        """The state glyph (the equalizer, or the pulse while connecting)
        and the second line, cut at its separator where it does not fit."""
        from runtime import ui
        k, n = self.k, self.now
        fs = k.fs
        th = k.th
        if meta is None:
            meta = _lines(n)[1]
        lh = ui.text_size("Ag")[1] + 2 * fs
        tx = x
        gw = size * 2 * fs
        if n["state"] == "playing":
            eq(k.cv, (x, y, gw, lh), th["value"] if not n["paused"]
               else paused_ink(th), fs, not n["paused"], size)
            tx = x + gw + 2 * fs
        elif n["state"] == "opening":
            pulse(k.cv, (x, y, gw, lh), th["value"], paused_ink(th), fs)
            tx = x + gw + 2 * fs
        meta = _fit(meta, x + w - tx, n)
        ui.at(tx, y, x + w - tx, lh)
        ui.label(meta, {"tone": "danger"} if n["state"] == "failed" else {"tone": "dim"})

    def head(self, x, y, w, a, title_face="ui", chev=True, ground="surface"):
        """Art, the title, the state line, and the chevron: one stop that
        opens the app."""
        from runtime import ui
        k, n = self.k, self.now
        fs = k.fs
        if chev:
            k.stop((x - 4 * fs, y - 4 * fs, w + 8 * fs, a + 8 * fs), "Open Music", ground)
        art(k.cv, k.th, (x, y, a, a), n, fs, 6 if a < 48 * fs else 8)
        tx = x + a + 8 * fs
        cw = 6 * fs if chev else 0
        tw = x + w - tx - cw - (6 * fs if chev else 0)
        title, _meta = _lines(n)
        lh = ui.text_size("Ag")[1] + 2 * fs
        ty = y + (a - 2 * lh) // 2
        ui.at(tx, ty, tw, lh)
        ui.label(title, {"face": title_face})
        self.state_line(tx, ty + lh, tw)
        if chev:
            chevron(k.cv, x + w - cw, y, a, k.th["ink_dim"], fs, k.desk)

    def compact(self, x, y, w):
        """The compact card at (x, y), `w` wide: its height."""
        k = self.k
        fs = k.fs
        p = 8 * fs
        a = 36 * fs
        bh = 26 * fs
        h = p + a + 6 * fs + bh + p
        k.cv.rrect(x, y, w, h, 8 * fs, k.th["surface"], ring=k.th["border"], line=fs)
        self.head(x + p, y + p, w - 2 * p, a)
        self.transport(x + p, y + p + a + 6 * fs, w - 2 * p, bh, 6 * fs)
        return h

    def strip(self, x, y, w, h):
        """The strip: a band over the page's foot."""
        from runtime import ui
        k, n = self.k, self.now
        fs = k.fs
        th = k.th
        k.cv.rect(x, y, w, h, th["surface_alt"])
        k.cv.rect(x, y, w, fs, th["border"])
        bw = 34 * fs
        a = h - 10 * fs
        hx, hy, hw = x + 8 * fs, y + 5 * fs, w - bw - 24 * fs
        k.stop((x, y + fs, hw + 12 * fs, h - fs), "Now playing", "surface_alt")
        art(k.cv, th, (hx, hy, a, a), n, fs, 4)
        tx = hx + a + 8 * fs
        cw = 6 * fs
        title, meta = _lines(n)
        lh = ui.text_size("Ag")[1] + fs
        ty = y + (h - 2 * lh) // 2
        ui.at(tx, ty, hx + hw - tx - cw - 6 * fs, lh)
        ui.label(title, {"face": "ui"})
        self.state_line(tx, ty + lh, hx + hw - tx - cw - 6 * fs, 7)
        chevron(k.cv, hx + hw - cw, y, h, th["ink_dim"], fs)
        g, rid = self._main()
        k.button((x + w - bw - 8 * fs, y + 6 * fs, bw, h - 12 * fs), "strip-" + rid, "tonal",
                 g=g, gsize=10)

    def wide(self, x, y, w, h, vol=None):
        """The compact card laid in one row (the desk window's foot): art
        and lines, the transport, then the volume."""
        k = self.k
        trans = 5 * 40 + 4 * 8
        vol_w = 200 if vol is not None else 0
        bx = x + w - vol_w - (20 if vol is not None else 0) - trans
        self.head(x, y, bx - 12 - x, h, "title", chev=False)
        self.transport(bx, y + (h - 34) // 2, trans, 34, 8)
        if vol is not None:
            volume_row(k, x + w - vol_w, y + (h - 32) // 2, vol_w, 32, vol, "surface")

    def full(self, x, y, w, a):
        """Now playing's head: the art, the title, two lines (the artist,
        then the station; the list, then the place), LIVE or the file's
        bar; its height."""
        from runtime import ui
        k, n = self.k, self.now
        fs = k.fs
        art(k.cv, k.th, (x, y, a, a), n, fs)
        tx = x + a + 12 * fs
        tw = x + w - tx
        title, meta = _lines(n)
        first, second = meta.split(" · ", 1) if " · " in meta else (meta, "")
        ui.at(tx, y, tw, 20 * fs)
        ui.label(title, {"face": "title"})
        self.state_line(tx, y + 20 * fs, tw, meta=first)
        if second:
            ui.at(tx, y + 37 * fs, tw, 18 * fs)
            ui.label(second, {"tone": "dim"})
        self.status(tx, y + 56 * fs, tw)
        return a

    def status(self, x, y, w):
        """LIVE for a station, a file's bar and its time."""
        from runtime import ui
        k, n = self.k, self.now
        fs = k.fs
        th = k.th
        cv = k.cv
        if n["state"] != "playing":
            return
        if n["kind"] == "radio":
            live = not n["paused"]
            ink = th["value"] if live else paused_ink(th)
            cy = y + 8 * fs
            for i, half in enumerate((0, 1, 2, 3, 2, 1, 0)):
                cv.rect(x + (3 - half) * fs, cy - 3 * fs + i * fs, (2 * half + 1) * fs, fs, ink)
            ui.at(x + 11 * fs, y, 60 * fs, 18 * fs)
            ui.label("LIVE", {"tone": "value"} if live else {"tone": "dim"})
            return
        t = "%s / %s" % (_clock(n["at"]), _clock(n["len"]))
        tw = ui.text_size(t)[0]
        bw = w - tw - 10 * fs
        cv.rrect(x, y + 8 * fs, bw, 4 * fs, 2 * fs, th["border"])
        cv.rrect(x, y + 8 * fs, bw * n["at"] // n["len"], 4 * fs, 2 * fs, th["value"])
        ui.at(x + bw + 8 * fs, y, tw + 2 * fs, 18 * fs)
        ui.label(t, {"tone": "dim"})


def volume_row(k, x, y, w, h, level, ground="panel"):
    """The volume: minus, the speaker and its meter (ten steps across what
    is left), plus. The meter is one stop."""
    fs = k.fs
    th = k.th
    bw = 32 * fs
    k.button((x, y, bw, h), "Quieter", g="minus", gsize=10)
    k.button((x + w - bw, y, bw, h), "Louder", g="plus", gsize=10)
    mx, mw = x + bw + 6 * fs, w - 2 * bw - 12 * fs
    k.stop((mx, y, mw, h), "Volume", ground)
    from runtime import ui
    isz = 16 * fs
    ui.at(mx + 6 * fs, y + (h - 18 * fs) // 2 + 2 * fs, isz + 2 * fs, 18 * fs)
    ui.icon("volume")
    ui.label(" ", {"face": "ui"})
    sx = mx + 6 * fs + isz + 6 * fs
    sw = mx + mw - 6 * fs - sx
    gap = 2 * fs
    seg = (sw - 9 * gap) // 10
    sh = 8 * fs
    for i in range(10):
        k.cv.rect(sx + i * (seg + gap), y + (h - sh) // 2, seg, sh,
                  th["value"] if i < level else th["border"])


class MusicMock:
    """A stand-in for the Music app: one scene's page, drawn with the verbs."""

    id = "music"
    domain = "system"
    TITLE = "Music"
    NEEDS = ("surface", "theme", "damage")
    UI = True

    def __init__(self, ctx, scene):
        self.ctx = ctx
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._ui = ctx.ui
        self.s = scene
        self._first = True
        self._r = array("i", [0] * 4)

    def _toolkit_focus(self):
        """What the toolkit is told has focus: a field (its caret), else
        nothing, the marks being drawn here (`Kit`) in the new grammar."""
        return self.s.get("focus") if self.s["page"] == "add" else NO_FOCUS

    def is_app(self, cart):
        return False

    def open(self):
        self._ui.reset()
        self._first = True

    def relayout(self, w, h, fs, cs=None):
        pass

    def handle_input(self, i):
        self._ui.keys(i)
        return True

    def handle_pointer(self, px, py, click):
        self._ui.point(px, py, click)
        return True

    # -- the frame ------------------------------------------------------------

    def draw(self, dt):
        from runtime import ui
        from runtime.layout_base import metric_class, DESK, content_of, bar_rows
        cv = self._surf.canvas()
        th = self._theme.colors()
        self.fs = max(1, int(getattr(cv, "font_scale", 1)))
        self.desk = metric_class() == DESK
        self.win = bool(self._surf.windowed())
        self.touch = ui.input_kind() == "touch"
        self.cv, self.th = cv, th
        self.k = Kit(cv, th, self.fs, self.desk, self.s.get("focus"), self.touch)
        self._ui.begin(cv, th, (0, 0, cv.w, cv.h), self.desk, self.win)
        ui.page("", {"nav": False} if self.s["page"] == "now" else None)
        cs = max(self.fs, int(self._surf.chrome_scale() or self.fs))
        page = content_of(cv.w, cv.h, 0 if self.win else bar_rows(cs))
        if self._first:
            ui.focus(self._toolkit_focus())
        getattr(self, "_" + self.s["page"])(page)
        if self._first:
            ui.focus(self._toolkit_focus())
        self.k.draw_rings()
        self._first = False

    def _hints(self, page, *choices):
        """The hint strip, 8 px in from either edge: the first of `choices`
        that fits, Back always last."""
        from runtime import ui
        if self.desk:
            return page
        items = choices[-1]
        for c in choices:
            if ui.hints_width(c) <= page[2] - 6 * self.fs:
                items = c
                break
        if ui.hints_width(items) > 0:
            band, page = ui.cut_bottom(page, 20 * self.fs)
            ui.at(band[0] + 3 * self.fs, band[1], band[2] - 6 * self.fs, band[3])
            ui.hints(items)
        return page

    def _row_h(self):
        from runtime import ui_draw
        return ui_draw.metrics("value_row")[17 if self.desk else 16] * self.fs

    def _tabs(self, page):
        """The two tabs, 6 px above and below, the toolkit's: the open one
        filled (selected), no stop of their own (Q E, L R or a tap switch
        them)."""
        from runtime import ui
        fs = self.fs
        band, page = ui.cut_top(page, self._row_h() + 12 * fs)
        ui.at(band[0] + 8 * fs, band[1] + 6 * fs, band[2] - 16 * fs, band[3] - 12 * fs)
        ui.tabs("tabs", TABS, self.s.get("tab", 0), {"mark": False})
        self.cv.rect(band[0], band[1] + band[3] - fs, band[2], fs, self.th["border"])
        return (page[0], page[1] + 4 * fs, page[2], page[3] - 4 * fs)

    # -- rows -------------------------------------------------------------------

    def _station_rows(self):
        now = self.s.get("now")
        rows = [("new", "Add a station", "", "lead")]
        for name, style, _url in STATIONS:
            rows.append(("music", name, style, _state(now, name, "radio")))
        return rows

    def _file_rows(self, desk=False):
        now = self.s.get("now")
        rows = []
        for kind, name, length in FILES:
            st = _state(now, name, "list" if kind == "list" else "song")
            row = ("music" if kind == "song" else "list", name,
                   "Playlist" if kind == "list" else ("Song" if desk else ""), st)
            rows.append(row + (length,) if desk else row)
        return rows

    def _rows(self, rect, rows, cur, oid):
        """`rows` (icon, name, right, state[, more]) as a table of parts in
        `rect`, cut to whole rows; the thumb 4 px clear of the rows where they
        run on. The rows' right edge."""
        from runtime import ui
        fs = self.fs
        rh = self._row_h()
        x, y, w, h = rect
        shown = max(1, h // rh)
        h = shown * rh
        runs_on = len(rows) > shown
        tw = 2 * fs
        rw = w - (tw + 6 * fs if runs_on else 0)
        keys = [(r[1] + "\t" + r[2] + "\t" + (r[3] or ""),) for r in rows]
        ui.at(x, y, rw, h)
        ui.table("", ("",), keys, -1, {"id": oid, "part": (0, rh)})
        r = self._r
        i = ui.table_part(r)
        while i >= 0:
            if i == cur and self.k.focus == oid:
                bar(self.cv, (r[0], r[1], r[2], rh), self.th, fs)
            self._row(r[0], r[1], r[2], rh, rows[i])
            i = ui.table_part(r)
        if runs_on:
            th = max(10 * fs, h * shown // len(rows))
            self.cv.rect(x + w - tw - 2 * fs, y + 2 * fs, tw, h - 4 * fs, self.th["border"])
            self.cv.rect(x + w - tw - 2 * fs, y + 2 * fs, tw, th, self.th["ink_dim"])
        return x + rw

    def _row(self, cx, cy, cw, rh, row):
        """One row: its leading icon (the equalizer where it plays) centred
        on its words, its name, its right column."""
        from runtime import ui
        fs = self.fs
        icon, name, right, st = row[0], row[1], row[2], row[3]
        more = row[4] if len(row) > 4 else None
        pad = 10 if self.desk else 8 * fs
        isz = 16 if self.desk else 16 * fs
        ix = cx + pad
        th = self.th
        if st in ("playing", "paused"):
            eq(self.cv, (ix, cy, isz, rh), th["value"] if st == "playing" else paused_ink(th),
               fs, st == "playing", 10)
        elif st == "opening":
            pulse(self.cv, (ix, cy, isz, rh), th["value"], paused_ink(th), fs)
        elif icon == "list":
            glyph(self.cv, "list", (ix, cy, isz, rh), th["ink"], fs, 10)
        elif icon:
            ui.at(ix, cy, isz + 2 * fs, rh)
            ui.icon(icon)
            ui.label(" ", {"tone": "value"} if st in ("lead", "sel") else {"face": "ui"})
        tx = ix + isz + 8 * fs
        ui.at(tx, cy, cw - (tx - cx), rh)
        ui.label(name, {"tone": "value"} if st and st != "lead" else {"face": "ui"})
        rx = cx + cw - pad
        if more is not None:
            mw = ui.text_size(more)[0]
            ui.at(rx - mw, cy, mw + fs, rh)
            ui.label(more, {"tone": "dim"})
            rx -= 90
        if right:
            rw = ui.text_size(right)[0]
            ui.at(rx - rw, cy, rw + fs, rh)
            ui.label(right, {"tone": "dim"})

    def _strip(self, page):
        from runtime import ui
        now = self.s.get("now")
        if not now:
            return page
        h = 34 * self.fs
        strip, page = ui.cut_bottom(page, h)
        MediaCard(self.k, now).strip(strip[0], strip[1], strip[2], h)
        return page

    # -- the handheld pages ------------------------------------------------------

    def _list_hints(self, other):
        verb = self.s.get("verb", "Play")
        if self.s.get("now"):
            return (("confirm", verb, "tabs", other, "more", "Player", "back", "Back"),
                    ("confirm", verb, "more", "Player", "back", "Back"))
        return (("confirm", verb, "tabs", other, "back", "Back"),)

    def _radio(self, page):
        page = self._hints(page, *self._list_hints("My music"))
        page = self._tabs(page)
        page = self._strip(page)
        self._rows(page, self._station_rows(), self.s.get("cur", -1), "stations")

    def _empty_at(self, page):
        """Where an empty state stands: one top for every one of them."""
        x, y, w, h = page
        return (x, y - 4 * self.fs, w, h + 4 * self.fs)

    def _files(self, page):
        from runtime import ui
        empty = self.s.get("empty")
        if empty:
            page = self._hints(page, ("tabs", "Radio", "back", "Back"))
        else:
            page = self._hints(page, *self._list_hints("Radio"))
        page = self._tabs(page)
        page = self._strip(page)
        if empty:
            ui.at(*self._empty_at(page))
            if empty == "nosd":
                ui.empty("folder", "No SD card",
                         ("Put in an SD card with your songs on it.",
                          "Radio plays without one."))
            else:
                ui.empty("music", "No music yet",
                         ("Put MP3 songs in the Music folder", "on the SD card."))
            return
        self._rows(page, self._file_rows(), self.s.get("cur", -1), "files")

    def _now(self, page):
        """Now playing, the card's full density on a 12 px grid from x 12 to
        308. Its keys are the page's own: Enter plays, pauses or tries
        again, Q E (L R) change the station or the song, W S (up down) the
        volume, M (Y) goes to the list, Back leaves."""
        from runtime import ui
        now = self.s["now"]
        fs = self.fs
        radio = now["kind"] == "radio"
        ui.bar("Now playing", (), (), True)
        card = MediaCard(self.k, now)
        verb = card._main()[1]
        skip = "Station" if radio else "Song"
        page = self._hints(page,
                           ("confirm", verb, "tabs", skip, "scroll", "Volume", "more", "List",
                            "back", "Back"),
                           ("confirm", verb, "tabs", "Skip", "scroll", "Volume", "more", "List",
                            "back", "Back"),
                           ("confirm", verb, "tabs", skip, "scroll", "Volume", "back", "Back"),
                           ("confirm", verb, "scroll", "Volume", "more", "List", "back", "Back"),
                           ("confirm", verb, "scroll", "Volume", "back", "Back"))
        x, y, w, h = page
        g = 12 * fs
        ix, iw = x + g, w - 2 * g
        a = (88 if self.touch else 72) * fs
        bh = 40 if self.touch else 36
        vh = 32 if self.touch else 28
        block = a + g + bh * fs + g + vh * fs
        iy = y + max(g, (h - block) // 2)
        card.full(ix, iy, iw, a)
        top = iy + a + g
        card.transport(ix, top, iw, bh * fs, g)
        volume_row(self.k, ix, top + bh * fs + g, iw, vh * fs, now["volume"] // 10)

    def _add(self, page):
        """Add a station, a page of its own on the 12 px gutter: each
        field's label over it, its line under it at its x (what plays, or
        why this one will not), Cancel and Add at the foot. Enter goes to
        the next field and then adds; the console's cancel key cancels, and
        no hint names a letter key while a field has the keys."""
        from runtime import ui
        fs = self.fs
        th = self.th
        err = self.s.get("err")
        ui.bar("Add a station", (), (), True)
        page = self._hints(page, ("confirm", "Next", "cancel", "Cancel"))
        x, y, w, h = page
        g = 12 * fs
        fx, fw = x + g, w - 2 * g
        rh = self._row_h() + 4 * fs
        lh = ui.text_size("Ag")[1] + 2 * fs
        cy = y + 8 * fs
        for label, key, help_, max_, hint in (
                ("Address", "url", err or "An MP3 stream or playlist", 200, "http://"),
                ("Name", "name", None, 32, "Optional")):
            ui.at(fx, cy, fw, lh)
            ui.label(label, {"face": "caption", "tone": "dim"})
            cy += lh
            focused = self.s.get("focus") == label
            if focused:
                bar(self.cv, (fx - (BAR_W[0] + 4) * fs, cy, w, rh), th, fs)
            ui.at(fx - FIELD_INSET * fs, cy, fw + 2 * FIELD_INSET * fs, rh)
            ui.field("", self.s.get(key, ""), max_, {"hint": hint, "mark": False, "id": label})
            if err and key == "url":
                _bx, by, bh, br = FIELD_BOX
                self.cv.rrect(fx, cy + by * fs, fw, rh - bh * fs, br * fs, None,
                              ring=th["danger"], line=fs)
            cy += rh + 2 * fs
            if help_:
                ui.at(fx, cy, fw, 2 * lh if err else lh)
                ui.label(help_, {"tone": "danger", "wrap": True, "lines": 2} if err
                         else {"tone": "dim"})
                cy += (2 * lh if err else lh)
            cy += 6 * fs
        bw = 88 * fs
        by = y + h - 28 * fs - g
        self.k.button((x + w - g - bw, by, bw, 28 * fs), "Add", "primary", "Add",
                      disabled=bool(err))
        self.k.button((x + w - 2 * g - 2 * bw + 4 * fs, by, bw, 28 * fs), "Cancel",
                      "normal", "Cancel")

    def _noaudio(self, page):
        from runtime import ui
        fs = self.fs
        x, y, w, h = page
        top = y + max(0, h // 2 - 90 * fs)
        ui.at(x, top, w, y + h - top)
        under = ui.empty("mute", "No sound on this console", ())
        for k, line in enumerate(("This console has no speaker,",
                                  "so it can't play music or radio.")):
            ui.at(x, under + 2 * fs + k * 18 * fs, w, 16 * fs)
            ui.label(line, {"align": "center", "face": "ui"})
        bw = 160 * fs
        ui.at(x + (w - bw) // 2, under + 48 * fs, bw, 40 * fs)
        ui.icon("home")
        ui.button("Back to home", "primary", {"id": "Back to home"})

    def _nowifi(self, page):
        from runtime import ui
        fs = self.fs
        page = self._hints(page, ("confirm", "Open", "tabs", "My music", "back", "Back"))
        page = self._tabs(page)
        x, y, w, h = self._empty_at(page)
        ui.at(x, y, w, h)
        under = ui.empty("wifi_off", "Radio needs Wi-Fi",
                         ("Turn on Wi-Fi to listen to stations.",
                          "Your own music plays without it."))
        bw = 150 * fs
        bh = self._row_h() + 4 * fs
        by = min(under, y + h - bh - 8 * fs)
        ui.at(x + (w - bw) // 2, by, bw, bh)
        ui.icon("wifi")
        ui.button("Wi-Fi settings", "primary", {"id": "Wi-Fi settings", "mark": False})
        if self.k.focus == "Wi-Fi settings":
            self.k.rings.append((x + (w - bw) // 2, by, bw, bh))

    # -- the desk window ---------------------------------------------------------

    def _desk(self, page):
        from runtime import ui
        cv, th = self.cv, self.th
        foot, body = ui.cut_bottom(page, 88)
        side, main = ui.cut_left(body, 168)
        sx, sy, sw, sh = ui.inset(side, 6, 6)
        ui.at(sx + 4, sy, sw, 18)
        ui.label("Library", {"face": "caption", "tone": "dim"})
        tab = self.s.get("tab", 0)
        rows = [("volume", "Radio", "", "sel" if tab == 0 else None),
                ("music", "My music", "", "sel" if tab == 1 else None)]
        rh = self._row_h()
        keys = [(r[1],) for r in rows]
        ui.at(sx, sy + 20, sw, 2 * rh)
        ui.table("", ("",), keys, tab, {"id": "Library", "part": (0, rh)})
        r = self._r
        i = ui.table_part(r)
        while i >= 0:
            row = rows[i]
            self._row(r[0], r[1], r[2], rh, (row[0], row[1], "", row[3]))
            i = ui.table_part(r)
        cv.rect(side[0] + side[2], side[1], 1, side[3], th["border"])
        mx, my, mw, mh = ui.inset(main, 4, 6)
        head = 20
        files = tab == 1
        body = (mx, my + head, mw, mh - head)
        rows = self._file_rows(desk=True) if files else self._station_rows()
        right = self._rows(body, rows, self.s.get("cur", -1), "files" if files else "stations")
        heads = ("Name", "Kind", "Length") if files else ("Station", "Style")
        ui.at(mx + 10 + 16 + 8, my, 120, head)
        ui.label(heads[0], {"face": "caption", "tone": "dim"})
        rx = right - 2
        for text in reversed(heads[1:]):
            tw = ui.text_size(text)[0]
            ui.at(rx - tw, my, tw + 2, head)
            ui.label(text, {"face": "caption", "tone": "dim"})
            rx -= 90
        cv.rect(foot[0], foot[1], foot[2], 1, th["border"])
        fx, fy, fw, fh = ui.inset(foot, 16, 12)
        now = self.s["now"]
        MediaCard(self.k, now).wide(fx, fy, fw, fh, now["volume"] // 10)


# -- the shell's status items and control center ----------------------------------


class _Section:
    """A popover component over its own UI context, as `popover.py`'s are."""

    def __init__(self, ws, scene):
        from runtime import ui_draw
        self.ws = ws
        self.s = scene
        self.host = ui_draw.AppHost()
        self.first = True

    def keys(self):
        return self.s.get("keys") or self.KEYS

    def opened(self):
        self.host.reset()
        self.first = True

    def handle_input(self, i):
        return "local" if self.host.keys(i) else None

    def click(self, px, py, rect):
        self.host.point(px, py, True)
        return "local"

    def typing(self):
        return False

    def closed(self):
        self.host.reset()

    def _kit(self, cv, desk):
        th = self.ws.theme_colors
        fs = 1 if desk else self.ws.layout.fs
        return Kit(cv, th, fs, desk, self.s.get("cc_focus"), self.s.get("input") == "touch")


class ControlCenter(_Section):
    """The handheld's control center: the console's card (volume,
    brightness, Wi-Fi), then each app's card under its caption (Music's
    media card first), 12 px in from the card's sides. Its first view ends
    6 px under Music's transport; it scrolls a card at a time, and where its
    column runs on past an edge an 8 px fade over its background says so."""

    KEYS = ("change", "Change", "scroll", "Row", "back", "Close")

    def size(self):
        fs = self.ws.layout.fs
        return (232 * fs, self._first_view())

    def _layout(self):
        """Each block's (kind, y, h) down the column from the card's top,
        and the column's end."""
        fs = self.ws.layout.fs
        rh = 18 * fs
        cap = 14 * fs
        out = []
        y = 6 * fs
        sys_h = 3 * rh + 8 * fs
        out.append(("console", y, sys_h))
        y += sys_h + 6 * fs
        if self.s.get("now"):
            out.append(("cap:Music", y, cap))
            y += cap
            ch = 8 * fs + 36 * fs + 6 * fs + 26 * fs + 8 * fs
            out.append(("music", y, ch))
            y += ch + 6 * fs
        out.append(("cap:Downloads", y, cap))
        y += cap
        out.append(("downloads", y, 2 * rh + 8 * fs))
        y += 2 * rh + 8 * fs + 6 * fs
        return out, y

    def _first_view(self):
        blocks, end = self._layout()
        for kind, y, h in blocks:
            if kind == "music":
                return y + h + 6 * self.ws.layout.fs
        return end

    def draw(self, cv, rect):
        from runtime import ui
        ws = self.ws
        fs = ws.layout.fs
        th = ws.theme_colors
        k = self._kit(cv, False)
        x, y, w, h = rect
        p = 12 * fs
        self.host.begin(cv, th, rect, False)
        if self.first:
            ui.focus(NO_FOCUS)
        blocks, end = self._layout()
        off = 0
        if self.s.get("scrolled"):
            off = [b for b in blocks if b[0] == "cap:Music"][0][1] - 14 * fs
        rh = 18 * fs
        cx, cw = x + p, w - 2 * p
        focus = k.focus or (None if self.s.get("input") == "touch" or "cc_focus" in self.s
                            else "Volume")
        cv.clip(x + fs, y + fs, w - 2 * fs, h - 2 * fs)
        for kind, by, bh in blocks:
            top = y + by - off
            if top + bh < y or top > y + h:
                continue
            if kind.startswith("cap:"):
                ui.at(cx + 8 * fs, top, cw, bh)
                ui.label(kind[4:], {"face": "caption", "tone": "dim"})
            elif kind == "music":
                MediaCard(k, self.s["now"]).compact(cx, top, cw)
            else:
                cv.rrect(cx, top, cw, bh, 8 * fs, th["surface"], ring=th["border"], line=fs)
                ry = top + 4 * fs
                rows = ((("volume", "slider", "Volume", 6), ("spark", "slider", "Brightness", 4),
                         ("wifi", "link", "Wi-Fi", "Home"))
                        if kind == "console" else
                        (("export", "progress", "Sky Run", 3), ("folder", "link",
                                                                 "See downloads", "")))
                for icon, verb, label, val in rows:
                    ui.at(cx + 2 * fs, ry, cw - 4 * fs, rh)
                    ui.icon(icon)
                    if verb == "slider":
                        ui.slider(label, val, 0, 10, {"steps": True, "mark": False})
                    elif verb == "link":
                        ui.link(label, val, {"mark": False})
                    else:
                        ui.progress(label, val, 5, {"count": True})
                    if label == focus:
                        bar(cv, (cx + fs, ry, cw, rh), th, fs)
                    ry += rh
        cv.clip()
        if end > h:
            tx, ty, tl = x + w - 6 * fs, y + 8 * fs, h - 16 * fs
            cv.rect(tx, ty, 2 * fs, tl, th["border"])
            cv.rect(tx, ty + tl * off // end, 2 * fs, tl * h // end, th["ink_dim"])
        self.fades = (off > 0, end - off > h)
        if self.first:
            ui.focus(NO_FOCUS)
        k.draw_rings()
        self.first = False
        if self.host.owed or self.host.tap == 2 or self.host.moved:
            ws.popover_touch()


class MusicMenu(_Section):
    """Music's status item's menu on the desk: the media card (compact),
    then the volume. Its hints follow the focus."""

    KEYS = ("change", "Change", "back", "Close")

    def size(self):
        return (404, 184)

    def draw(self, cv, rect):
        from runtime import ui
        ws = self.ws
        th = ws.theme_colors
        k = self._kit(cv, True)
        self.host.begin(cv, th, rect, True)
        if self.first:
            ui.focus(NO_FOCUS)
        x, y, w, h = rect
        p = 12
        now = self.s["now"]
        ch = MediaCard(k, now).compact(x + p, y + p, w - 2 * p)
        cv.rect(x + p, y + p + ch + 8, w - 2 * p, 1, th["border"])
        volume_row(k, x + p, y + p + ch + 16, w - 2 * p, 30, now["volume"] // 10, "surface")
        f = self.s.get("cc_focus")
        hints = (("confirm", "Pause", "change", "Button", "scroll", "Row", "back", "Close")
                 if f in ("Pause", "Previous", "Next", "Stop") else
                 ("back", "Close"))
        ui.at(x + p, y + h - 24, w - 2 * p, 20)
        ui.hints(hints)
        if self.first:
            ui.focus(NO_FOCUS)
        k.draw_rings()
        self.first = False
        if self.host.owed or self.host.tap == 2 or self.host.moved:
            ws.popover_touch()


def _cluster(ws):
    """The bar's status cluster on the glass, (x, y, w, h, k, gap): Music's
    icon left of the Wi-Fi, through the battery; k the glass's scale."""
    from runtime import shell_layout
    bars = ws.layout.bars
    got = shell_layout.cell_of(bars, "wifi")
    bat = shell_layout.cell_of(bars, "battery")
    if got is None or bat is None:
        return None
    i, (bx, by, bw, bh) = got
    rx, ry = bars[i][1][0], bars[i][1][1]
    k = ws.glass_canvas.w // ws.sys_canvas.w
    desk = ws.sys_canvas.w >= 800
    gap = 10 if desk else 4 * ws.layout.fs
    x0 = rx + bx - bw - gap
    x1 = bars[bat[0]][1][0] + bat[1][0] + bat[1][2]
    return x0 * k, (ry + by) * k, (x1 - x0) * k, bh * k, k


def _bar_status(scene):
    """Music's status icon in the bar: the accent while playing, the paused
    ink while paused, the danger ink when it failed (its desk menu's open
    state, a pill under it, is drawn over the glass by `_desk_marks`)."""
    def post(ws):
        got = _cluster(ws)
        if got is None:
            return
        x, y, w, h, k = got
        cv = ws.glass_canvas
        th = ws.theme_colors
        now = scene.get("now")
        if now:
            ink = th["danger"] if now["state"] == "failed" else \
                paused_ink(th) if now["paused"] else th["value"]
            ws.draw_icon("music", (x, y, h, h), ink, cv=cv)
    return post


def _scrim(ws, img, scene):
    """The control center's scrim (50 % black, its rounded card punched
    out) over the page beside it, and an 8 px fade over its panel's
    background where its column runs on. Bar menus (the desk's) take none."""
    from PIL import Image, ImageDraw
    m = ws.sysmenu
    if not m.open or not isinstance(m.comp, ControlCenter):
        return img
    px, py, pw, ph = m.panel_rect()
    k = ws.glass_canvas.w // ws.sys_canvas.w
    fs = ws.layout.fs
    top = ws.layout.cy * k
    hint = 0 if scene.get("input") == "touch" else 20 * fs * k
    dark = Image.new("RGB", img.size, (0, 0, 0))
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    d.rectangle((0, top, img.size[0], img.size[1] - hint - 1), fill=128)
    d.rounded_rectangle((px * k, py * k, (px + pw) * k - 1, (py + ph) * k - 1),
                        radius=8 * fs * k, fill=0)
    img = Image.composite(dark, img, mask)
    up, down = getattr(m.comp, "fades", (False, False))
    bg = img.getpixel(((px + 4 * fs) * k, (py + ph // 2) * k))
    fw = (pw - 4 * fs) * k
    n = 8 * fs * k
    band = Image.new("RGB", (fw, n), bg)
    grad = Image.linear_gradient("L").resize((fw, n))
    if up:
        img.paste(band, ((px + 2 * fs) * k, py * k), grad.transpose(Image.FLIP_TOP_BOTTOM))
    if down:
        img.paste(band, ((px + 2 * fs) * k, (py + ph) * k - n - 2 * fs * k), grad)
    return img


def _unfill(img, box, ground, to, fill=None):
    """A focused row's fill taken away (its bar stays): in `box` (x0, y0,
    x1, y1) the fill (the commonest colour that is not `ground`) goes back to
    `ground`, the words and the bar on it turn `to` (None: their own ink)
    with their edges blended the same way, and pixels off that blend (an
    icon's) stay."""
    from collections import Counter
    x0, y0, x1, y1 = box
    px = img.load()
    seen = Counter(px[x, y] for x in range(x0, x1) for y in range(y0, y1))
    if fill is None:
        fill = [c for c, _n in seen.most_common() if c != ground][0]
    ink = max(seen, key=lambda c: sum((c[i] - fill[i]) ** 2 for i in range(3)))
    to = to or ink
    d = [ink[i] - fill[i] for i in range(3)]
    dd = sum(v * v for v in d) or 1
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = px[x, y]
            if c == ground:
                continue
            e = [c[i] - fill[i] for i in range(3)]
            t = sum(e[i] * d[i] for i in range(3)) / dd
            if sum(v * v for v in e) < 900:
                t = 0.0
            if sum(v * v for v in e) - t * t * dd > 900:
                continue
            t = min(1.0, max(0.0, t))
            px[x, y] = tuple(int(ground[i] + t * (to[i] - ground[i]) + 0.5) for i in range(3))


def _bar_room(ws, img, scene):
    """The handheld bar the way the build lays it out with Music's item in
    it, the same whether or not the keys are on it:
    - the status cluster stands 6 px left of the clock, and its ring's room
      (3 px of clearance, 2 px of stroke, all round) is always reserved;
    - the tabs' gaps shrink until the last one stands 8 px clear of that;
    - the open tab keeps its pill (selected);
    - with `bar_focus` the cluster wears the ring (focused), and while the
      control center is open it wears the tabs' pill (selected)."""
    from PIL import ImageDraw
    got = _cluster(ws)
    if got is None or ws.sys_canvas.w >= 800:
        return img
    x0, _y, w, h, k = got
    bh = ws.layout.cy * k
    bg = img.getpixel((1, bh // 2))
    acc = _ink(ws, ws.theme_colors["accent"])

    def inked(x):
        return any(img.getpixel((x, y)) != bg for y in range(2, bh - 2))

    def runs(a, b, merge):
        segs = []
        for x in range(a, b):
            if inked(x):
                if segs and x - segs[-1][1] < merge:
                    segs[-1][1] = x + 1
                else:
                    segs.append([x, x + 1])
        return segs
    tabs = runs(30 * k, x0 - 4 * k, 5 * k)
    clock = runs(x0 + w + 5 * k, img.size[0], 5 * k)
    cl = runs(x0 - 4 * k, x0 + w + 5 * k, 99 * k)
    if len(tabs) < 2 or not clock or not cl:
        return img
    a, b = cl[0][0], cl[-1][1]
    ys = [y for y in range(bh) if any(img.getpixel((x, y)) != bg for x in range(a, b))]
    top, bot = ys[0], ys[-1] + 1
    pad, line = 3 * k, 2 * k
    rw = (b - a) + 2 * (pad + line)
    rx = clock[0][0] - 6 * k - rw
    strip = img.crop((0, 0, img.size[0], bh))
    out = img.copy()
    out.paste(bg, (tabs[1][0], 0, clock[0][0], bh))
    out.paste(strip.crop((a, 0, b, bh)), (rx + pad + line, 0))
    first = strip.crop((tabs[0][0], 0, tabs[0][1], bh))
    room = rx - 8 * k - tabs[0][0]
    rest = [strip.crop((sg[0], 0, sg[1], bh)) for sg in tabs[1:]]
    text = first.size[0] + sum(r.size[0] for r in rest)
    gap = min(14 * k, (room - text) // len(rest))
    x = tabs[0][0] + first.size[0] + gap
    for r in rest:
        out.paste(r, (x, 0))
        x += r.size[0] + gap
    dr = ImageDraw.Draw(out)
    m = ws.sysmenu
    if scene.get("bar_focus"):
        dr.rounded_rectangle((rx, top - pad - line, rx + rw - 1, bot + pad + line - 1),
                             radius=6 * k, outline=acc, width=line)
    elif m.open and isinstance(m.comp, ControlCenter):
        _pill_under(out, (rx + line, top - pad, rx + rw - line, bot + pad), bg,
                    _commonest(strip, (tabs[0][0], 0, tabs[0][1], bh), bg), 5 * k)
    return out


def _commonest(img, box, bg):
    """The commonest colour in `box` that is not `bg`: a pill's fill."""
    from collections import Counter
    x0, y0, x1, y1 = box
    seen = Counter(img.getpixel((x, y)) for x in range(x0, x1) for y in range(y0, y1))
    return [c for c, _n in seen.most_common() if c != bg][0]


def _pill_under(img, box, bg, fill, r):
    """A selected cell's fill (the pill the shell's tabs wear) put under
    what `box` holds."""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = box
    keep = img.crop(box)
    mask = Image.new("L", keep.size, 0)
    mp, kp = mask.load(), keep.load()
    for y in range(keep.size[1]):
        for x in range(keep.size[0]):
            if kp[x, y] != bg:
                mp[x, y] = 255
    ImageDraw.Draw(img).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=r, fill=fill)
    img.paste(keep, (x0, y0), mask)


def _desk_marks(ws, img, scene):
    """The desk in the grammar:
    - with Files in front, its focused row wears the bar alone (the fill is
      selection's: Projects, the open place, keeps it);
    - with Music's menu open, its status icon is selected: the task
      buttons' pill under it."""
    out = img.copy()
    px = out.load()
    if scene.get("page") == "desk":
        # the Library's open place is selected, not focused: its fill alone
        acc = _ink(ws, ws.theme_colors["accent"])
        wx, wy = WINDOW[0], WINDOW[1]
        for y in range(wy + 30, wy + 110):
            for x in range(wx, wx + 14):
                if px[x, y] == acc and px[x + 6, y] != acc:
                    px[x, y] = px[x + 6, y]
        return out
    if scene.get("shell") != "desk":
        return out
    if not scene.get("menu"):
        sx, sy = 560, 315
        fill = px[sx, sy]
        xs = [xx for xx in range(sx - 300, sx + 400) if 0 <= xx < out.size[0]
              and px[xx, sy] == fill]
        xa, xb = min(xs), max(xs) + 1
        ya = sy
        while px[sx, ya - 1] == fill:
            ya -= 1
        yb = sy
        while px[sx, yb + 1] == fill:
            yb += 1
        _unfill(out, (xa - 2, ya - 2, xb + 2, yb + 3), px[xa + 1, ya - 3], None, fill=fill)
        return out
    bh = ws.layout.cy
    bg = out.getpixel((300, 2))
    x = 30
    while out.getpixel((x, bh // 2)) == bg or out.getpixel((x + 30, bh // 2)) == bg:
        x += 1
    task = _commonest(out, (x, 1, x + 30, bh - 1), bg)
    got = _cluster(ws)
    cx, cy, _w, ch, _k = got
    _pill_under(out, (cx - 6, cy - 3, cx + ch + 6, cy + ch + 3), bg, task, 6)
    return out


def _strip_inset(img):
    """A handheld's hint strip 8 px in from both edges: moved in where it
    starts at the toolkit's 5, its last hint dropped where it runs past."""
    W, H = img.size
    if W != 320:
        return img
    y0 = H - 20
    bg = img.getpixel((W - 1, H - 2))
    cols = [x for x in range(W) if any(img.getpixel((x, y)) != bg for y in range(y0 + 2, H - 2))]
    if not cols:
        return img
    out = img.copy()
    if cols[0] != 8:
        dx = 8 - cols[0]
        band = img.crop((0, y0, W, H))
        out.paste(bg, (0, y0, W, H))
        out.paste(band, (dx, y0))
        cols = [c + dx for c in cols]
    if cols[-1] > W - 9:
        gaps = [cols[i] for i in range(len(cols) - 1)
                if cols[i + 1] - cols[i] > 8 and cols[i] <= W - 9]
        if gaps:
            out.paste(bg, (gaps[-1] + 1, y0, W, H))
    return out


def _battery(ws, img, kind):
    """The bar's battery the way the build draws its two states: below
    15 % all in the danger ink with a sliver of charge, charging with a
    bolt in the accent."""
    from runtime import shell_layout
    from PIL import ImageDraw
    bars = ws.layout.bars
    i, (bx, by, bw, bh) = shell_layout.cell_of(bars, "battery")
    rx, ry = bars[i][1][0] + bx, bars[i][1][1] + by
    bg = img.getpixel((rx - 1, ry + 1))
    ink = max(((img.getpixel((x, y)), 1) for x in range(rx, rx + bw) for y in range(ry, ry + bh)
               if img.getpixel((x, y)) != bg), key=lambda c: sum(c[0]))[0]
    th = ws.theme_colors
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.rectangle((rx, ry, rx + bw - 1, ry + bh - 1), fill=bg)
    cx, cy = rx + bw // 2, ry + bh // 2
    x0, x1 = cx - 7, cx + 5
    body = _ink(ws, th["danger"]) if kind == "low" else ink
    d.rectangle((x0, cy - 4, x1, cy + 3), outline=body)
    d.rectangle((x1 + 1, cy - 1, x1 + 2, cy), fill=body)
    if kind == "low":
        d.rectangle((x0 + 2, cy - 2, x0 + 3, cy + 1), fill=body)
    else:
        acc = _ink(ws, th["accent"])
        d.rectangle((x0 + 2, cy - 2, x1 - 2, cy + 1), fill=acc)
        d.line([(cx + 1, cy - 2), (cx - 1, cy), (cx + 1, cy), (cx - 1, cy + 1)], fill=bg)
    return out


def _wash(ws, keep):
    """The modal wash, drawn into the glass: everything under the dialog
    kept at `keep` of its brightness (0.3: the build's 70 % wash)."""
    from PIL import Image
    cv = ws.glass_canvas
    im = Image.eval(_GLASS[0](ws), lambda v: int(v * keep))
    src = im.tobytes()
    raw = bytearray(len(cv._buf))
    for i in range(0, len(src), 3):
        r, g, b = src[i], src[i + 1], src[i + 2]
        o = i // 3 * 2
        raw[o] = (r & 0xF8) | (g >> 5)
        raw[o + 1] = ((g >> 2) & 7) << 5 | (b >> 3)
    cv._buf[:] = raw


def _grow_card(img, box):
    """The dialog's card 6 px taller at its foot, so the focused button's
    ring stands as far from the card's edge below as above."""
    x, y, w, h = box
    px = img.load()
    inside = px[x + 4, y + h + 6]
    yb = y + h + 6
    while px[x + 4, yb] == inside:
        yb += 1
    xl = x
    while px[xl, yb - 3] == inside:
        xl -= 1
    xr = x + w
    while px[xr, yb - 3] == inside:
        xr += 1
    out = img.copy()
    foot = img.crop((xl - 3, yb - 3, xr + 4, yb + 4))
    row = img.crop((xl - 3, yb - 4, xr + 4, yb - 3))
    for k in range(6):
        out.paste(row, (xl - 3, yb - 3 + k))
    out.paste(foot, (xl - 3, yb + 3))
    return out


def _open_section(comp_cls, anchor):
    """Open `comp_cls` as the popover of bar widget `anchor`."""
    def go(g, ws, scene):
        mod = sys.modules[type(ws).__module__]
        real = mod._component_for
        mod._component_for = lambda w, name: comp_cls(w, scene) if name == anchor else \
            real(w, name)
        try:
            ws.widget_tap(anchor)
        finally:
            mod._component_for = real
        ws._golden_popover = True
    return go


_GLASS = [None]
_BOX = [None]


def _vm_stop(scene):
    """The question before a compiled game that needs the VM stopped while
    the music holds the Wi-Fi: the running set's dialog, its line wrapped,
    Cancel focused with the one ring, its hints following the focus."""
    def post(ws):
        from runtime import ui, ui_draw
        cv = ws.glass_canvas
        th = ws.theme_colors
        fs = ws.layout.fs
        _wash(ws, 0.3)
        host = ui_draw.AppHost()
        host.begin(cv, th, (0, 0, cv.w, cv.h), False)
        for o in ({"mark": False, "wash": False},) * 4:
            # again until the card has sized itself to its wrapped line
            ui.dialog("Stop the music to play Doom?", ("Cancel", "Stop & play"), "danger", o)
            ui.label("Doom needs the music's memory. You can start the music again after.",
                     {"wrap": True, "lines": 3, "face": "ui"})
            ui.dialog()
        box = ui_draw._C.api_box(host.ctx, "Cancel")
        band = (0, cv.h - 20 * fs, cv.w, 20 * fs)
        cv.rect(*band, th["panel"])
        ui.at(*band)
        ui.hints(("confirm", "Cancel", "change", "Button", "back", "Back"))
        if box:
            # the box is where the card stood a pass before: find the button
            im = _GLASS[0](ws)
            x, y, w, h = box
            back = im.getpixel((x + w // 2, y - 3 * fs))
            for dy in range(0, 24 * fs):
                if im.getpixel((x + 4 * fs, y + dy)) != back:
                    box = (x, y + dy, w, h)
                    break
            ring(cv, box, th["accent"], fs)
            _BOX[0] = box
    return post


class _NoWifi:
    """The radio off: no network, the bar's Wi-Fi drawn off."""

    def status(self):
        return (False, None, None)

    def scan(self):
        return []

    def known(self):
        return ["Home"]


# -- the scenes --------------------------------------------------------------------

D, DL = ("dark",), ("dark", "light")
CC = _open_section(ControlCenter, "battery")
MENU = _open_section(MusicMenu, "battery")

# name -> (config, input, modes, scene). The desk scenes open the app in a
# window; a `shell` scene is the home (or the desk) with what `then` opens.
SCENES = {
    "handheld_radio": (L.HANDHELD, None, DL, {
        "page": "radio", "cur": 3, "now": RADIO, "focus": "stations"}),
    "handheld_radio-pad": (L.HANDHELD, "pad", D, {
        "page": "radio", "cur": 1, "now": RADIO, "focus": "stations", "verb": "Pause"}),
    "handheld_radio-touch": (L.HANDHELD, "touch", D, {"page": "radio", "now": RADIO}),
    "handheld_radio-buffering": (L.HANDHELD, None, D, {
        "page": "radio", "cur": 2, "now": BUFFERING, "focus": "stations"}),
    "handheld_radio-nowifi": (L.HANDHELD, None, DL, {
        "page": "nowifi", "focus": "Wi-Fi settings", "wifi": "off"}),
    "handheld_files": (L.HANDHELD, None, DL, {
        "page": "files", "tab": 1, "cur": 2, "now": SONG, "focus": "files"}),
    "handheld_files-empty": (L.HANDHELD, None, DL, {"page": "files", "tab": 1,
                                                     "empty": True}),
    "handheld_files-nosd": (L.HANDHELD, None, D, {"page": "files", "tab": 1,
                                                  "empty": "nosd"}),
    "handheld_now-radio": (L.HANDHELD, None, DL, {"page": "now", "now": RADIO}),
    "handheld_now-radio-pad": (L.HANDHELD, "pad", D, {"page": "now", "now": RADIO}),
    "handheld_now-radio-touch": (L.HANDHELD, "touch", D, {"page": "now", "now": RADIO}),
    "handheld_now-playlist": (L.HANDHELD, None, DL, {"page": "now", "now": SONG}),
    "handheld_now-buffering": (L.HANDHELD, None, D, {"page": "now", "now": BUFFERING}),
    "handheld_now-error": (L.HANDHELD, None, D, {"page": "now", "now": FAILED}),
    "handheld_add": (L.HANDHELD, None, DL, {
        "page": "add", "url": "http://ice1.somafm.com/lush-128-mp3", "focus": "Address"}),
    "handheld_add-https": (L.HANDHELD, None, D, {
        "page": "add", "url": "https://stream.example.org/live.mp3", "focus": "Address",
        "err": "This address won't play. Ask a grown-up for one that starts with http://"}),
    "guition-s3_noaudio": (GUITION_S3, "touch", D, {"page": "noaudio"}),
    "desktop_music": (L.DESK, None, DL, {"page": "desk", "tab": 0, "cur": 3, "now": RADIO,
                                         "focus": "stations"}),
    "desktop_music-files": (L.DESK, None, DL, {"page": "desk", "tab": 1, "now": SONG,
                                               "focus": "Play"}),
    # the shell's: a status item on the desk, the control center on a handheld
    "status_desk-bar": (L.DESK, None, DL, {"shell": "desk", "now": RADIO}),
    "status_desk-menu": (L.DESK, None, DL, {"shell": "desk", "now": RADIO, "menu": True,
                                            "then": (MENU,)}),
    "status_desk-menu-keys": (L.DESK, None, D, {"shell": "desk", "now": RADIO, "menu": True,
                                                "cc_focus": "Pause", "then": (MENU,)}),
    "status_desk-menu-error": (L.DESK, None, D, {"shell": "desk", "now": FAILED,
                                                 "menu": True, "then": (MENU,)}),
    "cc_bar": (L.HANDHELD, None, D, {"shell": "home"}),
    "cc_bar-music": (L.HANDHELD, None, DL, {"shell": "home", "now": RADIO}),
    "cc_bar-paused": (L.HANDHELD, None, D, {"shell": "home", "now": RADIO_PAUSED}),
    "cc_bar-focus": (L.HANDHELD, None, DL, {"shell": "home", "now": RADIO, "bar_focus": True}),
    "cc_bar-low": (L.HANDHELD, None, D, {"shell": "home", "now": RADIO, "battery": "low"}),
    "cc_bar-charging": (L.HANDHELD, None, D, {"shell": "home", "now": RADIO,
                                              "battery": "charging"}),
    "cc_open": (L.HANDHELD, None, DL, {"shell": "home", "now": RADIO, "then": (CC,)}),
    "cc_open-keys": (L.HANDHELD, None, D, {
        "shell": "home", "now": RADIO, "cc_focus": "Pause",
        "keys": ("confirm", "Pause", "change", "Button", "scroll", "Row", "back", "Close"),
        "then": (CC,)}),
    "cc_open-scrolled": (L.HANDHELD, None, D, {
        "shell": "home", "now": RADIO, "cc_focus": "See downloads", "scrolled": True,
        "keys": ("confirm", "Open", "scroll", "Row", "back", "Close"), "then": (CC,)}),
    "cc_open-pad": (L.HANDHELD, "pad", D, {
        "shell": "home", "now": RADIO, "cc_focus": "Volume",
        "keys": ("left right", "Change", "scroll", "Row", "back", "Close"), "then": (CC,)}),
    "cc_open-touch": (L.HANDHELD, "touch", D, {"shell": "home", "now": RADIO,
                                               "input": "touch", "then": (CC,)}),
    "cc_open-buffering": (L.HANDHELD, None, D, {"shell": "home", "now": BUFFERING,
                                                "then": (CC,)}),
    "vmstop_dialog": (L.HANDHELD, None, D, {"shell": "home", "now": RADIO, "vmstop": True}),
}

# The desk window's place and size.
WINDOW = (110, 52, 820, 490)


def _setup(scene, config):
    def setup(g, ws):
        if scene.get("shell"):
            if scene["shell"] == "desk":
                L._desk_files(g, ws)
            else:
                L._home(g, ws)
            L._settle(g, ws, 6)
            for step in scene.get("then", ()):
                step(g, ws, scene)
            return
        from runtime.ui_draw import AppHost
        if scene.get("wifi") == "off":
            ws.wifi = _NoWifi()
        cls = MusicMock
        ctx = ws.app_context(cls.id, cls.NEEDS)
        ctx.ui = AppHost(cls.id)
        app = cls(ctx, scene)
        ws.register_app(app)
        if config == L.DESK:
            ws.open_desk()
        ws.open_app(app, cart={"title": "Music", "id": "moybyte.music",
                               "permissions": ["media", "media:url", "http"]})
        if config == L.DESK:
            L._settle(g, ws, 2)
            L._place(ws, "music", *WINDOW)
    return setup


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("filters", nargs="*")
    ap.add_argument("--theme", default=L.DEFAULT)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    g = L._goldens()
    g._CLOCK = L.CLOCK
    from PIL import Image
    n = 0
    for name, (config, inputs, modes, scene) in SCENES.items():
        if a.filters and not any(f in name or fnmatch.fnmatch(name, f) for f in a.filters):
            continue
        for mode in modes:
            gc.collect()  # a dropped workstation's covers go back to the pool
            MODE[0] = mode
            image = L._image
            _GLASS[0] = image
            posts = []
            if scene.get("now"):
                posts.append(_bar_status(scene))
            if scene.get("vmstop"):
                posts.append(_vm_stop(scene))

            def capture(ws, _i=image, _p=posts, _s=scene, _in=inputs):
                for p in _p:
                    p(ws)
                img = _i(ws)
                if _s.get("vmstop") and _BOX[0]:
                    img = _grow_card(img, _BOX[0])
                if _s.get("battery"):
                    img = _battery(ws, img, _s["battery"])
                if _s.get("shell") == "home":
                    img = _bar_room(ws, img, _s)
                elif _s.get("shell") == "desk" or config == L.DESK:
                    img = _desk_marks(ws, img, _s)
                img = _scrim(ws, img, _s)
                return img if _in == "touch" else _strip_inset(img)
            L._image = capture
            try:
                img, settled, _report = L.render(g, config, a.theme, mode,
                                                 _setup(scene, config), False, inputs)
            finally:
                L._image = image
            base = os.path.join(a.out, "%s-%s_%s" % (a.theme, mode, name))
            img.save(base + ".png")
            if img.size == (320, 240):
                img.resize((960, 720), Image.NEAREST).save(base + "@3x.png")
            print("%-34s %-5s %dx%d%s" % (name, mode, img.size[0], img.size[1],
                                          "" if settled else "  (did not settle)"))
            n += 1
    print("%d renders -> %s" % (n, a.out))


if __name__ == "__main__":
    main()
