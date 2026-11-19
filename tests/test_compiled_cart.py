"""One C source, two targets (sdk/include/moybyte/cart.h): a compiled cart's C
builds to its wasm module and, through the SDK's adapter (sdk/src/moycart.c),
to a native module a chip, both in one .moy, and the console picks which runs
(runtime/shell/app_perms.py's tier_of):

  * the two draw the same frame from the same cart: tests/fixtures/wasm/
    dual.moy uses every kind of verb, the sheet, the map, a layer, a paint
    image, a scene, the config, on the host's WAMR and as the host's module;
  * the routing: the native module where the console confines it (the P4s)
    unless Settings turned its full access off, the wasm one elsewhere unless
    the user chose the native one, the other where one is missing, and the
    wasm one in the browser;
  * the consent: full access is asked for only where the native module runs;
  * `moy app build` writes main.wasm and a module a chip it is asked for, and
    `moy app check` reads a dual cart as a wasm one;
  * every verb of moy-spec's header has a native body.
"""

import json
import os
import re
import shutil

import pytest

from runtime import host_app
from tools import elf_cart, wasm_cart
from ws_helpers import make_drv, no_unlock_banner, open_cart, quiesce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUAL = os.path.join(ROOT, "tests", "fixtures", "wasm", "dual.moy")
SAKURA = os.path.join(ROOT, "system_carts", "moybyte.sakura_c.moy")
_DT = 1.0 / 30.0


def _built(src, dst):
    from runtime import wasm_host
    if not wasm_host.available():
        pytest.skip("no host wasm binding")
    try:
        wasm_cart.build(src, dst, [], signed=False)
    except wasm_cart.ToolchainMissing as exc:
        pytest.skip(str(exc))
    elf_cart.build(dst, dst, ["host"], signed=False)
    return dst


def _ws(tmp_path, src=DUAL, folder="dual.moy"):
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    _built(src, os.path.join(root, folder))
    return no_unlock_banner(host_app.build_workstation(root))


def _frame(ws, title, frames=4):
    quiesce(ws)
    open_cart(ws, title)
    p = ws.run.player
    assert p.cart_error is None, p.cart_error
    drv = make_drv(ws)
    for _ in range(frames):
        drv.frame(_DT)
    assert p.cart_error is None, p.cart_error
    quiesce(ws)
    kind = type(p.lua).__name__
    px = bytes(ws.run.canvas._buf)
    ws.input.cart_quit = True
    for _ in range(2):
        drv.frame(_DT)
    return kind, px


def _opt(ws, cid, title, on=True):
    import app_perms
    ws.consent.opt(app_perms.app_id({"id": cid, "title": title}), "full_access", on)


def test_one_source_draws_the_same_frame_on_both_targets(tmp_path):
    ws = _ws(tmp_path)
    try:
        kind, wasm = _frame(ws, "Dual C")
        assert kind == "WasmHostRun"
        _opt(ws, "local.dual", "Dual C")
        kind, native = _frame(ws, "Dual C")
        assert kind == "ElfGame"
        assert wasm == native
        assert len(set(wasm)) > 8               # it drew something
    finally:
        ws.spine.exit()


def test_sakura_c_runs_on_both_targets(tmp_path):
    ws = _ws(tmp_path, SAKURA, "moybyte.sakura_c.moy")
    try:
        assert _frame(ws, "Sakura C", 10)[0] == "WasmHostRun"
        _opt(ws, "moybyte.sakura_c", "Sakura C")
        assert _frame(ws, "Sakura C", 10)[0] == "ElfGame"
    finally:
        ws.spine.exit()


@pytest.fixture
def here():
    import app_perms
    was = dict(app_perms._HERE)
    yield app_perms
    app_perms._HERE.update(was)


DUAL_CART = {"id": "local.dual", "title": "Dual", "runtime": "wasm", "native": ["esp32p4",
                                                                                 "esp32s3"],
             "permissions": ["graphics"]}


@pytest.mark.parametrize("chip, wasm, native, rec, tier", [
    ("esp32p4", True, ["esp32p4"], None, "elf"),             # a P4 runs the native module
    ("esp32p4", True, ["esp32p4"], {"perms": ["full_access"], "off": ["full_access"]},
     "wasm"),                                                # ...unless its access is off
    ("esp32p4", True, ["esp32s3"], None, "wasm"),            # none for it: the wasm one
    ("esp32s3", True, ["esp32s3"], None, "wasm"),            # an S3 runs the wasm module
    ("esp32s3", True, ["esp32s3"], {"perms": ["full_access"], "off": []}, "elf"),  # chosen
    ("esp32s3", False, ["esp32s3"], None, "elf"),            # no wasm tier: the native one
    (None, True, ["esp32p4", "esp32s3"], None, "wasm"),      # the browser
])
def test_the_console_and_its_consent_pick_the_module(here, chip, wasm, native, rec, tier):
    here._HERE.update(chip=chip, wasm=wasm)
    assert here.tier_of(dict(DUAL_CART, native=native), rec) == tier


def test_a_native_cart_and_a_wasm_cart_route_as_before(here):
    here._HERE.update(chip="esp32s3", wasm=True)
    assert here.tier_of({"native": ["esp32s3"], "permissions": ["full_access"]}) == "elf"
    assert here.tier_of({"runtime": "wasm"}) == "wasm"
    assert here.tier_of({"runtime": "lua"}) == "lua"


def test_full_access_is_asked_for_only_where_the_native_module_runs(here):
    here._HERE.update(chip="esp32p4", wasm=True)
    assert here.perms_of(DUAL_CART) == ["graphics", "full_access"]
    assert here.FULL_ACCESS_LINES["p4"] in (here.line("full_access"),
                                            here.FULL_ACCESS_LINES["p4"])
    assert any("machine code" in ln or "full access" in ln.lower()
               for ln in here.prompt(DUAL_CART))
    refusal = here.check(DUAL_CART, {"perms": ["graphics"], "off": []}, ())[2]
    assert refusal is not None and "full_access" in refusal
    here._HERE.update(chip="esp32s3")
    assert here.perms_of(DUAL_CART) == ["graphics"]
    assert here.check(DUAL_CART, {"perms": ["graphics"], "off": []}, ())[2] is None
    assert here.native_choice(DUAL_CART)
    opted = {"perms": ["graphics", "full_access"], "off": []}
    assert here.perms_of(DUAL_CART, opted) == ["graphics", "full_access"]
    granted, _absent, refusal = here.check(DUAL_CART, opted, ())
    assert refusal is None and "full_access" in granted


class _Store(dict):
    def set(self, k, v):
        self[k] = json.loads(json.dumps(v))


def test_the_choice_outlives_a_reinstall_and_turns_off():
    import app_perms
    c = app_perms.Consent(_Store())
    c.record("local.dual", ["graphics"])
    assert not app_perms.native_opted(c.get("local.dual"))
    c.opt("local.dual", "full_access", True)
    assert app_perms.native_opted(c.get("local.dual"))
    c.record("local.dual", ["graphics"])
    assert app_perms.native_opted(c.get("local.dual"))
    c.opt("local.dual", "full_access", False)
    assert not app_perms.native_opted(c.get("local.dual"))
    c.made("local.made")
    c.opt("local.made", "full_access", True, ["graphics"])
    assert c.get("local.made")["perms"] == ["graphics", "full_access"]


def test_moy_app_build_writes_both_and_check_reads_a_wasm_cart(tmp_path):
    from runtime import wasm_host  # noqa: F401
    from sdk.moybyte_sdk import cli
    cart = str(tmp_path / "dual.moy")
    shutil.copytree(DUAL, cart)
    try:
        built = dict(cli.build_cart(cart, ["host"], signed=False))
    except cli.Refused as exc:
        pytest.skip(str(exc))
    assert set(built) >= {"main", "host"}
    with open(os.path.join(cart, "manifest.json")) as f:
        man = json.load(f)
    assert man["moybyte"]["native"] == ["host"] and man["runtime"] == "wasm"
    assert cli.tier_of(man, cart) == "wasm"
    errors, warnings = [], []
    cli.check_manifest(man, cart, errors, warnings)
    assert errors == [], errors


def test_every_verb_of_the_header_has_a_native_body():
    with open(os.path.join(ROOT, "sdk", "include", "moy_cart.h")) as f:
        verbs = re.findall(r"MOY_IMPORT\(\w+\) [\w ]*?\b(moy_\w+)\(", f.read())
    with open(os.path.join(ROOT, "sdk", "include", "moybyte", "cart.h")) as f:
        head = f.read()
    with open(os.path.join(ROOT, "sdk", "src", "moycart.c")) as f:
        body = f.read()
    assert len(verbs) > 50
    for v in verbs:
        assert "#define %s moy_cart_%s\n" % (v, v[4:]) in head, v
        assert re.search(r"^[\w ]+\*?\b%s\(" % v, body, re.M), v


def test_settings_switch_runs_the_native_module_and_remembers(tmp_path):
    """On a console that does not confine a native module (the host's chip, as
    an S3's), App permissions carries the cart's switch, off; turning it on
    runs the native module from the next launch, and the record keeps it."""
    import app_perms
    ws = _ws(tmp_path)
    try:
        aid = app_perms.app_id(next(c for c in ws.carts.all if c["title"] == "Dual C"))
        ws.open_settings()
        sl = ws.settings_layer
        sl.open_page("permissions")
        ws.frame(_DT)
        rows = {title: perms for title, _aid, perms in sl.permits.rows()}
        assert rows["Dual C"] == (("native", app_perms.NATIVE_LINE),)
        sid = aid + " native"
        x, y, w, h = sl._sub.box(sid)
        for down in (True, False):
            sl.handle_pointer(x + w // 2, y + h // 2, down)
            ws.mark_dirty()
            ws.frame(_DT)
        assert app_perms.native_opted(ws.consent.get(aid))
        sl.back()
        assert _frame(ws, "Dual C")[0] == "ElfGame"
    finally:
        ws.spine.exit()


def test_sakura_c_is_a_wallpaper_on_both_targets(tmp_path, monkeypatch):
    import elf_run
    shown = []
    draw = elf_run.ElfGame._draw
    monkeypatch.setattr(elf_run.ElfGame, "_draw", lambda g: (shown.append(g._shown), draw(g)))
    ws = _ws(tmp_path, SAKURA, "moybyte.sakura_c.moy")
    try:
        drv = make_drv(ws)
        for opted, kind in ((False, "WasmHostRun"), (True, "ElfGame")):
            _opt(ws, "moybyte.sakura_c", "Sakura C", opted)
            ws.look.select_wallpaper("moybyte.sakura_c", persist=False)
            seen = set()
            for _ in range(6):
                drv.frame(_DT)
                seen.add(bytes(ws.wallpaper._wp_run.run.canvas._buf))
            assert type(ws.wallpaper._wp_run.h).__name__ == kind
            assert len(seen) > 1                    # the petals fall
            assert all(len(set(px)) > 16 for px in seen)    # over the tree's picture
            assert (True in shown) == opted     # the native frame reaches the canvas
            ws.look.select_wallpaper(None, persist=False)
            assert ws.wallpaper._wp_run is None
            assert ws.run.player.cart_error is None if ws.run.player else True
    finally:
        ws.spine.exit()
