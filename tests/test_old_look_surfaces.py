"""The old look's last surfaces in the 2026-10 look (docs/look_2026-10.md
step 33, GAPS 49-54): the stop card's rows by the run's kind, the month from
a date, a dialog's captioned fields and its Enter, the flat toast and card,
the loading pill, the bar widgets' popovers, the saver and the text console.
"""

from runtime import ui, ui_draw
from ws_helpers import build_ws, build_ws_with_cart, no_unlock_banner

DT = 1 / 30

CRASHER = "def _draw():\n    cls(1)\n    x = None * 2.0\n"


def _frames(ws, n=2):
    for _ in range(n):
        ws.input.begin_frame()
        ws.frame(DT)


def _stopped(tmp_path, src=CRASHER, **kw):
    ws = build_ws_with_cart(tmp_path, src, title="Crasher", type="game", **kw)
    _frames(ws, 3)
    return ws


# -- the stop card (GAPS 49) ---------------------------------------------------------

def test_a_crash_opens_the_stop_card_with_see_why_restart_and_home(tmp_path):
    ws = _stopped(tmp_path)
    p = ws.pause
    assert ws.cart_error and p.open and p.stopped
    assert p.rows == (p.SEE_WHY, p.RESTART, p.HOME)
    assert ws.wm.top_is_player()                 # over the run's last frame
    card = p._host.box("Pause")
    assert card is not None and card[2] > 0
    p.choose_row(p.SEE_WHY)                      # the crash-to-code throw
    assert ws.screen == "menu" and ws.menu_view == "code"
    assert ws.crash_popup and not p.open


def test_the_stop_cards_restart_and_home(tmp_path):
    ws = _stopped(tmp_path)
    p = ws.pause
    p.choose_row(p.RESTART)
    assert not p.open and ws.wm.top_is_player()
    _frames(ws, 3)                               # it stops again, on the same line
    assert p.open and p.stopped
    p.close()                                    # back (B, Esc, K) is Home
    assert not p.open and not ws.wm.top_is_player() and ws.cart_error is None


def test_a_compiled_carts_trap_has_no_see_why(tmp_path):
    ws = build_ws_with_cart(tmp_path, CRASHER, title="Crasher", type="game")
    ws.cart["runtime"] = "wasm"
    _frames(ws, 3)
    p = ws.pause
    assert p.open and p.rows == (p.RESTART, p.HOME)


def test_a_notice_is_the_same_card_over_the_screen_it_was_asked_from(tmp_path):
    from runtime import chrome_ops
    ws = build_ws(tmp_path)
    _frames(ws)
    cart = next(c for c in ws.carts.all if c.get("type") == "game")
    ws._open_workspace(cart)
    pl = ws.player
    pl._notice = pl.cart_error = chrome_ops.newer_text(["tilemap2"])
    pl._notice_title = chrome_ops.say_title(chrome_ops.SAY_NEWER)
    ws.run(ws.project, ws.launcher_layer)
    p = ws.pause
    assert p.open and p.stopped and not ws.wm.top_is_player()
    assert p.rows == (p.UPDATES, p.HOME)
    assert pl.notice == "It uses tilemap2. Update in Settings, then try again."
    _frames(ws)
    p.choose_row(p.UPDATES)
    assert ws.screen == "settings" and not p.open


def test_a_fresh_run_puts_a_stop_card_down(tmp_path):
    ws = _stopped(tmp_path)
    ws._exit_to_caller()
    assert not ws.pause.open


# -- the month (GAPS 53) ---------------------------------------------------------------

def test_the_month_from_a_date():
    assert ui.weekday(2026, 10, 1) == 3            # a Thursday
    assert ui.weekday(2024, 2, 29) == 3
    assert ui.month_days(2024, 2) == 29 and ui.month_days(2026, 2) == 28
    assert ui.month_days(2026, 10) == 31
    # five weeks for October 2026, six for a month that starts on a Sunday
    assert ui.month_h(2026, 3, 1, False) > ui.month_h(2026, 10, 1, False)


def test_the_calendar_popover_pages_and_marks_today(tmp_path):
    ws = no_unlock_banner(build_ws(tmp_path))
    ws.bar_layer._today = lambda: (2026, 10, 23)
    _frames(ws)
    ws.widget_tap("clock")
    _frames(ws)
    m = ws.sysmenu
    assert m.open and m.widget == "clock"
    assert m.comp.month() == (2026, 10, 23)
    m.comp.page(1)
    assert m.comp.month() == (2026, 11, 0)        # another month marks no today
    m.comp.page(-13)
    assert m.comp.month() == (2025, 10, 0)


def test_the_power_popovers_sliders_change_the_volume(tmp_path):
    ws = no_unlock_banner(build_ws(tmp_path))
    sl = ws.settings_layer
    sl.set_level("volume", 3)
    _frames(ws)
    ws.widget_tap("battery")
    _frames(ws, 3)
    comp = ws.sysmenu.comp
    comp.host.focus("Volume")
    _frames(ws)

    class _K:
        last_key = 0

        def __init__(self, n):
            self.n = n

        def pressed(self, n):
            return n == self.n

        def any_pressed(self):
            return True

    ws._sysmenu_layer.handle_input(_K("right"))
    _frames(ws)
    assert sl.level("volume")[0] == 4


# -- a dialog's fields (GAPS 52) --------------------------------------------------------

class _Page:
    def __init__(self):
        self.host = ui_draw.AppHost()
        self.texts = ["Sky Run", ""]
        self.done = False
        self.pick = -1

    def frame(self, cv, th, buttons=("Cancel", "Save")):
        self.host.begin(cv, th, (0, 0, cv.w, cv.h))
        self.pick = ui.dialog("Cart info", buttons)
        for i, label in enumerate(("Title", "Made by")):
            got, enter = ui.field(label, self.texts[i], 24, {"id": label})
            self.texts[i] = got
            self.done = self.done or enter
        ui.dialog()


def _typed(page, key):
    a = page.host.inp
    a[5] = key


def test_a_dialogs_fields_are_captioned_the_first_focused_and_enter_its_last_button(tmp_path):
    ws = build_ws(tmp_path)
    cv, th = ws.sys_canvas, ws.theme_colors
    page = _Page()
    for _ in range(2):
        page.frame(cv, th)
    title, made = page.host.box("Title"), page.host.box("Made by")
    assert title[1] + title[3] <= made[1]          # a field under the one before
    # each stop holds its caption over its box: taller than a bare field row
    assert title[3] >= 2 * ui_draw.metrics("value_row")[30] - 4
    _typed(page, ord("!"))                         # the first field holds focus
    page.frame(cv, th)
    assert page.texts[0] == "Sky Run!"
    _typed(page, 9)                                # Tab: the next field
    page.frame(cv, th)
    _typed(page, ord("A"))
    page.frame(cv, th)
    assert page.texts == ["Sky Run!", "A"]
    _typed(page, 13)                               # Enter: the dialog's last button
    page.frame(cv, th)
    assert page.pick == 1


def test_a_dialog_with_no_buttons_hands_enter_to_its_field(tmp_path):
    ws = build_ws(tmp_path)
    cv, th = ws.sys_canvas, ws.theme_colors
    page = _Page()
    for _ in range(2):
        page.frame(cv, th, ())
    assert page.host.box("Cancel") is None
    _typed(page, 13)
    page.frame(cv, th, ())
    assert page.done and page.pick == -1


# -- flat fills ---------------------------------------------------------------------

def _pixels(cv, r):
    x, y, w, h = r
    b = cv._buf
    return [bytes(b[2 * (yy * cv.w + x):2 * (yy * cv.w + x + w)]) for yy in range(y, y + h)]


def test_a_flat_toast_and_a_card_touch_nothing_past_their_drop(tmp_path):
    """GAPS 51 and the flat card: no soft shadow blends over what is under
    them -- a toast's pill paints its own rect, a card its rect and its 2 px
    drop."""
    ws = build_ws(tmp_path)
    cv, th = ws.sys_canvas, ws.theme_colors
    cv.rect(0, 0, cv.w, cv.h, th["panel"])
    before = bytes(cv._buf)
    host = ui_draw.AppHost()
    host.begin(cv, th, (0, 0, cv.w, cv.h))
    ui.at(20, 60, cv.w - 40, 40)
    x, y, w, h = ui.toast("Opening Sky Run…", "game", {"flat": True})
    after = bytes(cv._buf)
    for yy in range(cv.h):
        for xx in (x - 3, x + w + 2):
            if 0 <= xx < cv.w and not (y <= yy < y + h):
                o = 2 * (yy * cv.w + xx)
                assert after[o:o + 2] == before[o:o + 2]
    cv.rect(0, 0, cv.w, cv.h, th["panel"])
    before = bytes(cv._buf)
    ui_draw.modal(cv, th, (40, 40, 200, 100))
    after = bytes(cv._buf)
    changed = [i // 2 for i in range(0, len(after), 2) if after[i:i + 2] != before[i:i + 2]]
    xs = [p % cv.w for p in changed]
    ys = [p // cv.w for p in changed]
    assert min(xs) >= 40 and max(xs) < 240 and min(ys) >= 40 and max(ys) < 142


# -- the loading pill (GAPS 51) ---------------------------------------------------------

def test_the_loading_pill_says_what_opens_over_its_card(tmp_path, monkeypatch):
    ws = no_unlock_banner(build_ws(tmp_path))
    _frames(ws)
    sel = ws.launcher.selected()
    said = []
    real = ui.toast
    monkeypatch.setattr(ui, "toast", lambda t, k="check", o=None: (said.append((t, k, o)),
                                                                   real(t, k, o))[1])
    ws._draw_loading_toast()
    text, kind, o = said[-1]
    assert text == "Opening %s…" % sel["title"] and o == {"flat": True}
    grid = ws.launcher_layer.grid
    card = grid.tile_rect(grid.sel)
    x, y, w, h = ws._loading_rect
    mid = y + h // 2
    assert card[1] <= mid < card[1] + card[3]      # over the middle of the card


# -- the saver and the text console --------------------------------------------------

def test_the_saver_is_black_with_the_cover_and_its_title(tmp_path):
    ws = no_unlock_banner(build_ws(tmp_path))
    _frames(ws)
    ws.saver_state(True)
    cv = ws.sys_canvas
    assert ws._saver.frame(0) is True
    corner = bytes(cv._buf[0:2])
    assert corner == bytes(cv._buf[2 * (cv.w * cv.h - 1):2 * (cv.w * cv.h)])
    assert ws._saver.frame(0) is False            # one frame every six seconds


def test_the_text_console_marks_the_answer_and_says_when_the_script_ended(tmp_path):
    from runtime import text_console
    ws = build_ws(tmp_path)
    con = ws.script_console
    con.start("first")
    con.write("Hello\n")
    con.ask("Name? ")
    con.ed.set_text("Ada")
    con.submit()
    assert con.text().splitlines()[-1] == "Name? Ada"      # read without its mark
    raw = con._buf[(con._start + con.count() - 1) % con._cap]
    assert raw == "Name? " + text_console._ANSWER + "Ada"
    con.ask("")
    con._answer = "x"
    con.ask("")                                    # the script takes its line
    tick = con.driver(None)
    tick(DT)
    assert con.ended and not con.reading()


def test_the_dialog_caps_say_real_tdeck_keys():
    """Every hint the step's surfaces draw on the T-Deck names a key it has:
    its actions (`cancel` is Mic where letters type, `back` K), never Tab,
    Esc or Ctrl (ACT_KEYS in native/moy_ui/moy_ui_parts.c)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "runtime"
    for name in ("cards_layer.py", "block_editor_ui.py", "popover.py", "text_console.py",
                 "web_console_ui.py", "code_layer.py", "console_notices.py"):
        src = (root / name).read_text()
        for word in ('"Tab"', '"Esc"', '"Ctrl', '"Mic"', '"K"'):
            assert word not in src, "%s spells %s: name the action" % (name, word)

