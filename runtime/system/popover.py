"""The bar widgets' popovers (docs/theme_and_shell_2026-11.md §8.6, the look's
step 33): each a component the popover (`widgets.Popup`) holds, hung from
its widget on every glass in the system's flat card with no wash (D-01),
its content on the public UI API's verbs.

  `clock`    MonthView   the month (`ui.month`), today marked; left/right page
  `wifi`     WifiPanel   Settings' Wi-Fi page, the one implementation
  `battery`  PowerRows   on the desk: the battery's line, then Volume and
                         Brightness
             ControlCenter  on a handheld, opened by the status cluster:
                         the console's card, then each app's status section
  `status`   StatusMenu  on the desk: an app's status item's menu, its section

The system menu is the popover's ROWS, not a component (`widgets.Popup`).

Every component's interface: `size()` -> (w, h) in pixels at the current
scales, `draw(cv, rect)` (the card is the popover's, `rect` inside it),
`keys()` -> the hint strip's keys on a handheld, `handle_input(i)` ->
"local" when only the panel's pixels moved, "global" when something outside
it did, None for nothing; `click(px, py, rect)` the same; `typing()` ->
True while typed keys are its own (B and ESC are then the text field's);
`closed()` on every close, a tap outside included.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
    import layout_base as _lb
    from status_items import ITEMS as _ITEMS
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _lb
    from runtime.status_items import ITEMS as _ITEMS


def shift_month(y, m, n):
    k = y * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


def _desk():
    return _lb.metric_class() == _lb.DESK


def _pad(ws):
    return 12 if _desk() else 9 * ws.layout.fs


def _inner(ws, rect):
    """The rows' rect inside a popover's card: its padding off the card's
    top and foot, and the rows' own pad off its sides."""
    p = _pad(ws)
    x, y, w, h = rect
    return (x + p, y + p, w - 2 * p, h - 2 * p)


def _fit(ws, comp):
    """Size the popover to `comp` again where what it lists moved (the
    networks a scan found): the next frame repaints at the new size (a mark
    made inside a draw is spent with it, so it asks for that frame)."""
    m = ws.sysmenu
    w, h = comp.size()
    h += 2 if _desk() else 2 * ws.layout.fs
    if m.comp is comp and m.size[1] != h:
        m.size = (m.size[0], h)
        ws.request_frame()


_MONTH_KEYS = ("change", "Month", "back", "Close")
_MONTH_KEYS_DESK = ("change", "Month", "back", "Close")
_POWER_KEYS = ("change", "Change", "scroll", "Row", "back", "Close")
_WIFI_KEYS = ("confirm", "Join", "back", "Close")
_MENU_KEYS = ("change", "Button", "back", "Close")
_MENU_POINTER = ("back", "Close")


class MonthView:
    """The `clock` widget's component: the month, today marked; the arrows
    (left/right, or a chevron on the desk and under touch) page months."""

    def __init__(self, ws):
        self.ws = ws
        self.off = 0
        self.host = _ui_draw.AppHost()

    def size(self):
        ws = self.ws
        desk = _desk()
        fs = 1 if desk else ws.layout.fs
        y, m, _d = self.month()
        p = _pad(ws)
        h = _ui.month_h(y or 2026, m or 1, fs, desk) if y else 6 * 21 * fs
        foot = _ui_draw.metrics("value_row")[_HINTS_DESK] if desk else 0
        if desk:
            return (264, p + h + foot + p // 2)
        return (200 * fs, (6 + 3) * fs + h)

    def month(self):
        y, m, d = self.ws.bar_layer._today()
        if not y:
            return 0, 0, 0
        yy, mm = shift_month(y, m, self.off)
        return yy, mm, (d if self.off == 0 else 0)

    def keys(self):
        return _MONTH_KEYS

    def draw(self, cv, rect):
        ws = self.ws
        desk = _desk()
        y, m, today = self.month()
        p = _pad(ws)
        top = p if desk else 6 * ws.layout.fs
        x0, y0, w0, h0 = rect
        self.host.begin(cv, ws.theme_colors, (x0 + p, y0 + top, w0 - 2 * p, h0 - top), desk,
                        "panel")
        if y:
            _ui.at(x0 + p, y0 + top, w0 - 2 * p,
                   _ui.month_h(y, m, 1 if desk else ws.layout.fs, desk))
            step = _ui.month(y, m, today)
            if step:
                self.off += step
                ws.popover_touch()
        if desk:
            _ui.hints(_MONTH_KEYS_DESK)

    def page(self, n):
        self.off += n
        return "local"

    def handle_input(self, i):
        if i.steps("left"):
            return self.page(-1)
        if i.steps("right"):
            return self.page(1)
        return None

    def click(self, px, py, rect):
        self.host.point(px, py, True)
        return "local"

    def typing(self):
        return False

    def closed(self):
        self.off = 0


# value_row's slot of the desk's hint band (MOY_UI_M_VALUE_ROW_HINTS_DESK)
_HINTS_DESK = 17


class WifiPanel:
    """The `wifi` widget's component: Settings' Wi-Fi page (scan, pick, the
    password step, connect) drawn in the popover's card over its own UI
    context (`ui_draw.AppHost`), holding the radio lease Settings' page
    holds, released on every close. One implementation:
    `SettingsLayer.wifi_page`."""

    def __init__(self, ws):
        self.ws = ws
        self.rect = None
        self.host = _ui_draw.AppHost()

    def size(self):
        desk = _desk()
        fs = 1 if desk else self.ws.layout.fs
        row = _ui_draw.metrics("row")
        rh, gap = row[6 if desk else 5] * fs, row[7] * fs
        cap = _ui_draw.metrics("group")[4 if desk else 3] * fs
        n = min(len(self.ws.settings_layer.wifi_nets), 6) + 2
        foot = _ui_draw.metrics("value_row")[_HINTS_DESK] if desk else 0
        return ((300 if desk else 232 * fs), 2 * _pad(self.ws) + cap + n * (rh + gap) + foot)

    def keys(self):
        return _WIFI_KEYS

    def opened(self):
        self.host.reset()
        self.ws.settings_layer.wifi_enter()

    def draw(self, cv, rect):
        ws = self.ws
        self.rect = rect
        before = self._radio()
        _fit(ws, self)
        self.host.begin(cv, ws.theme_colors, _inner(ws, rect), _desk(), "panel")
        ws.settings_layer.wifi_page(popover=True)
        if self._radio() != before:
            ws._dirty = True          # the radio's state shows outside the panel
        elif self.host.owed or self.host.tap == 2 or self.host.moved:
            ws.popover_touch()        # a tap's release, next frame

    def _radio(self):
        sl = self.ws.settings_layer
        return (tuple(sl.wifi_known), self.ws._wifi_icon_kind())

    def _state(self):
        sl = self.ws.settings_layer
        return (len(sl.wifi_nets), sl.wifi_msg, sl.wifi_sel, sl.wifi_pick, sl.wifi_pw.text)

    def handle_input(self, i):
        before = self._state()
        radio = self._radio()
        self.ws.settings_layer.wifi_input(i, self.host, popover=True)
        if self._radio() != radio:
            return "global"
        if self._state() != before or i.any_pressed() or i.last_key:
            return "local"            # the page resolves the keys as it draws
        return None

    def click(self, px, py, rect):
        self.host.point(px, py, True)
        return "local"

    def typing(self):
        return self.ws.settings_layer.wifi_pick is not None

    def closed(self):
        self.ws.settings_layer.wifi_leave()


class PowerRows:
    """The `battery` widget's component: the battery's line (its level
    where the board reads one), then Volume and Brightness as Settings'
    sliders. Up/down pick a row, left/right (or a tap or a drag on a track)
    change it through Settings (`set_level`)."""

    KEYS = ("volume", "brightness")
    _LABELS = ("Volume", "Brightness")

    def __init__(self, ws):
        self.ws = ws
        self.rect = None
        self.host = _ui_draw.AppHost()

    def size(self):
        desk = _desk()
        fs = 1 if desk else self.ws.layout.fs
        row = _ui_draw.metrics("row")
        rh = row[6 if desk else 5] * fs
        gap = row[7] * fs
        rule = (2 * row[7] + (9 if desk else 5)) * fs
        foot = _ui_draw.metrics("value_row")[_HINTS_DESK] if desk else 0
        return ((300 if desk else 236 * fs),
                2 * _pad(self.ws) + 3 * rh + 3 * gap + rule + foot)

    def keys(self):
        return _POWER_KEYS

    def draw(self, cv, rect):
        ws = self.ws
        desk = _desk()
        self.rect = rect
        sl = ws.settings_layer
        self.host.begin(cv, ws.theme_colors, _inner(ws, rect), desk, "panel")
        lvl = ws.battery_level()
        _ui.icon("battery")
        _ui.info("Battery" if lvl is None else "%d%%" % lvl, "")
        _ui.rule()
        for k in range(len(self.KEYS)):
            key = self.KEYS[k]
            cur, lo, hi = sl.level(key)
            _ui.icon("mute" if key == "volume" and cur == lo
                     else "volume" if key == "volume" else "spark")
            got = _ui.slider(self._LABELS[k], cur, lo, hi)
            if got != cur:
                sl.set_level(key, got)
                ws._dirty = True
        if desk:
            _ui.hints(("change", "Change", "back", "Close"))
        if self.host.owed or self.host.tap == 2 or self.host.moved:
            ws.popover_touch()

    def handle_input(self, i):
        if self.host.keys(i):
            return "local"
        return None

    def click(self, px, py, rect):
        self.host.point(px, py, True)
        return "local"

    def typing(self):
        return False

    def closed(self):
        self.host.reset()


class ControlCenter:
    """A handheld's control center, the `battery` widget's component there,
    which the bar's status cluster opens (docs/theme_and_shell_2026-11.md
    §8.6): one column in a scroll region, the console's card first (Volume,
    Brightness, Wi-Fi), then each app's status section as a card under its
    app's name, in the bar's order. Its first view ends FIRST_FOOT under the
    first app's card; the region scrolls the rest, its thumb and fades
    saying so. The battery has no row: the bar's glyph shows it low and
    charging, and Settings keeps its exact level."""

    KEYS = ("volume", "brightness")
    _LABELS = ("Volume", "Brightness")
    W = 232                     # at the font scale
    FIRST_FOOT = 6
    SCRIM = 128                 # the page beside it dims by half, in both themes

    def __init__(self, ws):
        self.ws = ws
        self.rect = None
        self.host = _ui_draw.AppHost()
        self.gen = _ITEMS.gen

    def _metrics(self):
        """(fs, a stacked row's height, a caption's, the gap after a card)."""
        fs = self.ws.layout.fs
        row = _ui_draw.metrics("row")
        grp = _ui_draw.metrics("group")
        return fs, (row[5] + row[7]) * fs, grp[3] * fs, grp[5] * fs

    def _inset(self):
        """How far the console's card stands in from the panel's edges, the
        top and the foot as the sides: the group's inset."""
        return _ui_draw.metrics("group")[0] * self.ws.layout.fs

    def column(self):
        """(the first view's height, the column's), from the panel's top."""
        fs, rh, cap, gap = self._metrics()
        i = self._inset()
        w = (self.W - 2 * 12) * fs
        y = fs + i + 3 * rh             # the panel's frame, the inset, the card
        first = None
        for it in _ITEMS.shown:
            y += gap + cap + _ITEMS.height(it, w, False)
            if first is None:
                first = y + self.FIRST_FOOT * fs
        y += i + fs
        return (y if first is None else min(first, y)), y

    def size(self):
        ws = self.ws
        first, _end = self.column()
        lay = ws.layout
        room = lay.ch
        strip = ws.menu_ui.about._strip()
        if strip is not None and lay.cy < strip[1] < lay.cy + room:
            room = strip[1] - lay.cy           # above the hint strip
        return (self.W * lay.fs, min(first, room - 4 * lay.fs))

    def keys(self):
        return _POWER_KEYS

    def draw(self, cv, rect):
        ws = self.ws
        self.rect = rect
        sl = ws.settings_layer
        fs = ws.layout.fs
        x, y, w, h = rect
        i = self._inset()
        self.host.begin(cv, ws.theme_colors, (x + fs, y + fs, w - 2 * fs, h - 2 * fs), False,
                        "panel")
        _ui.at(x + fs, y + fs + i, w - 2 * fs, h - 2 * fs - i)
        _ui.scroll("cc")
        _ui.group("", {"card": True})
        for k in range(len(self.KEYS)):
            key = self.KEYS[k]
            cur, lo, hi = sl.level(key)
            _ui.icon("mute" if key == "volume" and cur == lo
                     else "volume" if key == "volume" else "spark")
            got = _ui.slider(self._LABELS[k], cur, lo, hi, {"steps": True})
            if got != cur:
                sl.set_level(key, got)
                ws._dirty = True
        _ui.icon("wifi" if ws._wifi_icon_kind() == "wifi" else "wifi_off")
        if _ui.link("Wi-Fi", self._network()):
            ws.close_popover()
            ws.toggle_popover("wifi")
            return
        for it in _ITEMS.shown:
            _ui.group(it.app or "")
            _ITEMS.draw(it, False)
            it = _ITEMS.opening
            if it is not None:
                _ITEMS.opening = None
                ws.open_status_app(it)
                return
        _ui.group()
        _ui.scroll()
        if self.gen != _ITEMS.gen:
            self.gen = _ITEMS.gen
            _fit(ws, self)
        if self.host.owed or self.host.tap == 2 or self.host.moved:
            ws.popover_touch()

    def _network(self):
        """The Wi-Fi row's value: the network joined, else Off."""
        w = self.ws.wifi
        try:
            st = w.status() if w is not None else None
        except Exception:  # noqa: BLE001 -- a status hiccup reads as no link
            st = None
        return st[1] if st and st[0] and st[1] else "Off"

    def handle_input(self, i):
        if self.host.keys(i):
            return "local"
        return None

    def click(self, px, py, rect):
        self.host.point(px, py, True)
        return "local"

    def typing(self):
        return False

    def closed(self):
        self.host.reset()


class StatusMenu:
    """An app's status item's menu on the desk, hung from its cell in the
    bar's right zone: the app's section in the popover's card, its keys'
    hints under it. No wash: the desk's menus take none."""

    def __init__(self, ws, item):
        self.ws = ws
        self.item = item
        self.rect = None
        self.host = _ui_draw.AppHost()
        self.gen = _ITEMS.gen

    def size(self):
        w = 404
        # under touch alone there are no hints, and no room kept for them
        foot = 0 if _ui.input_kind() == "touch" else _ui_draw.metrics("value_row")[_HINTS_DESK]
        it = self.item
        h = _ITEMS.height(it, w - 2 * _pad(self.ws), True) if it is not None else 0
        return (w, 2 * _pad(self.ws) + h + foot)

    def keys(self):
        return _MENU_KEYS

    def draw(self, cv, rect):
        ws = self.ws
        self.rect = rect
        it = self.item
        self.host.begin(cv, ws.theme_colors, _inner(ws, rect), True, "panel")
        if it is not None and it.pid:
            _ITEMS.draw(it, True)
        _ui.hints(_MENU_POINTER if _ui.input_kind() == "touch" else _MENU_KEYS)
        if self.gen != _ITEMS.gen:
            self.gen = _ITEMS.gen
            _fit(ws, self)
        if self.host.owed or self.host.tap == 2 or self.host.moved:
            ws.popover_touch()

    def handle_input(self, i):
        if self.host.keys(i):
            return "local"
        return None

    def click(self, px, py, rect):
        self.host.point(px, py, True)
        return "local"

    def typing(self):
        return False

    def closed(self):
        self.host.reset()


def component_for(ws, widget):
    """The component a bar widget opens (§8's table, fixed per widget)."""
    if widget == "clock":
        return MonthView(ws)
    if widget == "wifi":
        return WifiPanel(ws)
    if widget == "battery":
        return PowerRows(ws) if _desk() else ControlCenter(ws)
    if widget == "status":
        return StatusMenu(ws, ws.status_open)
    return None
