"""One C source, two targets (sdk/include/moybyte/cart.h): a compiled cart's C
builds to its wasm module and, through the SDK's adapter (sdk/src/moycart.c),
to a native module a chip, both in one .moy, and the console picks which runs
(runtime/shell/app_perms.py's tier_of):

  * the two draw the same frame from the same cart: tests/fixtures/wasm/
    dual.moy uses every kind of verb, the sheet, the map, a layer, a paint
    image, a scene, the config, par's items and a file's round trip, on the
    host's WAMR and as the host's module, and so does a game
    `moy app new --tier wasm --type game` writes;
  * the native module keeps what the wasm one keeps and plays what it plays
    (native/moy_elf/moy_os.h, A GAME'S CHANNELS): pmem, a pref and a written
    file across runs, a blit through a palette, blit565 and a blit of
    indices, and the audio verbs and the stream on the run's session;
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


def _twins(tmp_path, **cfg):
    """A workstation holding two copies of the built dual cart under their own
    ids, its config `cfg` added: "Dual W", run on the wasm module, and "Dual
    N", run on the native one, so each keeps its own pmem, prefs and files."""
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    built = _built(DUAL, os.path.join(root, "dual.moy"))
    for tag in ("w", "n"):
        dst = os.path.join(root, "dual_%s.moy" % tag)
        shutil.copytree(built, dst)
        mp = os.path.join(dst, "manifest.json")
        with open(mp) as f:
            man = json.load(f)
        man["id"], man["title"] = "local.dual_" + tag, "Dual " + tag.upper()
        man["moybyte"]["config"].update(cfg)
        with open(mp, "w") as f:
            json.dump(man, f)
    ws = no_unlock_banner(host_app.build_workstation(root))
    _opt(ws, "local.dual_n", "Dual N")
    return ws


def _pair(ws, frames=4):
    """Dual W's frame and Dual N's, each from its own tier."""
    kind, wasm = _frame(ws, "Dual W", frames)
    assert kind == "WasmHostRun"
    kind, native = _frame(ws, "Dual N", frames)
    assert kind == "ElfGame"
    return wasm, native


def test_pmem_a_pref_and_a_file_last_across_runs_on_both_targets(tmp_path):
    ws = _twins(tmp_path, mode=1)
    try:
        first = _pair(ws)
        assert first[0] == first[1]
        second = _pair(ws)
        assert second[0] == second[1]
        assert second[0] != first[0]            # each counted the second run
        third = _pair(ws)
        assert third[0] == third[1] and third[0] not in (first[0], second[0])
    finally:
        ws.spine.exit()


@pytest.mark.parametrize("mode", [2, 3, 4])
def test_a_blit_shows_the_same_frame_on_both_targets(tmp_path, mode):
    """2: a palette blit once, drawn over each frame after (the kept frame);
    3: blit565 and 4: a blit of indices, each frame, drawn over."""
    ws = _twins(tmp_path, mode=mode)
    try:
        wasm, native = _pair(ws)
        assert wasm == native
        assert len(set(wasm)) > 16
    finally:
        ws.spine.exit()


def _audio_run(ws, title, frames=6):
    from runtime import audio_binding
    a = audio_binding.get()
    if a is None:
        pytest.skip("no host audio library")
    a.trace(True)
    quiesce(ws)
    open_cart(ws, title)
    p = ws.run.player
    assert p.cart_error is None, p.cart_error
    drv = make_drv(ws)
    for _ in range(frames):
        drv.frame(_DT)
    assert p.cart_error is None, p.cart_error
    kind = type(p.lua).__name__
    # (queued, mixed out, output frames mixed with it empty, room): a wasm run's
    # own stream on the host, the speaker's render-ahead ring for the native one
    counts = p.lua.snd_counts() if kind == "WasmHostRun" else a.snd_counts()
    verbs = [tuple(r[1:]) for r in a.trace()
             if r[1] in (4, 5, 6, 7, 8, 9)]         # moy_aud.h's T_SFX .. T_LEVEL
    a.trace(False)
    ws.input.cart_quit = True
    for _ in range(2):
        drv.frame(_DT)
    return kind, verbs, counts[:3]


def test_the_audio_verbs_and_the_stream_reach_the_session_on_both_targets(tmp_path):
    ws = _twins(tmp_path, mode=5)
    try:
        kind, wasm, w_snd = _audio_run(ws, "Dual W")
        assert kind == "WasmHostRun"
        kind, native, n_snd = _audio_run(ws, "Dual N")
        assert kind == "ElfGame"
        assert len(wasm) == 8, wasm
        assert native == wasm
        # the cart keeps its stream full, and neither runs dry
        assert w_snd[0] > 2048 and w_snd[0] % 256 == 0 and w_snd[2] == 0, w_snd
        assert n_snd[0] % 256 == 0 and abs(n_snd[0] - w_snd[0]) <= 1024, n_snd
        assert n_snd[1] > 0 and n_snd[2] == 0, n_snd
    finally:
        ws.spine.exit()


def test_a_new_compiled_game_draws_the_same_frame_on_both_targets(tmp_path):
    from sdk.moybyte_sdk import cli
    src = cli.new_cart("Fresh", tier="wasm", typ="game", into=str(tmp_path))
    ws = _ws(tmp_path, src, "fresh.moy")
    try:
        kind, wasm = _frame(ws, "Fresh")
        assert kind == "WasmHostRun"
        _opt(ws, "local.fresh", "Fresh")
        kind, native = _frame(ws, "Fresh")
        assert kind == "ElfGame"
        assert wasm == native
        assert len(set(wasm)) > 2               # it drew something
    finally:
        ws.spine.exit()


def test_the_sound_ops_are_the_audio_queue_s():
    """moy_os.h's MOY_OS_SOUND_*, moycart.c's SOUND_* and elf_run's SOUND_OPS
    are moycore_run.h's AQ_* in its order."""
    import elf_run
    with open(os.path.join(ROOT, "native", "moycore", "moycore_run.h")) as f:
        aq = re.search(r"enum \{ (AQ_SFX = 0, [^}]*)\}", f.read()).group(1)
    assert [n.split("=")[0].strip() for n in aq.split(",")] == [
        "AQ_SFX", "AQ_MUSIC", "AQ_BEEP", "AQ_MUSIC_STOP", "AQ_SOUND_STOP", "AQ_VOLUME"]
    with open(os.path.join(ROOT, "native", "moy_elf", "moy_os.h")) as f:
        os_h = dict(re.findall(r"#define MOY_OS_SOUND_(\w+) (\d+)", f.read()))
    assert [int(os_h[k]) for k in ("SFX", "MUSIC", "BEEP", "MUSIC_STOP", "STOP", "VOLUME")] \
        == list(elf_run.SOUND_OPS)
    assert int(os_h["MAX"]) == elf_run.SOUND_MAX == 32
    with open(os.path.join(ROOT, "sdk", "src", "moycart.c")) as f:
        assert ("enum { SOUND_SFX, SOUND_MUSIC, SOUND_BEEP, SOUND_MUSIC_STOP, SOUND_STOP, "
                "SOUND_VOLUME };") in f.read()


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


def test_a_native_wallpaper_is_scaled_from_its_own_memory(tmp_path, monkeypatch):
    """On a desk whose system canvas is not the game canvas (the P4s), a
    native wallpaper's frame is never copied into the game canvas: the cover
    scale reads it from the run's screen slot, through the game canvas's
    palette, and lays the pixels the copy and the canvas's cover made
    (tests/test_screen_slot.py holds the slot keeping it between ticks)."""
    import device_canvas
    from runtime import host_canvas
    copies = []
    blit = device_canvas.DeviceCanvas.blit_indices
    monkeypatch.setattr(device_canvas.DeviceCanvas, "blit_indices",
                        lambda cv, *a: (copies.append(cv), blit(cv, *a))[1])
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    _built(SAKURA, os.path.join(root, "moybyte.sakura_c.moy"))
    ws = no_unlock_banner(host_app.build_workstation(root, sys_size=(1024, 600)))
    try:
        drv = make_drv(ws)
        _opt(ws, "moybyte.sakura_c", "Sakura C")
        ws.look.select_wallpaper("moybyte.sakura_c", persist=False)
        hosted = ws.wallpaper._wp_run
        gc = hosted.run.canvas
        assert type(hosted.h).__name__ == "ElfGame" and gc is not ws.sys_canvas
        frames = set()
        for _ in range(6):
            drv.frame(_DT)
            frames.add(bytes(hosted.kept()[0]))
        assert hosted.h.from_slot and hosted.h.presented >= 2 and len(frames) > 1
        assert gc not in copies                 # no frame went into the game canvas

        frame = hosted.kept()
        new = host_canvas.make_system_canvas(1024, 600)
        new.blit_cover(gc, frame)
        old_gc = host_canvas.make_canvas(320, 240)
        old_gc._wire = gc._wire
        old_gc.blit_indices(frame[0], 320, 240, 0, 0)
        old = host_canvas.make_system_canvas(1024, 600)
        old.blit_cover(old_gc)
        assert bytes(new._buf) == bytes(old._buf)
        assert len(set(memoryview(new._buf).cast("H"))) > 16
    finally:
        ws.spine.exit()
