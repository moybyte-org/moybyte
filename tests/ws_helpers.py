"""Shared workstation-test helpers (2026-08-18).

Some 27 test files each carried their own copy of one or more of these -- a
2-line `_ws`, a 12-line `_open_cart`, the cursor/toast quiesce -- and the
copies had begun to vary in ways nobody chose (three different `_open_cart`
miss behaviours, one of them a silent no-op on whatever the launcher had
selected). The exact duplicates import ONE body now; a file with a genuine
variant (a clock-pinning quiesce, a frame-first driver) keeps its local
wrapper, ideally calling these.
"""

from runtime import host_app


def build_ws(tmp_path, **kw):
    """A workstation over a fresh seeded store under tmp_path/carts."""
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def build_desktop_ws(tmp_path, **kw):
    """The windowed-desktop tier (the P4 shape) the WM suites drive."""
    kw.setdefault("sys_size", (1024, 600))
    kw.setdefault("font_scale", 2)
    kw.setdefault("windowed", True)
    return host_app.build_workstation(str(tmp_path / "carts"), **kw)


def open_cart(ws, title):
    """Open a seeded cart by TITLE. Games/tools/apps live in the launcher
    run-grid; a WALLPAPER leaves it (spec shell_ux_v1.md) but stays a real
    editable cart in the store, so fall back to opening it by reference (as
    ws.open() does). A missing title is an AssertionError, never a silent
    open of whatever the launcher had selected."""
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    cart = next((c for c in ws._all_carts if c["title"] == title), None)
    assert cart is not None, "seed cart not found: " + title
    ws._open_workspace(cart)
    ws.run(ws.project, ws.launcher_layer)


def make_drv(ws):
    return host_app.ConsoleDriver(ws)


def quiesce(ws):
    """Hide the cursor + clear the achievement toast so a rendered frame is
    deterministic chrome. (Suites that also pin the OS bar's live clock keep
    their own extended copy.)"""
    ws.pointer.visible = False
    ws.ach.toast = None
    ws.ach.toast_until = 0
