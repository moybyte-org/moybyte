# Map (grep -n a name to jump there):
#   shown                         a drawing's file name in words
#   GamePage                      Put in a game: the projects as a table
#   StoryPage                     Put in a story: the stories as cards
"""The pages a drawing is sent from: Put in a game and Put in a story, which
Paint and My Art both open over a drawing (docs/media/look_2026-10/
APPS_RESTYLE.md), on the public UI API's verbs. A page is drawn in the app's
frame and answers the frame its row or card is chosen."""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

try:
    import storybook_app as _stories
except ImportError:  # pragma: no cover - direct host import
    try:
        from runtime import storybook_app as _stories
    except ImportError:
        _stories = None


def dot():
    return " \u00b7 " if ui.has_symbols() else " - "


def shown(name):
    """A drawing's file name as the pages say it: in words, its first letter up."""
    words = str(name or "").replace("_", " ")
    return words[:1].upper() + words[1:]


def stories_here():
    """Whether this build carries the Storybook a drawing can go into."""
    return _stories is not None and hasattr(_stories, "add_picture_slide")


class GamePage:
    """Put in a game: the projects as a table, each its cover; Enter adds
    the drawing as the project's background (`PictureCopies.attach`) and a
    toast says where. Shared by Paint and My Art."""

    def __init__(self, copies, nav, name):
        self.copies = copies
        self.nav = nav
        self.name = name
        self.carts = copies.target_carts()
        self.cur = 0 if self.carts else -1

    def draw(self, cv, rect, desk, fs):
        """The page: its hints at the foot, then a group whose caption says
        what happens and whose rows are the projects. The title of the project
        the drawing went into, the frame it does, else None; "" where the copy
        failed (`copies.last_error`)."""
        x, y, w, h = rect
        if not desk and ui.hints_width(("back", "Back")) > 0:
            ui.at(x, y + h - 22 * fs, w, 22 * fs)
            ui.hints(("confirm", "Add", "back", "Back"))
            h -= 22 * fs
        ui.at(x + 16 * fs, y + 4 * fs, w - 32 * fs, 16 * fs)
        ui.label(shown(self.name) + (" becomes the background of\u2026" if ui.has_symbols()
                                     else " becomes the background of..."),
                 {"tone": "dim", "face": "caption"})
        ui.at(x, y + 20 * fs, w, h - 20 * fs)
        ui.scroll("games")
        ui.group("", {"card": True})
        rows = [("game", c.get("title") or "") for c in self.carts]
        cur, opened = ui.table("", ("", ""), rows, self.cur, {"id": "Games"})
        ui.group()
        ui.end()
        ui.scroll()
        self.cur = cur
        self._covers(cv, (x, y + 20 * fs, w, h - 20 * fs))
        if not self.carts:
            ui.label("No games yet: make one in Projects", {"tone": "dim"})
        if opened and 0 <= cur < len(self.carts):
            got = self.copies.attach(cur, self.name)
            return got if got else ""
        return None

    def _covers(self, cv, room):
        got = ui.table_rows()
        if got is None:
            return
        y0, rh, view, px, side = got
        vx, vy, vw, vh = view
        x1, y1 = min(vx + vw, room[0] + room[2]), min(vy + vh, room[1] + room[3])
        vx, vy = max(vx, room[0]), max(vy, room[1])
        vw, vh = max(0, x1 - vx), max(0, y1 - vy)
        r = side // 4
        cv.clip(vx, vy, vw, vh)
        for i in range(len(self.carts)):
            y = y0 + i * rh + (rh - side) // 2
            if y + side <= vy or y >= vy + vh:
                continue
            cart = self.carts[i]
            cover = self.nav.cover(cart)
            if cover:
                cv.cover(cover.pix, cover.w, 0, 0, cover.w, cover.h, px, y, side, side, r,
                         False)
            else:
                ui.initial((px, y, side, side), cart.get("title") or "", r)
        cv.clip()
        ui.table_pictures()


class StoryPage:
    """Put in a story: the stories as cards; Enter adds the drawing to the
    one chosen as a new last slide's picture (the Storybook's
    `add_picture_slide`), which a toast names and carries Undo for."""

    def __init__(self, carts, files, name):
        self.carts = carts
        self.files = files
        self.name = name
        self.stories = _stories.story_list(carts, files) if _stories is not None else []

    def draw(self, cv, rect, desk, fs):
        """The page in `rect`: (story title, slide) the drawing went into the
        frame it does, else None."""
        x, y, w, h = rect
        if not desk and ui.hints_width(("back", "Back")) > 0:
            ui.at(x, y + h - 22 * fs, w, 22 * fs)
            ui.hints(("confirm", "Add", "back", "Back"))
            h -= 22 * fs
        ui.at(x + 16 * fs, y + 4 * fs, w - 32 * fs, 16 * fs)
        ui.label(shown(self.name) + " becomes a new last slide of…"
                 if ui.has_symbols() else shown(self.name) + " becomes a new last slide of...",
                 {"tone": "dim", "face": "caption"})
        pw, ph = (256, 150) if desk else (128 * fs, 75 * fs)
        ui.at(x, y + 22 * fs, w, h - 22 * fs)
        ui.scroll("stories")
        ui.grid("stories", pw, ph, {"cols": 3 if desk else 2, "margin": 16, "top": 4,
                                    "foot": 30 if desk else 26})
        got = None
        for i, s in enumerate(self.stories):
            n = s["slides"]
            o = {"id": "Story " + s["folder"]}
            pic = s.get("cover")
            if pic is None:
                o["icon"] = "storybook"
            sub = "%d slide%s" % (n, "" if n == 1 else "s")
            cx, cy, cw, chh, st = ui.card(s["title"], sub, o)
            if cw and pic is not None:
                cv.picture(pic, cx, cy, cw, chh)
            if st & ui.ACTIVATED:
                got = i
        ui.grid()
        if not self.stories:
            ui.label("No stories yet: make one in Storybook", {"tone": "dim"})
        ui.end()
        ui.scroll()
        if got is None or _stories is None:
            return None
        return _stories.add_picture_slide(self.carts, self.files, self.stories[got], self.name)
