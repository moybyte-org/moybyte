"""A `uitree` app inside today's shell: it opens, paints, takes input and
closes on every size the shell goldens draw, beside the apps on today's
verbs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "uitree_fixtures"))

import uitree_parity as P  # noqa: E402
import test_shell_goldens as G  # noqa: E402

# one configuration per size, scale and tier the goldens draw
SIZES = sorted({(c["sys_size"], c["font_scale"], c["windowed"]): name
                for name, c in sorted(G.CONFIGS.items(), reverse=True)}.values())


@pytest.mark.parametrize("config", SIZES)
def test_a_uitree_app_opens_paints_takes_input_and_closes(config, tmp_path):
    import uitree_counter as C
    cls = C.layer()
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)
        seen["ws"] = ws

    # Add one twice, Reset (now on) asks, Back keeps; then Details is pushed
    # and Back pops it
    script = ["k:a", "k:a", "k:down", "k:a", "back", "k:down", "k:down", "k:down", "k:a", "back"]
    fs = P.frames(config, opened, script, str(tmp_path / "carts"))
    eng = seen["app"].engine
    trace = eng.trace_text()
    assert C.s.count == 2 and not C.s.asking, trace
    assert "action  add" in trace and "action  reset_ask" in trace and "action  keep" in trace, trace
    assert "action  more" in trace and "page    back" in trace, trace
    assert len(eng.pages) == 1
    for i in (1, 2, 4, 5, 9, 10):               # each of these steps drew a new frame
        assert fs[i].buf != fs[i - 1].buf, (i, fs[i].step, trace)


def test_a_uitree_app_closes_and_reopens_on_its_opened_hook(tmp_path):
    import uitree_counter as C
    cls = C.layer()
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)

    P.frames("tdeck_320x240_fs1_dark", opened, ["k:a"], str(tmp_path / "carts"))
    assert C.s.count == 1
    app = seen["app"]
    app.open()
    assert C.s.count == 0 and len(app.engine.pages) == 1
