"""`custom`: the theme a change in Appearance makes (docs/theming_2026-09.md
§9 phase 6 step 5, §4.9, §4.10).

The look (`appearance.Appearance.change`) holds one `Custom` while `custom`
is the theme being edited: the package's inputs and the bytes it answers.

  look     the manifest's `look`: `from` (the source theme's name, absent
           once the file as saved is the source), `accent` (an accent's A)
           and `tint` (a tint's name), each absent for the source's own
  src      the source's bytes when `from` is absent, taken once, so a
           change re-derives from them and never from its own last result
  over     the keys a change set over the source's: `frame`, `fonts`,
           `wallpapers` and `images`, each a value or DROP
  theme    custom's theme.json as derived
  files    the files beside it the package carries (fonts, licences, picture)
  pending  the package's files not yet written, name -> bytes
  stale    the files the package no longer names, removed at the save
  fresh    the folder is replaced at the save (a change started from
           another theme)

The colours are `moy_ui.theme_custom` of the source (C, phase 6 step 4); the
keys of `over` are set on its answer here. A wallpaper cart is
`"wallpapers": {"desk": id}`, a picture `"images": {"desk": "desk.moyimg"}`
with the file in the folder, and NONE `"wallpapers": {}`: the theme names
its wallpapers and names none, so the desk is `desktop`'s fill. A theme
naming neither key leaves the desk to the console's default.
"""

import json

CUSTOM = "custom"
PICTURE_FILE = "desk.moyimg"
MANIFEST = "manifest.json"
OVER_KEYS = ("frame", "fonts", "wallpapers", "images")
DROP = object()


class Custom:
    def __init__(self, look, src=None, over=None, fresh=False):
        self.look = look
        self.src = src
        self.over = over if over is not None else {}
        self.theme = None
        self.files = set()
        self.pending = {}
        self.stale = set()
        self.fresh = fresh
        self.font_moved = False
        self.wall_moved = False
        self.dirty = False

    def put(self, name, data):
        self.pending[name] = data
        self.stale.discard(name)

    def drop(self, name):
        self.pending.pop(name, None)
        if not self.fresh:
            self.stale.add(name)


def dumps(d):
    try:
        return json.dumps(d, separators=(",", ":")).encode()
    except TypeError:  # pragma: no cover - a json without separators
        return json.dumps(d).encode()


def overlay(src, over):
    """theme.json bytes `src` with the keys of `over` set (DROP removes)."""
    if not over:
        return src
    d = json.loads(src)
    for k in over:
        v = over[k]
        if v is DROP:
            d.pop(k, None)
        else:
            d[k] = v
    return dumps(d)


def minified(text):
    """JSON text `text` as `dumps` writes it, or None when it is not JSON."""
    try:
        return dumps(json.loads(text)).decode()
    except ValueError:
        return None


def derived_of(src):
    """theme.json bytes `src` but the keys in OVER_KEYS, as a dict: what
    the derivation writes (the colours, the source's metrics, text effects,
    surfaces and layout). None when it is not a JSON object."""
    try:
        d = json.loads(src)
    except ValueError:
        return None
    if type(d) is not dict:
        return None
    for k in OVER_KEYS:
        d.pop(k, None)
    return d


def keys_of(src):
    """{key: value} of `src`'s keys in OVER_KEYS, an absent one DROP."""
    try:
        d = json.loads(src)
    except ValueError:
        d = None
    if type(d) is not dict:
        d = {}
    return {k: d.get(k, DROP) for k in OVER_KEYS}


def manifest(look):
    return dumps({"format": "moy-1", "title": "Custom", "runtime": "theme",
                  "main": "theme.json",
                  "moybyte": {"type": "theme", "look": look}})


def read_look(src):
    """The manifest's `look`, read strictly: `from` a name, `accent` an int,
    `tint` a name; anything else of the three reads as absent, and a
    manifest that is not one reads as {}."""
    try:
        d = json.loads(src)
        got = d["moybyte"]["look"]
    except (ValueError, KeyError, TypeError):
        return {}
    if type(got) is not dict:
        return {}
    look = {}
    for k, kind in (("from", str), ("accent", int), ("tint", str)):
        v = got.get(k)
        if type(v) is kind:
            look[k] = v
    return look


def wall_keys(src):
    """(cart, image, said) of theme.json bytes `src`: the wallpaper cart its
    `wallpapers` names for the desk, the file its `images` names, and
    whether it names its wallpapers at all (NONE is `"wallpapers": {}`)."""
    if not src:
        return (None, None, False)
    try:
        d = json.loads(src)
    except ValueError:
        return (None, None, False)
    if type(d) is not dict:
        return (None, None, False)
    w = d.get("wallpapers")
    i = d.get("images")
    cart = w.get("desk") if type(w) is dict else None
    img = i.get("desk") if type(i) is dict else None
    return (cart if type(cart) is str and cart else None,
            img if type(img) is str and img else None,
            type(w) is dict or type(i) is dict)


def font_files(fonts):
    """The files a `fonts` value names, each once, with each X.fnt's
    licence X.txt beside it."""
    out = []
    if type(fonts) is dict:
        for role in fonts:
            f = fonts[role]
            if type(f) is str and f not in out:
                out.append(f)
                if f.endswith(".fnt"):
                    out.append(f[:-4] + ".txt")
    return out
