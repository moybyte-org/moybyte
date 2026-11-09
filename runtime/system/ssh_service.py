"""The SSH server's console side (native/moy_ssh, docs/os_2026-11/system.md
section 3): it serves while a password is set, holding the radio for the
"ssh" tag, and the console answers on the network as `<name>.local`, its name
Settings' Name, or with none set `moybyte-XXXX`: the last four hex digits of
the chip's MAC, which on an S3 is its WiFi station's. A P4's radio is its C6,
whose MAC is out of reach until the radio is up, so a P4 takes its own chip's.
The host's is a fixed stand-in, HOST_MAC, so its pixels stay the same.

The server reads its sign-in from the store's system folder at every
connection's start (native/moy_ssh/moy_ssh.h): `password` (the hash this
module writes), `ssh` (`password_auth = no` turns passwords off),
`authorized_keys` (ssh-copy-id's) and the host key it makes itself. No
password: no SSH, and the radio is not held for it.

A board whose station is its only I/O (the headless Zero) has no lease: its
console's `wifi_hold` is None, its radio and its mDNS name are up while it
serves its webhost, and the server rides them, holding, dialling and
announcing nothing (`_leases`).

A stop asked from a session's own command (`passwd -d` over SSH) ends the
server only after that session (moy_ssh.h, THE RUN), so the radio and the
name stay until the run reports its tasks parked: a shell wake looks every
PARK_MS. A start before then cancels the wake and keeps both.

  boot(ws)                 at the console's first frame: serve if a password is set
  set_password(ws, pw)     store the password's record (None: remove it), sign
                           every browser out (its key kept), and serve or
                           stop to match; False for one under MIN_PASSWORD
                           characters
  system_dir(ws)           the store's system folder the server signs in from
  is_set(ws)               whether a password is set
  name(ws)                 the console's name: Settings' Name, or default_name()
  default_name()           `moybyte-XXXX`, the name of a console with none set
  hostname(ws)             the mDNS name the console answers to
  rename(ws)               the name re-announced after Settings' Name changed
  status()                 (serving, sessions, signed in, refused) or None
  fingerprint(ws)          the host key's SHA256 fingerprint as OpenSSH prints it,
                           or None before the server first made its key
  passwd(door, args)       the shell's `passwd` (runtime/shell/sh_roles.py's
                           role command, the Zero's door): its exit code

The password is kept as moy_seal.h's record (THE PASSWORD: `$moy2$ITER$SALT$
W0$L`, PBKDF2 then SPAKE2+'s w0 and L = w1*G), made by moy_net.pw_hash, the
same C on every port; a record of another form reads as no password.
"""

try:
    import moy_ssh as _N
except ImportError:  # the host, or an image without the server
    _N = None

try:
    import moy_docs as _docs
except ImportError:  # host: the runtime package
    from runtime import moy_docs as _docs

try:
    import moybyte as _moybyte
except ImportError:  # host: the runtime package
    try:
        from runtime import moybyte as _moybyte
    except ImportError:  # the Zero: no console, so no wakes (_leases)
        _moybyte = None

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    try:
        from runtime import moy_log as _L
    except ImportError:
        _L = None

try:
    import devices as _D
except ImportError:  # host: the runtime package
    try:
        from runtime import devices as _D
    except ImportError:  # the Zero: no console's processes
        _D = None

TAG = "ssh"
PASSWORD = "password"
MIN_PASSWORD = 8                # the Zero's setup form's floor too (zero_setup.CONSOLE_MIN)
RECORD = "$moy2$"
NAME_MAX = 63
PREFIX = "moybyte"
HOST_MAC = b"\x02\x00\x00\x00\xbe\xef"
PARK_MS = 100

_state = {"serving": False, "name": None, "default": None, "park": None}


def system_dir(ws):
    """The store's system folder, where the server reads its sign-in: the one
    beside the carts the console booted on (the card's, or the internal
    store's without one), where its settings are."""
    path = _docs.system_store_path(getattr(ws, "carts_root", None) or _docs.CARTS_DIR)
    return path.rsplit("/", 1)[0]


def _path(ws, name):
    return system_dir(ws) + "/" + name


def is_set(ws):
    try:
        with open(_path(ws, PASSWORD)) as f:
            return f.read().startswith(RECORD)
    except OSError:
        return False


def hashed(password, salt):
    try:
        import moy_net
    except ImportError:  # host: the runtime package
        from runtime import net_binding as moy_net
    return moy_net.pw_hash(password, salt)


def _salt():
    import os
    return "".join("%02x" % b for b in os.urandom(16))


def _write(ws, name, text):
    try:
        _docs._mkdir(system_dir(ws))
    except Exception:  # noqa: BLE001 -- the folder may already be there
        pass
    with open(_path(ws, name), "w") as f:
        f.write(text)


def default_name():
    """Read once: the chip's MAC is fixed. Never stored, so a sync carries
    only a name the user set."""
    got = _state["default"]
    if got is None:
        try:
            import machine
            mac = machine.unique_id()
        except (ImportError, AttributeError):  # the host
            mac = HOST_MAC
        got = _state["default"] = "%s-%02x%02x" % (PREFIX, mac[-2], mac[-1])
    return got


def name(ws):
    system = getattr(ws, "system", None)
    return (str(system.get("name", "") or "") if system is not None else "") or default_name()


def hostname(ws):
    """The console's name as a DNS label: lowercase letters, digits and
    hyphens."""
    raw = name(ws)
    out = []
    for ch in raw.lower():
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    label = "".join(out).strip("-")[:NAME_MAX].strip("-")
    return label or default_name()


def _announce(ws):
    try:
        import moy_net
    except ImportError:
        return
    name = hostname(ws)
    try:
        if moy_net.wifi_mdns(name):
            _state["name"] = name
    except Exception as exc:  # noqa: BLE001 -- the name is a convenience
        print("Moybyte ssh: mDNS: %s" % (exc,))


def _dial(ws):
    """The station started toward the last-joined saved network, without
    waiting for it (the kernel's connect returns at once): the server
    listens whenever the link comes up."""
    w = getattr(ws, "wifi", None)
    try:
        if w is None or w.status()[0]:
            return
        store, root = getattr(w, "_store", None), getattr(w, "_root", None)
        nets = store.load_wifi(root) if store is not None and root is not None else []
        wlan = getattr(w, "wlan", None)
        if nets and wlan is not None:
            wlan.connect(nets[0]["ssid"], nets[0].get("password", "") or "")
    except Exception as exc:  # noqa: BLE001 -- the link is the radio's to find
        print("Moybyte ssh: wifi: %s" % (exc,))


def _leases(ws):
    """Whether the server holds the radio and the name for itself: a
    console's radio is off unless held. A console with no lease
    (`wifi_hold` None, the Zero) keeps both up for its webhost."""
    return getattr(ws, "wifi_hold", None) is not None


def rename(ws):
    if _state["serving"] and _leases(ws) and _state["name"] != hostname(ws):
        _announce(ws)


def start(ws):
    """Serve: the radio held for "ssh", the server listening, the name on
    the network. False with no server in the image or no password set."""
    if _N is None or not is_set(ws):
        return False
    if _state["serving"]:
        return True
    _unpark()
    lease = _leases(ws)
    if lease:
        ws.wifi_hold(TAG)
    try:
        _N.start(system_dir(ws))
    except Exception as exc:  # noqa: BLE001 -- a server that will not start serves nothing
        print("Moybyte ssh: %s" % (exc,))
        if lease:
            ws.wifi_release(TAG)
        return False
    _state["serving"] = True
    if lease:
        _dial(ws)
        _announce(ws)
    return True


def _unannounce():
    if _state["name"] is None:
        return
    _state["name"] = None
    try:
        import moy_net
        moy_net.wifi_mdns("")
    except Exception as exc:  # noqa: BLE001 -- a responder that will not end ends with the radio
        print("Moybyte ssh: mDNS: %s" % (exc,))


def _let_go(ws):
    _unannounce()
    ws.wifi_release(TAG)


def _unpark():
    w = _state["park"]
    _state["park"] = None
    if w is not None:
        _moybyte.WAKES.cancel(w)


def _as_shell(fn):
    if _L is None:
        return fn()
    was = _L.current()
    _L.current(_D.SHELL)
    try:
        return fn()
    finally:
        _L.current(was)


def _await_park(ws):
    def look():
        if _N.parked():
            _unpark()
            _let_go(ws)
    _state["park"] = _as_shell(lambda: _moybyte.WAKES.every(PARK_MS, look))


def stop(ws):
    if not _state["serving"]:
        return
    _state["serving"] = False
    try:
        _N.stop()
    finally:
        if _leases(ws):
            if _N.parked():
                _let_go(ws)
            else:
                _await_park(ws)


def boot(ws):
    try:
        start(ws)
    except Exception as exc:  # noqa: BLE001 -- the desk comes up regardless
        print("Moybyte ssh: %s" % (exc,))


def _sign_out():
    """Every browser's session ended and a web terminal cut: a session
    proves the password it was paired under, not the new one."""
    try:
        import moy_net
    except ImportError:  # host: the runtime package
        from runtime import net_binding as moy_net
    out = getattr(moy_net, "web_signout", None)
    if out is not None:
        out()


def set_password(ws, password):
    """The password's record into system/password, or the file removed; every
    browser signed out and the server started or stopped to match. False for
    a password under MIN_PASSWORD characters, which changes nothing."""
    if password is None:
        try:
            import os
            os.remove(_path(ws, PASSWORD))
        except OSError:
            pass
        _sign_out()
        stop(ws)
        return True
    if len(password) < MIN_PASSWORD:
        return False
    _write(ws, PASSWORD, hashed(password, _salt()) + "\n")
    _sign_out()
    start(ws)
    return True


def _day(t):
    try:
        import wallclock as _wc
    except ImportError:
        try:  # host: the runtime package
            from runtime import wallclock as _wc
        except ImportError:  # the Zero: no zone, the date in UTC
            import time
            if time.gmtime(0)[0] == 2000:   # MicroPython's epoch; a stamp is Unix seconds
                t -= 946684800
            return "%d-%02d-%02d" % time.gmtime(t)[:3]
    y, m, dd = _wc.localtime(t)[:3]
    return "%d-%02d-%02d" % (y, m, dd)


def passwd(d, a):
    """The shell's `passwd` for a door `d` (its `ws`, `out(text)`,
    `err(name, text)`): with no argument whether a password is set, what SSH
    serves, its host key and the browsers paired; PASSWORD sets it, `-d`
    removes it, `-l` lists the browsers paired with the console's webhost
    (`ws.webhost`, None where it serves none) and `-r ID|all` forgets one or
    every one. Its exit code."""
    ws = d.ws
    web = getattr(ws, "webhost", None)
    if not a:
        st = status()
        d.out("password %s\n" % ("set" if is_set(ws) else "not set"))
        if st is not None:
            d.out("ssh %s as %s.local, %d session(s), %d refused\n"
                  % ("serving" if st[0] else "off", hostname(ws), st[1], st[3]))
        fp = fingerprint(ws)
        d.out("host key %s\n" % (fp or "not made yet (the first SSH start makes it)"))
        if web is not None:
            d.out("%d browser(s) paired (passwd -l)\n" % len(web.browsers()))
        return 0
    if a == ["-d"]:
        set_password(ws, None)
        return 0
    if a[0] in ("-l", "-r"):
        if web is None:
            d.err("passwd", "this console serves no browser")
            return 1
        if a == ["-l"]:
            for kid, created, seen, name in web.browsers():
                d.out("%s  paired %s  seen %s  %s\n" % (kid, _day(created), _day(seen), name))
            return 0
        if len(a) == 2:
            if web.forget(a[1]) == 0:
                d.err("passwd", "no browser " + a[1])
                return 1
            return 0
        d.err("usage", "passwd -r ID|all")
        return 2
    if len(a) != 1 or a[0].startswith("-"):
        d.err("usage", "passwd PASSWORD | -d | -l | -r ID|all")
        return 2
    if not set_password(ws, a[0]):
        d.err("passwd", "not set: a password is %d characters or more" % MIN_PASSWORD)
        return 1
    return 0


def status():
    if _N is None:
        return None
    s = _N.stats()
    return (bool(s[7]), s[0], s[1], s[2])


HOST_KEY = "ssh_host_key"
_ED25519 = b"ssh-ed25519"


def fingerprint(ws):
    """`SHA256:<base64>` of the host key's public half, as `ssh` prints it on
    a first connection, so a person can check they reached this console. The
    key file is Dropbear's (native/moy_ssh): the type's name, then 64 bytes,
    the private half and the public."""
    try:
        with open(_path(ws, HOST_KEY), "rb") as f:
            raw = f.read(128)
    except OSError:
        return None
    n = len(_ED25519)
    if (len(raw) < 4 + n + 4 + 64 or raw[:4] != n.to_bytes(4, "big")
            or raw[4:4 + n] != _ED25519 or raw[4 + n:8 + n] != (64).to_bytes(4, "big")):
        return None
    pub = raw[8 + n + 32:8 + n + 64]
    blob = n.to_bytes(4, "big") + _ED25519 + (32).to_bytes(4, "big") + pub
    import hashlib
    import binascii
    d = binascii.b2a_base64(hashlib.sha256(blob).digest()).strip().rstrip(b"=")
    return "SHA256:" + d.decode()
