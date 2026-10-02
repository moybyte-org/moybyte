"""Get Carts -- the console's store (#124): carts other people publish,
browsed and installed over WiFi with no PC involved.

The screens, in the order a kid meets them:

  CHECKING   the radio comes up under the "carts" lease and every index is
             fetched -- moybyte-org's carts repositories unless `indexes.json`
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
  LICENCE    a file the cart needs that its repository does not host (Doom's
             WAD) shows its licence before it is fetched. NO has the focus;
             I AGREE is the only way on.
  GETTING    the download, a slice per frame (`cart_index.Install.step`), with
             its progress and CANCEL.
  READY / NOT INSTALLED  how it ended. A failed or cancelled install changed
             nothing on the shelf.

The installing is cart_index's; this module is the screens and the lease. The
radio is held while something is fetched -- the indexes, a licence and the
download it leads to -- and let go as soon as that ends: never while the kid
browses, never into a cart (PLAY lets go first), never past `close()`.

Blocking work (dialling the network, fetching an index) runs one frame after
its screen is shown, so CHECKING is on the glass before the wait starts -- the
update screen's arm (runtime/update_ui.py).
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
BUSY = ("checking", "licence_fetch", "connecting", "getting")


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
        self.lic = None               # [external, wrapped lines or None, top line]
        self._lic_text = ""
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
        self._held = False
        self._rows_scroll = None      # ListShellApp's touch model
        self._rows_taps = None

    # -- the app protocol (docs/app_api_v1.md) --------------------------------

    def open(self):
        self.arm_remove = False
        self.job = None
        self.lic = None
        if self._inst.net() is None:
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
        self._clamp_list(len(self.rows))

    def close(self):
        if self.job is not None and not self.job.finished:
            self.job.cancel()
            self._go("cart" if self.cur is not None else "list")
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
        self._found = []
        self._covers = []
        self._work = None
        self.unreached = 0
        self.starved = False
        self._step = 0
        self._go("checking")

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
                self._found.extend(_ci.parse_index(_ci.fetch(net, url, _ci.INDEX_LIMIT),
                                                   url))
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
            self._fetch_cover(net, self._covers.pop())
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
        sha = cart["cover"]["sha256"]
        self.thumbs[sha] = None
        try:
            data = _ci.fetch_cover(net, cart)
        except _ci.InstallError as exc:
            _ci._log(exc.detail)
            return
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

    def _build_rows(self):
        inst = self._inst
        root = inst.root()
        chip, fmt = inst.chip()
        have = inst.runtimes()
        carts = self.carts

        def _scan():
            rec = _ci.load_record(root) if root is not None else {}
            out = []
            for c in carts:
                p = _ci.plan(c, chip, fmt)
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
            rows = [{"cart": c, "plan": _ci.plan(c, chip, fmt), "state": "get",
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
        elif ph in BUSY:
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
        self.arm_remove = False
        self._next_licence()

    def _next_licence(self):
        for e in self.cur["plan"]["external"]:
            if e["path"] not in self.accepted:
                self.lic = [e, None, 0]
                self._go("licence_fetch")
                return
        self.lic = None
        self._go("connecting")

    def _pump_licence(self):
        if not self._online():
            self._fail(_ci.NET_MEMORY if self._starved() else "WiFi isn't connected.")
            return
        ext = self.lic[0]
        self._lic_text = _ci.licence_text(self._inst.net(), self.cur["cart"],
                                          ext["licence"])
        self.lic[1] = self._wrap(self._lic_text)
        self.lic[2] = 0
        self._go("licence", focus=1)       # NO has the focus

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
                               archive=keep)
        self._go("getting")
        self.armed = True

    def _pump_get(self):
        job = self.job
        if job.step():
            return
        self._release()
        if job.path:
            self._inst.rescan()
            self._build_rows()
            self._go("done")
        elif job.error:
            self._fail(job.error)
        else:
            self._go("cart")

    def _cancel(self):
        """B, or a tap on CANCEL -- never A, which a kid presses at anything."""
        checking = self.phase == "checking"
        if self.job is not None and not self.job.finished:
            self.job.cancel()
        self._release()
        self.status = "STOPPED" if checking else "STOPPED. NOTHING CHANGED."
        self._go("cart" if self.cur is not None and not checking else "list")

    def _fail(self, why):
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
        try:
            self._inst.session(lambda: _ci.remove(root, row["cart"]["folder"]))
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
        elif ph in BUSY:
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
        if ph in BUSY:
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
        elif i.pressed("a") and verbs and ph not in BUSY:
            verb = verbs[min(self.focus, len(verbs) - 1)]
            if ph != "cart" or self._enabled(verb):
                self._press(verb)
        elif i.pressed("b"):
            self._back()
        return True

    def _scroll_licence(self, d):
        lines = self.lic[1]
        page = self.layout.text_rows - 2
        top = self.lic[2] + d * (1 if abs(d) == 1 else page)
        self.lic[2] = max(0, min(top, max(0, len(lines) - page)))
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
        elif verb == "page":
            self._scroll_licence(arg)
        elif verb == "btn" and (self.phase != "cart" or self._enabled(arg)):
            self._press(arg)
        return True

    # -- draw ------------------------------------------------------------------

    def draw(self, dt):
        self._pump()
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
            if ph not in BUSY and ph not in ("nonet", "nowifi", "unreached",
                                             "nomemory"):
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
            y = self._line(cv, "Needs %s from %s" % (e["path"],
                                                     _host_of(e["archive"]["urls"][0])),
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
        ext, lines, top = self.lic
        y = self._line(cv, "Before getting %s:" % ext["path"], lay.text_y, th["ink"])
        y = self._line(cv, (ext["licence"].get("name") or "its licence")[:lay.cols],
                       y, th["accent"])
        page = lay.text_rows - 2
        x0, w0 = lay.body[0], lay.body[2]
        area = (x0, y, w0, page * lay.line_h)
        cv.rect(area[0], area[1], area[2], area[3], th["surface"])
        yy = y + 2 * fs
        for ln in lines[top:top + page]:
            cv.print(ln[:lay.cols], x0 + 4 * fs, yy, th["ink"], 1)
            yy += lay.line_h
        half = area[3] // 2
        self.hits.add((area[0], area[1], area[2], half), "page", -page)
        self.hits.add((area[0], area[1] + half, area[2], area[3] - half), "page", page)
        _ui.scroll_cues(cv, (x0 + w0 - 10 * fs, area[1] + 2 * fs),
                        (x0 + w0 - 10 * fs, area[1] + area[3] - 10 * fs),
                        top > 0, top + page < len(lines), th["accent"], fs)

    def _message(self):
        """(title, lines) for the screens that are only words and a button."""
        ph = self.phase
        name = self.cur["cart"]["name"] if self.cur is not None else ""
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
        if ph == "connecting":
            return "CONNECTING", ["Turning on WiFi..."]
        if ph == "getting":
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
