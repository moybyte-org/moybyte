"""The bar widgets' popovers (docs/shell_layout_2026-10.md §8, the look's
step 33): each a component the popover (`widgets.Popup`) holds, hung from
its widget on every glass in the system's flat card with no wash (D-01),
its content on the public UI API's verbs.

  `clock`    MonthView   the month (`ui.month`), today marked; left/right page
  `wifi`     WifiPanel   Settings' Wi-Fi page, the one implementation
  `battery`  PowerRows   the battery's line, then Volume and Brightness

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
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _lb


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
    networks a scan found): the frame repaints at the new size."""
    m = ws.sysmenu
    w, h = comp.size()
    h += 2 if _desk() else 2 * ws.layout.fs
    if m.comp is comp and m.size[1] != h:
        m.size = (m.size[0], h)
        ws._dirty = True


_MONTH_KEYS = ("change", "Month", "back", "Close")
_MONTH_KEYS_DESK = ("change", "Month", "back", "Close")
_POWER_KEYS = ("change", "Change", "up down", "Row", "back", "Close")
_WIFI_KEYS = ("confirm", "Join", "back", "Close")


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
        self.host.begin(cv, ws.theme_colors, (x0 + p, y0 + top, w0 - 2 * p, h0 - top), desk)
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
        if i.pressed("left"):
            return self.page(-1)
        if i.pressed("right"):
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
        self.host.begin(cv, ws.theme_colors, _inner(ws, rect), _desk())
        ws.settings_layer.wifi_page(popover=True)
        if self._radio() != before:
            ws._dirty = True          # the radio's state shows outside the panel
        elif self.host.owed or self.host.tap == 2:
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
        self.host.begin(cv, ws.theme_colors, _inner(ws, rect), desk)
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
        if self.host.owed or self.host.tap == 2:
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
        return PowerRows(ws)
    return None
