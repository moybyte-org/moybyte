# Map (grep -n a name to jump there):
#   GetCartsLayout                the page's rects: the shelf, a desk's pane and foot
#   GetCartsAppLayer              the Get Carts app: index, covers, install
#   GetCartsAppLayer._pump        one frame's slice of fetching and installing
#   GetCartsAppLayer._build_rows  the index's carts as rows
#   GetCartsAppLayer.blocker      why a row cannot be installed here
#   GetCartsAppLayer.draw         the app's frame: the page its phase shows
#   GetCartsAppLayer._shelf       the carts as cards
#   GetCartsAppLayer._cart_page   a cart's page (on the desk, the pane)
"""Get Carts -- the console's store (#124): carts other people publish,
browsed and installed over WiFi with no PC involved, drawn with the public UI
API's verbs (docs/ui_api_2026-10.md; the look's
docs/media/look_2026-10/APPS_RESTYLE.md, Get Carts).

The screens, in the order a kid meets them:

  CHECKING   the radio comes up under the "carts" lease and every index is
             fetched -- moybyte-org's carts repository unless `indexes.json`
             beside the carts folder names others (runtime/cart_index.py) --
             then the covers they name that this session has not drawn yet:
             one busy page, the shelves counted as they come.
  LIST       the shelf: a card per cart, its whole cover (the 128 x 128
             cover.png, SPEC.md 3.6, decoded once at the card's side; a cart
             with none, or one that does not decode, shows its genre's icon
             on a tinted tile), its name and a sub line -- its size, or where
             it stands: On console, Update ready, Name taken when a different
             cart already has its folder, Too big or Can't play -- and a badge
             on the cover's corner for those (the cover veiled where the
             console cannot take it). The caption row counts them and says
             what room is free; R checks again (on the desk, Check).
  CART       one cart: its cover, genre and source, where it stands, its
             description, what it takes and needs, and its verbs -- Remove,
             Play (a theme package's ("runtime": "theme") Use, which makes it
             the console's theme; removing the active one falls back to the
             default seed, the look's `store_moved`), Get or Update. A cart
             that will not fit in the store, that the engine would refuse to
             load (the Player's check, asked of the index before the
             download), or that this console cannot play says why and offers
             no Get. Remove asks first, in a dialog. On the desk the page is
             a pane beside the shelf, for the card focus is on, its verbs in
             the window's foot.
  LICENCE    a file the cart needs that is not in its release (Doom's WAD)
             shows its licence before it is fetched. No has the focus; I
             agree is the only way on.
  YOUR COPY  a console that can be handed a file (the browser) first asks
             whether it can read that file from anywhere the index names --
             its mirror beside the index, then its archive's hosts; where it
             cannot, the player chooses their own copy, which is held to the
             index's size and sha256 before anything is downloaded.
  GETTING    the download, a slice per frame (`cart_index.Install.step`), with
             its progress and Cancel, which B (Esc) or a tap reaches and A
             never does (a kid presses A twice after Get) -- until the build
             is checked and goes to a store of record that is not these files
             (the browser's), when it can no longer be cancelled and says so.
  READY / NOT INSTALLED  how it ended: the cart's page again under a toast,
             Play first; or the reason, and that nothing changed on the shelf.
  WI-FI IS OFF, SHELF AWAY, MEMORY FULL, NO INTERNET  why there is no shelf,
             and Try again; with Wi-Fi off, Wi-Fi opens the Wi-Fi page and
             coming back from it checks again at once.
  ON THE CONSOLE  a page a board serves: the carts it shows are the board's,
             and the board gets its own, so this one fetches nothing.

The installing is cart_index's; this module is the screens and the lease. The
radio is held while something is fetched -- the indexes, a licence and the
download it leads to -- and let go as soon as that ends: never while the kid
browses, never into a cart (Play lets go first), never past `close()`.

Work that may wait (dialling the network, fetching an index) runs one frame
after its screen is shown, so CHECKING is on the glass before the wait starts
-- the update screen's arm (runtime/update_ui.py). Every fetch is a
`cart_index` job stepped once a frame, which on a board or the host finishes
in that step and in the browser gives the frame back until its bytes come.
"""

import json

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

try:
    from app_shell import ListShellApp
    from layout_base import bar_rows, content_of, metric_class, window_foot, DESK
except ImportError:  # pragma: no cover - direct host import
    from runtime.app_shell import ListShellApp
    from runtime.layout_base import (bar_rows, content_of, metric_class, window_foot,
                                     DESK)

try:
    import cart_index as _ci
    import cart_listing as _cl
    from moy_fs import _exists, _read
    from ticks import _ticks_ms, _ticks_diff
    from player import fit_notice as _fit_notice
except ImportError:  # pragma: no cover - direct host import
    from runtime import cart_index as _ci
    from runtime import cart_listing as _cl
    from runtime.moy_fs import _exists, _read
    from runtime.ticks import _ticks_ms, _ticks_diff
    from runtime.player import fit_notice as _fit_notice


# An index fetched this session is reused for this long before the app checks
# again on its own; R (the desk's Check) asks at once.
REFRESH_MS = 600000

# Where a card stands, as its sub line and its badge say it; a cart that is
# only for the getting says its size instead.
STATE_WORD = {"installed": "On console", "update": "Update ready", "taken": "Name taken",
              "too_big": "Too big", "noplay": "Can't play"}
BADGE = {"installed": "check", "update": "getcarts", "taken": "warn", "too_big": "warn",
         "noplay": "warn"}
# The states a card's sub line and badge say in the danger ink, its cover veiled.
BLOCKED = ("taken", "too_big", "noplay")

# A verb's words and icon, by its name.
VERB = {"GET": ("Get", "getcarts"), "UPDATE": ("Update", "getcarts"),
        "PLAY": ("Play", "play"), "USE": ("Use", "palette"), "REMOVE": ("Remove", "trash"),
        "I AGREE": ("I agree", ""), "NO": ("No", ""), "CANCEL": ("Cancel", "close"),
        "OK": ("OK", ""), "TRY AGAIN": ("Try again", "rotate"), "WIFI": ("Wi-Fi", "wifi")}
# The verbs that go on: one is the page's primary, the last of them.
GO = ("GET", "UPDATE", "PLAY", "USE", "I AGREE", "TRY AGAIN", "WIFI", "OK")

# The id focus rests on while work is in hand: no stop, so Enter acts on none.
_NOWHERE = "nowhere"

# A toast stays this long, or until the next key.
TOAST_MS = 2000

# An external file's archive is held in RAM while it is checked and unpacked
# when it is under this share of the largest free block a compiled cart loads
# into, and kept in a file beside the build otherwise. The P4s' 32 MB clear it
# (and their flash store writes slowly); on an S3 the archive would sit in a
# Python heap that never shrinks, out of reach of the cart it was fetched for
# until a reboot, while its card writes fast.
ARCHIVE_RAM_SHARE = 4

# The phases whose work runs a slice per frame from draw().
BUSY = ("checking", "licence_fetch", "probing", "picked", "connecting", "getting")
# The phases past the list that are pages of their own (`has_page`).
_PAGES = ("licence", "pick", "cart", "done", "failed")
# The pages that say why there is no shelf, and Try again.
_AWAY = ("nonet", "nowifi", "unreached", "nomemory")


def _mb(n):
    """A size the way the screens say it: whole KB below a megabyte, one
    decimal above. Integer arithmetic, so every tier prints the same."""
    if n < 1048576:
        return "%d KB" % max(1, (n + 1023) // 1024)
    return "%d.%d MB" % (n // 1048576, (n % 1048576) * 10 // 1048576)


def _host_of(url):
    start = url.find("://")
    rest = url[start + 3:] if start >= 0 else url
    cut = rest.find("/")
    return rest if cut < 0 else rest[:cut]


def _ext_host(cart, e):
    """Where an external file comes from first, as the screens name it: its
    mirror's host, else its archive's."""
    return _host_of(_ci.resolve(cart["index"], _ci.external_sources(e)[0]))


def _any_key(inp):
    for k in ("a", "b", "up", "down", "left", "right"):
        if inp.pressed(k):
            return True
    return False


_DIM = {"tone": "dim"}
_DISABLED = {"disabled": True}
_TITLE = {"face": "title"}
_WRAP3 = {"wrap": True, "lines": 3}
_CARD_GROUP = {"card": True}
_DIM_CAPTION = {"tone": "dim", "face": "caption"}
_CHECK = {"id": "Check"}
_BACK = ("back", "Back")
_SHELF_HINTS = ("confirm", "Open", "R", "Check", "back", "Back")


def vid(verb):
    """A verb's button's id: its words, apart from the dialog's."""
    return "verb " + VERB[verb][0]


def genre_icon(cart):
    """The icon a cart with no cover shows: a theme's palette, music's
    note, an app's window, else the gamepad."""
    if cart.get("runtime") == "theme":
        return "palette"
    g = cart.get("genre")
    if g == "music":
        return "music"
    if cart.get("type") == "app":
        return "app"
    return "game"


class _Pane:
    """The desk pane's words and foot for one cart in one phase
    (`GetCartsApp._pane_parts`)."""

    def __init__(self):
        self.row = None
        self.st = self.phase = self.free = None
        self.word = self.src = ""
        self.state = None
        self.state_icon = ""
        self.state_h = 0
        self.state_o = None
        self.info = ()
        self.status = ""
        self.status_at = (0, 0, 0, 0)
        self.buttons = []         # (verb, rect, label, icon, kind, override table)


class GetCartsLayout:
    """Get Carts' rects over the content rect: the caption row, the shelf and
    the hint band on the handheld; on the desk the shelf beside the cart's
    pane, a rule between them, and the window's foot under both."""

    MIN_W = 300
    MIN_H = 220
    FOOT = 44            # the desk's foot: the rule to the frame's bottom
    PANE = 290           # the desk's pane, its rule included

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = fs = max(1, int(fs))
        self.cs = max(fs, int(cs)) if cs else fs
        self.windowed = bool(windowed)
        self.desk = metric_class() == DESK
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, 0 if windowed else bar_rows(self.cs))
        content = (self.cx, self.cy, self.cw, self.ch)
        self.full = content
        if self.desk:
            # the frame's rounded foot draws in the content's last rows: the
            # foot's band is that much shorter, its rule as high off the frame
            under = window_foot() if self.windowed else 0
            self.foot, self.body = ui.cut_bottom(content, self.FOOT - under)
            body = self.body
            pane_w = min(self.PANE, body[2] // 3)
            pane, self.shelf_area = ui.cut_right(body, pane_w)
            self.rule = (pane[0], pane[1], 1, pane[3])
            px, py, pw, ph = pane
            self.pane = (px + 1 + 17, py + 16, pw - 1 - 17 - 6, ph - 16)
            sx, sy, sw, sh = self.shelf_area
            self.caption = (sx + 24, sy + 8, sw - 24 - 20, 30)
            self.side = 128                       # a card's picture, the pane's cover
            self.cols = max(1, (sw - 32 + 16) // (self.side + 16))
            gw = self.cols * self.side + (self.cols - 1) * 16
            self.grid = (sx, sy + 34, 24 + gw + 24, sh - 34)
            self.margin = 24
            self.foot_pad = 8
            self.grid_o = {"cols": self.cols, "foot": 0, "top": 0, "margin": self.margin}
        else:
            self.foot, self.body = ui.cut_bottom(content, 20 * fs)
            bx, by, bw, bh = self.body
            self.side = 64 * fs
            self.cols = max(1, (bw - 32 * fs + 12 * fs) // (self.side + 12 * fs))
            self.caption = (bx + 16 * fs, by + 2 * fs, bw - 32 * fs, 16 * fs)
            self.grid = (bx, by + 16 * fs, bw, bh - 16 * fs)
            self.margin = 16
            self.grid_o = {"cols": self.cols, "foot": 32, "top": 4, "margin": self.margin}
            self.pane = (bx + 6 * fs, by + 8 * fs, bw - 12 * fs, bh - 8 * fs)

    def well(self):
        """The licence's well on the handheld page, or the desk's window: its
        rect, under the licence's name and over the verbs."""
        x, y, w, h = self.pane if not self.desk else self.full
        top = 28 * (1 if self.desk else self.fs)
        btn = (40 if self.desk else 26 * self.fs) + 12 * (1 if self.desk else self.fs)
        if self.desk:
            x, y, w, h = self.full[0] + 24, self.full[1] + 16, self.full[2] - 48, \
                self.full[3] - 16 - self.FOOT
            btn = 12
        return (x, y + top, w, max(20, h - top - btn))

    def well_text_w(self):
        return self.well()[2] - 2 * (8 if self.desk else 6 * self.fs)

    def page_verbs(self):
        """The handheld cart page's verb row: where its buttons sit."""
        x, y, w, h = self.pane
        fs = self.fs
        return (x, y + h - 42 * fs, w, 26 * fs)

    def split_verbs(self, rect, n):
        """`rect` cut into `n` verb buttons, the one that goes on last and
        wider."""
        x, y, w, h = rect
        gap = 6 * self.fs
        extra = 20 * self.fs if n > 1 else 0
        bw = (w - gap * (n - 1) - extra) // n
        out = [(x + i * (bw + gap), y, bw, h) for i in range(n - 1)]
        last = x + (n - 1) * (bw + gap)
        out.append((last, y, x + w - last, h))
        return out

    def licence_verbs(self):
        """The handheld licence page's No and I agree, under its well."""
        x, y, w, h = self.well()
        fs = self.fs
        foot = y + h + 8 * fs
        vw = 140 if self.desk else 96 * fs
        vh = 36 if self.desk else 26 * fs
        return ((x + w - 2 * vw - 6 * fs, foot, vw, vh), (x + w - vw, foot, vw, vh))


class GetCartsAppLayer(ListShellApp):
    id = "getcarts"
    domain = "system"
    TITLE = "Get carts"
    APP_TITLE = "Get Carts"
    APP_PERM = "getcarts"
    APP_FOLDER = "moybyte.get_carts.moy"
    # `install` is the network, its lease, the store an install writes and the
    # Wi-Fi page; `nav` is Play.
    NEEDS = ("surface", "theme", "damage", "install", "nav")
    UI = True

    def __init__(self, ctx, names):
        self.ctx = ctx
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._inst = ctx.install
        self._nav = ctx.nav
        self._ui = ctx.ui
        self.names = names
        cv = self._surf.canvas()
        self.layout = GetCartsLayout(cv.w, cv.h, self._surf.font_scale(),
                                     self._surf.windowed(),
                                     self._surf.chrome_scale())
        self.phase = "list"
        self.carts = []               # every index's carts, this session
        self.rows = []                # [{"cart", "plan", "state", "had", "runs", "fit"}]
        self.fetched_at = None
        self.indexes = ()
        self.unreached = 0
        self.starved = False          # an index failed for want of memory
        self.free = None              # (bytes, block) of the store
        self.cur = None               # the CART page's row (the desk's pane)
        self.job = None
        self.lic = None               # [external, wrapped lines or None, first line shown]
        self._lic_text = ""
        self.accepted = []
        self.asking = False           # Remove's dialog is up
        self.why = ""                 # the NOT INSTALLED page's reason
        self.toast = None             # (text, icon, until ms): a passing message
        self.thumbs = {}              # cover sha256 -> (side, RGB565) or None
        self._pane_k = None           # the desk pane's parts (`_pane_parts`)
        self._whole = (0, 0, 0, 0)    # the page's rect, the canvas's
        self._caption_key = (-1, None, None, "", "", "", 0)   # the shelf's caption, as last made
        self._covers = []             # the covers this check still fetches
        self._work = None             # the decode's scratch, while checking
        self._found = []
        self._step = 0
        self._fetch = None            # the cart_index job in hand, and what it is
        self._fetching = None
        self.supplied = {}            # external path -> the player's own copy
        self._ext = 0                 # the external the probe or pick is on
        self._pick = None             # the page's file question, while it is up
        self._picked = []             # answered ones whose files the install reads
        self._filecheck = None        # the FileCheck of a picked file
        self.pick_why = None          # what was wrong with the last one
        self._held = False
        self._away = False            # the Wi-Fi page is open over the app
        self._grab = None             # a drag over the licence: (y, first line) at its press
        self._focus = None            # the id focus moves to on the next frame
        self._focused = None          # the focused id as the last frame left it
        self._key = 0

    # -- the app protocol (docs/app_api_v1.md) --------------------------------

    def open(self):
        self.asking = False
        self.job = None               # one the keeper still holds lands by itself
        self.lic = None
        self._away = False
        if self._inst.home() is not None:
            self._go("board")
        elif self._inst.net() is None:
            self._go("nonet")
            self._focus = vid("TRY AGAIN")
        else:
            self._recover()
            fresh = (self.fetched_at is not None
                     and _ticks_diff(_ticks_ms(), self.fetched_at) < REFRESH_MS)
            if self.carts and fresh:
                self._build_rows()
                self._go("list")
                if self.rows:
                    self._focus = self._card_id(self.rows[0])
            else:
                self._check()
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.layout = GetCartsLayout(w, h, fs, self._surf.windowed(), cs)
        if self.lic is not None and self.lic[1] is not None:
            self.lic[1] = self._wrap(self._lic_text)
            self.lic[2] = 0

    def close(self):
        if self.job is not None and not self.job.finished and self.job.cancel():
            self._go("cart" if self.cur is not None else "list")
        self._drop_fetch()
        self._unpick()
        self._release()
        self._away = False

    def page_title(self):
        """The bar's title on a cart's own pages: the cart's name."""
        if self.cur is not None and not self.layout.desk and (
                self.phase in ("cart", "done", "licence", "pick")
                or self.phase in BUSY and self.phase != "checking"):
            return self.cur["cart"]["name"]
        return None

    # -- the lease -----------------------------------------------------------------

    def _hold(self):
        if not self._held:
            self._held = True
            self._inst.hold()

    def _release(self):
        if self._held:
            self._held = False
            self._inst.release()

    # -- phases --------------------------------------------------------------------

    def _go(self, phase):
        self.phase = phase
        self.armed = False
        if phase == "nowifi":
            self._focus = vid("WIFI")
        elif phase in _AWAY:
            self._focus = vid("TRY AGAIN")
        elif phase == "failed":
            self._focus = vid("OK")
        elif phase in BUSY or phase == "pick":
            self._focus = _NOWHERE            # Cancel is B's or a tap's, never Enter's
        self._damage.all()

    def _focus_go(self):
        """Focus on the cart's page's verb that goes on (the last)."""
        v = self._verbs()
        if v:
            self._focus = vid(v[-1])

    def _say(self, text, icon="check"):
        """A toast: `text` for TOAST_MS, or until the next key."""
        self.toast = (text, icon, _ticks_ms() + TOAST_MS)
        self._damage.all()

    def _check(self):
        self._drop_fetch()
        self._found = []
        self._covers = []
        self._work = None
        self.unreached = 0
        self.starved = False
        self._step = 0
        self._go("checking")

    def _drop_fetch(self):
        job, self._fetch = self._fetch, None
        self._fetching = None
        if job is not None:
            job.close()

    def _fetched(self, start, what):
        """The fetch job `what` names, started by `start()` the first time and
        stepped after: its bytes once they are all in, else None (still on its
        way). Raises the job's InstallError, with the job let go."""
        if self._fetching != what:
            self._drop_fetch()
            self._fetch = start()
            self._fetching = what
        try:
            if self._fetch.step():
                return None
        except _ci.InstallError:
            self._drop_fetch()
            raise
        data = self._fetch.data
        self._fetch = None
        self._fetching = None
        return data

    def _recover(self):
        root = self._inst.root()
        if root is None or not self._inst.writable():
            return
        try:
            self._inst.op(lambda: _ci.recover(root))
        except Exception as exc:  # noqa: BLE001 -- recover runs again next open
            _ci._log("recover failed: %s" % exc)

    def _pump(self):
        """The BUSY phases' slice of work, one per drawn frame."""
        if self.phase == "pick":
            self._poll_pick()
        if self.phase not in BUSY:
            return
        if not self.armed:
            self.armed = True         # this frame shows the screen; work next frame
            self._damage.again()
            return
        try:
            ph = self.phase
            if ph == "checking":
                self._pump_check()
            elif ph == "licence_fetch":
                self._pump_licence()
            elif ph == "probing":
                self._pump_probe()
            elif ph == "picked":
                self._pump_picked()
            elif ph == "connecting":
                self._pump_connect()
            else:
                self._pump_get()
        except _ci.InstallError as exc:
            _ci._log(exc.detail)
            self._fail(exc.text)
        except Exception as exc:  # noqa: BLE001 -- the store never takes the shell down
            _ci._log("%r" % (exc,))
            self._fail(_ci.STOPPED)
        if self.phase in BUSY:
            self._damage.again()

    def _online(self):
        """The lease taken and the link up; False (lease let go) when the
        console could not get online."""
        self._hold()
        if self._inst.net().online():
            return True
        self._release()
        return False

    def _starved(self):
        """True when the transport says the console has no memory left for a
        connection -- the radio's driver and a TLS download take theirs from
        the same internal RAM, so a radio that will not come up can be that
        too, not a network the kid has to join."""
        return _ci.net_text(self._inst.net(), None) == _ci.NET_MEMORY

    def _pump_check(self):
        net = self._inst.net()
        if self._step == 0:
            if not self._online():
                self._go("nomemory" if self._starved() else "nowifi")
                return
            root = self._inst.root()
            if root is not None and self._inst.writable():
                self.indexes = self._inst.op(lambda: _ci.load_indexes(root))
            else:
                self.indexes = list(_ci.DEFAULT_INDEXES)
            self._step = 1
            return
        i = self._step - 1
        if i < len(self.indexes):
            url = self.indexes[i]
            try:
                data = self._fetched(lambda: _ci.index_fetch(net, url), ("index", i))
                if data is None:
                    return
                self._found.extend(_ci.parse_index(data, url))
            except _ci.InstallError as exc:
                self.unreached += 1
                self.starved = self.starved or exc.text == _ci.NET_MEMORY
                _ci._log(exc.detail)
            self._step += 1
            if self._step - 1 < len(self.indexes):
                return
            self._covers = self._covers_to_fetch()
            if self._covers:
                return
        if self._covers:
            if not self._fetch_cover(net, self._covers[-1]):
                return
            self._covers.pop()
            if self._covers:
                return
        self._work = None
        self._release()
        if not self._found and self.unreached:
            self._go("nomemory" if self.starved else "unreached")
            return
        self.carts = self._found
        self._found = []
        self.fetched_at = _ticks_ms()
        self._build_rows()
        self._go("list")
        if self.rows:
            self._focus = self._card_id(self.cur if self.cur in self.rows else self.rows[0])

    def _covers_to_fetch(self):
        side = self.layout.side
        out = []
        seen = {}
        for c in self._found:
            ref = c.get("cover")
            if ref is None or ref["sha256"] in seen:
                continue
            seen[ref["sha256"]] = True
            got = self.thumbs.get(ref["sha256"])
            if got is None or got[0] != side:
                out.append(c)
        return out

    def _fetch_cover(self, net, cart):
        """One cover fetched and decoded: False while its bytes are on their
        way, True once it is drawn or known not to come."""
        sha = cart["cover"]["sha256"]
        self.thumbs[sha] = None
        try:
            data = self._fetched(lambda: _ci.cover_fetch(net, cart), ("cover", sha))
        except _ci.InstallError as exc:
            _ci._log(exc.detail)
            return True
        if data is None:
            return False
        try:
            import cover_png as _cp
        except ImportError:  # pragma: no cover - direct host import
            from runtime import cover_png as _cp
        side = self.layout.side
        swapped = getattr(self._surf.canvas(), "swapped565", False)
        try:
            if self._work is None and _cp.native() is not None:
                self._work = bytearray(_cp.WORK)
            pix = _cp.decode(data, _cp.SIDE // side,
                             _cp.RGB565_SW if swapped else _cp.RGB565, None,
                             self._work)
        except MemoryError:
            pix = None
        if pix is None:
            _ci._log("%s's cover does not decode" % cart["id"])
        else:
            self.thumbs[sha] = (side, pix)
        return True

    def _build_rows(self):
        inst = self._inst
        root = inst.root()
        chip, fmt = inst.chip()
        have = inst.runtimes()
        ranges = bool(getattr(inst.net(), "ranges", False))
        carts = self.carts

        def _scan():
            rec = _ci.load_record(root) if root is not None else {}
            out = []
            for c in carts:
                p = _ci.plan(c, chip, fmt, ranges)
                folder = "%s/%s" % (root, c["folder"])
                present = root is not None and _exists(folder)
                entry = rec.get(c["folder"])
                man = None
                if present and (entry is None or entry.get("id") != c["id"]):
                    try:
                        man = json.loads(_read(folder + "/manifest.json"))
                    except (OSError, ValueError):
                        man = None
                out.append({"cart": c, "plan": p,
                            "state": _ci.cart_state(c, p, entry, present, man),
                            "had": entry.get("version") if entry is not None else None,
                            "runs": c["runtime"] in _ci.RUNTIMES
                            and (c["runtime"] in have or c["runtime"] == "theme"),
                            "fit": None})
            return out, (inst.free() if root is not None else None)
        try:
            rows, free = inst.op(_scan)
        except Exception as exc:  # noqa: BLE001 -- an unreadable store lists everything as GET
            _ci._log("store scan failed: %s" % exc)
            rows = [{"cart": c, "plan": _ci.plan(c, chip, fmt, ranges), "state": "get",
                     "had": None, "runs": False, "fit": None} for c in carts]
            free = None
        for r in rows:
            r["fit"] = self._fit_why(r)
        rows.sort(key=lambda r: r["cart"]["name"].lower())
        self.rows = rows
        self.free = free
        if self.cur is not None:
            folder = self.cur["cart"]["folder"]
            ident = self.cur["cart"]["id"]
            self.cur = None
            for r in rows:
                if r["cart"]["folder"] == folder and r["cart"]["id"] == ident:
                    self.cur = r

    # -- the CART screen's verbs ---------------------------------------------------

    def _tap_row(self, i):
        """A card opened: its page, focus on the verb that goes on (on the
        desk the pane is that page, its verbs in the foot)."""
        if 0 <= i < len(self.rows):
            self.cur = self.rows[i]
            self.asking = False
            self._go("cart")
            self._focus_go()

    def _go_verb(self):
        """PLAY, or USE for a theme package: what an installed cart does."""
        cur = self.cur
        return "USE" if cur is not None and cur["cart"]["runtime"] == "theme" else "PLAY"

    def _verbs(self):
        st = self.cur["state"] if self.cur is not None else None
        if st == "get":
            return ("GET",)
        if st == "installed":
            return ("REMOVE", self._go_verb())
        if st == "update":
            return ("REMOVE", self._go_verb(), "UPDATE")
        return ()

    def _fit_why(self, row):
        """The Player's own refusal, asked before the download: a compiled
        cart whose load -- its declared memory and this console's module (or
        main.wasm, interpreted), by the engine's sizing -- needs more than the
        engine reports free. None when it fits, or nobody can say."""
        c, p = row["cart"], row["plan"]
        if c["runtime"] != "wasm" or not c.get("memory") or p["load_bytes"] is None:
            return None
        fit = self._inst.fit("wasm", c["memory"], p["load_bytes"], p["module"] is None)
        if fit is None:
            return None
        need, have = fit
        if need[0] > have[0] or need[1] > have[1]:
            return _fit_notice(c["name"], need, have)
        return None

    def blocker(self, row):
        """Why `row` cannot be installed here, or None."""
        if not self._inst.writable() or self._inst.root() is None:
            return "This console has nowhere to keep carts."
        if not row["runs"]:
            return "This console can't play this kind of cart."
        if row.get("fit"):
            return row["fit"]
        if self.free is not None:
            need = _ci.need_bytes(row["plan"], self.free[1])
            if need > self.free[0]:
                return "Not enough room: needs %s, %s free." % (_mb(need),
                                                               _mb(self.free[0]))
        return None

    def _press(self, verb):
        ph = self.phase
        if ph in ("cart", "done"):
            if verb in ("GET", "UPDATE"):
                self._start()
            elif verb == "PLAY":
                self._play()
            elif verb == "USE":
                self._use()
            elif verb == "REMOVE":
                self.asking = True
                self._damage.all()
        elif ph == "licence":
            if verb == "I AGREE":
                self.accepted.append(self.lic[0]["path"])
                self._next_licence()
            else:
                self._release()
                self._say("Not fetched")
                self._go("cart")
                self._focus_go()
        elif ph in BUSY or ph == "pick":
            self._cancel()
        elif verb == "WIFI":
            self._away = True
            self._inst.wifi()
        elif ph == "nonet":
            self.open()
        elif ph in _AWAY:
            self._check()
        elif ph == "failed":
            self._go("cart" if self.cur is not None else "list")
            if self.cur is not None:
                self._focus_go()

    def _start(self):
        if self.blocker(self.cur) is not None:
            return
        self.accepted = []
        self.supplied = {}
        self.pick_why = None
        self.asking = False
        self._next_licence()

    def _next_licence(self):
        for e in self.cur["plan"]["external"]:
            if e["path"] not in self.accepted:
                self.lic = [e, None, 0]
                self._go("licence_fetch")
                return
        self.lic = None
        self._ext = 0
        if self._inst.can_pick() and self.cur["plan"]["external"]:
            self._go("probing")
        else:
            self._go("connecting")

    def _pump_licence(self):
        if not self._online():
            self._fail(_ci.NET_MEMORY if self._starved() else "WiFi isn't connected.")
            return
        ext = self.lic[0]
        net = self._inst.net()
        cart = self.cur["cart"]
        data = self._fetched(lambda: _ci.licence_fetch(net, cart, ext["licence"]),
                             ("licence", ext["path"]))
        if data is None:
            return
        self._lic_text = _ci.as_text(data)
        self.lic[1] = self._wrap(self._lic_text)
        self.lic[2] = 0
        self._go("licence")
        self._focus = vid("NO")             # No has the focus

    # -- YOUR COPY: an external file this console cannot fetch -----------------

    def _external(self):
        ext = self.cur["plan"]["external"]
        return ext[self._ext] if self._ext < len(ext) else None

    def _next_external(self):
        self._ext += 1
        self.pick_why = None
        if self._external() is None:
            self._go("connecting")
        else:
            self._go("probing")

    def _pump_probe(self):
        """Can this console read the external file from its mirror or any of
        its archive's hosts? Yes: the install fetches it. No: the player picks
        a copy."""
        e = self._external()
        if e is None:
            self._go("connecting")
            return
        if not self._online():
            self._fail("WiFi isn't connected.")
            return
        if self._fetching != ("reach", self._ext):
            self._drop_fetch()
            net = self._inst.net()
            index = self.cur["cart"]["index"]
            self._fetch = _ci.Reach(net, [_ci.resolve(index, u)
                                          for u in _ci.external_sources(e)])
            self._fetching = ("reach", self._ext)
        if self._fetch.step():
            return
        reach, self._fetch, self._fetching = self._fetch, None, None
        if reach.url is not None:
            self._next_external()
            return
        _ci._log("%s: no host this console can read (%s)"
                 % (e["path"], "; ".join(reach.why)))
        self._ask_pick()

    def _ask_pick(self):
        e = self._external()
        if self._pick is not None:
            self._pick.close()        # a question answered with the wrong file
        self._pick = self._inst.pick(e["path"], e["size"],
                                     _ext_host(self.cur["cart"], e))
        if self._pick is None:
            self._fail(_ci.UNREACHABLE)
            return
        self._go("pick")

    def _poll_pick(self):
        got = self._pick.poll() if self._pick is not None else None
        if got is None:
            return
        if got[0] != "file":
            self._unpick()
            self._release()
            self._say("Not fetched")
            self._go("cart")
            self._focus_go()
            return
        e = self._external()
        self._filecheck = _ci.FileCheck(got[1], e["size"], e["sha256"])
        self._go("picked")

    def _pump_picked(self):
        """The picked file held to the index, a slice a frame."""
        chk = self._filecheck
        if chk.step():
            return
        self._filecheck = None
        e = self._external()
        if chk.why is None:
            self.supplied[e["path"]] = chk.path
            self._picked.append(self._pick)
            self._pick = None
            self._next_external()
            return
        self.pick_why = "That isn't %s: %s." % (e["path"], chk.why)
        self._ask_pick()

    def _unpick(self):
        """Every question to the player goes, and the files they answered with:
        the install has read them, or will not."""
        p, self._pick = self._pick, None
        done, self._picked = self._picked, []
        for q in [p] + done:
            if q is not None:
                q.close()
        chk, self._filecheck = self._filecheck, None
        if chk is not None:
            chk.close()

    def _wrap(self, text):
        """The licence's lines, broken at word breaks in the well's width in
        the face its lines print in."""
        room = self.layout.well_text_w()
        ui.face("ui")
        out = []
        try:
            for para in text.split("\n"):
                line = ""
                for word in para.split():
                    t = word if not line else line + " " + word
                    if line and ui.text_size(t)[0] > room:
                        out.append(line)
                        line = word
                    else:
                        line = t
                out.append(line)
        finally:
            ui.face()
        return out

    def _pump_connect(self):
        if not self._online():
            self._fail(_ci.NET_MEMORY if self._starved() else "WiFi isn't connected.")
            return
        row = self.cur
        arc = 0
        for e in row["plan"]["external"]:
            arc += e["archive"]["size"]
        mem = self._inst.memory()
        keep = "store" if (arc and mem is not None
                           and mem[1] < ARCHIVE_RAM_SHARE * arc) else "ram"
        self.job = _ci.Install(row["cart"], row["plan"], self._inst.net(),
                               self._inst.root(), self._inst.op, self.accepted,
                               archive=keep, supplied=self.supplied,
                               keep=self._inst.keep())
        self._go("getting")
        self.armed = True

    def _pump_get(self):
        job = self.job
        if job.step():
            return
        self._release()
        self._unpick()
        if job.path:
            self._inst.rescan()
            self._build_rows()
            self._go("done")
            self._say("%s is on your shelf" % self.cur["cart"]["name"])
            self._focus = vid(self._go_verb())
        elif job.error:
            self._fail(job.error)
        else:
            self._go("cart")
            self._focus_go()

    def _cancel(self):
        """B, or a tap on CANCEL -- never A, which a kid presses at anything.
        Not once the keeper has the build: it lands."""
        checking = self.phase == "checking"
        if self.job is not None and not self.job.finished and not self.job.cancel():
            return
        self._drop_fetch()
        self._unpick()
        self._release()
        self._say("Stopped" if checking else "Stopped. Nothing changed.")
        self._go("cart" if self.cur is not None and not checking else "list")
        if self.phase == "cart":
            self._focus_go()

    def _fail(self, why):
        self._drop_fetch()
        self._unpick()
        self._release()
        self.why = why
        self._go("failed")

    def _play(self):
        cart = self._inst.find(self.cur["cart"]["folder"])
        if cart is None:
            return
        self._release()
        self._nav.play(cart)

    def _use(self):
        """Make the installed theme package the console's theme: its name is
        its folder's stem."""
        folder = self.cur["cart"]["folder"]
        if self._inst.find(folder) is None:
            return
        name = folder[:-4] if folder.endswith(".moy") else folder
        self._theme.set(name)
        self._say("Using " + self.cur["cart"]["name"])
        self._damage.all()

    def _remove(self):
        """The cart off the shelf, its saves and the files it wrote with it:
        what Remove's dialog said."""
        self.asking = False
        row = self.cur
        root = self._inst.root()
        keep = self._inst.keep()
        try:
            self._inst.op(lambda: _ci.remove(root, row["cart"]["folder"], keep))
        except Exception as exc:  # noqa: BLE001 -- the folder stays; say so
            _ci._log("remove failed: %s" % exc)
            self._say("Can't remove it", "warn")
            self._damage.all()
            return
        self._inst.rescan()
        self._build_rows()
        self.cur = None
        self._say("Removed " + row["cart"]["name"])
        self._go("list")
        self._focus = self._card_id(row)

    def has_page(self):
        """Whether a page of the app is open, which `back` steps out of: a
        licence, a fetch or a pick, a cart's page, Remove's dialog."""
        if self.layout.desk and self._in_pane():
            return self.asking        # the pane is the shelf's: no page to leave
        return self._paged()

    def _paged(self):
        ph = self.phase
        return self.asking or ph in _PAGES or ph in BUSY

    def _in_pane(self):
        """Whether the desk shows this phase in the pane beside the shelf."""
        ph = self.phase
        return ph in ("list", "cart", "done", "getting") or (
            ph in BUSY and self.cur is not None and ph != "checking")

    def back(self):
        """Back one level (B, Esc, the bar's chevron): Remove's dialog closes,
        a licence is declined, a fetch or a pick cancelled, a cart's page goes
        back to the shelf. False on the shelf: the host leaves the app."""
        if not self._paged() or self.layout.desk and self.phase in ("cart", "done") \
                and not self.asking:
            return False
        ph = self.phase
        if self.asking:
            self.asking = False
            self._focus = vid("REMOVE")
        elif ph == "licence":
            self._press("NO")
        elif ph in BUSY or ph == "pick":
            self._cancel()
        else:
            row = self.cur
            self._go("list")
            if row is not None:
                self._focus = self._card_id(row)
        self._damage.all()
        return True

    # -- input -----------------------------------------------------------------

    def _current_verbs(self):
        """The page's verbs, left to right, the one that goes on last."""
        ph = self.phase
        if ph in ("cart", "done"):
            return self._verbs()
        if ph == "licence":
            return ("NO", "I AGREE")
        if ph == "getting" and self.job is not None and self.job.keeping:
            return ()
        if ph in BUSY or ph == "pick":
            return ("CANCEL",)
        if ph == "nowifi":
            return ("TRY AGAIN", "WIFI")
        if ph in _AWAY:
            return ("TRY AGAIN",)
        if ph == "failed":
            return ("OK",)
        return ()

    def handle_input(self, inp):
        k = getattr(inp, "last_key", 0) or 0
        if not isinstance(k, int):
            k = ord(k[0]) if k else 0
        typed = k != 0 and k != self._key
        self._key = k
        if self.toast is not None and (typed or _any_key(inp)):
            self.toast = None
            self._damage.all()
        if typed and k in (82, 114) and self.phase == "list" and not self.asking:
            self._check()                 # R: check again
            return True
        if self.phase == "licence" and self._focused == "Licence" and not self.asking:
            if (inp.pressed("up") or inp.pressed("down")) \
                    and self._scroll_licence(-1 if inp.pressed("up") else 1):
                return True               # the well scrolls; at its ends focus moves
        if self.phase in BUSY or self.phase == "pick":
            # A kid presses A at anything, and twice after Get: while work is
            # in hand only B (Esc) or a tap on Cancel stops it
            if inp.pressed("b"):
                self.back()
            return True
        if inp.pressed("b"):
            self.back()
        if self._ui.keys(inp):
            self._damage.all()
        return True

    def _scroll_licence(self, d):
        """The licence's text a line up or down: whether it moved."""
        lines = self.lic[1] or ()
        rows = self._well_rows()
        top = max(0, min(self.lic[2] + d, len(lines) - rows))
        if top == self.lic[2]:
            return False
        self.lic[2] = top
        self._damage.all()
        return True

    def _well_rows(self):
        ui.face("ui")
        lh = ui.text_size("Ag")[1]
        ui.face()
        return max(1, (self.layout.well()[3] - 8) // max(1, lh))

    def handle_pointer(self, px, py, click):
        down = bool(getattr(self._surf.pointer(), "down", False))
        if self.phase == "licence" and self.lic is not None and not self.asking:
            self._drag_licence(px, py, click, down)
        if self._ui.point(px, py, click, down):
            self._damage.all()
        return True

    def _drag_licence(self, px, py, click, down):
        """A drag over the licence's well scrolls it a line per line's
        height moved (a press with no drag focuses it, as a tap does)."""
        if click:
            box = self._ui.box("Licence")
            inside = box is not None and box[0] <= px < box[0] + box[2] \
                and box[1] <= py < box[1] + box[3]
            self._grab = (py, self.lic[2]) if inside else None
            return
        if self._grab is None:
            return
        y0, top0 = self._grab
        if not down:
            self._grab = None
        ui.face("ui")
        lh = max(1, ui.text_size("Ag")[1])
        ui.face()
        lines = self.lic[1] or ()
        top = max(0, min(top0 - (py - y0) // lh, len(lines) - self._well_rows()))
        if top != self.lic[2]:
            self.lic[2] = top
            self._damage.all()

    # -- draw ------------------------------------------------------------------

    def draw(self, dt):
        self._pump()
        if self._away and not self._inst.wifi_open():
            self._away = False            # back from the Wi-Fi page: look again
            if self.phase in _AWAY:
                self._check()
        elif self._away:
            self._damage.again()          # watch for the page to close
        if self.toast is not None:
            if _ticks_diff(_ticks_ms(), self.toast[2]) >= 0:
                self.toast = None
            else:
                self._damage.again()
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        whole = self._whole
        if whole[2] != cv.w or whole[3] != cv.h:
            whole = self._whole = (0, 0, cv.w, cv.h)
        self._ui.begin(cv, th, whole, lay.desk, lay.windowed)
        ui.page("")                       # the ground under the bar's band too
        was_phase = self.phase
        was_cur = self.cur is None
        was_asking = self.asking
        if self.phase in BUSY or self.phase == "pick":
            self._focus = _NOWHERE            # held there: no key reaches Cancel
        if self._focus is not None:
            ui.focus(self._focus)
            self._focus = None
        ph = self.phase
        shelf = ph == "list" or (lay.desk and self._in_pane())
        if shelf:
            self._shelf(cv)
            if lay.desk:
                self._pane(cv)
        elif ph in ("cart", "done"):
            self._cart_page(cv)
        elif ph == "getting":
            self._getting(cv)
        elif ph == "licence":
            self._licence(cv, th)
        elif ph == "pick":
            self._your_copy()
        elif ph in BUSY:
            self._busy()
        else:
            self._empty()
        self._dialog()
        if self.toast is not None:
            self._toast()
        self._focused = ui.focus()
        if lay.desk and ph in ("list", "cart", "done"):
            self._pane_follows()
        if (self.phase != was_phase or (self.cur is None) != was_cur
                or self.asking != was_asking):
            self._damage.again()          # an answer changed what the page shows: a
                                          # draw's `all` is spent with it, an `again` is not

    # -- the shelf ---------------------------------------------------------------

    def _card_id(self, row):
        return "Cart " + row["cart"]["folder"]

    def _shown(self, row):
        """Where a card stands as it says it: a cart only for the getting
        that this console cannot take is Too big or Can't play."""
        st = row["state"]
        if st == "get" and not row["runs"]:
            return "noplay"
        if st == "get" and (row["fit"] or self._no_room(row)):
            return "too_big"
        return st

    def _no_room(self, row):
        if self.free is None:
            return False
        return _ci.need_bytes(row["plan"], self.free[1]) > self.free[0]

    def _pic(self, row):
        """The cart's cover as decoded, (side, pixels), or None."""
        ref = row["cart"].get("cover")
        return self.thumbs.get(ref["sha256"]) if ref is not None else None

    def _caption(self):
        n = len(self.rows)
        left = "%d cart%s" % (n, "" if n == 1 else "s")
        if self.unreached:
            left += " · %d shelf away" % self.unreached if ui.has_symbols() \
                else " - %d shelf away" % self.unreached
        right = "%s free" % _mb(self.free[0]) if self.free is not None else ""
        return left, right

    def _card(self, row):
        """Row `row`'s card as the shelf draws it: (title, sub, override
        table), made again only when where the cart stands, or the room
        free, moves."""
        c = row.get("_card")
        st = row["state"]
        if c is not None and c[0] == st and c[1] is self.free:
            return c
        shown = self._shown(row)
        sub = STATE_WORD.get(shown) or _mb(row["plan"]["store_bytes"])
        o = {"id": self._card_id(row)}
        if shown in BADGE:
            o["badge"] = BADGE[shown]
        if shown == "update":
            o["tone"] = "value"
        elif shown in BLOCKED:
            o["tone"] = "danger"
        o["icon"] = genre_icon(row["cart"])
        o_pic = dict(o)
        del o_pic["icon"]
        c = row["_card"] = (st, self.free, row["cart"]["name"], sub, o, o_pic)
        return c

    def _shelf(self, cv):
        lay = self.layout
        key = self._caption_key
        if key[0] != len(self.rows) or key[1] != self.unreached or key[2] is not self.free:
            left, right = self._caption()
            dot = " · " if ui.has_symbols() else " - "
            self._caption_key = (len(self.rows), self.unreached, self.free, left, right,
                                 left + (dot + right if right else ""),
                                 ui.text_size(right)[0] if right else 0)
            key = self._caption_key
        x, y, w, h = lay.caption
        if lay.desk:
            bw = 104
            ui.at(x, y, w - bw - 8, h)
            ui.label(key[5], _DIM_CAPTION)
            ui.at(x + w - bw, y, bw, h)
            ui.icon("rotate")
            if ui.button("Check", "normal", _CHECK):
                self._check()
        else:
            ui.at(x, y, w, h)
            ui.label(key[3], _DIM_CAPTION)
            if key[4]:
                rw = key[6]
                ui.at(x + w - rw, y, rw, h)
                ui.label(key[4], _DIM_CAPTION)
            if ui.hints_width(_BACK) > 0:
                r = lay.foot
                ui.at(r[0], r[1], r[2], r[3])
                ui.hints(_SHELF_HINTS)
        g = lay.grid
        ui.at(g[0], g[1], g[2], g[3])
        ui.scroll("shelf")
        side = lay.side
        ui.grid("carts", side, side, lay.grid_o)
        rows = self.rows
        for i in range(len(rows)):
            row = rows[i]
            c = self._card(row)
            pic = self._pic(row)
            r = ui.card(c[2], c[3], c[4] if pic is None else c[5])
            if r[2] and pic is not None:
                cv.clip(g[0], g[1], g[2], g[3])
                cv.cover(pic[1], pic[0], 0, 0, pic[0], pic[0], r[0], r[1], r[2], r[3], 0, False)
                cv.clip()
            if r[4] & ui.ACTIVATED:
                self._tap_row(i)
        ui.grid()
        if not rows:
            ui.label("No carts on the shelf yet", _DIM)
        ui.end()
        ui.scroll()

    def _pane_follows(self):
        """On the desk the pane shows the card focus is on."""
        f = self._focused
        if f is None or not f.startswith("Cart "):
            return
        for row in self.rows:
            if self._card(row)[4]["id"] == f and row is not self.cur:
                self.cur = row
                if self.phase == "list" or self.phase == "done":
                    self.phase = "cart"
                self._damage.all()
                return

    # -- a cart's page -----------------------------------------------------------

    def _cover(self, row, x, y, side):
        """The cart's cover at (x, y), `side` square: a card of its own with
        no name, its picture's edge and corners the engine's."""
        top = (6 if self.layout.desk else 4 * self.layout.fs)    # style.json's card_top
        ui.at(x, y - top, side, side + top)
        c = row.get("_cover")
        if c is None:
            c = row["_cover"] = ("cover " + row["cart"]["folder"],
                                 {"disabled": True, "icon": genre_icon(row["cart"])})
        ui.grid(c[0], side, side)
        pic = self._pic(row)
        r = ui.card("", "", c[1] if pic is None else _DISABLED)
        if r[2] and pic is not None:
            cv = self._surf.canvas()
            cv.cover(pic[1], pic[0], 0, 0, pic[0], pic[0], r[0], r[1], r[2], r[3], 0, False)
        ui.grid()

    def _meta(self, c):
        dot = " · " if ui.has_symbols() else " - "
        word = _cl.genre_word(c.get("genre"))
        src = c.get("shelf") or _host_of(c.get("index") or "")
        return dot.join([p for p in (word, src) if p])

    def _state_line(self, row):
        """(text, icon, tone) of where the cart stands, or None."""
        c = row["cart"]
        st = row["state"]
        arrow = " → " if ui.has_symbols() else " -> "
        if st == "update":
            if row.get("had"):
                return ("Update ready: %s%s%s" % (row["had"], arrow, c["version"]),
                        "getcarts", "value")
            return ("Update ready", "getcarts", "value")
        if st == "installed":
            return ("On console", "check", "value")
        if st == "taken":
            return ("Another cart here is already called %s." % c["folder"], "warn",
                    "danger")
        why = self.blocker(row) if st == "get" else None
        if why:
            return (why, "warn", "danger")
        return None

    def _info(self, row, desk):
        c, p = row["cart"], row["plan"]
        lic = c["licence"]
        out = [("Licence", lic.get("spdx") or lic.get("name") or "?")]
        dot = " · " if ui.has_symbols() else ", "
        arrow = " → " if ui.has_symbols() else " -> "
        if desk:
            if row["state"] == "update" and row.get("had"):
                out.append(("Version", "%s%s%s" % (row["had"], arrow, c["version"])))
            out.append(("Takes", _mb(p["store_bytes"]) + " here"))
            out.append(("Fetch", _mb(p["download_bytes"])))
        else:
            out.append(("Size", "%s here%s%s to fetch" % (_mb(p["store_bytes"]), dot,
                                                          _mb(p["download_bytes"]))))
        out.append(("From", _host_of(c.get("index") or "")))
        for e in p["external"]:
            out.append(("Needs", "%s from %s" % (e["path"], _ext_host(c, e))))
        if p["slow"]:
            out.append(("Plays slowly on this console", ""))
        return out

    def _cart_page(self, cv):
        """The handheld's page of one cart: its cover beside its genre and
        source, where it stands and its description; its facts; its verbs."""
        lay = self.layout
        fs = lay.fs
        row = self.cur
        c = row["cart"]
        x, y, w, h = lay.pane
        side = lay.side
        self._cover(row, x + 6 * fs, y, side)
        tx = x + side + 18 * fs
        tw = x + w - tx
        ty = y - fs
        ui.at(tx, ty, tw, 16 * fs)
        ui.label(self._meta(c), {"tone": "dim", "face": "caption"})
        ty += 16 * fs
        state = self._state_line(row)
        lines = 3
        if state is not None:
            text, icon, tone = state
            n = 1 if tone == "value" else 2
            ui.at(tx, ty, tw, n * 16 * fs)
            ui.icon(icon)
            ui.label(text, {"tone": tone, "wrap": n > 1, "lines": n})
            ty += n * 16 * fs + 2 * fs
            lines -= n
        if c.get("description") and lines > 0:
            ui.at(tx, ty, tw, lines * 14 * fs)
            ui.label(c["description"], {"wrap": True, "lines": lines, "face": "caption"})
        top = y + side + 8 * fs
        foot = 26 * fs + 16 * fs
        ui.at(x - 6 * fs, top, w + 12 * fs, y + h - top - foot - 4 * fs)
        ui.scroll("facts")
        ui.group("", {"card": True})
        for label, value in self._info(row, False):
            ui.info(label, value)
        ui.group()
        ui.scroll()
        self._verb_row(lay.page_verbs())
        if ui.hints_width(("back", "Back")) > 0:
            v = self._current_verbs()
            items = (("confirm", VERB[self._focused_verb(v)][0]) if v else ()) + ("back", "Back")
            ui.at(*lay.foot)
            ui.hints(items)

    def _focused_verb(self, verbs):
        for v in verbs:
            if vid(v) == self._focused:
                return v
        return verbs[-1]

    def _verb_row(self, rect):
        """The page's verbs across `rect`, the one that goes on last and
        wider; a verb a blocker stops is greyed."""
        verbs = self._current_verbs()
        if not verbs:
            return
        for v, r in zip(verbs, self.layout.split_verbs(rect, len(verbs))):
            self._button(v, r)

    def _kind(self, v):
        verbs = self._current_verbs()
        if v == "REMOVE" or (v == "CANCEL" and self.phase == "getting"):
            return "danger"
        if v in GO and v == verbs[-1]:
            return "primary"
        return "normal"

    def _button(self, v, rect):
        label, icon = VERB[v]
        o = self._button_o(v)
        ui.at(*rect)
        if icon:
            ui.icon(icon)
        if ui.button(label, self._kind(v), o):
            self._press(v)

    def _button_o(self, v):
        """Verb `v`'s button's override table: its id, and greyed where a
        blocker stops it."""
        o = {"id": vid(v)}
        if v in ("GET", "UPDATE") and self.phase in ("cart", "done") \
                and self.blocker(self.cur) is not None:
            o["disabled"] = True
        if v in ("PLAY", "USE") and self.phase in ("cart", "done") \
                and self._inst.find(self.cur["cart"]["folder"]) is None:
            o["disabled"] = True
        return o

    # -- the desk's pane and foot ------------------------------------------------

    def _pane(self, cv):
        lay = self.layout
        x, y, w, h = lay.rule
        border = self._theme.colors()["border"]
        cv.rect(x, y, w, h, border)
        row = self.cur if self.cur is not None else (self.rows[0] if self.rows else None)
        fx, fy, fw, fh = lay.foot
        bx = lay.body[0]
        cv.rect(bx + 16, fy, lay.body[2] - 32, 1, border)
        if row is None:
            return
        c = row["cart"]
        px, py, pw, ph = lay.pane
        side = lay.side
        self._cover(row, px, py, side)
        busy = self.phase in BUSY
        k = None if busy else self._pane_parts(row)
        tx = px + side + 14
        tw = px + pw - tx
        ui.at(tx, py + 10, tw, 28)
        ui.label(c["name"], _TITLE)
        ty = py + 40
        if k is None:
            word = _cl.genre_word(c.get("genre"))
            src = c.get("shelf") or _host_of(c.get("index") or "")
        else:
            word = k.word
            src = k.src
        if word:
            ui.at(tx, ty, tw, 20)
            ui.label(word, _DIM_CAPTION)
            ty += 20
        if src:
            ui.at(tx, ty, tw, 20)
            ui.label(src, _DIM_CAPTION)
            ty += 20
        if k is not None and k.state is not None:
            ui.at(tx, ty + 4, tw, k.state_h)
            ui.icon(k.state_icon)
            ui.label(k.state, k.state_o)
        dy = py + side + 16
        if c.get("description"):
            ui.at(px, dy, pw, 60)
            ui.label(c["description"], _WRAP3)
        dy += 72
        if busy:
            self._pane_busy((px, dy, pw, fy - dy))
        else:
            ui.at(px - 10, dy, pw + 10, fy - dy - 8)
            ui.scroll("facts")
            ui.group("", _CARD_GROUP)
            for f in k.info:
                ui.info(f[0], f[1])
            ui.group()
            ui.scroll()
        if k is None:
            self._foot(row)
            return
        ui.at(k.status_at[0], k.status_at[1], k.status_at[2], k.status_at[3])
        ui.label(k.status, _DIM_CAPTION)
        for b in k.buttons:
            r = b[1]
            ui.at(r[0], r[1], r[2], r[3])
            if b[3]:
                ui.icon(b[3])
            if ui.button(b[2], b[4], b[5]):
                self._press(b[0])

    def _pane_parts(self, row):
        """What the desk's pane and foot draw for `row` outside a job: made
        again when the cart, where it stands, the phase or the room free
        moves, so a frame that only scrolls the shelf makes nothing."""
        k = self._pane_k
        if (k is not None and k.row is row and k.st == row["state"] and k.phase == self.phase
                and k.free is self.free):
            return k
        k = self._pane_k = _Pane()
        k.row = row
        k.st = row["state"]
        k.phase = self.phase
        k.free = self.free
        c = row["cart"]
        k.word = _cl.genre_word(c.get("genre"))
        k.src = c.get("shelf") or _host_of(c.get("index") or "")
        state = self._state_line(row)
        if state is not None:
            text, icon, tone = state
            k.state = text.split(":")[0] if tone == "value" else text
            k.state_icon = icon
            k.state_h = 22 if tone == "value" else 44
            k.state_o = {"tone": tone, "wrap": tone != "value", "lines": 2}
        k.info = self._info(row, True)
        lay = self.layout
        fx, fy, fw, fh = lay.foot
        verbs = self._current_verbs()
        ui.face("caption")
        widths = []
        for v in verbs:
            label, icon = VERB[v]
            widths.append(ui.text_size(label)[0] + (48 if icon else 28))
        ui.face()
        bx = fx + fw - 16 - sum(widths) - 8 * max(0, len(verbs) - 1)
        by = fy + max(1, (fh - 30) // 2)
        k.status = self._status(row)
        k.status_at = (fx + 26, by, max(0, bx - fx - 34), 30)
        for i, v in enumerate(verbs):
            label, icon = VERB[v]
            k.buttons.append((v, (bx, by, widths[i], 30), label, icon, self._kind(v),
                              self._button_o(v)))
            bx += widths[i] + 8
        return k

    def _pane_busy(self, rect):
        x, y, w, h = rect
        title, line, done, total = self._progress_of()
        ui.at(x, y, w, 24)
        ui.label(title, {"face": "ui"})
        if total:
            ui.at(x, y + 26, w, 38)
            ui.progress("Downloading", done, total)
        if line:
            ui.at(x, y + 70, w, 20)
            ui.label(line, {"tone": "dim", "face": "caption"})

    def _foot(self, row):
        """The window's foot: the status at its left, the verbs at its
        right, the one that goes on last and primary."""
        lay = self.layout
        fx, fy, fw, fh = lay.foot
        verbs = self._current_verbs()
        ui.face("caption")
        widths = []
        for v in verbs:
            label, icon = VERB[v]
            widths.append(ui.text_size(label)[0] + (48 if icon else 28))
        ui.face()
        bx = fx + fw - 16 - sum(widths) - 8 * max(0, len(verbs) - 1)
        status = self._status(row)
        ui.at(fx + 26, fy + max(1, (fh - 30) // 2), max(0, bx - fx - 34), 30)
        ui.label(status, {"tone": "dim", "face": "caption"})
        for i, v in enumerate(verbs):
            self._button(v, (bx, fy + max(1, (fh - 30) // 2), widths[i], 30))
            bx += widths[i] + 8

    def _status(self, row):
        dot = " · " if ui.has_symbols() else " - "
        if self.phase in BUSY:
            return self._progress_of()[0]
        st = self._shown(row)
        word = STATE_WORD.get(st)
        name = row["cart"]["name"]
        return name + dot + word[0].lower() + word[1:] if word else name

    # -- the getting -------------------------------------------------------------

    def _progress_of(self):
        """(title, line, done, total) of the work in hand."""
        name = self.cur["cart"]["name"] if self.cur is not None else ""
        job = self.job
        if self.phase == "getting" and job is not None:
            if job.keeping:
                return ("Getting " + name, "Putting it on the shelf…", job.total, job.total)
            dot = " · " if ui.has_symbols() else ", "
            line = "%s of %s" % (_mb(job.done), _mb(job.total))
            rate = job.rate()
            if rate:
                line += "%s%d KB/s" % (dot, rate // 1024)
            return ("Getting " + name, line, job.done, job.total)
        title, line = self._busy_words()
        return (title, line, 0, 0)

    def _getting(self, cv):
        lay = self.layout
        fs = lay.fs
        x, y, w, h = lay.pane
        side = lay.side
        self._cover(self.cur, x + (w - side) // 2, y, side)
        title, line, done, total = self._progress_of()
        ty = y + side + 7 * fs
        ui.at(x, ty, w, 22 * fs)
        ui.label(title, {"face": "title", "align": "center"})
        ty += 24 * fs
        bx, bw = x + 34 * fs, w - 68 * fs
        ui.at(bx, ty, bw, 27 * fs)
        if self.job is not None and self.job.keeping:
            ui.progress("Putting it on the shelf", total, total)
        else:
            ui.progress("Downloading", done, total)
        ty += 30 * fs
        if line and not (self.job is not None and self.job.keeping):
            ui.at(x, ty, w, 16 * fs)
            ui.label(line, {"tone": "dim", "face": "caption", "align": "center"})
        ty += 18 * fs
        if self._current_verbs():
            bw = 96 * fs
            self._button("CANCEL", (x + (w - bw) // 2, ty, bw, 26 * fs))
        if ui.hints_width(("back", "Back")) > 0 and self._current_verbs():
            ui.at(*lay.foot)
            ui.hints(("back", "Cancel"))

    # -- the pages that are words and a verb -------------------------------------

    def _busy_words(self):
        ph = self.phase
        if ph == "checking":
            return ("Checking", "Looking for carts…")
        if ph == "licence_fetch":
            return ("One moment", "Getting the licence…")
        if ph == "probing":
            e = self._external()
            return ("One moment", "Looking for %s…" % (e["path"] if e else "it"))
        if ph == "picked":
            return ("Your copy", "Checking your copy…")
        if ph == "connecting":
            return ("Connecting", "Turning on Wi-Fi…")
        return ("", "")

    def _busy(self):
        """One busy page: the app's icon, a title, a line, the shelves
        counted where they are, Cancel."""
        lay = self.layout
        title, line = self._busy_words()
        x, y, w, h = lay.pane if not lay.desk else lay.full
        top = y + (h // 6 if lay.desk else 0)
        ui.at(x, top, w, h)
        at = ui.empty("getcarts", title, (line,))
        n = len(self.indexes)
        if self.phase == "checking" and n and self._step:
            step = max(0, min(self._step - 1, n))
            bx, bw = x + w // 6, w - w // 3
            ui.at(bx, at + 2 * lay.fs, bw, (38 if lay.desk else 27 * lay.fs))
            ui.progress("Shelf %d of %d" % (min(step + 1, n), n), step, n)
            at += 30 * lay.fs
        bw = (120 if lay.desk else 96 * lay.fs)
        self._button("CANCEL", (x + (w - bw) // 2, at + 6 * lay.fs, bw,
                                40 if lay.desk else 26 * lay.fs))
        if not lay.desk and ui.hints_width(("back", "Back")) > 0:
            ui.at(*lay.foot)
            ui.hints(("back", "Cancel"))

    def _empty_words(self):
        """(icon, title, lines) for the pages that say why there is no
        shelf, or how a getting ended."""
        ph = self.phase
        if ph == "board":
            lines = self._lines("This page shows the carts kept on the console that "
                                "served it.")
            if self._inst.home() == "headless":
                lines += self._lines("A console with no screen can't get carts.")
            else:
                lines += self._lines("Get new carts on the console itself: turn Web "
                                     "console off there and open Get Carts.")
            return ("getcarts", "On the console", lines)
        if ph == "nonet":
            return ("warn", "No internet", ["This console has no way to fetch carts."])
        if ph == "nowifi":
            return ("wifi_off", "Wi-Fi is off", ["Join a network and Get Carts",
                                                 "looks again by itself."])
        if ph == "unreached":
            return ("warn", "Shelf away", ["Couldn't reach the cart shelf.",
                                           "Try again soon."])
        if ph == "nomemory":
            return ("warn", "Memory full", self._lines(_ci.NET_MEMORY))
        if ph == "failed":
            return ("warn", "Not installed", self._lines(self.why) + ["Nothing changed."])
        return ("getcarts", "", [])

    def _lines(self, text):
        """`text` broken at word breaks into the empty page's width."""
        lay = self.layout
        room = (lay.full[2] if lay.desk else lay.pane[2]) - 32 * lay.fs
        if lay.desk:
            room = min(room, 420)
        ui.face("ui")
        out, line = [], ""
        try:
            for word in str(text).split():
                t = word if not line else line + " " + word
                if line and ui.text_size(t)[0] > room:
                    out.append(line)
                    line = word
                else:
                    line = t
            if line:
                out.append(line)
        finally:
            ui.face()
        return out

    def _empty(self):
        lay = self.layout
        fs = lay.fs
        icon, title, lines = self._empty_words()
        x, y, w, h = lay.pane if not lay.desk else lay.full
        top = y + (h // 6 if lay.desk else 0)
        ui.at(x, top, w, h)
        at = ui.empty(icon, title, lines)
        verbs = self._current_verbs()
        if verbs:
            bh = 40 if lay.desk else 26 * fs
            bw = 180 if lay.desk else 124 * fs
            mw = bw - (40 if lay.desk else 30 * fs)
            g = 10 if lay.desk else 8 * fs
            widths = [mw] * (len(verbs) - 1) + [bw]
            bx = x + (w - sum(widths) - g * (len(verbs) - 1)) // 2
            for i, v in enumerate(verbs):
                self._button(v, (bx, at + (6 if lay.desk else 3 * fs), widths[i], bh))
                bx += widths[i] + g
        if not lay.desk and ui.hints_width(("back", "Back")) > 0:
            items = ()
            if verbs:
                items = ("confirm", VERB[self._focused_verb(verbs)][0])
            ui.at(*lay.foot)
            ui.hints(items + ("back", "Back"))

    def _your_copy(self):
        lay = self.layout
        fs = lay.fs
        x, y, w, h = lay.pane if not lay.desk else lay.full
        if lay.desk:
            x, y, w = x + 24, y + 16, w - 48
        title, lines = self._copy_words()
        ui.at(x, y, w, 22 * fs)
        ui.label(title, {"face": "title"})
        ty = y + 26 * fs
        for text, tone in lines:
            ui.at(x, ty, w, 32 * fs)
            ui.label(text, {"tone": tone, "wrap": True, "lines": 2})
            ty += 34 * fs
        bw = 96 * fs
        self._button("CANCEL", (x + w - bw, y + h - 30 * fs, bw, 26 * fs))
        if not lay.desk and ui.hints_width(("back", "Back")) > 0:
            ui.at(*lay.foot)
            ui.hints(("back", "Cancel"))

    def _copy_words(self):
        """(title, [(line, tone)]) of YOUR COPY's page."""
        e = self._external()
        lines = []
        if self.pick_why is not None:
            lines.append((self.pick_why, "danger"))
        lines.append(("This console can't fetch %s from %s."
                      % (e["path"], _ext_host(self.cur["cart"], e)), "ink"))
        lines.append(("Choose your own copy of %s (%s) in the box below."
                      % (e["path"], _mb(e["size"])), "dim"))
        return "Your copy of %s" % e["path"], lines

    def _licence(self, cv, th):
        """A file's licence before it is fetched: its name, its text in a
        well the arrows scroll while it holds focus, No and I agree."""
        lay = self.layout
        fs = lay.fs
        ext, lines, first = self.lic
        x, y, w, h = lay.well()
        ui.at(x, y - 26 * fs, w, 22 * fs)
        ui.label(ext["licence"].get("name") or "Its licence", {"face": "title"})
        ui.at(x, y, w, h)
        bx, by, bw, bh, _st = ui.part("Licence")
        ui.face("ui")
        lh = ui.text_size("Ag")[1]
        pad = 8 if lay.desk else 6 * fs
        cv.clip(bx + 1, by + 1, bw - 2, bh - 2)
        ty = by + pad // 2
        ink = th["ink"]
        for ln in (lines or ())[first:]:
            if ty >= by + bh:
                break
            cv.print(ln, bx + pad, ty, ink)
            ty += lh
        cv.clip()
        ui.face()
        if lines and first + self._well_rows() < len(lines):
            ui.fade((bx + 1, by + bh - 12 * fs, bw - 2, 12 * fs - 1))
        no, agree = lay.licence_verbs()
        self._button("NO", no)
        self._button("I AGREE", agree)
        if not lay.desk and ui.hints_width(("back", "Back")) > 0:
            ui.at(*lay.foot)
            f = self._focused
            ui.hints(("up down", "Scroll", "back", "No") if f == "Licence"
                     else ("confirm", VERB[self._focused_verb(("NO", "I AGREE"))][0],
                           "back", "No"))

    # -- the dialog and the toast ------------------------------------------------

    def _dialog(self):
        if not self.asking or self.cur is None:
            return
        name = self.cur["cart"]["name"]
        pick = ui.dialog("Remove %s?" % name, ("Cancel", "Remove"), "danger")
        ui.label("Its saves and the files it wrote go too.", {"wrap": True, "lines": 3})
        ui.dialog()
        if pick == 1:
            self._remove()
        elif pick == 0:
            self.asking = False
            self._focus = vid("REMOVE")
            self._damage.all()

    def _toast(self):
        lay = self.layout
        text, icon, _until = self.toast
        if lay.desk:
            fx, fy, fw, fh = lay.foot
            ui.at(lay.body[0], lay.body[1], lay.body[2], fy - lay.body[1] - 12)
        elif self.phase in ("cart", "done"):
            x, y, w, h = lay.pane                 # over the verbs, not on them
            ui.at(x, y, w, h - (26 + 16 + 6) * lay.fs)
        else:
            x, y, w, h = lay.body
            ui.at(x, y, w, h - 6 * lay.fs)
        ui.toast(text, icon)
