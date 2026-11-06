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

A stop asked from a session's own command (`passwd -d` over SSH) ends the
server only after that session (moy_ssh.h, THE RUN), so the radio and the
name stay until the run reports its tasks parked: a shell wake looks every
PARK_MS. A start before then cancels the wake and keeps both.

  boot(ws)                 at the console's first frame: serve if a password is set
  set_password(ws, pw)     store the password's hash (None: remove it), and
                           serve or stop to match; False where it cannot
  system_dir(ws)           the store's system folder the server signs in from
  is_set(ws)               whether a password is set
  name(ws)                 the console's name: Settings' Name, or default_name()
  default_name()           `moybyte-XXXX`, the name of a console with none set
  hostname(ws)             the mDNS name the console answers to
  rename(ws)               the name re-announced after Settings' Name changed
  status()                 (serving, sessions, signed in, refused) or None

The hash is moy_ssh's (`$moy1$SALT$HEX`, SHA-256 iterated), the same C on a
board; the host computes it with hashlib, the formula tests/test_moy_ssh.py
holds equal.
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
    from runtime import moybyte as _moybyte

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
    from runtime import devices as _D

TAG = "ssh"
PASSWORD = "password"
ROUNDS = 4096
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
            return f.read().startswith("$moy1$")
    except OSError:
        return False


def hashed(password, salt):
    if _N is not None:
        return _N.hash(password, salt)
    import hashlib
    d = hashlib.sha256((salt + password).encode()).digest()
    for _ in range(ROUNDS - 1):
        d = hashlib.sha256(d + password.encode()).digest()
    return "$moy1$%s$%s" % (salt, "".join("%02x" % b for b in d))


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


def rename(ws):
    if _state["serving"] and _state["name"] != hostname(ws):
        _announce(ws)


def start(ws):
    """Serve: the radio held for "ssh", the server listening, the name on
    the network. False with no server in the image or no password set."""
    if _N is None or not is_set(ws):
        return False
    if _state["serving"]:
        return True
    _unpark()
    ws.wifi_hold(TAG)
    try:
        _N.start(system_dir(ws))
    except Exception as exc:  # noqa: BLE001 -- a server that will not start serves nothing
        print("Moybyte ssh: %s" % (exc,))
        ws.wifi_release(TAG)
        return False
    _state["serving"] = True
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
        if _N.parked():
            _let_go(ws)
        else:
            _await_park(ws)


def boot(ws):
    try:
        start(ws)
    except Exception as exc:  # noqa: BLE001 -- the desk comes up regardless
        print("Moybyte ssh: %s" % (exc,))


def set_password(ws, password):
    """The password's hash into system/password, or the file removed; the
    server started or stopped to match."""
    if password is None:
        try:
            import os
            os.remove(_path(ws, PASSWORD))
        except OSError:
            pass
        stop(ws)
        return True
    if not password:
        return False
    _write(ws, PASSWORD, hashed(password, _salt()) + "\n")
    start(ws)
    return True


def status():
    if _N is None:
        return None
    s = _N.stats()
    return (bool(s[7]), s[0], s[1], s[2])
