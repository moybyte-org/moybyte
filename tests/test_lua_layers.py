"""A Lua cart's layers draw with the full verb set, through libmoy (#225).

SPEC.md 6 gives a layer the whole drawing API. A Lua cart's layer method is the
screen's own libmoy verb run against the layer's canvas
(native/moycore/moycore_layers.h), on the boards and on the host alike, so a
layer answers `L1:rect(...)`, `L1:sspr(...)` and the rest exactly as the screen
does.

What each check is watching for:

  * the VERB SET is libmoy's own LAYER_VERBS, read out of the vendored binding
    rather than restated, so a layer verb added upstream is a red test here
    until the prelude offers it too.
  * the CONFORMANCE CARTS -- every scene of the vendored suite, the five
    `layer_*` ones included -- run as Lua carts through the host's Lua runtime
    and hash to the spec's golden frames. tests/test_spec_conformance.py
    replays the TRACES through the canvas, which is the Python tier's drawing;
    this runs the CARTS, which is the Lua tier's, and is the host's lane of
    tools/p4_conformance.py.
  * a layer's DRAW STATE is its own and outlives the frame, as it does for a
    Python cart's layer and for libmoy's own.
  * the SCREEN gets its canvas back, even when a verb raises inside a layer
    method -- otherwise every later screen draw would land in the layer.
  * a layer libmoy drew into reaches draw_layer MARKED, and the device
    canvas refuses the async copy it predicted from the layer's old pixels.

Skipped without a C compiler, like the other host-lua suites.
"""

import hashlib
import os
import re

import pytest

from runtime import host_canvas
from runtime import lua_binding as lb
from runtime.cart_api import make_api
from runtime.input import InputState
from runtime.lua_ext import LAYER_VERBS, PRELUDE_HANDLES, install_handles

import test_spec_conformance as spec

pytestmark = pytest.mark.skipif(
    not lb.HostLuaRun.available(),
    reason="no C compiler for the host lua binding")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOY_LUA = os.path.join(ROOT, "native", "moycore", "libmoy", "moy_lua.c")


def test_the_layer_verbs_are_libmoys_own():
    with open(MOY_LUA) as fh:
        src = fh.read()
    table = re.search(r"LAYER_VERBS\[\]\s*=\s*\{(.*?)\{NULL", src, re.S)
    assert table, "libmoy's LAYER_VERBS table moved; find it again"
    upstream = re.findall(r'\{"(\w+)"', table.group(1))
    assert sorted(LAYER_VERBS) == sorted(upstream), (
        "a Lua layer must answer exactly what libmoy's layer answers: "
        "lua_ext has %r, libmoy %r" % (sorted(LAYER_VERBS), sorted(upstream)))


# -- the conformance carts, as Lua carts ---------------------------------------

class _Project:
    pass


class _Ws:
    pass


def _lua_frame(scene):
    """Run one conformance cart's main.lua for one frame, the way the host's
    Player does (runtime/lua_host.MoycoreHostRun over make_api), and return
    the index framebuffer."""
    from runtime.lua_host import MoycoreHostRun

    sheet, tilemap, flags = spec._build_assets(scene)
    canvas = host_canvas.make_canvas(spec.W, spec.H)
    inp = InputState()
    ns = make_api(canvas, inp, {}, sheet=sheet, tilemap=tilemap, flags=flags)
    ws = _Ws()
    ws.canvas = canvas
    ws.input = inp
    ws.project = _Project()
    ws.project.sheet = sheet
    ws.project.tilemap = tilemap
    ws.project.flags = flags
    with open(os.path.join(spec.HERE, "carts", scene + ".moy",
                           "main.lua")) as fh:
        src = fh.read()
    run = MoycoreHostRun(ws, ns, src)
    try:
        run.update(1 / 30)
    finally:
        run.close()
    canvas.flush_batch()
    return spec._index_frame(canvas)


@pytest.mark.parametrize("scene,golden",
                         [pytest.param(n, g, id=n)
                          for n, _, g in spec._scene_names()])
def test_the_lua_cart_draws_the_golden_frame_on_the_host(scene, golden):
    got = hashlib.sha256(_lua_frame(scene)).hexdigest()
    assert got == golden, (
        "%s as a Lua cart on the host:\n  golden %s\n  got    %s"
        % (scene, golden, got))


def test_the_five_layer_scenes_are_in_the_suite():
    names = {n for n, _, _ in spec._scene_names()}
    layer = {n for n in names if n.startswith("layer_")}
    assert layer == {"layer_top", "layer_bottom", "layer_left", "layer_right",
                     "layer_small"}, layer


# -- the mechanism, at the binding ----------------------------------------------

class _Run:
    """A bare host run with the prelude over a real make_api namespace."""

    def __init__(self, w=64, h=48):
        self.canvas = host_canvas.make_canvas(w, h)
        self.ns = make_api(self.canvas, InputState(), {})
        self.run = lb.HostLuaRun(self.canvas._buf, w, h,
                                 wire=self.canvas._wire)
        self.layers, _ = install_handles(self.ns, self.run.register,
                                         self.run.layer_bind)
        assert self.run.exec(PRELUDE_HANDLES, "prelude") is None

    def lua(self, src):
        err = self.run.exec(src, "@probe")
        assert err is None, err

    def word(self, buf, w, x, y):
        i = 2 * (y * w + x)
        return buf[i] | (buf[i + 1] << 8)

    def colour(self, c):
        """The word colour `c` is, read off the screen after a cls(c)."""
        self.lua("cls(%d)" % c)
        return self.word(self.canvas._buf, self.canvas.w, 0, 0)

    def close(self):
        self.run.close()


def test_a_layers_draw_state_is_its_own_and_outlives_the_frame():
    r = _Run()
    try:
        r.lua("L = make_layer(32, 16)\n"
              "L:cls(1)\n"
              "L:camera(4, 2)\n"
              "L:pal(8, 9)\n")
        nine, eight = r.colour(9), r.colour(8)
        # A later chunk (a later frame): the layer's camera and pal still hold,
        # and the screen's are untouched by them.
        r.lua("cls(0)\n"
              "L:rect(4, 2, 1, 1, 8)\n"
              "rect(0, 0, 1, 1, 8)\n"
              "CX, CY = L:camera()\n")
        lay = r.layers[0]._canvas
        assert r.word(lay._buf, 32, 0, 0) == nine, "the layer lost its camera or pal"
        assert r.word(r.canvas._buf, 64, 0, 0) == eight, "the screen took the layer's state"
        assert (r.run.get_global("CX"), r.run.get_global("CY")) == (4, 2)
    finally:
        r.close()


def test_every_layer_verb_draws_into_the_layer_not_the_screen():
    r = _Run()
    try:
        r.lua("cls(0)\n"
              "L = make_layer(64, 48)\n"
              "L:cls(1)\n"
              "L:pix(1, 1, 2) L:line(0, 5, 20, 5, 3) L:rect(2, 8, 4, 4, 4)\n"
              "L:rectb(8, 8, 4, 4, 5) L:circ(20, 20, 3, 6) L:circb(30, 20, 3, 7)\n"
              "L:oval(40, 20, 4, 2, 8) L:ovalb(50, 20, 4, 2, 9)\n"
              "L:tri(0, 30, 6, 30, 3, 36, 10) L:trib(10, 30, 16, 30, 13, 36, 11)\n"
              "L:print('HI', 20, 30, 12)\n"
              "L:fillp(0x5a5a, 13) L:rect(40, 30, 8, 8, 14) L:fillp()\n"
              "L:spr(1, 50, 0) L:sspr(0, 0, 8, 8, 56, 0, 4, 4)\n"
              "L:map(0, 0, 1, 1, 0, 40) L:tline(0, 47, 10, 47, 0, 0)\n"
              "L:clip(0, 0, 4, 4) L:palt(0, false) L:clip()\n"
              "P = L:pix(1, 1)\n")
        lay = r.layers[0]._canvas
        screen = bytes(r.canvas._buf)
        black = r.colour(0)
        assert all(r.word(screen, 64, x, y) == black
                   for y in range(48) for x in range(64)), \
            "a layer verb drew on the screen"
        assert r.run.get_global("P") == 2, "pix read the wrong surface"
        words = {r.word(lay._buf, 64, x, y) for y in range(48) for x in range(64)}
        assert len(words) >= 10, "the layer verbs did not draw: %r" % words
    finally:
        r.close()


def test_a_method_called_without_its_layer_says_how_to_call_it():
    r = _Run()
    try:
        r.lua("L = make_layer(8, 8)")
        err = r.run.exec("L.rect(1, 2, 3, 4, 5)", "@probe")
        assert err and "colon" in err, err
    finally:
        r.close()


def test_a_raising_verb_gives_the_screen_its_canvas_back():
    """The two natives are the runtime's; reached here BEFORE the prelude has
    captured and cleared them, on a bare run, so a verb that raises can be
    handed to one."""
    canvas = host_canvas.make_canvas(16, 8)
    run = lb.HostLuaRun(canvas._buf, 16, 8, wire=canvas._wire)
    try:
        lay = bytearray(16 * 8 * 2)
        run.layer_bind(lay, 16, 8)
        err = run.exec(
            "local c = __layer_canvas()\n"
            "local boom = __layer_verb(function() rect(0, 0, 16, 8, 3)\n"
            "                                     error('boom') end)\n"
            "OK, MSG = pcall(boom, { __c = c })\n"
            "cls(0) rect(0, 0, 16, 8, 3)\n", "@probe")
        assert err is None, err
        assert run.get_global("OK") is None or run.get_global("OK") == 0
        assert any(canvas._buf), "the screen's rect landed somewhere else"
        assert any(lay), "the verb did not run against the layer"
        # Nothing parked: a second canvas without a bind is an error, not a
        # canvas over the last layer's pixels.
        err = run.exec("__layer_canvas()", "@probe")
        assert err and "no layer buffer" in err, err
    finally:
        run.close()


def test_a_layer_drawn_by_libmoy_refuses_a_predicted_copy():
    """The layer restore (#54 Stage 2, the kernels' mg_lr_*) kicks a copy of
    the layer at the screen canvas's sync_back and the native draw_layer takes
    it unless the layer changed in between. A layer verb marks the layer
    (`__e`), so a layer libmoy drew into since is copied again."""
    from runtime import gfx_binding

    r = _Run(64, 48)
    screen = r.canvas
    gfx_binding.layer_engine(True)          # the copy lands as it starts
    try:
        gfx_binding.layer_init(screen._lrs, True)
        r.run.layer_restore(screen._lrs)
        screen._lrs_run = r
        # Drawn once and composited once, which consumes its mark and arms
        # the prediction.
        r.lua("L = make_layer(64, 48)\nL:cls(5)\ndraw_layer(L, 0, 0)\n")
        lay = r.layers[0]._canvas
        # Unmarked: the kick paints the layer, and draw_layer takes it, so a
        # screen poisoned after the kick stays poisoned.
        screen.sync_back()
        r.lua("rect(0, 0, 64, 1, 9)")
        r.lua("draw_layer(L, 0, 0)")
        assert r.word(screen._buf, 64, 0, 0) != r.word(lay._buf, 64, 0, 0), \
            "control: the predicted copy was not taken"
        # Drawn by libmoy since the kick: marked, so the sync copy runs.
        screen.sync_back()
        r.lua("L:rect(0, 0, 64, 48, 6)")
        r.lua("draw_layer(L, 0, 0)")
        assert bytes(screen._buf) == bytes(lay._buf), \
            "the copy predicted from the layer's old pixels was taken"
    finally:
        gfx_binding.layer_engine(False)
        screen._lrs_run = None
        r.close()
