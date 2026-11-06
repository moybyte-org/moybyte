"""The SSH server's console side (native/moy_ssh, docs/os_2026-11/system.md
section 3): it serves while a password is set, holding the radio for the
"ssh" tag, and the console answers on the network as `<name>.local`, its name
Settings' Name.

The server reads its sign-in from the store's system folder at every
connection's start (native/moy_ssh/moy_ssh.h): `password` (the hash this
module writes), `ssh` (`password_auth = no` turns passwords off),
`authorized_keys` (ssh-copy-id's) and the host key it makes itself. No
password: no SSH, and the radio is not held for it.

  boot(ws)                 at the console's first frame: serve if a password is set
  set_password(ws, pw)     store the password's hash (None: remove it), and
                           serve or stop to match; False where it cannot
  is_set()                 whether a password is set
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

TAG = "ssh"
PASSWORD = "password"
ROUNDS = 4096
NAME_MAX = 63
DEFAULT_NAME = "moybyte"

_state = {"serving": False, "name": None}


def system_dir():
    """The store's system folder, where the server reads its sign-in."""
    path = _docs.system_store_path()
    return path.rsplit("/", 1)[0]


def _path(name):
    return system_dir() + "/" + name


def is_set():
    try:
        with open(_path(PASSWORD)) as f:
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


def _write(name, text):
    try:
        _docs._mkdir(system_dir())
    except Exception:  # noqa: BLE001 -- the folder may already be there
        pass
    with open(_path(name), "w") as f:
        f.write(text)


def hostname(ws):
    """Settings' Name as a DNS label: lowercase letters, digits and hyphens."""
    raw = str(ws.system.get("name", "") or "") if getattr(ws, "system", None) else ""
    out = []
    for ch in raw.lower():
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    name = "".join(out).strip("-")[:NAME_MAX].strip("-")
    return name or DEFAULT_NAME


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
    if _N is None or not is_set():
        return False
    if _state["serving"]:
        return True
    ws.wifi_hold(TAG)
    try:
        _N.start(system_dir())
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


def stop(ws):
    if not _state["serving"]:
        return
    _state["serving"] = False
    try:
        _N.stop()
    finally:
        _unannounce()
        ws.wifi_release(TAG)


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
            os.remove(_path(PASSWORD))
        except OSError:
            pass
        stop(ws)
        return True
    if not password:
        return False
    _write(PASSWORD, hashed(password, _salt()) + "\n")
    start(ws)
    return True


def status():
    if _N is None:
        return None
    s = _N.stats()
    return (bool(s[7]), s[0], s[1], s[2])
