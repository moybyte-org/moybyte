# Map (grep -n a name to jump there):
#   PREFIX                 the add-on's folder names: web_console_<12 hex>.moy
#   installed              the add-on folders a carts root holds
#   AddonStep              the add-on that fits this image, and on a Zero the official carts, brought to it
#   official_behind        the official carts a store holds that its release has newer
"""The browser console add-on: a cart, one per page (tools/web_pack.py makes
them), whose pack the webhost serves (native/moy_net/moy_page.h); and on a
Zero the official carts, which its release publishes in the same index.

The firmware names the page it serves (moy_web.page_want), and its release
publishes, beside its manifest, a signed index listing every add-on it ever
published, each naming the page it fits (`moybyte.fits`). A console installs
the one that fits it through the ordinary install (cart_index.Install: every
file checked against the signed index before the folder moves into place),
from Get Carts like any cart, and that is the only way one arrives over the
air.

AFTER A FIRMWARE UPDATE the add-on that was installed fits the old image, so
the webhost answers "needs its page updated" until the one that fits the new
image is installed. `AddonStep` does that: started once the new image has
confirmed itself (so an image the bootloader rolls back never replaced the
add-on its predecessor serves), and on every boot whose add-on does not fit,
which is how an install a reset cut short resumes. It reads the console's own
index, installs the entry that fits, and takes the add-on folders that no
longer fit off the shelf. A step is one frame's share (`tick`); the radio is
the caller's lease (`inst.hold`/`release`).

WITH `carts` (the Zero, whose image carries only the core carts) the same
step brings the OFFICIAL carts to the release too: every official entry of
the index (cart_index.parse_index, the console's own index with its signature
verified) whose cart the store holds at an older manifest version -- the
seed's own rule (#47) -- is installed over it through the same Install, a cart at a time, its saves and
an edited config carried; an unchanged cart is not fetched at all. The
release it brought them to is the stamp `system/official` beside the carts
folder (the page digest the image names), so a cut one resumes and a store
already there asks nothing. A console board's image carries every seed cart
and seeds them itself, so its step is the add-on's alone.

`inst` is the install role's server as the shell drives it
(runtime/shell/shell_servers.py's InstallServer, bound), or the Zero's
stand-in: `net()`, `root()`, `op(fn)` (one store op), `chip()`, `hold()`,
`release()` and `rescan(folder)`. `index` is the console's own index url, or
a callable answering it.
"""

import json

import cart_index as _ci
from moy_fs import _exists, _read, _write_atomic, _mkdir
from moy_store_base import _sibling_path, cart_path as _cart_path

PREFIX = "web_console_"         # tools/web_pack.py's CART_ID, then 12 hex, then .moy
STATE_STALE = 2                 # moy_page.h's MOY_PAGE_STALE
STEP_MS = 8                     # an install's work in one frame
RETRY_MS = 60000                # a failed attempt starts again no sooner


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
    """The add-on that fits this image, installed over the one that does not,
    and with `carts` the official carts the store holds brought to the
    release (the module's docstring). `phase` is None while idle; `error` the
    last attempt's failure; `progress` (done, total) while it installs."""

    def __init__(self, inst, index, carts=False):
        self.inst = inst
        self.index = index
        self.carts = carts
        self._queue = []
        self._addon = False
        self.phase = None
        self.error = None
        self.wake = None              # called when a step starts: tick me each frame
        self._held = False
        self._job = None
        self._fetch = None
        self._gone = None
        self._ended = None
        self._pointed = False

    def point(self, again=False):
        """The page's state read from this console's store: once, or `again`
        when a cart may have arrived (the console's screen opening)."""
        if again or not self._pointed:
            self._pointed = True
            point(self.inst.root())

    def wanted(self):
        """An add-on is installed and none of them fits this image; or with
        `carts`, the official carts were last brought to another release."""
        self.point()
        return page_state() == STATE_STALE or self._behind()

    def _behind(self):
        if not self.carts:
            return False
        want = _ci.page_want()
        root = self.inst.root()
        return bool(want) and self.inst.op(lambda: official_stamp(root)) != want

    def busy(self):
        return self.phase is not None

    @property
    def progress(self):
        job = self._job
        return None if job is None else (job.done, job.total)

    def start(self, retry=False):
        """Begin when the add-on needs it: True while a step runs. `retry`
        waits out RETRY_MS after a failed attempt (a browser asking again);
        a boot's start and a person's button do not."""
        if self.phase is not None:
            return True
        if not self.wanted():
            return False
        if retry and self._ended is not None \
                and _ci._ticks_diff(_now(), self._ended) < RETRY_MS:
            return False
        url = self.index() if callable(self.index) else self.index
        if not url:
            self.error = "no update address"
            return False
        self._url = url
        self.error = None
        self.phase = "online"
        if not self._held:
            self._held = True
            self.inst.hold()
        self._addon = page_state() == STATE_STALE
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
        self._queue = []
        self.phase = None
        if self._held:
            self._held = False
            if release:
                self.inst.release()

    def tick(self, budget_ms=STEP_MS):
        """One frame's share: True while the step wants the next frame."""
        if self.phase is None:
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
        self.inst.rescan(None)
        self.point(again=True)          # the webhost reads the new pack's head
        return self._end(None)

    def _end(self, error):
        self.phase = None
        self.error = error
        if error is None:
            self._ended = None
            _ci._log("release: this firmware's browser console add-on and official "
                     "carts are installed")
        else:
            self._ended = _now()
            _ci._log("release: %s" % error)
        if self._job is not None:
            self._job = None
        if self._held:
            self._held = False
            self.inst.release()
        return False
