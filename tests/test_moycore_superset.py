"""The superset names a Lua run answers in C (native/moycore/moycore_superset.h).

col and mouse are moybyte's, outside libmoy's verb table, and a Lua run used to
reach them through a trampoline into the cart's Python namespace: a crossing
on every frame that asked for a colour by name. They are C now, on the boards
and the host alike, and these pin the C to the Python bodies they replaced
(runtime/palette.py's color, cart_api's mouse) value for value.
"""

import pytest

from runtime import host_canvas
from runtime import lua_binding as lb
from runtime.palette import color

pytestmark = pytest.mark.skipif(not lb.HostLuaRun.available(),
                                reason="no host Lua binding")


@pytest.fixture
def run():
    canvas = host_canvas.make_canvas(64, 48)
    r = lb.HostLuaRun(canvas._buf, 64, 48, wire=canvas._wire)
    yield r
    r.close()


def _ask(run, expr):
    err = run.exec("R = %s" % expr, "@probe")
    assert err is None, err
    return run.get_global("R")


@pytest.mark.parametrize("arg,py", [
    ('"red"', "red"), ('"peach"', "peach"), ('"black"', "black"),
    ('"no_such_colour"', "no_such_colour"), ("12", 12), ("70", 70), ("-1", -1),
    ("3.9", 3.9), ("-3.9", -3.9), ("true", True), ("false", False),
])
def test_col_answers_what_the_python_tier_answers(run, arg, py):
    assert _ask(run, "col(%s)" % arg) == color(py)


def test_col_of_nothing_is_an_error_as_the_trampoline_raised(run):
    err = run.exec("col(nil)", "@probe")
    assert err and "col" in err


def test_mouse_reads_the_pointer_from_the_snapshot(run):
    err = run.exec("function probe() X, Y, L, M, Rt, SX, SY = mouse() end", "@probe")
    assert err is None, err

    def ask():
        assert run.exec("probe()", "@probe") is None
        return tuple(run.get_global(n) for n in ("X", "Y", "SX", "SY")), \
            [run.exec("assert(%s == %s)" % (n, v), "@probe") is None
             for n, v in (("L", "true"), ("M", "false"), ("Rt", "false"))]

    # No pointer: zeros and false, as cart_api.mouse answers.
    (xy, _) = ask()
    assert xy == (0, 0, 0, 0)
    assert run.exec("assert(L == false)", "@probe") is None
    # A press this frame: its place, and `left` is the edge.
    run.snap[lb.SNAP_TOUCH_X], run.snap[lb.SNAP_TOUCH_Y] = 21, 34
    run.snap[lb.SNAP_TOUCH_DOWN] = 1 | 2 | 4
    (xy, flags) = ask()
    assert xy == (21, 34, 0, 0)
    assert flags == [True, True, True]
    # Held past the edge: still there, no longer a click.
    run.snap[lb.SNAP_TOUCH_DOWN] = 1 | 2
    ask()
    assert run.exec("assert(L == false and X == 21)", "@probe") is None
