"""The browser console's pack and the digest the image names (tools/gen_web_blob.py).

The console is an add-on cart (native/moy_net/moy_page.h): every board's build
packs the web bundle into `moybyte_web.pack` and bakes into the image the
digest of that pack and the stamp of the bundle in it, so a board serves only
the page its firmware was built with and says "needs its page updated" for any
other.

The generated C file is the drift-prone half: build output nobody reads, and
if it named a digest other than the pack's, every board would call its own
page stale. So this compiles it with the HOST compiler and reads it back
against the pack the same run wrote.
"""

import gzip
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

import board_config as bc                                         # noqa: E402
import gen_web_blob as gwb                                        # noqa: E402

ASSETS = gwb.asset_names()


def _put(d, name, raw):
    """One asset the way build.sh leaves it: the file and its gzip beside it."""
    (d / name).write_bytes(raw)
    (d / (name + ".gz")).write_bytes(gzip.compress(raw, 9, mtime=0))


def _dist(tmp_path, names=None, sizes=None):
    """A fake `firmware/web_runner/dist`: every asset raw, plus its gzip."""
    d = tmp_path / "dist"
    d.mkdir()
    names = names if names is not None else list(ASSETS)
    for i, name in enumerate(names):
        n = (sizes or {}).get(name, 1000 + i * 37)
        # Not a repeating byte: a length bug that drops or doubles a chunk is
        # invisible in a run of identical bytes.
        _put(d, name, bytes((i * 7 + j * (i + 3)) % 251 for j in range(n)))
    return d


def _generate(tmp_path, dist, **kw):
    out = tmp_path / "moy_web_blob.gen.c"
    rc = gwb.main(["--dist", str(dist), "--out", str(out)]
                  + (["--require"] if kw.get("require") else []))
    return rc, out


# -- the list, and the one that must equal it --------------------------------

def test_what_is_packed_is_what_the_server_serves(tmp_path):
    """Two lists would be one 404 on a board that has the bytes, or dead weight
    in the pack. The generator reads the webhost's own table
    (native/moy_net/moy_webhost.c); this pins that every name it packs is one
    the server answers with its bytes, from the installed add-on."""
    from runtime import net_binding as nb
    assert "micropython.wasm" in ASSETS and "moy_store.mjs" in ASSETS
    carts = tmp_path / "carts"
    path = nb._bake({n + ".gz": n.encode() for n in ASSETS}, carts=carts)
    tag = Path(path).parent.name[len("web_console_"):-len(".moy")]
    try:
        nb.web_start(0, str(carts), None, None, None, ())
        for n in ASSETS:
            r, _ = nb.web_handle("GET", "/p/%s/%s" % (tag, n), b"")
            assert r.startswith(b"HTTP/1.1 200") and r.endswith(n.encode()), n
    finally:
        nb.web_stop()
        nb._bake({})


def test_the_gz_is_what_gets_baked_when_there_is_one(tmp_path):
    """Raw does not fit: the four assets are 1,155,953 B raw against 572,693 B
    gzipped, and the T-Deck's slot had ~765KB free."""
    d = _dist(tmp_path)
    path, served = gwb.pick(str(d), "index.html")
    assert served == "index.html.gz" and path.endswith(".gz")


def test_a_raw_only_bundle_still_bakes(tmp_path):
    """The gz is a build step of the web runner, not a law -- and the server's
    lookup handles both, so the generator must too."""
    d = tmp_path / "dist"
    d.mkdir()
    for name in ASSETS:
        (d / name).write_bytes(b"x" * 10)
    assets, missing = gwb.collect(str(d))
    assert not missing
    assert [a[0] for a in assets] == list(ASSETS)


# -- the generated C, compiled -----------------------------------------------

def _compile_and_read(tmp_path, gen_c):
    """Compile the generated file with the host compiler and read back what
    the image names: (named, stamp, want hex)."""
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:                       # pragma: no cover -- toolchain-less CI
        pytest.skip("no host C compiler")
    drv = tmp_path / "drv.c"
    drv.write_text(
        '#include <stdio.h>\n'
        '#include "moy_web_blob.h"\n'
        'int main(void) {\n'
        '    printf("%u|%s|", moy_web_named, moy_web_stamp);\n'
        '    for (int i = 0; i < 32; i++) printf("%02x", moy_web_want[i]);\n'
        '    printf("\\n");\n'
        '    return 0;\n'
        '}\n')
    exe = tmp_path / "drv"
    r = subprocess.run(
        [cc, "-Wall", "-Werror", "-O1", "-I", str(MODULE), "-I", str(tmp_path),
         str(drv), str(gen_c), "-o", str(exe)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr[:400]
    named, stamp, want = run.stdout.strip().split("|")
    return int(named), stamp, want


def _read_pack(path):
    """The pack's head (native/moy_net/moy_page.h): its digest and {name: bytes}."""
    import hashlib
    import struct
    data = Path(path).read_bytes()
    assert data[:8] == b"MOYWEB01"
    count, size = struct.unpack_from("<II", data, 8)
    digest = data[16:48]
    assert len(data) == size
    assert hashlib.sha256(data[4096:]).digest() == digest
    out = {}
    for i in range(count):
        name, off, n = struct.unpack_from("<40sII", data, 48 + i * 48)
        assert off % 4 == 0, "%r is not 4-byte aligned" % name
        out[name.rstrip(b"\0").decode()] = data[off:off + n]
    return digest.hex(), out


def test_the_image_names_the_pack_its_build_wrote(tmp_path):
    """The whole point. A digest that is not the pack's is a board that calls
    its own add-on stale; a wrong length in the pack is a wasm the browser
    refuses to instantiate, and it fails only in a real browser."""
    d = _dist(tmp_path)
    pack = tmp_path / "out" / "moybyte_web.pack"
    out = tmp_path / "moy_web_blob.gen.c"
    assert gwb.main(["--dist", str(d), "--out", str(out), "--pack", str(pack)]) == 0
    named, stamp, want = _compile_and_read(tmp_path, out)
    digest, assets = _read_pack(pack)
    assert named == 1 and want == digest
    assert list(assets) == [n + ".gz" for n in ASSETS]
    for name in ASSETS:
        assert assets[name + ".gz"] == (d / (name + ".gz")).read_bytes(), name
    total = sum(len(v) for v in assets.values())
    assert stamp.startswith("%d %d " % (len(ASSETS), total))


def test_an_image_with_no_bundle_still_compiles_and_names_nothing(
        tmp_path, monkeypatch):
    """A firmware built with no web bundle available must still LINK -- the
    module exists and names no page, so the webhost answers its 404 and no
    add-on fits. This is the LOCAL path, so the levers that flip it strict are
    cleared: a real CI runner exports CI=true."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MOYBYTE_REQUIRE_WEB_BUNDLE", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    rc, gen_c = _generate(tmp_path, empty)
    assert rc == 0, "a missing bundle must not fail a local build"
    named, stamp, want = _compile_and_read(tmp_path, gen_c)
    assert named == 0 and stamp == "0 0 none" and want == "00" * 32


def test_a_half_built_bundle_is_no_bundle(tmp_path):
    """ALL OR NOTHING. A console missing its wasm is not a partial console --
    it is a page that dies at boot with an error nobody will read."""
    d = _dist(tmp_path)
    (d / "micropython.wasm").unlink()
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


def test_a_missing_bundle_is_never_quiet(tmp_path, capsys, monkeypatch):
    """The worst outcome of the three is a silent skip that produces a board
    with no console. (Local warn path -- see the no-bundle test above for why
    the strict levers are cleared.)"""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MOYBYTE_REQUIRE_WEB_BUNDLE", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    _generate(tmp_path, empty)
    err = capsys.readouterr().err
    assert "NO BROWSER CONSOLE" in err
    assert "web_runner/build.sh" in err


# -- what counts as the bundle: bytes, never timestamps ----------------------

def _empty_table(gen_c):
    text = gen_c.read_text()
    return '"0 0 none"' in text and "moy_web_named = 0u" in text


def test_a_gz_left_behind_by_a_removed_bundle_is_not_a_bundle(
        tmp_path, monkeypatch, capsys):
    """Removing the console's files (the raw ones are what the web build
    copies in) left the pre-gzipped copies behind, and the generator baked
    those: a 790 KB console nobody had built in a while, in an image rebuilt
    to be rid of it. The raw file is the bundle; its gzip is derived."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MOYBYTE_REQUIRE_WEB_BUNDLE", raising=False)
    d = _dist(tmp_path)
    for name in ASSETS:
        (d / name).unlink()
    assets, missing = gwb.collect(str(d))
    assert assets == [] and missing == list(ASSETS)
    rc, gen_c = _generate(tmp_path, d)
    assert rc == 0 and _empty_table(gen_c)
    err = capsys.readouterr().err
    assert "only its .gz is there" in err and "NO BROWSER CONSOLE" in err
    assert _generate(tmp_path, d, require=True)[0] == 1


def test_a_gz_that_is_not_the_files_gzip_is_not_a_bundle(tmp_path):
    """A build that stopped between the raw copy and the gzip leaves the old
    gzip beside a new file. Preferring it bakes the old console; so does one
    that is not a gzip at all, and neither may raise out of the generator."""
    d = _dist(tmp_path)
    (d / "worker.js").write_bytes(b"a new worker, its .gz not yet remade")
    (d / "moy_store.mjs.gz").write_bytes(b"not gzip data at all")
    assets, missing = gwb.collect(str(d))
    assert assets == [] and set(missing) == {"worker.js", "moy_store.mjs"}
    assert "not the gzip" in gwb.diagnose(str(d), "worker.js")
    assert "not the gzip" in gwb.diagnose(str(d), "moy_store.mjs")


def test_the_bytes_decide_not_the_timestamps(tmp_path):
    """A bundle restored from a backup (everything ancient), a gz newer than
    the file it no longer matches, and a gz older than a file it does match:
    the first two are decided by content, the last is a bundle."""
    d = _dist(tmp_path)
    _, gen_c = _generate(tmp_path, d)
    first = gen_c.read_text()
    # Other bytes, ancient mtimes (what `cp -p` from an older build brings).
    _put(d, "micropython.wasm", b"\0asm an older console")
    for p in (d / "micropython.wasm", d / "micropython.wasm.gz"):
        os.utime(p, (1, 1))
    assert _generate(tmp_path, d)[0] == 0
    assert gen_c.read_text() != first, "an old mtime kept the old blob"
    assert not _empty_table(gen_c)
    # The gz is NEWER than the raw file and does not match it.
    os.utime(d / "micropython.wasm", (1, 1))
    (d / "micropython.wasm").write_bytes(b"\0asm yet another console")
    os.utime(d / "micropython.wasm", (1, 1))
    assert "micropython.wasm" in gwb.collect(str(d))[1]
    # The gz is OLDER than the raw file and matches it.
    _put(d, "micropython.wasm", b"\0asm and the one after")
    os.utime(d / "micropython.wasm.gz", (1, 1))
    assert gwb.collect(str(d))[1] == []


def test_a_removed_bundle_rewrites_the_baked_file(tmp_path, monkeypatch):
    """The generated file is the only thing the image build reads, so it has
    to follow the bundle down to nothing: removed dist, same out path, an
    out file that is itself newer than anything that fed it."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("MOYBYTE_REQUIRE_WEB_BUNDLE", raising=False)
    d = _dist(tmp_path)
    _, gen_c = _generate(tmp_path, d)
    assert not _empty_table(gen_c)
    future = gen_c.stat().st_mtime + 3600
    os.utime(gen_c, (future, future))
    shutil.rmtree(d)
    rc, again = _generate(tmp_path, d)
    assert rc == 0 and again == gen_c and _empty_table(gen_c)


# -- the stamp, and why it is code ------------------------------------------

def test_the_stamp_is_a_string_in_the_source_not_a_comment(tmp_path):
    """ccache hashes the PREPROCESSED source -- comments are gone by then. If
    the bundle's digest rode
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
    raw = d / "micropython.wasm"
    _put(d, "micropython.wasm", bytes((b + 1) % 251 for b in raw.read_bytes()))
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
    """The staleness baking CANNOT fix. Baking makes a board's console current
    with its own firmware, but a dist that is itself behind `runtime/` bakes a
    stale console and reports success -- so the build says it out loud."""
    d = _dist(tmp_path)
    assets, _ = gwb.collect(str(d))
    root = tmp_path / "root"
    (root / "firmware" / "web_runner").mkdir(parents=True)
    old_rel, new_rel = (bc.runtime_file(n).relative_to(ROOT).as_posix()
                        for n in ("console.py", "wm_windowed.py"))
    old = root / old_rel
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("x")
    os.utime(old, (1, 1))
    assert gwb.stale_sources(assets, root=str(root)) == []
    newer = root / new_rel
    newer.parent.mkdir(parents=True, exist_ok=True)
    newer.write_text("x")                       # written now: after the bundle
    assert gwb.stale_sources(assets, root=str(root)) == [new_rel]


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


def test_the_watch_list_is_derived_from_the_web_runner_board_file():
    """It used to be a hand-written `runtime/*.py` glob, which could not see
    `device/` -- and `device/device_canvas.py` IS the class the browser
    rasterizes with since moycore stage 4. The one file whose staleness matters
    most was the one file the check was structurally blind to.

    So the set comes from the same staging declaration the build stages FROM
    (firmware/web_runner/board.toml, #161), plus the runner's own authored
    modules and page, which build.sh copies by name."""
    watched = set(gwb.watched_sources())
    assert "device/device_canvas.py" in watched
    for src in bc.staged_modules(str(ROOT / "firmware" / "web_runner")).values():
        rel = os.path.relpath(str(src), str(ROOT))
        assert rel in watched, "%s is staged into the bundle and unwatched" % rel
    for name in ("web_boot.py", "web_canvas.py"):
        assert "firmware/web_runner/" + name in watched
    # A denied module is not a source of this bundle, and the glob watched it.
    assert "runtime/host/host_app.py" not in watched


def test_a_stale_device_canvas_is_reported(tmp_path):
    """The regression itself: `device/` newer than the bundle now says so."""
    d = _dist(tmp_path)
    for p in d.iterdir():
        os.utime(p, (1, 1))          # ancient bundle: mtimes here tie otherwise
    assets, _ = gwb.collect(str(d))
    root = tmp_path / "root"
    (root / "device").mkdir(parents=True)
    (root / "device" / "device_canvas.py").write_text("x")
    assert gwb.stale_sources(assets, root=str(root)) == [
        "device/device_canvas.py"]


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


BOARDS = (
    ("lilygo_t_deck_plus_mainline", TDECK),
    ("esp32_p4_wifi6_touch_lcd_7b", P4),
    ("guition_jc8012p4a1c", ROOT / "firmware" / "guition_jc8012p4a1c"),
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
    """An overflow used to be a WARNING that still produced a full set of artifacts
    -- an image esptool refuses and no board can take over OTA. It fails now,
    on both boards (the P4 had no guard at all).
    """
    # The guard is ONE implementation in the shared build lib
    # (moybyte_app_size_guard, 2026-08-17), and since 2026-08-18 each board
    # reaches it through the shared build+collect step -- so the pin is that
    # the board runs that step, and that the step runs the guard.
    sh = (path / "build.sh").read_text()
    assert "moybyte_build_and_collect" in sh, \
        "%s does not run the shared build+collect step" % name
    lib = (ROOT / "tools" / "esp32_build_lib.sh").read_text()
    collect_body = lib.partition("moybyte_build_and_collect() {")[2]
    assert "moybyte_app_size_guard" in collect_body.partition("\n}")[0], \
        "the shared build+collect step no longer measures the slot"
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


def test_make_test_refreshes_the_binary_the_next_section_reads():
    """The checks below read the LIVE tree: they hash whatever
    `firmware/web_runner/dist` last produced against what the binary BAKED. So
    every web-runner build leaves a previously-built desktop MicroPython stale
    and this file red until somebody runs `make unix-micropython` by hand --
    which cost four separate red gates in one night on the #209 program.

    `make test` therefore runs that target itself. It is the same reasoning the
    target states about its own missing cache ("a cache MISS that skips the
    check is the bug"), one level up, and it is affordable for the same reason:
    0.4s warm.

    BOTH halves are pinned, because the second is what keeps the first
    harmless: the refresh must never take the host suite down with it. A
    machine with no C toolchain still runs `make test`, and tests/unix_mp.py is
    the thing that knows the difference between that machine and CI.
    """
    body = (ROOT / "Makefile").read_text()
    after = body.partition("\ntest:\n")[2]
    assert after, "the Makefile has no `test` rule"
    recipe = []
    for line in after.splitlines():
        if not line.startswith("\t"):
            break
        recipe.append(line)
    recipe = "\n".join(recipe)
    assert "-m pytest" in recipe
    assert "unix-micropython" in recipe, \
        "`make test` no longer refreshes the desktop MicroPython it checks"
    refresh = recipe.partition("unix-micropython")[2]
    assert refresh.lstrip().startswith("||"), \
        "the refresh must fall through on failure, not fail the test run"


# -- the module under a REAL MicroPython -------------------------------------

def _mp():
    from unix_mp import require_unix_mp
    return require_unix_mp("moy_web", why=(
        "moy_web names the browser console page this image serves. It "
        "is the only lane that runs the module rather than reading it."))


def _mp_run(code):
    exe = _mp()
    r = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_the_native_module_names_the_bundle_it_was_built_with():
    """The compiled article, driven by a real VM: the digest and stamp it
    answers are those of the bundle the local `firmware/web_runner/dist` last
    produced, which is exactly what the boards are built with. With no bundle
    built the module names none, which is also a correct state."""
    out = _mp_run("import moy_web\nprint(moy_web.stamp())\nprint(moy_web.page_want())\n")
    stamp, want = out.split("\n")
    if want == "None":
        assert stamp == "0 0 none"
        return
    assets, missing = gwb.collect(str(ROOT / "firmware" / "web_runner" / "dist"))
    # THE BINARY IS CACHED AND THE BUNDLE IS NOT: `require_unix_mp` returns
    # whatever `make unix-micropython` last built and never rebuilds it.
    stale = ("this binary was built against a DIFFERENT bundle than "
             "firmware/web_runner/dist holds now -- re-run `make "
             "unix-micropython` after building the bundle.")
    assert assets and not missing, stale
    assert gwb.pack(assets)[1].hex() == want, stale
    assert stamp == gwb.stamp_of(assets), stale
