"""moy-spec (SPEC.md) cart semantics on the reference console.

A `"format": "moy-1"` manifest is the brand-neutral spec cart: Lua by
definition, main.lua, the spec's 30fps default tick, a game. The store must
apply those defaults, carry the spec-only fields (`palette`, `extensions`)
through load/duplicate, the Player must refuse a cart requiring an extension
this build doesn't implement (SPEC.md 10), and a cart-supplied palette
(SPEC.md 2.2) must be live for exactly the run's lifetime.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RED64 = ["FF0000"] * 64


from ws_helpers import build_ws as _ws


def _open(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError("no cart titled " + title)


def _write_moy1(root, name, manifest, src="function _draw()\n  cls(1)\nend\n"):
    d = Path(root) / (name + ".moy")
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(manifest))
    (d / manifest.get("main", "main.lua")).write_text(src)
    return str(d)


# -- store: moy-1 defaults + spec-field passthrough -----------------------------

def test_moy1_defaults(tmp_path):
    from runtime import moy_carts
    path = _write_moy1(tmp_path, "spec", {"format": "moy-1", "title": "Spec"})
    cart = moy_carts.load(path)
    assert cart["runtime"] == "lua"          # spec carts are Lua by definition
    assert cart["main"] == "main.lua"
    assert cart["type"] == "game"
    assert cart["fps"] == 30                 # SPEC.md 5: 30 is the default tick
    assert cart["palette"] is None
    assert cart["extensions"] == []


def test_moybyte_defaults_unchanged(tmp_path):
    """The moy-1 branch must not move moybyte's own defaults."""
    from runtime import moy_carts
    d = Path(tmp_path) / "old.moy"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"title": "Old"}))
    (d / "main.py").write_text("def _draw():\n    pass\n")
    cart = moy_carts.load(str(d))
    assert cart["runtime"] == "python" and cart["main"] == "main.py"
    assert cart["type"] == "app" and cart["fps"] == 0


def test_spec_fields_survive_load_and_duplicate(tmp_path):
    from runtime import moy_carts
    path = _write_moy1(tmp_path, "spec", {
        "format": "moy-1", "title": "Spec",
        "palette": RED64, "extensions": ["layers"],
    })
    cart = moy_carts.load(path)
    assert cart["palette"] == RED64
    assert cart["extensions"] == ["layers"]
    dup = moy_carts.duplicate(cart, str(tmp_path))
    assert dup["palette"] == RED64
    assert dup["extensions"] == ["layers"]


# -- Player: the SPEC.md 10 refusal + the SPEC.md 2.2 palette lifetime ----------

def test_unknown_extension_refused(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    moy_carts.create("Warp", root, src="def _draw():\n    pass\n",
                     type="game", extensions=["warpdrive"])
    ws = _ws(tmp_path)                 # scans the pre-created cart with the seeds
    _open(ws, "Warp")
    assert ws.cart_error and "warpdrive" in ws.cart_error


def test_cart_palette_applied_then_restored(tmp_path):
    from runtime import moy_carts
    root = str(Path(tmp_path) / "carts")
    Path(root).mkdir(parents=True, exist_ok=True)
    moy_carts.create("Reddish", root, src="def _draw():\n    pass\n",
                     type="game", palette=RED64)
    from runtime import host_app
    ws = host_app.build_workstation(root)
    default_red = ws.canvas.palette[8]           # MOY64 idx 8 (FF004D)
    _open(ws, "Reddish")
    assert not ws.cart_error
    assert tuple(ws.canvas.palette[8]) == (255, 0, 0)   # cart table live
    ws.player.release_world()
    assert tuple(ws.canvas.palette[8]) == tuple(default_red)  # restored


# -- SPEC.md 3.1: a manifest survives a rewrite, and a bad field degrades ------

def test_duplicate_keeps_moy1_format_and_fps(tmp_path):
    """Copying a spec cart must yield another SPEC cart. Restamping it
    "moybyte-cart-v1" would make it unrecognisable to every other host -- and
    silently reset its declared tick to moybyte's app default."""
    from runtime import moy_carts
    path = _write_moy1(tmp_path, "spec", {
        "format": "moy-1", "title": "Spec", "fps": 60})
    cart = moy_carts.load(path)
    assert cart["format"] == "moy-1" and cart["fps"] == 60
    dup = moy_carts.duplicate(cart, str(tmp_path))
    assert dup["format"] == "moy-1"
    assert dup["fps"] == 60
    assert dup["runtime"] == "lua" and dup["main"] == "main.lua"


def test_moybyte_cart_duplicate_unchanged(tmp_path):
    """The passthrough must not restamp moybyte's own carts either way."""
    from runtime import moy_carts
    root = str(tmp_path / "c")
    Path(root).mkdir(parents=True)
    cart = moy_carts.create("Mine", root, src="def _draw():\n    pass\n")
    assert cart["format"] == moy_carts.CART_FORMAT
    assert moy_carts.duplicate(cart, root)["format"] == moy_carts.CART_FORMAT


def test_malformed_version_degrades_not_deletes(tmp_path):
    """A hand-typed "1.2" must read as unversioned, not drop the whole cart out
    of the gallery -- SPEC.md 3.1 leaves `version` to the author."""
    from runtime import moy_carts
    path = _write_moy1(tmp_path, "spec", {
        "format": "moy-1", "title": "Spec", "version": "1.2"})
    cart = moy_carts.load(path)
    assert cart is not None
    assert cart["version"] == 0
    assert cart["title"] == "Spec"


# -- SPEC.md 10: vendor extensions are namespaced, and moybyte knows its own ---

def test_vendor_namespaced_extension_is_accepted(tmp_path):
    """moybyte implements scenes, so a cart HONESTLY declaring the namespaced
    name must run -- before this the gate refused every vendor name, i.e. the
    one console that implements the feature rejected carts that declared it."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    moy_carts.create("Placed", root, src="def _draw():\n    pass\n",
                     type="game", extensions=["moybyte.scenes"])
    ws = _ws(tmp_path)
    _open(ws, "Placed")
    assert not ws.cart_error


def test_unknown_vendor_extension_still_refused(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    moy_carts.create("Warp2", root, src="def _draw():\n    pass\n",
                     type="game", extensions=["moybyte.warpdrive"])
    ws = _ws(tmp_path)
    _open(ws, "Warp2")
    assert ws.cart_error and "moybyte.warpdrive" in ws.cart_error


# -- SPEC.md 7.3: the host owns exit, so a cart never sees it ------------------

def test_cart_cannot_read_the_hosts_exit_button(tmp_path):
    from runtime import host_app
    from runtime.host_canvas import make_canvas as Canvas
    from runtime.input import InputState
    inp = InputState()
    api = host_app.make_api(Canvas(320, 240), inp, {})
    for name in ("left", "right", "up", "down", "a", "b", "run"):
        assert api["btn"](name) is False        # present, just not held
    inp.set_held("home", True)
    inp.begin_frame()
    assert inp.held("home")                     # the console still sees it
    assert api["btn"]("home") is False          # the cart never does
    assert api["btnp"]("home") is False
    inp.set_held("a", True)
    inp.begin_frame()
    assert api["btn"]("a") is True              # a real cart button is unaffected


def test_device_cart_buttons_match_host():
    """host_api and device_api must agree on the cart-visible button set --
    trivially, since 2026-08-17: both re-export the ONE tuple from
    runtime/cart_api.py (the source-regex this test used to run died with the
    device copy). The identity is the strongest agreement there is."""
    from runtime import host_api
    from device import device_api
    assert device_api.CART_BUTTONS is host_api.CART_BUTTONS
    assert host_api.CART_BUTTONS == ("left", "right", "up", "down",
                                     "a", "b", "run")


def test_host_and_device_make_api_agree_with_every_capability_gate_open():
    """Two backends, one cart namespace: the whole name set, all gates open.

    The BASE set is already pinned twice (test_wifi's keyset test, and
    test_multiplayer's), and each pins ONE gate on top of it -- wifi there, net
    there. Nothing pinned the rest, and the rest is where the names arrive in
    batches: `scenes` alone injects nine (#85/#109's actor verbs), added to two
    files by hand on the same day.

    This is the case the staging closure's keep-the-twin rule does NOT cover
    (tests/test_staging_closure.py, "derive them and the tripwire fires
    never"): there is no denial to derive from here, just two independent
    implementations of one contract, and asserting they match is the only way
    to learn that they stopped. A verb added to one only is a cart that runs on
    the host and raises NameError on glass, or the reverse.
    """
    import importlib
    from runtime import host_app
    from runtime import widgets

    class _StubInput:
        def held(self, n):
            return False

        def pressed(self, n):
            return False

    class _Stub:
        w, h = 320, 240

        def __getattr__(self, name):
            return lambda *a, **k: 0

    class _GpioGate:
        """The #9 pin backend's shape -- make_api binds these two methods, so
        unlike `wifi` (a bare object handed straight to the cart) this gate
        cannot be an `object()`."""

        def write(self, n, v):
            return False

        def read(self, n):
            return None

    modules_dir = ROOT / "device"
    sys.path.insert(0, str(modules_dir))
    try:
        for stale in ("device_util", "device_canvas", "device_api"):
            sys.modules.pop(stale, None)
        dev = importlib.import_module("device_api")
    finally:
        sys.path.remove(str(modules_dir))

    def names(mod, **kw):
        return set(mod.make_api(_Stub(), _StubInput(), {}, **kw).keys())

    base_h, base_d = names(host_app), names(dev)
    assert base_h == base_d

    # Every gate the Player can open, together -- so a name that only appears
    # under a combination is compared too.
    gates = dict(scenes=widgets.Scenes({}, []), images={}, tables={},
                 texts={}, wifi=object(), gpio=_GpioGate())
    full_h, full_d = names(host_app, **gates), names(dev, **gates)
    assert full_h == full_d, (
        "host-only: %s / device-only: %s"
        % (sorted(full_h - full_d), sorted(full_d - full_h)))
    # ...and the gates really do add something, or this test would pass while
    # measuring nothing (a Scenes that silently failed to construct, say).
    assert full_h - base_h, "the capability gates injected no names at all"

    # And the CANONICAL name list is this set. runtime/cart_verbs.CART_VERBS is
    # what the code editor's highlighter and the block compiler's reserved-name
    # gate derive from; both used to retype it by hand and both had drifted.
    # This is the assertion that makes it a single source rather than a fifth
    # copy, so it belongs here, beside the two namespaces it describes.
    from runtime.cart_verbs import CART_VERBS
    from runtime import players as players_mod
    with_net = names(host_app, net=players_mod.LoopbackNet(), **gates)
    public = set(n for n in with_net if not n.startswith("_"))
    assert set(CART_VERBS) == public, (
        "cart_verbs.CART_VERBS drifted from make_api -- missing: %s / extra: %s"
        % (sorted(public - set(CART_VERBS)), sorted(set(CART_VERBS) - public)))
    assert len(set(CART_VERBS)) == len(CART_VERBS), "duplicate name in CART_VERBS"


# -- SPEC.md 4.1: the host sandbox is a MAXIMUM, matched to what glass can give -

def test_host_lua_sandbox_matches_the_device_ceiling(tmp_path):
    """utf8 is the one library lupa's openlibs leaks that the device build drops
    from its sources outright -- a cart using it would run here and die on glass."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    probe = "\n".join(
        'if %s ~= nil then error("%s reachable") end' % (n, n)
        for n in ("io", "os", "debug", "package",       # coroutine: admitted 2026-09-02
                  "utf8", "require", "load", "dofile", "collectgarbage"))
    moy_carts.create("Sandbox", root, src="function _draw()\n" + probe + "\nend\n",
                     type="game", runtime="lua", main="main.lua")
    from runtime import host_app
    ws = host_app.build_workstation(root)
    if getattr(ws, "lua_runtime", None) is None:
        import pytest
        pytest.skip("lupa not installed")
    _open(ws, "Sandbox")
    assert not ws.cart_error
    ws.frame(1 / 30.0)
    assert not ws.cart_error, ws.cart_error


# -- SPEC.md 3.1/15: an unimplemented `runtime` is REFUSED, never ignored ------

def test_unknown_runtime_is_refused_cleanly(tmp_path):
    """Ignoring the field is the one reading that fails badly: the host would
    hand a script in another language to its Lua VM and blame the author's code.
    A clean refusal names the real problem instead."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    moy_carts.create("Wasm", root, src="(module)\n", type="game",
                     runtime="wasm", main="main.wasm")
    ws = _ws(tmp_path)
    _open(ws, "Wasm")
    assert ws.cart_error and "wasm" in ws.cart_error
    # and the refusal must not have left a half-started world behind
    assert ws.player._lua is None


def test_lua_runtime_still_runs(tmp_path):
    """The refusal must key on the binding being unimplemented, not on the field
    merely being present -- "lua" is core's binding and always runs."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    moy_carts.create("Luacart", root, type="game", runtime="lua", main="main.lua",
                     src="function _draw()\n  cls(3)\nend\n")
    from runtime import host_app
    ws = host_app.build_workstation(root)
    if getattr(ws, "lua_runtime", None) is None:
        import pytest
        pytest.skip("lupa not installed")
    _open(ws, "Luacart")
    assert not ws.cart_error
    ws.frame(1 / 30.0)
    assert not ws.cart_error


# -- a copy is the whole project, not just its code ----------------------------

def test_duplicate_carries_every_asset(tmp_path):
    """The picker's COPY used to arrive with only manifest/main/config: create()
    writes the code side, and every other asset lived solely in the source
    FOLDER -- so duplicating a game silently discarded its sprite sheet,
    tilemap, sounds, blocks and cover art."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    cart = moy_carts.create("Full", root, src="def _draw():\n    pass\n", type="game")
    p = Path(cart["path"])
    (p / "sprites.moygfx").write_text("0" * 128 + "\n")
    (p / "map.moymap").write_text("2 1\n0102\n")
    (p / "flags.moyflags").write_text("0003" + "\n")
    (p / "sounds.json").write_text(json.dumps({"sfx": [], "music": []}))
    (p / "blocks.json").write_text(json.dumps({"blocks": []}))
    for sub, fn, blob in (
            ("images", "cover.moyimg",
             json.dumps({"format": "moyimg-v1", "w": 1, "h": 1,
                         "codec": "rle", "data": "AA"})),
            ("tables", "scores.moysheet",
             json.dumps({"format": "moysheet-v1", "cells": {}})),
            ("docs", "notes.moytext",
             json.dumps({"format": "moytext-v1", "body": "hi"}))):
        (p / sub).mkdir()
        (p / sub / fn).write_text(blob)

    src = moy_carts.load(str(p))
    dup = moy_carts.duplicate(src, root)
    assert dup["sprites"] == src["sprites"]
    assert dup["map"] == src["map"]
    assert dup["flags"] == src["flags"]
    assert dup["sounds"] == src["sounds"]
    assert dup["blocks"] == src["blocks"]
    assert set(dup["images"]) == {"cover"}
    assert set(dup["tables"]) == {"scores"}
    assert set(dup["texts"]) == {"notes"}


# -- tile flags (SPEC.md 3.5) --------------------------------------------------

def test_flags_moyflags_loads_and_zero_pads(tmp_path):
    """A flags file may be SHORT (an author tags the first few tiles and stops)
    and may be laid out however -- sixteen lines, one line, spaces. Whitespace
    is not data; length is: the rest of the 512 tiles are zero."""
    from runtime import moy_carts
    path = _write_moy1(tmp_path, "flagged", {"format": "moy-1", "title": "F"})
    Path(path, moy_carts.FLAGS_NAME).write_text("00 01\n03\t80\n")
    cart = moy_carts.load(path)
    flags = moy_carts.parse_flags(cart["flags"])
    assert len(flags) == 512
    assert list(flags[:5]) == [0x00, 0x01, 0x03, 0x80, 0x00]
    assert not any(flags[4:])


def test_a_cart_with_no_flags_file_reads_as_all_zero(tmp_path):
    """SPEC.md 3.5: an absent file is all zero. The cart dict says None (the
    file is not there); the live table exists anyway, so fget()/fset() and
    map(..., layers) are callable for every cart ever written."""
    from runtime import moy_carts
    from runtime.project import Project
    path = _write_moy1(tmp_path, "plain", {"format": "moy-1", "title": "P"})
    cart = moy_carts.load(path)
    assert cart["flags"] is None
    table = Project.__new__(Project)._build_flags(cart)
    assert len(table) == 512 and not any(table)


def test_a_corrupt_flags_file_degrades_to_zero_rather_than_dropping_the_cart(tmp_path):
    """Every other asset in load() degrades; this one must too. An odd digit
    count has no honest alignment, so the parser refuses it -- and the cart
    still loads, with no flags, instead of vanishing from the gallery."""
    from runtime import moy_carts
    from runtime.project import Project
    path = _write_moy1(tmp_path, "bad", {"format": "moy-1", "title": "B"})
    Path(path, moy_carts.FLAGS_NAME).write_text("0102030")
    cart = moy_carts.load(path)
    assert cart is not None
    table = Project.__new__(Project)._build_flags(cart)
    assert not any(table)


def test_the_flags_blob_round_trips_through_a_seed(tmp_path):
    """A baked built-in carries its flags like its sheet and its map, or a
    seeded device runs the same cart with every tile untagged."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    table = bytearray(512)
    table[1], table[2], table[511] = 0x01, 0x03, 0xFF
    moy_carts.seed_builtins([{
        "title": "Tagged", "type": "game", "version": 1, "cfg": {},
        "src": "def _draw():\n    pass\n",
        "flags": moy_carts.flags_to_hex(table),
    }], root)
    cart = moy_carts.load(root + "/tagged.moy")
    assert moy_carts.parse_flags(cart["flags"]) == table


def test_duplicate_leaves_behind_saves_and_undo_history(tmp_path):
    """A copy is a NEW project: inheriting the original's journal would let it
    'undo' into another project's edits, and its pmem is that cart's save."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    cart = moy_carts.create("Saved", root, src="def _draw():\n    pass\n", type="game")
    p = Path(cart["path"])
    (p / "pmem.json").write_text(json.dumps([7] * 8))
    (p / "journal.jsonl").write_text('{"seq": 1}\n')
    dup = moy_carts.duplicate(moy_carts.load(str(p)), root)
    names = set(x.name for x in Path(dup["path"]).iterdir())
    assert "pmem.json" not in names
    assert not any(n.startswith("journal") for n in names)


def test_duplicate_of_a_pathless_cart_still_works(tmp_path):
    """The device's embedded fallback carts are dicts with no folder -- the file
    copy must be skipped, not crash."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    cart = moy_carts.create("Embedded", root, src="def _draw():\n    pass\n", type="game")
    pathless = dict(moy_carts.load(cart["path"]))
    pathless["path"] = None
    dup = moy_carts.duplicate(pathless, root)
    assert dup is not None and dup["src"] == cart["src"]


# -- SPEC.md 3.4: the launcher icon is a POINTER into the sheet ----------------

def test_icon_normalizes_both_manifest_shapes(tmp_path):
    from runtime import moy_carts
    for value, want in (
            (4, (4, 1, 1)),                  # bare tile id == 1x1
            ([4], (4, 1, 1)),
            ([4, 2, 2], (4, 2, 2)),
            (None, None),
            ("4", None),                     # malformed shapes cost the picture,
            ([], None),                      # never the cart
            ([-1, 1, 1], None),
            ([4, 0, 1], None),
            ([4, 4, 4], (4, 4, 4)),          # the SPEC.md 3.4 ceiling
            ([4, 5, 1], None),               # past it: ignored, not refused
            ([4, 1, 9], None),
            (True, None),
            ([4, "x", 2], None)):
        path = _write_moy1(tmp_path, "i%s" % abs(hash(repr(value))), {
            "format": "moy-1", "title": "Icon", "icon": value}
            if value is not None else {"format": "moy-1", "title": "Icon"})
        cart = moy_carts.load(path)
        assert cart is not None, value       # a bad icon never drops the cart
        assert cart["icon"] == want, (value, cart["icon"])


def test_icon_survives_duplicate(tmp_path):
    from runtime import moy_carts
    path = _write_moy1(tmp_path, "spec", {
        "format": "moy-1", "title": "Spec", "icon": [4, 2, 2]})
    dup = moy_carts.duplicate(moy_carts.load(path), str(tmp_path))
    assert dup["icon"] == (4, 2, 2)


def test_launcher_draws_the_declared_icon_tiles(tmp_path):
    """A 2x2 icon must resolve to a 16x16 blittable, and an absent one to tile 0
    -- the host's free choice when the cart declines to name tiles."""
    from runtime import moy_carts, host_app
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    # tile 0 blank, tiles 16..17/32..33 (a 2x2 block at tile 16) painted
    rows = ["0" * 128] * 16 + ["0" * 16 + "8" * 16 + "0" * 96] * 16
    for name, icon in (("Big", [16, 2, 2]), ("Plain", None)):
        c = moy_carts.create(name, root, src="def _draw():\n    pass\n",
                             type="game", icon=icon)
        Path(c["path"], "sprites.moygfx").write_text("\n".join(rows))
    ws = host_app.build_workstation(root)
    by_title = {c["title"]: c for c in ws.launcher.items if c.get("path")}
    big = ws.covers.icon_sheet_for(by_title["Big"])
    assert big is not None and (big.w, big.h) == (16, 16)
    plain = ws.covers.icon_sheet_for(by_title["Plain"])
    assert plain is not None and (plain.w, plain.h) == (8, 8)


def test_icon_past_the_sheet_falls_back(tmp_path):
    """SPEC.md 3.4: an icon naming tiles this cart doesn't have is IGNORED --
    the host picks instead. A cosmetic field must never blank the tile."""
    from runtime import moy_carts, host_app
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    c = moy_carts.create("Missing", root, src="def _draw():\n    pass\n",
                         type="game", icon=[500, 1, 1])
    Path(c["path"], "sprites.moygfx").write_text("\n".join(["8" * 128] * 8))
    ws = host_app.build_workstation(root)
    cart = next(x for x in ws.launcher.items if x["title"] == "Missing")
    assert cart["icon"] == (500, 1, 1)          # carried verbatim...
    assert ws.covers.icon_sheet_for(cart) is not None  # ...but never a blank tile


# -- pmem slots are signed 32-bit (SPEC.md 9, matching 4.2) --------------------

def test_pmem_round_trips_negative_values():
    """Slots held UNSIGNED 32-bit, so pmem(i, -1) read back 4294967295 -- wrong
    for a python cart, and not even representable in a LUA_32BITS integer, so a
    lua cart could not store a negative number at all."""
    from runtime.widgets import Pmem
    p = Pmem()
    for v in (-1, -42, 0, 7, 2147483647, -2147483648):
        p.cell(0, v)
        assert p.cell(0) == v, v
    p.cell(0, 2147483648)               # past the top: wraps like the VM
    assert p.cell(0) == -2147483648


def test_pmem_load_reinterprets_legacy_unsigned_saves(tmp_path):
    """Saves written before the width was pinned stored a negative as a huge
    unsigned value; reading them back signed is the migration."""
    from runtime import moy_carts
    d = Path(tmp_path) / "c.moy"
    d.mkdir()
    (d / "pmem.json").write_text(json.dumps([4294967295, 7, 2147483648]))
    cells = moy_carts.load_pmem(str(d))
    assert cells[0] == -1
    assert cells[1] == 7
    assert cells[2] == -2147483648


# -- a cart is a FOLDER; a .moy archive is transport, not storage --------------

def test_scan_ignores_a_moy_archive_file(tmp_path):
    """A .moy FILE is how a cart travels; unpacking belongs to whatever brought
    it here. It must be skipped silently, not reported as a corrupt cart."""
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    Path(root).mkdir(parents=True)
    moy_carts.create("Real", root, src="def _draw():\n    pass\n", type="game")
    (Path(root) / "downloaded.moy").write_bytes(b"PK\x03\x04not-a-folder")
    found = moy_carts.scan(root)
    assert [c["title"] for c in found] == ["Real"]
