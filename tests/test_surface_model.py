"""Surface model v1 (docs/surface_model_v1.md): the frame gate's kernel-epoch
leg, the leaf discipline around the glass binding, and the windowed-tier
fixtures the rest of this file builds on.

The kernel epoch (native/moy_glass) is the one glass signal the Python frame
gate reads: pinned on the binding the host reaches the C through, which is the
same C every board and the browser link.
"""

import os

from runtime import glass_binding as g
from runtime import host_app, web_input

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        "the fullscreen WM signals nothing: the frame gate reads the "
        "kernel's epoch (spec L6/§15)")


def test_the_console_reads_only_the_kernel_epoch():
    src = _read("runtime/console.py")
    assert src.count("_glass.kernel_epoch()") == 2
    assert src.count("_glass.") == 2


def test_the_s3_build_stages_no_windowed_tier():
    from tools.board_config import staged_modules

    board = os.path.join(_REPO, "firmware", "lilygo_t_deck_plus_mainline")
    staged = staged_modules(board, _REPO)
    assert "wm_windowed.py" not in staged and "surface.py" not in staged


# ---------------------------------------------------------------------------
# System-app carts are hidden from the Make tab (TEMPORARY -- see
# _make_items; #181 editable system apps removes this).
# ---------------------------------------------------------------------------

def test_make_hides_claimed_system_apps(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"), sys_size=(1024, 600),
                                    windowed=True)
    ws.open_projects()
    titles = [c.get("title") for c in ws.projects_grid.items if c.get("path")]
    claimed = []
    for cart in ws.carts.all:
        for app, _t in getattr(ws, "_apps", ()):
            if app.is_app(cart):
                claimed.append(cart.get("title"))
                break
    assert claimed, "fixture has no system-app carts -- test proves nothing"
    for t in claimed:
        assert t not in titles, "%r is a shell app, not an editable project" % t
    # ...and Make still offers real projects + the New card.
    assert any(not c.get("path") for c in ws.projects_grid.items), "the + New card"
    assert titles, "non-app carts must still be listed"


def test_desk_still_offers_system_apps(tmp_path):
    """Hiding is MAKE-only: the DESK icon column still opens them (on this
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
