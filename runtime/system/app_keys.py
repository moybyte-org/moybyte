"""Each app's own X25519 key pairs, kept by the console: moybyte's
`key_make`, `key_public` and `key_shared` (runtime/system/moybyte.py, THE
APP'S KEYS), served by the scheduler as the calling process's app
(`Scheduler.keys`). The secret half never crosses to the app: a key's public
half and an exchange's shared secret do.

WHERE: the store's system folder, beside the settings and the SSH host key
(runtime/system/ssh_service.py's `system_dir`): `system/keys/<app>/<name>`,
<app> the app id as a written file's name (cart_files.key: lowercase, every
other byte escaped, so the FAT card keeps two ids apart), the file the
secret's 32 bytes. No app's own file access reaches the system folder (the
files role is the user files', a compiled cart's writes its `apps/<id>/`),
and the web sync carries the settings file alone of it (moy_sync's
SYSTEM_FILES), so a synced or backed-up settings file holds no key. A key
the user reads over SSH or SFTP is the owner's own: the console's root is
theirs.

ONE KEY A NAME: a name is 1 to NAME_MAX of a-z, 0-9, '_' and '-', a file's
name on every store. `make` writes a pair once, to "<name>~part" renamed
into place, and never over one; a file that is not 32 bytes reads as no key
(strict readers), so `make` writes it again. `forget` deletes an app's
folder: a cart's removal (pkg.removed, Get Carts' and `pkg remove`;
CartManager.delete) calls it beside the consent it forgets.

Each function is one store op; the caller runs it inside the store's gate
(the scheduler through `ws.store.call`). MicroPython-safe: os only.
"""

import os

try:
    from cart_files import key as _file_key
    from moy_docs import system_store_path as _settings_path
except ImportError:  # host: the runtime package
    from runtime.cart_files import key as _file_key
    from runtime.moy_docs import system_store_path as _settings_path

DIR = "keys"
NAME_MAX = 32
SECRET = 32
PART = "~part"
_NAME = "abcdefghijklmnopqrstuvwxyz0123456789_-"
_HEX = "0123456789abcdef"


def folder(root):
    """The keys' folder of the store whose carts are at `root`."""
    return _settings_path(root).rsplit("/", 1)[0] + "/" + DIR


def _app_folder(root, app):
    return folder(root) + "/" + _file_key(app.encode())


def _path(root, app, name):
    if not isinstance(name, str) or not 0 < len(name) <= NAME_MAX:
        raise ValueError("a key's name is 1 to %d characters" % NAME_MAX)
    for c in name:
        if c not in _NAME:
            raise ValueError("a key's name is a-z, 0-9, '_' and '-'")
    return _app_folder(root, app) + "/" + name


def _hex(b):
    return "".join("%02x" % x for x in b)


def _unhex(text):
    if not isinstance(text, str) or len(text) != 2 * SECRET:
        raise ValueError("a public key is %d hex digits" % (2 * SECRET))
    text = text.lower()
    out = bytearray(SECRET)
    for i in range(SECRET):
        hi, lo = _HEX.find(text[2 * i]), _HEX.find(text[2 * i + 1])
        if hi < 0 or lo < 0:
            raise ValueError("a public key is %d hex digits" % (2 * SECRET))
        out[i] = hi * 16 + lo
    return bytes(out)


def _crypto():
    try:
        import moy_app
    except ImportError:  # host: the runtime package
        from runtime import moy_app
    return moy_app


def _secret(path):
    try:
        with open(path, "rb") as f:
            got = f.read(SECRET + 1)
    except OSError:
        return None
    return got if len(got) == SECRET else None


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def make(root, app, name):
    """The public key of app `app`'s pair `name` (hex), the pair made and
    kept where there is none."""
    path = _path(root, app, name)
    c = _crypto()
    secret = _secret(path)
    if secret is None:
        secret = c.random(SECRET)
        at = folder(root)
        _mkdir(at.rsplit("/", 1)[0])
        _mkdir(at)
        _mkdir(_app_folder(root, app))
        part = path + PART
        with open(part, "wb") as f:
            f.write(secret)
        try:
            os.remove(path)
        except OSError:
            pass
        os.rename(part, path)
    return _hex(c.x25519(secret))


def public(root, app, name):
    """The public key of app `app`'s pair `name` (hex), None where none was
    made."""
    secret = _secret(_path(root, app, name))
    return _hex(_crypto().x25519(secret)) if secret is not None else None


def shared(root, app, name, peer):
    """X25519 of app `app`'s secret `name` and the public key `peer` (hex):
    the shared secret (hex), None where no pair is under `name` or `peer` is
    of small order."""
    path = _path(root, app, name)
    other = _unhex(peer)
    secret = _secret(path)
    if secret is None:
        return None
    try:
        return _hex(_crypto().x25519(secret, other))
    except ValueError:  # a peer of small order
        return None


def forget(root, app):
    """Every key of app `app` deleted, and its folder."""
    at = _app_folder(root, app)
    try:
        names = os.listdir(at)
    except OSError:
        return
    for n in names:
        try:
            os.remove(at + "/" + n)
        except OSError:
            pass
    try:
        os.rmdir(at)
    except OSError:
        pass
