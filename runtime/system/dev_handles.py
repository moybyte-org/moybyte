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
#   _Http                    http: a fetch's body, kept or windowed
#   _Ring                    the base of the event-ring devices
#   _Ble                     ble: GATT by handle
#   _Usb                     usb: the USB host
#   _Midi                    midi: the MIDI ports
#   _Camera                  camera: a picture per read
#   _media_roots             the folders a process's media handle plays from
#   _Media                   media: the media service's stream by handle
#   _Net                     net: the radio link's messages on a port
"""Leases and device APIs by handle: what a compiled program (a wasm app, a
WASI program, an ELF module) holds where a Python program holds an object
(docs/os_2026-11/hardware.md section 4). The rows are native/os_calls.json's
`moybyte.dev_*`. The kernel's handle table serves every lease -- gpio<n>,
pins, uart<n>, spi<n>, i2c, pwm<n> and adc<n> -- and the device APIs lora
and mic on the calling program's own task (native/moy_kernel/moy_dev.h,
which says what they answer); the other device APIs are served here, by the door's Python half as the calling
process (runtime/shell/shell_servers.py, ConsoleCalls), on the VM's task:

  open(name, cfg) -> h      a device API ("http", "ble", "usb", "midi",
                            "camera", "media", "net"),
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

  http      cfg url (required) cap (a body kept up to cap bytes) or window
            (a body of any length through a window of that many bytes); read:
            the body's next bytes, 0 while it runs and at its end; get status
            (0 while it runs, the HTTP status, IO where it got no response),
            errno (why), length (a kept body's), ended (a window's)
  ble       set: one action a set, its outcome a record --
            scan=<ms>, connect=aa:bb:cc:dd:ee:ff | off, find=<uuid> (16-bit
            hex, 0x.. or not, or the 128-bit string), read=<handle>,
            subscribe=<handle>, unsubscribe=<handle>, advertise=<name> |
            off (with data=<hex>, the manufacturer data, and interval=<ms>);
            write=<handle> (with response=0|1) names where a write goes;
            write: to it; get connected; read: the BLE face's events since
            the last read, whole records only (FULL where the next does not
            fit, which waits): a 12-byte head, the event (u16, moy_gatt.h's
            MOY_GATT_EV_*: 1 ADV, 2 SCAN_DONE, 3 CONNECTED, 4 DISCONNECTED,
            5 FOUND, 6 READ, 7 WRITTEN, 8 NOTIFY, 9 ADVERTISING), n (u16),
            a and b (i32, the driver's: a status is 0 or -errno), then n
            bytes -- ADV's the address (6 bytes, as printed) and the name
            (a the device's index, b its RSSI), READ's the value read,
            NOTIFY's the notification's (a its handle, b its length)
  usb       the USB host's devices (a device is its bus address, 1..127):
            set serial=<addr> (baud=<n>, 115200 by default) opens a CDC-ACM
            device's line, write=<addr> names where a write goes (a serial
            or a USB-MIDI device); write: to it; read: the USB face's
            events, whole records as ble's are (FULL where the next does
            not fit): the event (moy_usb.h's MOY_USB_EV_*: 1 ATTACH, 2
            DETACH, 3 REPORT, 4 DATA, 5 SENT), n, a the device's address
            and b the driver's value (ATTACH's its class, 1 HID, 2 CDC, 3
            MIDI, 0 OTHER; REPORT's the report's length; DATA's the bytes
            that waited; SENT's 0 or -errno), then n bytes -- ATTACH's the
            class and the kind (u8 each: a HID boot keyboard 1, mouse 2),
            vid and pid (u16) and the name, REPORT's the HID report,
            DATA's the bytes the device sent. The open reads as an ATTACH
            for each device already plugged in
  midi      one port a handle, named by the open's cfg: uart=<row> (baud=<n>,
            31250 by default; the row's `lease:<row>` too), usb=<addr> (a
            USB-MIDI device, as a usb handle's ATTACH names it) or
            ble=aa:bb:cc:dd:ee:ff (a BLE-MIDI peripheral: connected, its
            MIDI characteristic found and subscribed); write: MIDI bytes,
            whole messages (no system exclusive), to it; read: the messages
            it sent, 3 bytes each (status, data1, data2: 0 where the
            message has fewer), whole messages only (FULL where one does
            not fit); get port (the driver's), state (0 while a BLE port
            connects, 1 once messages flow, 2 failed or gone); a write to a
            BLE port before it is ready is ABSENT. A process holds the
            `midi` API from its first port to its last
  camera    read: the latest frame, w * h RGB565 pixels big-endian (the
            glass's byte order), whole (FULL where `into` cannot hold it),
            0 bytes before the first; get w, h
  media     a client of the media service's one stream, the system's
            (docs/os_2026-11/hardware.md section 3): write: play the source
            the bytes name (UTF-8): a file, an .m3u or .pls playlist
            (`media`) or an http(s):// URL (`media:url` and `http`), in
            place of what plays, whoever played it, DENIED
            for a file outside its cart's folder and the folder its
            `files` permission names (or a playlist naming one), there or
            not, ABSENT for a file not there, BAD for https where nothing
            verifies it, FULL where THE SHARES' MEDIA row is;
            set volume=0..100, pause=1|0, seek=<seconds>, skip=next|prev,
            stop=1, applied in that order (ABSENT with no stream, a seek a
            stream or an unknown length cannot take, a skip past the list's
            end); get state (0 idle,
            1 opening, 2 playing, 3 ended, 4 failed), paused, entry,
            entries, rate, channels, kbps, volume, err, at and len (seconds
            heard and long, a file's), stamp (moves when what these show
            does), mine (1 where this process's write set what plays), tls (1
            where an https URL plays); read: the now-playing text, four
            UTF-8 lines -- title, artist, station, src -- whole (FULL where
            it does not fit), 0 bytes with no stream; xfer: a file's probe,
            its path in, its length in seconds (u32) then its title and
            artist on two lines out, DENIED where it may not play the file,
            ABSENT where it reads none. Its close and its process's end
            leave the stream playing
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
name needs (a device's own, `media` or `media:url` for media, and a midi
port's UART its `lease:<row>`), BUSY where another process holds the
device (a Python program's object, or another compiled program's handle; a
process opens every device API but http and midi once), BAD for a name, a
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
ENOTCONN = 107              # the radios' drivers' (moy_gatt.h, moy_lora.h)
EPROTONOSUPPORT = 93        # the media service's https where nothing verifies
_CODE = {EPERM: DENIED, EACCES: DENIED, EBUSY: BUSY, EINVAL: BAD, ENODEV: ABSENT,
         ENOENT: ABSENT, EBADF: ABSENT, ENOMEM: NOMEM, ENOSPC: FULL, ENOTCONN: ABSENT,
         EPROTONOSUPPORT: BAD}

_BATCH = 8                  # events a read takes from the input table at a time

HANDLES = {}                # pid -> {h: its handle}
_NEXT = [1]


def _code(exc):
    if isinstance(exc, OSError):
        e = exc.args[0] if exc.args and isinstance(exc.args[0], int) else EIO
        return _CODE.get(e, IO)
    return BAD


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


def _hex(v, n=None):
    try:
        import binascii
    except ImportError:  # pragma: no cover
        import ubinascii as binascii
    if v.startswith("0x"):
        v = v[2:]
    try:
        b = binascii.unhexlify(v)
    except (ValueError, TypeError):
        raise OSError(EINVAL, "not hex: %r" % v)
    if n is not None and len(b) != n:
        raise OSError(EINVAL, "%r is not %d bytes" % (v, n))
    return b


def _int(v, lo, hi, what):
    if not isinstance(v, int) or not lo <= v <= hi:
        raise OSError(EINVAL, "%s=%r" % (what, v))
    return v


def _u32(x):
    return (x & 0xFFFFFFFF).to_bytes(4, "little")


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


HEAD = 12                   # a record's head: event, n (u16), a, b (i32)
BLE_HEAD = HEAD


def _record(ev, a, b, body=b""):
    return (ev.to_bytes(2, "little") + len(body).to_bytes(2, "little") + _u32(a) + _u32(b)
            + body)


class _Ring(_Handle):
    """A handle reading its driver's ring through a reader of its own (only
    its source, only its KIND), whole records only: what `_body` makes of
    each event waits in `pend` until a read has room for it."""

    KIND = 0
    reader = -1

    def _reader(self, src):
        t = _moybyte.WAKES.table
        if t is None:
            raise OSError(ENODEV, "no input table")
        self.reader = t.reader(1 << self.KIND, src)
        self.buf = _array("i", [0] * (_moybyte.EV_FIELDS * _BATCH))
        self.pend = []

    def _free(self):
        t = _moybyte.WAKES.table
        if t is not None and self.reader >= 0:
            t.reader_free(self.reader)
        self.reader = -1

    def _body(self, ev, a, b):
        return b""

    def _rec(self, ev, a, b):
        """The record event (ev, a, b) reads as, None for none."""
        return _record(ev, a, b, self._body(ev, a, b))

    def _pull(self):
        t = _moybyte.WAKES.table
        n = t.events(self.buf, self.reader) if t is not None and self.reader >= 0 else 0
        F, K, ID, A, B = (_moybyte.EV_FIELDS, _moybyte.EV_KIND, _moybyte.EV_ID, _moybyte.EV_A,
                          _moybyte.EV_B)
        for i in range(n):
            k = i * F
            if self.buf[k + K] != self.KIND:
                continue
            r = self._rec(self.buf[k + ID], self.buf[k + A], self.buf[k + B])
            if r is not None:
                self.pend.append(r)
        return n

    def read(self, cap):
        out = bytearray()
        while True:
            if not self.pend:
                if not self._pull():
                    break
                continue
            r = self.pend[0]
            if len(out) + len(r) > cap:
                break
            out += r
            self.pend.pop(0)
        if not out and self.pend:
            return FULL
        return bytes(out)


_BLE_ACTS = ("scan", "connect", "find", "read", "subscribe", "unsubscribe", "advertise")


def _gatt(v):
    return _int(v, 1, 0xFFFF, "handle")


def _addr(v):
    parts = v.split(":") if isinstance(v, str) else ()
    if len(parts) != 6 or not all(len(p) == 2 for p in parts):
        raise OSError(EINVAL, "an address is aa:bb:cc:dd:ee:ff")
    _hex("".join(parts))
    return v.lower()


def _uuid(v):
    s = v[2:] if v.startswith("0x") else v
    if len(s) <= 4:
        return int.from_bytes(_hex(("0000" + s)[-4:]), "big")
    _hex(s.replace("-", ""), 16)
    return s


class _Ble(_Ring):
    KIND = _moybyte.BLE

    def __init__(self, cfg):
        f = _face("ble")
        if _moybyte.WAKES.table is None:
            raise OSError(ENODEV, "no input table")
        f.open()
        try:
            self._reader(f.source())
        except BaseException:
            f.close()
            raise
        self.f = f
        self.target = None
        self.response = True
        if cfg:
            try:
                self.set(cfg)
            except BaseException:
                self._free()
                f.close()
                raise

    def set(self, cfg):
        _only(cfg, _BLE_ACTS + ("data", "interval", "write", "response"))
        acts = [k for k in _BLE_ACTS if k in cfg]
        if len(acts) > 1:
            raise OSError(EINVAL, "one action a set")
        if ("data" in cfg or "interval" in cfg) and "advertise" not in cfg:
            raise OSError(EINVAL, "data and interval are advertise's")
        if "write" in cfg:
            self.target = _gatt(cfg["write"])
        if "response" in cfg:
            self.response = bool(_int(cfg["response"], 0, 1, "response"))
        if not acts:
            return 0
        act, v, f = acts[0], cfg[acts[0]], self.f
        if act == "scan":
            f.scan(_int(v, 1, 60000, "scan"))
        elif act == "connect":
            if v == "off":
                f.disconnect()
            else:
                f.connect(_addr(v))
        elif act == "find":
            f.find(_uuid(v))
        elif act == "read":
            f.read(_gatt(v))
        elif act in ("subscribe", "unsubscribe"):
            f.subscribe(_gatt(v), act == "subscribe")
        elif v == "off":
            f.stop_advertising()
        else:
            f.advertise(v, _hex(cfg.get("data", "")),
                        _int(cfg.get("interval", 200), 20, 10240, "interval"))
        return 0

    def write(self, data):
        if self.target is None:
            raise OSError(EINVAL, "set write=<handle> first")
        self.f.write(self.target, data, self.response)
        return len(data)

    def get(self, what):
        if what != "connected":
            raise OSError(EINVAL, "no %s" % what)
        return int(self.f.connected())

    def _body(self, ev, a, b):
        f = self.f
        if ev == f.EV_ADV:
            seen = f.devices()
            if not 0 <= a < len(seen):
                return b""
            addr, _rssi, name = seen[a]
            return _hex(addr.replace(":", "")) + name.encode()
        if ev == f.EV_READ and b == 0 and a > 0:
            into = bytearray(a)
            return bytes(into[:f.value(into)])
        if ev == f.EV_NOTIFY:
            into = bytearray(244)                   # MOY_GATT_VALUE
            got = f.notified(into)
            return bytes(into[:got[0]]) if got else b""
        return b""

    def close(self, pid):
        self._free()
        self.f.sweep(pid)


USB_RX = 1024               # MOY_USB_RX: a device's bytes kept
USB_REPORT = 64             # MOY_USB_REPORT


class _Usb(_Ring):
    KIND = _moybyte.USB

    def __init__(self, cfg):
        f = _face("usb")
        if _moybyte.WAKES.table is None:
            raise OSError(ENODEV, "no input table")
        f.open()
        try:
            self._reader(f.source())
            self.f = f
            self.target = None
            for d in f.devices():
                self.pend.append(_record(f.EV_ATTACH, d[0], d[1], self._about(d)))
            if cfg:
                self.set(cfg)
        except BaseException:
            self._free()
            f.close()
            raise

    @staticmethod
    def _about(d):
        addr, cls, kind, vid, pid, name = d
        return bytes((cls, kind)) + vid.to_bytes(2, "little") + pid.to_bytes(2, "little") + \
            name.encode()

    def set(self, cfg):
        _only(cfg, ("serial", "baud", "write"))
        if "baud" in cfg and "serial" not in cfg:
            raise OSError(EINVAL, "baud is serial's")
        if "write" in cfg:
            self.target = _int(cfg["write"], 1, 127, "write")
        if "serial" in cfg:
            self.f.serial(_int(cfg["serial"], 1, 127, "serial"),
                          _int(cfg.get("baud", 115200), 1, 4000000, "baud"))
        return 0

    def write(self, data):
        if self.target is None:
            raise OSError(EINVAL, "set write=<addr> first")
        self.f.write(self.target, data)
        return len(data)

    def _rec(self, ev, a, b):
        f = self.f
        body = b""
        try:
            if ev == f.EV_ATTACH:
                for d in f.devices():
                    if d[0] == a:
                        body = self._about(d)
            elif ev == f.EV_REPORT:
                into = bytearray(USB_REPORT)
                body = bytes(into[:f.report(a, into)])
            elif ev == f.EV_DATA:
                into = bytearray(USB_RX)
                body = bytes(into[:f.read(a, into)])
                if not body:
                    return None             # an earlier record carried them
        except OSError:                     # gone before its record was read
            pass
        return _record(ev, a, b, body)

    def close(self, pid):
        self._free()
        self.f.sweep(pid)


MIDI_BYTES = 3              # a message read: status, data1, data2
_MIDI_STATE = {"connecting": 0, "finding": 0, "ready": 1, "failed": 2, "gone": 2}


class _Midi(_Ring):
    KIND = _moybyte.MIDI

    def __init__(self, cfg):
        f = _face("midi")
        if _moybyte.WAKES.table is None:
            raise OSError(ENODEV, "no input table")
        _only(cfg, ("uart", "baud", "usb", "ble"))
        ways = [k for k in ("uart", "usb", "ble") if k in cfg]
        if len(ways) != 1:
            raise OSError(EINVAL, "a midi handle is one port: uart=, usb= or ble=")
        if "baud" in cfg and ways[0] != "uart":
            raise OSError(EINVAL, "baud is a uart's")
        way = ways[0]
        v = cfg[way]
        try:
            self._reader(f.source())
            if way == "uart":
                if not isinstance(v, str) or not v.startswith("uart"):
                    raise OSError(EINVAL, "uart=%r" % (v,))
                self.port = f.uart(v, _int(cfg.get("baud", 31250), 1, 4000000, "baud"))
            elif way == "usb":
                self.port = f.usb(_int(v, 1, 127, "usb"))
            else:
                self.port = f.ble(_addr(v))
        except BaseException:
            self._free()
            if not f.ports():
                f.close()
            raise
        self.f = f

    def _rec(self, port, a, b):
        if port != self.port:
            return None
        return bytes((a & 0xFF, (a >> 8) & 0x7F, b & 0x7F))

    def write(self, data):
        self.f.send(self.port, data)
        return len(data)

    def get(self, what):
        if what == "port":
            return self.port
        if what == "state":
            return _MIDI_STATE.get(self.f.state(self.port), 2)
        raise OSError(EINVAL, "no %s" % what)

    def close(self, pid):
        self._free()
        if any(type(x) is _Midi for x in HANDLES.get(pid, {}).values()):
            self.f.close(self.port)
        else:
            self.f.sweep(pid)


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


_MEDIA_STATES = ("idle", "opening", "playing", "ended", "failed")
_MEDIA_NOW = ("paused", "entry", "entries", "rate", "channels", "kbps", "volume", "err", "at",
              "len")


def _text(data):
    return bytes(data).decode("utf-8")


def _norm(path):
    """`path` with its "." and ".." folded, or None where a ".." climbs past
    its root."""
    out = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not out:
                return None
            out.pop()
        else:
            out.append(part)
    return "/" + "/".join(out)


def _media_roots(pid):
    """The folders process `pid`'s media handle plays files from, as the
    media service's paths (`dev_media.where`): its cart's own folder and the
    one its `files` permission names, the folder the files role makes its
    grant for (`moy_app.policy`, by FILE_KIND_DIRS under the store's files
    root). None where the process is not gated (a bare host, the shell)."""
    try:
        import devices
        import moy_app
        from moy_store_base import FILE_KIND_DIRS, files_root
    except ImportError:  # host: the runtime package
        from runtime import devices, moy_app
        from runtime.moy_store_base import FILE_KIND_DIRS, files_root
    have = devices.grants(pid) if devices.grants is not None else None
    if have is None:
        return None
    where = _face("media").where
    roots = []
    cart = devices.cart_of(pid) if devices.cart_of is not None else None
    if cart and cart.get("path"):
        roots.append(_norm(where(cart["path"])))
    kind = moy_app.policy(tuple(have))[1]
    root = getattr(devices.console, "carts_root", None)
    if kind is not None and root:
        roots.append(_norm(where(files_root(root) + "/" + FILE_KIND_DIRS[kind])))
    return [r for r in roots if r is not None]


def _within(roots, path):
    p = _norm(path)
    return p is not None and any(p.startswith(r.rstrip("/") + "/") for r in roots)


class _Media(_Handle):
    def __init__(self, cfg):
        _only(cfg, ())
        self.f = _face("media")

    def _allow(self):
        """The test a file the calling process names must pass: inside its
        folders (`_media_roots`), else None where it is not gated."""
        try:
            import devices
        except ImportError:  # host: the runtime package
            from runtime import devices
        roots = _media_roots(devices.pid())
        return None if roots is None else (lambda path: _within(roots, path))

    def write(self, data):
        self.f.play(_text(data), self._allow())
        return len(data)

    def _none(self):
        raise OSError(ENOENT, "no stream there")

    def set(self, cfg):
        _only(cfg, ("volume", "pause", "seek", "skip", "stop"))
        f = self.f
        if "stop" in cfg:
            _int(cfg["stop"], 1, 1, "stop")
        if "skip" in cfg and cfg["skip"] not in ("next", "prev"):
            raise OSError(EINVAL, "skip=%r" % (cfg["skip"],))
        if f.now() is None and any(k != "stop" for k in cfg):
            self._none()
        if "volume" in cfg:
            f.volume(_int(cfg["volume"], 0, 100, "volume"))
        if "pause" in cfg:
            if _int(cfg["pause"], 0, 1, "pause"):
                f.pause()
            else:
                f.resume()
        if "seek" in cfg and not f.seek(_int(cfg["seek"], 0, 0x7FFFFFFF, "seek")):
            self._none()
        if "skip" in cfg and not (f.next() if cfg["skip"] == "next" else f.prev()):
            self._none()
        if "stop" in cfg:
            f.stop()
        return 0

    def get(self, what):
        f = self.f
        if what == "tls":
            return int(f.tls())
        if what == "stamp":
            return f.stamp() or 0
        n = f.now()
        if what == "state":
            return 0 if n is None else _MEDIA_STATES.index(n["state"])
        if what == "mine":
            try:
                import devices
            except ImportError:  # host: the runtime package
                from runtime import devices
            return int(n is not None and n["client"] == devices.pid())
        if what in _MEDIA_NOW:
            return 0 if n is None else int(n[what])
        raise OSError(EINVAL, "no %s" % what)

    def read(self, cap):
        n = self.f.now()
        if n is None:
            return b""
        out = "".join((n[k] or "") + "\n" for k in ("title", "artist", "station", "src"))
        out = out.encode("utf-8")
        return FULL if len(out) > cap else out

    def xfer(self, data, cap):
        path = _text(data)
        allow = self._allow()
        if allow is not None and not allow(self.f.where(path)):
            raise OSError(EPERM, "not one of its folders")
        got = self.f.probe(path)
        if got is None:
            self._none()
        out = _u32(got[0]) + ((got[1] or "") + "\n" + (got[2] or "")).encode("utf-8")
        return FULL if len(out) > cap else out

    def close(self, pid):
        """The handle's end: the stream is the service's and plays on."""


NET_HEAD = 10               # a net record's head: the sender (6 bytes), port, n (u16)
NET_PORTS = 4               # the ports a process holds at once
_NET_CODE = {-1: EINVAL, -2: ENOSPC, -3: EBUSY, -4: EBADF, -5: EIO, -6: EBUSY}


def _link():
    try:
        import devices
    except ImportError:  # host: the runtime package
        from runtime import devices
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


_DEVICES = {"http": _Http, "ble": _Ble, "usb": _Usb,
            "midi": _Midi, "camera": _Camera,
            "media": _Media, "net": _Net}
# A device with one holder: a process opens it once.
_ONE = ("ble", "usb", "camera", "media")
# The permissions a device needs, one of them, where they are not its name.
_PERMS = {"media": ("media", "media:url"), "net": ("multiplayer",)}


def _need(pid, name):
    try:
        import devices
    except ImportError:  # host: the runtime package
        from runtime import devices
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
