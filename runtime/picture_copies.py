"""A drawing's COPIES (#108 copy-on-use): the wallpaper and a project's
background are copies of a gallery drawing at the moment they were made, never
references, stamped with where they came from so a later edit can offer
"your drawing changed -> UPDATE".

Two apps make them -- Paint (its WALL and GAME actions on the open drawing)
and Files (WALL, GAME, the "used in" list and its UPDATE on any drawing) --
each over its OWN roles: `files` reads the drawing, `wallpaper` writes the
backdrop's copy and selects it, `carts` writes a project's `images/bg`, and
`nav` says which carts are projects. Neither reaches the other: Paint's open
picture is the `artwork` role's (`current`), and a renamed drawing is followed
there (`artwork.follow`).

`current_picture` is the third use of Paint's open picture: Storybook's "use my
painting", which reads it through `artwork.current` and `files`.
"""

try:
    from file_widgets import cover_indices
    from moy_store_base import COVER_FILE
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.file_widgets import cover_indices
    from runtime.moy_store_base import COVER_FILE


DRAWINGS = "drawings"
WALL_TITLE = "My Art"


class PictureCopies:
    """The copy-on-use verbs over one app's roles. `last_error` is the last
    refusal's kid-facing text ("" after a success)."""

    def __init__(self, ctx):
        self._files = ctx.files
        self._carts = ctx.carts
        self._wall = ctx.wallpaper
        self._nav = ctx.nav
        self.last_error = ""

    def wall_cart(self):
        """The My Art wallpaper cart, or None."""
        for cart in self._carts.all():
            if cart.get("type") == "wallpaper" and cart.get("title") == WALL_TITLE:
                return cart
        return None

    def wallpaper_id(self):
        wall = self.wall_cart()
        return self._wall.id_for(wall) if wall is not None else None

    def set_wallpaper(self, name):
        """Copy drawing `name` into the wallpaper slot (and My Art's images/bg
        for the launcher shelf) and make it the active desktop backdrop.
        Editing the drawing afterwards changes nothing until it is set
        again."""
        files = self._files
        if not files.ready():
            self.last_error = "STORAGE OFF"
            return False
        blob = files.load(DRAWINGS, name)[0] if name else None
        if not blob:
            self.last_error = "SAVE FIRST"
            return False
        stamped = files.stamp(blob, DRAWINGS, name, files.sig(blob))
        _v, err = self._wall.save_copy(stamped)
        if err is not None:
            self.last_error = str(err)
            return False
        wall = self.wall_cart()
        if wall is not None and wall.get("path"):
            _v, err = self._carts.save_image(wall, "bg", stamped)
            if err is not None:
                self.last_error = str(err)
                return False
        wp_id = self.wallpaper_id()
        if wp_id is None:
            self.last_error = "NO WALLPAPER"
            return False
        self._wall.select(wp_id)
        self.last_error = ""
        return True

    def _targets(self):
        # System-app identities (Paint itself, Files, Calc, ...) are not game
        # projects; offering them a bg copy would just be clutter.
        return [cart for cart in self._carts.all()
                if cart.get("type") in ("game", "app") and cart.get("path")
                and not self._nav.is_system_app(cart)]

    def targets(self):
        """Project titles a drawing can be copied into as a background."""
        return tuple(cart.get("title", "PROJECT") for cart in self._targets())

    def attach(self, index, name):
        """Copy drawing `name` into project `index` of `targets()` as
        `images/bg.moyimg`: the project's title, or None."""
        files = self._files
        if not files.ready():
            self.last_error = "STORAGE OFF"
            return None
        targets = self._targets()
        try:
            target = targets[int(index)]
        except (IndexError, TypeError, ValueError):
            self.last_error = "NO PROJECT"
            return None
        blob = files.load(DRAWINGS, name)[0] if name else None
        data = files.decode_image(blob)
        if not blob or data is None:
            self.last_error = "SAVE FIRST"
            return None
        # The signature is of the SOURCE file as it stands now (before any
        # resize), so change-detection compares the drawing's content_sig to it.
        sig = files.sig(blob)
        if data[0] != 320 or data[1] != 240:
            game = cover_indices(data[2], data[0], data[1], 320, 240)
            blob = files.encode_image(320, 240, game)
        _v, err = self._carts.save_image(target, "bg",
                                         files.stamp(blob, DRAWINGS, name, sig))
        if err is not None:
            self.last_error = str(err)
            return None
        self.last_error = ""
        return target.get("title", "PROJECT")

    def usage(self, name):
        """Where drawing `name` is used -- the File Manager's "used in:" list:
        rows {"label", "kind", "index", "stale"}, one per copy stamped from it
        (the wallpaper, any project bg); `stale` when the drawing changed since.
        Pull-based: a renamed or deleted source matches nothing."""
        files = self._files
        if not files.ready() or not name:
            return []
        src_key = DRAWINGS + "/" + str(name)
        src, err = files.load(DRAWINGS, name)
        if err is not None:      # an unreadable source lists nothing
            return []
        cur = files.sig(src or "")
        rows = []
        wsrc, wsig = files.provenance(self._wall.load_copy()[0])
        if wsrc == src_key:
            rows.append({"label": "WALLPAPER", "kind": "wall", "index": -1,
                         "stale": wsig != cur})
        for i, cart in enumerate(self._targets()):
            blob = (cart.get("images") or {}).get("bg")
            if not blob:
                continue
            psrc, psig = files.provenance(blob)
            if psrc == src_key:
                rows.append({"label": cart.get("title", "PROJECT"),
                             "kind": "game", "index": i, "stale": psig != cur})
        return rows

    def resend(self, row, name):
        """Re-copy drawing `name` to one usage row (its UPDATE). True on
        success."""
        if not isinstance(row, dict) or not name:
            return False
        if row.get("kind") == "wall":
            return self.set_wallpaper(name)
        return self.attach(row.get("index", -1), name) is not None


def current_picture(files, artwork):
    """Paint's open picture as `(w, h, indices)`, or None: the picture
    `artwork.current()` names (a cart's cover decoded as one), else the newest
    drawing."""
    cur = artwork.current()
    kind, name = cur if cur is not None else (DRAWINGS, None)
    if name is None:
        names = files.list(DRAWINGS)[0]
        if not names:
            return None
        name = names[0]
    blob, err = files.load(kind, name)
    if err is not None or not blob:
        return None
    if kind != DRAWINGS and name == COVER_FILE:
        return files.decode_cover(blob)
    return files.decode_image(blob)
