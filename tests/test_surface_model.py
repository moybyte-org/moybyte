"""Surface model v1 -- Phase A gates (docs/surface_model_v1.md §9), on the
kernel's surface table (§15: native/moy_glass, the glass pass of sprint 3).

Two layers of gate:
  * The table's semantics (§2): one monotonic mint, reborn-with-fresh-gens,
    the set-level epoch covering unknown sids, prefix-scoped sync -- pinned on
    the binding the host reaches the C through, which is the same C every
    board and the browser link.
  * Leaf discipline (§2/L6): wm.py never signals the table; console.py
    reaches it for one call per painted frame (the epoch fold) and the
    kernel's own epoch, never per write; the S3 build stages no windowed WM.

The third layer is GONE as of moycore stage 4: the L8 stream-hash accounting
ran over a windowed RECORDING session, and the recording tier it measured
(per-surface command streams, skip-draw, the keyframe verb) was deleted when
the wasm head started rasterizing. Those were the doc's Phase B/D machinery,
whose retirement the §5.4 stage-4 amendment records.

The table is one per image, so each test below names sids of its own.

The fixture is therefore the ordinary raster workstation in windowed mode.
"""

import os

from runtime import glass_binding as g
from runtime import host_app, web_input

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# The surface table's semantics (§2)
# ---------------------------------------------------------------------------

def test_mint_is_monotonic_and_shared():
    a = g.surface("win:mint-a")
    b = g.surface("mint-b")
    gens = list(g.gens(a)) + list(g.gens(b))
    g.touch(a)
    g.move(b)
    assert g.gens(a)[0] > max(gens)
    assert g.gens(b)[1] > g.gens(a)[0]


def test_reborn_surface_gets_fresh_gens():
    """The aliasing hazard (§2): a client cached gen G for a sid; the window
    closes and reopens. The reborn record must NEVER reuse a gen a client
    could hold -- fresh mint, strictly newer."""
    old = g.content_gen(g.surface_find("win:reborn"))   # unknown sid = epoch
    g.touch(g.surface("win:reborn"))
    seen = g.content_gen(g.surface_find("win:reborn"))
    g.sync([], "win:reborn")                            # window closed: row dropped
    assert g.surface_find("win:reborn") == 0
    g.touch(g.surface("win:reborn"))                    # reopened + first change
    assert g.content_gen(g.surface_find("win:reborn")) > seen > old


def test_epoch_covers_unknown_sids():
    """Class A un-attributed: a ws._dirty write nobody attributed must make
    EVERY surface -- even one with no row -- read as changed (§3)."""
    g0 = g.content_gen(g.surface_find("never-registered"))
    g.epoch()
    assert g.content_gen(g.surface_find("never-registered")) != g0
    # ...and a consumer that saw the epoch'd value sees no change until the
    # next signal (compare is !=, per-consumer last-seen).
    g1 = g.content_gen(g.surface_find("never-registered"))
    assert g.content_gen(g.surface_find("never-registered")) == g1


def test_a_stale_handle_rides_the_epoch_alone():
    h = g.surface("win:stale")
    g.touch(h)
    g.drop(h)
    g.epoch()
    assert g.content_gen(h) == g.content_gen(0)


def test_sync_is_prefix_scoped():
    g.touch(g.surface("win:sync-a"))
    g.touch(g.surface("sync-chips"))
    g.surface("sync-cursor")
    g.sync(["win:sync-other"], "win:sync-")
    assert g.surface_find("win:sync-a") == 0             # dropped with its slot
    assert g.surface_find("sync-chips") != 0             # non-window: never dropped
    assert g.surface_find("sync-cursor") != 0


def test_placement_wire_shape():
    h = g.surface("win:place")
    p0 = g.gens(h)[1]
    assert g.place(h, 40, 30, 100, 80, 1)
    assert g.gens(h)[1] > p0                             # placement moved its gen
    assert not g.place(h, 40, 30, 100, 80, 1)            # unchanged: no mint
    assert g.placement(h) == [40, 30, 1, 1]


def test_the_kernel_epoch_reads_as_dirty_for_every_retained_frame(tmp_path):
    """The kernel draws frames of its own under a Python WM (the idle wake,
    the saver, the floor): the frame gate reads its epoch as one more leg,
    dirty for as many frames as the backend retains (§15)."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    for _ in range(4):
        drv.frame(1 / 60)
    assert not ws._needs_redraw(1 / 60)
    g.kernel_bump()
    retained = max(1, getattr(ws._sys_canvas or ws.canvas, "RETAINED_FRAMES", 1))
    for _ in range(retained):
        assert ws._needs_redraw(1 / 60)
        ws._dirty = False
    assert not ws._needs_redraw(1 / 60)


# ---------------------------------------------------------------------------
# Leaf discipline
# ---------------------------------------------------------------------------

def _read(rel):
    with open(os.path.join(_REPO, rel)) as f:
        return f.read()


def test_wm_never_signals_the_table():
    src = _read("runtime/wm.py")
    assert "moy_glass" not in src and "glass_binding" not in src, (
        "the fullscreen WM signals nothing: the frame gate folds its dirty "
        "into the epoch (spec L6/§15)")


def test_the_frame_gate_folds_dirty_once_per_painted_frame():
    src = _read("runtime/console.py")
    assert src.count("_glass.epoch()") == 1
    assert "_glass.touch(" not in src


def test_the_s3_build_stages_no_windowed_tier():
    from tools.board_config import staged_modules

    board = os.path.join(_REPO, "firmware", "lilygo_t_deck_plus_mainline")
    staged = staged_modules(board, _REPO)
    assert "wm_windowed.py" not in staged and "surface.py" not in staged


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
