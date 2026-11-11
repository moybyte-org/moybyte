"""`moybyte.midi`: the `midi` device API's face (docs/os_2026-11/hardware.md
section 1) over its driver (native/moy_input/moy_midi.h, the module
moy_midi): MIDI over a leased UART, a USB-MIDI device and a BLE-MIDI
peripheral, every message a MIDI record on the driver's ring. Needs the
`midi` permission (and `lease:<row>` for a UART); one process holds it, and
its end closes every port.

  uart(row="uart1", baud=31250) -> port
                        the board's UART row leased to the caller, its bytes
                        read off the FIFO by the driver
  usb(addr) -> port     a USB-MIDI device (its address, as moybyte.usb
                        lists it)
  ble(addr) -> port     a BLE-MIDI peripheral: connected, its MIDI I/O
                        characteristic found and subscribed (moybyte.ble's
                        host, which must be up) by the driver's own machine
                        on the BLE driver's events (moy_midi_ble); messages
                        flow once it is
  send(port, data)      MIDI bytes, whole messages (no system exclusive)
  on(fn)                fn(port, status, data1, data2) for each message
  ports() -> [(port, transport, unit)]   transport UART, USB or BLE
  state(port)           "ready" once messages flow (a UART's and a USB
                        device's at once), a BLE port's "connecting",
                        "finding", "failed" or "gone" before or after
  source()              the ring source its messages ride (a reader's)
  close(port=None)      one port, or all of them and the API

A compiled program's ports are the kernel's handles (native/moy_kernel/
moy_dev.h's midi row, native/moy_input/moy_midi_dev.c), which share the
driver's one holder: a process holds the API while either side holds a
port.

A transport the console lacks refuses with OSError(ENODEV); `present()` is
whether any is there."""

try:
    import moy_midi as _N
except ImportError:  # host: the runtime package
    from runtime import moy_midi as _N

try:
    import devices as _D
except ImportError:  # host: the runtime package
    from runtime import devices as _D

try:
    import moybyte as _moybyte
    from ticks import _ticks_ms
except ImportError:  # host: the runtime package
    from runtime import moybyte as _moybyte
    from runtime.ticks import _ticks_ms

UART, USB, BLE = _N.UART, _N.USB, _N.BLE
_BLE_STATES = ("connecting", "finding", "ready", "failed", "gone")   # the driver's BLE_*

# devices' sweep order: this one runs before the leases', so a UART's reader
# stops before its driver goes.
SWEEP_FIRST = True

_wakes = {}         # pid -> wake id
_took = {}          # pid -> the USB and BLE drivers it took through this API
_ports = {}         # pid -> {port: [transport, unit, the UART lease | the BLE state]}


def _usb():
    try:
        import moy_usb
    except ImportError:
        try:
            from runtime import moy_usb
        except ImportError:  # a console with no USB host
            return None
    return moy_usb if moy_usb.present() else None


def _gatt():
    try:
        import moy_gatt
    except ImportError:  # host: the runtime package
        from runtime import moy_gatt
    return moy_gatt if moy_gatt.present() else None


def _ble():
    try:
        import dev_ble
    except ImportError:  # host: the runtime package
        from runtime import dev_ble
    return dev_ble


def _leases():
    try:
        import leases
    except ImportError:  # host: the runtime package
        from runtime import leases
    return leases


def _uart_rows():
    return [o[0] for o in _leases().LEASES.offers() if o[0].startswith("uart")]


def _take():
    p = _D.need("midi")
    if _N.owner() != p:
        _D.check(_N.open(p))
    _ports.setdefault(p, {})
    return p


def _err(e):
    return _D.error(e)


def uart(row="uart1", baud=31250):
    p = _take()
    L = _leases().LEASES
    o = L.offer(row)
    if o is None or not row.startswith("uart"):
        raise _err(_D.ENODEV)
    u = L.take(p, row, {"baudrate": baud})
    port = _N.port(p, UART, o[2])
    if port < 0:
        L.drop(u)
        raise _err(-port)
    rc = _N.uart(p, port)
    if rc < 0:
        _N.unport(p, port)
        L.drop(u)
        raise _err(-rc)
    _ports[p][port] = [UART, o[2], u]
    return port


def usb(addr):
    p = _take()
    U = _usb()
    if U is None:
        raise _err(_D.ENODEV)
    if U.owner() != p:
        _D.check(U.open(p))
        _took.setdefault(p, []).append(U)
    for i in range(U.count()):
        d = U.device(i)
        if d is not None and d[0] == addr and d[1] == U.MIDI:
            port = _D.check(_N.port(p, USB, addr))
            _ports[p][port] = [USB, addr, None]
            return port
    raise _err(_D.ENODEV)


def ble(addr):
    p = _take()
    G = _gatt()
    if G is None:
        raise _err(_D.ENODEV)
    if G.owner() != p:
        _D.check(G.open(p))
        _took.setdefault(p, []).append(G)
    a = _ble().addr_bytes(addr)
    t = 0
    for i in range(G.seen_count()):
        s = G.seen(i)
        if s is not None and s[0] == a:
            t = s[1]
    port = _D.check(_N.port(p, BLE, 0))
    _ports[p][port] = [BLE, 0, None]
    rc = _N.ble(p, port, a, t)
    if rc < 0:
        _N.unport(p, port)
        del _ports[p][port]
        raise _err(-rc)
    return port


def send(port, data):
    p = _take()
    v = _ports[p].get(port)
    if v is None:
        raise _err(_D.EBADF)
    if v[0] == UART:
        _D.check(_N.usb_pack(data, bytearray(4 * len(data) + 4)))   # whole messages only
        v[2].write(data)
    elif v[0] == USB:
        out = bytearray(4 * len(data) + 4)
        n = _D.check(_N.usb_pack(data, out))
        _D.check(_usb().write(p, v[1], memoryview(out)[:n]))
    else:
        _D.check(_N.ble_send(p, port, data, _ticks_ms()))


def on(fn):
    """Wake `fn(port, status, data1, data2)` for each MIDI message."""
    p = _take()
    w = _wakes.get(p)
    if w is not None:
        _moybyte.WAKES.cancel(w)
    from array import array
    buf = array("i", [0] * (_moybyte.EV_FIELDS * 8))
    F, K, ID, A, B = (_moybyte.EV_FIELDS, _moybyte.EV_KIND, _moybyte.EV_ID, _moybyte.EV_A,
                      _moybyte.EV_B)
    midi = _moybyte.MIDI

    def wake():
        n = _moybyte.events(buf)
        while n:
            for i in range(n):
                k = i * F
                if buf[k + K] == midi:
                    a = buf[k + A]
                    fn(buf[k + ID], a & 0xff, (a >> 8) & 0x7f, buf[k + B])
            n = _moybyte.events(buf) if n == 8 else 0
    _wakes[p] = _moybyte.WAKES.on_ring(_N.source(), wake, 1 << midi)
    return _wakes[p]


def ports():
    p = _D.pid()
    return [(k, v[0], v[1]) for k, v in sorted(_ports.get(p, {}).items())]


def state(port):
    p = _D.pid()
    v = _ports.get(p, {}).get(port)
    if v is None:
        raise _err(_D.EBADF)
    return _BLE_STATES[_D.check(_N.ble_state(p, port))] if v[0] == BLE else "ready"


def source():
    return _N.source()


def _drop(p, port, v):
    _N.unport(p, port)
    if v[0] == UART:
        _leases().LEASES.drop(v[2])


def close(port=None):
    p = _D.pid()
    if port is None:
        sweep(p)
        return
    v = _ports.get(p, {}).pop(port, None)
    if v is None:
        raise _err(_D.EBADF)
    _drop(p, port, v)


def present():
    return bool(_uart_rows()) or _usb() is not None or _gatt() is not None


def sweep(pid):
    """The process's ports closed -- each UART's reader stopped before its
    lease goes, which is why this runs ahead of the leases' sweep -- and the
    API let go."""
    w = _wakes.pop(pid, None)
    if w is not None:
        _moybyte.WAKES.cancel(w)
    held = _ports.pop(pid, {})
    for port, v in held.items():
        _drop(pid, port, v)
    for d in _took.pop(pid, ()):
        d.sweep(pid)
    return _N.sweep(pid)
