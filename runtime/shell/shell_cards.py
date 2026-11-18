# Map (grep -n a name to jump there):
#   PauseMenu                   the card over a held game: Resume, Options, Quit
#   DeleteConfirm               "Delete <title>?" before any cart is deleted
#   SystemMenuUI                the system menu's rows and their actions
#   SystemMenuUI.card_rows      the home's selected card as a menu group
#   SystemMenuUI.open_card      that group alone, hung at a card (long press, pad X)
#   SystemMenuUI._draw_look     the rows as the look's popover card
#   AboutCard                   the system menu's About: Moy, the version, the board, two rows
"""The cards the console puts up over what is running: the pause and stop
cards (`PauseMenu`), the delete confirm (`DeleteConfirm`), the system menu
(`SystemMenuUI`) and its About card (`AboutCard`).
"""

from widgets import DANGER_ICON as _DANGER_ICON
import chrome_ops as _ch
import ui_draw as _ui_draw
import ui as _ui
import layout_base as _lb
import shell_layout as _shell_layout
import app_perms as _perms
import moy_carts as _carts
import about_ui as _about_ui


# The 2026-10 look's words and icons for the system menu's rows, by string id
# (docs/history/look_2026-10.md step 25a).
_LOOK_TEXT = {"menu.cart": "Cart", "menu.delete_cart": "Delete cart",
              "menu.favourite_add": "Add favourite",
              "menu.favourite_remove": "Remove favourite", "menu.system": "System",
              "menu.home": "Home",
              "menu.clear_search": "Clear search", "menu.search": "Search",
              "menu.tasks": "Tasks",
              "menu.settings": "Settings", "menu.about": "About", "menu.sleep": "Sleep",
              "menu.reboot": "Restart Moybyte"}
_LOOK_ICON = {"menu.delete_cart": "trash", "menu.home": "home",
              "menu.favourite_add": "star", "menu.favourite_remove": "star_on",
              "menu.clear_search": "search", "menu.search": "search",
              "menu.tasks": "layout",
              "menu.settings": "settings", "menu.about": "doc", "menu.sleep": "sleep",
              "menu.reboot": "power"}
# The selected card's verbs (LauncherHomeLayer.card_verbs) as menu rows:
# (the chrome's label, icon, the look's text); the star's are the string ids'.
_CARD_ROW = {"change": ("CHANGE", "pencil", "Change"), "copy": ("DUPLICATE", "dup", "Duplicate"),
             "send": ("SEND", "export", "Send"), "delete": ("DELETE", "trash", "Delete")}
# The system group's beam row (runtime/run/link_beam.py), by the beam under
# way: none, a send, a receive.
_BEAM_ROW = {None: ("RECEIVE CART", "wifi", "Receive a cart"),
             1: ("STOP SENDING", "stop", "Stop sending"),
             2: ("STOP RECEIVING", "stop", "Stop receiving")}


def _row(kind, sid, action=None):
    """A Popup row: (kind, the chrome's label, action, icon, the look's text)."""
    return (kind, _ui_draw.s(sid), action, _LOOK_ICON.get(sid, ""), _LOOK_TEXT[sid])


def _in_box(px, py, b):
    return b is not None and b[0] <= px < b[0] + b[2] and b[1] <= py < b[1] + b[3]


def _desk_class():
    return _lb.metric_class() == _lb.DESK


class _CardVerb:
    """A card group row's action: the home's verb on its selected card.
    Its Delete asks first, as every route to a delete does (`DeleteConfirm`)."""

    def __init__(self, home, verb):
        self.home = home
        self.verb = verb

    def __call__(self):
        self.home._do_verb(self.verb)


class PauseMenu:
    """The pause menu (docs/history/look_2026-10.md step 25a): over a fullscreen game
    Super holds the run (`Pace.pause`: no tick, no draw, its clock and its
    music held) and a card centred on the glass names the game and offers
    Resume, Restart, Options and Quit; Esc, B or Super again resumes, and
    the cart's next frame comes as if no time passed. On a board with a
    desk, in both modes, over a game full screen or docked in the Editor's
    Run slot, Play in a window follows Resume (owner, 2026-10-31): the run
    goes on in a desk window of its own (`WindowedWM.play_in_window`); over
    a game already in a window there is no such row. On a handheld
    Minimise follows Resume where the run can join the running set
    (owner, 2026-11-03; `Procs.minimise`): the game is held where it is
    and the console goes home. Options is the card's
    second page, the console's Volume (Esc goes back). The card is a
    popover of the public UI API's with nothing to hang from, holding a
    `ui.menu`, drawn on the system canvas over the frozen frame; the menu's
    keys and taps are this object's, so an action runs in the input phase,
    and Options' slider takes the engine's.

    A stopped run is its sibling, the stop card (GAPS 49, `stop`): the same
    card over the run's last frame washed, naming the cart and Stopped, the
    sentence (`say.crash_line`), then See why (the Code tab on the line,
    `Workstation._crash_to_code`; none for a compiled or native cart or
    one with nothing to edit), Restart, Home and Details (the error exact
    in the code face on a `sunk` inset), its keys in its foot. A notice (a
    compiled cart this console cannot run) is the same card with its words
    in the body face and Open Updates (a newer console's) or Home. Esc, B or
    Super leave for Home.

    What a kid reads comes first, in the shell's strings: the sentence,
    then what to do (`stop.yours` where See why opens the line, else
    `stop.maker`, a compiled cart's, whose bug only its maker can fix). The
    error exact, or a notice's cause, waits under Details, the card's last
    row, for a parent or the cart's author: chosen, it stands where the
    words were, and chosen again, the words come back. A compiled cart that ships
    `src/` has See the code, its `src/` to read (`Workstation.see_code`)."""

    ITEMS = ("Resume", "Restart", "Options", "Quit")
    ICONS = ("play", "rotate", "settings", "close")
    MINIMISE = "Minimise"
    HAND_ITEMS = ("Resume", MINIMISE, "Restart", "Options", "Quit")
    HAND_ICONS = ("play", "minimize", "rotate", "settings", "close")
    WINDOW = "Play in a window"
    DESK_ITEMS = ("Resume", WINDOW, "Restart", "Options", "Quit")
    DESK_ICONS = ("play", "maximize", "rotate", "settings", "close")
    SEE_WHY, RESTART, HOME, UPDATES = "See why", "Restart", "Home", "Open Updates"
    SEE_CODE, DETAILS = "See the code", "Details"
    _STOP_ICONS = {"See why": "code", "Restart": "rotate", "Home": "home",
                   "Open Updates": "settings", "See the code": "code", "Details": "doc"}

    def __init__(self, ws):
        self.ws = ws
        self.open = False
        self.sel = 0
        self.page = 0               # 0 the menu, 1 Options
        self._host = None
        # The run's frame under the card, kept as the game left it and held
        # washed as the card draws over it (`ui_draw.Hold`), where the game's
        # frame is the system canvas (the handheld): each frame starts from
        # it, and a smaller card (Options) and the resumed game find it
        # whole. Where the game is composited, its frame repaints under it.
        self._hold = _ui_draw.Hold()
        self._capture = False       # keep that frame before the card first draws
        self.stopped = False        # the stop card, over a run that stopped
        self.details = False        # ...its Details shown: the error exact
        self.rows = self.ITEMS      # the card's rows
        self.icons = self.ICONS

    def _width(self, desk):
        if self.stopped:
            fs = self.ws.layout.fs
            return 520 if desk else min(self.ws.sys_canvas.w - 32 * fs, 340 * fs)
        return 300 if desk else 188 * self.ws.layout.fs

    def prealloc(self):
        """Mint the layers the frame under the card is kept and held in
        (`ui_draw.held_layer`) where the game's frame is the system canvas:
        an idle frame's work, so the card's first frame does not mint them.
        The first, or None elsewhere or with no room."""
        ws = self.ws
        cv = ws.sys_canvas
        if ws.run.canvas is not cv:
            return None
        lay = _ui_draw.held_layer(ws, cv, 2)
        if lay is not None:
            _ui_draw.held_layer(ws, cv)
        return lay

    def show(self):
        ws = self.ws
        if ws.cart_error is not None:
            self.stop()
            return
        ws.run.player.pace.pause()
        self.stopped = False
        offers = getattr(ws.wm, "offers_window", None)
        if offers is not None and offers():
            self.rows, self.icons = self.DESK_ITEMS, self.DESK_ICONS
        elif ws.procs.handheld() and ws.procs._detachable():
            self.rows, self.icons = self.HAND_ITEMS, self.HAND_ICONS
        else:
            self.rows, self.icons = self.ITEMS, self.ICONS
        self._open()

    def stop(self):
        """Open the stop card over the run that stopped (its error, or its
        notice, is the Player's), its rows by the run's kind."""
        ws = self.ws
        player = ws.run.player
        self.stopped = True
        cart = ws.cart or {}
        if player.notice is not None:
            rows = (self.UPDATES, self.HOME) if player.newer else (self.HOME,)
            if player.notice_detail:
                rows += (self.DETAILS,)
        elif ws.run.project is None or ws.web.parked:
            rows = (self.RESTART, self.HOME, self.DETAILS)
        elif _carts.is_compiled(cart):
            rows = ((self.RESTART, self.SEE_CODE, self.HOME, self.DETAILS)
                    if ws.text.code_sources() else (self.RESTART, self.HOME, self.DETAILS))
        else:
            rows = (self.SEE_WHY, self.RESTART, self.HOME, self.DETAILS)
        self.details = False
        self.rows = rows
        self.icons = tuple([self._STOP_ICONS[r] for r in rows])
        self._open()

    def _open(self):
        ws = self.ws
        self.open = True
        self.sel = 0
        self.page = 0
        ws.input.spend_key()        # the key that opened it is no press of the menu's
        self._hold.drop()
        self.ws.bar_layer.drop_strips("pause")   # its bar's strip with it
        self._capture = True
        if self._host is None:
            self._host = _ui_draw.AppHost()
        else:
            self._host.reset()
        ws.pause_touch()

    def close(self):
        """Resume: the run is given back (a stopped run's card goes Home)."""
        if not self.open:
            return
        if self.stopped:
            self.choose_row(self.HOME)
            return
        self.open = False
        self._hold.unsnap(self.ws, self.ws.sys_canvas)   # the frame as the game left it
        self._hold.drop()
        self.ws.bar_layer.drop_strips("pause")   # its bar's strip with it
        self.ws.run.player.pace.resume()
        self.ws.pause_touch()

    def back(self):
        """Esc and B: Options back to the menu, the menu back to the game."""
        if self.page:
            self.page = 0
            self._host.reset()
            self.ws.pause_touch()
        else:
            self.close()

    def dismiss(self):
        """Close a stop card a run left, if one is up (its run gone)."""
        if self.open and self.stopped:
            self._shut()

    def _shut(self):
        """The stop card goes, the run's last frame put back."""
        self.open = False
        self.stopped = False
        self._hold.unsnap(self.ws, self.ws.sys_canvas)
        self._hold.drop()
        self.ws.bar_layer.drop_strips("pause")   # its bar's strip with it
        self.ws.pause_touch()
        self.ws.mark_dirty()

    def choose_row(self, row):
        """A stop card's row: See why throws into the Code tab on the line,
        See the code opens a compiled cart's `src/` there to read, Restart
        runs the cart again, Open Updates opens Settings' Updates, Home
        leaves for the run's caller, and Details shows or hides the error
        exact, the card staying up."""
        ws = self.ws
        if row == self.DETAILS:
            self.details = not self.details
            self._host.reset()
            ws.pause_touch()
            ws.mark_dirty()
            return
        self._shut()
        if row == self.SEE_WHY and ws.crash_to_code():
            return
        if row == self.SEE_CODE and ws.see_code():
            return
        if row == self.RESTART:
            ws.reset_canvas_state()
            ws.start()
            return
        ws.cart_error = None
        ws.reset_canvas_state()
        if ws.wm.top_is_player():
            ws.spine.exit_to_caller()
        if row == self.UPDATES:
            ws.open_settings()
            ws.settings_layer.show_row("ota_channel")

    def choose(self, i):
        ws = self.ws
        row = self.rows[i]
        if self.stopped:
            self.choose_row(row)
            return
        if row == "Resume":
            self.close()
            return
        if row == self.WINDOW:
            self.close()
            ws.wm.play_in_window()        # the same run, in a desk window
        elif row == self.MINIMISE:
            self._shut()                  # the run stays held: it joins the running set
            if not ws.procs.minimise():
                ws.run.player.pace.resume()
        elif row == "Restart":
            self.close()
            ws.reset_canvas_state()
            ws.start()                   # the same cart from the top, same caller
        elif row == "Options":
            self.page = 1
            self._host.reset()
            ws.pause_touch()
            return
        elif row == "Quit":
            self.close()
            ws.reset_canvas_state()
            ws.spine.exit_to_caller()
        ws.mark_dirty()

    def _unwash_strip(self, cv):
        """The desk's PROJECTS window's hint strip names the dialog's keys
        (`launcher_layer.modal_keys`), so it stays unwashed, as under the
        system menu (`SystemMenuUI._scrim_bands`): its pixels back from the
        window's buffer over the dialog's scrim, where the card is clear of
        it."""
        ws = self.ws
        p = ws.menu_ui._projects()
        blit = getattr(cv, "blit_strip_rect", None)
        if p is None or p[1] is None or blit is None:
            return
        sx, sy, sw, sh = p[1]
        card = self._host.box(self.title)
        if card is not None and card[0] < sx + sw and sx < card[0] + card[2] \
                and card[1] < sy + sh and sy < card[1] + card[3]:
            return
        win = ws.wm.windows["projects"]
        cx, cy = win.content_rect()[:2]
        blit(win.buf, cx, cy, sx, sy, sw, sh)

    def handle_input(self, i):
        if i.pressed("b") or i.pressed("stop") or getattr(i, "typed", 0) == 27:
            self.back()
            return
        if self.page:
            if self._host.keys(i):
                self.ws.pause_touch()
            return
        n = len(self.rows)
        if i.steps("up") and self.sel > 0:
            self.sel -= 1
            self.ws.pause_touch()
        elif i.steps("down") and self.sel < n - 1:
            self.sel += 1
            self.ws.pause_touch()
        elif i.pressed("a") or i.pressed("run"):
            self.choose(self.sel)

    def shows_bar(self):
        """Whether the bar shows over the paused game (owner, 2026-11-05):
        on a handheld, over a game full screen, so the status cluster and
        the control center are reachable while it is paused."""
        ws = self.ws
        return (self.open and not self.stopped and ws.wm.top_is_player()
                and not ws.windowed_chrome and not _desk_class())

    def at_top(self):
        """The keys are on the card's first row: up walks onto the bar."""
        return not self.page and self.sel == 0

    def handle_pointer(self, px, py, click):
        host = self._host
        ws = self.ws
        if click and self.shows_bar() and ws.bar_layer.handle_bar_tap("pause", px, py):
            ws.pause_touch()
            return
        if self.page:
            if click and not _in_box(px, py, host.box("Pause")):
                self.back()
            elif host.point(px, py, click, bool(getattr(ws.pointer, "down", False))):
                ws.pause_touch()
            return
        if not click:
            return
        for j, t in enumerate(self.rows):
            if _in_box(px, py, host.box(t)):
                self.sel = j
                self.choose(j)
                return
        if not self.stopped and not _in_box(px, py, host.box("Pause")):
            self.close()                  # a press outside the card resumes

    def surface(self):
        """The rect the card centres on: the paused game's window content on
        the windowed desk, else the glass."""
        ws = self.ws
        wm = ws.wm
        if getattr(wm, "mode", None) == "desktop":
            win = getattr(wm, "windows", {}).get("desktop")
            if win is not None and not win.minimized:
                return win.content_rect()
        cv = ws.sys_canvas
        return (0, 0, cv.w, cv.h)

    def draw(self):
        # The card is the shell's, not the held cart's: the shell's fonts and
        # icons, whatever the run left the app-cart boundary at.
        was = _ui_draw.cart_text(False)
        try:
            self._draw()
        finally:
            _ui_draw.cart_text(was)

    def _draw(self):
        ws = self.ws
        cv = ws.sys_canvas
        hold = self._hold
        top = ws.wm.top_is_player()
        if self._capture and top and ws.sysmenu.open and self.shows_bar():
            self._capture = False
            if self.prealloc() is not None:
                hold.snap(ws, cv)
                _ui_draw.scrim(cv, ws.theme_colors, self.scrim_rect())
                hold.keep(ws, cv, (0, 0, cv.w, cv.h), self._washed_key(cv), True)
        if ws.sysmenu.open and self.shows_bar():
            # a popover the bar opened over the paused game (the control
            # center): over the paused game kept washed, the bar and the
            # popover, which the pause draws (the popover's own layer is
            # under this one); the card waits
            if not (hold.snapped and hold.put(ws, cv, (0, 0, cv.w, cv.h),
                                              self._washed_key(cv))):
                _ui_draw.scrim(cv, ws.theme_colors, self.scrim_rect())
            ws.icon_spec()
            self._bar()
            ws.menu_ui._draw_sysmenu()
            return
        if not top:
            self._capture = False         # a notice over the screen it opened from
        elif self._capture:
            self._capture = False
            if self.prealloc() is not None:
                hold.snap(ws, cv)
        elif hold.snapped and hold.put(ws, cv, (0, 0, cv.w, cv.h), self._washed_key(cv)):
            ws.icon_spec()
            self._card()                  # over the frame kept washed
            self._bar()
            return
        else:
            hold.unsnap(ws, cv)
        ws.icon_spec()                    # the theme's set in the engine, as the bar has it
        # the paused game dims under the card (D-01): over the frame just
        # put back, or the composite just made, never over the last scrim;
        # the whole glass, or on the desk the content under its bar
        if hold.snapped or ws.run.canvas is not cv or not top:
            _ui_draw.scrim(cv, ws.theme_colors, self.scrim_rect())
            if hold.snapped and top:
                hold.keep(ws, cv, (0, 0, cv.w, cv.h), self._washed_key(cv), True)
        self._card()
        self._bar()

    def _bar(self):
        """The bar over the paused game, where it shows (`shows_bar`)."""
        if self.shows_bar():
            self.ws.bar_layer.draw_status_strip("pause")

    def _washed_key(self, cv):
        """What the kept frame washed is a function of: the frame kept, the
        glass and the look."""
        return (self.ws.held_pages[2], cv, cv.w, cv.h, self.scrim_rect(),
                self.ws.look.look_gen)

    def scrim_rect(self):
        """What the card's scrim washes: on the desk, and under a notice
        over the screen it was asked from, the content under its bar, else
        the glass -- a desk game full screen's too."""
        ws = self.ws
        wm = ws.wm
        if not wm.top_is_player() or (getattr(wm, "mode", None) == "desktop"
                                      and not self._over_full()):
            lay = ws.layout
            return (lay.cx, lay.cy, lay.cw, lay.ch)
        cv = ws.sys_canvas
        return (0, 0, cv.w, cv.h)

    def _over_full(self):
        """Whether the card is over a desk game full screen."""
        win = getattr(self.ws.wm, "windows", {}).get("desktop")
        return win is not None and not win.minimized and win.saved is not None

    def draw_card(self):
        """The card alone, over a scrim already there: the desk's frame
        that paints the card's change and nothing under it."""
        was = _ui_draw.cart_text(False)
        try:
            self.ws.icon_spec()
            self._card()
        finally:
            _ui_draw.cart_text(was)

    def card_rect(self):
        """The rect the card's last draw painted, its shadow's reach with
        it, on the glass; None before it first draws. A draw that had not
        settled lays its body out before it knows its height, down to the
        foot of the surface it centres on."""
        box = self._host.box("Pause") if self._host is not None else None
        if box is None:
            return None
        x, y, w, h = box
        dp = _ui_draw.depth()
        r = (dp[8] + abs(dp[9]) + abs(dp[10]) + 2) * self.ws.layout.fs
        cv = self.ws.sys_canvas
        x0, y0 = max(0, x - r), max(0, y - r)
        x1, y1 = min(cv.w, x + w + r), min(cv.h, y + h + r)
        if self._host.owed:
            sx, sy, sw, sh = self.surface()
            y1 = max(y1, min(cv.h, sy + sh + r))
        return (x0, y0, x1 - x0, y1 - y0)

    def _card(self):
        ws = self.ws
        cv = ws.sys_canvas
        host = self._host
        desk = _desk_class()
        if not self.page:
            host.focus(self.rows[self.sel])
        host.begin(cv, ws.theme_colors, self.surface(), desk)
        _ui.popover("Pause", self._width(desk))
        cart = ws.cart
        _ui.icon("moy")
        if self.stopped:
            self._stop_body(cart)
            _ui.popover()
            if host.due():
                ws.request_frame()
            return
        _ui.info(str((cart.get("title") if cart else "") or "Game"), "Paused")
        if self.page:
            sl = ws.settings_layer
            cur, lo, hi = sl.level("volume")
            if cur == lo:
                _ui.icon("mute")
            got = _ui.slider("Volume", cur, lo, hi, {"steps": True})
            if got != cur:
                sl.set_level("volume", got)
            _ui.hints(("change", "Change", "back", "Back"))
        else:
            _ui.menu("", self.rows, self.icons)
            _ui.hints(("back", "Resume"))
        _ui.popover()
        if host.due():
            ws.request_frame()

    def _error_lines(self):
        """The error exact's inset: four lines on the desk, three on a
        handheld, whose glass holds it with the card's rows."""
        return _STOP_ERROR if _desk_class() else _STOP_ERROR_HAND

    def _stop_body(self, cart):
        """The stop card's body under its icon: the cart and its state, what
        happened, the rows and the keys."""
        ws = self.ws
        player = ws.run.player
        title = str((cart.get("title") if cart else "") or "Game")
        if player.notice is not None:
            _ui.info(title, "")
            _ui.rule()
            _ui.label(str(player.notice_title or ""), _STOP_SENTENCE)
            if self.details and player.notice_detail:
                _ui.label(player.notice_detail, self._error_lines())
            else:
                _ui.label(player.notice, _STOP_WORDS)
        else:
            _ui.info(title, "Stopped")
            _ui.rule()
            _ui.label(_ch.crash_title(ws.cart_error, ws.run.player.crash_line), _STOP_SENTENCE)
            if self.details:
                _ui.label(str(ws.cart_error or "").strip() or _ui_draw.s("chrome.no_text"),
                          self._error_lines())
            else:
                _ui.label(_ui_draw.s("stop.yours" if self.SEE_WHY in self.rows
                                     else "stop.maker"), _STOP_WORDS)
        _ui.menu("", self.rows, self.icons)
        if _desk_class() or not (self.details or self.SEE_CODE in self.rows):
            # a handheld's glass holds the keys under three rows and the
            # words, not under four or the error exact
            _ui.hints(("confirm", "Choose", "back", "Home"))


# The stop card's lines: the sentence, a notice's words, the error exact.
_STOP_SENTENCE = {"wrap": True, "lines": 2}
_STOP_WORDS = {"wrap": True, "lines": 4, "tone": "dim"}
_STOP_ERROR = {"wrap": True, "lines": 4, "face": "mono", "sunk": True}
_STOP_ERROR_HAND = {"wrap": True, "lines": 3, "face": "mono", "sunk": True}
_WRAP = {"wrap": True, "lines": 3}


class DeleteConfirm:
    """The one question before a cart is deleted, on every route that
    deletes one: Make's X and DEL, the desk PROJECTS window's Delete, a
    card menu's Delete and the system menu's "Delete cart". Deleting a cart
    is permanent (the Trash holds files, never carts), so the console asks
    with the public UI API's dialog, the kind Files asks "Empty the Trash?"
    with: "Delete <title>?", "This can't be undone.", Cancel and Delete,
    Delete the danger button and Cancel where focus opens. The dialog dims
    the content under the bars (D-01), centred in it.

    Its keys and taps go to the dialog's engine, which moves focus between
    the buttons and activates one; B, Esc, HOME, the console's key (the
    T-Deck's Mic) and a press outside the card cancel. The answer is read
    as the dialog draws, and the delete runs at the frame's tail.

    The same dialog asks the running set's one question (`ask_close`,
    runtime/shell/procs.py): a game that does not fit beside the ones
    running names what to close, Cancel and Close; and the Player's before
    a compiled game the VM must stop for while the music holds the Wi-Fi,
    Cancel and Stop & play. Its line wraps, the card growing to fit.

    And a cart beamed from a console nearby (`ask_close` with `kind` and
    `no`, runtime/run/link_beam.py): "Receive <title>?", the console it
    comes from, No and Receive; every way the dialog closes but Receive
    answers no.

    And the first run of a cart that arrived as files (`ask_lines`,
    `Notices.ask_first`): "Open <title>?" over the install prompt's
    lines, as Get Carts draws them (`app_perms.show`), No and Open.

    And the intents' "open with" menu (`choose`, runtime/system/intents.py):
    "Open with", the item's name over a menu of the apps that open it, each
    row its app's icon and title, the one chosen last first and focused, and
    Cancel. The rows are stops of the dialog's engine: up and down move
    through them, Enter or A picks, a tap on a row picks it."""

    BUTTONS = ("Cancel", "Delete")

    def __init__(self, ws):
        self.ws = ws
        self.open = False
        self.cart = None
        self.title = ""
        self._then = None            # what follows a delete (the open cart's: go home)
        self._host = None
        self.focused = None          # the focused button as the last frame left it
        self.line = self.UNDONE      # the line under the title
        self.buttons = self.BUTTONS
        self._act = None             # what the danger button does
        self.rows = ()               # a choice's rows (`choose`), else ()
        self.icons = ()
        self._choice = None          # fn(i) told the row picked, None on a cancel
        self.lines = ()              # a first-run prompt's lines (`ask_lines`), else ()
        self.kind = "danger"         # the verb button's kind
        self._no = None              # what a cancel answers (`ask_close`'s `no`)

    UNDONE = "This can't be undone."
    CLOSE = ("Cancel", "Close")

    def _arm(self):
        ws = self.ws
        self.open = True
        self.focused = None
        ws.input.spend_key()         # the key that asked is no press of the dialog's
        if self._host is None:
            self._host = _ui_draw.AppHost()
        else:
            self._host.reset()
        ws.mark_dirty()

    def choose(self, title, label, rows, icons, then):
        """Ask which of `rows` (their `icons` beside them), under `label`:
        `then(i)` with the row picked, at the frame's tail, or `then(None)`
        where the menu was cancelled."""
        self._drop_choice()
        self._no = None
        self.cart = None
        self.title = title
        self.line = label
        self.lines = ()
        self.rows = list(rows)
        self.icons = list(icons)
        self.buttons = ("Cancel",)
        self._then = self._act = None
        self._choice = then
        self._arm()
        return True

    def _drop_choice(self):
        fn, self._choice = self._choice, None
        self.rows = self.icons = ()
        if fn is not None:
            fn(None)

    def ask_close(self, title, line, then, buttons=None, kind="danger", no=None):
        """Ask to close what `title` names so that what `line` says can
        open; `then()` closes it and opens that. `buttons` (Cancel, then
        the verb) name the answers where Close does not, `kind` is the
        verb's button, and `no()` runs at the tail on every other answer."""
        self._drop_choice()
        self._no = no
        self.kind = kind
        self.cart = None
        self.title = title
        self.line = line
        self.lines = ()
        self.buttons = buttons or self.CLOSE
        self._then = None
        self._act = then
        self._arm()
        return True

    def ask_lines(self, title, lines, then, buttons):
        """Ask the install prompt's question: `title` over the prompt's
        `lines` (`app_perms.show`), `buttons` No and the verb; `then()` on
        the verb."""
        self.ask_close(title, "", then, buttons)
        self.lines = list(lines)
        return True

    def ask(self, cart, then=None):
        """Ask before `cart` (a cart dict, or None for the open cart) is
        deleted; `then()` runs after a delete that removed it."""
        ws = self.ws
        target = cart if cart is not None else ws.cart
        if target is None or not target.get("path"):
            return False
        name = str(target.get("title") or "").strip()
        self._drop_choice()
        self._no = None
        self.kind = "danger"
        self.cart = cart
        self.title = "Delete %s?" % name if name else "Delete this cart?"
        self.line = self.UNDONE
        self.lines = ()
        self.buttons = self.BUTTONS
        self._then = then
        self._act = self._delete
        self._arm()
        return True

    def cancel(self):
        """The cart stays."""
        if not self.open:
            return
        self.open = False
        self.cart = self._then = self._act = None
        self._drop_choice()
        no, self._no = self._no, None
        if no is not None:
            self.ws.defer(no, toast=False)
        self.ws.mark_dirty()

    def _delete(self):
        ws = self.ws
        cart, then = self.cart, self._then
        self.open = False
        self.cart = self._then = None
        before = len(ws.carts.all)
        ws.carts.delete(cart)
        if then is not None and len(ws.carts.all) < before:
            then()
        ws.mark_dirty()

    def _unwash_strip(self, cv):
        """The desk's PROJECTS window's hint strip names the dialog's keys
        (`launcher_layer.modal_keys`), so it stays unwashed, as under the
        system menu (`SystemMenuUI._scrim_bands`): its pixels back from the
        window's buffer over the dialog's scrim, where the card is clear of
        it."""
        ws = self.ws
        p = ws.menu_ui._projects()
        blit = getattr(cv, "blit_strip_rect", None)
        if p is None or p[1] is None or blit is None:
            return
        sx, sy, sw, sh = p[1]
        card = self._host.box(self.title)
        if card is not None and card[0] < sx + sw and sx < card[0] + card[2] \
                and card[1] < sy + sh and sy < card[1] + card[3]:
            return
        win = ws.wm.windows["projects"]
        cx, cy = win.content_rect()[:2]
        blit(win.buf, cx, cy, sx, sy, sw, sh)

    def handle_input(self, i):
        if i.pressed("b") or i.pressed("stop") or i.pressed("home") or getattr(i, "typed", 0) == 27:
            self.cancel()
            return
        if self._host.keys(i):
            self.ws.mark_dirty()

    def handle_pointer(self, px, py, click):
        host = self._host
        ws = self.ws
        card = host.box(self.title)
        if click and card is not None and not _in_box(px, py, card):
            self.cancel()             # a press outside the card: the cart stays
            return
        if host.point(px, py, click, bool(getattr(ws.pointer, "down", False))):
            ws.mark_dirty()

    def surface(self):
        """What the dialog dims and centres in: the content under the bars."""
        lay = self.ws.layout
        return (lay.cx, lay.cy, lay.cw, lay.ch)

    def draw(self):
        if not self.open:
            return
        ws = self.ws
        cv = ws.sys_canvas
        ws.icon_spec()                # the theme's set in the engine, as the bar has it
        self._host.begin(cv, ws.theme_colors, self.surface(), _desk_class())
        got = -1
        if self.rows:
            pick = _ui.dialog(self.title, self.buttons, "normal")
            got = _ui.menu(self.line, self.rows, self.icons)
        elif self.lines:
            pick = _ui.dialog(self.title, self.buttons, "primary")
            _perms.show(_ui, self.lines)
        else:
            pick = _ui.dialog(self.title, self.buttons, self.kind)
            _ui.label(self.line, _WRAP)
        _ui.dialog()
        self._unwash_strip(cv)
        self.focused = _ui.focus()
        if got >= 0:
            self.open = False         # the answer is in: the tail hands it over
            fn, self._choice = self._choice, None
            self.rows = self.icons = ()
            if fn is not None:
                ws.defer(lambda: fn(got), toast=False)
        elif pick == 0:
            self.cancel()
        elif pick == 1:
            self.open = False         # the answer is in: the tail acts
            act, self._act = self._act, None
            self._no = None
            if act is not None:
                ws.defer(act, toast=False)
        elif self._host.due():
            ws.request_frame()


# A popover's fade, rows at the scale (native/moy_ui/moy_ui_api_int.h's FADE_H).
_FADE_H = 10


def _peek(rows, cut, fade, rh):
    """The body a popover cut at `cut` shows (the engine's `pop_body` and
    `peek_note`): the cut where it falls inside a row at least `fade` (half
    the engine's FADE_H at the scale) under its middle -- a part taller
    than a row `rh`, a row's middle down it --, else the deepest such cut
    above it, so the top of a row's words always reads above the fade's
    dense half. `rows` are (top, height, peekable)."""
    best = 0
    for t, h, row in rows:
        if not row:
            continue
        at = t + min(h, rh) // 2 + fade
        if at <= cut < t + h:
            return cut
        if best < at <= cut:
            best = at
    return best if 0 < best < cut else cut


class SystemMenuUI:
    """The ≡ system menu's rows and their actions (#52). Its drawing is the
    look's card (`_draw_look`); About is about_ui's card. The `sysmenu` Popup, the `_about` flag,
    `reboot_hook` and `toggle_sysmenu()` stay on the Workstation (its tested
    surface); the privileged verbs (REBOOT, DELETE) are reached through it."""

    def __init__(self, ws):
        self.ws = ws
        self._host = None           # the look's card: its verbs' context
        self._card_at = (0, 0)      # where a card's menu hangs (`open_card`)
        self._card_proj = False     # a card's menu hangs from PROJECTS' hero
        self.about = AboutCard(ws)
        # The handheld home under the card, washed (`put_home`), and the
        # frame that put it back
        self._held = _ui_draw.Hold()
        self._put_f = -1
        self._key = None            # the key `put_home` asked, on frame `_key_f`
        self._key_f = -1

    def _sysmenu_items(self):
        """The rows for this open of the ≡ menu (see class note for the tuple form).
        The cart group is OMITTED entirely (not greyed) when it has no row."""
        rows = []
        cart = self.ws.cart
        if cart is not None:
            # headed by the cart's own title over Delete (which asks first:
            # `DeleteConfirm`), offered only for a cart the store can delete: a
            # built-in is the console's, so it has no group. A run restarts
            # from the pause menu.
            if self._deletable(cart):
                title = cart.get("title") or ""
                head = _row("header", "menu.cart")
                if title:
                    head = ("header", title.upper(), None, "", title)
                rows.append(head)
                rows.append(_row("item", "menu.delete_cart", self._menu_delete_cart))
                rows.append(("sep",))
        elif self.ws.screen == "launcher" or self._projects_focused():
            # over the home, or the desk's PROJECTS window holding the focus,
            # the selected card's group in the open cart's place: its title
            # over its verbs (`card_rows`)
            card = self.card_rows(None if self.ws.screen == "launcher"
                                  else self.ws.projects_layer)
            if card:
                rows.extend(card)
                rows.append(("sep",))
        rows.append(_row("header", "menu.system"))
        # Home, wherever the screen is not the home: the way out of a surface
        # with no back of its own (the Editor's bar draws none, and the
        # T-Deck's Code tab types K), as the home key goes (`home_key`).
        ws = self.ws
        if not ws.windowed_chrome and not ws.wm.top_is("launcher"):
            rows.append(_row("item", "menu.home", ws.home.home_key))
        # SEARCH (#105): only meaningful over the run-grid, so it's offered ONLY
        # from the launcher home -- the label flips to CLEAR SEARCH once a query
        # is active/typing, so the same row both opens and dismisses it.
        if self.ws.screen == "launcher":
            sid = ("menu.clear_search" if (self.ws.search_typing or self.ws.search_query)
                   else "menu.search")
            rows.append(_row("item", sid, self.ws.home.toggle_search))
        if not ws.wm.top_is(ws.tasks_layer.id):
            rows.append(_row("item", "menu.tasks", ws.open_tasks))
        # Beaming a cart where the console has a radio link: the receive
        # state opened, or the beam under way stopped.
        link = getattr(ws, "link", None)
        if link is not None and ws.netplay is None:
            role = link.beam_role()
            label, icon, text = _BEAM_ROW.get(role, _BEAM_ROW[None])
            rows.append(("item", label, self._menu_beam, icon, text))
        rows.append(_row("item", "menu.settings", self.ws.open_settings))
        rows.append(_row("item", "menu.about", self._menu_about))
        # Sleep where the board's idle ladder can blank its light (absent on
        # a tier with none); restarting the console is last.
        if getattr(self.ws, "idle_ladder", None) is not None:
            rows.append(_row("item", "menu.sleep", self._menu_sleep))
        rows.append(_row("item", "menu.reboot", self._menu_reboot))
        return rows

    def card_rows(self, home=None):
        """A home's selected card as a menu group (the fullscreen home's,
        else `home`'s, the desk's projects window): its title over a row a
        verb of `card_verbs` (the favourite star's words follow its state;
        Delete asks first, as the cart group's does, and stands set apart
        under a rule). [] where the card has none."""
        home = home or self.ws.launcher_layer
        verbs = home.card_verbs()
        if not verbs:
            return []
        it = home.grid.selected()
        title = it.get("title") or ""
        rows = [("header", title.upper(), None, "", title) if title
                else _row("header", "menu.cart")]
        for v in verbs:
            if v == "favourite":
                fav = self.ws.carts.is_favorite(it)
                rows.append(_row("item", "menu.favourite_remove" if fav
                                 else "menu.favourite_add", home.toggle_favourite))
            else:
                label, icon, text = _CARD_ROW[v]
                if icon == _DANGER_ICON:
                    rows.append(("sep",))     # a verb that destroys, set apart
                rows.append(("item", label, _CardVerb(home, v), icon, text))
        return rows

    def open_card(self, home, at):
        """`home`'s selected card's group alone, as a menu hung at `at`
        (x, y): what a long press on the card and a gamepad's X open. False
        where the card has no verbs."""
        rows = self.card_rows(home)
        if not rows:
            return False
        ws = self.ws
        m = ws.sysmenu
        m.fs = ws.look.effective_font_scale()
        m.cs = ws.layout.cs
        m.w = 0
        # from the desk's PROJECTS window the menu hangs from its hero's
        # cover, below or above it and inside the window (`_menu_cell`,
        # `surface`), never over the cover it acts on
        proj = self._projects() if home is ws.projects_layer else None
        self._card_proj = proj is not None and proj[2] is not None
        if self._card_proj:
            hx, hy, hw, hh = proj[2]
            at = (hx + hw // 2, hy + hh)
        self._card_at = at
        m.show(rows, widget="card")
        ws.popover_touch()
        return True

    def _deletable(self, cart):
        """Whether the open cart is the store's to delete: a store that
        writes, and a cart a Delete takes (`CartManager.deletable`)."""
        return bool(getattr(self.ws, "can_manage", True)) and self.ws.carts.deletable(cart)

    def _menu_delete_cart(self):
        # Ask before deleting the OPEN cart (None: carts.delete() targets
        # ws.cart, which need not be the home's selection); a delete that
        # removed it goes home. carts.delete() guards read-only / last-cart.
        self.ws.confirm.ask(None, self.ws.home.go_home)

    def _menu_beam(self):
        ws = self.ws
        if ws.link.beam_role() is None:
            ws.link.beam_receive(ws)
        else:
            ws.link.beam_stop(ws)
        ws.mark_dirty()

    def _menu_about(self):
        # About's card over the washed content (AboutCard).
        self.ws.mark_dirty()
        self.about.open()
        self.ws.about = True

    def _menu_sleep(self):
        # The idle ladder's BLANK rung now: the light goes off and the next
        # input wakes the screen (and is swallowed, as the ladder's own is).
        self.ws.settings_layer.commit_name()   # a Name still being typed is kept
        self.ws.idle_ladder.sleep()

    def _menu_reboot(self):
        # Device: the injected reboot hook (machine.reset). Host / no hook: a safe
        # fallback to the home launcher (a hard reset would kill the sim window).
        self.ws.mark_dirty()
        self.ws.settings_layer.commit_name()   # a Name still being typed is kept
        hook = self.ws.reboot_hook
        if hook is not None:
            try:
                hook()
                return
            except Exception as exc:  # noqa: BLE001
                print("Moybyte reboot failed:", exc)
        self.ws.home.go_home()                 # safe stub when no reboot hook is wired

    def _draw_sysmenu(self):
        """The popover: the menu's rows as the look's card (`_draw_look`), or
        a component in the system's flat card."""
        ws = self.ws
        m = ws.sysmenu
        if self.looked():
            self._draw_look()
            return
        if m.comp is None:
            return
        # A component's popover: the system's flat card, its drop inside the
        # panel's rect, the component's verbs in it; on a handheld home the
        # hint strip says its keys
        x, y, w, h = m.panel_rect()
        cv = ws.sys_canvas
        th = ws.theme_colors
        desk = _desk_class()
        drop = 2 * (1 if desk else ws.layout.fs)
        _ui_draw.modal(cv, th, (x, y, w, h - drop), desk)
        m.comp.draw(cv, (x, y, w, h - drop))
        strip = self.about._strip()
        if strip is not None:
            cv.rect(strip[0], strip[1], strip[2], strip[3], th["panel"])
            _ui.at(strip[0], strip[1], strip[2], strip[3])
            _ui.hints(m.comp.keys())

    # -- the 2026-10 look's card (docs/history/look_2026-10.md step 25a) ----------------

    def scrims(self):
        """Whether a system modal's scrim is up: this menu or a card's menu,
        ABOUT, the pause menu, the delete confirm. A bar widget's popover (the calendar, Wi-Fi,
        power) hangs from its widget as an app's picker does, and dims
        nothing (docs/history/look_2026-10.md, D-01), but a handheld's
        control center, which dims the page beside it."""
        ws = self.ws
        m = ws.sysmenu
        return (m.open and (m.comp is None or getattr(m.comp, "SCRIM", None) is not None)) \
            or ws.about or ws.pause.open or ws.confirm.open

    def draw_full(self, dt):
        """The menu on a frame of the whole glass: the scrim, then the card.
        Where the handheld home lies under it alone the frame under the
        card is held washed (`ui_draw.Hold`, surface model §14.5): a
        frame that put it back draws the card alone."""
        ws = self.ws
        f = ws.frame_state.frames_drawn
        if self._put_f != f:
            cv = ws.sys_canvas
            # the key the home's put asked this frame, before it drew
            key = self._key if self._key_f == f else self._home_key(cv)
            if key is None:
                self.scrim()
            else:
                # the scrim stores to the held layer as it lands; the rest
                # of the glass is copied there after it
                g = ws.launcher_layer.grid
                still = not (g.dragging or g.flinging)
                lay = self._held.layer(ws, cv, still, dt)
                also = None if lay is None else getattr(lay, "_buf", None)
                self.scrim(also)
                self._held.keep(ws, cv, (0, 0, cv.w, cv.h), key, still, dt,
                                self.scrim_rects() if also is not None else ())
        self._draw_sysmenu()

    def put_home(self, cv, dt):
        """Put the home under the open menu back washed, as the frame that
        held it left it: True when it was, and the home draws nothing."""
        key = self._key = self._home_key(cv)
        self._key_f = self.ws.frame_state.frames_drawn
        if key is None or not self._held.put(self.ws, cv, (0, 0, cv.w, cv.h), key, dt):
            return False
        self._put_f = self._key_f
        return True

    def holds_home(self):
        """Whether the open menu holds the frame under it: the fullscreen
        WM, the home under it and nothing else, where the home's retained
        frame stays the one before the menu (its close stamps it)."""
        wm = self.ws.wm
        return (self.ws.sysmenu.open and getattr(wm, "pop_paint", None) is None
                and wm.overlay_sig() == 64 and wm.top_is("launcher"))

    def _home_key(self, cv):
        """What the frame under the card is a function of while the home
        lies under it alone: the home's retained key (the menu's keys and
        the marks it lets go among it) and the scrim's rects; None where
        the menu does not hold it."""
        if not self.holds_home():
            return None
        return (self.ws.launcher_layer._retained_key(cv), self.scrim_rects())

    def scrim(self, also=None):
        """Dim what the system menu, a card's menu or ABOUT covers: the
        content under the bars, which stay as they are (D-01). Once a full
        frame, before the modal draws; a frame that paints the popover's
        rect alone keeps it. `also`: a buffer each washed pixel is stored
        to as well (the held layer's)."""
        ws = self.ws
        m = ws.sysmenu
        a = getattr(m.comp, "SCRIM", None) if m.open else None
        for rect in self.scrim_rects():
            _ui_draw.scrim(ws.sys_canvas, ws.theme_colors, rect, None, also, a)

    def scrim_rects(self):
        """The rects `scrim` washes, on the glass: none for a bar widget's
        popover but the control center (a component with a `SCRIM` of its
        own), which dims the page under the bar down to the hint strip,
        ABOUT's content, or the system menu's bands."""
        ws = self.ws
        m = ws.sysmenu
        if m.open and m.comp is not None and not ws.about:
            if getattr(m.comp, "SCRIM", None) is None:
                return ()
            lay = ws.layout
            h = lay.ch
            strip = self.about._strip()
            if strip is not None and lay.cy < strip[1] < lay.cy + h:
                h = strip[1] - lay.cy
            return ((lay.cx, lay.cy, lay.cw, h),)
        if ws.about:
            lay = ws.layout
            return ((lay.cx, lay.cy, lay.cw, lay.ch),)
        return self._scrim_bands()

    def _scrim_bands(self):
        """The rects the menu's scrim washes: what it dims (`_under`), less
        the desk's PROJECTS window's hint strip where that window is on top
        -- the strip names the menu's keys, as the home's does -- as the
        bands above, below, left and right of it."""
        x, y, w, h = self._under()
        p = self._projects()
        strip = p[1] if p is not None else None
        if strip is None:
            return ((x, y, w, h),)
        sx0, sy0 = max(x, strip[0]), max(y, strip[1])
        sx1 = min(x + w, strip[0] + strip[2])
        sy1 = min(y + h, strip[1] + strip[3])
        if sx0 >= sx1 or sy0 >= sy1:
            return ((x, y, w, h),)
        bands = ((x, y, w, sy0 - y), (x, sy1, w, y + h - sy1),
                 (x, sy0, sx0 - x, sy1 - sy0), (sx1, sy0, x + w - sx1, sy1 - sy0))
        return tuple(b for b in bands if b[2] > 0 and b[3] > 0)

    def surface(self):
        """The glass the menu's card takes, (x, y, w, h): what the scrim
        dims (`_under`), or for a card's menu hung from the desk's PROJECTS
        window, that window's content less its hint strip."""
        if self.ws.sysmenu.widget == "card" and self._card_proj:
            p = self._projects()
            if p is not None:
                (x, y, w, h), strip, _hero = p
                if strip is not None and y < strip[1] < y + h:
                    h = strip[1] - y
                return (x, y, w, h)
        return self._under()

    def _projects_focused(self):
        """Whether the desk's PROJECTS window holds the input focus."""
        wm = self.ws.wm
        return getattr(wm, "mode", None) == "desktop" \
            and getattr(wm, "input_holder", lambda: None)() == "projects"

    def _projects(self):
        """The desk's PROJECTS window where it is the top one on the glass:
        (its content rect, its hint strip's rect -- None where the strip
        has no height --, its hero's rect -- None where it lays out none),
        each on the glass; else None. Its grid lays out in the window's own
        layout, so the rects are read under it."""
        ws = self.ws
        wm = ws.wm
        order = getattr(wm, "z_order", None)
        if getattr(wm, "mode", None) != "desktop" or not order or order[-1] != "projects":
            return None
        win = wm.windows.get("projects")
        if win is None or win.minimized or win.ctx is None:
            return None
        grid = ws.projects_grid
        cx, cy, cw, ch = win.content_rect()
        grid.set_layout(win.ctx.layout)
        try:
            foot, hero = grid.foot(), grid.hero_rect()
        finally:
            grid.set_layout(ws.layout)
        strip = None
        if foot is not None and foot[5][3] > 0:
            sx, sy, sw, sh = foot[5]
            strip = (cx + sx, cy + sy, sw, sh)
        if hero is not None:
            hero = (cx + hero[0], cy + hero[1], hero[2], hero[3])
        return ((cx, cy, cw, ch), strip, hero)

    def _under(self):
        """What the menu's scrim dims, (x, y, w, h): the content under the
        bars, less the home's hint strip when the home is under it -- the
        strip names the menu's keys, so it stays uncovered and unwashed, as
        the bars do (D-01)."""
        ws = self.ws
        lay = ws.layout
        x, y, w, h = lay.cx, lay.cy, lay.cw, lay.ch
        if ws.screen == "launcher":
            foot = ws.launcher_layer.grid.foot()
            if foot is not None:
                top = foot[5][1]
                if y < top < y + h:
                    h = top - y
        return (x, y, w, h)

    def looked(self):
        """The menu's rows show as the look's card: the menu is open on
        rows, not a component."""
        m = self.ws.sysmenu
        return m.open and m.comp is None

    def _look_w(self, desk):
        return 220 if desk else 150 * self.ws.layout.fs

    def look_card(self):
        """The look's card, (x, y, w, h), as the engine lays it out (a
        popover hung under the system button, `_draw_look`'s rows), known
        before it draws: what the windowed desk snapshots and paints alone.
        tests/test_system_menu.py holds it to the card the engine draws."""
        ws = self.ws
        cv = ws.sys_canvas
        desk = _desk_class()
        fs = getattr(cv, "font_scale", 1) or 1
        d = 1 if desk else 0
        row = _ui_draw.metrics("row")
        grp = _ui_draw.metrics("group")
        vr = _ui_draw.metrics("value_row")
        rh, gap = row[5 + d] * fs, row[7] * fs
        cap = grp[3 + d] * fs
        pad = vr[28 + d] * fs
        rule = (2 * row[7] + (9 if desk else 5)) * fs
        # the body's rows as the engine stacks them, (top, height, a row
        # its cut may peek: a rule is not)
        rows, body, n, ruled = [], 0, 0, False
        for it in ws.sysmenu.items:
            if it[0] == "header":
                rows.append((body, cap, True))
                body += cap + gap
                n, ruled = 0, False
            elif it[0] == "sep":
                ruled = n > 0             # inside a group: its danger rows set apart
            elif it[0] == "item":
                if ruled:
                    rows.append((body, rule, False))
                    body += rule + gap
                    n, ruled = 0, False
                rows.append((body, rh, True))
                body += rh + gap
                n += 1
        sx, sy, sw, sh = self.surface()
        e = 4 * fs                       # the card's gap from every edge
        x0, x1, y0, y1 = sx + e, sx + sw - e, sy + e, sy + sh - e
        w = min(self._look_w(desk), sw - 2 * pad, x1 - x0)
        ax, ay, aw, ah = self._menu_cell()
        x = max(x0, min(ax, x1 - w))
        top, foot = max(ay + ah + e, y0), min(ay - e, y1)
        below, above = y1 - top, foot - y0
        bh = body
        dn = 2 * pad + bh <= below or (2 * pad + bh > above and below >= above)
        room = below if dn else above
        if ah == 0 and room < 2 * pad + bh <= y1 - y0:
            # a point's card (a finger's) slides to fit whole
            return (x, y1 - (2 * pad + bh) if dn else y0, w, 2 * pad + bh)
        if 2 * pad + bh > room:
            bh = max(rh, _peek(rows, room - 2 * pad, _FADE_H * fs // 2, rh))
        h = 2 * pad + bh
        y = top if dn else foot - h
        return (x, y, w, h)

    def panel_rect(self):
        """The rect the open popover paints: the Popup's, or the look's card
        with its shadow's reach."""
        m = self.ws.sysmenu
        if not self.looked():
            return m.panel_rect()
        x, y, w, h = self.look_card()
        dp = _ui_draw.depth()
        fs = self.ws.layout.fs
        r = (dp[8] + abs(dp[9]) + abs(dp[10]) + 2) * fs
        cv = self.ws.sys_canvas
        x0, y0 = max(0, x - r), max(0, y - r)
        x1, y1 = min(cv.w, x + w + r), min(cv.h, y + h + r)
        return (x0, y0, x1 - x0, y1 - y0)

    def _menu_cell(self):
        """The system button's cell on the system canvas, the card's anchor,
        its left at least the popover's gap from the glass's edge (4 pixels
        at the font scale, 8 on the desk), as far as it hangs below the bar.
        A card's menu (`open_card`) starts at its point instead, a line as
        wide as the card and centred on it, kept on the glass, and slides
        to fit whole where the content has the room (`look_card`); from the
        desk's PROJECTS window, from its hero's cover, a rect it never
        covers."""
        ws = self.ws
        if ws.sysmenu.widget == "card":
            if self._card_proj:
                p = self._projects()
                if p is not None and p[2] is not None:
                    return p[2]
            x, y = self._card_at
            cv = ws.sys_canvas
            w = min(self._look_w(_desk_class()), cv.w)
            return (max(0, min(x - w // 2, cv.w - w)), y, w, 0)
        bars = ws.layout.bars
        got = _shell_layout.cell_of(bars, "menu")
        if got is None:
            return (0, 0, 0, 0)
        i, (bx, by, bw, bh) = got
        rx, ry = bars[i][1][0], bars[i][1][1]
        x = rx + bx
        edge = 4 * ws.layout.fs * _lb.unit()
        if x < edge:
            bw, x = bw - (edge - x), edge
        return (x, ry + by, bw, bh)

    def _draw_look(self):
        """The rows as a popover of the public UI API's hung under the system
        button: a captioned `ui.menu` a group, each row its icon, the
        Popup's row the focused one (its keys stay the Popup's, so an action
        runs in the input phase)."""
        ws = self.ws
        m = ws.sysmenu
        cv = ws.sys_canvas
        host = self._host
        if host is None:
            host = self._host = _ui_draw.AppHost()
        if 0 <= m.sel < len(m.items) and m.items[m.sel][0] == "item":
            host.focus(m.items[m.sel][4])
        desk = _desk_class()
        host.begin(cv, ws.theme_colors, self.surface(), desk)
        _ui.at(*self._menu_cell())
        _ui.popover("System", self._look_w(desk))
        cap, texts, icons, kinds = "", [], [], []
        for it in m.items:
            if it[0] in ("header", "sep"):
                # a header begins a group; a rule inside one sets its danger
                # rows apart, a menu of their own under the group's rows
                if texts:
                    _ui.menu(cap, texts, icons, kinds=kinds)
                if it[0] == "header" or texts:
                    cap, texts, icons, kinds = (it[4] if it[0] == "header" else ""), [], [], []
            elif it[0] == "item":
                texts.append(it[4])
                icons.append(it[3])
                # Delete is a verb that destroys: the danger rows' ink
                kinds.append("danger" if it[3] == _DANGER_ICON else "")
        if texts:
            _ui.menu(cap, texts, icons, kinds=kinds)
        _ui.popover()
        if host.due():
            ws.request_frame()

    def look_hit(self, px, py):
        """The Popup row index a tap at (px, py) lands on in the look's card,
        -1 inside the card on no row, None outside it."""
        host = self._host
        if host is None:
            return None
        m = self.ws.sysmenu
        for i, it in enumerate(m.items):
            if it[0] == "item" and _in_box(px, py, host.box(it[4])):
                return i
        return -1 if _in_box(px, py, host.box("System")) else None

    def _draw_about(self):
        """About: the look's card over the washed content (AboutCard)."""
        self.about.draw()


class AboutCard:
    """The system menu's About: a card over the washed content, hung under
    the bar -- Moy, the wordmark, the version and the board, then Updates
    (the updater's state; no row where there is no updater) and Licences. A
    touch board's card has a close in its corner; Esc, B or a tap outside
    closes it. Its words are about_ui's."""

    def __init__(self, ws):
        self.ws = ws
        self.host = _ui_draw.AppHost()
        self._words = None            # (version, board, updates) for this open
        self._fresh = True

    def open(self):
        self.host.reset()
        ws = self.ws
        u = ws.updater
        v = _about_ui.version_words(u, ws.toggles.ota_channel() if u is not None else None)
        b = _about_ui.board_of()
        from settings_layer import BOARDS
        upd = None
        if u is not None:
            ready = getattr(ws.update_ui, "offered", None)
            upd = ("%s is ready" % ready) if ready else "Up to date"
        self._words = ("Version " + v if v else None,
                       _about_ui.board_words(BOARDS.get(b, b)) if b else None, upd)

    def rect(self):
        """The card's (x, y, w) on the system canvas."""
        ws = self.ws
        lay = ws.layout
        desk = _desk_class()
        fs = lay.fs
        w = 420 if desk else (300 if lay.cw >= 440 * fs else 248) * fs
        top = lay.cy + (52 if desk else (16 if lay.cw >= 440 * fs else 8) * fs)
        return (lay.cx + (lay.cw - w) // 2, top, w)

    def draw(self):
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        desk = _desk_class()
        fs = 2 if desk else lay.fs
        if self._words is None:
            self.open()
        ver, board, upd = self._words
        x, top, w = self.rect()
        settled = self.host.begin(cv, ws.theme_colors, (lay.cx, lay.cy, lay.cw, lay.ch), desk)
        _ui.at(x, top - 4 * (1 if desk else lay.fs), w, 0)
        shown = _ui.popover("About", w)
        n = 32 * fs
        head = n + (52 if desk else 30) + (44 if desk else 30) * (1 if desk else lay.fs)
        r = _ui.part("About head", 0, head, {"disabled": True, "fill": "surface"})
        if r[2]:
            cx = r[0] + r[2] // 2
            ws.draw_icon("moy", (cx - n // 2, r[1], n, n), ws.theme_colors["value"], cv)
            y = r[1] + n + (14 if desk else 8 * lay.fs)
            lh = (26 if desk else 17 * lay.fs)
            _ui.at(r[0], y, r[2], lh + 6)
            _ui.label("moybyte", _WORDMARK)
            y += lh + 6
            for line in (ver, board):
                if line:
                    _ui.at(r[0], y, r[2], 20 if desk else 15 * lay.fs)
                    _ui.label(line, _CENTRE_DIM)
                    y += 22 if desk else 15 * lay.fs
        _ui.rule()
        if upd is not None and _ui.link("Updates", upd):
            self.close()
            ws.open_settings()
            ws.settings_layer.show_row("ota_channel")
        if _ui.link("Licences"):
            self.close()
            ws.open_settings()
            ws.settings_layer.open_page("licences")
        if desk:
            _ui.hints(_HINT_ABOUT)
        _ui.popover()
        strip = self._strip()
        if strip is not None:
            # the home's hint strip, which the wash leaves clear, says the card's keys
            cv.rect(strip[0], strip[1], strip[2], strip[3], ws.theme_colors["panel"])
            _ui.at(strip[0], strip[1], strip[2], strip[3])
            _ui.hints(_HINT_ABOUT)
        if _ui.input_kind() == "touch":
            c = self.host.box("About")
            if c is not None:
                _ui.at(c[0] + c[2] - 34 * lay.fs, c[1] + 6 * lay.fs, 28 * lay.fs, 28 * lay.fs)
                if _ui.tool("close", "Close"):
                    self.close()
        if not shown:
            self.close()
        if not settled:
            ws.request_frame()

    def _strip(self):
        """The home's hint strip under the card, (x, y, w, h), or None (an
        arrangement with no foot strip)."""
        ws = self.ws
        if ws.screen != "launcher" or _desk_class():
            return None
        foot = getattr(ws.launcher_layer.grid, "foot", None)
        foot = foot() if foot is not None else None
        if foot is None or foot[5][3] <= 0:
            return None
        return foot[5]

    def close(self):
        ws = self.ws
        ws.about = False
        self._words = None
        ws.mark_dirty()


_WORDMARK = {"face": "title", "align": "center"}
_HINT_ABOUT = ("confirm", "Open", "back", "Close")
_CENTRE_DIM = {"tone": "dim", "align": "center"}
