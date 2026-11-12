"""Leases as MicroPython `machine` objects (docs/os_2026-11/hardware.md
section 1), over the lease table (native/moy_kernel/moy_lease.h).

A Python process asks `moybyte.lease(name, **cfg)` for a row its board
offers, and gets MicroPython's own object for it, wrapped:

  "gpio<n>"   a machine.Pin on pin n; cfg is Pin's (mode, pull, value)
  "uart<n>"   a machine.UART; cfg is UART's, tx and rx defaulting to the row's
  "spi<n>"    a machine.SPI; cfg is SPI's, sck, mosi and miso defaulting to
              the row's (MicroPython's SPI(1) is the chip's SPI2)
  "pwm<n>"    a machine.PWM on pin n; cfg is PWM's (freq, duty_u16)
  "adc<n>"    a machine.ADC on pin n; cfg is ADC's
  "i2c"       a machine.SoftI2C on two pins of the gpio row; cfg is
              SoftI2C's, scl and sda required

A PWM, an ADC and an I2C bus are pins of the gpio row (an I2C bus is clocked
by the CPU, so it holds no unit), each needing `lease:gpio` as a pin does.

so a driver written for MicroPython runs on it unchanged. The lease is the
process's until it ends: the sweep (`moybyte.end`, on exit, crash and kill)
deinits each object, then drops its lease, which leaves its pins safe, and
an object kept past its process raises OSError(EBADF) instead of driving a
pin. A take is refused (OSError) where the board offers no such row
(EINVAL), another process holds the unit or a pin (EBUSY), or the process
lacks `lease:<row>` (EPERM: `lease:gpio` for any gpio pin), which `grants`
answers; the shell (pid 1) may take any.

A leased Pin's `irq(handler, trigger)` opens the pin's edges as a ring source
(moy_lease_edge) and wakes the process on them (moybyte's `on_ring`): the
handler runs with the pin, from the wake, for each EDGE the trigger names.

`pins()` is what the `pins` command lists: every row the board offers, each
pin or unit with the pid and name of the process holding it.

The `pins` permission's verbs (`pin_write`, `pin_read`: hardware.md section
1's digital subset) are `local_pins()`'s on a board whose gpio row has pins:
each takes the pin for the calling process the first time, marked PINS.

The machine objects are the VM's own `machine` on a board. A host has none and
offers nothing, unless a test or the simulator gives the board's rows and a
stand-in (`stand_in`, runtime/host/lease_sim.py).
"""

import moy_lease as _N

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

import moybyte as _moybyte

try:
    import machine as _machine
    _machine.Pin, _machine.UART, _machine.SPI
except (ImportError, AttributeError):  # a host: no machine objects
    _machine = None

try:
    from array import array as _array
except ImportError:  # pragma: no cover
    _array = None

EPERM, EBADF, EBUSY, EINVAL, ENODEV = 1, 9, 16, 22, 19
SHELL = 1
EDGE_DEPTH = 64
_BATCH = 8              # edge records an irq wake reads at a time

_CLASS = {"gpio": _N.GPIO, "uart": _N.UART, "spi": _N.SPI}


def _pid():
    return _L.current() if _L is not None else SHELL


def _name(pid):
    if pid == SHELL:
        return "shell"
    return _L.name(pid) if _L is not None else "pid %d" % pid


_ON_PINS = ("gpio", "pwm", "adc", "i2c")


def perm_of(name):
    """The permission a lease of row `name` needs: `lease:gpio` for anything
    on the gpio row's pins."""
    return "lease:gpio" if name.startswith(_ON_PINS) else "lease:" + name


class _Leased:
    """MicroPython's object on a leased resource, live while its lease is."""

    def __init__(self, leases, h, obj, name, pid):
        self._leases = leases
        self._h = h
        self._obj = obj
        self.lease_name = name
        self.lease_pid = pid

    def _o(self):
        if not _N.live(self._h):
            raise OSError(EBADF, "the lease on %s ended" % self.lease_name)
        return self._obj

    def _end(self):
        """The sweep: the driver ended before the lease is dropped."""
        d = getattr(self._obj, "deinit", None)
        if d is not None:
            try:
                d()
            except Exception:  # noqa: BLE001 -- a sweep finishes
                pass


class Pin(_Leased):
    """A leased machine.Pin."""

    IN = OUT = OPEN_DRAIN = PULL_UP = PULL_DOWN = IRQ_RISING = IRQ_FALLING = None

    def __init__(self, leases, h, obj, name, pid, pin):
        _Leased.__init__(self, leases, h, obj, name, pid)
        self.pin = pin
        self._irq = None            # its irq wake while a handler is set
        self._out = None            # LocalPins: whether it last drove the pin

    def init(self, *args, **kw):
        return self._o().init(*args, **kw)

    def value(self, *v):
        return self._o().value(*v)

    def __call__(self, *v):
        return self._o().value(*v)

    def on(self):
        self._o().value(1)

    def off(self):
        self._o().value(0)

    def toggle(self):
        o = self._o()
        o.value(1 - o.value())

    def irq(self, handler=None, trigger=None, hard=False):
        self._o()
        self._leases.irq(self, handler, trigger)

    def _end(self):
        self._leases.irq_end(self)


class UART(_Leased):
    """A leased machine.UART."""

    def init(self, *args, **kw):
        return self._o().init(*args, **kw)

    def deinit(self):
        return self._o().deinit()

    def any(self):
        return self._o().any()

    def read(self, *n):
        return self._o().read(*n)

    def readinto(self, buf, *n):
        return self._o().readinto(buf, *n)

    def readline(self):
        return self._o().readline()

    def write(self, buf):
        return self._o().write(buf)

    def flush(self):
        return self._o().flush()

    def txdone(self):
        return self._o().txdone()

    def sendbreak(self):
        return self._o().sendbreak()


class SPI(_Leased):
    """A leased machine.SPI."""

    def init(self, *args, **kw):
        return self._o().init(*args, **kw)

    def deinit(self):
        return self._o().deinit()

    def read(self, n, write=0):
        return self._o().read(n, write)

    def readinto(self, buf, write=0):
        return self._o().readinto(buf, write)

    def write(self, buf):
        return self._o().write(buf)

    def write_readinto(self, out, into):
        return self._o().write_readinto(out, into)


class PWM(_Leased):
    """A leased machine.PWM."""

    def init(self, *args, **kw):
        return self._o().init(*args, **kw)

    def freq(self, *v):
        return self._o().freq(*v)

    def duty_u16(self, *v):
        return self._o().duty_u16(*v)

    def deinit(self):
        return self._o().deinit()


class ADC(_Leased):
    """A leased machine.ADC."""

    def read_u16(self):
        return self._o().read_u16()


class I2C(_Leased):
    """A leased machine.SoftI2C."""

    def scan(self):
        return self._o().scan()

    def readfrom(self, addr, n, stop=True):
        return self._o().readfrom(addr, n, stop)

    def readfrom_into(self, addr, buf, stop=True):
        return self._o().readfrom_into(addr, buf, stop)

    def writeto(self, addr, buf, stop=True):
        return self._o().writeto(addr, buf, stop)

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        return self._o().readfrom_mem(addr, reg, n, addrsize=addrsize)

    def readfrom_mem_into(self, addr, reg, buf, addrsize=8):
        return self._o().readfrom_mem_into(addr, reg, buf, addrsize=addrsize)

    def writeto_mem(self, addr, reg, buf, addrsize=8):
        return self._o().writeto_mem(addr, reg, buf, addrsize=addrsize)


def _bind_pin_names(m):
    for n in ("IN", "OUT", "OPEN_DRAIN", "PULL_UP", "PULL_DOWN", "IRQ_RISING", "IRQ_FALLING"):
        setattr(Pin, n, getattr(m.Pin, n, None))


def _maker(m, kind):
    """`machine`'s class `kind`, or OSError(ENODEV) where this VM has none."""
    mk = getattr(m, kind, None)
    if mk is None:
        raise OSError(ENODEV, "this console has no %s" % kind)
    return mk


class Leases:
    def __init__(self, machine=None):
        self.machine = None
        self.grants = None          # fn(pid) -> its permissions, None: not gated
        self.settle = None          # fn(): the radio down when no row holds it
        self.held = {}              # pid -> [its leased objects]
        if machine is not None:
            self.use(machine)

    def use(self, machine):
        """The module whose Pin, UART and SPI a lease hands out."""
        self.machine = machine
        _bind_pin_names(machine)

    # -- the offers ------------------------------------------------------------

    def offers(self):
        """[(name, cls, unit, named pins, every pin)]: the board's rows."""
        return _N.offers()

    def offer(self, name):
        for o in _N.offers():
            if o[0] == name:
                return o
        return None

    def names(self):
        """Every name a lease takes: gpio<n> for each pin of the gpio row, and
        each unit row."""
        out = []
        for o in _N.offers():
            if o[1] == _N.GPIO:
                out.extend("gpio%d" % p for p in o[4])
            else:
                out.append(o[0])
        return out

    # -- taking --------------------------------------------------------------------

    def _may(self, pid, perm):
        g = self.grants
        if g is None or pid == SHELL:
            return True
        have = g(pid)
        return have is None or perm in have

    def take(self, pid, name, cfg=None, flags=0):
        """Row `name` leased to process `pid` (cfg as `lease` takes it): the
        wrapped object."""
        cfg = dict(cfg or {})
        if not flags & _N.PINS and not self._may(pid, perm_of(name)):
            raise OSError(EPERM, 'needs "%s"' % perm_of(name))
        m = self.machine
        if m is None:
            raise OSError(ENODEV, "this console leases nothing")
        if name.startswith("gpio") and name[4:].isdigit():
            pin = int(name[4:])
            h = _N.take(pid, _N.GPIO, 0, (pin,), flags | _N.VM)
            make = lambda: Pin(self, h, m.Pin(pin, **cfg), name, pid, pin)  # noqa: E731
        elif name[:3] in ("pwm", "adc") and name[3:].isdigit():
            pin = int(name[3:])
            wrap, kind = (PWM, "PWM") if name[:3] == "pwm" else (ADC, "ADC")
            mk = _maker(m, kind)
            h = _N.take(pid, _N.GPIO, 0, (pin,), flags | _N.VM)
            make = lambda: wrap(self, h, mk(m.Pin(pin), **cfg), name, pid)  # noqa: E731
        elif name == "i2c":
            scl, sda = cfg.pop("scl", None), cfg.pop("sda", None)
            if scl is None or sda is None or scl == sda:
                raise OSError(EINVAL, "an i2c lease names its scl and sda pins")
            mk = _maker(m, "SoftI2C")
            h = _N.take(pid, _N.GPIO, 0, (scl, sda), flags | _N.VM)
            make = lambda: I2C(self, h, mk(scl=m.Pin(scl), sda=m.Pin(sda), **cfg),  # noqa: E731
                               name, pid)
        else:
            o = self.offer(name)
            if o is None or o[1] == _N.GPIO:
                raise OSError(EINVAL, "no lease %s on this board" % name)
            cls, unit, named = o[1], o[2], o[3]
            if cls == _N.UART:
                tx = cfg.pop("tx", named[0])
                rx = cfg.pop("rx", named[1])
                pins = (tx,) if tx == rx else (tx, rx)
                h = _N.take(pid, cls, unit, pins, flags | _N.VM)
                make = lambda: UART(self, h, m.UART(unit, tx=tx, rx=rx, **cfg), name, pid)  # noqa: E731
            else:
                sck = cfg.pop("sck", named[0])
                mosi = cfg.pop("mosi", named[1])
                miso = cfg.pop("miso", named[2])
                h = _N.take(pid, cls, unit, (sck, mosi, miso), flags | _N.VM)
                make = lambda: SPI(self, h, m.SPI(unit - 1, sck=m.Pin(sck), mosi=m.Pin(mosi),  # noqa: E731
                                                  miso=m.Pin(miso), **cfg), name, pid)
        try:
            obj = make()
        except BaseException:
            _N.drop(h)
            raise
        self.held.setdefault(pid, []).append(obj)
        return obj

    def lease(self, name, **cfg):
        return self.take(_pid(), name, cfg)

    def drop(self, obj):
        """One lease given back before its process ends."""
        objs = self.held.get(obj.lease_pid) or []
        if obj in objs:
            objs.remove(obj)
        if _N.live(obj._h):
            obj._end()
            _N.drop(obj._h)

    def sweep(self, pid):
        """Process `pid` ended: each object's driver ended, then its leases
        dropped, every pin they held left safe and its radio holds let go
        (the station powered down when they were the last). Answers how
        many."""
        for obj in self.held.pop(pid, ()):
            if _N.live(obj._h):
                obj._end()
        n = _N.sweep(pid)
        if self.settle is not None:
            self.settle()          # its radio holds went with its rows
        return n

    def of(self, pid, name):
        """The live object process `pid` holds for `name`, or None."""
        for obj in self.held.get(pid, ()):
            if obj.lease_name == name and _N.live(obj._h):
                return obj
        return None

    # -- edges ------------------------------------------------------------------------

    def irq(self, pin, handler, trigger):
        self.irq_end(pin)
        if handler is None:
            return
        if trigger is None:
            trigger = (Pin.IRQ_RISING or 1) | (Pin.IRQ_FALLING or 2)
        rising = Pin.IRQ_RISING or 1
        falling = Pin.IRQ_FALLING or 2
        src = _N.edge(pin._h, pin.pin, EDGE_DEPTH, False)
        buf = _array("i", [0] * (_moybyte.EV_FIELDS * _BATCH))
        F, K, B = _moybyte.EV_FIELDS, _moybyte.EV_KIND, _moybyte.EV_B
        edge = _moybyte.EDGE

        def wake():
            n = _moybyte.events(buf)
            while n:
                for i in range(n):
                    k = i * F
                    if buf[k + K] == edge and trigger & (rising if buf[k + B] else falling):
                        handler(pin)
                n = _moybyte.events(buf) if n == _BATCH else 0
        pin._irq = _moybyte.WAKES.on_ring(src, wake)

    def irq_end(self, pin):
        if pin._irq is not None:
            _moybyte.WAKES.cancel(pin._irq)
            pin._irq = None
            if _N.live(pin._h):
                try:
                    _N.edge_close(pin._h, pin.pin)
                except OSError:
                    pass

    # -- what `pins` lists ---------------------------------------------------------

    def rows(self):
        """[(name, pins, pid, process name, how)]: each row the board offers,
        a gpio pin a row, with who holds it (pid 0 and None: no one) and how
        ("lease", "pins"); then each device a process holds (moy_lease.h's
        DEVICES: "mic", "lora", "ble", "media"), how "device"."""
        held = {}
        devices = []
        for h in _N.leases():
            owner, cls, unit, flags, pins, _edges = _N.get(h)
            dev = _N.device_name(cls)
            if dev is not None:
                devices.append((dev, (), owner, _name(owner), "device"))
                continue
            how = "pins" if flags & _N.PINS else "lease"
            for p in pins:
                held[("p", p)] = (owner, how)
            if cls != _N.GPIO:
                held[("u", cls, unit)] = (owner, how)
        out = []
        for name, cls, unit, named, pins in _N.offers():
            if cls == _N.GPIO:
                for p in pins:
                    owner, how = held.get(("p", p), (0, None))
                    out.append(("gpio%d" % p, (p,), owner, _name(owner) if owner else None, how))
            else:
                owner, how = held.get(("u", cls, unit), (0, None))
                out.append((name, named, owner, _name(owner) if owner else None, how))
        return out + sorted(devices)

    def pins(self):
        """The `pins` command's lines: a row each, `name pins holder`."""
        lines = []
        for name, pins, owner, who, how in self.rows():
            at = ",".join(str(p) for p in pins)
            lines.append("%-6s %-9s %s" % (name, at, "%s/%d %s" % (who, owner, how)
                                           if owner else "-"))
        return "\n".join(lines)


LEASES = Leases(_machine)
_moybyte.ENDS.append(LEASES.sweep)


def lease(name, **cfg):
    return LEASES.lease(name, **cfg)


def pins():
    return LEASES.pins()


def stand_in(machine, spec):
    """The host's stand-in: the board rows `spec` (moy_lease's offer spec;
    tools/board_config.py lease_spec reads a board's) offered, with `machine`
    (runtime/host/lease_sim.py) for its objects. None for `spec` ends it."""
    _N.reset()
    _N.offer(spec)
    LEASES.held.clear()
    if machine is not None:
        LEASES.use(machine)
    else:
        LEASES.machine = _machine


class LocalPins:
    """The `pins` permission's verbs over the lease table: `write(n, v)` and
    `read(n)`, the browser's GpioLink's shape (`ws.gpio`). A pin is taken for
    the calling process at its first use, an output by `write`, an input by
    `read`; a pin another process holds, or none of the gpio row, answers
    False and None."""

    def __init__(self, leases):
        self.leases = leases

    def _pin(self, n, out):
        pid = _pid()
        name = "gpio%d" % int(n)
        p = self.leases.of(pid, name)
        if p is None:
            try:
                p = self.leases.take(pid, name, {"mode": Pin.OUT if out else Pin.IN}, _N.PINS)
            except OSError:
                return None
            p._out = out
        elif out and not getattr(p, "_out", False):
            p.init(Pin.OUT)
            p._out = True
        return p

    def write(self, n, v):
        p = self._pin(n, True)
        if p is None:
            return False
        p.value(1 if v else 0)
        return True

    def read(self, n):
        p = self._pin(n, False)
        return None if p is None else p.value()


def local_pins():
    """The `pins` verbs' service on a board whose gpio row has pins, else
    None (the console then has no `pin_write`)."""
    if LEASES.machine is None:
        return None
    for o in _N.offers():
        if o[1] == _N.GPIO and o[4]:
            return LocalPins(LEASES)
    return None
