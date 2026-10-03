"""Get Carts -- the console's store (#124): carts other people publish,
browsed and installed over WiFi with no PC involved.

The screens, in the order a kid meets them:

  CHECKING   the radio comes up under the "carts" lease and every index is
             fetched -- moybyte-org's carts repository unless `indexes.json`
             beside the carts folder names others (runtime/cart_index.py) --
             then the covers they name that this session has not drawn yet.
  LIST       a big row per cart: its cover when its index names one, its
             name, licence and size here, and where it stands -- GET, ON
             CONSOLE, UPDATE, NAME TAKEN when a different cart already has its
             folder, TOO BIG or CAN'T PLAY. A row's cover is the reduction of
             the 128 x 128 cover.png (SPEC.md 3.6) whose side is nearest the
             row's height, centred on it, decoded once and kept at that size
             (the file itself is not kept); one that does not come, or does
             not decode, leaves the row as it is without one.
  CART       one cart: where it comes from, its licence, what it takes and
             needs, and its verbs -- GET or UPDATE, PLAY, REMOVE. A cart that
             will not fit in the store, that the engine would refuse to load
             (the Player's check, asked of the index before the download), or
             that this console cannot play says why and offers no GET.
  LICENCE    a file the cart needs that is not in its release (Doom's WAD)
             shows its licence before it is fetched. NO has the focus;
             I AGREE is the only way on.
  YOUR COPY  a console that can be handed a file (the browser) first asks
             whether it can read that file from anywhere the index names --
             its mirror beside the index, then its archive's hosts; where it
             cannot, the player chooses their own copy, which is held to the
             index's size and sha256 before anything is downloaded.
  GETTING    the download, a slice per frame (`cart_index.Install.step`), with
             its progress and CANCEL -- until the build is checked and goes to
             a store of record that is not these files (the browser's), when
             it can no longer be cancelled and says so.
  READY / NOT INSTALLED  how it ended. A failed or cancelled install changed
             nothing on the shelf.
  ON THE CONSOLE  a page a board serves: the carts it shows are the board's,
             and the board gets its own, so this one fetches nothing.

The installing is cart_index's; this module is the screens and the lease. The
radio is held while something is fetched -- the indexes, a licence and the
download it leads to -- and let go as soon as that ends: never while the kid
browses, never into a cart (PLAY lets go first), never past `close()`.

Work that may wait (dialling the network, fetching an index) runs one frame
after its screen is shown, so CHECKING is on the glass before the wait starts
-- the update screen's arm (runtime/update_ui.py). Every fetch is a
`cart_index` job stepped once a frame, which on a board or the host finishes
in that step and in the browser gives the frame back until its bytes come.
"""

import json

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui

try:
    from app_shell import ListShellLayout, ListShellApp
except ImportError:  # pragma: no cover - direct host import
    from runtime.app_shell import ListShellLayout, ListShellApp

try:
    import cart_index as _ci
    from moy_fs import _exists, _read
    from ticks import _ticks_ms, _ticks_diff
    from player import fit_notice as _fit_notice
except ImportError:  # pragma: no cover - direct host import
    from runtime import cart_index as _ci
    from runtime.moy_fs import _exists, _read
    from runtime.ticks import _ticks_ms, _ticks_diff
    from runtime.player import fit_notice as _fit_notice


# An index fetched this session is reused for this long before the app checks
# again on its own; CHECK on the list asks at once.
REFRESH_MS = 600000

STATE_LABEL = {"get": "GET", "installed": "ON CONSOLE", "update": "UPDATE",
               "taken": "NAME TAKEN", "too_big": "TOO BIG", "noplay": "CAN'T PLAY"}

# An external file's archive is held in RAM while it is checked and unpacked
# when it is under this share of the largest free block a compiled cart loads
# into, and kept in a file beside the build otherwise. The P4s' 32 MB clear it
# (and their flash store writes slowly); on an S3 the archive would sit in a
# Python heap that never shrinks, out of reach of the cart it was fetched for
# until a reboot, while its card writes fast.
ARCHIVE_RAM_SHARE = 4

# The phases whose work runs a slice per frame from draw().
BUSY = ("checking", "licence_fetch", "probing", "picked", "connecting", "getting")


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


class GetCartsLayout(ListShellLayout):
    MIN_W = 300
    MIN_H = 220

    def __init__(self, w, h, fs=1, windowed=False, cs=None):
        self._init_frame(w, h, fs, windowed, cs)
        fs = self.fs
        self.top_h = 24 * fs
        hy, hh = self.bar_h + 2 * fs, self.top_h - 4 * fs
        self.head = (4 * fs, hy, 50 * fs, hh)
        self.head2 = (self.w - 70 * fs, hy, 66 * fs, hh)
        self.status_h = 14 * fs
        body_y = self.bar_h + self.top_h + 2 * fs
        self.body = (4 * fs, body_y, self.w - 8 * fs,
                     max(60 * fs, self.h - self.status_h - body_y - 4 * fs))
        bx, by, bw, bh = self.body
        self.btn_h = 26 * fs
        self.btns = (bx, by + bh - self.btn_h, bw, self.btn_h)
        self.cols = max(10, (bw - 8 * fs) // (8 * fs))
        self.text_y = by + 4 * fs
        self.line_h = 12 * fs
        self.text_rows = max(1, (self.btns[1] - self.text_y - 4 * fs) // self.line_h)
        self.row_h = 30 * fs
        rh = self.row_h - 2 * fs
        side = 16
        while side < 128 and abs(2 * side - rh) < abs(side - rh):
            side *= 2
        self.thumb = side              # a row cover's side
        self.list_y = by + 2 * fs
        self.list_rows = max(1, (by + bh - self.list_y) // self.row_h)

    def buttons(self, n):
        return _ui.hsplit(self.btns, n, gap=6 * self.fs)

    def lic_area(self):
        """The licence box's VIEWPORT: two fixed header lines below `text_y`
        (the external path, then the licence name), then every remaining text
        row down to the buttons. The scroll model needs the whole band where
        the draw needs a slot, and both come off this one formula."""
        page = max(1, self.text_rows - 2)
        return (self.body[0], self.text_y + 2 * self.line_h, self.body[2],
                page * self.line_h)


class GetCartsAppLayer(ListShellApp):
    id = "getcarts"
    domain = "system"
    TITLE = "GET CARTS"
    APP_TITLE = "Get Carts"
    APP_PERM = "getcarts"
    APP_FOLDER = "get_carts.moy"
    # `install` is the network, its lease and the store an install writes;
    # `nav` is PLAY.
    NEEDS = ("surface", "theme", "damage", "install", "nav")

    def __init__(self, ctx, names):
        self.ctx = ctx
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._inst = ctx.install
        self._nav = ctx.nav
        self.names = names
        cv = self._surf.canvas()
        self.layout = GetCartsLayout(cv.w, cv.h, self._surf.font_scale(),
                                     self._surf.windowed(),
                                     self._surf.chrome_scale())
        self.hits = _ui.Hits()
        self.phase = "list"
        self.carts = []               # every index's carts, this session
        self.rows = []                # [{"cart", "plan", "state", "runs"}]
        self.fetched_at = None
        self.indexes = ()
        self.unreached = 0
        self.starved = False          # an index failed for want of memory
        self.free = None              # (bytes, block) of the store
        self.sel = 0
        self.top = 0
        self.cur = None               # the CART screen's row
        self.focus = 0
        self.job = None
        self.lic = None               # [external, wrapped lines or None, scroll px]
        self._lic_text = ""
        self._lic_scroll = None       # lazy ui.ScrollRegion over the licence text
        self._lic_taps = None
        self._lic_frame_dt_ms = 33.0  # last draw() tick, for the drag's fling EMA
        self.accepted = []
        self.armed = False
        self.arm_remove = False
        self.why = ""                 # the NOT INSTALLED screen's reason
        self.status = ""
        self.thumbs = {}              # cover sha256 -> (side, RGB565) or None
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
        self._rows_scroll = None      # ListShellApp's touch model
        self._rows_taps = None

    # -- the app protocol (docs/app_api_v1.md) --------------------------------

    def open(self):
        self.arm_remove = False
        self.job = None               # one the keeper still holds lands by itself
        self.lic = None
        if self._inst.home() is not None:
            self._go("board")
        elif self._inst.net() is None:
            self._go("nonet")
        else:
            self._recover()
            fresh = (self.fetched_at is not None
                     and _ticks_diff(_ticks_ms(), self.fetched_at) < REFRESH_MS)
            if self.carts and fresh:
                self._build_rows()
                self._go("list")
            else:
                self._check()
        self._damage.all()

    def relayout(self, w, h, fs, cs=None):
        self.layout = GetCartsLayout(w, h, fs, self._surf.windowed(), cs)
        if self.lic is not None and self.lic[1] is not None:
            self.lic[1] = self._wrap(self._lic_text)
            self.lic[2] = 0
            self._lic_reset_scroll()
        self._clamp_list(len(self.rows))

    def close(self):
        if self.job is not None and not self.job.finished and self.job.cancel():
            self._go("cart" if self.cur is not None else "list")
        self._drop_fetch()
        self._unpick()
        self._release()

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

    def _go(self, phase, focus=0):
        self.phase = phase
        self.armed = False
        self.focus = focus
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
            self._inst.session(lambda: _ci.recover(root))
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
                self.indexes = self._inst.session(lambda: _ci.load_indexes(root))
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

    def _covers_to_fetch(self):
        side = self.layout.thumb
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
        side = self.layout.thumb
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
                            "runs": c["runtime"] in _ci.RUNTIMES
                            and c["runtime"] in have,
                            "fit": None})
            return out, (inst.free() if root is not None else None)
        try:
            rows, free = inst.session(_scan)
        except Exception as exc:  # noqa: BLE001 -- an unreadable store lists everything as GET
            _ci._log("store scan failed: %s" % exc)
            rows = [{"cart": c, "plan": _ci.plan(c, chip, fmt, ranges), "state": "get",
                     "runs": False, "fit": None} for c in carts]
            free = None
        for r in rows:
            r["fit"] = self._fit_why(r)
        rows.sort(key=lambda r: r["cart"]["name"].lower())
        self.rows = rows
        self.free = free
        if self.sel >= len(rows):
            self.sel = max(0, len(rows) - 1)
        self._clamp_list(len(rows))
        if self.cur is not None:
            folder = self.cur["cart"]["folder"]
            ident = self.cur["cart"]["id"]
            self.cur = None
            for r in rows:
                if r["cart"]["folder"] == folder and r["cart"]["id"] == ident:
                    self.cur = r

    # -- the CART screen's verbs ---------------------------------------------------

    def _tap_row(self, i):
        if 0 <= i < len(self.rows):
            self.sel = i
            self.cur = self.rows[i]
            self.arm_remove = False
            self.status = ""
            self._go("cart")

    def _verbs(self):
        st = self.cur["state"] if self.cur is not None else None
        if st == "get":
            return ("GET",)
        if st == "installed":
            return ("PLAY", "REMOVE")
        if st == "update":
            return ("UPDATE", "PLAY", "REMOVE")
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

    def _enabled(self, verb):
        if verb in ("GET", "UPDATE"):
            return self.blocker(self.cur) is None
        if verb == "PLAY":
            return self._inst.find(self.cur["cart"]["folder"]) is not None
        return True

    def _press(self, verb):
        ph = self.phase
        if ph == "cart":
            if verb in ("GET", "UPDATE"):
                self._start()
            elif verb == "PLAY":
                self._play()
            elif verb == "REMOVE":
                self._remove()
        elif ph == "licence":
            if verb == "I AGREE":
                self.accepted.append(self.lic[0]["path"])
                self._next_licence()
            else:
                self._release()
                self.status = "NOT FETCHED"
                self._go("cart")
        elif ph in BUSY or ph == "pick":
            self._cancel()
        elif ph == "done":
            if verb == "PLAY":
                self._play()
            else:
                self._go("list")
        elif ph in ("nowifi", "unreached", "nomemory"):
            self._check()
        elif ph == "failed":
            self._go("cart" if self.cur is not None else "list")

    def _start(self):
        if self.blocker(self.cur) is not None:
            return
        self.accepted = []
        self.supplied = {}
        self.pick_why = None
        self.arm_remove = False
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
        self._lic_reset_scroll()
        self._go("licence", focus=1)       # NO has the focus

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
            self.status = "NOT FETCHED"
            self._go("cart")
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
        cols = self.layout.cols
        out = []
        for para in text.split("\n"):
            para = para.strip()
            out.extend(_ui.wrap_words(para, cols) if para else [""])
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
                               self._inst.root(), self._inst.session, self.accepted,
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
        elif job.error:
            self._fail(job.error)
        else:
            self._go("cart")

    def _cancel(self):
        """B, or a tap on CANCEL -- never A, which a kid presses at anything.
        Not once the keeper has the build: it lands."""
        checking = self.phase == "checking"
        if self.job is not None and not self.job.finished and not self.job.cancel():
            return
        self._drop_fetch()
        self._unpick()
        self._release()
        self.status = "STOPPED" if checking else "STOPPED. NOTHING CHANGED."
        self._go("cart" if self.cur is not None and not checking else "list")

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
        self._nav.play(cart, self)

    def _remove(self):
        if not self.arm_remove:
            self.arm_remove = True
            self.status = "TAP REMOVE AGAIN"
            self._damage.all()
            return
        self.arm_remove = False
        row = self.cur
        root = self._inst.root()
        keep = self._inst.keep()
        try:
            self._inst.session(lambda: _ci.remove(root, row["cart"]["folder"], keep))
        except Exception as exc:  # noqa: BLE001 -- the folder stays; say so
            _ci._log("remove failed: %s" % exc)
            self.status = "CAN'T REMOVE IT"
            self._damage.all()
            return
        self._inst.rescan()
        self._build_rows()
        self.cur = None
        self.status = ("REMOVED " + row["cart"]["name"]).upper()
        self._go("list")

    def _back(self):
        ph = self.phase
        if ph == "licence":
            self._press("NO")
        elif ph in BUSY or ph == "pick":
            self._cancel()
        elif ph in ("cart", "done", "failed"):
            self.status = ""
            self._go("list")

    # -- input -----------------------------------------------------------------

    def _current_verbs(self):
        ph = self.phase
        if ph == "cart":
            return self._verbs()
        if ph == "licence":
            return ("I AGREE", "NO")
        if ph == "getting" and self.job is not None and self.job.keeping:
            return ()
        if ph in BUSY or ph == "pick":
            return ("CANCEL",)
        if ph == "done":
            return ("PLAY", "OK")
        if ph in ("nowifi", "unreached", "nomemory"):
            return ("TRY AGAIN",)
        if ph == "failed":
            return ("OK",)
        return ()

    def handle_input(self, i):
        ph = self.phase
        if ph == "list":
            if self.rows:
                self._list_nav(i, len(self.rows))
            return True
        if ph == "licence" and self.lic is not None and self.lic[1] is not None:
            if i.pressed("up"):
                self._scroll_licence(-1)
                return True
            if i.pressed("down"):
                self._scroll_licence(1)
                return True
        verbs = self._current_verbs()
        if verbs and (i.pressed("left") or i.pressed("right")):
            step = -1 if i.pressed("left") else 1
            self.focus = (self.focus + step) % len(verbs)
            self._damage.all()
        elif i.pressed("a") and verbs and ph not in BUSY and ph != "pick":
            verb = verbs[min(self.focus, len(verbs) - 1)]
            if ph != "cart" or self._enabled(verb):
                self._press(verb)
        elif i.pressed("b"):
            self._back()
        return True

    def _scroll_licence(self, d):
        """Key/d-pad step: one line (`d` is ±1), in the SAME pixel offset the
        drag and the fling share (`_lic_region`) -- a keyboard step kills a
        live fling, the way `_sync_scroll_from_top` does for the Settings
        rows."""
        sr = self._lic_region()
        sr.scroll_by(d * self.layout.line_h)
        sr.stop()
        self.lic[2] = sr.offset
        self._damage.all()

    # -- the licence text's TOUCH model (#113) --------------------------------
    #
    # The text carries no selection to preserve (unlike the row lists), so it
    # rides the Library shelf's pattern rather than the Settings rows': the
    # region's offset IS `self.lic[2]`, pixel-smooth, with no page snapping.

    def _lic_region(self):
        """The licence text's ScrollRegion + drag/fling machine, built lazily
        and re-synced to the live layout each sample."""
        if self._lic_scroll is None:
            self._lic_scroll = _ui.ScrollRegion()
            self._lic_taps = _ui.DragTap(self._lic_scroll)
        sr = self._lic_scroll
        lay = self.layout
        sr.set(lay.lic_area(), len(self.lic[1]) * lay.line_h)
        if not (sr.drag_active or sr.animating):
            sr.offset = self.lic[2]
        return sr

    def _lic_reset_scroll(self):
        """A fresh licence (a new fetch, a relayout) starts at the top and
        drops any fling still coasting from the one before it."""
        if self._lic_scroll is not None:
            self._lic_scroll.stop()
            self._lic_scroll.offset = 0

    def _lic_pointer(self, px, py, click):
        """One pointer sample over the licence text: a held drag SCROLLS it
        (`self.lic[2]` follows the region's offset); a clean release does
        nothing -- the text is not a tap target, only YES/NO are.

        `dt_ms` feeds the release-velocity EMA (the fling, #113): `ctx.surface`
        carries no per-sample dt of its own (the kernel's banked pointer dt is
        a `Workstation` internal), so this rides `_lic_frame_dt_ms` -- the
        loop's own last `draw()` tick, injected there, never a clock read
        here."""
        sr = self._lic_region()
        self._lic_taps.frame(px, py, click, self._surf.pointer().down,
                             slop=4 * self.layout.fs + 2,
                             dt_ms=self._lic_frame_dt_ms)
        if self._lic_taps.dragging:
            self.lic[2] = sr.offset
            self._damage.all()

    def handle_pointer(self, px, py, click):
        lay = self.layout
        if self.hits.pointer_frame(px, py, self._surf.pointer()):
            self._damage.all()
        if self.phase == "list" and not _ui.rect_in(px, py, lay.head2):
            row = self._rows_pointer(px, py, click, len(self.rows))
            if row is not None:
                self._tap_row(row)
            return True
        if self.phase == "licence" and self.lic is not None and self.lic[1] is not None:
            self._lic_pointer(px, py, click)
        if not click:
            return True
        hit = self.hits.at(px, py)
        if hit is None:
            return True
        verb, arg = hit
        if verb == "check":
            self._check()
        elif verb == "back":
            self._back()
        elif verb == "btn" and (self.phase != "cart" or self._enabled(arg)):
            self._press(arg)
        return True

    # -- draw ------------------------------------------------------------------

    def draw(self, dt):
        self._pump()
        if dt > 0:
            self._lic_frame_dt_ms = min(dt * 1000.0, 100.0)
        if self.phase == "licence" and self._lic_scroll is not None:
            # A coasting fling advances BEFORE the text paints, so the lines
            # below are drawn at this frame's offset (settings_layer's
            # rows_anim_frame does the same ahead of its own draw).
            if self._lic_scroll.tick(self._lic_frame_dt_ms):
                self.lic[2] = self._lic_scroll.offset
                self._damage.all()
            if self._lic_scroll.animating:
                self._damage.again()
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        fs = lay.fs
        self.hits.clear()
        cv.cls(th["panel"])
        _ui.toolbar(cv, th, (0, lay.bar_h, lay.w, lay.top_h))
        ph = self.phase
        if ph == "list":
            cv.print("PICK A CART", 8 * fs, lay.bar_h + 8 * fs, th["title_ink"], 1)
            self._chip(cv, th, "CHECK", lay.head2, "check")
            self._draw_list(cv, th)
        else:
            if ph not in BUSY and ph not in ("pick", "nonet", "nowifi", "unreached",
                                             "nomemory", "board"):
                self._chip(cv, th, "<", lay.head, "back")
            title = self.cur["cart"]["name"] if self.cur is not None else "GET CARTS"
            x = lay.head[0] + lay.head[2] + 8 * fs
            cv.print(title[:max(1, (lay.w - x) // (8 * fs) - 1)], x,
                     lay.bar_h + 8 * fs, th["title_ink"], 1)
            if ph == "cart":
                self._draw_cart(cv, th)
            elif ph == "licence":
                self._draw_licence(cv, th)
            else:
                self._draw_message(cv, th)
            self._draw_buttons(cv, th)
        cv.rect(0, lay.h - lay.status_h, lay.w, lay.status_h, self.names["black"])
        cv.print(self._status_text()[:max(1, lay.w // (8 * fs) - 1)], 4 * fs,
                 lay.h - lay.status_h + 3 * fs, self.names["yellow"], 1)

    def _chip(self, cv, th, label, rect, verb):
        _ui.chip(cv, th, rect, label, fs=self.layout.fs,
                 state=self.hits.state_of(verb))
        self.hits.add(rect, verb)

    def _status_text(self):
        if self.status:
            return self.status
        if self.phase == "list":
            n = len(self.rows)
            text = "%d CART%s" % (n, "" if n == 1 else "S")
            if self.free is not None:
                text += "  %s FREE" % _mb(self.free[0])
            if self.unreached:
                text += "  %d SHELF AWAY" % self.unreached
            return text
        if self.phase == "cart" and self.cur is not None:
            return self.cur["cart"].get("shelf", "").upper()
        return ""

    def _line(self, cv, text, y, ink):
        lay = self.layout
        cv.print(text[:lay.cols], lay.body[0] + 4 * lay.fs, y, ink, 1)
        return y + lay.line_h

    def _draw_list(self, cv, th):
        lay = self.layout
        fs = lay.fs
        if not self.rows:
            self._line(cv, "NO CARTS ON THE SHELF YET", lay.text_y + 8 * fs,
                       th["ink_dim"])
            return
        for r in range(lay.list_rows):
            i = self.top + r
            if i >= len(self.rows):
                break
            row = self.rows[i]
            st = row["state"]
            if st == "get" and not row["runs"]:
                st = "noplay"
            elif st == "get" and row["fit"]:
                st = "too_big"
            rect = lay.row_rect(r)
            ref = row["cart"].get("cover")
            pic = self.thumbs.get(ref["sha256"]) if ref is not None else None
            if pic is not None:
                side, pix = pic
                h = min(side, rect[3])
                top = (side - h) // 2
                cv.blit565(memoryview(pix)[2 * side * top:2 * side * (top + h)],
                           side, h, rect[0], rect[1] + (rect[3] - h) // 2)
                shift = side + 4 * fs
                rect = (rect[0] + shift, rect[1], rect[2] - shift, rect[3])
            on = i == self.sel
            _ui.row(cv, th, rect, row["cart"]["name"], on=on,
                    value=STATE_LABEL[st], value_ink=self._state_ink(th, st),
                    pad=6 * fs, text_dy=5 * fs, fs=fs)
            ink = _ui.state_colors(th, "row", _ui.ON if on else _ui.REST)[1]
            info = "%s  %s" % (row["cart"]["licence"].get("spdx") or "",
                               _mb(row["plan"]["store_bytes"]))
            cv.print(info[:max(1, (rect[2] - 12 * fs) // (8 * fs))],
                     rect[0] + 6 * fs, rect[1] + 17 * fs, ink, 1)
        self._rows_bar(cv, th, len(self.rows))

    def _state_ink(self, th, st):
        if st == "get":
            return th["play"]
        if st == "update":
            return self.names["orange"]
        if st == "installed":
            return th["accent"]
        return th["ink_dim"]

    def _draw_cart(self, cv, th):
        lay = self.layout
        row = self.cur
        c = row["cart"]
        p = row["plan"]
        y = lay.text_y
        lic = c["licence"]
        y = self._line(cv, "Licence: %s" % (lic.get("spdx") or lic.get("name") or "?"),
                       y, th["ink"])
        y = self._line(cv, "Takes %s here, %s to fetch" % (_mb(p["store_bytes"]),
                                                          _mb(p["download_bytes"])),
                       y, th["ink"])
        for e in p["external"]:
            y = self._line(cv, "Needs %s from %s" % (e["path"], _ext_host(c, e)),
                           y, th["ink"])
        if p["slow"]:
            y = self._line(cv, "Plays slowly on this console.", y, self.names["orange"])
        st = row["state"]
        if st == "installed":
            y = self._line(cv, "On this console.", y, th["accent"])
        elif st == "update":
            y = self._line(cv, "An update is ready.", y, self.names["orange"])
        elif st == "taken":
            for ln in _ui.wrap_words("Another cart here is already called %s."
                                     % c["folder"], lay.cols):
                y = self._line(cv, ln, y, th["danger"])
        why = self.blocker(row) if st in ("get", "update") else None
        if why:
            for ln in _ui.wrap_words(why, lay.cols):
                y = self._line(cv, ln, y, th["danger"])
        if self.arm_remove:
            self._line(cv, "Tap REMOVE again. Its saves go too.", y, th["danger"])

    def _draw_licence(self, cv, th):
        lay = self.layout
        fs = lay.fs
        ext, lines, _off = self.lic
        self._line(cv, "Before getting %s:" % ext["path"], lay.text_y, th["ink"])
        self._line(cv, (ext["licence"].get("name") or "its licence")[:lay.cols],
                   lay.text_y + lay.line_h, th["accent"])
        area = lay.lic_area()
        cv.rect(area[0], area[1], area[2], area[3], th["surface"])
        sr = self._lic_region()
        off = sr.offset
        i = off // lay.line_h
        yy = area[1] + 2 * fs - off % lay.line_h
        clip = getattr(cv, "clip", None)
        if clip is not None:
            clip(*area)
        while yy < area[1] + area[3] and i < len(lines):
            cv.print(lines[i][:lay.cols], area[0] + 4 * fs, yy, th["ink"], 1)
            yy += lay.line_h
            i += 1
        if clip is not None:
            clip()
        self._lic_bar(cv, th)

    def _lic_bar(self, cv, th):
        """The licence box's scrollbar, drawn from the SAME region the drag
        moves -- the way `_rows_bar` draws the cart list's."""
        lay = self.layout
        if len(self.lic[1]) * lay.line_h > lay.lic_area()[3]:
            self._lic_region().draw_bar(cv, th)

    def _message(self):
        """(title, lines) for the screens that are only words and a button."""
        ph = self.phase
        name = self.cur["cart"]["name"] if self.cur is not None else ""
        if ph == "board":
            lines = ["This page shows the carts kept on the console that served it."]
            if self._inst.home() == "headless":
                lines.append("A console with no screen can't get carts.")
            else:
                lines.append("Get new carts on the console itself: turn WEB CONSOLE "
                             "off there and open Get Carts.")
            return "ON THE CONSOLE", lines
        if ph == "nonet":
            return "NO INTERNET", ["This console has no way to fetch carts."]
        if ph == "nowifi":
            return "WIFI IS OFF", ["Join a network in the WiFi app, then try again."]
        if ph == "unreached":
            return "SHELF AWAY", ["Couldn't reach the cart shelf. Try again soon."]
        if ph == "nomemory":
            return "MEMORY FULL", [_ci.NET_MEMORY]
        if ph == "checking":
            n = len(self.indexes)
            step = max(0, min(self._step - 1, n))
            return "CHECKING", ["Looking for carts..."] + (
                ["Shelf %d of %d" % (step + 1, n)] if n and self._step else [])
        if ph == "licence_fetch":
            return "ONE MOMENT", ["Getting the licence..."]
        if ph == "probing":
            return "ONE MOMENT", ["Looking for %s..." % self._external()["path"]]
        if ph in ("pick", "picked"):
            e = self._external()
            lines = [] if self.pick_why is None else [self.pick_why]
            lines.append("This console can't fetch %s from %s."
                         % (e["path"], _ext_host(self.cur["cart"], e)))
            lines.append("Choose your own copy of %s (%s) in the box below."
                         % (e["path"], _mb(e["size"])) if ph == "pick"
                         else "Checking your copy...")
            return "YOUR COPY", lines
        if ph == "connecting":
            return "CONNECTING", ["Turning on WiFi..."]
        if ph == "getting":
            if self.job is not None and self.job.keeping:
                return "GETTING", [name, "Putting it on the shelf..."]
            return "GETTING", [name]
        if ph == "done":
            return "READY", ["%s is on your shelf." % name]
        if ph == "failed":
            return "NOT INSTALLED", [self.why, "Nothing changed."]
        return "", []

    def _draw_message(self, cv, th):
        lay = self.layout
        fs = lay.fs
        title, lines = self._message()
        x, y = lay.body[0] + 4 * fs, lay.text_y + 4 * fs
        scale = 2 if len(title) * 16 * fs <= lay.body[2] - 8 * fs else 1
        ink = th["danger"] if self.phase in ("failed", "nowifi", "unreached",
                                             "nomemory", "nonet") else th["play"]
        if self.phase == "pick" and self.pick_why is not None:
            ink = th["danger"]
        cv.print(title, x, y, ink, scale)
        y += 8 * fs * scale + 8 * fs
        for text in lines:
            for ln in _ui.wrap_words(text, lay.cols):
                y = self._line(cv, ln, y, th["ink"])
        if self.phase == "getting" and self.job is not None:
            self._draw_progress(cv, th, y + 4 * fs)

    def _draw_progress(self, cv, th, y):
        lay = self.layout
        fs = lay.fs
        job = self.job
        x, w, h = lay.body[0] + 4 * fs, lay.body[2] - 8 * fs, 10 * fs
        cv.rectb(x, y, w, h, th["ink_dim"])
        fill = (w - 2) * job.done // job.total if job.total else 0
        if fill > 0:
            cv.rect(x + 1, y + 1, min(fill, w - 2), h - 2, th["play"])
        y += h + 6 * fs
        y = self._line(cv, "%s of %s" % (_mb(job.done), _mb(job.total)), y, th["ink"])
        rate = job.rate()
        if rate:
            self._line(cv, "%d KB/s" % (rate // 1024), y, th["ink_dim"])

    def _draw_buttons(self, cv, th):
        verbs = self._current_verbs()
        if not verbs:
            return
        fs = self.layout.fs
        rects = self.layout.buttons(len(verbs))
        for k, verb in enumerate(verbs):
            enabled = self.phase != "cart" or self._enabled(verb)
            kind = "normal"
            if verb in ("GET", "UPDATE", "PLAY", "I AGREE", "TRY AGAIN"):
                kind = "play"
            elif verb in ("REMOVE", "CANCEL"):
                kind = "danger"
            _ui.button(cv, th, rects[k], verb, kind=kind, on=k == self.focus,
                       disabled=not enabled,
                       state=self.hits.state_of("btn", verb))
            if enabled:
                self.hits.add(rects[k], "btn", verb)
        if self.phase == "cart" and self.arm_remove:
            _ui.focus_ring(cv, th, rects[len(verbs) - 1], fs)
