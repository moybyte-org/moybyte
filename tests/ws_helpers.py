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
    cart = next((c for c in ws.carts.all if c["title"] == title), None)
    assert cart is not None, "seed cart not found: " + title
    ws._open_workspace(cart)
    ws.run(ws.project, ws.launcher_layer)


def make_drv(ws):
    return host_app.ConsoleDriver(ws)


def quiesce(ws):
    """Hide the cursor + take down the achievement toast so a rendered frame is
    deterministic chrome. (Suites that also pin the OS bar's live clock keep
    their own extended copy.)

    The DEADLINE is what takes an overlay down (#209 landing B) -- the shell's
    own flat field, not the payload on `ws.ach`, which is read only while that
    deadline is up."""
    ws.pointer.visible = False
    ws._toast_until = 0


def build_ws_with_cart(tmp_path, src, title="Cart", type="app", edit=None,
                       editor=False):
    """A workstation over a store holding ONE hand-authored cart, opened.

    `editor=True` lands in the Editor (`open_in_editor`) instead of RUNNING the
    cart. A title the launcher does not carry is an AssertionError, like
    `open_cart`: a select loop that falls through in silence opens whatever the
    launcher had selected, which reads as a passing test against a seed cart."""
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(carts_dir)
    host_app.moy_carts.create(title, carts_dir, src=src, type=type,
                              edit=edit or [])
    ws = host_app.build_workstation(carts_dir)
    sel = next((i for i, c in enumerate(ws.launcher.items)
                if c["title"] == title), None)
    assert sel is not None, "created cart not on the launcher: " + title
    ws.launcher.sel = sel
    if editor:
        ws.open_in_editor()
    else:
        ws.open()
    return ws


def build_ws_with_shelf(tmp_path, n):
    """A workstation whose launcher holds at least `n` carts -- more than one
    viewport, which is the precondition every shelf-scrolling suite needs --
    topped up through the real store, parked at the head."""
    from runtime import host_app, moy_carts

    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)        # seeds the system carts
    while len(ws.launcher.items) < n:                 # top up with extra carts
        i = len(ws.launcher.items)
        moy_carts.create("Extra %02d" % i, carts_dir,
                         src="def _draw():\n    cls(1)\n", type="app")
        ws.launcher.items = moy_carts.scan(carts_dir)
    ws.launcher.sel = 0
    ws.launcher.scroll = 0
    return ws


def device_frames(ws, n=1, dt=1 / 30):
    """`n` whole frames in the DEVICE lane: the boards' InputState needs
    `handle_input`/`handle_pointer` driven by hand, where the host driver does
    it for you (`game_pointer`, the tuple a cart's `touch()` reads, is only
    refreshed by `handle_pointer`)."""
    for _ in range(n):
        ws.input.begin_frame()
        ws.handle_input()
        ws.handle_pointer()
        ws.frame(dt)


class StubInput:
    """The cart-facing input with nothing held and nothing pressed -- what
    `make_api` wants from a test with no opinion about buttons."""

    def held(self, n):
        return False

    def pressed(self, n):
        return False
