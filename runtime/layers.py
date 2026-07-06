"""The console's Layer protocol + the self-contained surface adapters, extracted
from Workstation (runtime/console.py) -- docs/shell_layers_refactor_v1.md.

`Workstation` is the compositor/router over a z-ordered stack of Layers; this
module holds the shell-side building blocks that are NOT the router itself:

  * `Layer`      -- the uniform surface interface (id / domain / draw / handle_input
                    / handle_pointer / lifecycle).
  * `_LegacyLayer` -- the Phase-0 shim: a Layer whose facets just call injected
                    callables (the still-smeared surfaces + splash/toast/cursor use
                    it until they're promoted to their own Layer file).
  * The Phase-1 adapters -- `_BlocksLayer` / `_UpdateLayer` / `_MapLayer` /
                    `_MusicLayer` / `_PerfLayer` / `_AchOverlayLayer` / `_SysMenuLayer`
                    / `_AboutLayer` -- real Layer types for the surfaces that were
                    already their own objects (block_ui / update_ui / map_ui /
                    music_ui / perf_ui / ach_ui / menu_ui).

Every class here references ONLY its `self.ws` back-ref (and, for `_LegacyLayer` /
`_AchOverlayLayer`, injected callables) -- no console module-level constants or
NAMES -- so this file is a dependency-free leaf: console.py imports it, it imports
nothing back (no circular import), and both the host and the device freeze the
identical source (build.sh stages it into modules/ like editors.py).
"""


class Layer:
    """One composited surface of the console (docs/shell_layers_refactor_v1.md): it
    owns its own pixels, input, state, and constants, and plugs into Workstation's
    z-ordered layer stack. `Workstation` is the compositor/router over the stack; a
    Layer is a consumer of the shared draw toolkit (`_glyph`/`_icon`/`_btn`/...) and
    lifecycle verbs (`go_home`/`open`/...) reached through its `self.ws` back-ref.

      id      -- stable router key ("launcher", "cards", "code", "bar", ...).
      domain  -- "system" (drawn straight on the reflowed sys_canvas) or "game"
                 (drawn on the fixed 320x240 game canvas, composited into the system
                 canvas as one viewport, #39). The router owns the single composite.

    handle_input / handle_pointer return a *handled* bool so the router can stop at
    the top-most layer that claims the event (overlay > content > chrome)."""

    id = "layer"
    domain = "system"

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

    def on_enter(self):
        pass

    def on_leave(self):
        pass


class _LegacyLayer(Layer):
    """Phase-0 shim (docs/shell_layers_refactor_v1.md §5): a Layer whose draw / input /
    pointer just call the EXISTING Workstation `_draw_*` / input methods, so the router
    can drive the whole shell as a z-ordered stack while every surface's pixels + taps
    stay byte-identical. Each smeared surface is later promoted to a real Layer
    (Phase 1/2), replacing its shim in place.

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
    """The running cart (Stage 2 of docs/shell_ux_technical_plan_v1.md): a thin
    adapter over `ws.player` -- the run-loop black box that starts a cart, ticks it
    each frame, feeds it input, and guarantees it exits. Game domain (drawn on the
    fixed 320x240 canvas, composited by the router). All the logic lives on the
    Player; this just gives it the "desktop" content-layer identity the string-keyed
    router still switches to."""

    id = "desktop"
    domain = "game"

    def draw(self, dt):
        self.ws.player.tick(dt)

    def handle_input(self, i):
        return self.ws.player.handle_input(i)

    def handle_pointer(self, px, py, click):
        return self.ws.player.handle_pointer(px, py, click)


class _BlocksLayer(Layer):
    """The block editor (#29), full-screen on the SYSTEM canvas (covers the cart)."""

    id = "blocks"
    domain = "system"

    def draw(self, dt):
        self.ws.block_ui._draw_blocks()

    def handle_input(self, i):
        self.ws.block_ui._blocks_input()   # cursor nav + insert menu (#29)
        return True

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


class _MapLayer(Layer):
    """The map/tilemap editor (#32), a game-canvas viewport panel over the frozen
    cart. Tap = paint one cell, drag = pan (#37); the d-pad pans the window.

    Stage 4 (#46 zoned bar): draw() calls ws.bar_layer._draw_status_strip("menu")
    LAST (chrome over content) so the Editor's lent top-bar zone (the tab ladder +
    PLAY) shows on this tab; handle_pointer routes a tap through
    ws.bar_layer.handle_bar_tap("menu", ...) FIRST, before the map click/pan."""

    id = "map"
    domain = "game"

    def draw(self, dt):
        ws = self.ws
        ws._draw_menu_backdrop()          # frozen cart frame + reset draw state
        ws.map_ui._draw_map()
        ws.bar_layer._draw_status_strip("menu")

    def handle_input(self, i):
        self.ws.map_ui._map_input()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        gx, gy = ws._game_xy(px, py)       # the map lives in the 320x240 viewport
        if click and ws.bar_layer.handle_bar_tap("menu", gx, gy):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        if click:
            ws.map_ui._map_click(gx, gy)
        elif ws.pointer.down:
            ws.map_ui._map_pan_drag(gx, gy)
        else:
            ws.map_ui._map_release(gx, gy)
        return True


class _MusicLayer(Layer):
    """The music/sound editor (#50), a full-screen tracker on the game canvas. The
    frozen cart isn't drawn (the editor covers it); the live mixer is ticked so a
    PLAY preview keeps sounding, then the preview flag auto-clears when it ends."""

    id = "music"
    domain = "game"

    def draw(self, dt):
        ws = self.ws
        ws._reset_canvas_state()
        if ws.audio is not None:
            ws.audio.tick(dt)
        mu = ws.music_ui
        if mu.music_preview is not None and not mu._music_preview_active():
            mu.music_preview = None
        mu._draw_music()

    def handle_input(self, i):
        # D-pad navigates the tracker (#50): up/down move the step/slot cursor,
        # left/right change the value under it (pitch / SFX-id), A plays/stops the
        # preview, B leaves. Tap remains the primary path.
        self.ws.music_ui._music_input()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        gx, gy = ws._game_xy(px, py)
        if click:
            ws.music_ui._music_click(gx, gy)   # step list + edit pad + actions
        return True


class _PerfLayer(Layer):
    """The perf HUD (#43/#44): the bottom-right FPS chip + optional frame-time
    breakdown. Game domain -- drawn on the 320x240 canvas before the composite, so
    it rides the viewport (a read-only consumer of the timing fields)."""

    id = "perf"
    domain = "game"

    def draw(self, dt):
        ws = self.ws
        ws.perf_ui._draw_fps()
        if ws.perf_hud:
            ws.perf_ui._draw_perf_hud()   # frame-time breakdown above the FPS chip


class _AchOverlayLayer(Layer):
    """A draw-only overlay adapter for the AchievementsUI surfaces (#21): the
    confetti burst, the achievements list view, and the Easter-egg popup -- each a
    separate z-ordered overlay gated in _overlay_stack, delegating to ach_ui."""

    domain = "system"

    def __init__(self, ws, id, fn):
        self.ws = ws
        self.id = id
        self._fn = fn

    def draw(self, dt):
        self._fn()


class _SysMenuLayer(Layer):
    """The ≡ dropdown system menu (#52): a modal overlay. Up/Down move (skipping
    headers), Enter/A/RUN select (close-on-select), ESC/B dismiss; a row tap
    moves+selects, a tap outside dismisses. Always consumes the event + clears the
    game pointer's tap so a running cart underneath never also sees it."""

    id = "sysmenu"
    domain = "system"

    def draw(self, dt):
        self.ws.menu_ui._draw_sysmenu()

    def handle_input(self, i):
        m = self.ws.sysmenu
        if i.pressed("b") or i.pressed("stop"):
            m.close()
        elif i.pressed("up"):
            m.move(-1)
        elif i.pressed("down"):
            m.move(1)
        elif i.pressed("a") or i.pressed("run"):
            m.activate()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if click:
            ws._dirty = True
            ws.sysmenu.click(px, py)   # row -> move+select; outside -> dismiss
        gp = ws.input.game_pointer
        ws.input.game_pointer = (gp[0], gp[1], False, False)
        return True


class _AboutLayer(Layer):
    """The ABOUT info modal (#52): any tap / ESC / B / A / RUN dismisses it. Modal --
    always consumes + clears the game pointer's tap."""

    id = "about"
    domain = "system"

    def draw(self, dt):
        self.ws.menu_ui._draw_about()

    def handle_input(self, i):
        if i.pressed("b") or i.pressed("stop") or i.pressed("a") or i.pressed("run"):
            self.ws._about = False
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if click:
            ws._about = False
            ws._dirty = True
        gp = ws.input.game_pointer
        ws.input.game_pointer = (gp[0], gp[1], False, False)
        return True
