"""The retained home-frame stamp (launcher_layer, 2026-07-27): a repeat home
visit whose pixels are provably unchanged presents the captured frame in one
blit instead of rebuilding wallpaper + panel + cards + bar (~150ms on P4 glass
-- the last remaining transition frame after the cover warm-up).

Pins:
  * a re-entry with an unchanged _retained_key does NOT re-run the grid draw;
  * the stamped frame is PIXEL-IDENTICAL to a live render of the same state;
  * selection / title changes invalidate (the silent-cache §2.1 net);
  * the paint-continuity guard: foreign paints since the last home draw reset
    the drag-partial streak (the retained ping-pong buffers are foreign).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ws(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    return ws


from ws_helpers import make_drv as _drv


from ws_helpers import quiesce as _quiesce


def _count_grid(ws):
    calls = [0]
    orig = ws.launcher.draw
    ws.launcher.draw = lambda cv, sf=None: (calls.__setitem__(0, calls[0] + 1),
                                            orig(cv, sf))[1]
    return calls


def _settle_covers(ws, drv, limit=400):
    """Run frames until the cover pipeline has nothing left to land: no build
    in flight and the cover generation unchanged for several frames. Builds
    and the idle prefetch are sliced by the WALL CLOCK, so how many covers a
    frame lands depends on how fast the machine is, and a cover landing between
    two renders makes them differ for a reason that has nothing to do with the
    stamp (a slow CI runner did exactly that at x=213 of the shelf)."""
    covers = ws.covers
    last = covers.gen
    still = 0
    for _ in range(limit):
        _quiesce(ws)
        drv.frame(0.0)
        if covers._jobs or covers.gen != last:
            last = covers.gen
            still = 0
            continue
        still += 1
        if still >= 5:
            return
    raise AssertionError("the cover pipeline never settled in %d frames" % limit)


def _settle_home(tmp_path):
    """Boot to the home shelf with every cover landed, one settled paint
    captured. The bar's live HH:MM is bound to a constant for the same reason
    the covers are drained: two renders a moment apart must not differ by
    anything but the path under test."""
    ws = _ws(tmp_path)
    ws.bar_layer._clock_text = lambda: "00:00"
    drv = _drv(ws)
    drv.frame(0.0)
    _settle_covers(ws, drv)
    _quiesce(ws)
    ws._dirty = True
    drv.frame(0.0)
    assert ws.launcher_layer._lib_key is not None    # capture landed
    return ws, drv


def test_reentry_stamps_instead_of_redrawing(tmp_path):
    ws, drv = _settle_home(tmp_path)
    ws.open_settings()
    drv.frame(0.0)
    calls = _count_grid(ws)
    ws.go_home()
    _quiesce(ws)          # navigation can fire an achievement toast; an active
    drv.frame(0.0)        # toast reads as _animating and refuses the stamp
    assert ws.wm.top_kind() == "launcher"
    assert calls[0] == 0                             # stamped, not re-rendered


def test_stamped_frame_matches_live_render(tmp_path):
    ws, drv = _settle_home(tmp_path)
    ws.open_settings()
    drv.frame(0.0)
    ws.go_home()
    _quiesce(ws)
    drv.frame(0.0)                                   # the stamped re-entry
    stamped = bytes(ws.sys_canvas._buf)
    gen = ws.covers.gen
    ws.launcher_layer._lib_key = None                # force the live path
    ws._dirty = True
    drv.frame(0.0)
    live = bytes(ws.sys_canvas._buf)
    assert ws.covers.gen == gen, "a cover landed between the two renders"
    assert stamped == live


def test_selection_change_invalidates(tmp_path):
    ws, drv = _settle_home(tmp_path)
    ws.open_settings()
    drv.frame(0.0)
    ws.go_home()
    _quiesce(ws)
    drv.frame(0.0)
    calls = _count_grid(ws)
    nxt = next(i for i, it in enumerate(ws.launcher.items)
               if it.get("path") and i != ws.launcher.sel)
    ws.launcher.sel = nxt
    ws._dirty = True
    drv.frame(0.0)
    assert calls[0] == 1                             # live redraw, ring + all


def test_title_change_invalidates(tmp_path):
    """A rename with an unchanged item COUNT must still repaint -- the key
    carries per-item titles precisely because len() alone cannot see it."""
    ws, drv = _settle_home(tmp_path)
    it = next(x for x in ws.launcher.items if x.get("path"))
    it["title"] = "RENAMED"
    calls = _count_grid(ws)
    ws._dirty = True
    drv.frame(0.0)
    assert calls[0] == 1


def test_foreign_paints_reset_the_partial_streak(tmp_path):
    """After a visit elsewhere the retained ping-pong buffers hold the OTHER
    screen -- the drag partial's streak must re-arm from zero (latent on the
    device root before this; masked while transitions painted many frames)."""
    ws, drv = _settle_home(tmp_path)
    ws._dirty = True
    drv.frame(0.0)                                   # second consecutive paint
    assert ws.launcher_layer._full_streak == 2
    ws.open_settings()
    drv.frame(0.0)                                   # a foreign paint
    ws.go_home()
    _quiesce(ws)
    drv.frame(0.0)                                   # re-entry paint no. 1
    assert ws.launcher_layer._full_streak == 1       # NOT the inherited 2
    ws._dirty = True
    drv.frame(0.0)                                   # consecutive paint no. 2
    assert ws.launcher_layer._full_streak == 2


def test_a_frame_that_deferred_a_cover_is_not_stamped_back(tmp_path, monkeypatch):
    """A painted frame that drew a placeholder because its card's cover build
    was pushed past the frame's budget is not a settled frame: the frames
    after it draw the grid again, step the build, and every cover lands --
    including cards past the idle prebuild's first screenful, which nothing
    else ever decodes. Stamping that frame back left them placeholders."""
    from runtime import cover_cache
    ws, drv = _settle_home(tmp_path)
    la = ws.launcher
    ws.covers.prefetch_tick = lambda: None        # only painted frames build
    monkeypatch.setattr(cover_cache, "_COVER_SLICE_MS", 0)
    ws.covers._drop_payloads(0)
    la.sel = len(la.items) - 1
    la._scroll_to_sel()
    ws._dirty = True
    shown = set((la.items[i].get("path")) for i, _r in la._visible())
    want = [(it["path"], div) for it, div in la.cover_specs() if it["path"] in shown]
    assert want, "no covered card on the last screenful"
    for _ in range(400):
        _quiesce(ws)
        drv.frame(0.0)
        if all(k in ws.covers._cache for k in want):
            break
    missing = [k[0].rsplit("/", 1)[-1] for k in want if k not in ws.covers._cache]
    assert not missing, "never landed: %s" % missing

