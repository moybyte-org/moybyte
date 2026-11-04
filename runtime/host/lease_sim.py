"""The host's stand-in for MicroPython's `machine` objects on a lease
(runtime/system/leases.py): Pin, UART and SPI with the boards' API, whose pins
live in the lease table's stand-in (native/moy_kernel/moy_lease.c), so a lease's
drop leaves them safe there and `moy_lease.pin_state` reads them back as it
reads a board's GPIO matrix. Pure Python, for CPython and the desktop
MicroPython alike; never staged.

The far end, for a test: `moy_lease.sim_level(pin, v)` moves an input pin (an
edge where one is open); `feed(unit, data)` is what a UART's peer sent;
`sent(unit)` what it wrote; `loopback(unit, on)` joins its TX to its RX, as
uart_set_loop_back does; `SPI_LOG[id]` every buffer an SPI host wrote."""

try:
    import moy_lease as _N
except ImportError:  # CPython: the runtime package
    from runtime import moy_lease as _N

_RX = {}            # unit -> bytearray its peer sent, unread
_TX = {}            # unit -> bytearray it wrote
_LOOP = {}          # unit -> TX joined to RX
SPI_LOG = {}        # SPI id -> [bytes written]


def feed(unit, data):
    _RX.setdefault(unit, bytearray()).extend(data)


def sent(unit):
    return bytes(_TX.get(unit, b""))


def loopback(unit, on):
    _LOOP[unit] = bool(on)


def reset():
    _RX.clear()
    _TX.clear()
    _LOOP.clear()
    SPI_LOG.clear()


class Pin:
    IN = 1
    OUT = 3
    OPEN_DRAIN = 7
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_RISING = 1
    IRQ_FALLING = 2

    def __init__(self, id, mode=-1, pull=-1, value=None):
        self.id = int(id)
        self._mode = self.IN
        self._pull = None
        if mode != -1 or pull != -1 or value is not None:
            self.init(mode, pull, value=value)

    def init(self, mode=-1, pull=-1, value=None):
        if mode != -1 and mode is not None:
            self._mode = mode
        if pull != -1:
            self._pull = pull
        st = _N.PIN_IN
        if self._mode in (self.OUT, self.OPEN_DRAIN):
            st |= _N.PIN_OUT
        if self._pull == self.PULL_UP:
            st |= _N.PIN_PU
        elif self._pull == self.PULL_DOWN:
            st |= _N.PIN_PD
        lvl = _N.sim_get(self.id) if value is None else (1 if value else 0)
        _N.sim_pin(self.id, st, lvl)

    def value(self, *v):
        if not v:
            return _N.sim_get(self.id)
        if _N.pin_state(self.id) & _N.PIN_OUT:
            _N.sim_level(self.id, 1 if v[0] else 0)
        return None

    def __call__(self, *v):
        return self.value(*v)

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)


def _periph(pin, out):
    _N.sim_pin(pin, _N.PIN_IN | _N.PIN_PERIPH | (_N.PIN_OUT if out else 0), _N.sim_get(pin))


class UART:
    def __init__(self, id, baudrate=115200, tx=None, rx=None, **kw):
        self.id = int(id)
        self.tx, self.rx = tx, rx
        self.init(baudrate, **kw)

    def init(self, baudrate=115200, **kw):
        self.baudrate = baudrate
        if self.rx is not None:
            _periph(self.rx, False)
        if self.tx is not None:
            _periph(self.tx, True)

    def deinit(self):
        pass

    def any(self):
        return len(_RX.get(self.id, b""))

    def read(self, n=-1):
        buf = _RX.get(self.id)
        if not buf:
            return None
        n = len(buf) if n is None or n < 0 else n
        out = bytes(buf[:n])
        del buf[:n]
        return out

    def readinto(self, into, n=-1):
        got = self.read(len(into) if n < 0 else n)
        if not got:
            return None
        into[:len(got)] = got
        return len(got)

    def readline(self):
        buf = _RX.get(self.id)
        if not buf:
            return None
        i = buf.find(b"\n")
        return self.read(len(buf) if i < 0 else i + 1)

    def write(self, data):
        data = bytes(data)
        _TX.setdefault(self.id, bytearray()).extend(data)
        if _LOOP.get(self.id):
            feed(self.id, data)
        return len(data)

    def flush(self):
        pass

    def txdone(self):
        return True

    def sendbreak(self):
        pass


class SPI:
    MSB = 0
    LSB = 1

    def __init__(self, id, baudrate=500000, sck=None, mosi=None, miso=None, **kw):
        self.id = int(id)
        self.pins = tuple(getattr(p, "id", p) for p in (sck, mosi, miso) if p is not None)
        self.init(baudrate, **kw)

    def init(self, baudrate=500000, polarity=0, phase=0, **kw):
        self.baudrate, self.polarity, self.phase = baudrate, polarity, phase
        for i, p in enumerate(self.pins):
            _periph(p, i < 2)

    def deinit(self):
        pass

    def write(self, buf):
        SPI_LOG.setdefault(self.id, []).append(bytes(buf))

    def read(self, n, write=0):
        SPI_LOG.setdefault(self.id, []).append(bytes([write]) * n)
        return b"\xff" * n

    def readinto(self, buf, write=0):
        for i in range(len(buf)):
            buf[i] = 0xFF
        SPI_LOG.setdefault(self.id, []).append(bytes([write]) * len(buf))

    def write_readinto(self, out, into):
        self.write(out)
        for i in range(len(into)):
            into[i] = 0xFF
