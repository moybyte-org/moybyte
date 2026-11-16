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

The store's load retries the source once after a collect (modmoy_store.c's
`big_str`), because the block usually exists and just has not been reclaimed.
What is pinned here is the console's half: when the cart still will not load,
the workspace refuses BEFORE `ws.cart` is assigned: the kid gets the error
panel a crashing cart gets, and the launcher stays usable.
"""

import shutil
import sys

from runtime import moy_carts

from ws_helpers import build_ws as _ws, open_cart
from ws_helpers import shelf  # noqa: E402


GOOD_SRC = "def _draw():\n    cls(1)\n"


def _mk(ws, title):
    """Create a cart in the live store and re-adopt the roster (which re-slims
    it, so opening it is a real rehydrate -- the path that fails)."""
    moy_carts.create(title, str(ws.carts_root), src=GOOD_SRC)
    ws.carts.apply(shelf(str(ws.carts_root)))
    cart = next(c for c in ws.carts.all if c["title"] == title)
    assert cart.get("lazy") is True, "the cart must be slim for this to test anything"
    return cart


def _deny_source(monkeypatch, cart, times):
    """Make this cart's load come back empty the next `times` loads -- what
    the store answers when the source will not read."""
    store = sys.modules["moy_store"]
    real = store.load
    left = [times]

    def fake(path):
        if path == cart["path"] and left[0] > 0:
            left[0] -= 1
            return None
        return real(path)

    monkeypatch.setattr(store, "load", fake)
    return left


def _select(ws, cart):
    if not ws.home.select_cart(cart):
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
    assert ws.run.player.cart_error is not None
    assert "Couldn't open" in ws.run.player.cart_error, ws.run.player.cart_error
    assert "Mossmoss" in ws.run.player.cart_error, ws.run.player.cart_error
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
    assert ws.run.project.cart is None
    assert ws.editor_app.project is not ws.run.project
    assert "Couldn't open" in (ws.run.player.cart_error or ""), ws.run.player.cart_error
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
    ws.home.go_home()
    assert ws.screen == "launcher"
    assert ws.run.player.cart_error is None
    _select(ws, good)
    ws.open()
    assert ws.cart is good
    assert ws.run.player.cart_error is None, ws.run.player.cart_error


def test_a_refused_open_leaves_no_open_cart_behind(tmp_path, monkeypatch):
    """The cart that WAS open is closed, not half-replaced: a workspace that
    refuses must not leave the previous run's world alive either (#66)."""
    ws = _ws(tmp_path)
    bad = _mk(ws, "Mossmoss")
    open_cart(ws, "Star Catcher")
    assert ws.run.player.ns is not None
    _deny_source(monkeypatch, bad, 99)
    ws.open_in_editor(bad)
    assert ws.cart is None
    assert ws.run.player.ns is None, "the dead run's world outlived the refusal"
    assert ws.run.fat_cart is None


def test_a_cart_with_no_folder_at_all_is_refused(tmp_path):
    """The other way a rehydrate comes back empty -- the card was pulled."""
    ws = _ws(tmp_path)
    cart = _mk(ws, "Mossmoss")
    shutil.rmtree(cart["path"])         # its handle still names the folder
    ws.open_in_editor(cart)
    assert ws.cart is None
    assert "Couldn't open" in (ws.run.player.cart_error or "")
