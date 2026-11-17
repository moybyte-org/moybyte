# Map (grep -n a name to jump there):
#   PREFIX                 the add-on's folder names: web_console_<12 hex>.moy
#   installed              the add-on folders a carts root holds
#   AddonStep              the add-on that fits this image, installed over the one that does not
"""The browser console add-on: a cart, one per page (tools/web_pack.py makes
them), whose pack the webhost serves (native/moy_net/moy_page.h).

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

`inst` is the install role's server as the shell drives it
(runtime/shell/shell_servers.py's InstallServer, bound), or the Zero's
stand-in: `net()`, `root()`, `op(fn)` (one store op), `chip()`, `hold()`,
`release()` and `rescan(folder)`. `index` is the console's own index url, or
a callable answering it.
"""

import cart_index as _ci

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


class AddonStep:
    """The add-on that fits this image, installed over the one that does not
    (the module's docstring). `phase` is None while idle; `error` the last
    attempt's failure; `progress` (done, total) while it installs."""

    def __init__(self, inst, index):
        self.inst = inst
        self.index = index
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
        """An add-on is installed and none of them fits this image."""
        self.point()
        return page_state() == STATE_STALE

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
        _ci._log("browser console: the add-on fits another firmware; updating it")
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
        fits = [c for c in _ci.fitting(_ci.parse_index(data, self._url))
                if c.get("runtime") == "web"]
        if not fits:
            return self._end("no browser console for this firmware")
        cart = fits[0]
        chip, fmt = self.inst.chip()
        ranges = bool(getattr(self.inst.net(), "ranges", False))
        self._keep = cart["folder"]
        self._job = _ci.Install(cart, _ci.plan(cart, chip, fmt, ranges), self.inst.net(),
                                self.inst.root(), self.inst.op)
        self.phase = "install"
        return True

    def _install(self, budget):
        job = self._job
        if job.step(budget):
            return True
        self._job = None
        if not job.path:
            return self._end(job.error or "stopped")
        root = self.inst.root()
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
            _ci._log("browser console: the add-on that fits this firmware is installed")
        else:
            self._ended = _now()
            _ci._log("browser console: %s" % error)
        if self._job is not None:
            self._job = None
        if self._held:
            self._held = False
            self.inst.release()
        return False
