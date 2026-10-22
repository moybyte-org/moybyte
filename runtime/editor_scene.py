# Map (grep -n a name to jump there):
#   ScenePage              the handheld Scene tab on the verbs
#   ScenePage.draw         the view, the actor or the stamps, and the foot
#   ScenePage._actor       the picked actor: its tag, its place, its verbs
#   ScenePage._stamps      the stamp tool's sprites, a page at a time, and its tag
#   ScenePage.palette_tap  a tap on a stamp
"""The handheld Editor's Scene tab on the public UI API
(docs/media/look_2026-10/EDITOR_LAYOUT.md's `scene` pictures): the world
from the content rect's top left; beside it the picked actor -- its tag and
sprite, its place, then Tag, Flip, Front, Back and Remove -- or, under the
stamp tool, the sprites a page at a time and the tag a stamp gives; under
both the pick and stamp tools, Snap, the zoom and the scene's name.

The view is `SceneEditorUI.draw_view`'s and its gestures `SceneEditorUI`'s
(a drag moves an actor or pans; under the stamp tool a tap on empty world
places one); the keys stay the tab's own (the arrows nudge the picked actor
or pan, A steps the zoom). The frame begins with no stop holding focus, and
the verbs take the taps. A tag is typed in a popover, whose keys are its own
while it is open. Every change is the core's (`SceneEditor`), synced to the
running game as the tab's taps are."""

try:
    import ui
    from editor_map import checker
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui
    from runtime.editor_map import checker

ACTOR_VERBS = ("Tag", "Flip", "Front", "Back", "Remove")
ACTOR_ICONS = ("notes", "flip", "arrow_up", "arrow_down", "del")
CELL = 22                        # a stamp's pitch; the sprite's tile 20 inside it
TOOL = 24                        # a foot tool's cell


def _width(text):
    ui.face("ui")
    w = ui.text_size(text)[0]
    ui.face()
    return w


class ScenePage:
    """The Scene tab over `su`, the workstation's `SceneEditorUI`; `host`
    is the frame's `AppHost`."""

    def __init__(self, su, host):
        self.su = su
        self.host = host
        self.pop = None                  # "tag" or "stamp" while its popover is open
        self.text = ""                   # the tag being typed
        self.tool = "pointer"            # the foot's tool: pointer | stamp
        su.place_taps = False

    def _greys(self):
        return self.su.ws.look.live.fixed[:2]

    def _checker(self, cv, x, y, s):
        dark, light = self._greys()
        checker(cv, x, y, s, s, 4, dark, light)

    # -- drawing ------------------------------------------------------------------

    def draw(self, cv, th, rect, desk):
        su = self.su
        host = self.host
        host.begin(cv, th, rect, desk, False, elsewhere=self.pop is None)
        ui.at(*rect)
        ui.page("")
        se = su.sceneedit
        if se is None or su.ws.project.sheet is None:
            ui.at(*rect)
            ui.empty("scene", "No scene", ("This cart has no sprites to place",))
            ui.end()
            return
        x, y, w, h = rect
        su.draw_view(cv, False)
        r = se.selected()
        if r is not None:
            s = su._sv_metrics()[2] * su.ws.project.sheet.TILE
            vx, vy = self._to_view(r["x"], r["y"])
            ax, ay, aw, ah = su._sv_area()
            if ax <= vx and ay <= vy and vx + s <= ax + aw and vy + s <= ay + ah:
                ui.cursor(vx, vy, s, s)
        px = su.layout.tp_x0
        if self.tool == "stamp":
            self._stamps(cv, se, px, y + 6)
        elif r is not None:
            self._actor(cv, se, r, px, y + 6)
        else:
            ui.at(px, y + 8, 96, 18)
            ui.label("Nothing picked")
            ui.at(px, y + 28, 96, 60)
            ui.label("Tap an actor to change it, or take the stamp to place one",
                     {"tone": "dim", "wrap": True, "lines": 4})
        self._foot(se, x, y + h - 26, w)
        if self.pop is not None:
            self._tag_pop(se)
        ui.end()

    def _to_view(self, wx, wy):
        su = self.su
        se = su.sceneedit
        x0, y0, scale, _vw, _vh = su._sv_metrics()
        return x0 + (wx - se.cam_x) * scale, y0 + (wy - se.cam_y) * scale

    def _actor(self, cv, se, r, px, y):
        sheet = self.su.ws.project.sheet
        self._checker(cv, px + 68, y, 24)
        img = sheet.tile_image(r.get("tile", 0), 0)
        if img is not None:
            cv.spr(img, px + 68, y, 3)
        ui.at(px, y + 2, 66, 18)
        ui.label(str(r.get("tag") or "thing"), {"face": "title"})
        ui.at(px, y + 28, 98, 16)
        ui.label("x %d  y %d" % (r.get("x", 0), r.get("y", 0)), {"tone": "dim"})
        ui.at(px, y + 46, 92, 1)
        ui.rule()
        ui.at(px - 6, y + 50, 104, 5 * 22)
        got = ui.menu("", ACTOR_VERBS, ACTOR_ICONS,
                      kinds=("", "", "", "", "danger"))
        if got is not None and got >= 0:
            self.act(ACTOR_VERBS[got])

    def _stamps(self, cv, se, px, y):
        su = self.su
        sheet = su.ws.project.sheet
        ids = su._palette_ids()
        for k in range(len(ids)):
            tx = px + (k % 4) * CELL
            ty = y + (k // 4) * CELL
            self._checker(cv, tx, ty, CELL - 2)
            img = sheet.tile_image(ids[k], -1)
            if img is not None:
                cv.spr(img, tx + 2, ty + 2, 2)
        if se.n in ids:
            k = ids.index(se.n)
            ui.cursor(px + (k % 4) * CELL, y + (k // 4) * CELL, CELL - 2, CELL - 2)
        py = y + 4 * CELL + 4
        pages = max(1, (sheet.count + 15) // 16)
        cur = su.scene_page // 16
        ui.at(px - 2, py - 2, 20, 20)
        if ui.tool("back", "", {"id": "Previous stamps", "disabled": cur == 0}):
            su.scene_page = max(0, su.scene_page - 16)
        ui.at(px + 18, py, 52, 16)
        ui.label("%d / %d" % (cur + 1, pages), {"align": "center"})
        ui.at(px + 70, py - 2, 20, 20)
        if ui.tool("forward", "", {"id": "Next stamps", "disabled": cur + 1 >= pages}):
            su.scene_page = min((pages - 1) * 16, su.scene_page + 16)
        ui.at(px, py + 22, 26, 18)
        ui.label("Tag", {"tone": "dim"})
        ui.at(px + 26, py + 21, 70, 20)
        if ui.button(str(se.stamp_tag or "thing"), "normal", {"id": "Stamp tag"}):
            self.open_tag("stamp", se.stamp_tag or "")
        ui.at(px, py + 48, 98, 16)
        ui.label("Tap to place", {"tone": "dim"})

    def _foot(self, se, x, y, w):
        su = self.su
        ui.at(x, y - 1, w, 1)
        ui.rule()
        for i, (icon, word, tool) in enumerate((("pointer", "Pick", "pointer"),
                                                ("stamp", "Stamp", "stamp"))):
            ui.at(x + 2 + i * TOOL, y + 1, TOOL, TOOL)
            if ui.tool(icon, "", {"id": "Tool " + word, "current": self.tool == tool}):
                self.set_tool(tool)
        cx = x + 2 + 2 * TOOL + 4
        ui.at(cx, y + 6, 1, 14)
        ui.rule()
        cx += 5
        sw = _width("Snap") + 30
        ui.at(cx, y + 3, sw, 20)
        ui.icon("grid")
        if ui.button("Snap", "normal", {"id": "Snap", "current": bool(se.snap)}):
            se.snap = not se.snap
        cx += sw + 4
        zoom = "%d%s" % (su._sv_metrics()[2], "×" if ui.has_symbols() else "x")
        zw = _width(zoom) + 30
        ui.at(cx, y + 3, zw, 20)
        ui.icon("search")
        if ui.button(zoom, "normal", {"id": "Zoom"}):
            su._cycle_zoom()
        name = str(su.scene_name or "")
        nw = min(_width(name), x + w - 6 - (cx + zw + 6))
        if nw > 0:
            ui.at(x + w - 6 - nw, y + 5, nw, 18)
            ui.label(name, {"tone": "dim"})

    def _tag_pop(self, se):
        box = self.host.box("Stamp tag" if self.pop == "stamp" else "Tag")
        if box is not None:
            ui.at(*box)
        if not ui.popover("Tag popover", 150):
            self.close()
            return
        text, done = ui.field("Tag", self.text, se.TAG_MAX, {"id": "Tag text", "hint": "coin"})
        self.text = text
        if done:
            self.commit()
        ui.popover()

    # -- the verbs ------------------------------------------------------------------

    def set_tool(self, tool):
        self.tool = tool
        self.su.place_taps = tool == "stamp"

    def act(self, verb):
        """Actor verb `verb` on the picked actor (its word in ACTOR_VERBS)."""
        su = self.su
        se = su.sceneedit
        if verb == "Tag":
            r = se.selected()
            self.open_tag("tag", r.get("tag", "") if r is not None else "")
            return
        changed = False
        if verb == "Flip":
            changed = se.toggle_flip()
        elif verb == "Front":
            changed = se.front_sel()
        elif verb == "Back":
            changed = se.back_sel()
        elif verb == "Remove":
            changed = se.delete_sel()
        if changed:
            su._sync_live()

    def open_tag(self, which, text):
        """Type the picked actor's tag (`which` "tag") or the stamp's
        ("stamp") in a popover, the keyboard in text mode while it is."""
        self.pop = which
        self.text = str(text)
        self.su.ws._set_text_mode(True)
        self.host.focus("Tag text")

    def commit(self):
        """The typed tag, given to the actor or the stamp."""
        su = self.su
        se = su.sceneedit
        if self.text:
            if self.pop == "tag":
                if se.set_tag(self.text):
                    su._sync_live()
            else:
                se.stamp_tag = self.text[:se.TAG_MAX]
        self.close()

    def close(self):
        self.pop = None
        self.su.ws._set_text_mode(False)

    # -- input ------------------------------------------------------------------------

    def palette_tap(self, px, py):
        """A tap on a stamp under the stamp tool picks it. Whether it was one."""
        su = self.su
        lay = su.layout
        se = su.sceneedit
        if se is None or self.tool != "stamp":
            return False
        x0, y0 = lay.tp_x0, lay.tp_y0
        if not (x0 <= px < x0 + 4 * CELL and y0 <= py < y0 + 4 * CELL):
            return False
        k = (py - y0) // CELL * 4 + (px - x0) // CELL
        ids = su._palette_ids()
        if 0 <= k < len(ids):
            se.set_brush(ids[k])
        return True

    def keys(self, i):
        """A frame's keys while the tag popover is open: the engine's.
        Whether the page owes a frame."""
        return self.host.keys(i)

    def point(self, px, py, click, down):
        """A pointer sample for the verbs. Whether the page owes a frame."""
        return self.host.point(px, py, click, down)

    def owed(self):
        """Whether a tap or a rest frame is still to be drawn."""
        h = self.host
        return h.owed or h.tap != 0
