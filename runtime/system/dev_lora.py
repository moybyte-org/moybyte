"""`moybyte.lora`: the `lora` device API's face (docs/os_2026-11/hardware.md
sections 1 and 4) over its driver (native/moy_net/moy_lora.h, the module
moy_lora). Needs the `lora` permission; one process holds the radio at a
time, taken at its first verb and let go at its end.

Raw packets (the mesh case):

  config(freq=868100000, sf=9, bw=125000, cr=5, power=14, sync=0x12,
         preamble=8)        the radio's raw settings
  listen(on=True)           receive between transmissions
  send(data)                a packet out (OSError EBUSY while one is going)
  recv(into, meta=None) -> n | None
                            the next waiting packet into `into`: with an
                            array('i', 3) `meta`, its RSSI (dBm), SNR (dB x 4)
                            and port (0: raw) go there and n is returned,
                            allocating nothing; without one,
                            (n, rssi, snr_db) or None

LoRaWAN, over the OS-side MAC (OTAA, class A, EU868):

  join(dev_eui, join_eui, app_key, dr=5)
                            each the bytes a network console prints (8, 8
                            and 16); answers at once, its outcome an event
  uplink(port, data, confirmed=False, dr=5)
                            OSError ENOTCONN before a join
  downlink(into) -> (n, port) | None
                            the next downlink a receive window caught
  joined() -> (dev_addr, fcnt_up) | None
  busy()                    whether a transmission, a join or an uplink's
                            receive windows run (listening is not busy)

  on(fn)                    wake `fn(event, n, rssi, snr_db)` for each of the
                            radio's events: EV_RX, EV_TX_DONE, EV_JOINED,
                            EV_JOIN_FAILED, EV_DOWN, EV_UP_DONE (n 1: acked)
  close()

The receive windows are timed by the driver in C. On a board the driver's own
task steps it; the host's stand-in is stepped by its test
(runtime/host/lora_sim.py)."""

try:
    import moy_lora as _N
except ImportError:  # host: the runtime package
    from runtime import moy_lora as _N

try:
    import devices as _D
except ImportError:  # host: the runtime package
    from runtime import devices as _D

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package
    from runtime import moybyte as _moybyte

EV_RX, EV_TX_DONE, EV_JOINED = _N.EV_RX, _N.EV_TX_DONE, _N.EV_JOINED
EV_JOIN_FAILED, EV_DOWN, EV_UP_DONE = _N.EV_JOIN_FAILED, _N.EV_DOWN, _N.EV_UP_DONE

_held = {}          # pid -> [meta, wake id]


def _take():
    p = _D.need("lora")
    if _N.owner() != p:
        _D.check(_N.open(p))
    if p not in _held:
        from array import array
        _held[p] = [array("i", [0, 0, 0]), None]
    return p


def config(freq=868100000, sf=9, bw=125000, cr=5, power=14, sync=0x12, preamble=8):
    p = _take()
    _D.check(_N.config(p, freq, sf, bw, cr, power, sync, preamble))


def listen(on=True):
    _D.check(_N.listen(_take(), on))


def send(data):
    _D.check(_N.send(_take(), data))


def recv(into, meta=None):
    p = _take()
    m = meta if meta is not None else _held[p][0]
    n = _D.check(_N.recv(p, into, m))
    if meta is not None:
        return n
    if n == 0:
        return None
    return n, m[0], m[1] / 4


def join(dev_eui, join_eui, app_key, dr=5):
    _D.check(_N.join(_take(), bytes(dev_eui), bytes(join_eui), bytes(app_key), dr))


def uplink(port, data, confirmed=False, dr=5):
    _D.check(_N.uplink(_take(), port, data, confirmed, dr))


def downlink(into):
    p = _take()
    m = _held[p][0]
    n = _D.check(_N.recv(p, into, m))
    return (n, m[2]) if n else None


def joined():
    j, addr, fcnt = _N.joined()
    return (addr, fcnt) if j and _N.owner() == _D.pid() else None


def busy():
    return _N.owner() == _D.pid() and _N.mode() not in (_N.IDLE, _N.LISTEN)


def on(fn):
    """Wake `fn(event, n, rssi, snr_db)` for each of the radio's events."""
    p = _take()
    h = _held[p]
    if h[1] is not None:
        _moybyte.WAKES.cancel(h[1])
    from array import array
    buf = array("i", [0] * (_moybyte.EV_FIELDS * 4))
    F, K, ID, A, B = (_moybyte.EV_FIELDS, _moybyte.EV_KIND, _moybyte.EV_ID, _moybyte.EV_A,
                      _moybyte.EV_B)
    lora = _moybyte.LORA

    def wake():
        n = _moybyte.events(buf)
        while n:
            for i in range(n):
                k = i * F
                if buf[k + K] == lora:
                    b = buf[k + B] & 0xFFFFFFFF
                    rssi = b & 0xFFFF
                    snr = b >> 16
                    fn(buf[k + ID], buf[k + A], rssi - 0x10000 if rssi & 0x8000 else rssi,
                       (snr - 0x10000 if snr & 0x8000 else snr) / 4)
            n = _moybyte.events(buf) if n == 4 else 0
    h[1] = _moybyte.WAKES.on_ring(_N.source(), wake, 1 << lora)
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
