"""Responsive visual wallpaper + theme picker system app."""

try:
    from chrome import THEMES
except ImportError:  # pragma: no cover - direct host import
    from runtime.chrome import THEMES


class AppearanceLayout:
    def __init__(self, w, h, fs=1, windowed=False):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(fs))
        fs = self.fs
        self.bar_h = 0 if windowed else 18 * fs
        self.tabs_h = 30 * fs
        self.catalog_h = min(self.h // 2, 142 * fs)
        self.preview = (0, self.bar_h + self.tabs_h, self.w,
                        max(1, self.h - self.bar_h - self.tabs_h - self.catalog_h))
        self.catalog = (0, self.h - self.catalog_h, self.w, self.catalog_h)
        labels = ("IMAGES", "CARTS", "THEMES")
        widths = (70, 62, 70)
        self.tabs = []
        x = 6 * fs
        for i in range(3):
            bw = widths[i] * fs
            self.tabs.append((x, self.bar_h + 3 * fs, bw, self.tabs_h - 6 * fs))
            x += bw + 4 * fs

    def cards(self, count):
        fs = self.fs
        x, y, w, h = self.catalog
        gap = 6 * fs
        min_w = 116 * fs
        cols = max(1, min(count if count else 1, (w - gap) // (min_w + gap)))
        card_w = (w - gap * (cols + 1)) // cols
        rows = max(1, (count + cols - 1) // cols)
        # Divide the available band exactly. On the 320x240 tier five themes use
        # three compact rows; a fixed minimum would push the last row off-screen.
        card_h = max(1, (h - gap * (rows + 1)) // rows)
        out = []
        for i in range(count):
            out.append((x + gap + (i % cols) * (card_w + gap),
                        y + gap + (i // cols) * (card_h + gap),
                        card_w, card_h))
        return out


class AppearanceAppLayer:
    """Large live preview with visual Images/Carts/Themes catalogs."""

    id = "appearance"
    domain = "system"
    MODES = ("images", "carts", "themes")

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self.names = names
        self._in = in_rect
        self.layout = AppearanceLayout(ws.sys_canvas.w, ws.sys_canvas.h,
                                       ws._effective_font_scale(), ws.windowed_chrome)
        self.mode = "images"
        self.sel = 0
        self.status = "IMAGE WALLPAPERS"

    @staticmethod
    def is_app(cart):
        if (not cart or cart.get("title") != "Appearance"
                or "appearance" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:
            return int(cart.get("version", 0)) >= 1
        return str(path).replace("\\", "/").rsplit("/", 1)[-1] == "theme_picker.moy"

    def relayout(self, w, h, fs):
        self.layout = AppearanceLayout(w, h, fs, self.ws.windowed_chrome)

    def open(self):
        # Land on the current wallpaper's source category.
        cart = self.ws._wp_cart_by_id(self.ws.wallpaper_id)
        self.mode = "images" if cart is not None and cart.get("title") == "My Art" else "carts"
        self.sel = self._selected_index()
        self.status = self.mode.upper()
        self.ws._dirty = True

    def _image_items(self):
        wall = next((c for c in self.ws.wallpaper_carts()
                     if c.get("title") == "My Art"), None)
        return [wall] if wall is not None else []

    def _cart_items(self):
        return [c for c in self.ws.wallpaper_carts() if c.get("title") != "My Art"]

    def _items(self):
        if self.mode == "images":
            return self._image_items()
        if self.mode == "carts":
            return self._cart_items()
        return list(THEMES)

    def _selected_index(self):
        items = self._items()
        if self.mode == "themes":
            for i, item in enumerate(items):
                if item[0] == self.ws.theme_name:
                    return i
            return 0
        for i, cart in enumerate(items):
            if self.ws._wp_id_for(cart) == self.ws.wallpaper_id:
                return i
        return 0

    def _set_mode(self, mode):
        self.mode = mode
        self.sel = self._selected_index()
        self.status = mode.upper()
        self.ws._dirty = True

    def _apply(self, index):
        items = self._items()
        if not items:
            return
        self.sel = max(0, min(int(index), len(items) - 1))
        item = items[self.sel]
        if self.mode == "themes":
            self.ws.set_theme(item[0], persist=True)
            self.status = item[0].upper() + " THEME"
        else:
            self.ws.select_wallpaper(self.ws._wp_id_for(item), persist=True)
            self.status = item.get("title", "WALLPAPER").upper()
        self.ws._dirty = True

    def handle_input(self, inp):
        items = self._items()
        if inp.pressed("left") and items:
            self._apply((self.sel - 1) % len(items))
        elif inp.pressed("right") and items:
            self._apply((self.sel + 1) % len(items))
        elif inp.pressed("up"):
            self._set_mode(self.MODES[(self.MODES.index(self.mode) - 1) % 3])
        elif inp.pressed("down"):
            self._set_mode(self.MODES[(self.MODES.index(self.mode) + 1) % 3])
        return True

    def handle_pointer(self, px, py, click):
        lay = self.layout
        if click and not self.ws.windowed_chrome and py < lay.bar_h:
            return bool(self.ws.bar_layer.handle_bar_tap("tool", px, py))
        if not click:
            return True
        for i, r in enumerate(lay.tabs):
            if self._in(px, py, r):
                self._set_mode(self.MODES[i])
                return True
        for i, r in enumerate(lay.cards(len(self._items()))):
            if self._in(px, py, r):
                self._apply(i)
                return True
        return True

    def _button(self, cv, label, r, on=False):
        th = self.ws.theme_colors
        bg = th["accent"] if on else th["panel"]
        fg = self.names["black"] if on else th["title_ink"]
        cv.rect(r[0], r[1], r[2], r[3], bg)
        cv.rectb(r[0], r[1], r[2], r[3], th["edge"] if on else th["dim"])
        fw = 8 * self.layout.fs
        cv.print(label, r[0] + max(2, (r[2] - len(label) * fw) // 2),
                 r[1] + max(1, (r[3] - 8 * self.layout.fs) // 2), fg, 1)

    def draw(self, dt):
        cv = self.ws.sys_canvas
        # The preview is the actual wallpaper renderer. Live cart wallpapers animate;
        # Paint images take the direct resolution-aware system path.
        self.ws.wallpaper.draw(dt)
        lay = self.layout
        th = self.ws.theme_colors
        cv.rect(0, lay.bar_h, lay.w, lay.tabs_h, 48)
        for i, r in enumerate(lay.tabs):
            self._button(cv, ("IMAGES", "CARTS", "THEMES")[i], r,
                         self.mode == self.MODES[i])

        # A quiet preview caption, not a floating card.
        px, py, pw, ph = lay.preview
        label = self.status[:max(1, pw // (8 * lay.fs) - 4)]
        cv.rect(px, py + ph - 18 * lay.fs, pw, 18 * lay.fs, self.names["black"])
        cv.print(label, px + 8 * lay.fs, py + ph - 13 * lay.fs,
                 self.names["white"], 1)

        cx, cy, cw, ch = lay.catalog
        cv.rect(cx, cy, cw, ch, th["panel"])
        cv.rect(cx, cy, cw, 2 * lay.fs, th["edge"])
        items = self._items()
        cards = lay.cards(len(items))
        for i, r in enumerate(cards):
            if self.mode == "themes":
                self._draw_theme_card(cv, r, items[i], i == self.sel)
            else:
                self._draw_wall_card(cv, r, items[i], i == self.sel,
                                     self.mode == "images")
        if not self.ws.windowed_chrome:
            self.ws.bar_layer._draw_status_strip("tool")

    def _draw_wall_card(self, cv, r, cart, selected, image_kind):
        x, y, w, h = r
        th = self.ws.theme_colors
        pad = 3 * self.layout.fs
        ix, iy, iw, ih = x + pad, y + pad, w - pad * 2, max(12, h - 20 * self.layout.fs)
        title = cart.get("title", "WALLPAPER")
        if image_kind:
            preview = self.ws.artwork.thumbnail(iw, ih)
            if preview is not None:
                cv.spr(preview, ix, iy)
            else:
                cv.rect(ix, iy, iw, ih, self.names["light_grey"])
                cv.circ(ix + iw * 3 // 4, iy + ih // 3,
                        max(2, min(iw, ih) // 8), self.names["yellow"])
        else:
            self._cart_scene(cv, ix, iy, iw, ih, title)
        cv.rect(x, y + h - 17 * self.layout.fs, w, 17 * self.layout.fs,
                th["accent"] if selected else th["title"])
        cv.print(title[:max(1, w // (8 * self.layout.fs) - 2)], x + 5 * self.layout.fs,
                 y + h - 13 * self.layout.fs,
                 self.names["black"] if selected else th["title_ink"], 1)
        cv.rectb(x, y, w, h, th["accent"] if selected else th["dim"])

    def _cart_scene(self, cv, x, y, w, h, title):
        # Distinct miniature visual identities; the large area above is the real preview.
        if "Ocean" in title:
            cv.rect(x, y, w, h, self.names["blue"])
            for row in range(3):
                cv.line(x, y + h // 2 + row * 5 * self.layout.fs,
                        x + w - 1, y + h // 2 + row * 5 * self.layout.fs,
                        21 + row)
            cv.circ(x + w * 3 // 4, y + h // 3, max(2, h // 10), 7)
        elif "Sakura" in title:
            cv.rect(x, y, w, h, 22)
            cv.line(x + w // 3, y + h, x + w // 2, y + h // 4, 28)
            cv.circ(x + w // 2, y + h // 3, max(3, h // 4), self.names["pink"])
            cv.circ(x + w * 2 // 3, y + h // 3, max(2, h // 6), self.names["peach"])
        elif "Space" in title or "Night" in title:
            cv.rect(x, y, w, h, 60)
            for i in range(12):
                cv.rect(x + (i * 37) % max(1, w), y + (i * 19) % max(1, h),
                        self.layout.fs, self.layout.fs, 7 if i % 3 else 10)
            cv.circ(x + w * 3 // 4, y + h // 3, max(3, h // 6), 10)
        else:
            cv.rect(x, y, w, h, self.ws.theme_colors["hilite"])
            cv.rect(x, y + h * 2 // 3, w, h // 3, self.names["dark_green"])
            cv.circ(x + w * 3 // 4, y + h // 3, max(2, h // 8), self.names["yellow"])

    def _draw_theme_card(self, cv, r, item, selected):
        name, tok = item
        x, y, w, h = r
        fs = self.layout.fs
        cv.rect(x, y, w, h, tok["panel"])
        compact = h < 46 * fs
        band_h = max(6 * fs, h - 18 * fs) if compact else 16 * fs
        cv.rect(x + 3 * fs, y + 3 * fs, w - 6 * fs, band_h, tok["title"])
        if compact:
            cv.rect(x + w - 17 * fs, y + 6 * fs, 10 * fs,
                    max(3 * fs, band_h - 6 * fs), tok["accent"])
        else:
            cv.rect(x + 8 * fs, y + 25 * fs, w * 3 // 5, 12 * fs, tok["hilite"])
            cv.rect(x + w - 25 * fs, y + 25 * fs, 17 * fs, 12 * fs, tok["accent"])
        cv.print(name.upper(), x + 6 * fs, y + h - 14 * fs, tok["title_ink"], 1)
        cv.rectb(x, y, w, h, tok["accent"] if selected else tok["dim"])
