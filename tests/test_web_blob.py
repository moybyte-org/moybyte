"""The browser console, baked into the firmware image (tools/gen_web_blob.py).

The board serves the wasm console over WiFi, and it used to serve it ONLY from
a copy somebody had put on the board's storage. That copy drifts with nothing
to detect it -- on 2026-08-15 a board served a bundle old enough to still carry
a desktop-blackout bug fixed in dist/ hours earlier -- and the T-Deck could not
even be pushed to, because the push tool hands the board a url over serial and
that board's USB-CDC RX is dead under the desktop. So the image carries the
bundle.

The generated C file is the drift-prone half of that: it is build output nobody
reads, it carries absolute `.incbin` paths, and if it silently emitted the
wrong lengths the board would serve a truncated wasm that a browser refuses to
instantiate with nothing useful in its console. So this compiles it -- with the
HOST compiler, which is the same `.incbin` mechanism the two cross toolchains
use -- and reads the bytes back out.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
MODULE = ROOT / "native" / "moy_web"

sys.path.insert(0, str(TDECK / "modules"))
sys.path.insert(0, str(ROOT / "tools"))

import gen_web_blob as gwb                                        # noqa: E402
import moy_webhost as wh                                          # noqa: E402


def _dist(tmp_path, names=None, sizes=None):
    """A fake `firmware/web_runner/dist` holding pre-gzipped assets."""
    d = tmp_path / "dist"
    d.mkdir()
    names = names if names is not None else list(wh.ASSETS)
    for i, name in enumerate(names):
        n = (sizes or {}).get(name, 1000 + i * 37)
        # Not a repeating byte: a length bug that drops or doubles a chunk is
        # invisible in a run of identical bytes.
        (d / (name + ".gz")).write_bytes(bytes((i * 7 + j) % 251
                                               for j in range(n)))
    return d


def _generate(tmp_path, dist, **kw):
    out = tmp_path / "moy_web_blob.gen.c"
    rc = gwb.main(["--dist", str(dist), "--out", str(out)]
                  + (["--require"] if kw.get("require") else []))
    return rc, out


# -- the list, and the one that must equal it --------------------------------

def test_what_is_baked_is_what_the_server_serves():
    """Two lists would be one 404 on a board that has the bytes, or dead weight
    in a 5MB slot. The generator reads moy_webhost.ASSETS itself; this pins
    that it is still that module's list and not a copy that drifted."""
    assert gwb.asset_names() == list(wh.ASSETS)
    assert "micropython.wasm" in gwb.asset_names()


def test_the_gz_is_what_gets_baked_when_there_is_one(tmp_path):
    """Raw does not fit: the four assets are 1,155,953 B raw against 572,693 B
    gzipped, and the T-Deck's slot had ~765KB free."""
    d = _dist(tmp_path)
    (d / "index.html").write_bytes(b"raw copy, also present")
    path, served = gwb.pick(str(d), "index.html")
    assert served == "index.html.gz" and path.endswith(".gz")


def test_a_raw_only_bundle_still_bakes(tmp_path):
    """The gz is a build step of the web runner, not a law -- and the server's
    lookup handles both, so the generator must too."""
    d = tmp_path / "dist"
    d.mkdir()
    for name in wh.ASSETS:
        (d / name).write_bytes(b"x" * 10)
    assets, missing = gwb.collect(str(d))
    assert not missing
    assert [a[0] for a in assets] == list(wh.ASSETS)


# -- the generated C, compiled -----------------------------------------------

def _compile_and_read(tmp_path, gen_c):
    """Compile the generated file with the host compiler and dump the table.

    This is the only place the baked bundle is exercised as CODE. `.incbin` is
    an assembler feature, so what the host toolchain does here is what the
    Xtensa and RISC-V ones do on the boards.
    """
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:                       # pragma: no cover -- toolchain-less CI
        pytest.skip("no host C compiler")
    drv = tmp_path / "drv.c"
    drv.write_text(
        '#include <stdio.h>\n'
        '#include "moy_web_blob.h"\n'
        'int main(void) {\n'
        '    printf("%s|%u\\n", moy_web_stamp, moy_web_asset_count);\n'
        '    for (unsigned i = 0; i < moy_web_asset_count; i++) {\n'
        '        const moy_web_asset_t *a = &moy_web_assets[i];\n'
        '        printf("%s|%u|%lu\\n", a->name, a->len,\n'
        '               (unsigned long)((size_t)a->data & 3u));\n'
        '        fwrite(a->data, 1, a->len, stderr);\n'
        '    }\n'
        '    return 0;\n'
        '}\n')
    exe = tmp_path / "drv"
    r = subprocess.run(
        [cc, "-Wall", "-Werror", "-O1", "-I", str(MODULE), "-I", str(tmp_path),
         str(drv), str(gen_c), "-o", str(exe)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    run = subprocess.run([str(exe)], capture_output=True)
    assert run.returncode == 0, run.stderr[:400]
    lines = run.stdout.decode().strip().split("\n")
    stamp, count = lines[0].split("|")
    table = [ln.split("|") for ln in lines[1:]]
    return stamp, int(count), table, run.stderr


def test_the_baked_bytes_come_back_byte_for_byte(tmp_path):
    """The whole point. A wrong length here is a wasm the browser refuses to
    instantiate, and it fails only in a real browser on a real board."""
    d = _dist(tmp_path)
    rc, gen_c = _generate(tmp_path, d)
    assert rc == 0
    stamp, count, table, blob = _compile_and_read(tmp_path, gen_c)

    assert count == len(wh.ASSETS)
    expect = b""
    for name in wh.ASSETS:
        expect += (d / (name + ".gz")).read_bytes()
    assert blob == expect, "the embedded bytes are not the bundle's"
    assert [row[0] for row in table] == [n + ".gz" for n in wh.ASSETS]
    for row, name in zip(table, wh.ASSETS):
        assert int(row[1]) == (d / (name + ".gz")).stat().st_size
        assert row[2] == "0", "%s is not 4-byte aligned" % row[0]
    assert stamp.startswith("%d %d " % (count, len(expect)))


def test_an_image_with_no_bundle_still_compiles_and_reports_nothing(tmp_path):
    """A firmware built with no web bundle available must still LINK -- the
    module exists and says it has nothing, so moy_webhost falls through to its
    404. An ImportError in the middle of a request is the other design, and it
    fails at the worst moment."""
    empty = tmp_path / "empty"
    empty.mkdir()
    rc, gen_c = _generate(tmp_path, empty)
    assert rc == 0, "a missing bundle must not fail a local build"
    stamp, count, table, blob = _compile_and_read(tmp_path, gen_c)
    assert count == 0 and blob == b""
    assert stamp == "0 0 none"


def test_a_half_built_bundle_is_no_bundle(tmp_path):
    """ALL OR NOTHING. A console missing its wasm is not a partial console --
    it is a page that dies at boot with an error nobody will read."""
    d = _dist(tmp_path)
    (d / "micropython.wasm.gz").unlink()
    assets, missing = gwb.collect(str(d))
    assert assets == [] and missing == ["micropython.wasm"]


def test_require_turns_a_missing_bundle_into_a_failed_build(tmp_path, capsys):
    """What CI sets. A published image with no console is exactly the silent
    drift baking it was meant to end, so there it fails; a local flash gets the
    warning, because building the bundle needs emsdk (~1.7GB) and flashing is
    the daily loop."""
    empty = tmp_path / "empty"
    empty.mkdir()
    rc, _ = _generate(tmp_path, empty, require=True)
    assert rc == 1
    err = capsys.readouterr().err
    assert "web_runner/build.sh" in err, "the failure must name the fix"


def test_the_env_switch_is_the_same_lever(tmp_path, monkeypatch):
    monkeypatch.setenv("MOYBYTE_REQUIRE_WEB_BUNDLE", "1")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _generate(tmp_path, empty)[0] == 1
    # ...and an explicit 0 opts out even under CI, which is the escape hatch a
    # broken wasm toolchain needs to still ship a board image.
    monkeypatch.setenv("MOYBYTE_REQUIRE_WEB_BUNDLE", "0")
    monkeypatch.setenv("CI", "true")
    assert _generate(tmp_path, empty)[0] == 0


def test_a_missing_bundle_is_never_quiet(tmp_path, capsys):
    """The worst outcome of the three is a silent skip that produces a board
    with no console."""
    empty = tmp_path / "empty"
    empty.mkdir()
    _generate(tmp_path, empty)
    err = capsys.readouterr().err
    assert "NO BROWSER CONSOLE" in err
    assert "web_runner/build.sh" in err


# -- the stamp, and why it is code ------------------------------------------

def test_the_stamp_is_a_string_in_the_source_not_a_comment(tmp_path):
    """ccache hashes the PREPROCESSED source -- comments are gone by then, and
    `.incbin` content was never in the hash at all. If the bundle's digest rode
    only in a comment, a rebuild after a new web build would be a cache HIT and
    the image would keep serving the old console. Which is the exact bug this
    whole feature exists to end, reintroduced one level down.
    """
    d = _dist(tmp_path)
    _, gen_c = _generate(tmp_path, d)
    body = gen_c.read_text()
    stamped = [ln for ln in body.splitlines()
               if ln.startswith("const char moy_web_stamp")]
    assert len(stamped) == 1
    # Strip comments the way cpp would, and the digest must survive.
    digest = stamped[0].split('"')[1].split()[-1]
    assert len(digest) == 12
    assert digest in _strip_comments(body)


def _strip_comments(src):
    out, i = [], 0
    while i < len(src):
        if src.startswith("/*", i):
            i = src.find("*/", i) + 2
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def test_a_changed_bundle_changes_the_stamp(tmp_path):
    d = _dist(tmp_path)
    _, gen_c = _generate(tmp_path, d)
    first = gen_c.read_text()
    # Same SIZE, different bytes: a size-only stamp would miss this, and a
    # rebuilt wasm is usually about as big as the one it replaces.
    p = d / "micropython.wasm.gz"
    p.write_bytes(bytes((b + 1) % 251 for b in p.read_bytes()))
    _generate(tmp_path, d)
    assert gen_c.read_text() != first


def test_an_unchanged_bundle_does_not_touch_the_file(tmp_path):
    """Rewriting an identical file would relink every build for nothing."""
    d = _dist(tmp_path)
    _, gen_c = _generate(tmp_path, d)
    before = gen_c.stat().st_mtime_ns
    os.utime(gen_c, ns=(before - 10 ** 9, before - 10 ** 9))
    stale = gen_c.stat().st_mtime_ns
    _generate(tmp_path, d)
    assert gen_c.stat().st_mtime_ns == stale


def test_a_bundle_older_than_the_console_says_so(tmp_path):
    """The staleness baking CANNOT fix. `p4_push_web` compares dist against the
    board, so a dist that is itself behind `runtime/` pushes -- and now bakes --
    a stale console and reports success. That is the original bug wearing the
    fix's clothes, so the build says it out loud."""
    d = _dist(tmp_path)
    assets, _ = gwb.collect(str(d))
    root = tmp_path / "root"
    (root / "runtime").mkdir(parents=True)
    (root / "firmware" / "web_runner").mkdir(parents=True)
    old = (root / "runtime" / "console.py")
    old.write_text("x")
    os.utime(old, (1, 1))
    assert gwb.stale_sources(assets, root=str(root)) == []
    newer = root / "runtime" / "wm_windowed.py"
    newer.write_text("x")                       # written now: after the bundle
    assert gwb.stale_sources(assets, root=str(root)) == [
        "runtime/wm_windowed.py"]


def test_staleness_is_a_warning_and_never_a_failure(tmp_path, capsys):
    """mtimes are a heuristic -- a fresh checkout reorders them and a touched
    file means nothing. A build that refuses on a heuristic is one people learn
    to work around."""
    d = _dist(tmp_path)
    for p in d.iterdir():
        os.utime(p, (1, 1))                     # the whole bundle is ancient
    rc, _ = _generate(tmp_path, d)
    assert rc == 0
    assert "OLDER than console sources" in capsys.readouterr().err


# -- the module, and the two builds that carry it ----------------------------

def test_the_generated_file_is_not_committed():
    """It carries this machine's absolute paths and a digest of build output.
    Tracking it would also mean every build dirties the tree, which
    `make release` refuses outright."""
    r = subprocess.run(["git", "check-ignore", "-q",
                        str(MODULE / "moy_web_blob.gen.c")], cwd=str(ROOT))
    assert r.returncode == 0, "moy_web_blob.gen.c must stay gitignored"


def test_the_two_build_twins_name_the_same_sources():
    """micropython.mk (unix port) and micropython.cmake (both boards) are the
    pair that drifted once already: moy_gfx's cmake gained sources its .mk
    never did, the unix port stopped linking, and nothing caught it."""
    mk = (MODULE / "micropython.mk").read_text()
    cm = (MODULE / "micropython.cmake").read_text()
    for src in ("modmoy_web.c", "moy_web_blob.gen.c"):
        assert src in mk, "%s missing from micropython.mk" % src
        assert src in cm, "%s missing from micropython.cmake" % src


def test_the_native_module_hands_out_a_read_only_view():
    """Zero-copy is the design: the blob is flash-mapped rodata and a `bytes`
    per request would be ~523KB on a board with ~23KB of internal SRAM free in
    play (#66). Read-only because a write to flash would fault -- moy_alloc's
    `typecode |= 0x80` is the opposite case, and copying that line here would
    turn a Python TypeError into a hardware exception."""
    c = (MODULE / "modmoy_web.c").read_text()
    assert "mp_obj_new_memoryview" in c
    assert "typecode |= 0x80" not in c, "the baked bundle must not be writable"


BOARDS = (
    ("lilygo_t_deck_plus_mainline", TDECK),
    ("esp32_p4_wifi6_touch_lcd_7b", P4),
)


@pytest.mark.parametrize("name,path", BOARDS, ids=[b for b, _ in BOARDS])
def test_every_board_bakes_the_console_in(name, path):
    """Per board, by name -- the same pin shape as "every board injects the web
    console", and for the same reason: the shared half can be perfect while one
    board quietly does not carry it, and a capability-gated feature that is
    absent reads as a feature that does not exist.
    """
    # The bake lives in the SHARED build lib since 2026-08-17 (both boards
    # call moybyte_stage_native, which generates the blob into the staged
    # copy), and the staging is board.toml's [native.shared] -- so the pin
    # follows: the board must reach the lib step, and must not DENY the module.
    sh = (path / "build.sh").read_text()
    lib = (ROOT / "tools" / "esp32_build_lib.sh").read_text()
    assert "moybyte_stage_native" in sh, "%s never bakes the bundle" % name
    assert "gen_web_blob.py" in lib
    from tools import board_config
    assert "moy_web" in board_config.native_modules(path, ROOT), (
        "%s never stages the module" % name)


def test_the_p4_usermod_list_includes_the_module():
    """Both boards' tracked cmake now includes ONE generated list
    (.staged/micropython.cmake, written by board_config.stage_native from
    board.toml), so the pin is that the generator emits the module's include
    for this board's declared set."""
    cm = (P4 / "native" / "micropython.cmake").read_text()
    assert ".staged/micropython.cmake" in cm
    from tools import board_config
    assert "moy_web" in board_config.native_modules(P4, ROOT)


@pytest.mark.parametrize("name,path", BOARDS, ids=[b for b, _ in BOARDS])
def test_an_image_too_big_for_its_slot_fails_the_build(name, path):
    """Baking ~573KB into the app leaves the T-Deck ~188KB of its 5MB slot. An
    overflow used to be a WARNING that still produced a full set of artifacts
    -- an image esptool refuses and no board can take over OTA. It fails now,
    on both boards (the P4 had no guard at all).
    """
    # The guard is ONE implementation in the shared build lib now
    # (moybyte_app_size_guard, 2026-08-17); each board must still CALL it.
    sh = (path / "build.sh").read_text()
    assert "moybyte_app_size_guard" in sh, "%s does not measure its slot" % name
    lib = (ROOT / "tools" / "esp32_build_lib.sh").read_text()
    # The OVERFLOW branch only -- everything from `headroom < 0` to the `elif`
    # that merely warns about a thin margin.
    head, _, rest = lib.partition('"${headroom}" -lt 0 ]; then')
    assert rest, "the build lib has no overflow branch"
    branch = rest.split("elif", 1)[0]
    assert "exit 1" in branch, "the size guard only warns about an oversized image"
    for word in ("slot", "OVERFLOW"):
        assert word in branch, (
            "the size guard's failure does not name the %s" % word)


def test_the_module_is_importable_by_the_generator_without_a_board():
    """`gen_web_blob` reaches moy_webhost through the device modules tree; if
    that import needs a board it cannot run at build time on the host."""
    spec = importlib.util.find_spec("moy_webhost")
    assert spec is not None


# -- the module under a REAL MicroPython -------------------------------------

def _mp():
    from unix_mp import require_unix_mp
    return require_unix_mp("moy_web", why=(
        "moy_web is the browser console baked into the firmware image. Its "
        "memoryview points at flash-mapped rodata and must stay READ-ONLY -- "
        "a write there is a hardware exception on a board, and this is the "
        "only lane that runs the module rather than reading it."))


def _mp_run(code):
    exe = _mp()
    r = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_the_native_module_serves_the_bytes_it_baked():
    """The compiled article, driven by a real VM: the table it exposes and the
    bytes behind it are the ones the generator recorded.

    This is a live-tree check -- it reads whatever bundle the local
    `firmware/web_runner/dist` last produced, which is exactly what the boards
    bake. With no bundle built the module reports zero assets, which is also a
    correct state and is asserted as such.
    """
    # binascii.hexlify, not hexdigest(): MicroPython's hashlib has no such
    # method, and this runs on the real VM, not CPython.
    out = _mp_run(
        "import moy_web, hashlib, binascii\n"
        "print(moy_web.stamp())\n"
        "for n in moy_web.assets():\n"
        "    b = moy_web.asset(n)\n"
        "    h = binascii.hexlify(hashlib.sha256(bytes(b)).digest())\n"
        "    print(n, len(b), h.decode()[:16])\n")
    lines = out.split("\n")
    count, total = int(lines[0].split()[0]), int(lines[0].split()[1])
    assert len(lines) == count + 1
    if count == 0:
        assert total == 0 and lines[0] == "0 0 none"
        return
    assets, _ = gwb.collect(str(ROOT / "firmware" / "web_runner" / "dist"))
    baked = {a[0]: (a[2], a[3][:16]) for a in assets}
    assert sum(v[0] for v in baked.values()) == total
    for line in lines[1:]:
        name, size, sha = line.split()
        assert baked[name] == (int(size), sha), name


def test_the_native_module_refuses_a_write_to_flash():
    """Read-only is not a nicety: `data` is a pointer into memory-mapped flash,
    so a store through that view is a fault on a board rather than a Python
    error. moy_alloc marks its views writable (`typecode |= 0x80`) and copying
    that line here is the mistake this pins."""
    out = _mp_run(
        "import moy_web\n"
        "n = moy_web.assets()\n"
        "if not n:\n"
        "    print('EMPTY')\n"
        "else:\n"
        "    try:\n"
        "        moy_web.asset(n[0])[0] = 1\n"
        "        print('WRITABLE')\n"
        "    except TypeError:\n"
        "        print('READONLY')\n")
    assert out in ("READONLY", "EMPTY"), out


def test_an_unknown_asset_is_none_not_an_exception():
    """moy_webhost falls THROUGH on a miss (to the pushed copy, then to a 404
    that explains itself). An exception there would take out a request that has
    a perfectly good answer."""
    assert _mp_run("import moy_web\nprint(moy_web.asset('nope.gz'))") == "None"
