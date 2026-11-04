"""`moybyte.ble`: the `ble` device API's face (docs/os_2026-11/hardware.md
section 1) over its driver (native/moy_input/moy_gatt.h, the module
moy_gatt). Needs the `ble` permission; one process holds it at a time, taken
at its first verb and let go at its end (a connection dropped, advertising
stopped). Present only where the console's BLE host runs: Settings' keyboard
input starts it, and with keyboard input off there is no `moybyte.ble`.

Addresses are "aa:bb:cc:dd:ee:ff"; a UUID is an int (16-bit) or the 128-bit
string. Every verb answers at once; what it did arrives as an event, to
`on(fn)`: fn(event, value, status), status 0 or a negative errno.

  scan(ms=3000)             EV_ADV (value: the device's index) as each
                            device is first heard, then EV_SCAN_DONE (-EBUSY
                            while the console's keyboard is scanning)
  devices() -> [(addr, rssi, name)]   what the last scan heard
  connect(addr)             EV_CONNECTED
  find(uuid)                EV_FOUND, value: the characteristic's handle
  read(handle)              EV_READ, value: its length; value(into) -> n
  write(handle, data, response=True)  EV_WRITTEN when it asks a response
  subscribe(handle, on=True)          EV_NOTIFY (value: the handle) for each
                                      notification; notified(into) ->
                                      (n, handle) | None
  disconnect()              EV_DISCONNECTED
  advertise(name, data=b"", interval_ms=200)
                            a non-connectable beacon: the name and
                            manufacturer data (its company id first);
                            EV_ADVERTISING
  stop_advertising()
  close()"""

try:
    import moy_gatt as _N
except ImportError:  # host: the runtime package
    from runtime import moy_gatt as _N

try:
    import devices as _D
except ImportError:  # host: the runtime package
    from runtime import devices as _D

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package
    from runtime import moybyte as _moybyte

EV_ADV, EV_SCAN_DONE, EV_CONNECTED = _N.EV_ADV, _N.EV_SCAN_DONE, _N.EV_CONNECTED
EV_DISCONNECTED, EV_FOUND, EV_READ = _N.EV_DISCONNECTED, _N.EV_FOUND, _N.EV_READ
EV_WRITTEN, EV_NOTIFY, EV_ADVERTISING = _N.EV_WRITTEN, _N.EV_NOTIFY, _N.EV_ADVERTISING

_held = {}          # pid -> [meta, wake id]


def _take():
    p = _D.need("ble")
    if _N.owner() != p:
        _D.check(_N.open(p))
    if p not in _held:
        from array import array
        _held[p] = [array("i", [0]), None]
    return p


def _flip(b):
    n = len(b)
    out = bytearray(n)
    for i in range(n):
        out[i] = b[n - 1 - i]
    return bytes(out)


def addr_bytes(s):
    return _flip(bytes(int(x, 16) for x in s.split(":")))


def addr_str(b):
    return ":".join("%02x" % b[5 - i] for i in range(6))


def uuid_bytes(u):
    if isinstance(u, int):
        return bytes([u & 0xFF, (u >> 8) & 0xFF])
    s = u.replace("-", "")
    return _flip(bytes(int(s[i:i + 2], 16) for i in range(0, 32, 2)))


def scan(ms=3000):
    _D.check(_N.scan(_take(), ms))


def devices():
    out = []
    for i in range(_N.seen_count()):
        s = _N.seen(i)
        if s is not None:
            out.append((addr_str(s[0]), s[2], s[3]))
    return out


def connect(addr):
    p = _take()
    for i in range(_N.seen_count()):
        s = _N.seen(i)
        if s is not None and addr_str(s[0]) == addr:
            return _D.check(_N.connect(p, s[0], s[1]))
    return _D.check(_N.connect(p, addr_bytes(addr), 0))


def disconnect():
    _D.check(_N.disconnect(_take()))


def connected():
    return _N.owner() == _D.pid() and _N.state() == _N.CONNECTED


def find(uuid):
    _D.check(_N.find(_take(), uuid_bytes(uuid)))


def read(handle):
    _D.check(_N.read(_take(), handle))


def value(into):
    return _D.check(_N.value(_take(), into))


def write(handle, data, response=True):
    _D.check(_N.write(_take(), handle, data, response))


def subscribe(handle, on=True):
    _D.check(_N.subscribe(_take(), handle, on))


def notified(into):
    p = _take()
    m = _held[p][0]
    n = _D.check(_N.notified(p, into, m))
    return (n, m[0]) if n else None


def advertise(name, data=b"", interval_ms=200):
    _D.check(_N.advertise(_take(), name, data, interval_ms))


def stop_advertising():
    _D.check(_N.stop_advertising(_take()))


def on(fn):
    """Wake `fn(event, value, status)` for each of this API's events."""
    p = _take()
    h = _held[p]
    if h[1] is not None:
        _moybyte.WAKES.cancel(h[1])
    from array import array
    buf = array("i", [0] * (_moybyte.EV_FIELDS * 4))
    F, K, ID, A, B = (_moybyte.EV_FIELDS, _moybyte.EV_KIND, _moybyte.EV_ID, _moybyte.EV_A,
                      _moybyte.EV_B)
    ble = _moybyte.BLE

    def wake():
        n = _moybyte.events(buf)
        while n:
            for i in range(n):
                k = i * F
                if buf[k + K] == ble:
                    fn(buf[k + ID], buf[k + A], buf[k + B])
            n = _moybyte.events(buf) if n == 4 else 0
    h[1] = _moybyte.WAKES.on_ring(_N.source(), wake, 1 << ble)
    return h[1]


def close():
    sweep(_D.pid())


def present():
    return _N.present()


def sweep(pid):
    h = _held.pop(pid, None)
    if h is not None and h[1] is not None:
        _moybyte.WAKES.cancel(h[1])
    return _N.sweep(pid)
