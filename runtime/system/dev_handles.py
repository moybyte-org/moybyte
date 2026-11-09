"""Leases and device APIs by handle: what a compiled program (a wasm app, a
WASI program, an ELF module) holds where a Python program holds an object
(docs/os_2026-11/hardware.md section 4). The rows are native/os_calls.json's
`moybyte.dev_*`, served by the door's Python half as the calling process
(runtime/shell/shell_servers.py, ConsoleCalls):

  open(name, cfg) -> h      a lease of row `name` (leases.py's names:
                            "gpio<n>", "uart<n>", "spi<n>", "pwm<n>",
                            "adc<n>", "i2c") or a device API ("http", "lora",
                            "mic"), configured by `cfg`
  close(h)                  given back; the process's end closes the rest
  read(h, into) -> n        what waits, 0 when nothing does
  write(h, bytes) -> n      what was sent
  xfer(h, bytes, into) -> n a write, then a read, as one transfer
  set(h, cfg) -> 0          configured again
  get(h, what) -> int       one number

`cfg` is words `key=value`, a value a number (0x.. too) or a name. What each
handle answers:

  gpio<n>   cfg mode=in|out|od pull=up|down|none value=0|1; set the same and
            edges=rising|falling|both|off; get value; read: the edges since
            the last read, 8 bytes each (the stamp, microseconds wrapping at
            2^30, and the level after it, each a little-endian uint32)
  uart<n>   cfg baudrate bits parity=none|even|odd stop tx rx timeout rxbuf
            txbuf; read: the bytes waiting; write; get any
  spi<n>    cfg baudrate polarity phase bits firstbit=msb|lsb sck mosi miso;
            read: n bytes clocked in, 0 clocked out; write; xfer: full duplex,
            as many bytes in as out (FULL where `into` is shorter)
  pwm<n>    cfg freq duty (0..65535); set the same; get freq, duty
  adc<n>    get value (0..65535)
  i2c       cfg scl sda (required) freq addr; set addr; read: n bytes from
            the device at addr; write: to it; xfer: a write with no stop,
            then a read (a register read); a device that does not answer is
            ABSENT
  http      cfg url (required) cap (a body kept up to cap bytes) or window
            (a body of any length through a window of that many bytes); read:
            the body's next bytes, 0 while it runs and at its end; get status
            (0 while it runs, the HTTP status, IO where it got no response),
            errno (why), length (a kept body's), ended (a window's)
  lora      cfg freq sf bw cr power sync preamble listen=0|1 (listening by
            default); set the same; write: a packet out; read: the next packet
            in; get rssi, snr (dB x 4) of the packet read last
  mic       read: PCM16 mono samples, little-endian, oldest first; get rate,
            drops

A handle is its process's: another process's number is ABSENT. Each answer
that fails is moy_app.h's code, negated: DENIED without the permission the
name needs (leases.perm_of's `lease:<row>`, a device's own), BUSY where
another process holds the lease or the device (a Python program's object, or
another compiled program's handle), BAD for a name, a key or a value this
console does not take, ABSENT for a handle not open and a device this console
lacks, FULL where `into` cannot hold one record, IO where the driver failed.
The process's end (moybyte.ENDS) closes every handle it left open, before the
leases' sweep and the device APIs' run.

Ring records are read through their handle, on every tier: a compiled program
polls its handles where it polls its messages (in `_update`, or on a timer
of its own), so a device's records never ride the event queue, whose
messages are text for the console's events. Python's callbacks (`irq`, a
face's `on`) stay Python's."""

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package
    from runtime import moybyte as _moybyte

try:
    from array import array as _array
except ImportError:  # pragma: no cover
    _array = None

# moy_app.h's codes, negated: what a row answers when it fails.
STALE, FULL, NOMEM, DENIED, BAD, ABSENT, IO, BUSY = -1, -2, -3, -4, -7, -8, -6, -11

EPERM, ENOENT, EIO, EBADF, ENOMEM, EACCES, EBUSY, ENODEV, EINVAL, ENOSPC = (
    1, 2, 5, 9, 12, 13, 16, 19, 22, 28)
_CODE = {EPERM: DENIED, EACCES: DENIED, EBUSY: BUSY, EINVAL: BAD, ENODEV: ABSENT,
         ENOENT: ABSENT, EBADF: ABSENT, ENOMEM: NOMEM, ENOSPC: FULL}

EDGE_DEPTH = 64
EDGE_BYTES = 8
_BATCH = 8                  # edge records a read takes from the ring at a time

HANDLES = {}                # pid -> {h: its handle}
_NEXT = [1]


def _code(exc):
    if isinstance(exc, OSError):
        e = exc.args[0] if exc.args and isinstance(exc.args[0], int) else EIO
        return _CODE.get(e, IO)
    return BAD


def _leases():
    try:
        import leases
    except ImportError:  # host: the runtime package
        from runtime import leases
    return leases


def _face(name):
    try:
        import devices
    except ImportError:  # host: the runtime package
        from runtime import devices
    f = devices.api(name)
    if f is None:
        raise OSError(ENODEV, "this console has no %s" % name)
    return f


def _value(v):
    try:
        return int(v, 0)
    except ValueError:
        return v


def words(cfg):
    """`cfg`'s words as {key: value}: a value a number where it reads as one."""
    out = {}
    for w in (cfg or "").split():
        k, eq, v = w.partition("=")
        if not eq or not k:
            raise OSError(EINVAL, "a cfg word is key=value: %r" % w)
        out[k] = _value(v)
    return out


def _only(cfg, keys):
    for k in cfg:
        if k not in keys:
            raise OSError(EINVAL, "no cfg key %r here" % k)
    return cfg


def _named(cfg, key, names):
    if key in cfg:
        v = cfg[key]
        if v not in names:
            raise OSError(EINVAL, "%s=%r" % (key, v))
        cfg[key] = names[v]


class _Handle:
    """What every handle answers where its kind does not."""

    def read(self, cap):
        raise OSError(EINVAL, "no read")

    def write(self, data):
        raise OSError(EINVAL, "no write")

    def xfer(self, data, cap):
        raise OSError(EINVAL, "no xfer")

    def set(self, cfg):
        raise OSError(EINVAL, "no set")

    def get(self, what):
        raise OSError(EINVAL, "no %s" % what)

    def close(self, pid):
        pass


class _Lease(_Handle):
    def __init__(self, obj):
        self.obj = obj

    def close(self, pid):
        _leases().LEASES.drop(self.obj)


class _Gpio(_Lease):
    def __init__(self, obj):
        _Lease.__init__(self, obj)
        self.reader = -1
        self.trigger = 0
        self.buf = None

    @staticmethod
    def cfg(cfg):
        P = _leases().Pin
        _only(cfg, ("mode", "pull", "value"))
        _named(cfg, "mode", {"in": P.IN, "out": P.OUT, "od": P.OPEN_DRAIN})
        _named(cfg, "pull", {"up": P.PULL_UP, "down": P.PULL_DOWN, "none": None})
        return cfg

    def set(self, cfg):
        edges = cfg.pop("edges", None)
        if edges is not None and edges not in ("rising", "falling", "both", "off"):
            raise OSError(EINVAL, "edges=%r" % edges)
        cfg = self.cfg(cfg)
        v = cfg.pop("value", None)
        if cfg:
            self.obj.init(cfg.get("mode", -1), cfg.get("pull", -1))
        if v is not None:
            self.obj.value(1 if v else 0)
        if edges is not None:
            self._edges_off()
            if edges != "off":
                self._edges_on({"rising": 1, "falling": 2, "both": 3}[edges])
        return 0

    def get(self, what):
        if what != "value":
            raise OSError(EINVAL, "no %s" % what)
        return self.obj.value()

    def _edges_on(self, trigger):
        L = _leases()
        t = _moybyte.WAKES.table
        if t is None:
            raise OSError(ENODEV, "no input table")
        src = L._N.edge(self.obj._h, self.obj.pin, EDGE_DEPTH, False)
        try:
            self.reader = t.reader(1 << _moybyte.EDGE, src)
        except BaseException:
            L._N.edge_close(self.obj._h, self.obj.pin)
            raise
        self.trigger = trigger
        self.buf = _array("i", [0] * (_moybyte.EV_FIELDS * _BATCH))

    def _edges_off(self):
        if self.reader < 0:
            return
        t = _moybyte.WAKES.table
        if t is not None:
            t.reader_free(self.reader)
        self.reader = -1
        N = _leases()._N
        if N.live(self.obj._h):
            try:
                N.edge_close(self.obj._h, self.obj.pin)
            except OSError:
                pass

    def read(self, cap):
        if self.reader < 0:
            return b""
        if cap < EDGE_BYTES:
            return FULL
        t = _moybyte.WAKES.table
        F, T, K, B = _moybyte.EV_FIELDS, _moybyte.EV_T, _moybyte.EV_KIND, _moybyte.EV_B
        out = bytearray()
        want = cap // EDGE_BYTES
        while len(out) // EDGE_BYTES < want:
            n = t.events(self.buf, self.reader) if t is not None else 0
            for i in range(n):
                k = i * F
                if self.buf[k + K] != _moybyte.EDGE:
                    continue
                level = 1 if self.buf[k + B] else 0
                if self.trigger & (1 if level else 2):
                    out += (self.buf[k + T] & 0xFFFFFFFF).to_bytes(4, "little")
                    out += level.to_bytes(4, "little")
            if n < _BATCH:
                break
        # a batch read may hold more than fits: what is past `cap` is lost,
        # as a ring's overrun is (`drops`), so ask with room for a batch
        return bytes(out[:want * EDGE_BYTES])

    def close(self, pid):
        self._edges_off()
        _Lease.close(self, pid)


class _Uart(_Lease):
    @staticmethod
    def cfg(cfg):
        _only(cfg, ("baudrate", "bits", "parity", "stop", "tx", "rx", "timeout", "rxbuf",
                    "txbuf"))
        _named(cfg, "parity", {"none": None, "even": 0, "odd": 1})
        return cfg

    def read(self, cap):
        n = min(cap, self.obj.any())
        return (self.obj.read(n) or b"") if n > 0 else b""

    def write(self, data):
        return self.obj.write(data) or 0

    def set(self, cfg):
        self.obj.init(**self.cfg(cfg))
        return 0

    def get(self, what):
        if what != "any":
            raise OSError(EINVAL, "no %s" % what)
        return self.obj.any()


class _Spi(_Lease):
    @staticmethod
    def cfg(cfg):
        _only(cfg, ("baudrate", "polarity", "phase", "bits", "firstbit", "sck", "mosi", "miso"))
        _named(cfg, "firstbit", {"msb": 0, "lsb": 1})
        return cfg

    def read(self, cap):
        return self.obj.read(cap, 0) if cap > 0 else b""

    def write(self, data):
        self.obj.write(data)
        return len(data)

    def xfer(self, data, cap):
        if cap < len(data):
            return FULL
        into = bytearray(len(data))
        self.obj.write_readinto(data, into)
        return bytes(into)

    def set(self, cfg):
        cfg = self.cfg(cfg)
        for k in ("sck", "mosi", "miso"):
            if k in cfg:
                raise OSError(EINVAL, "a bus's pins are its open's")
        self.obj.init(**cfg)
        return 0


class _Pwm(_Lease):
    @staticmethod
    def cfg(cfg):
        _only(cfg, ("freq", "duty"))
        if "duty" in cfg:
            cfg["duty_u16"] = cfg.pop("duty")
        return cfg

    def set(self, cfg):
        cfg = self.cfg(cfg)
        if "freq" in cfg:
            self.obj.freq(cfg["freq"])
        if "duty_u16" in cfg:
            self.obj.duty_u16(cfg["duty_u16"])
        return 0

    def get(self, what):
        if what == "freq":
            return self.obj.freq()
        if what == "duty":
            return self.obj.duty_u16()
        raise OSError(EINVAL, "no %s" % what)


class _Adc(_Lease):
    @staticmethod
    def cfg(cfg):
        return _only(cfg, ())

    def get(self, what):
        if what != "value":
            raise OSError(EINVAL, "no %s" % what)
        return self.obj.read_u16()


class _I2c(_Lease):
    def __init__(self, obj, addr):
        _Lease.__init__(self, obj)
        self.addr = addr

    @staticmethod
    def cfg(cfg):
        return _only(cfg, ("scl", "sda", "freq"))

    def _at(self):
        if self.addr is None:
            raise OSError(EINVAL, "set addr first")
        return self.addr

    def set(self, cfg):
        _only(cfg, ("addr",))
        if "addr" in cfg:
            a = cfg["addr"]
            if not isinstance(a, int) or not 0 <= a < 128:
                raise OSError(EINVAL, "addr=%r" % a)
            self.addr = a
        return 0

    def get(self, what):
        if what != "addr":
            raise OSError(EINVAL, "no %s" % what)
        return -1 if self.addr is None else self.addr

    def read(self, cap):
        return self.obj.readfrom(self._at(), cap) if cap > 0 else b""

    def write(self, data):
        self.obj.writeto(self._at(), data)
        return len(data)

    def xfer(self, data, cap):
        a = self._at()
        self.obj.writeto(a, data, False)
        return self.obj.readfrom(a, cap) if cap > 0 else b""


_LEASES = {"gpio": _Gpio, "uart": _Uart, "spi": _Spi, "pwm": _Pwm, "adc": _Adc}


def _lease(pid, name, cfg):
    L = _leases()
    kind = name.rstrip("0123456789")
    if name == "i2c":
        addr = cfg.pop("addr", None)
        obj = L.LEASES.take(pid, name, _I2c.cfg(cfg))
        hd = _I2c(obj, None)
        if addr is not None:
            try:
                hd.set({"addr": addr})
            except OSError:
                L.LEASES.drop(obj)
                raise
        return hd
    cls = _LEASES.get(kind)
    if cls is None or kind == name:
        raise OSError(EINVAL, "no lease %s" % name)
    return cls(L.LEASES.take(pid, name, cls.cfg(cfg)))


class _Http(_Handle):
    def __init__(self, cfg):
        _only(cfg, ("url", "cap", "window"))
        url = cfg.get("url")
        if not isinstance(url, str):
            raise OSError(EINVAL, "an http handle names its url")
        f = _face("http")
        self.f = f
        if "window" in cfg:
            self.req = f.stream(url, cfg["window"])
            self.stream = True
        else:
            self.req = f.get(url, cfg.get("cap", 16384))
            self.stream = False
        self.at = 0
        self.err = 0

    def _state(self):
        if self.err:
            return None
        try:
            return self.req.state()
        except OSError as exc:
            self.err = exc.args[0] if exc.args and isinstance(exc.args[0], int) else EIO
            return None

    def read(self, cap):
        if cap <= 0 or self._state() is None:
            return b""
        buf = bytearray(cap)
        if self.stream:
            n = self.req.readinto(buf)
        else:
            n = self.req.readinto(buf, self.at)
            self.at += n
        return bytes(buf[:n])

    def get(self, what):
        st = self._state()
        if what == "status":
            return IO if self.err else 0 if st is None else st[0]
        if what == "errno":
            return self.err
        if what == "length":
            return 0 if st is None or st[1] is None else st[1]
        if what == "ended":
            return int(self.stream and st is not None and self.req.ended())
        raise OSError(EINVAL, "no %s" % what)

    def close(self, pid):
        self.req.close()


_LORA_CFG = ("freq", "sf", "bw", "cr", "power", "sync", "preamble")


class _Lora(_Handle):
    def __init__(self, cfg):
        self.f = _face("lora")
        self.meta = _array("i", [0, 0, 0])
        self.last = (0, 0)
        cfg.setdefault("listen", 1)
        self.set(cfg)

    def set(self, cfg):
        _only(cfg, _LORA_CFG + ("listen",))
        listen = cfg.pop("listen", None)
        if cfg:
            self.f.config(**cfg)
        if listen is not None:
            self.f.listen(bool(listen))
        return 0

    def write(self, data):
        self.f.send(data)
        return len(data)

    def read(self, cap):
        buf = bytearray(max(cap, 0))
        n = self.f.recv(buf, self.meta)
        if n:
            self.last = (self.meta[0], self.meta[1])
        return bytes(buf[:n])

    def get(self, what):
        if what == "rssi":
            return self.last[0]
        if what == "snr":
            return self.last[1]
        raise OSError(EINVAL, "no %s" % what)

    def close(self, pid):
        self.f.sweep(pid)


class _Mic(_Handle):
    def __init__(self, cfg):
        _only(cfg, ())
        self.f = _face("mic")
        self.f.open()

    def read(self, cap):
        buf = bytearray(cap & ~1)
        n = self.f.read(buf) if buf else 0
        return bytes(buf[:2 * n])

    def get(self, what):
        if what == "rate":
            return self.f.rate() or 0
        if what == "drops":
            return self.f.drops()
        raise OSError(EINVAL, "no %s" % what)

    def close(self, pid):
        self.f.sweep(pid)


_DEVICES = {"http": _Http, "lora": _Lora, "mic": _Mic}
# A device with one holder: a process opens it once.
_ONE = ("lora", "mic")


def _need(pid, perm):
    try:
        import devices
    except ImportError:  # host: the runtime package
        from runtime import devices
    if not devices.may(pid, perm):
        raise OSError(EPERM, 'needs "%s"' % perm)


def _mine(pid, h):
    hd = HANDLES.get(pid, {}).get(h)
    if hd is None:
        raise OSError(EBADF, "no handle %d" % h)
    return hd


def _run(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- every refusal is a code
        return _code(exc)


def open(pid, name, cfg=None):  # noqa: A001 -- the row's verb
    """Handle `h` > 0 on `name` for process `pid`, or a code."""
    def go():
        c = words(cfg)
        dev = _DEVICES.get(name)
        if dev is None:
            hd = _lease(pid, name, c)
        else:
            _need(pid, name)
            if name in _ONE and any(type(x) is dev for x in HANDLES.get(pid, {}).values()):
                raise OSError(EBUSY, "%s is open" % name)
            hd = dev(c)
        h = _NEXT[0]
        _NEXT[0] += 1
        HANDLES.setdefault(pid, {})[h] = hd
        return h
    return _run(go)


def close(pid, h):
    def go():
        hd = _mine(pid, h)
        del HANDLES[pid][h]
        hd.close(pid)
        return 0
    return _run(go)


def read(pid, h, cap):
    return _run(lambda: _mine(pid, h).read(cap))


def write(pid, h, data):
    return _run(lambda: _mine(pid, h).write(bytes(data)))


def xfer(pid, h, data, cap):
    return _run(lambda: _mine(pid, h).xfer(bytes(data), cap))


def set(pid, h, cfg):  # noqa: A001 -- the row's verb
    return _run(lambda: _mine(pid, h).set(words(cfg)))


def get(pid, h, what):
    return _run(lambda: int(_mine(pid, h).get(what)))


def sweep(pid):
    """Process `pid` ended: every handle it left open closed."""
    held = HANDLES.pop(pid, None)
    for hd in (held or {}).values():
        try:
            hd.close(pid)
        except Exception:  # noqa: BLE001 -- a sweep finishes
            pass
    return len(held or ())


_moybyte.ENDS.insert(0, sweep)
