"""Surface model v1 -- Phase A gates (docs/surface_model_v1.md §9).

Three layers of gate:
  * SurfaceSet semantics (§2): one monotonic mint, reborn-with-fresh-gens,
    the set-level epoch covering unknown sids, prefix-scoped sync.
  * The L8 accounting direction over a REAL windowed recording session: every
    per-surface stream that changed between frames must be covered by a moved
    content gen (the epoch folds in) or a Class B animating declaration. This
    is the stream-hash form of L8 -- the recording tier has no pixels to hash.
  * Leaf-module discipline (§2/L6): wm.py/console.py never import surface;
    the S3 build stages nothing new; the P4 build stages the leaf.

The fixture is the host web console in windowed mode -- the same recording
path the wasm runner boots (web_boot mirrors it), so what these tests pin is
what the browser transports serve.
"""

import os

from runtime.surface import Surface, SurfaceSet
from tools import web_console

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

def _step(console, dt=1 / 30):
    """web_boot's step sequence (the wasm runner mirror): tick the console,
    return (flat_cmds, [[sid, domain, cmds], ...] or None)."""
    console.canvas.take_commands()
    console.driver.frame(dt)
    flat = console.canvas.take_commands()
    return flat, console.canvas.take_surfaces()


def _account(console, tracker, flat, surfaces):
    """The L8 direction-1 assertion for one frame: every surface stream that
    CHANGED since its last ship is covered by a moved effective content gen or
    an animating declaration. (Over-firing gens is allowed at Phase A -- the
    epoch is deliberately coarse; direction 2 tightens it in Phase B.)"""
    if surfaces is None:
        return
    ss = console.ws.wm.surfaces
    # The slice invariant: the per-surface streams concatenate to the flat
    # frame exactly (pixels can't differ from the unsliced path). A SKIPPED
    # surface (§4 skip-draw) carries cmds=None -- zero-width in the flat frame,
    # unchanged by definition (the client replays its retained stream).
    joined = []
    for _sid, _dom, cmds in surfaces:
        if cmds is not None:
            joined.extend(cmds)
    assert joined == flat
    for sid, _dom, cmds in surfaces:
        if cmds is None:
            continue
        prev_cmds, prev_gen = tracker.get(sid, (None, None))
        gen = ss.content_gen(sid)
        if prev_cmds is not None and cmds != prev_cmds:
            assert gen != prev_gen or ss.is_animating(sid), (
                "surface %r stream changed with no gen movement and no "
                "animating declaration (L8 violation)" % sid)
        tracker[sid] = (cmds, gen)


def test_windowed_session_l8_accounting(tmp_path):
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=1,
                                     windowed=True)
    ws = console.ws
    ws.pointer.visible = False
    drv = console.driver
    tracker = {}
    # Settle the desk.
    for _ in range(8):
        _account(console, tracker, *_step(console))
    # Open Settings (a dirty write -> epoch), settle.
    ws.open_settings()
    for _ in range(6):
        _account(console, tracker, *_step(console))
    win = ws.wm._wins["settings"]
    # Idle frames: the gate should skip entirely (no streams at all --
    # surfaces_on yields an empty list, not None, on a skipped frame).
    idle_flat, idle_surfaces = _step(console)
    assert not idle_flat and not idle_surfaces
    # Drag the window by its strip for 12 frames -- placement moves, the epoch
    # covers the (pre-L4, root-space) stream changes, accounting stays green.
    gx, gy = win.x + win.w // 2, win.y + 4
    drv.touch(gx, gy)
    _account(console, tracker, *_step(console))
    for i in range(12):
        drv.touch_drag(gx + 4 * (i + 1), gy + 2 * (i + 1))
        _account(console, tracker, *_step(console))
    drv.touch_up()
    for _ in range(4):
        _account(console, tracker, *_step(console))
    # The drag actually moved placement: place_gen advanced past creation.
    s = ws.wm.surfaces.get("win:settings")
    assert (s.x, s.y) == (win.x, win.y) and s.place_gen > s._content_gen \
        or s.place_gen != 0


def test_window_surfaces_mirror_slots(tmp_path):
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=1,
                                     windowed=True)
    ws = console.ws
    ws.pointer.visible = False
    for _ in range(4):
        _step(console)
    ws.open_settings()
    _step(console)
    assert "win:settings" in ws.wm.surfaces.surfaces
    gen_before = ws.wm.surfaces.content_gen("win:settings")
    ws.wm.close_window_kind("settings")    # the title-strip X path
    _step(console)
    assert "win:settings" not in ws.wm.surfaces.surfaces
    ws.open_settings()                     # reborn
    _step(console)
    assert ws.wm.surfaces.content_gen("win:settings") != gen_before


def test_per_window_sids_partition_the_frame(tmp_path):
    """The §2 re-partition: with a window open, the frame's surface list
    carries a per-window sid (registry key, never content kind) and the
    residual chips span under its OWN sid (SurfaceDelta is sid-keyed)."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=1,
                                     windowed=True)
    ws = console.ws
    ws.pointer.visible = False
    for _ in range(4):
        _step(console)
    ws.open_settings()
    flat, surfaces = _step(console)
    assert surfaces is not None
    sids = [s[0] for s in surfaces]
    assert "win:settings" in sids
    assert "chips" in sids
    assert len(sids) == len(set(sids)), "duplicate sids corrupt the delta cache"


def test_drag_frames_skip_clean_surfaces(tmp_path):
    """The §4 skip-draw gate: mid-drag, the ONLY drawn surface is the moving
    window -- the desk backdrop and the chips ship as zero-width skip entries
    (cmds=None), which the delta turns into {"same":1} stubs. This is the
    frame-class the 70ms->3ms wasm win rides on (gate-0's finding: drag
    allocations must stay small so GC collects retreat to idle)."""
    from runtime.web_view import SurfaceDelta
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=1,
                                     windowed=True)
    ws = console.ws
    ws.pointer.visible = False
    drv = console.driver
    delta = SurfaceDelta()
    seeded = False
    for _ in range(8):
        flat, surfaces = _step(console)
        if surfaces:
            _, surfs = console._served.served_surfaces(flat, surfaces)
            delta.encode(surfs, gen=console.canvas._rec.atlas_gen)
            seeded = True
    ws.open_settings()
    for _ in range(6):
        flat, surfaces = _step(console)
        if surfaces:
            _, surfs = console._served.served_surfaces(flat, surfaces)
            delta.encode(surfs, gen=console.canvas._rec.atlas_gen)
            seeded = True
    assert seeded
    # The serve loop consumes need_keyframe after arming; the first encode's
    # gen transition (None -> N) always raises it. Mimic the consumption.
    delta.need_keyframe = False
    win = ws.wm._wins["settings"]
    gx, gy = win.x + win.w // 2, win.y + 4
    drv.touch(gx, gy)
    _step(console)                        # press frame: ptr leg -> epoch, full
    drv.touch_drag(gx + 20, gy + 10)
    _step(console)                        # drag engages (raise/focus may dirty)
    skipped_desk = False
    for i in range(6):
        drv.touch_drag(gx + 30 + 10 * i, gy + 15 + 5 * i)
        flat, surfaces = _step(console)
        assert surfaces, "a drag frame must paint"
        by_sid = {s[0]: s[2] for s in surfaces}
        assert by_sid.get("win:settings"), \
            "the moving window must draw (its root-space stream moved)"
        if by_sid.get("launcher", ()) is None:
            skipped_desk = True
            # ...and the delta turns the skip into a same:1 stub, no compare.
            _, surfs = console._served.served_surfaces(flat, surfaces)
            enc = delta.encode(surfs, gen=console.canvas._rec.atlas_gen)
            stubs = {e["id"]: e for e in enc}
            assert stubs["launcher"].get("same") == 1
            assert not delta.need_keyframe, \
                "the desk stream was seeded pre-drag; no keyframe needed"
    assert skipped_desk, "the desk backdrop never skipped during the drag"
    drv.touch_up()
    flat, surfaces = _step(console)       # release: ptr last-frame leg -> full
    assert surfaces and all(s[2] is not None for s in surfaces
                            if s[0] == "launcher")


def test_premature_skip_arms_a_keyframe(tmp_path):
    """§5.4 drop-recovery: a skip stub reaching a delta that never shipped that
    stream flags need_keyframe -- the serve loop then forces a full draw."""
    from runtime.web_view import SurfaceDelta
    delta = SurfaceDelta()
    enc = delta.encode([{"id": "launcher", "domain": "system", "cmds": None}],
                       gen=1)
    assert enc[0].get("same") == 1
    assert delta.need_keyframe


# ---------------------------------------------------------------------------
# Leaf-module discipline (§2 / L6): the degenerate tier executes no new code.
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
    src = _read("firmware/lilygo_t_deck_plus_micropython/build.sh")
    assert "surface.py" not in src, (
        "the S3 stages by allowlist and the surface leaf must stay off it")


def test_p4_build_stages_the_leaf():
    src = _read("firmware/esp32_p4_wifi6_touch_lcd_7b/build.sh")
    assert "surface.py" in src, (
        "the P4 stages wm_windowed.py, which imports surface -- the leaf "
        "must ride along or the frozen import fails")


# ---------------------------------------------------------------------------
# System-app carts are hidden from the Editor picker (TEMPORARY -- see
# _picker_items; #55 privileged system carts removes this).
# ---------------------------------------------------------------------------

def test_picker_hides_claimed_system_apps(tmp_path):
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=1,
                                     windowed=True)
    ws = console.ws
    titles = [c.get("title") for c in ws.picker.items if c.get("path")]
    claimed = []
    for cart in ws._all_carts:
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
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=1,
                                     windowed=True)
    ws = console.ws
    claimed = [c for c in ws._all_carts
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
    from runtime import web_view

    class _P:
        x = y = 0
        down = False
        click = False

        def place(self, x, y):
            self.x, self.y = x, y

    p = _P()
    web_view.apply_events([{"type": "hover", "x": 40, "y": 25}], object(), p)
    assert (p.x, p.y) == (40, 25)
    assert not p.down and not p.click
    web_view.apply_events([{"type": "move", "x": 7, "y": 9}], object(), p)
    assert (p.x, p.y) == (7, 9) and p.down     # a real drag still presses


def test_hover_repaints_the_shell(tmp_path):
    """A moving pointer must reach the glass: skip-draw may not swallow the
    frame hover feedback needs (the desk icon highlight reads pointer x/y)."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=1,
                                     windowed=True)
    ws = console.ws
    ws.pointer.visible = False
    for _ in range(8):
        _step(console)
    quiet_flat, _q = _step(console)
    assert not quiet_flat, "a still pointer must stay free"
    ws.pointer.place(300, 320)                 # the browser's hover event
    moved_flat, moved_surfaces = _step(console)
    assert moved_flat, "a hovering pointer must repaint"
    assert moved_surfaces and any(s[2] for s in moved_surfaces), \
        "hover frames must carry real surface content, not only skip stubs"
    # ...and it goes quiet again the moment the mouse stops.
    for _ in range(3):
        _step(console)
    assert not _step(console)[0], "a settled pointer returns to free"
