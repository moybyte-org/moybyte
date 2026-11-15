"""The console's input policy: the ONE owner of what the keys mean on the
surface that holds them (docs/refactor_2026-11.md, phase 5).

Four policies, each decided in one place from what the surface answers
(the `Layer` input protocol, runtime/shell/layers.py) and, under that, from
what its page has focused (the toolkit's `api_typing`, `api_cancel` and
`api_back`, in C, for every tier):

- TEXT MODE is derived from the surface holding the keys (`sync`: before
  and after the frame's key routing, after its pointer routing and after
  its draw) and applied only when it changes (`apply`): on a T-Deck text
  mode types W A S D and presses no direction (native/moy_input/moy_kbd.c);
  a menu over a handheld's surface takes the keys as a list and is game
  mode.
  No surface sets it: a field taking focus, a prompt opening, a cart's
  `textmode()` all reach the keyboard through their answer here.
- CANCEL (`cancel`, the T-Deck's Mic) asks the popover a field types in,
  then the holder.
- BACK (`back`: B, Esc, the bar's chevron, a page's own way out) is one
  cascade: a menu, a fullscreen game's pause, the toolkit's step, the
  surface's own page step, then leaving the surface.
- THE TRACKBALL: whether it steps the holder as W A S D do (`ball_steps`).

THE SWITCH SWALLOWS THE BYTE THAT CAUSED IT. Every surface reads one
typed-key press, the table's `typed` (THE PRESS, native/moy_input/moy_input.h).
Taking the keyboard for a text surface is a screen change, and the key that
made it is still down when the surface's first frame reads it; `apply`
spends the press (`spend_key`), so it types nothing, and releasing it and
pressing it again still types. It runs on a change only: a per-frame spend
would swallow the keystroke of the frame it ran in.
"""

import ui_draw as _ui_draw


class InputPolicy:

    def __init__(self, ws):
        self.ws = ws

    # -- who holds the keys ------------------------------------------------------

    def holder(self):
        """The surface the keyboard reaches (`Workstation.surface_of` the
        WM's `input_kind`), or None (a minimised window's)."""
        ws = self.ws
        return ws.surface_of(ws.wm.input_kind())

    def _modal(self):
        """A menu holds a handheld's keys over the surface: the pause menu,
        the delete confirm, the system menu, About."""
        ws = self.ws
        return not ws.windowed_chrome and (ws.modal_layer() is not None or ws.about)

    def _typing_popover(self):
        """The popover component a field types in (the Wi-Fi panel's
        password), or None."""
        m = self.ws.sysmenu
        comp = m.comp if m.open else None
        return comp if comp is not None and comp.typing() else None

    # -- text mode -------------------------------------------------------------

    def typing(self):
        """Whether the keyboard should be in text mode now."""
        if self._typing_popover() is not None:
            return True
        if self._modal():
            return False
        s = self.holder()
        return s is not None and bool(s.typing())

    def sync(self):
        """The mode the holder wants, applied on a change."""
        on = self.typing()
        if on != bool(self.ws.input.text_mode):
            self.apply(on)

    def apply(self, on):
        """Put the keyboard in text mode (`on`) or game mode: the table's flag
        the drivers read, and the board keyboard's mode. A turn to text
        spends the typed-key press, so the key that brought the surface up
        types nothing into it."""
        ws = self.ws
        i = ws.input
        if on:
            i.spend_key()
        i.text_mode = on
        kb = ws.keyboard
        if kb is not None:
            kb.set_game_mode(not on)

    def attach_keyboard(self):
        """The board's keyboard takes the mode in force as it is attached:
        the console boots on the home, no text surface, so on the T-Deck the
        raw matrix from the first frame, where Mic and a held direction
        read; the C3 itself boots in ASCII, where a bare Mic sends nothing."""
        kb = self.ws.keyboard
        if kb is not None:
            kb.set_game_mode(not self.ws.input.text_mode)

    # -- cancel ----------------------------------------------------------------

    def cancel(self):
        """The host's cancel (the T-Deck's Mic inside a field): the popover a
        field types in, else the holder (`Layer.cancel`). True where one
        took it; False and the key keeps its own meaning."""
        ws = self.ws
        pop = self._typing_popover()
        if pop is not None:
            took = pop.cancel()
        elif self._modal():
            return False
        else:
            s = self.holder()
            took = s is not None and s.cancel()
        if took:
            ws.mark_dirty()
        return bool(took)

    # -- back ------------------------------------------------------------------

    def back(self, leave=None):
        """Back one level: a menu closes (the pause menu steps back), a
        fullscreen game pauses, else the holder steps (`step`) and, on its
        first page, is left (`leave`) -- on the desk only where `leave` says
        (a desk window is left by its X; B on Settings' rows leaves it)."""
        ws = self.ws
        if leave is None:
            leave = not ws.windowed_chrome
        ws.mark_dirty()
        if ws.pause.open and not ws.sysmenu.open:
            ws.pause.back()
        elif ws.confirm.open:
            ws.confirm.cancel()
        elif ws.sysmenu.open:
            ws.sysmenu.close()
            ws.popover_touch()
        elif ws.about:
            ws.menu_ui.about.close()
        elif ws.plays_fullscreen() or ws.game_pauses():
            ws.system_key()
        else:
            kind = ws.wm.input_kind()
            s = ws.surface_of(kind)
            if s is not None and not self.step(s) and leave:
                self.leave(kind)

    def typed_escape(self, i):
        """Esc on a text surface arrives as the byte 27, not `stop` (a board
        keyboard types it there, native/moy_input/moy_hid.c): a fresh one
        over a surface with a view of its own open -- a field's edit, a
        dialog -- or one whose Esc leaves it (`ESC_LEAVES`: the Editor's
        Code tab, whose back closes its find strip or popups first) is its
        back, as `stop` is elsewhere. True where it took it; a running app
        and a game keep the byte (a terminal's Esc, a game's `key()`)."""
        if i.typed != 27 or not i.text_mode:
            return False
        ws = self.ws
        if ws.modal_layer() is not None or ws.sysmenu.open:
            return False
        s = self.holder()
        if s is None or not (self.has_page(s) or getattr(s, "ESC_LEAVES", False)):
            return False
        self.back()
        return True

    def takes_b(self, s):
        """Whether B on surface `s` is the host's back: a surface whose B
        is its back (`B_BACK`), or one with a page open."""
        return s.B_BACK or self.has_page(s)

    def takes_stop(self, s):
        """Whether Esc (`stop`) is the host's back over surface `s`: a
        fullscreen game's (its pause), one with a page open, and every
        surface whose Esc leaves it (`ESC_LEAVES`; a running app's stays its
        own on its first page, which its bar's chevron leaves)."""
        ws = self.ws
        return (s is None or s.ESC_LEAVES or self.has_page(s)
                or ws.plays_fullscreen() or ws.game_pauses())

    def step(self, s):
        """One step back inside surface `s`, never out of it: the toolkit's
        (a field's edit cancelled, a popover dismissed, a lent page left),
        else its own page step. False on its first page."""
        h = s.ui_host()
        if h is not None and _ui_draw.app_back(h.ctx, True):
            h.owed = True
            self.ws.mark_dirty()
            return True
        return bool(s.back())

    def has_page(self, s):
        """Whether surface `s` has a view of its own open that `step` steps
        out of (the desk window's back chevron shows then)."""
        h = s.ui_host()
        return bool((h is not None and _ui_draw.app_back(h.ctx, False)) or s.has_page())

    def leave(self, kind):
        """Leave surface `kind`, past its first page: what it holds is
        written (`leave_surface`), and it goes back to whoever opened it --
        Settings to what it was opened over, the task manager to its
        caller, the Editor home or to the app that opened it, an app that
        minimises on the desk to its chip."""
        ws = self.ws
        if kind == "settings":
            ws.leave_surface(kind)
            ws.exit_settings()
        elif kind == "tasks":
            ws.close_tasks()
        elif kind == "menu":
            ws.spine.exit()
        elif kind == "desktop" or ws.app(kind) is not None:
            ws.leave_surface(kind)
            app = ws.app(kind)
            if app is not None and app.BACK_MINIMISES and ws.wm.minimise_kind(kind):
                return
            ws.spine.exit_to_caller()

    # -- the trackball ---------------------------------------------------------

    def ball_steps(self):
        """Whether the trackball is the W A S D of the surface that holds
        the keys: every popover and menu, About, and every fullscreen
        surface -- the home, the apps (a cart's page too), the Editor's tabs --
        but one whose `ball_steps` says otherwise: a running game, a canvas
        whose cursor it moves (the sprite tab and Paint until a popover
        opens, the map, the scene) and a caret, which takes it as arrows
        (`InputRouter.nav`). The desk's windows are pointed at."""
        ws = self.ws
        if ws.modal_layer() is not None or ws.about:
            return True
        if ws.windowed_chrome:
            return False
        if ws.bar_layer.focus is not None:
            return True
        s = self.holder()
        return s is None or bool(s.ball_steps())
