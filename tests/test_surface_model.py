"""Surface model v1 -- Phase A gates (docs/surface_model_v1.md §9).

Two layers of gate:
  * SurfaceSet semantics (§2): one monotonic mint, reborn-with-fresh-gens,
    the set-level epoch covering unknown sids, prefix-scoped sync.
  * Leaf-module discipline (§2/L6): wm.py/console.py never import surface;
    the S3 build stages nothing new; the P4 build stages the leaf.

The third layer is GONE as of moycore stage 4: the L8 stream-hash accounting
ran over a windowed RECORDING session, and the recording tier it measured
(per-surface command streams, skip-draw, the keyframe verb) was deleted when
the wasm head started rasterizing. Those were the doc's Phase B/D machinery,
whose retirement the §5.4 stage-4 amendment records. What survives here is the
registry itself -- Phase A/C groundwork for the raster tiers, which do not
drive it yet -- and the discipline that keeps it a leaf.

The fixture is therefore the ordinary raster workstation in windowed mode.
"""

import os

from runtime import host_app, web_input
from runtime.surface import Surface, SurfaceSet

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# SurfaceSet semantics (§2)
# ---------------------------------------------------------------------------

def test_mint_is_monotonic_and_shared():
    ss = SurfaceSet()
    a = ss.get("win:make")
    b = ss.get("bar")
    gens = [a._content_gen, a.place_gen, b._content_gen, b.place_gen]
    ss.touch("win:make")
    ss.move("bar")
    assert ss.get("win:make")._content_gen > max(gens)
    assert ss.get("bar").place_gen > ss.get("win:make")._content_gen


def test_reborn_surface_gets_fresh_gens():
    """The aliasing hazard (§2): a client cached gen G for a sid; the window
    closes and reopens. The reborn record must NEVER reuse a gen a client
    could hold -- fresh mint, strictly newer."""
    ss = SurfaceSet()
    old = ss.content_gen("win:settings")  # forces nothing: unknown sid = epoch
    ss.touch("win:settings")
    seen = ss.content_gen("win:settings")
    ss.sync(set())                        # window closed: record dropped
    ss.touch("win:settings")              # reopened + first change
    assert ss.content_gen("win:settings") > seen > old


def test_epoch_covers_unknown_sids():
    """Class A un-attributed: a ws._dirty write nobody attributed must make
    EVERY surface -- even one with no record -- read as changed (§3)."""
    ss = SurfaceSet()
    g0 = ss.content_gen("never-registered")
    ss.epoch()
    assert ss.content_gen("never-registered") != g0
    # ...and a consumer that saw the epoch'd value sees no change until the
    # next signal (compare is !=, per-consumer last-seen).
    g1 = ss.content_gen("never-registered")
    assert ss.content_gen("never-registered") == g1


def test_sync_is_prefix_scoped():
    ss = SurfaceSet()
    ss.touch("win:make")
    ss.touch("chips")
    ss.get("cursor")
    ss.sync({"win:other"})
    assert "win:make" not in ss.surfaces          # dropped with its slot
    assert "chips" in ss.surfaces                 # non-window: never dropped
    assert "cursor" in ss.surfaces


def test_placement_wire_shape():
    s = Surface("win:make", "system", 7)
    s.x, s.y, s.scale, s.z = 40, 30, 2, 1
    assert s.place() == [40, 30, 2, 1]


# ---------------------------------------------------------------------------
# The L8 accounting gate over a real windowed recording session
# ---------------------------------------------------------------------------

def _read(rel):
    with open(os.path.join(_REPO, rel)) as f:
        return f.read()


def test_surface_is_a_leaf_module():
    for shared in ("runtime/wm.py", "runtime/console.py"):
        src = _read(shared)
        assert "import surface" not in src and "from surface" not in src, (
            "%s must never import the surface leaf (spec L6/§2)" % shared)


def test_s3_build_does_not_stage_the_leaf():
    # Both boards stage by DENYLIST since #161 Phase 3 (board.toml), so the
    # exclusion is now a line with a reason on it rather than an absence from a
    # shell script -- but the claim is unchanged, and it is asked of the staged
    # set rather than of build.sh's text.
    from tools.board_config import staged_modules, denials

    board = os.path.join(_REPO, "firmware", "lilygo_t_deck_plus_mainline")
    assert "surface.py" not in staged_modules(board, _REPO), (
        "the S3 is the fullscreen-stack tier and the surface leaf must stay off it")
    assert denials(board)["surface.py"]["kind"] == "tier", (
        "the S3's denial of surface.py must be recorded as a TIER decision, "
        "not as a host-only or broken-import one")


def test_p4_build_stages_the_leaf():
    from tools.board_config import staged_modules

    board = os.path.join(_REPO, "firmware", "esp32_p4_wifi6_touch_lcd_7b")
    staged = staged_modules(board, _REPO)
    assert "surface.py" in staged, (
        "the P4 stages wm_windowed.py, which imports surface -- the leaf "
        "must ride along or the frozen import fails")
    assert "wm_windowed.py" in staged


# ---------------------------------------------------------------------------
# System-app carts are hidden from the Editor picker (TEMPORARY -- see
# _picker_items; #181 editable system apps removes this).
# ---------------------------------------------------------------------------

def test_picker_hides_claimed_system_apps(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True)
    titles = [c.get("title") for c in ws.picker.items if c.get("path")]
    claimed = []
    for cart in ws.carts.all:
        for app, _t in getattr(ws, "_apps", ()):
            if app.is_app(cart):
                claimed.append(cart.get("title"))
                break
    assert claimed, "fixture has no system-app carts -- test proves nothing"
    for t in claimed:
        assert t not in titles, "%r is a shell app, not an editable project" % t
    # ...and the picker still offers real projects + the New tile.
    assert any(not c.get("path") for c in ws.picker.items), "the + New tile"
    assert titles, "non-app carts must still be listed"


def test_desk_still_offers_system_apps(tmp_path):
    """Hiding is PICKER-only: the DESK icon column still opens them (on this
    tier apps leave the shelf by design -- "apps are windows, games are
    fullscreen" -- so the desk is their access path), and the filter must not
    cost a kid access to Files or Paint."""
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True)
    claimed = [c for c in ws.carts.all
               if any(app.is_app(c) for app, _t in getattr(ws, "_apps", ()))]
    assert claimed
    ids = [row[0] for row in ws.wm._backdrop_layer._icon_catalog()]
    for app, _t in getattr(ws, "_apps", ()):
        if app.id in ws.wm._backdrop_layer.HIDDEN_APPS:
            continue
        assert app.id in ids, "%s must stay reachable from the desk" % app.id
    # ...and each claimed cart still supplies its icon art to that column.
    arts = [row[2] for row in ws.wm._backdrop_layer._icon_catalog() if row[2] is not None]
    assert arts, "the desk icons lost their cart artwork"


# ---------------------------------------------------------------------------
# Hover: the browser reports an idle pointer, and the shell repaints for it.
# ---------------------------------------------------------------------------

def test_hover_event_places_without_pressing():
    """A hover must NOT assert `down` -- that would fake a drag out of an idle
    mouse (drag-scrolling a grid, moving a window)."""
    from runtime import web_input

    class _P:
        x = y = 0
        down = False
        click = False

        def place(self, x, y):
            self.x, self.y = x, y

    p = _P()
    web_input.apply_events([{"type": "hover", "x": 40, "y": 25}], object(), p)
    assert (p.x, p.y) == (40, 25)
    assert not p.down and not p.click
    web_input.apply_events([{"type": "move", "x": 7, "y": 9}], object(), p)
    assert (p.x, p.y) == (7, 9) and p.down     # a real drag still presses


def test_hover_repaints_the_shell(tmp_path):
    """A moving pointer must reach the glass: the redraw gate may not swallow
    the frame hover feedback needs (the desk icon highlight reads pointer x/y).

    Measured in PAINTS rather than in shipped command streams -- the recording
    tier this was written against is gone, and `_frames_drawn` is the same
    signal the wasm head's step_frame reports to its page.
    """
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True)
    ws.pointer.visible = False
    for _ in range(8):
        ws.frame(1 / 30.0)

    def painted():
        before = ws._frames_drawn
        ws.frame(1 / 30.0)
        return ws._frames_drawn != before

    assert not painted(), "a still pointer must stay free"
    ws.pointer.place(300, 320)                 # the browser's hover event
    assert painted(), "a hovering pointer must repaint"
    for _ in range(3):
        ws.frame(1 / 30.0)
    assert not painted(), "a settled pointer returns to free"
