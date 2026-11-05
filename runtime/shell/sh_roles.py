"""The C shell's role commands (docs/os_2026-11/system.md section 1): the
command faces of the console's Python services, which the shell reaches
through its door (runtime/shell/sh_door.py's `role`) and refuses with "needs
the console" while the VM is stopped. `r_<name>(door, args)` answers the
exit code; its output goes through the door to the session's fd 1, its
complaints to fd 2. Imported at the first role command.

Each is the face of one implementation, never a second copy, run as the shell
(pid 1: full access, not gated); what the shell takes (the radio, the
microphone, the USB host, a MIDI port, the audio ring) it gives back before
the command ends, except a media stream, which plays until `media stop`.

  perm [list [APP]]          each app's asked permission, on or off: Settings'
                             App permissions (runtime/shell/app_perms.py)
  perm on|off APP PERM       turn one on or off from its next launch
  notify [list]              the notifications not yet opened, newest first
  notify send [-u low|normal|critical] SUMMARY [BODY]
                             post one as the console; prints its id
  notify open ID             act on it, as the banner's tap does
  notify clear [ID]          clear one, or every one
  service [status [NAME|PID]]  each service cart: pid, state, id
  service start|stop|restart NAME|PID
                             the task manager's Stop is the same stop
  lease                      what the board leases and who holds it
  http get URL [CAP]         a GET, its body to stdout; not 2xx exits 1
  mic                        its rate, holder and drops
  mic read MS                MS of PCM16 mono at its rate to stdout
  lora send TEXT...          a raw packet
  lora recv [MS]             the next packet within MS: "RSSI SNR TEXT"
  ble scan [MS]              what a scan hears: "ADDR RSSI NAME"
  usb [devices]              what is plugged in: "ADDR CLASS VID:PID NAME"
  midi                       the transports this console has
  midi send PORT BYTE...     MIDI bytes (hex) to uart<n>, usb:ADDR or ble:ADDR
  media                      the stream's state, one `key value` a line
  media play SRC | stop | pause | resume | next | prev | volume [V]
  snd                        the audio ring's counters
  snd play [FRAMES]          stdin (PCM16 mono, 22,050 Hz) through the ring
  python [-c CODE]           a REPL over stdin into the shell's VM (an
                             expression's value printed, a block ended by a
                             blank or unindented line), or CODE run
"""

try:
    from ticks import _ticks_ms, _ticks_diff
except ImportError:  # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_diff

try:
    import sh_door as _door
except ImportError:  # host: the runtime package
    from runtime import sh_door as _door

USAGE = 2
WAIT_MS = 20000                 # an http request's wait, at most
MIC_MS = 10000                  # a mic read, at most
LORA_MS = 5000                  # a recv's wait, by default
SCAN_MS = 3000                  # a BLE scan, by default
POLL_MS = 10

_WHY = {1: "not permitted", 2: "not found", 5: "i/o error", 9: "not open",
        12: "out of memory", 16: "busy", 19: "this console has none",
        22: "invalid argument", 28: "no room", 93: "https is refused",
        107: "not connected", 110: "timed out", 113: "unreachable"}


def why(e):
    """An OSError as a line: its own words, else its errno's."""
    a = e.args
    if len(a) > 1 and isinstance(a[1], str) and not a[1].startswith("errno"):
        return a[1]
    n = a[0] if a and isinstance(a[0], int) else 0
    return _WHY.get(n, "error %d" % n)


def _sleep(ms):
    try:
        import time
        time.sleep_ms(ms)
    except AttributeError:  # CPython
        time.sleep(ms / 1000)


def _int(s, default=None):
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def _usage(d, name, text):
    d.err("usage", name + " " + text)
    return USAGE


def _api(d, name):
    try:
        import devices
    except ImportError:  # host: the runtime package
        from runtime import devices
    f = devices.api(name)
    if f is None:
        d.err(name, _WHY[19])
    return f


def _sched():
    try:
        import scheduler
    except ImportError:  # host: the runtime package
        from runtime import scheduler
    return scheduler


# -- perm ---------------------------------------------------------------------------


def _perm_carts(ws):
    try:
        import app_perms
    except ImportError:  # host: the runtime package
        from runtime import app_perms
    out = []
    for cart in ws.carts.all:
        if ws.is_system_app(cart):
            continue
        perms = app_perms.asked(cart.get("permissions"))
        if perms:
            out.append((app_perms.app_id(cart), cart.get("id"), perms))
    return out


def r_perm(d, a):
    ws = d.ws
    rows = _perm_carts(ws)
    c = ws.consent
    if not a or a[0] == "list":
        want = a[1] if len(a) > 1 else None
        hit = False
        for aid, cid, perms in rows:
            if want is None or want in (aid, cid):
                hit = True
                d.out("".join("%s %s %s\n" % (aid, p, "on" if c.is_on(aid, p) else "off")
                              for p in perms))
        if want is not None and not hit:
            d.err("perm", "%s: asks for nothing" % want)
            return 1
        return 0
    if a[0] in ("on", "off") and len(a) == 3:
        for aid, cid, perms in rows:
            if a[1] in (aid, cid):
                if a[2] not in perms:
                    d.err("perm", "%s does not ask for %s" % (a[1], a[2]))
                    return 1
                c.turn(aid, a[2], a[0] == "on")
                return 0
        d.err("perm", "%s: asks for nothing" % a[1])
        return 1
    return _usage(d, "perm", "[list [APP]] | on|off APP PERM")


# -- notify ---------------------------------------------------------------------------


def _notes():
    try:
        import notifications
    except ImportError:  # host: the runtime package
        from runtime import notifications
    return notifications


def r_notify(d, a):
    N = _notes()
    notes = N.NOTES
    if not a or a[0] == "list":
        d.out("".join("%d %s: %s\n" % (n.id, n.app or "?", n.summary) for n in notes.list))
        return 0
    verb = a[0]
    if verb == "send":
        a = a[1:]
        urgency = N.NORMAL
        if len(a) >= 2 and a[0] == "-u":
            urgency = {"low": N.LOW, "normal": N.NORMAL, "critical": N.CRITICAL}.get(
                a[1], _int(a[1], -1))
            a = a[2:]
            if urgency not in (N.LOW, N.NORMAL, N.CRITICAL):
                return _usage(d, "notify", "send [-u low|normal|critical] SUMMARY [BODY]")
        if not a or len(a) > 2:
            return _usage(d, "notify", "send [-u low|normal|critical] SUMMARY [BODY]")
        nid = notes.post(_door.SHELL, a[0], a[1] if len(a) > 1 else "", urgency=urgency)
        d.out("%d\n" % nid)
        return 0
    if verb == "clear" and len(a) == 1:
        notes.clear_all()
        return 0
    if verb in ("clear", "open") and len(a) == 2:
        nid = _int(a[1])
        ok = nid is not None and (notes.clear(nid) if verb == "clear" else notes.act(nid))
        if not ok:
            d.err("notify", "%s: no such notification" % a[1])
            return 1
        return 0
    return _usage(d, "notify", "[list] | send ... | open ID | clear [ID]")


# -- service --------------------------------------------------------------------------


def _svc_name(cart):
    return cart.get("id") or cart.get("title") or "service"


def _svc_rows(ws):
    """[(name, cart, Proc or None)]: every service cart, with its live or last row."""
    S = _sched().SCHED
    out = []
    for cart in ws.carts.all:
        if cart.get("type") != "service":
            continue
        name = _svc_name(cart)
        p = S.find(name)
        if p is None:
            for q in S.services():
                if q.name == name and (p is None or q.pid > p.pid):
                    p = q
        out.append((name, cart, p))
    return out


def _svc_line(name, p):
    sch = _sched()
    if p is None:
        return "-     stopped    %s\n" % name
    st = p.state if p.state != sch.EXITED else "exited %d" % (p.code or 0)
    return "%-5s %-10s %s\n" % (p.pid if p.state != sch.EXITED else "-", st, name)


def _svc_find(rows, key):
    pid = _int(key)
    for name, cart, p in rows:
        if name == key or (pid is not None and p is not None and p.pid == pid):
            return name, cart, p
    return None


def r_service(d, a):
    ws = d.ws
    sch = _sched()
    rows = _svc_rows(ws)
    if not a or a[0] == "status":
        if len(a) <= 1:
            d.out("".join(_svc_line(n, p) for n, _c, p in rows))
            return 0
        hit = _svc_find(rows, a[1])
        if hit is None:
            d.err("service", "%s: no such service" % a[1])
            return 1
        d.out(_svc_line(hit[0], hit[2]))
        return 0 if hit[2] is not None and hit[2].state != sch.EXITED else 3
    if a[0] not in ("start", "stop", "restart") or len(a) != 2:
        return _usage(d, "service", "[status [NAME|PID]] | start|stop|restart NAME|PID")
    hit = _svc_find(rows, a[1])
    if hit is None:
        d.err("service", "%s: no such service" % a[1])
        return 1
    name, cart, p = hit
    live = p is not None and p.state != sch.EXITED
    if a[0] in ("stop", "restart") and live:
        ws.procs.stop_service(p.pid)
    elif a[0] == "stop":
        d.err("service", "%s is not running" % name)
        return 1
    if a[0] in ("start", "restart"):
        if sch.SCHED.find(name) is not None:
            return 0
        if sch.start_cart(ws, cart) is None:
            d.err("service", "%s did not start: its log says why" % name)
            return 1
    return 0


# -- lease ------------------------------------------------------------------------------


def r_lease(d, a):
    if a:
        return _usage(d, "lease", "")
    try:
        from leases import pins
    except ImportError:  # host: the runtime package
        from runtime.leases import pins
    text = pins()
    d.out(text + "\n" if text else "")
    return 0


# -- the device faces --------------------------------------------------------------------


def r_http(d, a):
    if not a or a[0] != "get" or len(a) not in (2, 3):
        return _usage(d, "http", "get URL [CAP]")
    f = _api(d, "http")
    if f is None:
        return 1
    req = f.get(a[1], _int(a[2], f.CAP_MAX) if len(a) == 3 else f.CAP_MAX)
    try:
        t0 = _ticks_ms()
        st = req.state()
        while st is None:
            if _ticks_diff(_ticks_ms(), t0) > WAIT_MS:
                d.err("http", _WHY[110])
                return 1
            _sleep(POLL_MS)
            st = req.state()
        body = req.body()
        if body and d.io is not None:
            d.io.write(1, body)
    finally:
        req.close()
    if not 200 <= st[0] < 300:
        d.err("http", "status %d" % st[0])
        return 1
    return 0


def r_mic(d, a):
    f = _api(d, "mic")
    if f is None:
        return 1
    if not a:
        d.out("rate %d holder %d drops %d\n" % (f.rate(), f.holder(), f.drops()))
        return 0
    ms = _int(a[1]) if len(a) == 2 and a[0] == "read" else None
    if ms is None or not 0 < ms <= MIC_MS:
        return _usage(d, "mic", "[read MS], MS up to %d" % MIC_MS)
    rate = f.open()
    try:
        want = rate * ms // 1000
        buf = bytearray(1024)
        t0 = _ticks_ms()
        while want > 0 and _ticks_diff(_ticks_ms(), t0) < ms + 1000:
            n = min(f.read(buf), want)
            if n:
                d.io.write(1, bytes(buf[:2 * n]))
                want -= n
            else:
                _sleep(POLL_MS)
    finally:
        f.close()
    return 0


def r_lora(d, a):
    if not a or a[0] not in ("send", "recv") or (a[0] == "send" and len(a) < 2) \
            or (a[0] == "recv" and len(a) > 2):
        return _usage(d, "lora", "send TEXT... | recv [MS]")
    f = _api(d, "lora")
    if f is None:
        return 1
    try:
        if a[0] == "send":
            f.send(" ".join(a[1:]).encode())
            return 0
        ms = _int(a[1], LORA_MS) if len(a) == 2 else LORA_MS
        f.listen(True)
        buf = bytearray(256)
        t0 = _ticks_ms()
        while _ticks_diff(_ticks_ms(), t0) < ms:
            got = f.recv(buf)
            if got is not None:
                n, rssi, snr = got
                d.out("%d %s %s\n" % (rssi, snr, bytes(buf[:n]).decode("utf-8", "replace")))
                return 0
            _sleep(POLL_MS)
        d.err("lora", "nothing heard")
        return 1
    finally:
        f.close()


def r_ble(d, a):
    if not a or a[0] != "scan" or len(a) > 2:
        return _usage(d, "ble", "scan [MS]")
    f = _api(d, "ble")
    if f is None:
        return 1
    ms = _int(a[1], SCAN_MS) if len(a) == 2 else SCAN_MS
    try:
        f.scan(ms)
        _sleep(ms)
        d.out("".join("%s %d %s\n" % (addr, rssi, name or "-")
                      for addr, rssi, name in f.devices()))
    finally:
        f.close()
    return 0


def r_usb(d, a):
    if a and a != ["devices"]:
        return _usage(d, "usb", "[devices]")
    f = _api(d, "usb")
    if f is None:
        return 1
    kinds = {f.HID: "hid", f.CDC: "cdc", f.MIDI: "midi"}
    try:
        d.out("".join("%d %s %04x:%04x %s\n" % (addr, kinds.get(cls, "other"), vid, pid,
                                                 name or "-")
                      for addr, cls, _kind, vid, pid, name in f.devices()))
    finally:
        f.close()
    return 0


def r_midi(d, a):
    f = _api(d, "midi")
    if f is None:
        return 1
    if not a:
        names = list(f._uart_rows())
        try:
            import devices
        except ImportError:  # host: the runtime package
            from runtime import devices
        names += [t for t in ("usb", "ble") if devices.present(t)]
        d.out("".join(n + "\n" for n in names))
        return 0
    if a[0] != "send" or len(a) < 3:
        return _usage(d, "midi", "[send uart<n>|usb:ADDR|ble:ADDR BYTE...]")
    try:
        data = bytes(int(b, 16) for b in a[2:])
    except ValueError:
        return _usage(d, "midi", "send PORT BYTE... (each byte in hex)")
    to = a[1]
    try:
        if to.startswith("usb:"):
            port = f.usb(int(to[4:]))
        elif to.startswith("ble:"):
            port = f.ble(to[4:])
        else:
            port = f.uart(to)
        f.send(port, data)
    finally:
        f.close()
    return 0


def r_media(d, a):
    f = _api(d, "media")
    if f is None:
        return 1
    if not a:
        now = f.now()
        if now is None:
            d.out("state idle\n")
            return 0
        d.out("".join("%s %s\n" % (k, now[k]) for k in sorted(now)))
        return 0
    verb = a[0]
    if verb == "play" and len(a) == 2:
        f.play(a[1])
    elif verb in ("stop", "pause", "resume") and len(a) == 1:
        getattr(f, verb)()
    elif verb in ("next", "prev") and len(a) == 1:
        if not getattr(f, verb)():
            d.err("media", "no %s entry" % verb)
            return 1
    elif verb == "volume" and len(a) <= 2:
        v = f.volume(_int(a[1]) if len(a) == 2 else None)
        if v is None:
            d.err("media", "nothing plays")
            return 1
        d.out("%d\n" % v)
    else:
        return _usage(d, "media", "[play SRC | stop | pause | resume | next | prev | volume [V]]")
    return 0


def r_snd(d, a):
    try:
        import moybyte as m
    except ImportError:  # host: the runtime package
        from runtime import moybyte as m
    if not a:
        c = m.snd_counts()
        if c is None:
            d.err("snd", _WHY[19])
            return 1
        d.out("queued %d played %d starved %d room %d open %d depth %d\n" % tuple(c[:6]))
        return 0
    if a[0] != "play" or len(a) > 2:
        return _usage(d, "snd", "[play [FRAMES]]")
    if not m.snd_open(_int(a[1], 0) if len(a) == 2 else 0):
        d.err("snd", "nothing would play it")
        return 1
    try:
        data = _door.In(d.io).read()
        at = 0
        while at + 1 < len(data):
            took = m.snd(bytearray(data[at:at + 4096]))
            at += 2 * took
            if not took:
                _sleep(POLL_MS)
        c = m.snd_counts()
        t0 = _ticks_ms()
        while c is not None and c[3] < c[5] and _ticks_diff(_ticks_ms(), t0) < 2000:
            _sleep(POLL_MS)
            c = m.snd_counts()
    finally:
        m.snd_close()
    return 0


# -- python ------------------------------------------------------------------------------


def _open_brackets(text):
    n = 0
    for ch in text:
        if ch in "([{":
            n += 1
        elif ch in ")]}":
            n -= 1
    return n > 0


def r_python(d, a):
    stdin = _door.In(d.io)
    stdout = _door.Out(d.io, 1)
    stderr = _door.Out(d.io, 2)
    try:
        import moybyte
    except ImportError:  # host: the runtime package
        from runtime import moybyte
    ns = {"__name__": "__main__", "moybyte": moybyte}
    ns.update(_door.stdio_names(stdin, stdout, stderr))
    if a:
        if a[0] != "-c" or len(a) < 2:
            return _usage(d, "python", "[-c CODE]")
        src = a[1]
        return _door.run_program(_door.SHELL, lambda: exec(src, ns), stderr)
    status = [0, None]              # the last failure's code, SystemExit's

    def one(src):
        try:
            try:
                code = compile(src, "<stdin>", "eval")
            except SyntaxError:
                exec(compile(src, "<stdin>", "exec"), ns)
                return
            v = eval(code, ns)
        except SystemExit as e:
            status[1] = _door.exit_code(e.args[0] if e.args else None, stderr)
            return
        if v is not None:
            ns["_"] = v
            stdout.write(repr(v) + "\n")

    def run(src):
        code = _door.run_program(_door.SHELL, lambda: one(src), stderr)
        if code:
            status[0] = code
        return status[1] is not None

    held = []
    while True:
        line = stdin.readline()
        end = not line
        line = line.rstrip("\r\n")
        if held:
            text = "\n".join(held)
            if not end and (_open_brackets(text) or (line.strip() and line[:1] in " \t")):
                held.append(line)
                continue
            held = []
            if run(text + "\n"):
                break
        if end:
            break
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.endswith(":") or s.endswith("\\") or _open_brackets(s):
            held = [line]
            continue
        if run(line + "\n"):
            break
    return status[1] if status[1] is not None else status[0]
