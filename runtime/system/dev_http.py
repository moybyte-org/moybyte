"""`moybyte.http`: the `http` device API's face (docs/os_2026-11/hardware.md
section 1) over its driver (native/moy_net/moy_fetch.h, the module moy_http).
Needs the `http` permission.

  get(url, cap=16384, on_done=None) -> Request
                     a GET served off the VM's thread, its body kept up to
                     `cap` bytes (at most CAP_MAX); `on_done(req)` is woken
                     when it finishes. `https` verifies the server's
                     certificate (a failure: OSError EKEYREJECTED); a
                     console that cannot verify one (`tls()` False: the
                     host) refuses it, a redirect to it too (OSError
                     EPROTONOSUPPORT)
  tls() -> bool      whether this console's https verifies
  Request.state()    None while it runs, else (status, length); OSError
                     with its errno when it got no response
  Request.body()     the body as bytes (None while it runs)
  Request.readinto(buf, off=0) -> n
  Request.close()

The radio is a lease (`Workstation.wifi_hold`): a request holds the console's
"http" tag from its `get` until it finishes, is closed, or its process ends,
and the last one out lets it go. A `get` with the station down asks for the
last-joined network (the console's cart_net `dial`) and returns at once; the
request waits for the link on the worker's task (moy_net_dialing), so
neither holds a frame.

The host serves a request as it is queued (the driver's `run`), over the
real client, which speaks plain TCP there."""

try:
    import moy_http as _N
except ImportError:  # host: the runtime package
    from runtime import moy_http as _N

try:
    import devices as _D
except ImportError:  # host: the runtime package
    from runtime import devices as _D

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package
    from runtime import moybyte as _moybyte

CAP_MAX = _N.CAP_MAX
TAG = "http"
_HOST = hasattr(_N, "PART")
# The host runs a request at its get; a test turns this off to see it queued.
auto_run = _HOST

_open = {}          # handle -> Request, every request not yet closed
_held = [False]     # the radio lease is taken


class Request:
    def __init__(self, h, owner):
        self.h = h
        self.owner = owner
        self.wake = None

    def _st(self):
        st = _N.state(self.h)
        if isinstance(st, int):
            raise _D.error(-st)
        return st

    def state(self):
        if auto_run and _HOST:
            _N.run()
        st, status, n, err = self._st()
        if st == _N.FAILED:
            _settle()
            raise _D.error(err)
        if st != _N.DONE:
            return None
        _settle()
        return status, n

    def body(self):
        if self.state() is None:
            return None
        n = self._st()[2]
        buf = bytearray(n)
        if n:
            _D.check(_N.read(self.h, 0, buf))
        return bytes(buf)

    def readinto(self, buf, off=0):
        return _D.check(_N.read(self.h, off, buf))

    def close(self):
        _end(self)


def _end(req):
    if _open.pop(req.h, None) is None:
        return
    if req.wake is not None:
        _moybyte.WAKES.cancel(req.wake)
        req.wake = None
    _N.close(req.h)
    _settle()


def _busy():
    for req in _open.values():
        st = _N.state(req.h)
        if not isinstance(st, int) and st[0] in (_N.QUEUED, _N.RUNNING):
            return True
    return False


def _settle():
    """The radio lease let go once no request is in flight."""
    if _held[0] and not _busy():
        _held[0] = False
        ws = _D.console
        if ws is not None:
            ws.wifi_release(TAG)


def _hold():
    ws = _D.console
    if ws is None or _held[0]:
        return
    _held[0] = True
    ws.wifi_hold(TAG)
    dial = getattr(getattr(ws, "cart_net", None), "dial", None)
    if dial is not None:
        dial()                     # never waited: the worker's task waits for the link


def _wake_for(req, on_done):
    src = _N.source()
    if not src or _moybyte.WAKES.table is None:
        return
    buf = _moybyte_buf()
    F, K, A = _moybyte.EV_FIELDS, _moybyte.EV_KIND, _moybyte.EV_A
    me = req.h & 0xFFFF

    def wake():
        n = _moybyte.events(buf)
        while n:
            for i in range(n):
                k = i * F
                if buf[k + K] == _moybyte.NET and buf[k + A] == me and req.h in _open:
                    on_done(req)
            n = _moybyte.events(buf) if n == 4 else 0
    req.wake = _moybyte.WAKES.on_ring(src, wake, 1 << _moybyte.NET)


def _moybyte_buf():
    from array import array
    return array("i", [0] * (_moybyte.EV_FIELDS * 4))


def get(url, cap=16384, on_done=None):
    p = _D.need("http")
    _hold()
    h = _N.open(p, str(url), int(cap))
    if h < 0:
        _settle()
        raise _D.error(-h)
    req = Request(h, p)
    _open[h] = req
    if on_done is not None:
        _wake_for(req, on_done)
    if auto_run:
        _N.run()
    return req


def tls():
    return bool(_N.tls())


def present():
    return _N.present() and _D.console is not None and getattr(_D.console, "wifi", None) is not None


def sweep(pid):
    """Process `pid` ended: its requests closed, the radio let go once none
    is in flight."""
    for req in [r for r in _open.values() if r.owner == pid]:
        _end(req)
    _N.sweep(pid)
    _settle()
