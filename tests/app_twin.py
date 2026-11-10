"""The app ABI's C rows as the Python they replaced: the oracle
tests/test_moy_app.py holds native/moy_app's bindings to (the ctypes one on
CPython, the native module on the desktop MicroPython).

This is what `runtime/shell/app_context.py`'s `Damage`, `Surface`, `Theme` and
`Prefs`, `runtime/toolkit/widgets.py`'s `Clipboard`, the ArtworkService's open picture
and `runtime/shell/system_api.py`'s grant policy did before sprint 5 steps 4 to 6
moved them to C, reshaped only where the design moved the line
(native/moy_app/README.md): a grant table keyed by the app's id,
the role mask checked on every call, the damage flags the frame gate takes, each
grant's surface row and the pointer it is offset from, the live token table
under its generation, Paint's open picture in its settings rows, a clipboard of
at most CLIP_MAX bytes of text, and the key a cart's grant is made under (its
id, else its title's slug, ASCII only); and the binding of the rows the console
serves in Python, which check the grant, count the call and call the server
with the grant first. It runs on CPython and on MicroPython alike, and
nothing in the runtime imports it.
"""

import json

CLIP_MAX = 4096
SLOTS = 32
ID_MAX = 63
KEY_MAX = 127
_ROLES = ("damage", "surface", "theme", "files", "carts", "nav", "prefs",
          "notify", "wallpaper", "artwork", "clipboard", "install", "ui", "moybyte")
# The roles every grant holds (moy_app.h's MOY_ROLE_FLOOR).
_FLOOR = ("moybyte",)
_ROWS = ("damage.all", "damage.again",
         "surface.canvas", "surface.size", "surface.font_scale",
         "surface.chrome_scale", "surface.windowed", "surface.bar_h",
         "surface.content_rect", "surface.pointer",
         "theme.colors", "theme.token", "theme.gen", "theme.light", "theme.name",
         "theme.variant",
         "files.readable", "files.ready", "files.begin", "files.end", "files.list",
         "files.count", "files.load", "files.save", "files.delete",
         "files.duplicate", "files.rename", "files.new_name", "files.trash_list",
         "files.restore", "files.empty_trash", "files.history", "files.history_ops",
         "files.history_commit", "files.encode_image", "files.decode_image",
         "files.decode_cover", "files.encode_cover", "files.sig", "files.stamp",
         "files.encode_text", "files.decode_text", "files.provenance",
         "prefs.get", "prefs.set",
         "prefs.clear", "wallpaper.load_copy", "wallpaper.save_copy",
         "artwork.current", "artwork.follow", "clipboard.put_text", "clipboard.text",
         "clipboard.kind", "clipboard.seq",
         # the toolkit's rows: a compiled app's, served by native/moy_ui and
         # counted by nothing here
         "ui.page", "ui.label", "ui.button", "ui.toggle", "ui.slider", "ui.list",
         "ui.part", "ui.focus", "ui.keys_taken", "ui.at", "ui.group", "ui.info", "ui.link",
         "ui.cycle", "ui.seg", "ui.swatches", "ui.hints", "ui.icon", "ui.progress",
         "ui.table", "ui.scroll", "ui.field", "ui.dialog", "ui.tabs", "ui.grid", "ui.card", "ui.popover",
         "ui.menu", "ui.tool", "ui.bar", "ui.rule", "ui.text_view", "ui.table_part",
         "ui.part_at", "ui.split", "ui.table_dist", "ui.table_floor", "ui.held",
         "ui.slots", "ui.slot", "ui.slot_next", "ui.held_last",
         "ui.held_all",
         # the kernel's messages, notifications and badges, items handed,
         # timers, alarms and log (native/moy_kernel/moy_evq.h, moy_notes.h,
         # moy_items.h, moy_log.h) and
         # its tiers' services, counted by nothing here
         "moybyte.notify", "moybyte.send", "moybyte.award", "moybyte.progress",
         "moybyte.on", "moybyte.every", "moybyte.after",
         "moybyte.cancel", "moybyte.log", "moybyte.recv",
         "moybyte.item_read", "moybyte.item_write", "moybyte.at",
         "moybyte.service_stop", "moybyte.service_running",
         # the crypto rows: native/moy_net/moy_crypto.h's, and the device
         # handles (native/moy_kernel/moy_dev.h), counted by nothing here
         "moybyte.random", "moybyte.sha256", "moybyte.hmac_sha256",
         "moybyte.aes_gcm_seal", "moybyte.aes_gcm_open", "moybyte.x25519",
         "moybyte.dev_open", "moybyte.dev_close", "moybyte.dev_read", "moybyte.dev_write",
         "moybyte.dev_xfer", "moybyte.dev_set", "moybyte.dev_get",
         "moybyte.ed25519_public", "moybyte.ed25519_sign", "moybyte.ed25519_verify",
         # the bar's status items (native/moy_kernel/moy_status.h), counted
         # by nothing here
         "moybyte.status_show", "moybyte.status_state", "moybyte.status_remove",
         # the crypto rows appended after them
         "moybyte.aes_ctr", "moybyte.aes_ccm_seal", "moybyte.aes_ccm_open")
_TOKENS = ("panel", "edge", "title", "title_ink", "accent", "hilite", "dim",
           "desktop", "desktop_pattern", "surface", "surface_alt", "ink",
           "ink_dim", "border", "selection", "selection_ink", "focus", "play",
           "author", "danger", "surface_light", "bar", "bar_edge", "bar_light",
           "chrome_ink", "chrome_ink_dim", "title_active", "title_inactive",
           "shade_hi", "shade_lo", "shade_dark", "ink_accent",
           "band_light", "value", "value_ink", "sunk", "lcd", "lcd_ink")
_FLAGS = ("surface_light", "bar_light", "band_light")
NAME_MAX = 31
DOC_MAX = 255
# The shared folders a files grant is made for, each by its name and its own
# kind; a grant reaches every kind in its folder (moy_store_base.FILE_KIND_DIRS).
_FOLDERS = ("documents", "pictures", "music", "recordings")
_KINDS = ("docs", "drawings", "music", "recordings")
_KIND_FOLDER = {"docs": "documents", "stories": "documents", "drawings": "pictures",
                "sprites": "pictures", "music": "music", "recordings": "recordings"}
_PERMS = (("appearance", "theme"), ("files", "files"), ("launch", "nav"),
          ("prefs", "prefs"), ("clipboard", "clipboard"))
_ALL, _AGAIN = 1, 2
_ENOSPC = 28


def roles():
    return _ROLES


def rows():
    return _ROWS


def kinds():
    return _KINDS


def perms():
    return _PERMS


# The crypto rows, as native/moy_net/moy_crypto.h answers them: CPython's
# hashlib, hmac and pyca's cryptography the oracle, imported where called.
_RANDOM_MAX = 4096


def _bytes(o):
    return o.encode("utf-8", "surrogateescape") if isinstance(o, str) else bytes(o)


def random(n):
    if not 0 <= n <= _RANDOM_MAX:
        raise ValueError("a length the call refuses")
    import os
    return os.urandom(n)


def sha256(data):
    import hashlib
    return hashlib.sha256(_bytes(data)).digest()


def hmac_sha256(key, data):
    import hmac
    return hmac.new(_bytes(key), _bytes(data), "sha256").digest()


def _gcm(key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _bytes(key)
    if len(key) not in (16, 24, 32):
        raise ValueError("a length the call refuses")
    return AESGCM(key)


def aes_gcm_seal(key, plain):
    import os
    nonce = os.urandom(12)
    return nonce + _gcm(key).encrypt(nonce, _bytes(plain), None)


def aes_gcm_open(key, sealed):
    from cryptography.exceptions import InvalidTag
    g, sealed = _gcm(key), _bytes(sealed)
    if len(sealed) < 28:
        return None
    try:
        return g.decrypt(sealed[:12], sealed[12:], None)
    except InvalidTag:
        return None


def _aes_key(key):
    key = _bytes(key)
    if len(key) not in (16, 24, 32):
        raise ValueError("a length the call refuses")
    return key


def aes_ctr(key, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key, data = _aes_key(key), _bytes(data)
    if len(data) < 16:
        raise ValueError("a length the call refuses")
    e = Cipher(algorithms.AES(key), modes.CTR(data[:16])).encryptor()
    return e.update(data[16:]) + e.finalize()


def _ccm(key, tag):
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    key = _aes_key(key)
    if tag not in (4, 6, 8, 10, 12, 14, 16):
        raise ValueError("a length the call refuses")
    return AESCCM(key, tag_length=tag)


def aes_ccm_seal(key, data, tag):
    c, data = _ccm(key, tag), _bytes(data)
    if len(data) < 13:
        raise ValueError("a length the call refuses")
    return c.encrypt(data[:13], data[13:], None)


def aes_ccm_open(key, sealed, tag):
    from cryptography.exceptions import InvalidTag
    c, sealed = _ccm(key, tag), _bytes(sealed)
    if len(sealed) < 13 + tag:
        return None
    try:
        return c.decrypt(sealed[:13], sealed[13:], None)
    except InvalidTag:
        return None


def x25519(secret, peer=None):
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import x25519 as cx
    secret = _bytes(secret)
    if len(secret) != 32 or (peer is not None and len(_bytes(peer)) not in (0, 32)):
        raise ValueError("a length the call refuses")
    k = cx.X25519PrivateKey.from_private_bytes(secret)
    if not peer:
        return k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    try:
        return k.exchange(cx.X25519PublicKey.from_public_bytes(_bytes(peer)))
    except ValueError:
        raise ValueError("a peer key of small order")


def _ed25519_key(secret):
    from cryptography.hazmat.primitives.asymmetric import ed25519 as ced
    secret = _bytes(secret)
    if len(secret) != 32:
        raise ValueError("a length the call refuses")
    return ced.Ed25519PrivateKey.from_private_bytes(secret)


def ed25519_public(secret):
    from cryptography.hazmat.primitives import serialization as ser
    return _ed25519_key(secret).public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)


def ed25519_sign(secret, msg):
    return _ed25519_key(secret).sign(_bytes(msg))


def ed25519_verify(pub, msg, sig):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519 as ced
    pub, sig = _bytes(pub), _bytes(sig)
    if len(pub) != 32 or len(sig) != 64:
        raise ValueError("a length the call refuses")
    try:
        ced.Ed25519PublicKey.from_public_bytes(pub).verify(sig, _bytes(msg))
    except (InvalidSignature, ValueError):
        return False
    return True


def tokens():
    return _TOKENS


class Pointer(tuple):
    """surface.pointer()'s answer: five numbers, by name as by index."""

    x = property(lambda self: self[0])
    y = property(lambda self: self[1])
    down = property(lambda self: self[2])
    click = property(lambda self: self[3])
    visible = property(lambda self: self[4])


class App:
    def __init__(self, settings):
        self._rows = settings
        self._grants = {}           # handle -> [id, run, roles, kind, ns]
        self._gen = [1] * SLOTS
        self._damage = 0
        self._clip = None
        self._seq = 0
        self._counts = [0] * len(_ROWS)
        self._surf = {}             # handle -> (canvas row, w, h, ox, oy, content, fs, cs, win)
        self._canvases = [None] * SLOTS
        self._ptr = None
        self._servers = {}
        self._served = {}
        self._tokens = {}
        self._near = {}             # what a RUN grant is answered
        self._names = ["", ""]      # theme, variant
        self._tgen = 0
        self._colors = {}           # run -> (the colour dict, its generation)
        self._store = (None, False, False, None)    # root, readable, writable, no_store
        self._sessions = 0
        self._copy_gen = 0

    def store_bind(self, root, readable, writable, no_store):
        readable = root is not None and bool(readable)
        self._store = (root, readable, readable and bool(writable), no_store)

    def files_end_all(self):
        n, self._sessions = self._sessions, 0
        return n

    def copy_gen(self):
        return self._copy_gen

    def grant(self, app_id, roles, kind=None, ns=None, run=False, owner=0):
        if not isinstance(app_id, str):
            raise TypeError("a str")
        mask = []
        for r in roles:
            if r not in _ROLES:
                raise ValueError("unknown app context role: " + str(r))
            if r not in mask:
                mask.append(r)
        if kind is not None and kind not in _KINDS:
            raise ValueError("unknown files kind")
        ns = app_id if ns is None else ns
        if not app_id or len(app_id.encode()) > ID_MAX or len(ns.encode()) > ID_MAX:
            raise ValueError("an argument the role refuses")
        for h, g in self._grants.items():
            if g[0] == app_id and g[1] == bool(run) and g[5] == owner:
                self._grants[h] = [app_id, bool(run), set(mask), kind, ns, owner]
                return h
        for slot in range(SLOTS):
            if not any((h & 0xFF) == slot for h in self._grants):
                h = self._gen[slot] << 12 | 12 << 8 | slot
                self._grants[h] = [app_id, bool(run), set(mask), kind, ns, owner]
                self._surf[h] = (0, 0, 0, 0, 0, (0, 0, 0, 0), 0, 0, False)
                return h
        raise OSError(_ENOSPC, "the grant table is full")

    def regrant(self, app_id, roles, svc, kind=None):
        mask, smask = set(), set()
        for got, rs in ((mask, roles), (smask, svc)):
            for r in rs:
                if r not in _ROLES:
                    raise ValueError("unknown app context role: " + str(r))
                got.add(r)
        if kind is not None and kind not in _KINDS:
            raise ValueError("unknown files kind")
        if not app_id or len(app_id.encode()) > ID_MAX:
            raise ValueError("an argument the role refuses")
        got = 0
        for g in self._grants.values():
            if g[0] == app_id and g[1]:
                g[2] = mask | {"surface", "theme"} if "surface" in g[2] else set(smask)
                g[3] = kind
                got += 1
        return got

    def end(self, h):
        if h not in self._grants:
            return False
        del self._grants[h]
        slot = h & 0xFF
        self._gen[slot] = self._gen[slot] + 1 if self._gen[slot] < (1 << 18) - 1 else 1
        return True

    def count(self):
        return len(self._grants)

    def damage_take(self):
        d, self._damage = self._damage, 0
        return d

    def damage_drop(self):
        self._damage &= ~_ALL

    def surface_write(self, canvas, w, h, fs, cs, windowed, content, ox=0, oy=0):
        row = getattr(canvas, "_crow", None)
        ch = getattr(row, "h", 0) if row is not None else 0
        for g, grant in self._grants.items():
            if "surface" in grant[2]:
                self._surf[g] = (ch, w, h, ox, oy, tuple(content), fs, cs,
                                 bool(windowed))
                self._canvases[g & 0xFF] = canvas

    def bind_pointer(self, p):
        self._ptr = p
        return p is not None

    def theme_write(self, name, variant, colors, near=None):
        def table(d):
            tok = {}
            for k, v in d.items():
                if k not in _TOKENS:
                    raise ValueError("a token outside the vocabulary")
                tok[k] = int(v)
            return tok
        tok = table(colors)
        near_tok = tok if near is None else table(near)
        if len(name.encode()) > NAME_MAX or len(variant.encode()) > NAME_MAX:
            raise ValueError("an argument the role refuses")
        self._tokens = tok
        self._near = near_tok
        self._names[0], self._names[1] = name, variant
        self._tgen += 1
        return self._tgen

    def serve(self, role, server):
        self._servers[role] = server

    def served(self):
        return dict(self._served)

    def grant_id(self, h):
        g = self._grants.get(h)
        if g is None:
            raise ValueError("a grant that ended")
        return g[0]

    def _serve(self, h, role, verb):
        self._hold(h, role)
        server = self._servers.get(role)
        if server is None:
            raise ValueError("no server for the role")
        key = role + "." + verb
        self._served[key] = self._served.get(key, 0) + 1
        return getattr(server, verb)

    def counts(self):
        return tuple(self._counts)

    def _hold(self, h, role, row=None):
        if row is not None:
            self._counts[_ROWS.index(row)] += 1
        g = self._grants.get(h)
        if g is None:
            raise ValueError("a grant that ended")
        if role not in g[2] and role not in _FLOOR:
            raise ValueError("a role the grant does not hold")
        return g


class _Role:
    def __init__(self, app, g):
        if not isinstance(app, App):
            raise TypeError("an App")
        self._app, self._g = app, g


class Damage(_Role):
    def all(self):
        self._app._hold(self._g, "damage", "damage.all")
        self._app._damage |= _ALL

    def again(self):
        self._app._hold(self._g, "damage", "damage.again")
        self._app._damage |= _AGAIN


class Surface(_Role):
    def _row(self, row):
        self._app._hold(self._g, "surface", row)
        return self._app._surf[self._g]

    def canvas(self):
        self._row("surface.canvas")
        return self._app._canvases[self._g & 0xFF]

    def size(self):
        r = self._row("surface.size")
        return (r[1], r[2])

    def font_scale(self):
        return self._row("surface.font_scale")[6]

    def chrome_scale(self):
        return self._row("surface.chrome_scale")[7]

    def windowed(self):
        return self._row("surface.windowed")[8]

    def bar_h(self):
        return self._row("surface.bar_h")[5][1]

    def content_rect(self):
        return self._row("surface.content_rect")[5]

    def pointer(self):
        r = self._row("surface.pointer")
        p = self._app._ptr
        if p is None:
            return None
        return Pointer((p.x - r[3], p.y - r[4], bool(p.down), bool(p.click),
                        bool(p.visible)))


class Theme(_Role):
    def colors(self):
        app = self._app
        app._hold(self._g, "theme", "theme.colors")
        run = app._grants[self._g][1]
        have = app._colors.get(run) if app._colors else None
        if have is not None and have[1] == app._tgen:
            return have[0]
        src = app._near if run else app._tokens
        d = {}
        for k in _TOKENS:
            if k in src:
                v = src[k]
                d[k] = bool(v) if k in _FLAGS else v
        app._colors[run] = (d, app._tgen)
        return d

    def token(self, role):
        self._app._hold(self._g, "theme", "theme.token")
        role = int(role)
        if not 0 <= role < len(_TOKENS):
            raise ValueError("an argument the role refuses")
        app = self._app
        return (app._near if app._grants[self._g][1] else app._tokens).get(_TOKENS[role])

    def gen(self):
        self._app._hold(self._g, "theme", "theme.gen")
        return self._app._tgen

    def light(self):
        self._app._hold(self._g, "theme", "theme.light")
        return bool(self._app._tokens.get("surface_light", 0))

    def name(self):
        self._app._hold(self._g, "theme", "theme.name")
        return self._app._names[0]

    def variant(self):
        self._app._hold(self._g, "theme", "theme.variant")
        return self._app._names[1]

    def set(self, *a, **kw):
        return self._app._serve(self._g, "theme", "set")(self._g, *a, **kw)

    def set_variant(self, *a, **kw):
        return self._app._serve(self._g, "theme", "set_variant")(self._g, *a, **kw)

    def change(self, *a, **kw):
        return self._app._serve(self._g, "theme", "change")(self._g, *a, **kw)

    def choices(self, *a, **kw):
        return self._app._serve(self._g, "theme", "choices")(self._g, *a, **kw)

    def set_chrome_scale(self, *a, **kw):
        return self._app._serve(self._g, "theme", "set_chrome_scale")(self._g, *a, **kw)

    def edit_file(self, *a, **kw):
        return self._app._serve(self._g, "theme", "edit_file")(self._g, *a, **kw)

    def name_custom(self, *a, **kw):
        return self._app._serve(self._g, "theme", "name_custom")(self._g, *a, **kw)

    def name_check(self, *a, **kw):
        return self._app._serve(self._g, "theme", "name_check")(self._g, *a, **kw)


class Prefs(_Role):
    def _key(self, g, key):
        if not isinstance(key, str):
            raise TypeError("a str")
        k = g[4] + "_" + key
        if not key or len(k.encode()) > KEY_MAX:
            raise ValueError("an argument the role refuses")
        return k

    def get(self, key, default=None):
        g = self._app._hold(self._g, "prefs", "prefs.get")
        k = self._key(g, key)
        rows = self._app._rows
        t = None if rows is None else rows.text(k)
        return default if t is None else json.loads(t)

    def set(self, key, value):
        g = self._app._hold(self._g, "prefs", "prefs.set")
        k = self._key(g, key)
        text = json.dumps(value)
        if self._app._rows is not None:
            self._app._rows.set_text(k, text)

    def clear(self, key):
        g = self._app._hold(self._g, "prefs", "prefs.clear")
        k = self._key(g, key)
        if self._app._rows is not None:
            self._app._rows.delete(k)


class Clipboard(_Role):
    def put_text(self, text):
        self._app._hold(self._g, "clipboard", "clipboard.put_text")
        s = str(text)
        if len(s.encode()) > CLIP_MAX:
            return False
        self._app._clip = s
        self._app._seq += 1
        return True

    def text(self):
        self._app._hold(self._g, "clipboard", "clipboard.text")
        return self._app._clip or ""

    def kind(self):
        self._app._hold(self._g, "clipboard", "clipboard.kind")
        return None if self._app._clip is None else "text"

    def seq(self):
        self._app._hold(self._g, "clipboard", "clipboard.seq")
        return self._app._seq


def _read(perms):
    roles, kinds = [], []
    for perm in perms or ():
        perm = str(perm)
        colon = perm.find(":")
        head = perm[:colon] if colon >= 0 else perm
        role = None
        for p, r in _PERMS:
            if p == head:
                role = r
        if role is None:
            continue
        if role == "files":
            folder = "documents" if colon < 0 else perm[colon + 1:]
            if folder in _FOLDERS and folder not in kinds:
                kinds.append(folder)
            continue
        if role not in roles:
            roles.append(role)
    return roles, kinds


def policy(perms):
    roles, kinds = _read(perms)
    if len(kinds) == 1:
        roles.append("files")
    return (tuple(r for r in _ROLES if r in roles),
            _KINDS[_FOLDERS.index(kinds[0])] if len(kinds) == 1 else None)


def manifest_error(perms):
    _roles, kinds = _read(perms)
    if len(kinds) > 1:
        return ("manifest asks for %d file folders (%s) - pick one"
                % (len(kinds), ", ".join(kinds)))
    return None


def id_for(cart_id, title):
    if isinstance(cart_id, str) and cart_id:
        return cart_id[:ID_MAX]
    out = ""
    for ch in (title if isinstance(title, str) and title else "app"):
        if "A" <= ch <= "Z":
            out += chr(ord(ch) + 32)
        elif "a" <= ch <= "z" or "0" <= ch <= "9":
            out += ch
        elif ch in " -_":
            out += "_"
    return out[:ID_MAX] or "cart"


class Artwork(_Role):
    """Paint's open picture: the rows paint_doc_kind and paint_doc."""

    def _rows_text(self, key):
        rows = self._app._rows
        t = None if rows is None else rows.text("paint_" + key)
        v = None if t is None else json.loads(t)
        return v if isinstance(v, str) else None

    def current(self):
        self._app._hold(self._g, "artwork", "artwork.current")
        name = self._rows_text("doc")
        kind = self._rows_text("doc_kind") or "drawings"
        if name is None or len(name.encode()) > DOC_MAX or len(kind.encode()) > DOC_MAX:
            return None
        return (kind, name)

    def follow(self, kind, old, new):
        self._app._hold(self._g, "artwork", "artwork.follow")
        for x in (kind, old, new):
            if not isinstance(x, str):
                raise TypeError("a str")
        if (not new or len(new.encode()) > DOC_MAX or len(kind.encode()) > DOC_MAX
                or len(old.encode()) > DOC_MAX
                or any(ord(ch) < 0x20 for ch in new)):
            raise ValueError("an argument the role refuses")
        name = self._rows_text("doc")
        if name != old or (self._rows_text("doc_kind") or "drawings") != kind:
            return False
        self._app._rows.set_text("paint_doc", json.dumps(new))
        return True


# The user-files rows: the grant and the kind it was made for checked, the row
# counted, the store's readiness checked, then the layer's verb over the bound
# root -- the layer itself being native/moy_store's, which
# tests/test_userfiles_parity.py holds to the Python it replaced.

def _layer():
    import moy_ufiles
    return moy_ufiles


class Files(_Role):
    def _row(self, row, kind=None, need="read"):
        """(True, None) when the row may run, else (False, its `err`)."""
        app = self._app
        g = app._hold(self._g, "files", "files." + row)
        if (kind is not None and g[3] is not None
                and _KIND_FOLDER.get(kind) != _KIND_FOLDER[g[3]]):
            raise ValueError("a role the grant does not hold")
        root, readable, writable, no_store = app._store
        if root is None or (need == "read" and not readable) or (
                need == "write" and not writable):
            return (False, no_store)
        return (True, None)

    def _op(self, row, kind, need, fn, *args):
        ok, why = self._row(row, kind, need)
        if not ok:
            return (None, why)
        try:
            return (fn(*(args + (self._app._store[0],))), None)
        except ValueError:
            return (None, "a refused argument")
        except MemoryError:
            return (None, "memory allocation failed")
        except OSError as e:
            return (None, str(e))

    def readable(self):
        self._row("readable", need=None)
        return self._app._store[1]

    def ready(self):
        self._row("ready", need=None)
        return self._app._store[2]

    def begin(self):
        ok, why = self._row("begin", need="write")
        if not ok:
            return (None, why)
        self._app._sessions += 1
        return (True, None)

    def end(self):
        self._app._hold(self._g, "files", "files.end")
        if self._app._sessions:
            self._app._sessions -= 1

    def list(self, kind):
        return self._op("list", kind, "read", _layer().list_files, kind)

    def count(self, kind):
        return self._op("count", kind, "read", _layer().count_files, kind)

    def load(self, kind, name):
        return self._op("load", kind, "read", _layer().load_file, kind, name)

    def save(self, kind, name, blob):
        return self._op("save", kind, "write", _layer().save_file, kind, name, blob)

    def delete(self, kind, name):
        return self._op("delete", kind, "write", _layer().delete_file, kind, name)

    def duplicate(self, kind, name):
        return self._op("duplicate", kind, "write", _layer().duplicate_file, kind, name)

    def rename(self, kind, name, new):
        return self._op("rename", kind, "write", _layer().rename_file, kind, name, new)

    def new_name(self, kind, title=None):
        uf = _layer()
        if title:
            return self._op("new_name", kind, "read", uf.free_file_name, kind, title)
        return self._op("new_name", kind, "read", uf.new_file_name, kind)

    def trash_list(self):
        return self._op("trash_list", None, "read", _layer().trash_list)

    def restore(self, kind, name):
        return self._op("restore", kind, "write", _layer().restore_file, kind, name)

    def empty_trash(self):
        return self._op("empty_trash", None, "write", _layer().empty_trash)

    def history(self, kind, name):
        return self._op("history", kind, "read", _layer().load_history, kind, name)

    def history_ops(self, kind, name):
        return self._op("history_ops", kind, "read", _layer().history_ops, kind, name)

    def history_commit(self, kind, name, ops, keyframe=None):
        uf = _layer()
        return self._op("history_commit", kind, "write",
                        lambda k, n, root: uf.history_commit(k, n, ops, keyframe, root),
                        kind, name)

    # -- the codecs: their value, or None with no layer bound

    def _codec(self, row):
        return self._row(row, need="layer")[0]

    def encode_image(self, w, h, indices):
        if not self._codec("encode_image"):
            return None
        return _layer().encode_image(w, h, indices)

    def decode_image(self, blob):
        if not blob:
            self._app._hold(self._g, "files")
            return None
        return _layer().decode_image(blob) if self._codec("decode_image") else None

    def decode_cover(self, blob):
        if not blob:
            self._app._hold(self._g, "files")
            return None
        return _layer().decode_cover(blob) if self._codec("decode_cover") else None

    def encode_cover(self, indices):
        return _layer().encode_cover(indices) if self._codec("encode_cover") else None

    def sig(self, blob):
        return _layer().content_sig(blob or "") if self._codec("sig") else None

    def stamp(self, blob, kind, name, sig):
        if not isinstance(blob, str):
            self._app._hold(self._g, "files")
            return blob
        return _layer().stamp_provenance(blob, kind, name, sig) if self._codec("stamp") else blob

    def encode_text(self, body):
        return str(body) if self._codec("encode_text") else None

    def decode_text(self, blob):
        if not self._codec("decode_text") or not isinstance(blob, str) or not blob:
            return []
        return blob.split("\n")

    def provenance(self, blob):
        if not isinstance(blob, str) or not blob:
            self._app._hold(self._g, "files")
            return (None, None)
        if not self._codec("provenance"):
            return (None, None)
        return _layer().read_provenance(blob)


# The roles every row of which the console serves (native/os_calls.json's "shell" rows).
_SERVED = {
    "carts": ("readable ready begin end all can_journal slug create journal rescan "
              "hydrate images save_image encode_image"),
    "nav": ("open_app is_system_app projects edit open_image open_text edit_file "
            "play run_script text_mode"),
    "notify": "award groups today",
    "wallpaper": "current carts id_for title select preview thumbnail",
    "install": ("hold release fit memory chip runtimes home can_pick pick root "
                "writable op rescan free find net keep wifi wifi_open consent"),
}


def _served_role(role):
    def row(verb):
        def call(self, *a, **kw):
            return self._app._serve(self._g, role, verb)(self._g, *a, **kw)
        return call
    return type(role[0].upper() + role[1:], (_Role,),
                {v: row(v) for v in _SERVED[role].split()})


Carts = _served_role("carts")
Nav = _served_role("nav")
Notify = _served_role("notify")
Install = _served_role("install")


class Wallpaper(_served_role("wallpaper")):
    """The rows served in Python, and the copy's, over the layer."""

    def _copy(self, row, write):
        app = self._app
        app._hold(self._g, "wallpaper", "wallpaper." + row)
        root, readable, writable, no_store = app._store
        if root is None or not (writable if write else readable):
            return no_store
        return None

    def load_copy(self):
        why = self._copy("load_copy", False)
        if why is not None:
            return (None, why)
        try:
            return (_layer().load_copy(self._app._store[0]), None)
        except OSError as e:
            return (None, str(e))

    def save_copy(self, blob):
        why = self._copy("save_copy", True)
        if why is not None:
            return (None, why)
        try:
            _layer().save_copy(blob, self._app._store[0])
        except OSError as e:
            return (None, str(e))
        self._app._copy_gen += 1
        return (None, None)
