"""A cart whose SOURCE will not read must fail closed at the boundary.

Seen on the T-Deck (2026-09-05), opening a 77KB cart against a heap reporting
`free=3039k big=75k` -- 3 MB free, no block big enough for the source:

    Moybyte cart unreadable: /sd/.../mossmoss-17.moy memory allocation failed
      File "console.py", line 1, in open_in_editor
      ...
    KeyError: src

`load()` swallowed the MemoryError and returned None, `rehydrate()` left the
cart dict SLIM, and the workspace opened anyway -- so the failure surfaced two
frames later inside `Player.start`, as an uncaught KeyError, with `ws.cart`
pointing at the half-loaded dict and a later `go_home()` raising in turn.

Two behaviours are pinned here. The read RETRIES once after a collect, because
the block usually exists and just has not been reclaimed (the previous cart's
payloads are dropped a few statements earlier, by `CartManager.reslim`, with
nothing collecting in between). And when it still will not read, the workspace
refuses BEFORE `ws.cart` is assigned: the kid gets the error panel a crashing
cart gets, and the launcher stays usable.
"""

from runtime import moy_carts

from ws_helpers import build_ws as _ws, open_cart


GOOD_SRC = "def _draw():\n    cls(1)\n"


def _mk(ws, title):
    """Create a cart in the live store and re-adopt the roster (which re-slims
    it, so opening it is a real rehydrate -- the path that fails)."""
    moy_carts.create(title, str(ws.carts_root), src=GOOD_SRC)
    ws.carts.apply(moy_carts.scan(str(ws.carts_root)))
    cart = next(c for c in ws.carts.all if c["title"] == title)
    assert cart.get("lazy") is True, "the cart must be slim for this to test anything"
    return cart


def _deny_source(monkeypatch, cart, times):
    """Make this cart's main.py read raise MemoryError the next `times` reads --
    the device's own failure, not a missing file."""
    real = moy_carts._read_recover
    target = cart["path"] + "/main.py"
    left = [times]

    def fake(path):
        if path == target and left[0] > 0:
            left[0] -= 1
            raise MemoryError("memory allocation failed, allocating 77824 bytes")
        return real(path)

    monkeypatch.setattr(moy_carts, "_read_recover", fake)
    return left


def _select(ws, cart):
    for i, it in enumerate(ws.launcher.items):
        if it.get("path") == cart.get("path"):
            ws.launcher.sel = i
            return
    raise AssertionError("cart not on the shelf: " + cart["title"])


def test_run_refuses_a_cart_whose_source_will_not_read(tmp_path, monkeypatch):
    """RUN lands on the error panel, not on a KeyError two frames later."""
    ws = _ws(tmp_path)
    cart = _mk(ws, "Mossmoss")
    _deny_source(monkeypatch, cart, 99)
    _select(ws, cart)
    ws.open()
    assert ws.cart is None, "ws.cart was left on a dict with no source"
    assert "src" not in cart
    assert ws.player.cart_error is not None
    assert "Couldn't open" in ws.player.cart_error, ws.player.cart_error
    assert "Mossmoss" in ws.player.cart_error, ws.player.cart_error
    assert ws.screen == "desktop", "the kid was stranded, not shown the panel"
    ws.pointer.visible = False
    ws.frame(0.016)                     # the panel paints over a cart-less desktop


def test_open_in_editor_refuses_a_cart_whose_source_will_not_read(tmp_path,
                                                                  monkeypatch):
    """EDIT is the path the T-Deck traceback came from. Opening the Editor over
    an empty project would be a dead end, so it refuses the same way."""
    ws = _ws(tmp_path)
    cart = _mk(ws, "Mossmoss")
    _deny_source(monkeypatch, cart, 99)
    ws.open_in_editor(cart)
    assert ws.cart is None
    assert ws.project.cart is None
    assert ws.editor_app.project is not ws.project
    assert "Couldn't open" in (ws.player.cart_error or ""), ws.player.cart_error
    assert ws.screen == "desktop"


def test_the_launcher_survives_a_refused_cart(tmp_path, monkeypatch):
    """The failure must not take the console down with it: HOME works, the
    error clears, and the next cart opens normally."""
    ws = _ws(tmp_path)
    cart = _mk(ws, "Mossmoss")
    good = _mk(ws, "Fine")
    _deny_source(monkeypatch, cart, 99)
    _select(ws, cart)
    ws.open()
    ws.go_home()
    assert ws.screen == "launcher"
    assert ws.player.cart_error is None
    _select(ws, good)
    ws.open()
    assert ws.cart is good
    assert ws.player.cart_error is None, ws.player.cart_error


def test_a_refused_open_leaves_no_open_cart_behind(tmp_path, monkeypatch):
    """The cart that WAS open is closed, not half-replaced: a workspace that
    refuses must not leave the previous run's world alive either (#66)."""
    ws = _ws(tmp_path)
    bad = _mk(ws, "Mossmoss")
    open_cart(ws, "Star Catcher")
    assert ws.player.ns is not None
    _deny_source(monkeypatch, bad, 99)
    ws.open_in_editor(bad)
    assert ws.cart is None
    assert ws.player.ns is None, "the dead run's world outlived the refusal"
    assert ws._fat_cart is None


def test_the_source_read_retries_once_after_a_collect(tmp_path, monkeypatch):
    """The common case: the block exists, it just had not been reclaimed. One
    collect and the same read succeeds -- so the kid never sees the panel."""
    ws = _ws(tmp_path)
    cart = _mk(ws, "Mossmoss")
    left = _deny_source(monkeypatch, cart, 1)
    _select(ws, cart)
    ws.open()
    assert left[0] == 0, "the read was never retried"
    assert ws.cart is cart
    assert cart["src"] == GOOD_SRC
    assert ws.player.cart_error is None, ws.player.cart_error


def test_the_retry_is_once_not_a_loop(tmp_path, monkeypatch):
    """A heap that is genuinely out cannot be waited out; two failures is the
    answer, not a retry storm."""
    ws = _ws(tmp_path)
    cart = _mk(ws, "Mossmoss")
    left = _deny_source(monkeypatch, cart, 2)
    assert moy_carts.load(cart["path"]) is None
    assert left[0] == 0


def test_a_cart_with_no_folder_at_all_is_refused(tmp_path):
    """The other way a rehydrate comes back empty -- the card was pulled."""
    ws = _ws(tmp_path)
    cart = _mk(ws, "Mossmoss")
    cart["path"] = str(tmp_path / "gone.moy")
    ws.open_in_editor(cart)
    assert ws.cart is None
    assert "Couldn't open" in (ws.player.cart_error or "")
