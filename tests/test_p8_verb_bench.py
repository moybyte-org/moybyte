"""The p8 verb bench (`tools/p8_verb_bench.py`): its carts really are carts.

The bench itself needs a board, so what is checked here is the half that can
rot without anyone noticing -- the generated p8 source. Every row of OPS is an
expression this repo's own porter has to accept and the real Player has to
run; a row that stops porting, or that Lua constant-folds away, would produce
a clean-looking zero on the board instead of an error.

Written after 2026-09-10, when six variants of an earlier throwaway version of
this bench all read 5.18ms -- one number, six carts, no variance -- because
the launcher had not rescanned and every arm was the previous cart. A bench
that cannot fail loudly is worse than no bench."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import p8_verb_bench as B                                       # noqa: E402


def test_the_control_is_first_and_every_row_is_distinct():
    """Every op is quoted NET of the control, so the control must be the row
    the runner meets first, and no slug may repeat (they become cart titles)."""
    assert B.OPS[0][0] == B.CONTROL
    slugs = [s for s, _e, _w, _p in B.OPS]
    assert len(slugs) == len(set(slugs))
    assert all(w for _s, _e, w, _p in B.OPS), "a row says what it prices"


def test_every_row_ports_and_runs_on_the_real_player(tmp_path):
    """The porter accepts each expression and the Player runs it. This is the
    check that a new OPS row is a cart and not a typo.

    ONE cart carrying every row, not one cart per row: the rows are cheap and
    a workstation is not, and twelve of them under `make test`'s xdist was
    enough load to trip timing-sensitive tests in other workers (caught
    2026-09-10 -- the suite passed serially and failed two different tests on
    alternate parallel runs). A test that costs its neighbours their result is
    a broken test however green it is."""
    from test_import_p8 import _need_lua, _run_p8
    _need_lua()
    # Each row in its own function, so a Lua error names the row rather than a
    # line number in a generated file.
    body = "".join(p for _s, _e, _w, p in B.OPS)
    body += "".join("function op_%s() local a=0 for i=1,2 do %s end end\n"
                    % (slug, expr) for slug, expr, _why, _p in B.OPS)
    body += ("function _update()\n"
             + "".join(" op_%s()\n" % slug for slug, _e, _w, _p in B.OPS)
             + "end\nfunction _draw() cls(0) end\n")
    ws = _run_p8(tmp_path, body, frames=2, dt=1.0 / 30)
    assert ws.player.cart_error is None, (
        "a row of OPS does not run: %s\n  rows: %s"
        % (ws.player.cart_error, ", ".join(s for s, _e, _w, _p in B.OPS)))


def test_a_row_that_does_no_work_would_be_caught():
    """The control is the only row allowed to be free. Every other expression
    has to reach a verb or an operator -- an expression Lua folds at compile
    time would read as a verb that costs nothing at all."""
    for slug, expr, _why, _p in B.OPS[1:]:
        rhs = expr.split("=", 1)[1]
        assert ("(" in rhs or any(op in rhs for op in ("&", ">>", "<<", "+", "|", "~"))), \
            "%s: %r has nothing to price" % (slug, rhs)


def test_per_call_arithmetic_is_net_of_the_control():
    assert B.per_call_ns([3.0, 3.0, 3.0], 1.0, calls=1000) == pytest.approx(2000.0)
    assert B.per_call_ns([1.0], 1.0, calls=1000) == 0.0


def test_build_carts_writes_a_playable_cart_per_row(tmp_path):
    """The porter is the VENDORED one, so these are the carts this tree emits
    -- benching a cart some other porter wrote would price the wrong code."""
    made = B.build_carts(str(tmp_path), ops=B.OPS[:2])
    assert [s for s, _t, _c in made] == [B.CONTROL, B.OPS[1][0]]
    for _slug, title, cart in made:
        main = Path(cart) / "main.lua"
        assert main.exists(), cart
        assert (Path(cart) / "manifest.json").exists()
        assert title.startswith("P8Bench ")
        # The shim is p8.lua's now (SPEC.md 4); main.lua is the cart.
        assert "end shim =" in (Path(cart) / "p8.lua").read_text(
            encoding="utf-8")
