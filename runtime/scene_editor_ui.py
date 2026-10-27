# Map (grep -n a name to jump there):
#   SceneLayout          the scene editor's geometry: the handheld page's or a desk pane's
#   SceneEditorUI        the placement editor's UI: the world view and its gestures
#   SceneEditorUI.build  build the SceneEditor over a scene
#   SceneEditorUI.save   persist the edited scene
"""The scene (placed-actor) placement editor's UI layer (#85 Stage 2): a panned
WYSIWYG view of the WORLD -- the tilemap as the backdrop, the fixed 320x240
game viewport as a frame, every placed actor as its sprite -- and the
gestures over it. On the handheld the Scene tab is the page
`runtime/editor_scene.py` draws round the view (the stamps, the picked
actor's props, the tools); in a desk Editor's Scene pane the view fills the
pane, the stamps are the Stamps pane's, the props the Actor pane's and the
tools the pane header's (`runtime/editor_desk.py`).

The map editor's gesture grammar, over objects:

  * tap empty world                   -> place ONE actor there (selects it)
  * tap an existing actor             -> select it (topmost wins)
  * drag an actor                     -> move it (live, one undo step)
  * drag empty world                  -> pan the view (map-editor gesture)

Coordinates are WORLD-SPACE pixels (#85 Section 7): the editor shows a viewport
frame at world (0,0)-(320,240) over a pannable world, so screen-space carts just
never pan. Zoom only goes IN (1x/2x/3x -- sprites only ever UPscale, the map
editor's rule). Every committed gesture is (a) one in-editor undo step and (b)
synced into the LIVE widgets.Scenes via Scenes.put, so PLAY without an explicit
SAVE runs the freshest placement -- the exact live-edit semantics the shared
TileMap gives the map tab. SAVE persists through Project.commit_scene (atomic
write + the durable undo journal, Stage 1). The editor manages the cart's
DEFAULT scene (multi-scene UX is a deferred follow-up, #85 decision log 4)."""

try:
    import ui as _ui              # frozen on device
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
_in = _ui.rect_in   # one hit-test (ui.rect_in)

try:
    from editors import SceneEditor, KeyEdge
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import SceneEditor, KeyEdge

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H, pane as _in_pane
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H, pane as _in_pane)

# The handheld page (editor_scene.py): the right column's width and the
# foot's height, beside and under the view.
HAND_COL = 104
HAND_FOOT = 27

# Zoom SCALES (not cell sizes): world px -> view px multipliers. 1x shows the
# most world; 2x/3x zoom IN for fine placement (sprites only ever upscale).
_SV_ZOOMS = (1, 2, 3)
# The tiles a page of the handheld page's palette shows: four rows of four.
_TP_PAGE = 16
# Drag-vs-tap threshold (px), the map editor's gesture rule (#37).
_SC_PAN_THRESH = 6


class SceneLayout(LayoutBase):
    """The scene editor's geometry, from the SYSTEM canvas size (w, h) and
    font scale. On the handheld (`verbs`) the world view runs from the
    content rect's top left to the page's right column and foot, which
    `editor_scene.ScenePage` draws; in a desk Editor's Scene pane (`pane`)
    the view fills the pane."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1,
                 chrome_scale=None):
        LayoutBase.__init__(self, w, h, font_scale,
                            chrome_scale=chrome_scale)
        fs = self.fs
        cx, cy, cw, ch = self.cx, self.cy, self.cw, self.ch
        self.body_fill = (cx, cy, cw, ch)
        self.zooms = _SV_ZOOMS
        self.pan_thresh = _SC_PAN_THRESH * fs
        self.tp_page = _TP_PAGE
        self.pane = _in_pane()
        self.verbs = not self.pane
        if self.pane:
            self.sv_x0, self.sv_y0 = cx + 2 * fs, cy + 2 * fs
            self.sv_avail_w, self.sv_avail_h = cw - 4 * fs, ch - 4 * fs
        else:
            self.sv_x0, self.sv_y0 = cx, cy
            self.sv_avail_w = cw - HAND_COL
            self.sv_avail_h = ch - HAND_FOOT - 1
            self.tp_x0 = cx + cw - HAND_COL + 6
            self.tp_y0 = cy + 6


class SceneEditorUI:
    """The placement editor's UI: world view + palette + props row + gesture
    handling. One instance lives on Workstation (`self.scene_ui`), built once in
    Workstation.__init__; `ws.scene_ui.build()` is called lazily from
    set_menu_view("scene") the first time a cart's scene tab is opened --
    exactly the MapEditorUI lifecycle."""

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        self.sceneedit = None          # SceneEditor while menu_view == "scene"
        self.scene_name = None         # the DEFAULT scene this editor manages
        self.scene_page = 0            # first tile id shown in the palette
        self.scene_zoom = 0            # index into layout.zooms (0 = 1x)
        self._skey = KeyEdge()         # Ctrl+Z/Y edge tracker
        self._press = None             # gesture origin (px, py); None outside one
        self._mode = None              # open gesture: "move" (an actor) or "pan"
        self._panning = False          # the pan gesture crossed the tap threshold
        self._drag = None              # last pointer (px, py) during a view pan
        self._grab = (0, 0)            # world offset actor-origin - press (a drag
                                       # holds the sprite where the finger picked it)
        self.place_taps = True         # the handheld page's stamp tool: a tap on empty world places
        self._page = None              # the handheld's page on the verbs (editor_scene.py)
        sc = ws.sys_canvas
        self.layout = SceneLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def page(self):
        """The handheld's Scene page on the verbs, built at its first use."""
        if self._page is None:
            try:
                from editor_scene import ScenePage
            except ImportError:  # pragma: no cover - host fallback
                from runtime.editor_scene import ScenePage
            self._page = ScenePage(self, _ui_draw.AppHost("editor_scene"))
        return self._page

    def pointer(self, px, py, click, down):
        """A pointer sample on the handheld's page (the layer's, after the
        bar's): a press in the view is the scene's gesture, the rest the
        verbs'."""
        page = self.page()
        if page.pop is None:
            if click and _in(px, py, self._sv_area()):
                self._scene_click(px, py)
                return True
            if self._press is not None:
                if down:
                    self._scene_drag(px, py)
                else:
                    self._scene_release(px, py)
                return True
            if click and page.palette_tap(px, py):
                return True
        return page.point(px, py, click, down)

    def relayout(self, w, h, fs, cs=None):
        """Rebuild the responsive geometry (#39) -- ws._relayout fan-out."""
        self.layout = SceneLayout(w, h, fs, chrome_scale=cs)
        if self.scene_zoom >= len(self.layout.zooms):
            self.scene_zoom = 0
        self._clamp_cam()

    def build(self):
        """Build the SceneEditor over the open cart's DEFAULT scene (the
        manifest's element 0; a cart with none starts an empty "main"). Called
        from Workstation.set_menu_view("scene")."""
        ws = self.ws
        if self.sceneedit is not None or ws.project.sheet is None:
            return
        scenes = ws.project.scenes
        names = getattr(scenes, "names", None) if scenes is not None else None
        self.scene_name = names[0] if names else "main"
        raw = scenes.raw(self.scene_name) if scenes is not None else None
        self.sceneedit = SceneEditor(raw or "")

    def reset(self):
        """Drop the active editor (a stale one must never leak into an unrelated
        cart). Called from Workstation.open()/go_home()/_reload_after_walk."""
        self.sceneedit = None
        self.scene_name = None

    def on_open(self):
        """Reset gesture/zoom/props state -- called from EditorApp.open_scene
        before set_menu_view("scene") (re)builds the editor."""
        self.scene_zoom = 0
        self.scene_page = 0
        self._press = None
        self._mode = None
        self._panning = False
        self._drag = None
        self._skey.reset()

    # -- live sync + persistence ---------------------------------------------

    def _sync_live(self):
        """Push the edited rows into the LIVE widgets.Scenes so the next PLAY's
        _init sees them (the run copies them, `ProjectStore.play_world`). Called after
        every committed gesture and after undo/redo."""
        se = self.sceneedit
        scenes = self.ws.project.scenes
        if se is None or scenes is None or self.scene_name is None:
            return
        scenes.put(self.scene_name, se.serialize())

    def save(self):
        """SAVE: persist the edited scene through Project.commit_scene (atomic
        write + manifest registration + the durable undo journal, Stage 1).
        The ws entry point Workstation.save_scene forwards here."""
        ws = self.ws
        se = self.sceneedit
        if se is None or self.scene_name is None:
            return
        self._sync_live()                       # the store text == the live text
        if ws.project.commit_scene(self.scene_name, se.serialize()):
            se.dirty = False

    # -- view metrics ---------------------------------------------------------

    def _sv_metrics(self):
        """The LIVE view metrics: (x0, y0, scale, view_w, view_h). `scale` is
        view px per world px at the current zoom; view_w/h are the WORLD span
        (px) the view window shows."""
        lay = self.layout
        idx = self.scene_zoom
        if idx < 0:
            idx = 0
        elif idx >= len(lay.zooms):
            idx = len(lay.zooms) - 1
        scale = lay.zooms[idx]
        return (lay.sv_x0, lay.sv_y0, scale,
                lay.sv_avail_w // scale, lay.sv_avail_h // scale)

    def _sv_area(self):
        """The current view rectangle (x, y, w, h) for _in() hit-tests."""
        x0, y0, scale, vw, vh = self._sv_metrics()
        return (x0, y0, vw * scale, vh * scale)

    def _world_at(self, px, py):
        """World coords under pointer (px, py), or None outside the view."""
        se = self.sceneedit
        if se is None or not _in(px, py, self._sv_area()):
            return None
        x0, y0, scale, vw, vh = self._sv_metrics()
        return (se.cam_x + (px - x0) // scale, se.cam_y + (py - y0) // scale)

    def _clamp_cam(self):
        """Clamp the camera so the view never scrolls far past the world's
        content extents (viewport + tilemap + actors, editors_scene.world_size)."""
        se = self.sceneedit
        if se is None:
            return
        x0, y0, scale, vw, vh = self._sv_metrics()
        ww, wh = se.world_size(self.ws.project.tilemap)
        se.cam_x = max(0, min(max(0, ww - vw), se.cam_x))
        se.cam_y = max(0, min(max(0, wh - vh), se.cam_y))

    def wheel_zoom(self, px, py, d):
        """A wheel notch at (px, py): the next zoom in (`d` < 0) or out, a
        whole step, the world point under the pointer kept under it."""
        se = self.sceneedit
        if se is None:
            return
        z = max(0, min(len(self.layout.zooms) - 1, self.scene_zoom - d))
        if z == self.scene_zoom:
            return
        x0, y0, scale, _w, _h = self._sv_metrics()
        wx = se.cam_x + (px - x0) // scale
        wy = se.cam_y + (py - y0) // scale
        self.scene_zoom = z
        x0, y0, scale, _w, _h = self._sv_metrics()
        se.cam_x = wx - (px - x0) // scale
        se.cam_y = wy - (py - y0) // scale
        self._clamp_cam()

    def drag_pan(self, dx, dy):
        """The view dragged `dx`, `dy` pixels: the camera the other way."""
        _x0, _y0, scale, _w, _h = self._sv_metrics()
        self._pan(-dx // scale if dx > 0 else (-dx + scale - 1) // scale,
                  -dy // scale if dy > 0 else (-dy + scale - 1) // scale)

    def _cycle_zoom(self):
        self.scene_zoom = (self.scene_zoom + 1) % len(self.layout.zooms)
        self._clamp_cam()

    def _pan(self, dx, dy):
        """Pan the camera by (dx, dy) world px, clamped to the world."""
        se = self.sceneedit
        if se is None:
            return
        se.cam_x += dx
        se.cam_y += dy
        self._clamp_cam()

    def _palette_ids(self):
        """The tile ids on the current palette page (the map editor's window)."""
        sheet = self.ws.project.sheet
        if sheet is None:
            return []
        start = self.scene_page
        return list(range(start, min(start + self.layout.tp_page, sheet.count)))

    # -- input ----------------------------------------------------------------

    def _scene_input(self):
        """Keyboard: the handheld page's popover takes the keys while it is
        open. Otherwise the d-pad nudges the selected actor (or pans when nothing is selected), A
        cycles zoom, Ctrl+Z/Y walk the in-editor history, B steps back."""
        ws = self.ws
        i = ws.input
        se = self.sceneedit
        page = self._page if self.layout.verbs else None
        if page is not None:
            if page.pop is not None:
                if page.keys(i):
                    ws._dirty = True
                if i.pressed("b"):
                    page.close()
                    ws._dirty = True
                return
            if page.owed():
                ws._dirty = True
        if se is not None:
            g = 8
            if se.selected() is not None:
                if i.steps("up"):
                    self._nudge(0, -1)
                if i.steps("down"):
                    self._nudge(0, 1)
                if i.steps("left"):
                    self._nudge(-1, 0)
                if i.steps("right"):
                    self._nudge(1, 0)
            else:
                if i.steps("up"):
                    self._pan(0, -g)
                if i.steps("down"):
                    self._pan(0, g)
                if i.steps("left"):
                    self._pan(-g, 0)
                if i.steps("right"):
                    self._pan(g, 0)
            if i.pressed("a"):
                self._cycle_zoom()
            self._skey.undo_redo(getattr(i, "last_key", 0),
                                 self._undo, self._redo)
        ws._leave_or_home(ws.exit)

    def _nudge(self, dx, dy):
        se = self.sceneedit
        if se is not None and se.nudge_sel(dx, dy):
            self._sync_live()
            self._clamp_cam()

    def _undo(self):
        se = self.sceneedit
        if se is not None and se.undo():
            self._sync_live()
            self._clamp_cam()

    def _redo(self):
        se = self.sceneedit
        if se is not None and se.redo():
            self._sync_live()
            self._clamp_cam()

    # -- pointer gestures ------------------------------------------------------

    def _scene_click(self, px, py):
        """Pointer PRESS. In the world view: an actor under the tap -> select it
        and open a MOVE gesture; empty world -> open a PAN gesture (a release
        inside the tap threshold PLACES there instead)."""
        ws = self.ws
        se = self.sceneedit
        if se is None:
            return
        if _in(px, py, self._sv_area()):
            self._press = (px, py)
            self._panning = False
            self._drag = None
            w = self._world_at(px, py)
            if w is None:
                return
            hit = se.select_at(w[0], w[1])
            if hit is not None:
                self._mode = "move"
                r = se.selected()
                self._grab = (r["x"] - w[0], r["y"] - w[1])
                se.begin_edit()
            else:
                self._mode = "pan"
            ws._dirty = True

    def _scene_drag(self, px, py):
        """Held-drag: a MOVE gesture tracks the actor under the finger (live,
        snapped); a PAN gesture past the tap threshold scrolls the camera by the
        drag delta (content follows the finger, the map editor's #37 rule)."""
        se = self.sceneedit
        press = self._press
        if se is None or press is None:
            return
        if self._mode == "move":
            x0, y0, scale, vw, vh = self._sv_metrics()
            wx = se.cam_x + (px - x0) // scale
            wy = se.cam_y + (py - y0) // scale
            se.move_sel(wx + self._grab[0], wy + self._grab[1])
            self.ws._dirty = True
            return
        if self._mode != "pan":
            return
        if not self._panning:
            thresh = self.layout.pan_thresh
            if abs(px - press[0]) < thresh and abs(py - press[1]) < thresh:
                return                           # still within the tap dead-zone
            self._panning = True
            self._drag = press
        last = self._drag
        if last is None:
            return
        x0, y0, scale, vw, vh = self._sv_metrics()
        dx = _ui_draw.drag_steps(last[0] - px, scale)
        dy = _ui_draw.drag_steps(last[1] - py, scale)
        if dx or dy:
            self._pan(dx, dy)
            self._drag = (last[0] - dx * scale, last[1] - dy * scale)

    def _scene_release(self, px, py):
        """Pointer up: a MOVE gesture commits (one undo step iff it moved); a
        PAN gesture that never crossed the threshold was a TAP on empty world ->
        PLACE a new actor there with the active stamp."""
        se = self.sceneedit
        if se is not None and self._press is not None:
            if self._mode == "move":
                if se.end_edit():
                    self._sync_live()
                    self._clamp_cam()
            elif (self._mode == "pan" and not self._panning
                  and (self.place_taps or self.layout.pane)):
                w = self._world_at(*self._press)
                if w is not None:
                    se.place(w[0], w[1])
                    self._sync_live()
            self.ws._dirty = True
        self._press = None
        self._mode = None
        self._panning = False
        self._drag = None

    # -- drawing ---------------------------------------------------------------

    def _draw_scene(self):
        """The Scene tab: the handheld's page round the view, or a desk
        pane's view. Indexed API only, so host == device."""
        ws = self.ws
        cv = ws.sys_canvas
        lay = self.layout
        th = ws.theme_colors
        if lay.verbs:
            self.page().draw(cv, th, (lay.cx, lay.cy, lay.cw, lay.ch), False)
            return
        bx, by, bw, bh = lay.body_fill
        col = th["surface"]
        if self.sceneedit is None or ws.project.sheet is None:
            cv.rect(bx, by, bw, bh, col)
            return
        # The view fills its own rect first: the body round it is all that
        # takes the surface.
        vx, vy, vw, vh = self._sv_area()
        vx = max(bx, min(vx, bx + bw))
        vy = max(by, min(vy, by + bh))
        vr = max(vx, min(vx + vw, bx + bw))
        vb = max(vy, min(vy + vh, by + bh))
        cv.rect(bx, by, bw, vy - by, col)
        cv.rect(bx, vb, bw, by + bh - vb, col)
        cv.rect(bx, vy, vx - bx, vb - vy, col)
        cv.rect(vr, vy, bx + bw - vr, vb - vy, col)
        self.draw_view(cv)

    def draw_view(self, cv, boxes=True):
        """The world view: the dark field, the tilemap backdrop, the game
        viewport's frame and the placed actors, each boxed, the selected one
        in white -- or, with `boxes` false, not at all (the handheld page's
        view, which rings it with the canvas cursor)."""
        ws = self.ws
        NAMES = self._NAMES
        se = self.sceneedit
        sheet = ws.project.sheet
        x0, y0, scale, vw, vh = self._sv_metrics()
        tm = ws.project.tilemap
        # -- the world view: dark field, tilemap backdrop, viewport frame, actors.
        va = self._sv_area()
        cv.rect(va[0], va[1], va[2], va[3], NAMES["dark_blue"])
        g = sheet.TILE
        if tm is not None:
            # Visible map cells, drawn at the zoom scale (the scene draws OVER
            # the tilemap, #85 Section 4). Via spr_tile (#163): a contiguous
            # run coalesces into ONE native blit_batch, where the old
            # tile_image + spr pair paid an Image blit crossing PER CELL that
            # no batch could collect (pixel parity pinned by the #63 suite).
            c0 = se.cam_x // g
            r0 = se.cam_y // g
            c1 = (se.cam_x + vw - 1) // g
            r1 = (se.cam_y + vh - 1) // g
            spr_tile = cv.spr_tile
            for cy in range(r0, r1 + 1):
                if cy < 0 or cy >= tm.h:
                    continue
                for cx in range(c0, c1 + 1):
                    if cx < 0 or cx >= tm.w:
                        continue
                    vx = x0 + (cx * g - se.cam_x) * scale
                    vy = y0 + (cy * g - se.cam_y) * scale
                    if (vx < x0 or vy < y0 or vx + g * scale > x0 + va[2]
                            or vy + g * scale > y0 + va[3]):
                        continue              # only whole cells (no clip bleed)
                    tid = tm.mget(cx, cy)
                    if tid >= 0:
                        spr_tile(sheet, tid, vx, vy, -1, scale, 0)
        # The fixed game viewport's frame at world (0,0)-(320,240): what a
        # non-panning cart shows. Each edge clamped to the view rectangle.
        self._frame_world(cv, 0, 0, 320, 240, NAMES["yellow"])
        # Placed actors, list order = draw order; box every actor so a blank
        # tile (a pure tag marker, e.g. Hop Quest's spawn) stays visible.
        for i, r in enumerate(se.rows):
            vx = x0 + (r["x"] - se.cam_x) * scale
            vy = y0 + (r["y"] - se.cam_y) * scale
            side = g * scale
            if (vx + side <= x0 or vy + side <= y0
                    or vx >= x0 + va[2] or vy >= y0 + va[3]):
                continue
            if (vx >= x0 and vy >= y0 and vx + side <= x0 + va[2]
                    and vy + side <= y0 + va[3]):
                img = sheet.tile_image(r["tile"], 0)
                if img is not None:
                    cv.spr(img, vx, vy, scale)
                if not boxes and i == se.sel:
                    continue
                cv.rectb(vx, vy, side, side,
                         NAMES["white"] if i == se.sel else NAMES["dark_grey"])
                if i == se.sel and side > 4:
                    cv.rectb(vx + 1, vy + 1, side - 2, side - 2, NAMES["white"])

    def _frame_world(self, cv, wx, wy, ww, wh, color):
        """Outline the world rect (wx, wy, ww, wh) in view coords, each edge
        clamped to the view rectangle (the canvas has no clip region, and an
        unclamped rectb would bleed over the palette column)."""
        se = self.sceneedit
        x0, y0, scale, vw, vh = self._sv_metrics()
        va = self._sv_area()
        vx1 = x0 + va[2]
        vy1 = y0 + va[3]

        def to_vx(w):
            return x0 + (w - se.cam_x) * scale

        def to_vy(w):
            return y0 + (w - se.cam_y) * scale

        left, top = to_vx(wx), to_vy(wy)
        right, bottom = to_vx(wx + ww), to_vy(wy + wh)
        cl = max(left, x0)
        ct = max(top, y0)
        cr = min(right, vx1)
        cb = min(bottom, vy1)
        if cr <= cl or cb <= ct:
            return
        if y0 <= top < vy1:
            cv.rect(cl, top, cr - cl, 1, color)
        if y0 < bottom <= vy1:
            cv.rect(cl, bottom - 1, cr - cl, 1, color)
        if x0 <= left < vx1:
            cv.rect(left, ct, 1, cb - ct, color)
        if x0 < right <= vx1:
            cv.rect(right - 1, ct, 1, cb - ct, color)
