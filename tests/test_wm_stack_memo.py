"""Stage 6c guardrail (docs/shell_ux_technical_plan_v1.md Section 5): the WM's
visible/draw/overlay layer stack is MEMOIZED -- rebuilt only on a real change (a
back-stack push/pop, a menu_view tab switch, or an overlay-gate/splash flip), so a
static top-of-stack allocates NO new per-frame list.

This is the #66 perf-recovery lever: before Stage 6c, frame()/handle_input()/
handle_pointer() each rebuilt `[content] + overlays` from scratch -- ~9 fresh lists per
frame, EVEN DURING PLAY. The golden set can't see an allocation win (same pixels), so
this test is the witness: it drives the real shared console (host == device) and proves
the stack builder (`FullscreenStackWM._rebuild`) is not reached on repeat static frames
and the accessors hand back the SAME list objects, and that a genuine change (nav /
overlay gate / tab switch) DOES invalidate the memo.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import host_app  # noqa: E402

DT = 1.0 / 30


def _running_cart(tmp_path):
    """A cart PLAYING on the desktop (screen == "desktop"), settled so nothing transient
    animates the OVERLAY set (the cart itself animates -- that's the perf-critical path
    whose stack must stay memoized): splash cleared, the open-cart celebration toast
    cleared."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    assert ws.launcher.items, "system carts should be seeded"
    ws.launcher.sel = 0
    ws.open()                      # PLAY the first seed cart -> screen "desktop"
    ws._splash_until = None
    ws.ach.toast = None
    for _ in range(3):
        drv.frame(DT)
    ws.ach.toast = None            # drop any "First Steps" toast so the overlays are static
    for _ in range(2):
        drv.frame(DT)
    return ws, drv


def _count_rebuilds(ws):
    """Wrap FullscreenStackWM._rebuild so the test can assert how many times the stack
    was actually (re)built. Returns a 1-element list the wrapper increments."""
    calls = [0]
    orig = ws.wm._rebuild

    def counting(content, sig):
        calls[0] += 1
        return orig(content, sig)

    ws.wm._rebuild = counting
    return calls


# -- the core win: a static top-of-stack rebuilds NOTHING ---------------------

def test_static_play_stack_is_memoized_zero_rebuilds(tmp_path):
    """A running cart in steady state: across many frames the WM returns the SAME
    visible/draw/reversed/overlay list objects and never rebuilds them."""
    ws, drv = _running_cart(tmp_path)
    assert ws.screen == "desktop"
    a_vis = ws.wm.visible_stack()
    a_draw = ws.wm.draw_stack()
    a_rev = ws.wm.visible_stack_rev()
    a_ovl = ws.wm.overlay_stack()
    # The content slot IS the active content layer (spec alias), so the memo is faithful.
    assert a_vis[0] is ws._content_layer()
    # Reversed is genuinely top -> bottom (cursor first, content last).
    assert a_rev[0] is a_vis[-1] and a_rev[-1] is a_vis[0]

    calls = _count_rebuilds(ws)
    for _ in range(30):
        drv.frame(DT)                       # the real hot path: handle_input+pointer+frame
        assert ws.wm.visible_stack() is a_vis
        assert ws.wm.draw_stack() is a_draw
        assert ws.wm.visible_stack_rev() is a_rev
        assert ws.wm.overlay_stack() is a_ovl
    assert calls[0] == 0, "a static top-of-stack must not rebuild the layer stack"


def test_static_launcher_stack_is_memoized(tmp_path):
    """The static launcher home (a solid-fill wallpaper so nothing animates) is likewise
    memoized -- zero rebuilds across frames."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    ws.select_wallpaper("fill:dark_blue", persist=False)
    ws._splash_until = None
    ws.ach.toast = None
    for _ in range(3):
        drv.frame(DT)
    ws.ach.toast = None
    a = ws.wm.draw_stack()
    calls = _count_rebuilds(ws)
    for _ in range(20):
        drv.frame(DT)
        assert ws.wm.draw_stack() is a
    assert calls[0] == 0


# -- and a REAL change DOES invalidate the memo -------------------------------

def test_backstack_push_pop_invalidates_the_memo(tmp_path):
    """A navigation (a back-stack push/pop) rebuilds the stack: the returned object is a
    fresh one and the content slot follows the new top-of-stack."""
    ws, drv = _running_cart(tmp_path)
    a = ws.wm.visible_stack()
    assert a is ws.wm.visible_stack()          # stable until a change
    ws.go_home()                               # pop the Player -> launcher root
    b = ws.wm.visible_stack()
    assert b is not a
    assert ws.screen == "launcher"
    assert b[0] is ws._content_layer()         # content slot tracked the new top


def test_overlay_gate_change_invalidates_the_memo(tmp_path):
    """An overlay gate flipping ON (the system menu opening) rebuilds the stack and adds
    the overlay -- the memo signature caught it without any explicit nav."""
    ws, drv = _running_cart(tmp_path)
    ws.go_home()
    for _ in range(2):
        drv.frame(DT)
    a = ws.wm.draw_stack()
    assert ws.wm.draw_stack() is a
    ws.toggle_sysmenu()                        # opens the ≡ dropdown overlay
    b = ws.wm.draw_stack()
    assert b is not a
    assert ws._sysmenu_layer in b and ws._sysmenu_layer not in a


def test_editor_tab_switch_invalidates_the_memo(tmp_path):
    """Switching Editor tabs keeps screen == "menu" but resolves _content_layer to a
    different tab, so the memo must rebuild (EditorApp.tab bumps content_gen)."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert ws.launcher.items
    ws.launcher.sel = 0
    ws.open_in_editor()
    ws.set_menu_view("cards")
    a = ws.wm.visible_stack()
    assert ws.wm.visible_stack() is a
    ws.set_menu_view("code")
    b = ws.wm.visible_stack()
    assert b is not a
    assert b[0] is ws.code_layer               # the Code tab is now the content slot


def test_splash_expiry_invalidates_the_memo(tmp_path):
    """The boot splash occupies the DRAW content slot (input still routes to the content
    underneath). Arming/clearing it flips the signature, so the draw stack rebuilds."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    ws._splash_until = None
    for _ in range(2):
        drv.frame(DT)
    plain = ws.wm.draw_stack()
    assert plain[0] is ws._content_layer()     # no splash -> real content in the draw slot
    import runtime.console as C
    ws._splash_until = C._ticks_ms() + 10 ** 6  # arm the splash
    armed = ws.wm.draw_stack()
    assert armed is not plain
    assert armed[0] is ws._splash_layer         # splash takes the draw content slot
    # ...but input still routes to the content underneath (visible stack keeps content).
    assert ws.wm.visible_stack()[0] is ws._content_layer()
