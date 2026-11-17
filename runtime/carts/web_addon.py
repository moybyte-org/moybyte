# Map (grep -n a name to jump there):
#   installed              the add-on folders (web_console_<12 hex>.moy) a carts root holds
#   AddonStep              the add-on that fits this image, and on the XIAO Zero the official carts, brought to it
#   official_behind        the official carts a store holds that its release has newer
#   OfficialCarts          /official's backend: the official carts a page asks a headless board for
"""The browser console add-on: a cart, one per page (tools/web_pack.py makes
them), whose pack the webhost serves (native/moy_net/moy_page.h); and on a
Zero the official carts, which its release publishes in the same index.

The firmware names the page it serves (moy_web.page_want), and its release
publishes, beside its manifest, a signed index listing every add-on it ever
published, each naming the page it fits (`moybyte.fits`). Every board gets
the one that fits it through the ordinary install (cart_index.Install: every
file checked against the signed index before the folder moves into place):
from Get Carts like any cart, or by this module's step (`AddonStep`). The
website's install writes it into the store with the image
(tools/store_image.py).

AFTER A FIRMWARE UPDATE the add-on that was installed fits the old image, so
the webhost answers "needs its page updated" until the one that fits the new
image is installed. `AddonStep` does that: started once the new image has
confirmed itself (so an image the bootloader rolls back never replaced the
add-on its predecessor serves), and on every boot whose add-on does not fit,
which is how an install a reset cut short resumes. It reads the console's own
index, installs the entry that fits, and takes the add-on folders that no
longer fit off the shelf. A step is one frame's share (`tick`); the radio is
the caller's lease (`inst.hold`/`release`).

WHERE THE ONE THAT FITS IS THERE ALREADY (the website's install, a cable
push, moy_seed.bring_addon's move to a card) the others are taken off at the
confirm all the same (`tidy`, once a boot, before the step's start): the
folders as the shelf lists them, or as the carts folder does where the board
keeps no shelf (a Zero), so a console's warm boot lists nothing more. That is
housekeeping, not an owner's removal: it writes no line to `system/removed`.

WITH `absent` (every board) a store with NO add-on gets the one that fits
too, unless its owner removed the console: a removal records it
(moy_seed.ADDON_LINE in `system/removed`, which Get Carts' install of it
drops again), and a board with that line never fetches it. A board the
website's flasher wrote carries the add-on in its store already; the step
brings it to a wiped store, a cable flash whose add-on step failed, and a
console that had none before. On a console it waits for a network: it does
nothing at all where none is saved (`inst.configured()`), never runs while a
game plays (`inst.playing()`: a start is put off and a running step stops,
the shelf as it was), and a failed attempt is tried again after RETRY_MS,
doubling to RETRY_MAX_MS (`due`, which the console's frame polls; a Zero's
serving loop asks again itself). A boot's confirm starts it, a WiFi join
(`nudge`) and the update screen's exit start it again.

WITH `carts` (the XIAO Zero, which updates over the air) the same step
brings the OFFICIAL carts the store holds to the release: every official
entry of the index (cart_index.parse_index, the console's own index with its
signature verified) whose cart the store holds at an older manifest version
-- the seed's own rule (#47) -- is installed over it through the same
Install, a cart at a time, its saves and an edited config carried; an
unchanged cart is not fetched at all. The carts its image carries are
current already (the new image's seed, zero_host.seed_carts, runs before
the step), so what it fetches is the official carts the page installed. The
release it brought them to is the stamp `system/official` beside the carts
folder (the page digest the image names), so a cut one resumes and a store
already there asks nothing. A console board's image carries every seed cart
and seeds them itself, so its step is the add-on's alone.

A ZERO'S PAGE asks for the rest (`OfficialCarts`, the webhost's sealed
`/official`): the browser console it serves lists the official carts of the
board's own release and asks the board to install or remove one, and the
board fetches from its release over its own network and writes its own
store -- the board stays the one writer of its carts, as with `/update`.

`inst` is the install role's server as the shell drives it
(runtime/shell/shell_servers.py's InstallServer, bound), or the Zero's
stand-in: `net()`, `root()`, `op(fn)` (one store op), `chip()`, `hold()`,
`release()` and `rescan(folder)`. `index` is the console's own index url, or
a callable answering it.
"""

import json

import cart_index as _ci
from moy_fs import _exists, _read, _write_atomic, _mkdir
from moy_seed import ADDON_LINE as _ADDON_LINE, addon_removed as _addon_removed, \
    unremove as _unremove
from moy_store_base import _sibling_path, cart_path as _cart_path

PREFIX = "web_console_"         # tools/web_pack.py's CART_ID, then 12 hex, then .moy
STATE_ABSENT = 1                # moy_page.h's MOY_PAGE_ABSENT
STATE_STALE = 2                 # moy_page.h's MOY_PAGE_STALE
STATE_READY = 3                 # moy_page.h's MOY_PAGE_READY
STEP_MS = 8                     # an install's work in one frame
RETRY_MS = 60000                # a failed attempt starts again no sooner
RETRY_MAX_MS = 3600000          # and each failure doubles the wait, up to this


def installed(root, op):
    """The add-on folders under `root`, whichever page each fits."""
    def _list():
        import os
        try:
            return sorted(n for n in os.listdir(root)
                          if n.startswith(PREFIX) and n.endswith(".moy"))
        except OSError:
            return []
    return op(_list)


def page_state():
    """moy_page.h's state of this console's page, or None where there is no
    webhost (the host's console, a browser)."""
    try:
        import moy_net
    except ImportError:
        return None
    state = getattr(moy_net, "page_state", None)
    return None if state is None else state()


def point(root):
    """Name `root` to the webhost's page (moy_page_at): its state is read
    from the add-on folders there, once, until the next point."""
    try:
        import moy_net
    except ImportError:
        return
    at = getattr(moy_net, "page_at", None)
    if at is not None:
        at(root)


def _now():
    return _ci._ticks_ms()


OFFICIAL_STAMP = "system/official"   # beside the carts folder: the page the official carts are at


def official_stamp(root):
    try:
        return _read(_sibling_path(root, OFFICIAL_STAMP)).strip() or None
    except (OSError, ValueError):
        return None


def save_official_stamp(root, want):
    _mkdir(_sibling_path(root, "system"))
    _write_atomic(_sibling_path(root, OFFICIAL_STAMP), want + "\n")


def official_behind(root, carts):
    """The official entries of `carts` whose cart the store `root` holds at
    an older manifest version (cart_index.cart_state's "update"). Call in a
    store op."""
    out = []
    for c in carts:
        if not _ci.is_official(c):
            continue
        folder = _cart_path(root, c["folder"])
        if not _exists(folder):
            continue
        try:
            man = json.loads(_read(folder + "/manifest.json"))
        except (OSError, ValueError):
            man = None
        if _ci.cart_state(c, None, None, True, man) == "update":
            out.append(c)
    return out


class AddonStep:
    """The add-on that fits this image, installed over the one that does not
    (and with `absent` where there is none and the console was not removed),
    and with `carts` the official carts the store holds brought to the
    release (the module's docstring). `phase` is None while idle; `error` the
    last attempt's failure; `progress` (done, total) while it installs; `due`
    the ticks_ms an attempt was put off at, else None (`poll` starts it once
    its wait is over)."""

    def __init__(self, inst, index, carts=False, absent=False):
        self.inst = inst
        self.index = index
        self.carts = carts
        self.absent = absent
        self._queue = []
        self._addon = False
        self._stamp = False           # the official stamp, once read (False: unread)
        self._fitless = False         # the release lists no add-on for this image
        self.phase = None
        self.error = None
        self.wake = None              # called when a step starts: tick me each frame
        self._held = False
        self._job = None
        self._fetch = None
        self._gone = None
        self._ended = None
        self._wait = RETRY_MS
        self._pointed = False
        self._removed = None          # the record's console line, once read
        self._removed_at = None       # ...under this page state
        self._tidied = False          # this boot's tidy has run
        self._swept = None            # the folders a tidy takes, rescanned after
        self.due = None
        self._due_ms = 0

    def point(self, again=False):
        """The page's state read from this console's store: once, or `again`
        when a cart may have arrived or gone (the console's screen opening),
        the removal record with it."""
        if again or not self._pointed:
            self._pointed = True
            self._removed = None
            point(self.inst.root())

    def removed(self):
        """Whether the console's owner removed the browser console
        (moy_seed.addon_removed): read again when the page's state has
        changed since (a removal takes the add-on away) or at a point."""
        state = page_state()
        if self._removed is None or self._removed_at != state:
            root = self.inst.root()
            self._removed = bool(self.inst.op(lambda: _addon_removed(root)))
            self._removed_at = state
        return self._removed

    def restore(self):
        """The console asked for again where it was removed (a Zero's page,
        its only face): the record's line dropped and the step started."""
        root = self.inst.root()
        self.inst.op(lambda: _unremove(root, [_ADDON_LINE]))
        self._removed = None
        return self.nudge()

    def _addon_wanted(self):
        state = page_state()
        if state == STATE_STALE:
            return True
        return self.absent and state == STATE_ABSENT and not self.removed()

    def wanted(self):
        """An add-on is installed and none of them fits this image, or with
        `absent` none is installed and the console was not removed; or with
        `carts`, the official carts were last brought to another release."""
        self.point()
        return self._addon_wanted() or self._behind()

    def tidy(self):
        """The image has confirmed itself (call it no sooner): once a boot,
        where the add-on that fits it is installed (the page READY), every
        other add-on folder taken off the shelf by the step's own clean phase,
        no removal recorded. True while that runs (`tick` it)."""
        if self._tidied or self.phase is not None:
            return self.phase is not None
        self._tidied = True
        want = _ci.page_want()
        self.point()
        if not want or page_state() != STATE_READY:
            return False
        keep = PREFIX + want[:12] + ".moy"
        root = self.inst.root()
        stale = [f for f in self._addons(root) if f != keep]
        if not stale:
            return False
        _ci._log("release: taking %d browser console add-on(s) this firmware "
                 "does not serve off the shelf" % len(stale))
        self._swept = stale
        self._gone = self._removals(root, stale)
        self.error = None
        self.phase = "clean"
        if self.wake is not None:
            self.wake()
        return True

    def _addons(self, root):
        """The add-on folders under `root`: from the shelf the boot scanned
        where the server keeps one (a console's `carts()`), else listed."""
        shelf = getattr(self.inst, "carts", None)
        if shelf is None:
            return installed(root, self.inst.op)
        out = []
        for c in shelf() or ():
            path = c.get("path") if type(c) is dict else None
            name = path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""
            if name.startswith(PREFIX) and name.endswith(".moy"):
                out.append(name)
        return out

    def _ask(self, name):
        fn = getattr(self.inst, name, None)
        return None if fn is None else fn()

    def _put_off(self, ms):
        self.due = _now()
        self._due_ms = ms

    def poll(self, now=None):
        """The console's frame, while `due` is set: the put-off attempt
        started once it is due. True while a step runs."""
        if self.due is None or self.phase is not None:
            return self.phase is not None
        if _ci._ticks_diff(_now() if now is None else now, self.due) < self._due_ms:
            return False
        self.due = None
        return self.start(retry=True)

    def nudge(self):
        """A network was joined: an attempt now, the back-off forgotten."""
        self._ended = None
        self._wait = RETRY_MS
        self._fitless = False
        self.due = None
        return self.start()

    def _behind(self):
        if not self.carts:
            return False
        want = _ci.page_want()
        if not want:
            return False
        if self._stamp is False:
            root = self.inst.root()
            self._stamp = self.inst.op(lambda: official_stamp(root))
        return self._stamp != want

    def busy(self):
        return self.phase is not None

    @property
    def progress(self):
        job = self._job
        return None if job is None else (job.done, job.total)

    def start(self, retry=False):
        """Begin when the add-on needs it: True while a step runs. `retry`
        waits out the back-off after a failed attempt (a browser asking
        again, the console's put-off attempt); a boot's start and a person's
        button do not. Nothing starts where no network is saved, and a game
        playing puts it off."""
        if self.phase is not None:
            return True
        if retry and (self._fitless or self._ended is not None
                      and _ci._ticks_diff(_now(), self._ended) < self._wait):
            return False
        if self._ask("configured") is False or not self.wanted():
            return False
        if self._ask("playing"):
            self._put_off(RETRY_MS)
            return False
        url = self.index() if callable(self.index) else self.index
        if not url:
            self.error = "no update address"
            self._ended = _now()
            return False
        self._url = url
        self.error = None
        self.phase = "online"
        if not self._held:
            self._held = True
            self.inst.hold()
        self._addon = self._addon_wanted()
        _ci._log("release: bringing %s to this firmware"
                 % ("the browser console add-on" if self._addon else "the official carts"))
        if self.wake is not None:
            self.wake()
        return True

    def stop(self, release=True):
        """Drop the step, the shelf as it was. `release` False leaves the
        radio's hold to whoever takes the same lease now."""
        job, self._job = self._job, None
        if job is not None:
            if not job.cancel():
                job.finish()            # past the point of no return: it lands
            while not job.finished and job.step():
                pass
        if self._fetch is not None:
            self._fetch.close()
            self._fetch = None
        self._gone = None
        if self._swept is not None:
            self._swept = None
            self._tidied = False        # the next tidy takes the rest
        self._queue = []
        self.phase = None
        if self._held:
            self._held = False
            if release:
                self.inst.release()

    def tick(self, budget_ms=STEP_MS):
        """One frame's share: True while the step wants the next frame. A
        game that starts stops it, to start again after it."""
        if self.phase is None:
            return False
        if self._ask("playing"):
            self.stop()
            self._put_off(RETRY_MS)
            return False
        try:
            return getattr(self, "_" + self.phase)(budget_ms)
        except Exception as exc:  # noqa: BLE001 -- the step never takes a frame down
            return self._end(getattr(exc, "text", None) or repr(exc))

    def _online(self, _budget):
        net = self.inst.net()
        if net is None:
            return self._end("no network")
        ready = getattr(net, "ready", None)
        up = ready() if ready is not None else net.online()
        if up is None:
            return True
        if not up:
            return self._end("WiFi isn't connected.")
        self._fetch = _ci.index_fetch(net, self._url)
        self.phase = "index"
        return True

    def _index(self, _budget):
        if self._fetch.step():
            return True
        data, self._fetch = self._fetch.data, None
        carts = _ci.fitting(_ci.parse_index(data, self._url, self._url))
        self._queue = []
        if self._addon:
            fits = [c for c in carts if c.get("runtime") == "web"]
            if not fits:
                # The release does not change under its image: asking again
                # until a person or a boot does is a fetch of the index a
                # minute for nothing.
                self._fitless = True
                return self._end("no browser console for this firmware")
            self._keep = fits[0]["folder"]
            self._queue.append(fits[0])
        if self.carts:
            root = self.inst.root()
            self._queue += self.inst.op(lambda: official_behind(root, carts))
        self.phase = "install"
        return self._next_job()

    def _next_job(self):
        """The next cart of the queue's install started: False when none is
        left, the step then ending."""
        if not self._queue:
            return self._done()
        cart = self._queue.pop(0)
        chip, fmt = self.inst.chip()
        ranges = bool(getattr(self.inst.net(), "ranges", False))
        self._job = _ci.Install(cart, _ci.plan(cart, chip, fmt, ranges), self.inst.net(),
                                self.inst.root(), self.inst.op)
        return True

    def _install(self, budget):
        job = self._job
        if job.step(budget):
            return True
        self._job = None
        if not job.path:
            return self._end(job.error or "stopped")
        if job.cart.get("runtime") != "web":
            self.inst.rescan(job.path)
        return self._next_job()

    def _done(self):
        """Every install landed: the official stamp, then the add-ons that no
        longer fit taken away."""
        root = self.inst.root()
        if self.carts:
            want = _ci.page_want()
            if want:
                self.inst.op(lambda: save_official_stamp(root, want))
                self._stamp = want
        if not self._addon:
            return self._end(None)
        old = [f for f in installed(root, self.inst.op) if f != self._keep]
        self._gone = self._removals(root, old)
        self.phase = "clean"
        return True

    def _removals(self, root, folders):
        for f in folders:
            yield from _ci.stepped(self.inst.op, _ci.remove_steps(root, f))

    def _clean(self, _budget):
        if next(self._gone, _ci._DONE) is not _ci._DONE:
            return True
        self._gone = None
        swept, self._swept = self._swept, None
        if swept is None:
            self.inst.rescan(None)
            self.point(again=True)      # the webhost reads the new pack's head
            return self._end(None)
        for f in swept:
            self.inst.rescan(f)
        self.phase = None
        return False

    def _end(self, error):
        self.phase = None
        self.error = error
        if error is None:
            self._ended = None
            self._wait = RETRY_MS
            _ci._log("release: this firmware's browser console add-on and official "
                     "carts are installed")
        else:
            if self._ended is not None:
                self._wait = min(self._wait * 2, RETRY_MAX_MS)
            self._ended = _now()
            if not self._fitless:
                self._put_off(self._wait)
            _ci._log("release: %s" % error)
        if self._job is not None:
            self._job = None
        if self._held:
            self._held = False
            self.inst.release()
        return False


def listing(c):
    """Official entry `c` as a page lists it: what its row and page show and
    what its install takes in the store, without the urls and digests only
    the board's install reads."""
    out = {}
    for key in ("id", "name", "version", "folder", "runtime", "description", "genre",
                "tags", "memory", "depends", "index", "shelf"):
        if key in c:
            out[key] = c[key]
    lic = c.get("licence") or {}
    out["licence"] = dict((k, lic[k]) for k in ("spdx", "name") if k in lic)
    out["core"] = bool((c.get("moybyte") or {}).get("core"))
    out["assets"] = [{"size": a["size"],
                      "files": dict((fn, {"size": m["size"]}) for fn, m in a["files"].items())}
                     for a in c["assets"]]
    cover = c.get("cover")
    if cover is not None:
        out["cover"] = {"size": cover["size"], "sha256": cover["sha256"]}
    return out


class OfficialCarts:
    """`/official`'s backend on a board with no screen (the Zero): the
    official carts of its own release index (`index`, a url or a callable
    answering one), listed for the page it serves and installed or removed
    when that page asks. The page asks and the board does the work, a slice
    per poll (`step`), as zero_host.ZeroUpdate updates its firmware: a POST
    queues and answers at once (`request`), a GET reads where it stands
    (`answer`).

      check    the index fetched and its official entries that fit this
               image listed (`listing`), with the store's room: "listed",
               handed to the next GET once, then "idle"
      install  the index fetched again and the entry `folder` names installed
               through cart_index.Install (its saves and an edited config
               carried), its removal record dropped: "done"
      remove   an optional built-in's removal recorded (moy_seed.note_removed,
               as pkg.removed records it) and its folder taken off: "done";
               a core cart is refused
      cancel   an install stopped while it can be

    Everything a request makes -- the index, the list, the install's buffers
    -- is made for it and let go when it ends, so an idle board holds none of
    it. `busy()` (the release's own AddonStep) holds a request back: two
    installs never write one store. `reserve` is the room the store keeps
    free (zero_host.SEED_RESERVE)."""

    def __init__(self, inst, index, busy=None, reserve=0):
        self.inst = inst
        self.index = index
        self.other = busy
        self.reserve = reserve
        self.state = "idle"
        self.action = None
        self.folder = None
        self.error = None
        self._listed = None
        self._phase = None
        self._url = None
        self._fetch = None
        self._job = None
        self._gone = None
        self._held = False
        self._cancelled = False

    def busy(self):
        return self.state in ("checking", "installing", "removing")

    # -- the request side -----------------------------------------------------

    def request(self, action, folder=None):
        """Queue `check`, `install`, `remove` or `cancel`: (ok, message)."""
        if action == "cancel":
            return self._cancel()
        if action not in ("check", "install", "remove"):
            return False, "action must be check, install, remove or cancel"
        if self.busy():
            return False, "busy: " + self.state
        if self.other is not None and self.other():
            return False, "busy: the release's own install"
        if action != "check" and not (isinstance(folder, str) and _ci.plain_name(folder)
                                      and folder.endswith(".moy")):
            return False, "no cart folder named"
        self._listed = None
        self.error = None
        self.action = action
        self.folder = folder
        self._cancelled = False
        if action == "remove":
            import moy_carts
            if not moy_carts.removable(folder):
                return False, "%s stays on this console" % folder
            root = self.inst.root()
            if not self.inst.op(lambda: _exists(_cart_path(root, folder))):
                return False, "%s isn't on this console" % folder
            self.state = "removing"
            self._gone = self._removal(root, folder)
            return True, "queued"
        url = self.index() if callable(self.index) else self.index
        if not url:
            return False, "no update address"
        self._url = url
        self.state = "checking" if action == "check" else "installing"
        self._phase = "online"
        if not self._held:
            self._held = True
            self.inst.hold()
        return True, "queued"

    def _cancel(self):
        job = self._job
        if self.state != "installing":
            return False, "nothing to cancel"
        if job is not None and not job.cancel():
            return False, "it is landing"
        self._cancelled = True
        if job is None:
            self._end(None)             # nothing written yet: stopped now
        return True, "cancelled"

    # -- what a GET reads ------------------------------------------------------

    def answer(self, ok=None, message=None):
        """The /official document as JSON text: the state, the request it
        is about, the install's progress, the error; a check's list and the
        store's room the once a GET reads them."""
        doc = {"state": self.state, "action": self.action, "folder": self.folder,
               "error": self.error, "progress": None}
        job = self._job
        if job is not None:
            doc["progress"] = [job.done, job.total]
        if ok is not None:
            doc["ok"] = ok
            doc["message"] = message
        text = json.dumps(doc)
        listed = self._listed
        if listed is not None and ok is None:
            self._listed = None
            self.state = "idle"
            text = text[:-1] + ", " + listed[1:]
        return text

    # -- the pumped side -------------------------------------------------------

    def step(self, budget_ms=STEP_MS):
        """One slice of the request in hand: True while it wants the next."""
        try:
            if self.state == "removing":
                if next(self._gone, _ci._DONE) is not _ci._DONE:
                    return True
                self._gone = None
                return self._end(None)
            if self._phase is None:
                return False
            return getattr(self, "_" + self._phase)(budget_ms)
        except MemoryError:
            return self._end(_ci.NO_MEMORY)
        except Exception as exc:  # noqa: BLE001 -- a request never takes the host down
            return self._end(getattr(exc, "text", None) or repr(exc))

    def _online(self, _budget):
        net = self.inst.net()
        if net is None:
            return self._end("no network")
        ready = getattr(net, "ready", None)
        up = ready() if ready is not None else net.online()
        if up is None:
            return True
        if not up:
            return self._end("WiFi isn't connected.")
        self._fetch = _ci.index_fetch(net, self._url)
        self._phase = "index"
        return True

    def _index(self, _budget):
        if self._fetch.step():
            return True
        data, self._fetch = self._fetch.data, None
        carts = [c for c in _ci.fitting(_ci.parse_index(data, self._url, self._url))
                 if _ci.is_official(c)]
        data = None
        root = self.inst.root()
        room = self.inst.op(lambda: _room(root, self.reserve))
        if self.action == "check":
            listed = json.dumps({"carts": [listing(c) for c in carts], "free": room})
            carts = None
            self._release()
            self._phase = None
            self._listed = listed
            self.state = "listed"
            _ci._log("official: %d bytes listed" % len(listed))
            return False
        cart = None
        for c in carts:
            if c["folder"] == self.folder:
                cart = c
        carts = None
        if cart is None:
            return self._end("It isn't in this console's release.")
        chip, fmt = self.inst.chip()
        ranges = bool(getattr(self.inst.net(), "ranges", False))
        p = _ci.plan(cart, chip, fmt, ranges)
        if room is not None and _ci.need_bytes(p, room[1]) > room[0]:
            return self._end(_ci.FULL)
        self._job = _ci.Install(cart, p, self.inst.net(), root, self.inst.op)
        self._phase = "install"
        return True

    def _install(self, budget):
        job = self._job
        if job.step(budget):
            return True
        if not job.path:
            return self._end(None if self._cancelled else (job.error or _ci.STOPPED))
        import moy_carts
        root = self.inst.root()
        folder = self.folder
        self.inst.op(lambda: moy_carts.unremove(root, [folder]))
        self.inst.rescan(job.path)
        return self._end(None)

    def _removal(self, root, folder):
        import moy_carts
        self.inst.op(lambda: moy_carts.note_removed(root, folder))
        yield
        yield from _ci.stepped(self.inst.op, _ci.remove_steps(root, folder))
        self.inst.rescan(folder)

    def _release(self):
        if self._held:
            self._held = False
            self.inst.release()

    def _end(self, error):
        self._phase = None
        self._fetch = None
        self._job = None
        self._release()
        self.error = error
        if self._cancelled:
            self.state = "idle"
        else:
            self.state = "done" if error is None else "error"
        _ci._log("official: %s %s %s" % (self.action, self.folder or "",
                                         error or self.state))
        return False


def _room(root, reserve):
    """(bytes an install may take, block size) of the store `root` with
    `reserve` kept free, or None where it cannot say."""
    import os
    try:
        st = os.statvfs(root)
    except (OSError, AttributeError):
        return None
    return max(0, st[0] * st[3] - reserve), st[0]
