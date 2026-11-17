"""`moybyte.usb`: the `usb` device API's face (docs/os_2026-11/hardware.md
section 1, #83) over its driver (native/p4/moy_usb/moy_usb.h, the module
moy_usb): the USB host on a board's OTG port, present on the P4 boards and
None on any other console. Needs the `usb` permission; one process holds it,
taken at its first verb and let go at its end.

A device is its bus address. Every verb answers at once; what changes
arrives as an event, to `on(fn)`: fn(event, addr, value).

  open()               the API held before its first verb
  source()             the ring source its events ride (a reader's)
  devices() -> [(addr, cls, kind, vid, pid, name)]
                       what is plugged in: cls HID, CDC, MIDI or OTHER; a
                       HID boot keyboard's or mouse's kind KEYBOARD or MOUSE
  report(addr, into) -> n
                       a HID device's latest report (EV_REPORT as each
                       comes, value: its length)
  serial(addr, baud=115200)
                       a CDC-ACM device's line, 8N1, DTR and RTS up
  write(addr, data)    to a serial or MIDI device (EV_SENT, value 0 or
                       -errno)
  read(addr, into) -> n
                       its bytes, oldest first (EV_DATA, value: how many wait)
  on(fn)               EV_ATTACH (value: the class), EV_DETACH, EV_REPORT,
                       EV_DATA, EV_SENT
  close()

The host has a USB host only under its stand-in (runtime/host/usb_sim.py)."""

try:
    import moy_usb as _N
except ImportError:  # host, or a console with no USB host
    _N = None
if _N is None:
    try:
        from runtime import moy_usb as _N
    except ImportError:  # a console with no USB host
        _N = None

import devices as _D
import moybyte as _moybyte

if _N is not None:
    HID, CDC, MIDI, OTHER = _N.HID, _N.CDC, _N.MIDI, _N.OTHER
    KEYBOARD, MOUSE = _N.KEYBOARD, _N.MOUSE
    EV_ATTACH, EV_DETACH, EV_REPORT = _N.EV_ATTACH, _N.EV_DETACH, _N.EV_REPORT
    EV_DATA, EV_SENT = _N.EV_DATA, _N.EV_SENT

_wakes = {}          # pid -> wake id


def _take():
    p = _D.need("usb")
    if _N.owner() != p:
        _D.check(_N.open(p))
    return p


def open():  # noqa: A001 -- the verb's name
    _take()


def source():
    return _N.source()


def devices():
    _take()
    out = []
    for i in range(_N.count()):
        d = _N.device(i)
        if d is not None:
            out.append((d[0], d[1], d[2], d[4], d[5], d[6]))
    return out


def report(addr, into):
    return _D.check(_N.report(_take(), addr, into))


def serial(addr, baud=115200):
    _D.check(_N.serial(_take(), addr, baud))


def write(addr, data):
    _D.check(_N.write(_take(), addr, data))


def read(addr, into):
    return _D.check(_N.read(_take(), addr, into))


def on(fn):
    """Wake `fn(event, addr, value)` for each of this API's events."""
    p = _take()
    w = _wakes.get(p)
    if w is not None:
        _moybyte.WAKES.cancel(w)
    from array import array
    buf = array("i", [0] * (_moybyte.EV_FIELDS * 4))
    F, K, ID, A, B = (_moybyte.EV_FIELDS, _moybyte.EV_KIND, _moybyte.EV_ID, _moybyte.EV_A,
                      _moybyte.EV_B)
    usb = _moybyte.USB

    def wake():
        n = _moybyte.events(buf)
        while n:
            for i in range(n):
                k = i * F
                if buf[k + K] == usb:
                    fn(buf[k + ID], buf[k + A], buf[k + B])
            n = _moybyte.events(buf) if n == 4 else 0
    _wakes[p] = _moybyte.WAKES.on_ring(_N.source(), wake, 1 << usb)
    return _wakes[p]


def close():
    sweep(_D.pid())


def present():
    return _N is not None and bool(_N.present())


def sweep(pid):
    w = _wakes.pop(pid, None)
    if w is not None:
        _moybyte.WAKES.cancel(w)
    return _N.sweep(pid) if _N is not None else 0
