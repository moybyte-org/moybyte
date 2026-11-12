# Map (grep -n a name to jump there):
#   open                     a handle by name and config: a lease or a device API
#   close                    a handle given back; sweep closes a process's rest
#   read                     what waits on a handle
#   write                    bytes to a handle
#   xfer                     a write, then a read, as one transfer
#   set                      a handle configured again
#   get                      one number from a handle
#   sweep                    a process's end closes every handle it held
#   words                    a cfg string of `key=value` words as a dict
#   _Handle                  the base every handle's verbs default from
#   _Camera                  camera: a picture per read
#   _Net                     net: the radio link's messages on a port
"""Leases and device APIs by handle: what a compiled program (a wasm app, a
WASI program, an ELF module) and a Lua program hold where a Python program
holds an object
(docs/os_2026-11/hardware.md section 4). The rows are native/os_calls.json's
`moybyte.dev_*`. The kernel's handle table serves every lease -- gpio<n>,
pins, uart<n>, spi<n>, i2c, pwm<n> and adc<n> -- and the device APIs lora,
mic, midi, http, usb, ble and media on the calling program's own task
(native/moy_kernel/moy_dev.h, which says what they answer); the other device
APIs are served here, by the door's Python half as the calling process
(runtime/shell/shell_servers.py, ConsoleCalls), on the VM's task:

  open(name, cfg) -> h      a device API ("camera", "net"),
                            configured by `cfg`
  close(h)                  given back; the process's end closes the rest
  read(h, into) -> n        what waits, 0 when nothing does
  write(h, bytes) -> n      what was sent
  xfer(h, bytes, into) -> n a write, then a read, as one transfer
  set(h, cfg) -> 0          configured again
  get(h, what) -> int       one number

`cfg` is words `key=value`, a value a number (0x.. too) or a name; an
address, a UUID, a name and bytes (in hex) stay text. What each handle
answers:

  camera    read: the latest frame, w * h RGB565 pixels big-endian (the
            glass's byte order), whole (FULL where `into` cannot hold it),
            0 bytes before the first; get w, h
  net       the console-to-console radio link (docs/netplay_v1.md, Ports),
            one port a handle: cfg port=1..65535 (required; port 0 is the
            game's net.send and on_net) and from=any|match (any by
            default: every console in range, as a datagram socket hears;
            match: only the live match's peer, nothing outside a match);
            a port another process holds is BUSY; write: one message, at
            most 240 bytes, to every console in range on the handle's port
            (BAD for more, IO where the radio refused it); read: the
            messages heard on it, whole records only (FULL where the next
            does not fit, which waits): the sender's MAC (6 bytes), the
            port (u16) and n (u16), then n bytes; get port, drops (messages
            the port's queue, four deep, dropped while full), peers
            (consoles heard). A process holds NET_PORTS ports at most (FULL
            past them), and the console MOY_MATCH_PORTS (FULL too); the
            radio stays up while any port is open

A handle is its process's: another process's number is ABSENT. Each answer
that fails is moy_app.h's code, negated: DENIED without the permission the
name needs (a device's own, `multiplayer` for net), BUSY where another
process holds the device (a Python program's object, or another compiled
program's handle; a process opens camera once), BAD for a name, a
key or a value this console does not take, ABSENT for a handle not open, a
device this console lacks and a radio verb that needs a connection or a
join, FULL where `into` cannot hold one record or a table has no room, IO
where the driver failed.
The process's end (moybyte.ENDS) closes every handle it left open, before
the leases' sweep and the device APIs' run.

Ring records are read through their handle, on every tier: a compiled program
polls its handles where it polls its messages (in `_update`, or on a timer
of its own), so a device's records never ride the event queue, whose
messages are text for the console's events. Python's callbacks (`irq`, a
face's `on`) stay Python's."""

import moybyte as _moybyte

# moy_app.h's codes, negated: what a row answers when it fails.
STALE, FULL, NOMEM, DENIED, BAD, ABSENT, IO, BUSY = -1, -2, -3, -4, -7, -8, -6, -11

EPERM, ENOENT, EIO, EBADF, ENOMEM, EACCES, EBUSY, ENODEV, EINVAL, ENOSPC = (
    1, 2, 5, 9, 12, 13, 16, 19, 22, 28)
ENOTCONN = 107              # the radios' drivers' (moy_gatt.h, moy_lora.h)
_CODE = {EPERM: DENIED, EACCES: DENIED, EBUSY: BUSY, EINVAL: BAD, ENODEV: ABSENT,
         ENOENT: ABSENT, EBADF: ABSENT, ENOMEM: NOMEM, ENOSPC: FULL, ENOTCONN: ABSENT}

HANDLES = {}                # pid -> {h: its handle}
_NEXT = [1]


def _code(exc):
    if isinstance(exc, OSError):
        e = exc.args[0] if exc.args and isinstance(exc.args[0], int) else EIO
        return _CODE.get(e, IO)
    return BAD


def _face(name):
    import devices
    f = devices.api(name)
    if f is None:
        raise OSError(ENODEV, "this console has no %s" % name)
    return f


def _value(v):
    try:
        return int(v, 0)
    except ValueError:
        return v


# Keys whose value stays text: an address, a UUID, a name, bytes in hex.
_TEXT = ("connect", "find", "advertise", "data")


def words(cfg):
    """`cfg`'s words as {key: value}: a value a number where it reads as one."""
    out = {}
    for w in (cfg or "").split():
        k, eq, v = w.partition("=")
        if not eq or not k:
            raise OSError(EINVAL, "a cfg word is key=value: %r" % w)
        out[k] = v if k in _TEXT else _value(v)
    return out


def _int(v, lo, hi, what):
    if not isinstance(v, int) or not lo <= v <= hi:
        raise OSError(EINVAL, "%s=%r" % (what, v))
    return v


def _only(cfg, keys):
    for k in cfg:
        if k not in keys:
            raise OSError(EINVAL, "no cfg key %r here" % k)
    return cfg


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


class _Camera(_Handle):
    def __init__(self, cfg):
        _only(cfg, ())
        self.f = _face("camera")
        self.f.open()
        w, h = self.f.size()
        self.buf = bytearray(w * h * 2)

    def read(self, cap):
        n = self.f.capture(self.buf)
        if n > cap:
            return FULL
        return bytes(self.buf[:n])

    def get(self, what):
        if what == "w":
            return self.f.size()[0]
        if what == "h":
            return self.f.size()[1]
        raise OSError(EINVAL, "no %s" % what)

    def close(self, pid):
        self.f.sweep(pid)


NET_HEAD = 10               # a net record's head: the sender (6 bytes), port, n (u16)
NET_PORTS = 4               # the ports a process holds at once
_NET_CODE = {-1: EINVAL, -2: ENOSPC, -3: EBUSY, -4: EBADF, -5: EIO, -6: EBUSY}


def _link():
    import devices
    ws = devices.console
    link = getattr(ws, "link", None)
    if link is None:
        raise OSError(ENODEV, "this console has no radio link")
    return ws, link


def _net_rc(rc):
    if rc < 0:
        raise OSError(_NET_CODE.get(rc, EIO), "net %d" % rc)
    return rc


class _Net(_Handle):
    """One port of the radio link (players.EspNowLink's ports), the
    process's radio hold ("net") taken at its first port and let go at its
    last."""

    def __init__(self, pid, cfg):
        _only(cfg, ("port", "from"))
        port = _int(cfg.get("port"), 1, 0xFFFF, "port")
        frm = {"any": 0, "match": 1}.get(cfg.get("from", "any"))
        if frm is None:
            raise OSError(EINVAL, "from=%r" % (cfg.get("from"),))
        if sum(1 for x in HANDLES.get(pid, {}).values() if type(x) is _Net) >= NET_PORTS:
            raise OSError(ENOSPC, "a process holds %d ports" % NET_PORTS)
        ws, self.link = _link()
        self.port = port
        hold = getattr(ws, "wifi_hold", None)
        if hold is not None:
            hold("net", pid)
        try:
            _net_rc(self.link.open_port(port, frm))
        except BaseException:
            self._let_go(ws, pid)
            raise

    def write(self, data):
        if len(data) > 240:
            raise OSError(EINVAL, "a message is at most 240 bytes")
        _net_rc(self.link.port_send(self.port, data))
        return len(data)

    def read(self, cap):
        out = bytearray()
        head = self.port.to_bytes(2, "little")
        while True:
            room = cap - len(out) - NET_HEAD
            r = self.link.port_take(self.port, room) if room >= 0 else None
            if r is None or r == -2:
                break
            if isinstance(r, int):
                _net_rc(r)
            mac, body = r
            out += mac + head + len(body).to_bytes(2, "little") + body
        if not out and (r == -2 or (r is None and self.link.port_waiting(self.port) > 0)):
            return FULL
        return bytes(out)

    def get(self, what):
        if what == "port":
            return self.port
        if what == "drops":
            return _net_rc(self.link.port_drops(self.port))
        if what == "sent":
            return _net_rc(self.link.port_sent(self.port))
        if what == "refused":
            return _net_rc(self.link.port_refused(self.port))
        if what == "peers":
            return len(self.link.peers)
        raise OSError(EINVAL, "no %s" % what)

    def _let_go(self, ws, pid):
        if not any(type(x) is _Net for x in HANDLES.get(pid, {}).values()):
            release = getattr(ws, "wifi_release", None)
            if release is not None:
                release("net", pid)

    def close(self, pid):
        self.link.close_port(self.port)
        self._let_go(_link()[0], pid)


_DEVICES = {"camera": _Camera, "net": _Net}
# A device with one holder: a process opens it once.
_ONE = ("camera",)
# The permissions a device needs, one of them, where they are not its name.
_PERMS = {"net": ("multiplayer",)}


def _need(pid, name):
    import devices
    perms = _PERMS.get(name, (name,))
    for perm in perms:
        if devices.may(pid, perm):
            return
    raise OSError(EPERM, 'needs "%s"' % perms[0])


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
            raise OSError(EINVAL, "no device %s" % name)
        _need(pid, name)
        if name in _ONE and any(type(x) is dev for x in HANDLES.get(pid, {}).values()):
            raise OSError(EBUSY, "%s is open" % name)
        hd = dev(pid, c) if dev is _Net else dev(c)
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
