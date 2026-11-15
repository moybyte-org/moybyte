"""The console's Layer protocol + the self-contained surface adapters, extracted
from Workstation (runtime/shell/console.py) -- docs/history/shell_layers_refactor_v1.md.

`Workstation` is the compositor/router over a z-ordered stack of Layers; this
module holds the shell-side building blocks that are NOT the router itself:

  * `Layer`      -- the uniform surface interface (id / domain / draw / handle_input
                    / handle_pointer) and the input protocol the console's input
                    policy asks of whatever holds the keys (runtime/shell/input_policy.py).
  * `_LegacyLayer` -- a Layer whose facets call injected callables: the permanent
                    home of the four draw-only overlays (splash / toast / notice /
                    cursor), which have no state worth a class of their own.
  * The Phase-1 adapters -- `_BlocksLayer` / `_UpdateLayer` / `_MapLayer` /
                    `_MusicLayer` / `_PerfLayer` / `_SysMenuLayer` / `_PauseLayer` /
                    `_ConfirmLayer` / `_AboutLayer` -- real Layer types for the
                    surfaces that were already their own objects (block_ui /
                    update_ui / map_ui / music_ui / perf_ui / menu_ui / pause /
                    confirm / about).

Every class here references ONLY its `self.ws` back-ref (and, for `_LegacyLayer`,
injected callables) -- no console module-level constants or
NAMES -- so this file is a leaf over the toolkit's engine: console.py imports
it, it imports nothing back (no circular import), and both the host and the
device freeze the identical source (build.sh stages it into modules/ like the
editor cores).
"""

import ui_draw as _ui_draw


class Layer:
    """One composited surface of the console (docs/history/shell_layers_refactor_v1.md): it
    owns its own pixels, input, state, and constants, and plugs into Workstation's
    z-ordered layer stack. `Workstation` is the compositor/router over the stack; a
    Layer is a consumer of the shared draw toolkit (`_glyph`/`_icon`/`_btn`/...) and
    lifecycle verbs (`go_home`/`open`/...) reached through its `self.ws` back-ref.

      id      -- stable router key ("launcher", "cards", "code", "bar", ...).
      domain  -- "system" (drawn straight on the reflowed sys_canvas) or "game"
                 (drawn on the fixed 320x240 game canvas, composited into the system
                 canvas as one viewport, #39). The router owns the single composite.

    handle_input / handle_pointer return a *handled* bool so the router can stop at
    the top-most layer that claims the event (overlay > content > chrome).

    The INPUT PROTOCOL is what the console's input policy asks of the surface
    that holds the keys (runtime/shell/input_policy.py), each verb defaulted
    here: `ui_host` (the AppHost its pages draw through, which answers the
    toolkit's half: a focused field types, Mic and back cancel its edit, back
    dismisses a popover), `typing`, `cancel`, `back` and `has_page` (its own
    page step, after the toolkit's), `close` (leaving it, by any route),
    `page_title` and `ball_steps`. `B_BACK`: its B is the host's back, as
    every app's is; a surface whose B is a button of its own leaves it
    False. `ESC_LEAVES`: Esc on its first page leaves it.

    The FRAME PROTOCOL is what the console's frame asks of a surface,
    whichever it is, so no surface is named there: `moved` (it changed on
    its own since it drew, and draws again), `ON_WALLPAPER` (it shows the
    live wallpaper behind it, whose animation keeps frames coming), `BAR`
    (the strip the bar's keys walk while a registered app is on top: the
    host's "app" strip, its own, or None for none), `leave` (its own way
    back past its first page, else the host's) and `blur` (the keys went
    to another surface)."""

    id = "layer"
    domain = "system"
    B_BACK = False
    ESC_LEAVES = True
    ON_WALLPAPER = False
    BAR = "app"

    def __init__(self, ws):
        self.ws = ws

    def visible(self):
        return True

    def draw(self, dt):
        pass

    def handle_input(self, i):
        return False

    def handle_pointer(self, px, py, click):
        return False

    def ui_host(self):
        """The AppHost the surface's page draws through, or None."""
        return None

    def typing(self):
        """Whether the surface takes typed bytes: by default, a field focused
        on its page (`ui_draw.app_typing`)."""
        h = self.ui_host()
        return h is not None and _ui_draw.app_typing(h.ctx)

    def cancel(self):
        """The host's cancel (the T-Deck's Mic) inside the surface: True where
        it took it. By default a focused field's edit gives its draft back."""
        h = self.ui_host()
        if h is not None and _ui_draw.app_cancel(h.ctx, True):
            h.owed = True
            return True
        return False

    def back(self):
        """One page of the surface back, after the toolkit's step (a field's
        edit, a popover): True where it took it, False on its first page,
        where the host leaves it."""
        return False

    def has_page(self):
        """Whether a page of its own is open, which `back` steps out of."""
        return False

    def close(self):
        """Leaving the surface, by any route: what it holds is written.
        Change-gated and cheap (#154)."""

    def page_title(self):
        """The open page's name, after the back chevron; None: the surface's."""
        return None

    def ball_steps(self):
        """Whether the trackball steps the surface as W A S D do."""
        return True

    def moved(self):
        """Whether what it shows moved on its own since it drew (an async
        state it follows): the frame draws it again with nothing dirty."""
        return False

    def leave(self):
        """Leave it past its first page by its own way back: True where it
        has one, False for the host's (the caller, the Editor's home)."""
        return False

    def blur(self):
        """The keys went to another surface: what is typed is kept."""


class AppLayer(Layer):
    """A registered app's surface (docs/app_api_v1.md): its pages draw through
    the AppHost its context lends (`ctx.ui`, held as `_ui`), and B is its
    back. `BACK_MINIMISES`: a back on its first page minimises it on the
    desk."""

    B_BACK = True
    BACK_MINIMISES = False
    _ui = None

    def ui_host(self):
        return self._ui


class _LegacyLayer(Layer):
    """A Layer whose draw / input / pointer facets call injected Workstation
    methods. It is the permanent home of the four draw-only overlays -- splash,
    toast, notice and the cursor -- each a `_draw_*` method with no state that
    would earn a class of its own (docs/history/shell_layers_refactor_v1.md §5
    is where the shape came from).

    `draw` is a callable(dt); `kbd` is a callable(i) -> handled bool; `ptr` is a
    callable(px, py, click) -> handled bool. Any may be None (that facet no-ops)."""

    def __init__(self, ws, id, domain="system", draw=None, kbd=None, ptr=None):
        self.ws = ws
        self.id = id
        self.domain = domain
        self._draw_fn = draw
        self._kbd_fn = kbd
        self._ptr_fn = ptr

    def draw(self, dt):
        if self._draw_fn is not None:
            self._draw_fn(dt)

    def handle_input(self, i):
        return bool(self._kbd_fn(i)) if self._kbd_fn is not None else False

    def handle_pointer(self, px, py, click):
        return bool(self._ptr_fn(px, py, click)) if self._ptr_fn is not None else False


# -- Phase-1 adapters: real Layer types for the surfaces that were already their
#    own objects (BlockEditorUI / MapEditorUI / MusicEditorUI / UpdateUI /
#    SystemMenuUI / AchievementsUI / PerfHud). Each is thin -- it delegates to the
#    UI object (+ the minimal Workstation glue: coord translation, the shared
#    editor-panel backdrop) through its `self.ws` back-ref -- but it gives the
#    surface a named Layer identity (the step toward its own module) and drops the
#    generic _LegacyLayer shim. Behavior + draw order are byte-identical.


class _PlayerLayer(Layer):
    """The running cart (Stage 2 of docs/history/shell_ux_technical_plan_v1.md): a thin
    adapter over `ws.run.player` -- the run-loop black box that starts a cart, ticks it
    each frame, feeds it input, and guarantees it exits. Game domain (drawn on the
    fixed 320x240 canvas, composited by the router). All the logic lives on the
    Player; this just gives it the "desktop" content-layer identity the string-keyed
    router still switches to."""

    id = "desktop"
    domain = "game"
    ESC_LEAVES = False

    def ball_steps(self):
        """An app's page takes the trackball as its W A S D (`AppFace.ball_steps`);
        a game's trackball is its pointer."""
        return self.ws.run.player.face.ball_steps()

    def typing(self):
        return self.ws.run.player.face.typing()

    def cancel(self):
        """Mic in a script's prompt is its Stop; in an app's field, the
        field's edit given back (`AppFace.cancel`)."""
        ws = self.ws
        if (ws.cart is not None and ws.cart.get("type") == "script"
                and ws.script_console.reading()):
            ws.spine.exit_to_caller()
            return True
        return ws.run.player.face.cancel()

    def back(self):
        return self.ws.run.player.face.back()

    def has_page(self):
        return self.ws.run.player.face.has_page()

    def draw(self, dt):
        self.ws.run.player.tick(dt)

    def handle_input(self, i):
        return self.ws.run.player.handle_input(i)

    def handle_pointer(self, px, py, click):
        return self.ws.run.player.handle_pointer(px, py, click)


class _BlocksLayer(Layer):
    """The block editor (#29), full-screen on the SYSTEM canvas (covers the cart)."""

    id = "blocks"
    domain = "system"

    def draw(self, dt):
        self.ws.block_ui._draw_blocks(dt)

    def at_top(self):
        """Up goes on to the bar from the outline's first row
        (InputRouter._bar_keys)."""
        bu = self.ws.block_ui
        be = bu.blocks_ed
        return (be is not None and be.cur == 0 and bu.blk_menu is None
                and bu.blk_kbd is None and not be.moving())

    def handle_input(self, i):
        self.ws.block_ui._blocks_input()   # cursor nav + insert menu (#29)
        return True

    def typing(self):
        """An entry prompt's keypad types."""
        return self.ws.block_ui.blk_kbd is not None

    def cancel(self):
        """Mic, Esc or B in an entry prompt: it closes, as its Cancel does."""
        if not self.ws.block_ui.cancel_prompt():
            return False
        self.ws.mark_dirty()
        return True

    back = cancel

    def handle_pointer(self, px, py, click):
        self.ws.block_ui._blocks_pointer(px, py, click)   # outline + insert menu
        return True


class _UpdateLayer(Layer):
    """The firmware-update screen (#53): pump the install/reboot flow, then draw it."""

    id = "update"
    domain = "system"

    def draw(self, dt):
        self.ws.update_ui._pump_update(dt)   # advance the install / reboot countdown
        self.ws.update_ui._draw_update(dt)

    def handle_input(self, i):
        self.ws.update_ui._update_input(i)
        return True

    def handle_pointer(self, px, py, click):
        self.ws.update_ui._update_pointer(px, py, click)
        return True


class _WebConsoleLayer(Layer):
    """The WEB CONSOLE connection screen (#197): the glass while wasm mode is on.

    A parked fullscreen surface in the shape of the update screen -- it owns
    every pixel and claims every event, because while the toggle is on there is
    nothing else for the glass to be doing. Entry state (the revealed address is
    cleared) is reset by `WebConsole.park` (web_console.py), NOT by the Layer
    protocol's `on_enter`: nothing in the router calls that hook today."""

    id = "webconsole"
    domain = "system"

    def draw(self, dt):
        ws = self.ws
        lay = ws.layout
        ws.web.ui.draw((lay.cx, lay.cy, lay.cw, lay.ch))

    def handle_input(self, i):
        return self.ws.web.ui.handle_input(i)

    def handle_pointer(self, px, py, click):
        return self.ws.web.ui.handle_pointer(px, py, click)


class _MapLayer(Layer):
    """The map/tilemap editor (#32), SYSTEM-domain responsive (#39 step 3): the
    handheld's Map page, or a desk pane's view (`map_editor_ui.py`). Tap = paint
    one cell, drag = pan (#37); the d-pad pans the window.

    The frame draws the "menu" strip over it (`bar_layer.STRIP_OF`), the
    Editor's lent top-bar zone (the tab ladder + PLAY); handle_pointer routes a
    tap through ws.bar_layer.handle_bar_tap("menu", ...) FIRST, before the map
    click/pan."""

    id = "map"
    domain = "system"
    def ball_steps(self):
        """The trackball moves the map's cursor."""
        return False

    def draw(self, dt):
        ws = self.ws
        ws.reset_canvas_state()          # game-canvas hygiene (degradation shares it)
        ws.map_ui._draw_map()

    def at_top(self):
        """Up goes on to the bar from the map's top row (InputRouter._bar_keys)."""
        return self.ws.map_ui.at_top()

    def handle_input(self, i):
        self.ws.map_ui._map_input()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        # SYSTEM coords (#39 step 3): the editor draws on the system canvas at
        # native size, so it hit-tests the raw pointer -- no _game_xy translation.
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        if ws.map_ui.layout.verbs:
            if ws.map_ui.pointer(px, py, click, ws.pointer.down):
                ws.mark_dirty()
        elif click:
            ws.map_ui._map_click(px, py)
        elif ws.pointer.down:
            ws.map_ui._map_pan_drag(px, py)
        else:
            ws.map_ui._map_release(px, py)
        return True


class _SceneLayer(Layer):
    """The scene placement editor (#85 Stage 2), SYSTEM-domain responsive (#39):
    the handheld's Scene page, or a desk pane's world view
    (scene_editor_ui.SceneEditorUI). Tap = place/select, drag = move an actor or
    pan the world (the map editor's gesture grammar).

    The frame draws the "menu" strip over it (`bar_layer.STRIP_OF`), the
    Editor's lent top-bar zone (the tab ladder + PLAY, no SAVE -- #111);
    handle_pointer routes a tap through ws.bar_layer.handle_bar_tap("menu", ...)
    FIRST, before the placement click/pan -- the exact _MapLayer shape."""

    id = "scene"
    domain = "system"
    def ball_steps(self):
        """The trackball points at the placements."""
        return False

    def ui_host(self):
        """The handheld page's context, where its tag popover's field types."""
        page = self.ws.scene_ui._page
        return page.host if page is not None else None

    def back(self):
        """The tag popover closes."""
        page = self.ws.scene_ui._page
        if page is None or page.pop is None:
            return False
        page.close()
        return True

    def draw(self, dt):
        ws = self.ws
        ws.reset_canvas_state()          # game-canvas hygiene (degradation shares it)
        ws.scene_ui._draw_scene()

    def handle_input(self, i):
        self.ws.scene_ui._scene_input()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        # SYSTEM coords (#39): the editor draws on the system canvas at native
        # size, so it hit-tests the raw pointer -- no _game_xy translation.
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        if ws.scene_ui.layout.verbs:
            if ws.scene_ui.pointer(px, py, click, ws.pointer.down):
                ws.mark_dirty()
        elif click:
            ws.scene_ui._scene_click(px, py)
        elif ws.pointer.down:
            ws.scene_ui._scene_drag(px, py)
        else:
            ws.scene_ui._scene_release(px, py)
        return True


class _MusicLayer(Layer):
    """The music/sound editor (#50), SYSTEM-domain responsive (#39 step 3): the
    handheld's Music page (`editor_music.py`). The live mixer is ticked so a
    preview keeps sounding, then the preview flag auto-clears when it ends.

    The frame draws the "menu" strip over it (`bar_layer.STRIP_OF`), the
    Editor's lent top-bar zone (the tab ladder + PLAY + X, no SAVE -- #111);
    handle_pointer routes a tap through ws.bar_layer.handle_bar_tap("menu", ...)
    FIRST, before the page's verbs."""

    id = "music"
    domain = "system"

    def draw(self, dt):
        ws = self.ws
        ws.reset_canvas_state()
        if ws.audio_out is not None:
            ws.audio_out.tick(dt)
        mu = ws.music_ui
        if mu.music_preview is not None and not mu._music_preview_active():
            mu.music_preview = None
        mu._draw_music()

    def at_top(self):
        """Up goes on to the bar from the first step (InputRouter._bar_keys)."""
        return self.ws.music_ui.at_top()

    def handle_input(self, i):
        # D-pad navigates the steps (#50): up/down move the step/slot cursor,
        # left/right change the value under it (pitch / SFX-id), A plays/stops the
        # preview, B leaves. Tap remains the primary path.
        self.ws.music_ui._music_input()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        # SYSTEM coords (#39 step 3): hit-test the raw pointer, no _game_xy.
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        ws.music_ui.pointer(px, py, click)   # the page's verbs
        return True


class _PerfLayer(Layer):
    """The perf HUD (#43/#44): the bottom-right FPS chip + optional frame-time
    breakdown. Game domain -- drawn on the 320x240 canvas before the composite, so
    it rides the viewport (a read-only consumer of the timing fields)."""

    id = "perf"
    domain = "game"

    def draw(self, dt):
        self.ws.perf_ui.draw()


class _PauseLayer(Layer):
    """The pause menu's layer (docs/history/look_2026-10.md step 25a): its card over
    the frozen game, drawn on the system canvas; it takes every key and tap
    while it shows, and clears the game pointer's tap so the held cart never
    sees one."""

    id = "pause"
    domain = "system"

    def draw(self, dt):
        full = getattr(self.ws.wm, "pause_full", None)
        if full is not None:
            full()                    # the windowed desk: a whole frame under it
        self.ws.pause.draw()
        if full is not None:
            full(True)

    def handle_input(self, i):
        self.ws.pause.handle_input(i)
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        ws.pause.handle_pointer(px, py, click)
        gp = ws.input.game_pointer
        ws.input.game_pointer = (gp[0], gp[1], False, False)
        return True


class _ConfirmLayer(Layer):
    """The delete confirm's layer (`shell_cards.DeleteConfirm`): its
    dialog over the content, drawn on the system canvas; it takes every key
    and tap while it shows, and clears the game pointer's tap so a cart
    underneath never sees one."""

    id = "confirm"
    domain = "system"

    def draw(self, dt):
        self.ws.confirm.draw()

    def handle_input(self, i):
        self.ws.confirm.handle_input(i)
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        ws.confirm.handle_pointer(px, py, click)
        gp = ws.input.game_pointer
        ws.input.game_pointer = (gp[0], gp[1], False, False)
        return True


class _SysMenuLayer(Layer):
    """The popover layer (#52, docs/theme_and_shell_2026-11.md §8.6): the ONE modal
    panel a bar widget opens -- the system menu's rows, or a component. Rows:
    Up/Down move (skipping headers), Enter/A/RUN select (close-on-select). A
    component takes the keys itself, every key while it is typing. B, STOP,
    ESC and HOME close it; a tap outside closes it, a tap inside is the panel's.
    Always consumes the event + clears the game pointer's tap so a running
    cart underneath never also sees it.

    A change that stays inside the panel tells the WM (`popover_touch`),
    which paints the panel's rect alone where it can; one that reaches
    outside it (an action, a connect, a stepped setting) marks the frame
    dirty."""

    id = "sysmenu"
    domain = "system"

    def draw(self, dt):
        p = self.ws.pause
        if p.open and p.shows_bar():
            return                    # over the paused game the pause draws it
        paint = getattr(self.ws.wm, "pop_paint", None)
        if paint is not None:
            paint(False)              # the windowed WM: the scrim, its snapshots, the menu
        else:
            self.ws.menu_ui.draw_full(dt)   # a full frame: dim the content first

    def _comp(self, fn, *args):
        """Run a component's handler: a change inside the panel is the
        popover's (whatever dirty mark the handler left), one outside it
        marks the frame."""
        ws = self.ws
        was = ws.frame_state.dirty
        got = fn(*args)
        if got == "global":
            ws.mark_dirty()
        elif got == "local":
            ws.frame_state.dirty = was
            ws.popover_touch()

    def handle_input(self, i):
        ws = self.ws
        m = ws.sysmenu
        comp = m.comp
        if comp is not None and comp.typing():
            self._comp(comp.handle_input, i)
        elif (i.pressed("b") or i.pressed("stop") or i.pressed("home")
              or i.last_key == 27):
            m.close()
            ws.popover_touch()
        elif comp is not None:
            self._comp(comp.handle_input, i)
        elif i.steps("up"):
            m.move(-1)
            ws.popover_touch()
        elif i.steps("down"):
            m.move(1)
            ws.popover_touch()
        elif i.pressed("a") or i.pressed("run"):
            m.activate()
            ws.popover_touch()
            ws.mark_dirty()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if click:
            m = ws.sysmenu
            comp = m.comp
            if comp is not None:
                r = m.panel_rect()
                if px >= r[0] and py >= r[1] and px < r[0] + r[2] and py < r[1] + r[3]:
                    self._comp(comp.click, px, py, r)
                else:
                    m.close()             # outside dismisses
                    ws.popover_touch()
                    c = ws.layout.clock_hit()
                    if c[0] <= px < c[0] + c[2] and c[1] <= py < c[1] + c[3]:
                        ws.ach_ui._tap_clock()    # the clock's egg counts the tap that closes it
            else:
                hit = ws.menu_ui.look_hit(px, py)
                if hit is None:
                    m.close()             # outside dismisses
                elif hit >= 0:
                    m.sel = hit           # a row: move+select
                    m.activate()
                    ws.mark_dirty()
                ws.popover_touch()
        gp = ws.input.game_pointer
        ws.input.game_pointer = (gp[0], gp[1], False, False)
        return True


class _AboutLayer(Layer):
    """About's card (shell_cards.AboutCard) over the washed content: its rows
    take the keys and taps; Esc, B or a tap outside closes it. Modal --
    always consumes + clears the game pointer's tap."""

    id = "about"
    domain = "system"

    def draw(self, dt):
        self.ws.menu_ui.scrim()
        self.ws.menu_ui._draw_about()

    def handle_input(self, i):
        card = self.ws.menu_ui.about
        if i.pressed("b") or i.pressed("stop"):
            card.close()
        elif card.host.keys(i):
            self.ws.mark_dirty()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if ws.menu_ui.about.host.point(px, py, click, bool(getattr(ws.pointer, "down", False))):
            ws.mark_dirty()
        gp = ws.input.game_pointer
        ws.input.game_pointer = (gp[0], gp[1], False, False)
        return True
