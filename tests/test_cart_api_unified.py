"""The cart API is ONE function (runtime/cart_api.py, 2026-08-17).

make_api existed twice for a year -- runtime/host_api.py and
device/device_api.py, ~80% line-identical -- and the copies drifted in both
directions (the device's _Layer lost `tline`; the host's multi-tile spr lost
the #63 span cache). These tests pin the unification the strongest way
available: the three names are the SAME OBJECT, so a future edit cannot
re-fork them without failing here first. The behavioural nets are elsewhere
(golden frames, the sakura/brick parity suites, test_semantic_traces on real
MicroPython, both boards' on-glass suites); this file pins the STRUCTURE.
"""

from runtime import cart_api
from runtime import host_api
from device import device_api


def test_one_make_api_object():
    assert host_api.make_api is cart_api.make_api
    assert device_api.make_api is cart_api.make_api


def test_one_button_tuple_and_layer_class():
    assert host_api.CART_BUTTONS is cart_api.CART_BUTTONS
    assert device_api.CART_BUTTONS is cart_api.CART_BUTTONS
    assert host_api._Layer is cart_api._Layer
    assert device_api._Layer is cart_api._Layer
    assert host_api._decode_moyimg is cart_api._decode_moyimg
    assert device_api._decode_moyimg is cart_api._decode_moyimg


def test_layer_verbs_carry_tline():
    """The drift the merge found: the device's _Layer verb list was missing
    `tline`, so a layer's textured line worked on the host and raised
    AttributeError on a board. The unified list is the superset; if a verb
    leaves this tuple it should be by decision, not by a re-fork."""
    assert "tline" in cart_api._Layer._VERBS


def test_host_app_reexports_survive():
    """host_app re-exports every host_api name for the sim/web/tests -- the
    unification must not have moved any import site."""
    from runtime import host_app
    assert host_app.make_api is cart_api.make_api
    assert host_app._Layer is cart_api._Layer
    assert host_app._decode_moyimg is cart_api._decode_moyimg


def test_the_base_namespace_keyset_is_pinned():
    """The frozen kid API's base key-set -- the cart vocabulary contract
    (docs/moy_cart_api.md). A key appearing or vanishing here is a PUBLIC API
    change on all four tiers at once and should be a loud, deliberate diff."""

    class _P:
        x = y = 0
        click = down = False

    class _In:
        pointer = _P()

        def held(self, name):
            return False

        def pressed(self, name):
            return False

    class _Canvas:
        w, h = 320, 240

        def __getattr__(self, name):
            return lambda *a, **k: None

    ns = cart_api.make_api(_Canvas(), _In(), {})
    assert set(ns) == {
        "W", "H", "cls", "pix", "line", "rect", "rectb", "circ", "circb",
        "spr", "tri", "trib", "oval", "ovalb", "fillp",
        "sspr", "tline", "background",
        "_moy_restore_bg", "make_layer", "draw_layer", "map", "mget", "mset",
        "sget", "sset", "fget", "fset",
        "print", "touch", "mouse", "clip", "camera", "pal", "palt",
        "btn", "btnp", "players", "key", "keyp", "time", "pmem",
        "textmode", "quit", "view", "cfg", "col",
        "sfx", "beep", "music", "music_stop", "sound_stop", "volume",
        "rnd", "flr", "Image", "image",
        "_moy_cfg",
    }


def test_the_lua_tier_gets_the_config_DICT_beside_the_cfg_closure():
    """`cfg` is a Python closure and C cannot call one. moycore's `run_begin`
    takes the cart's config, `h_cfg` reads it for libmoy's `cfg` verb, and
    `device/moycore_glue.py` picks the DICT out of the namespace by name -- so
    the namespace has to carry both, and they have to be the same config.

    NOTHING PRODUCED IT until 2026-09-11. Every Lua cart on every board read
    `cfg(key, default)` as `default`, silently and forever, while
    `tests/test_moycore_glue.py` passed -- because that test hands `_moy_cfg`
    to the glue itself, so it pinned the CONSUMER over a feature with no
    producer. This pins the producer, and the name is asserted against the
    glue's own source so the two cannot drift apart.
    """
    import pathlib

    class _In:
        def held(self, name):
            return False

        def pressed(self, name):
            return False

    class _Canvas:
        w, h = 320, 240

        def __getattr__(self, name):
            return lambda *a, **k: None

    config = {"perf": 1, "speed": "fast"}
    ns = cart_api.make_api(_Canvas(), _In(), config)
    # The dict ITSELF: the glue hands this object to C, and an edit through the
    # Config tab must be visible to the running cart, not to a stale copy.
    assert ns["_moy_cfg"] is config
    # ... and it is the same config the Python-tier closure reads.
    assert ns["cfg"]("perf", 0) == 1
    assert ns["cfg"]("missing", "d") == "d"

    glue = (pathlib.Path(__file__).resolve().parent.parent
            / "device" / "moycore_glue.py").read_text(encoding="utf-8")
    assert '"_moy_cfg"' in glue, (
        "the glue no longer reads this name -- it is the whole reason the "
        "namespace carries a dict beside the closure")
