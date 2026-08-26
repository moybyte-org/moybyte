"""The scene (placed-actor) placement editor's UI layer (#85 Stage 2): a panned
WYSIWYG view of the WORLD on the left -- the tilemap as the backdrop, the fixed
320x240 game viewport as a frame, every placed actor as its sprite -- a paged
tile palette on the right to pick the stamp, a pan d-pad + zoom, and the
selected actor's inline props row (tag / flip / delete / z-order).

Practically the map editor with an actor palette instead of a tile palette
(#85 Section 4) -- same gesture grammar, same shared-console home, mirroring
map_editor_ui.py's MapEditorUI shape (layout class + UI class + injected
NAMES/_in). The genuinely new machinery is OBJECT select/move/delete and the
inline tag row:

  * tap a sheet tile in the palette   -> it's the active stamp
  * tap empty world                   -> place ONE actor there (selects it)
  * tap an existing actor             -> select it (topmost wins)
  * drag an actor                     -> move it (live, one undo step)
  * drag empty world                  -> pan the view (map-editor gesture)
  * TAG field (selected)              -> type a tag, ENTER commits
  * SNAP toggle                       -> 8px tile-grid placement vs free-pixel
  * FRONT/BACK                        -> z-order = list order (#85 Section 2)

Coordinates are WORLD-SPACE pixels (#85 Section 7): the editor shows a viewport
frame at world (0,0)-(320,240) over a pannable world, so screen-space carts just
never pan. Zoom only goes IN (1x/2x/3x -- sprites only ever UPscale, the map
editor's rule). Every committed gesture is (a) one in-editor undo step and (b)
synced into the LIVE widgets.Scenes via Scenes.put, so PLAY without an explicit
SAVE runs the freshest placement -- the exact live-edit semantics the shared
TileMap gives the map tab. SAVE persists through Project.commit_scene (atomic
write + the durable undo journal, Stage 1). The editor manages the cart's
DEFAULT scene (multi-scene UX is a deferred follow-up, #85 decision log 4)."""

from array import array

try:
    import ui as _ui              # frozen on device
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui

try:
    from editors import SceneEditor, KeyEdge
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import SceneEditor, KeyEdge

try:
    from chrome import _gbtn as _chrome_gbtn
except ImportError:  # pragma: no cover - direct host import (chrome not yet aliased)
    from runtime.chrome import _gbtn as _chrome_gbtn

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H)

# World view (left): the panned window onto the world, same rectangle the map
# editor uses (clear of the palette column at x >= 206 and the bottom row).
_SV_X0 = 14
_SV_Y0 = 32
_SV_AVAIL_W = 192      # usable view width  (14 .. 206)
_SV_AVAIL_H = 164      # usable view height (32 .. 196)
# Zoom SCALES (not cell sizes): world px -> view px multipliers. 1x shows the
# most world; 2x/3x zoom IN for fine placement (sprites only ever upscale).
_SV_ZOOMS = (1, 2, 3)
# Tile palette (right): a paged grid of sheet tiles; tap to pick the stamp.
# Identical geometry to the map editor's palette (one muscle memory).
_TP_X0 = 210
_TP_Y0 = 32
_TP_CELL = 22
_TP_COLS = 4
_TP_ROWS = 4
_TP_PAGE = _TP_COLS * _TP_ROWS
_TP_AREA = (_TP_X0, _TP_Y0, _TP_COLS * _TP_CELL, _TP_ROWS * _TP_CELL)
_TP_PREV = (_TP_X0, _TP_Y0 + _TP_ROWS * _TP_CELL + 2, 42, 18)
_TP_NEXT = (_TP_X0 + 46, _TP_Y0 + _TP_ROWS * _TP_CELL + 2, 42, 18)
# Pan d-pad cluster (right column, under the palette) -- the map editor's slots:
# zoom in the center, and the free top-left corner slot holds the FLIP toggle.
_SC_FLIP = (218, 146, 24, 16)
_SC_ZOOM = (244, 164, 24, 16)
_PAN_UP = (244, 146, 24, 16)
_PAN_LF = (218, 164, 24, 16)
_PAN_RT = (270, 164, 24, 16)
_PAN_DN = (244, 182, 24, 16)
# Bottom row: the selected actor's TAG field + DEL, and the SNAP toggle on the
# right (the slot the map editor gives its SKY swatch).
_SC_TAG = (14, 198, 120, 20)
_SC_DEL = (138, 198, 40, 20)
_SC_SNAP = (206, 198, 100, 20)
# Title-band toolbar (the map editor's 4-slot strip): UNDO / REDO / FRONT / BACK.
_SC_UNDO = (196, 17, 26, 13)
_SC_REDO = (224, 17, 26, 13)
_SC_FRONT = (252, 17, 26, 13)
_SC_BACK = (280, 17, 26, 13)
# Drag-vs-tap threshold (px), the map editor's gesture rule (#37).
_SC_PAN_THRESH = 6


class SceneLayout(LayoutBase):
    """Responsive scene-editor geometry (#39): the panel, the panned world view,
    the paged tile palette + pan/zoom column, the props row and the toolbar,
    derived from the SYSTEM canvas size (w, h) + font scale.

    Same hard contract as MapLayout: at (320, 240, 1) every field equals the
    frozen module constant, byte for byte (the `_base` branch); the responsive
    formulas only run on a larger canvas / bigger font. The world VIEW is the
    star of the reflow -- a big panel shows the whole 320x240 viewport (and
    beyond) with no panning."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1, bounds=None):
        # `bounds` (bx, by, bw, bh) confines the whole editor to a SUB-RECT of the
        # system canvas -- the right pane of the combined Blocks+Scene workspace
        # (blocks-left / objects-right, Scratch-style). A bounded layout never takes
        # the frozen 320x240 branch (it's a big-screen feature), so `_base` excludes
        # it and the T-Deck's scene tab is byte-identical.
        LayoutBase.__init__(self, w, h, font_scale, base_extra=bounds is None)
        fs = self.fs
        self.zooms = _SV_ZOOMS
        if self._base:
            self.body_fill = (0, 18, _BASE_W, _BASE_H - 18)
            self.panel = (8, 16, 304, 204)
            self.title_xy = (14, 18)
            self.sv_x0, self.sv_y0 = _SV_X0, _SV_Y0
            self.sv_avail_w, self.sv_avail_h = _SV_AVAIL_W, _SV_AVAIL_H
            self.tp_x0, self.tp_y0 = _TP_X0, _TP_Y0
            self.tp_cell, self.tp_cols, self.tp_rows = _TP_CELL, _TP_COLS, _TP_ROWS
            self.tp_page = _TP_PAGE
            self.tp_area = _TP_AREA
            self.tp_prev, self.tp_next = _TP_PREV, _TP_NEXT
            self.flip_btn = _SC_FLIP
            self.zoom_btn = _SC_ZOOM
            self.pan_up, self.pan_lf, self.pan_rt, self.pan_dn = \
                _PAN_UP, _PAN_LF, _PAN_RT, _PAN_DN
            self.tag_btn, self.del_btn, self.snap_btn = _SC_TAG, _SC_DEL, _SC_SNAP
            self.undo_btn, self.redo_btn = _SC_UNDO, _SC_REDO
            self.front_btn, self.back_btn = _SC_FRONT, _SC_BACK
            self.pan_thresh = _SC_PAN_THRESH
            return
        # -- responsive: the MapLayout formulas (right column anchored to the
        # panel's right edge, button row to its bottom, view fills the rest) ----
        bar_h = 18 * fs
        if bounds is not None:
            # Confined to the workspace's right pane: the panel fills the pane
            # (minus a hair of inset), and every rect below derives from px/py/
            # pw/ph exactly as the full-canvas path -- so the whole scene editor
            # relocates with no other change.
            bx, by, bw, bh = bounds
            px, py = bx + 2 * fs, by + 2 * fs
            pw, ph = bw - 4 * fs, bh - 4 * fs
            self.body_fill = (bx, by, bw, bh)
        else:
            px, py = 8 * fs, bar_h - 2 * fs
            pw, ph = self.w - 16 * fs, self.h - (bar_h - 2 * fs) - 20 * fs
            self.body_fill = (0, bar_h, self.w, self.h - bar_h)
        self.panel = (px, py, pw, ph)
        p_right = px + pw
        p_bottom = py + ph
        self.title_xy = (px + 6 * fs, py + 2 * fs)
        row_y = p_bottom - 22 * fs
        rc_x = p_right - 106 * fs
        self.sv_x0 = px + 6 * fs
        self.sv_y0 = py + 16 * fs
        self.sv_avail_w = rc_x - self.sv_x0
        self.sv_avail_h = row_y - self.sv_y0 - 2 * fs
        pan_dn_y = row_y - 16 * fs
        pan_mid_y = row_y - 34 * fs
        pan_up_y = row_y - 52 * fs
        bw, bh = 24 * fs, 16 * fs
        self.pan_up = (rc_x + 38 * fs, pan_up_y, bw, bh)
        self.pan_lf = (rc_x + 12 * fs, pan_mid_y, bw, bh)
        self.pan_rt = (rc_x + 64 * fs, pan_mid_y, bw, bh)
        self.pan_dn = (rc_x + 38 * fs, pan_dn_y, bw, bh)
        self.zoom_btn = (rc_x + 38 * fs, pan_mid_y, bw, bh)
        self.flip_btn = (rc_x + 12 * fs, pan_up_y, bw, bh)
        self.tp_x0 = rc_x + 4 * fs
        self.tp_y0 = self.sv_y0
        self.tp_cell = _TP_CELL * fs
        self.tp_cols = _TP_COLS
        self.tp_rows = max(1, (pan_up_y - 22 * fs - self.tp_y0) // self.tp_cell)
        self.tp_page = self.tp_cols * self.tp_rows
        self.tp_area = (self.tp_x0, self.tp_y0,
                        self.tp_cols * self.tp_cell, self.tp_rows * self.tp_cell)
        tp_by = self.tp_y0 + self.tp_rows * self.tp_cell + 2 * fs
        self.tp_prev = (self.tp_x0, tp_by, 42 * fs, 18 * fs)
        self.tp_next = (self.tp_x0 + 46 * fs, tp_by, 42 * fs, 18 * fs)
        self.tag_btn = (px + 6 * fs, row_y, 120 * fs, 20 * fs)
        self.del_btn = (px + 130 * fs, row_y, 40 * fs, 20 * fs)
        self.snap_btn = (rc_x, row_y, 100 * fs, 20 * fs)
        tb_y = py + 2 * fs
        tbw, tbh, gap = 26 * fs, 13 * fs, 2 * fs
        tb_right = p_right - 6 * fs
        self.back_btn = (tb_right - tbw, tb_y, tbw, tbh)
        self.front_btn = (self.back_btn[0] - gap - tbw, tb_y, tbw, tbh)
        self.redo_btn = (self.front_btn[0] - gap - tbw, tb_y, tbw, tbh)
        self.undo_btn = (self.redo_btn[0] - gap - tbw, tb_y, tbw, tbh)
        self.pan_thresh = _SC_PAN_THRESH * fs


class SceneEditorUI:
    """The placement editor's UI: world view + palette + props row + gesture
    handling. One instance lives on Workstation (`self.scene_ui`), built once in
    Workstation.__init__; `ws.scene_ui.build()` is called lazily from
    set_menu_view("scene") the first time a cart's scene tab is opened --
    exactly the MapEditorUI lifecycle."""

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self.sceneedit = None          # SceneEditor while menu_view == "scene"
        self.scene_name = None         # the DEFAULT scene this editor manages
        self.scene_page = 0            # first tile id shown in the palette
        self.scene_zoom = 0            # index into layout.zooms (0 = 1x)
        self.tag_edit = False          # the TAG field is capturing keystrokes
        self.tag_buf = ""              # the tag being typed (commits on ENTER)
        self._skey = KeyEdge()         # Ctrl+Z/Y edge tracker
        self._tag_kprev = 0            # tag-typing edge detect (wifi-field pattern)
        self._press = None             # gesture origin (px, py); None outside one
        self._mode = None              # open gesture: "move" (an actor) or "pan"
        self._panning = False          # the pan gesture crossed the tap threshold
        self._drag = None              # last pointer (px, py) during a view pan
        self._grab = (0, 0)            # world offset actor-origin - press (a drag
                                       # holds the sprite where the finger picked it)
        self._tp_memo = None           # #163: palette-grid quad pack, keyed on
                                       # (geometry, count) -- relayout invalidates
        sc = ws.sys_canvas
        self.layout = SceneLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def relayout(self, w, h, fs):
        """Rebuild the responsive geometry (#39) -- ws._relayout fan-out."""
        self.layout = SceneLayout(w, h, fs)
        if self.scene_zoom >= len(self.layout.zooms):
            self.scene_zoom = 0
        self._clamp_cam()

    def relayout_bounded(self, bounds):
        """Rebuild the scene geometry CONFINED to `bounds` (bx, by, bw, bh) -- the
        right pane of the combined Blocks+Scene workspace. Mirrors relayout() but
        into a sub-rect; called each frame the workspace is active by the Blocks
        tab, so a window resize (new sys_canvas size) re-derives the pane."""
        sc = self.ws.sys_canvas
        self.layout = SceneLayout(sc.w, sc.h, getattr(sc, "font_scale", 1), bounds)
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
        self.tag_edit = False
        self.tag_buf = ""

    def on_open(self):
        """Reset gesture/zoom/props state -- called from EditorApp.open_scene
        before set_menu_view("scene") (re)builds the editor."""
        self.scene_zoom = 0
        self.scene_page = 0
        self.tag_edit = False
        self.tag_buf = ""
        self._press = None
        self._mode = None
        self._panning = False
        self._drag = None
        self._skey.reset()
        self._tag_kprev = 0

    # -- live sync + persistence ---------------------------------------------

    def _sync_live(self):
        """Push the edited rows into the LIVE widgets.Scenes so the next PLAY's
        _init sees them (the map tab's live-TileMap semantics). Called after
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
        if se is None or not self._in(px, py, self._sv_area()):
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
        """Keyboard: while the TAG field is capturing, typed bytes edit the tag
        (ENTER commits, ESC cancels -- the wifi-password pattern). Otherwise the
        d-pad nudges the selected actor (or pans when nothing is selected), A
        cycles zoom, Ctrl+Z/Y walk the in-editor history, B leaves (PLAY)."""
        ws = self.ws
        i = ws.input
        se = self.sceneedit
        if se is not None and self.tag_edit:
            k = i.last_key
            if k and k != self._tag_kprev:
                if k in (10, 13):                    # ENTER -> commit the tag
                    self._tag_commit()
                elif k == 27:                        # ESC -> cancel
                    self._tag_cancel()
                elif k == 8:                         # BACKSPACE -> delete
                    self.tag_buf = self.tag_buf[:-1]
                elif 32 <= k <= 126 and len(self.tag_buf) < SceneEditor.TAG_MAX:
                    self.tag_buf += chr(k)
                ws._dirty = True
            self._tag_kprev = k
            return                                   # the field owns every key
        if se is not None:
            g = 8
            if se.selected() is not None:
                if i.pressed("up"):
                    self._nudge(0, -1)
                if i.pressed("down"):
                    self._nudge(0, 1)
                if i.pressed("left"):
                    self._nudge(-1, 0)
                if i.pressed("right"):
                    self._nudge(1, 0)
            else:
                if i.pressed("up"):
                    self._pan(0, -g)
                if i.pressed("down"):
                    self._pan(0, g)
                if i.pressed("left"):
                    self._pan(-g, 0)
                if i.pressed("right"):
                    self._pan(g, 0)
            if i.pressed("a"):
                self._cycle_zoom()
            self._skey.undo_redo(getattr(i, "last_key", 0),
                                 self._undo, self._redo)
        ws._leave_or_home(ws._leave_menu)

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

    # -- the TAG field (inline props, #85 Section 4) --------------------------

    def _tag_start(self):
        """Tap on the TAG field with an actor selected: begin capturing keys.
        Flips the device keyboard to clean ASCII (the code-editor/wifi mode)."""
        se = self.sceneedit
        r = se.selected() if se is not None else None
        if r is None:
            return
        self.tag_edit = True
        self.tag_buf = r.get("tag", "")
        self._tag_kprev = 0
        self.ws._set_text_mode(True)

    def _tag_commit(self):
        se = self.sceneedit
        self.tag_edit = False
        self.ws._set_text_mode(False)
        if se is not None and se.set_tag(self.tag_buf):
            self._sync_live()

    def _tag_cancel(self):
        self.tag_edit = False
        self.ws._set_text_mode(False)

    # -- pointer gestures ------------------------------------------------------

    def _scene_click(self, px, py):
        """Pointer PRESS. In the world view: an actor under the tap -> select it
        and open a MOVE gesture; empty world -> open a PAN gesture (a release
        inside the tap threshold PLACES there instead). Elsewhere: the palette /
        toolbar / props buttons."""
        ws = self.ws
        se = self.sceneedit
        if se is None:
            return
        lay = self.layout
        if self.tag_edit and not self._in(px, py, lay.tag_btn):
            self._tag_commit()                   # tap-away commits the typed tag
        if self._in(px, py, self._sv_area()):
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
            return
        if self._in(px, py, lay.undo_btn):
            self._undo()
            return
        if self._in(px, py, lay.redo_btn):
            self._redo()
            return
        if self._in(px, py, lay.front_btn):
            if se.front_sel():
                self._sync_live()
            return
        if self._in(px, py, lay.back_btn):
            if se.back_sel():
                self._sync_live()
            return
        if self._in(px, py, lay.tag_btn):
            self._tag_start()
            return
        if self._in(px, py, lay.del_btn):
            if se.delete_sel():
                self._sync_live()
            return
        if self._in(px, py, lay.snap_btn):
            se.snap = not se.snap
            return
        if self._in(px, py, lay.flip_btn):
            if se.toggle_flip():
                self._sync_live()
            return
        if self._in(px, py, lay.tp_area):
            col = (px - lay.tp_x0) // lay.tp_cell
            row = (py - lay.tp_y0) // lay.tp_cell
            if 0 <= col < lay.tp_cols and 0 <= row < lay.tp_rows:
                k = row * lay.tp_cols + col
                ids = self._palette_ids()
                if 0 <= k < len(ids):
                    se.set_brush(ids[k])     # pick sprite + adopt its object type (#85/#93)
        elif self._in(px, py, lay.tp_prev):
            self.scene_page = max(0, self.scene_page - lay.tp_page)
        elif self._in(px, py, lay.tp_next):
            sheet = ws.project.sheet
            if sheet is not None and self.scene_page + lay.tp_page < sheet.count:
                self.scene_page += lay.tp_page
        elif self._in(px, py, lay.zoom_btn):
            self._cycle_zoom()
        elif self._in(px, py, lay.pan_up):
            self._pan(0, -8)
        elif self._in(px, py, lay.pan_dn):
            self._pan(0, 8)
        elif self._in(px, py, lay.pan_lf):
            self._pan(-8, 0)
        elif self._in(px, py, lay.pan_rt):
            self._pan(8, 0)

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
        dx = (last[0] - px) // scale
        dy = (last[1] - py) // scale
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
            elif self._mode == "pan" and not self._panning:
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

    def _gbtn(self, kind, label, rect, fill, cv):
        _chrome_gbtn(self.ws, self._NAMES, kind, label, rect, fill, cv)

    def _draw_scene(self):
        """The placement editor: the world view (tilemap backdrop + the 320x240
        viewport frame + placed actors), the tile palette, the pan/zoom column,
        the props row and the toolbar. Indexed API only, so host == device."""
        ws = self.ws
        NAMES = self._NAMES
        cv = ws.sys_canvas
        lay = self.layout
        se = self.sceneedit
        sheet = ws.project.sheet
        th = ws.theme_colors
        light = (not lay._base) or ws.light_chrome()  # tokens on every responsive tier; _base stays frozen only in DARK chrome
        cv.rect(*(lay.body_fill + ((th["surface"] if light else NAMES["black"]),)))
        # Only the sliver of `panel` that `body_fill` did not already cover in
        # this exact colour -- the full re-fill rewrote ~94% of ~450K px for
        # nothing (see ui.fill_uncovered).
        _ui.fill_uncovered(cv, lay.panel, lay.body_fill,
                           th["surface"] if light else NAMES["black"])
        cv.rectb(*(lay.panel + (NAMES["green"],)))
        x0, y0, scale, vw, vh = self._sv_metrics()
        title = "SCENE"
        if self.scene_name:
            title += " " + str(self.scene_name)[:8].upper()
        title += "  TILE " + str(se.n if se else 0) + "  z" + str(self.scene_zoom + 1)
        cv.print(title, lay.title_xy[0], lay.title_xy[1],
                 th["ink"] if light else NAMES["green"], 1)
        if se is None or sheet is None:
            return
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
                cv.rectb(vx, vy, side, side,
                         NAMES["white"] if i == se.sel else NAMES["dark_grey"])
                if i == se.sel and side > 4:
                    cv.rectb(vx + 1, vy + 1, side - 2, side - 2, NAMES["white"])
        # -- tile palette (the stamp picker; the brush tile is boxed white).
        # #163 conversion: the grid's chrome is a MEMOIZED quad pack -- one
        # fill_rects paints every cell's black bg, one paints every dark
        # border -- with the tile stamps as a single spr_tile run between
        # them (one native blit_batch). Cells are disjoint rects, so the
        # phase split is pixel-identical to the old per-cell rect/spr/rectb
        # order; the brush cell's white border paints LAST over its dark
        # twin (opaque, same final pixels as drawing it alone). Was ~250
        # per-call verbs on an 80-cell pane; now 3 batched calls + a rectb.
        ids = self._palette_ids()
        tscale = max(1, lay.tp_cell // (sheet.TILE + 6))
        npal = len(ids)
        key = (lay.tp_x0, lay.tp_y0, lay.tp_cols, lay.tp_cell, npal)
        memo = self._tp_memo
        if memo is None or memo[0] != key:
            cell = lay.tp_cell
            bg = []
            bd = []
            for k in range(npal):
                x = key[0] + (k % key[2]) * cell
                y = key[1] + (k // key[2]) * cell
                bg += [x, y, cell, cell, 0]        # ci unused: c overrides
                bd += [x, y, cell, 1, 0,
                       x, y + cell - 1, cell, 1, 0,
                       x, y, 1, cell, 0,
                       x + cell - 1, y, 1, cell, 0]
            memo = (key, array("h", bg), array("h", bd))
            self._tp_memo = memo
        cv.fill_rects(memo[1], npal, 0, 0, NAMES["black"])
        inset = (lay.tp_cell - sheet.TILE * tscale) // 2
        for k in range(npal):
            cv.spr_tile(sheet, ids[k],
                        lay.tp_x0 + (k % lay.tp_cols) * lay.tp_cell + inset,
                        lay.tp_y0 + (k // lay.tp_cols) * lay.tp_cell + inset,
                        -1, tscale, 0)
        cv.fill_rects(memo[2], npal * 4, 0, 0, NAMES["dark_grey"])
        if se.n in ids:
            k = ids.index(se.n)
            cv.rectb(lay.tp_x0 + (k % lay.tp_cols) * lay.tp_cell,
                     lay.tp_y0 + (k // lay.tp_cols) * lay.tp_cell,
                     lay.tp_cell, lay.tp_cell, NAMES["white"])
        ws._btn("<", lay.tp_prev, NAMES["blue"], cv)
        ws._btn(">", lay.tp_next, NAMES["blue"], cv)
        # -- pan/zoom column + FLIP (props) in the corner slot.
        sel = se.selected()
        self._gbtn("flip_h", "FL", lay.flip_btn,
                   NAMES["dark_purple"] if sel is not None and sel.get("flip")
                   else (NAMES["indigo"] if sel is not None else NAMES["dark_grey"]),
                   cv)
        ws._btn("Z" + str(self.scene_zoom + 1), lay.zoom_btn, NAMES["dark_purple"], cv)
        ws._btn("^", lay.pan_up, NAMES["indigo"], cv)
        ws._btn("v", lay.pan_dn, NAMES["indigo"], cv)
        ws._btn("<", lay.pan_lf, NAMES["indigo"], cv)
        ws._btn(">", lay.pan_rt, NAMES["indigo"], cv)
        # -- props row: TAG field + DEL, SNAP toggle on the right.
        tx, ty, tw2, th2 = lay.tag_btn
        fs = lay.fs
        cv.rect(tx, ty, tw2, th2, NAMES["dark_blue"])
        if self.tag_edit:
            tag_text = "TAG " + self.tag_buf + "_"
        elif sel is not None:
            tag_text = "TAG " + (sel.get("tag") or "-")
        else:
            tag_text = "TAG -"
        cv.print(tag_text[:14], tx + 4 * fs, ty + (th2 - 8 * fs) // 2,
                 NAMES["white"], 1)
        cv.rectb(tx, ty, tw2, th2,
                 NAMES["white"] if self.tag_edit else
                 (NAMES["light_grey"] if sel is not None else NAMES["dark_grey"]))
        self._gbtn("clear", "DEL", lay.del_btn,
                   NAMES["red"] if sel is not None else NAMES["dark_grey"], cv)
        ws._btn("SNAP " + ("ON" if se.snap else "OFF"), lay.snap_btn,
                NAMES["dark_green"] if se.snap else NAMES["dark_grey"], cv)
        # -- toolbar: UNDO / REDO / FRONT (to top) / BACK (to bottom).
        self._gbtn("undo", "UN", lay.undo_btn,
                   NAMES["blue"] if se.can_undo() else NAMES["dark_grey"], cv)
        self._gbtn("redo", "RE", lay.redo_btn,
                   NAMES["blue"] if se.can_redo() else NAMES["dark_grey"], cv)
        self._gbtn("arr_u", "FR", lay.front_btn,
                   NAMES["orange"] if sel is not None else NAMES["dark_grey"], cv)
        self._gbtn("arr_d", "BK", lay.back_btn,
                   NAMES["orange"] if sel is not None else NAMES["dark_grey"], cv)

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
