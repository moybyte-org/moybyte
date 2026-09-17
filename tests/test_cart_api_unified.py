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

from ws_helpers import StubInput


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

    class _Canvas:
        w, h = 320, 240

        def __getattr__(self, name):
            return lambda *a, **k: None

    config = {"perf": 1, "speed": "fast"}
    ns = cart_api.make_api(_Canvas(), StubInput(), config)
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


def test_a_cart_may_be_several_scripts_and_each_one_is_its_own_chunk(tmp_path):
    """SPEC.md 4: the manifest's `sources` is the whole load order, `main` among
    them, and the host runs each entry as its OWN chunk.

    The standing case is a PICO-8 port, whose GENERATED half -- data tables plus
    the compat shim -- is `p8.lua` rather than the first 61% of `main.lua`. The
    store splits the list at `main` because that is how the tiers use it: `src`
    travels on its own (the Editor edits it, the crash panel maps its lines), so
    what is left is the pieces either side.

    Why chunks and not a concatenation: the shim publishes 96 globals, which
    cross a chunk boundary, while its four per-cart upvalue captures
    (`__p8_gff`, `__music_map`, `__p8_map_raw`, `__p8_sheet`) sit beside the data
    tables INSIDE p8.lua -- which is what fixes the cut there. And each chunk is
    named after its file, so a fault in generated code reads `p8.lua:N:` instead
    of landing on the kid's line in the crash-to-code panel.
    """
    import json
    import pathlib
    from runtime import moy_carts

    root = tmp_path / "carts"
    cart = root / "port.moy"
    cart.mkdir(parents=True)
    (cart / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": "Port", "main": "main.lua",
         "sources": ["p8.lua", "main.lua", "perf.lua"],
         "runtime": "lua", "ported_from": "pico-8"}), encoding="utf-8")
    (cart / "main.lua").write_text("function _update() end\n", encoding="utf-8")
    (cart / "p8.lua").write_text("-- the generated half\n", encoding="utf-8")
    (cart / "perf.lua").write_text("-- the wrapper\n", encoding="utf-8")

    got = moy_carts.load(str(cart))
    assert got["src"] == "function _update() end\n", \
        "main.lua must be the cart's OWN code and nothing else"
    assert got["src_before"] == [("p8.lua", "-- the generated half\n")], \
        "the store did not read the scripts listed before main"
    assert got["src_after"] == [("perf.lua", "-- the wrapper\n")], \
        "a script listed AFTER main must stay after it -- that is where a "\
        "wrapper around what the cart defined has to run"

    # No `sources` is a one-script cart, and says so with empty lists -- the
    # tiers iterate them unconditionally, so None would be a per-frame guard.
    plain = root / "plain.moy"
    plain.mkdir()
    (plain / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": "Plain", "main": "main.lua",
         "runtime": "lua"}), encoding="utf-8")
    (plain / "main.lua").write_text("function _update() end\n", encoding="utf-8")
    got = moy_carts.load(str(plain))
    assert got["src_before"] == [] and got["src_after"] == []

    # A `sources` that omits `main` is refused, not reordered: SPEC.md 4 requires
    # main in the list, and running it last would fail inside code the author
    # actually wrote.
    bad = root / "bad.moy"
    bad.mkdir()
    (bad / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": "Bad", "main": "main.lua",
         "sources": ["p8.lua"], "runtime": "lua"}), encoding="utf-8")
    (bad / "main.lua").write_text("function _update() end\n", encoding="utf-8")
    (bad / "p8.lua").write_text("-- half\n", encoding="utf-8")
    assert moy_carts.load(str(bad)) is None

    # ONE body builds the chunk list, and BOTH tiers hand the whole list to
    # load() rather than exec-ing pieces of it: load is where the verb profiler
    # arms and where the host tier opens the PICO-8 machine, so a chunk run
    # outside it lands on the wrong side of both.
    root_dir = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("device/moycore_glue.py", "runtime/lua_host.py"):
        body = (root_dir / rel).read_text(encoding="utf-8")
        assert "cart_chunks(ns, src)" in body, rel
        assert 'load(src, "@cart")' not in body, \
            "%s still loads main.lua alone" % rel
    from runtime.lua_ext import cart_chunks
    assert cart_chunks({"_moy_pre": [("p8.lua", "P")],
                        "_moy_post": [("perf.lua", "Q")]}, "C") == [
        ("P", "@p8.lua"), ("C", "@cart"), ("Q", "@perf.lua")]
    assert cart_chunks({}, "C") == [("C", "@cart")], \
        "a one-script cart must reach load() as the list it always was"

    # ...and the diet drops them: 62KB of shim resident per cart is the largest
    # single payload on a ported shelf and the least useful (#66).
    from runtime import cart_manager
    assert "src_before" in cart_manager._HEAVY_CART_KEYS
    assert "src_after" in cart_manager._HEAVY_CART_KEYS


def test_a_several_script_cart_survives_the_bake_and_the_seed(tmp_path):
    """The seed path is the OTHER writer, and it REGENERATES rather than copies.

    A device seeds from a baked blob, so every field a cart declares has to be
    carried explicitly or the seeded copy loses it -- silently, which for
    `sources` means a cart seeded without its prologue, failing inside code the
    author did write. This drives the whole round trip: folder -> blob ->
    seeded folder -> loaded cart."""
    import json
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                           / "tools"))
    import gen_device_carts
    from runtime import moy_carts

    src = tmp_path / "system_carts"
    cart = src / "port.moy"
    cart.mkdir(parents=True)
    (cart / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": "Port", "type": "game", "version": 3,
         "system": True, "order": 1, "runtime": "lua", "main": "main.lua",
         "sources": ["p8.lua", "main.lua", "perf.lua"]}), encoding="utf-8")
    (cart / "main.lua").write_text("function _update() end\n", encoding="utf-8")
    (cart / "p8.lua").write_text("-- prologue\n", encoding="utf-8")
    (cart / "perf.lua").write_text("-- epilogue\n", encoding="utf-8")

    baked = gen_device_carts.build_carts(str(src))
    assert len(baked) == 1
    assert baked[0]["src_before"] == [("p8.lua", "-- prologue\n")]
    assert baked[0]["src_after"] == [("perf.lua", "-- epilogue\n")]

    root = str(tmp_path / "carts")
    (tmp_path / "carts").mkdir()
    moy_carts.seed_builtins(baked, root=root)
    seeded = json.loads((tmp_path / "carts" / "port.moy" / "manifest.json")
                        .read_text(encoding="utf-8"))
    assert seeded["sources"] == ["p8.lua", "main.lua", "perf.lua"], \
        "the regenerated manifest must name the order it was written with"

    got = moy_carts.load(root + "/port.moy")
    assert got["src_before"] == [("p8.lua", "-- prologue\n")]
    assert got["src_after"] == [("perf.lua", "-- epilogue\n")]
