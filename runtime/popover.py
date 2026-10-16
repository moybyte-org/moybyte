"""The popover's components (docs/shell_layout_2026-10.md §8).

A component is one painter, one panel size and one navigation handler; where
it draws is its container's business. Two containers hold them: the popover
(`widgets.Popup`, desktop mode, one at a time) and a fullscreen page in
console mode (the calendar as its own kind, `CalendarLayer`; the wifi and
power rows as the Settings page opened at them). No component is docked in
a bar: a bar widget is compact, and what is bigger opens (§1, 2026-10-14).

  `clock`    MonthView   the month calendar, today marked; left/right page
  `wifi`     WifiPanel   the Settings WIFI panel, the one implementation
  `battery`  PowerRows   Settings' level, VOLUME and BRIGHTNESS rows

The system menu is the popover's ROWS, not a component (`widgets.Popup`).

Every component's interface: `size()` -> (w, h) in pixels at the current
scales, `draw(cv, rect)`, `handle_input(i)` -> "local" when only the panel's
pixels moved, "global" when something outside it did, None for nothing;
`click(px, py, rect)` the same; `typing()` -> True while typed keys are its
own (B and ESC are then the text field's); `closed()` on every close, a tap
outside included.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
    from layers import Layer
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime.layers import Layer

_in = _ui.rect_in

MONTHS = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
          "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")
DAYS = "MTWTFSS"
_DOW_T = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
_MDAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def weekday(y, m, d):
    """0 for Monday .. 6 for Sunday (Sakamoto's rule: no datetime on a board)."""
    if m < 3:
        y -= 1
    return (y + y // 4 - y // 100 + y // 400 + _DOW_T[m - 1] + d + 6) % 7


def month_days(y, m):
    if m == 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
        return 29
    return _MDAYS[m - 1]


def shift_month(y, m, n):
    k = y * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


def draw_month(cv, th, rect, y, m, today, fs, ink, dim):
    """The month grid in `rect`: the month's name and year, the weekday
    initials, then six weeks of `ui.cell`s from Monday, `today` (a day of
    this month, or 0) marked on. The calendar's one painter: the bar cell,
    the popover and the console-mode page all call it."""
    x, y0, w, h = rect
    fw = 8 * fs
    head = 12 * fs
    title = "%s %d" % (MONTHS[m - 1], y)
    if len(title) * fw > w:
        title = "%s %d" % (MONTHS[m - 1][:3], y)
    cv.print(title, x + (w - len(title) * fw) // 2, y0 + (head - 8 * fs) // 2, ink, 1)
    cw = w // 7
    gx = x + (w - cw * 7) // 2
    dy = y0 + head
    for k in range(7):
        cv.print(DAYS[k], gx + k * cw + (cw - fw) // 2, dy, dim, 1)
    gy = dy + 10 * fs
    ch = (y0 + h - gy) // 6
    if ch <= 0:
        return
    first = weekday(y, m, 1)
    n = month_days(y, m)
    for d in range(1, n + 1):
        i = first + d - 1
        r = (gx + (i % 7) * cw, gy + (i // 7) * ch, cw, ch)
        _ui_draw.cell(cv, th, r, label=str(d), on=(d == today), pad=0, fs=fs,
                 text_dy=(ch - 8 * fs) // 2)


class MonthView:
    """The `clock` widget's component: the month calendar, today marked; the
    arrows (left/right, or a tap on the title's halves) page months. The
    day cursor is display only."""

    def __init__(self, ws):
        self.ws = ws
        self.off = 0

    def size(self):
        lay = self.ws.layout
        cs = lay.cs
        return (7 * 22 * cs + 8 * cs, 22 * cs + 6 * 14 * cs + 8 * cs + 10 * lay.fs)

    def month(self):
        y, m, d = self.ws.bar_layer._today()
        if not y:
            return 0, 0, 0
        yy, mm = shift_month(y, m, self.off)
        return yy, mm, (d if self.off == 0 else 0)

    def draw(self, cv, rect):
        ws = self.ws
        th = ws.theme_colors
        y, m, today = self.month()
        if not y:
            return
        p = 4 * ws.layout.cs
        draw_month(cv, th, (rect[0] + p, rect[1] + p, rect[2] - 2 * p, rect[3] - 2 * p),
                   y, m, today, ws.layout.fs, th["ink"], th["ink_dim"])

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
        x, y, w, _h = rect
        if py < y + 4 * self.ws.layout.cs + 12 * self.ws.layout.fs:
            return self.page(-1 if px < x + w // 2 else 1)
        return None

    def typing(self):
        return False

    def closed(self):
        self.off = 0


class PanelGeom:
    """The Settings panel's row geometry over another rect: what the WIFI
    panel and the Settings rows read (`settings_panel`, `settings_row_rect`,
    `row_band`...) when a popover holds them, so Settings draws them -- one
    implementation, the popover a container for it."""

    def __init__(self, lay, rect):
        fs = lay.fs
        self.fs = fs
        self.cs = lay.cs
        self.font_w = lay.font_w
        self.set_row_h = lay.set_row_h
        self.row_text_dy = lay.row_text_dy
        self.settings_panel = rect
        self.set_x = rect[0] + 10 * fs
        self.set_w = rect[2] - 20 * fs
        self.set_row_y0 = rect[1] + 6 * lay.cs

    def settings_row_rect(self, i):
        return (self.set_x, self.set_row_y0 + i * self.set_row_h,
                self.set_w, self.set_row_h - 2)

    def row_band(self, rect):
        d = self.row_text_dy
        return (rect[0], rect[1] + d, rect[2], rect[3] - 2 * d)


class _SettingsComponent:
    """A component Settings draws: its calls run with the panel geometry
    over the popover's rect (`SettingsLayer.in_panel`)."""

    def __init__(self, ws):
        self.ws = ws

    def _run(self, rect, fn, *args):
        sl = self.ws.settings_layer
        was = sl._geom
        sl._geom = PanelGeom(self.ws.layout, rect)
        try:
            return fn(*args)
        finally:
            sl._geom = was


class WifiPanel(_SettingsComponent):
    """The `wifi` widget's component: the Settings WIFI panel's rows (scan,
    pick, the password step in the same container, connect), holding the
    radio lease Settings' panel holds, released on every close."""

    def __init__(self, ws):
        _SettingsComponent.__init__(self, ws)
        self.rect = None

    def size(self):
        lay = self.ws.layout
        fs = lay.fs
        return (304 * fs, 6 * lay.cs + 5 * lay.set_row_h + 44 * fs)

    def opened(self):
        self.ws.settings_layer.wifi_enter()

    def draw(self, cv, rect):
        self.rect = rect
        self._run(rect, self.ws.settings_layer._draw_wifi)

    def _changed(self, before):
        now = self._state()
        if now[0] != before[0]:
            return "global"           # the radio's state shows outside the panel
        return "local" if now[1] != before[1] else None

    def _state(self):
        sl = self.ws.settings_layer
        return ((tuple(sl.wifi_known), self.ws._wifi_icon_kind()),
                (len(sl.wifi_nets), sl.wifi_msg, sl.wifi_sel, sl.wifi_pick,
                 sl.wifi_pw.text))

    def handle_input(self, i):
        before = self._state()
        self._run(self.rect or (0, 0, 0, 0), self.ws.settings_layer._wifi_input, i)
        return self._changed(before)

    def click(self, px, py, rect):
        before = self._state()
        self._run(rect, self.ws.settings_layer._wifi_pointer, px, py, True)
        return self._changed(before)

    def typing(self):
        return self.ws.settings_layer.wifi_pick is not None

    def closed(self):
        self.ws.settings_layer.wifi_leave()


class PowerRows(_SettingsComponent):
    """The `battery` widget's component: the level line and Settings' VOLUME
    and BRIGHTNESS rows, drawn by Settings' own row painter. Up/down pick a
    row, left/right (or a tap on a row's ends) step it."""

    KEYS = ("volume", "brightness")

    def __init__(self, ws):
        _SettingsComponent.__init__(self, ws)
        self.sel = 0
        self.rect = None

    def size(self):
        lay = self.ws.layout
        return (208 * lay.fs, 12 * lay.cs + 3 * lay.set_row_h)

    def draw(self, cv, rect):
        self.rect = rect
        self._run(rect, self.ws.settings_layer.draw_power, self.sel)

    def handle_input(self, i):
        if i.pressed("up") and self.sel > 0:
            self.sel -= 1
            return "local"
        if i.pressed("down") and self.sel < len(self.KEYS) - 1:
            self.sel += 1
            return "local"
        d = -1 if i.pressed("left") else (1 if i.pressed("right") else 0)
        if d:
            self.ws.settings_layer.step_row(self.KEYS[self.sel], d)
            return "global"
        return None

    def click(self, px, py, rect):
        sl = self.ws.settings_layer
        g = PanelGeom(self.ws.layout, rect)
        edge = 5 * g.font_w
        for k in range(len(self.KEYS)):
            x, y, w, h = g.settings_row_rect(k + 1)
            if _in(px, py, (x, y, w, h)):
                self.sel = k
                if px >= x + w - edge:
                    sl.step_row(self.KEYS[k], 1)
                    return "global"
                if px <= x + edge:
                    sl.step_row(self.KEYS[k], -1)
                    return "global"
                return "local"
        return None

    def typing(self):
        return False

    def closed(self):
        self.sel = 0


def component_for(ws, widget):
    """The component a bar widget opens (§8's table, fixed per widget)."""
    if widget == "clock":
        return MonthView(ws)
    if widget == "wifi":
        return WifiPanel(ws)
    if widget == "battery":
        return PowerRows(ws)
    return None


class CalendarLayer(Layer):
    """The calendar as its own kind (console mode's container, §8 (c)): the
    month grid over the content rect, pushed on the back-stack, so back, B,
    HOME and the bar's X pop it. Left/right page months."""

    id = "calendar"
    domain = "system"
    zone_gen = 0

    def __init__(self, ws):
        self.ws = ws
        self.view = MonthView(ws)

    def draw(self, dt):
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        cx, cy, cw, ch = ws.bar_layer.content_rect("calendar")
        cv.rect(cx, cy, cw, ch, th["panel"])
        y, m, today = self.view.month()
        if y:
            p = 6 * ws.layout.fs
            draw_month(cv, th, (cx + p, cy + p, cw - 2 * p, ch - 2 * p), y, m,
                       today, ws.layout.fs, th["ink"], th["ink_dim"])
        ws.bar_layer._draw_status_strip("calendar")

    def handle_input(self, i):
        ws = self.ws
        if i.pressed("b") or i.pressed("stop") or i.pressed("home"):
            ws.close_calendar()
        elif self.view.handle_input(i):
            ws._dirty = True
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if not click:
            return True
        if ws.bar_layer.handle_bar_tap("calendar", px, py):
            return True
        cx, cy, cw, ch = ws.bar_layer.content_rect("calendar")
        if self.view.click(px, py, (cx, cy, cw, ch)):
            ws._dirty = True
        return True
