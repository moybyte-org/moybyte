"""The app ABI's C rows as the Python they replaced: the oracle
tests/test_moy_app.py holds native/moy_app's bindings to (the ctypes one on
CPython, the native module on the desktop MicroPython).

This is what `runtime/app_context.py`'s `Damage`, `Surface`, `Theme` and
`Prefs`, `runtime/widgets.py`'s `Clipboard` and `runtime/system_api.py`'s grant
policy did before sprint 5 steps 4 and 5 moved them to C, reshaped only where
the design moved the line (docs/kernel_appabi_2026-10.md section 2): a grant
table keyed by the app's id, the role mask checked on every call, the damage
flags the frame gate takes, each grant's surface row and the pointer it is
offset from, the live token table under its generation, a clipboard of at most
CLIP_MAX bytes of text, and the key a cart's grant is made under (its id, else
its title's slug, ASCII only). It runs on CPython and on MicroPython alike, and
nothing in the runtime imports it.
"""

import json

CLIP_MAX = 4096
SLOTS = 32
ID_MAX = 63
KEY_MAX = 127
_ROLES = ("damage", "surface", "theme", "files", "carts", "nav", "prefs",
          "notify", "wallpaper", "artwork", "clipboard", "install")
_ROWS = ("damage.all", "damage.again",
         "surface.canvas", "surface.size", "surface.font_scale",
         "surface.chrome_scale", "surface.windowed", "surface.bar_h",
         "surface.pointer",
         "theme.colors", "theme.token", "theme.gen", "theme.light", "theme.name",
         "theme.variant", "theme.skin",
         "prefs.get", "prefs.set",
         "prefs.clear", "clipboard.put_text", "clipboard.text",
         "clipboard.kind", "clipboard.seq")
_TOKENS = ("panel", "edge", "title", "title_ink", "accent", "hilite", "dim",
           "desktop", "desktop_pattern", "surface", "surface_alt", "ink",
           "ink_dim", "border", "selection", "selection_ink", "focus", "play",
           "author", "danger", "surface_light", "bar", "bar_edge", "bar_light",
           "chrome_ink", "chrome_ink_dim", "title_active", "title_inactive")
_FLAGS = ("surface_light", "bar_light")
NAME_MAX = 31
_KINDS = ("docs", "drawings", "sprites", "music")
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
        self._surf = {}             # handle -> (canvas row, w, h, ox, oy, bar_h, fs, cs, win)
        self._canvases = [None] * SLOTS
        self._ptr = None
        self._servers = {}
        self._served = {}
        self._tokens = {}
        self._names = ["", "", ""]  # theme, variant, skin
        self._tgen = 0
        self._colors = None
        self._colors_gen = 0

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
            if g[0] == app_id and g[1] == bool(run):
                self._grants[h] = [app_id, bool(run), set(mask), kind, ns]
                return h
        for slot in range(SLOTS):
            if not any((h & 0xFF) == slot for h in self._grants):
                h = self._gen[slot] << 12 | 12 << 8 | slot
                self._grants[h] = [app_id, bool(run), set(mask), kind, ns]
                self._surf[h] = (0, 0, 0, 0, 0, 0, 0, 0, False)
                return h
        raise OSError(_ENOSPC, "the grant table is full")

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

    def surface_write(self, canvas, w, h, fs, cs, windowed, bar_h, ox=0, oy=0):
        row = getattr(canvas, "_crow", None)
        ch = getattr(row, "h", 0) if row is not None else 0
        for g, grant in self._grants.items():
            if "surface" in grant[2]:
                self._surf[g] = (ch, w, h, ox, oy, bar_h, fs, cs, bool(windowed))
                self._canvases[g & 0xFF] = canvas

    def bind_pointer(self, p):
        self._ptr = p
        return p is not None

    def theme_write(self, name, variant, colors):
        tok = {}
        for k, v in colors.items():
            if k not in _TOKENS:
                raise ValueError("a token outside the vocabulary")
            tok[k] = int(v)
        if len(name.encode()) > NAME_MAX or len(variant.encode()) > NAME_MAX:
            raise ValueError("an argument the role refuses")
        self._tokens = tok
        self._names[0], self._names[1] = name, variant
        self._tgen += 1

    def theme_write_skin(self, skin):
        if len(skin.encode()) > NAME_MAX:
            raise ValueError("an argument the role refuses")
        self._names[2] = skin
        self._tgen += 1

    def serve(self, role, server):
        self._servers[role] = server

    def served(self):
        return dict(self._served)

    def _serve(self, role, verb):
        key = role + "." + verb
        self._served[key] = self._served.get(key, 0) + 1
        return getattr(self._servers[role], verb)

    def counts(self):
        return tuple(self._counts)

    def _hold(self, h, role, row=None):
        if row is not None:
            self._counts[_ROWS.index(row)] += 1
        g = self._grants.get(h)
        if g is None:
            raise ValueError("a grant that ended")
        if role not in g[2]:
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
        return self._row("surface.bar_h")[5]

    def pointer(self):
        r = self._row("surface.pointer")
        p = self._app._ptr
        if p is None:
            return None
        return Pointer((p.x - r[3], p.y - r[4], bool(p.down), bool(p.click),
                        bool(p.visible)))

    def glyph(self, *a, **kw):
        return self._app._serve("surface", "glyph")(*a, **kw)


class Theme(_Role):
    def colors(self):
        app = self._app
        app._hold(self._g, "theme", "theme.colors")
        if app._colors is None or app._colors_gen != app._tgen:
            d = {}
            for k in _TOKENS:
                if k in app._tokens:
                    v = app._tokens[k]
                    d[k] = bool(v) if k in _FLAGS else v
            app._colors, app._colors_gen = d, app._tgen
        return app._colors

    def token(self, role):
        self._app._hold(self._g, "theme", "theme.token")
        role = int(role)
        if not 0 <= role < len(_TOKENS):
            raise ValueError("an argument the role refuses")
        return self._app._tokens.get(_TOKENS[role])

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

    def skin(self):
        self._app._hold(self._g, "theme", "theme.skin")
        return self._app._names[2]

    def set(self, *a, **kw):
        return self._app._serve("theme", "set")(*a, **kw)

    def set_variant(self, *a, **kw):
        return self._app._serve("theme", "set_variant")(*a, **kw)

    def set_skin(self, *a, **kw):
        return self._app._serve("theme", "set_skin")(*a, **kw)


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
            kind = "docs" if colon < 0 else perm[colon + 1:]
            if kind in _KINDS and kind not in kinds:
                kinds.append(kind)
            continue
        if role not in roles:
            roles.append(role)
    return roles, kinds


def policy(perms):
    roles, kinds = _read(perms)
    if len(kinds) == 1:
        roles.append("files")
    return (tuple(r for r in _ROLES if r in roles),
            kinds[0] if len(kinds) == 1 else None)


def manifest_error(perms):
    _roles, kinds = _read(perms)
    if len(kinds) > 1:
        return ("manifest asks for %d file kinds (%s) - pick one"
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
