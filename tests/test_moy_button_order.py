"""The button bit order is libmoy's ABI, and BOTH InputStates must speak it.

This file exists because of a bug that shipped to both boards and was invisible
to everything else in the tree.

moycore hands libmoy one integer per player and `h_btn` reads it as
`(mask >> b) & 1`, where `b` is the `moy_button` enum value. So bit i means
button i of moy.h's enum -- LEFT, RIGHT, UP, DOWN, A, B, RUN -- and nothing
else. On 2026-08-13 the packing was "de-duplicated" to use `InputState.BUTTONS`
as the order. That reads as obviously-correct single-sourcing, and it is not:
there are TWO InputState classes, and their BUTTONS differ in order as well as
in length.

    host   (runtime/input.py)        left right up down a b run home
    boards (modules/moybyte/input.py) up down left right a b x y run ...

The host's first seven happen to be libmoy's, so the host stayed right. The
boards' d-pad rotated a quarter turn -- press UP, the cart goes LEFT -- and
`run` landed on bit 8, which libmoy never looks at. Every Lua cart on both
boards, for a day.

What is worth noticing is how little could have caught it. No exception. No
failing test. The conformance goldens replay recorded verb TRACES, so they never
touch input. A per-cart fps number is unchanged -- the cart is doing the same
work, just the wrong direction. The semantic-trace suite drives scripted input
through the real glue, which is the nearest net, and it was passing: it builds
the HOST InputState, the one tier where the order was right.

So the net has to be this: read the enum out of the C header and check the
Python against it, on both classes, by behaviour.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MOY_H = (ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "native"
         / "moy_gfx" / "libmoy" / "moy.h")


def _enum_from_header():
    """libmoy's moy_button order, parsed from the vendored header.

    Parsed rather than restated: a restatement is a fifth copy of the thing
    this file is about, and it would agree with the code by construction on
    exactly the day upstream changed the enum.
    """
    src = MOY_H.read_text(encoding="utf-8")
    m = re.search(r"typedef enum\s*\{(.*?)\}\s*moy_button\s*;", src, re.S)
    assert m, "moy_button enum not found in %s" % MOY_H
    names = re.findall(r"MOY_BTN_(\w+)", m.group(1))
    names = [n.lower() for n in names if n != "COUNT"]
    assert names, "moy_button parsed empty"
    return tuple(names)


def _device_input_module():
    """The boards' moybyte.input, loaded by path.

    It is not importable as `moybyte.input` from the host tree, and importing
    it is the whole point -- the tier this test exists for is the one the suite
    otherwise only ever greps.
    """
    path = (ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules"
            / "moybyte" / "input.py")
    spec = importlib.util.spec_from_file_location("_dev_input_for_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_moy_buttons_matches_the_vendored_enum():
    from runtime.lua_ext import MOY_BUTTONS
    assert MOY_BUTTONS == _enum_from_header()


@pytest.mark.parametrize("tier", ["host", "device"])
def test_both_input_classes_pack_bits_in_libmoy_order(tier):
    """The behavioural check, on the object each tier actually builds.

    Asserting the two BUTTONS tuples match would be wrong -- they legitimately
    differ (the boards have fifteen buttons, the host eight). What must match is
    the MASK, which is why button_masks takes the order as an argument.
    """
    from runtime.lua_ext import MOY_BUTTONS

    if tier == "host":
        from runtime.input import InputState
        press = lambda inp, n: inp.set_held(n, True)          # noqa: E731
    else:
        InputState = _device_input_module().InputState
        press = lambda inp, n: inp.set_button(n, True)        # noqa: E731

    for i, name in enumerate(MOY_BUTTONS):
        inp = InputState()
        press(inp, name)
        inp.begin_frame()                       # this frame: held AND pressed
        held, pressed = inp.button_masks(MOY_BUTTONS)
        assert held == 1 << i, \
            "%s: %r packed to bit %s, libmoy reads bit %d as %r" % (
                tier, name,
                [b for b in range(32) if held >> b & 1], i, name)
        assert pressed == 1 << i, (tier, name)


@pytest.mark.parametrize("tier", ["host", "device"])
def test_button_masks_requires_an_explicit_order(tier):
    """A caller that forgets the argument must fail LOUDLY.

    The old signature took none and packed in whatever order the class happened
    to hold -- so the failure mode of getting it wrong was silently-wrong bits.
    A TypeError is the entire improvement.
    """
    if tier == "host":
        from runtime.input import InputState
    else:
        InputState = _device_input_module().InputState
    inp = InputState()
    inp.begin_frame()
    with pytest.raises(TypeError):
        inp.button_masks()


def test_every_snapshot_filler_sources_the_order_from_lua_ext():
    """Both fillers must IMPORT the order, not restate it.

    Four hand-written copies of the tuple existed when this broke -- in
    lua_host, in moycore_glue's fast path (via BUTTONS), in its fallback path,
    and in the enum itself -- and the two that were right made the two that
    were wrong impossible to see. Behaviour is checked above; this pins the
    plumbing, because a future copy would pass those tests on the day it was
    written and diverge later, which is precisely what happened.
    """
    for rel in ("runtime/lua_host.py",
                "firmware/lilygo_t_deck_plus_micropython/modules/"
                "moycore_glue.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "MOY_BUTTONS" in src, rel
        assert "button_masks" in src, rel
        assert "(MOY_BUTTONS)" in src, \
            "%s imports the order but never passes it" % rel
        # ...and no local re-listing of the order beside it.
        assert '"left", "right", "up", "down"' not in src, \
            "%s restates the button order instead of importing it" % rel
