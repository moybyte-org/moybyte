"""`tools/esp32_build_lib.sh`, EXECUTED (#208 rank 6).

Four hundred lines that every board build sources, and until now the only nets
over it were two substring assertions -- `"moybyte_sdkconfig_guard" in
build_script` -- which say a board still CALLS the guard and nothing about what
the guard does. Its blast radius grew on 2026-08-22, when it stopped being
handed a hand-typed list of required settings and started deriving one from each
board's `sdkconfig.board`: a bug in it now silently mis-configures three boards
at once, and the failure mode it exists to catch (a setting that reads as
decided and does nothing) is invisible in a built image.

The library is SOURCED, not executed, and its functions take their inputs as
positional arguments and named environment variables. So the shell itself runs
here, in bash, against temp trees -- nothing is transcribed into Python.

WHAT CANNOT RUN HERE, said out loud rather than left as a gap:

  * `moybyte_clone_micropython` / `moybyte_setup_idf` / `moybyte_build_and_collect`
    clone ~500MB and invoke a cross toolchain.
  * the APPLY half of the three `patch` helpers needs the real upstream tree the
    diffs were cut against; their GUARD half (the idempotence that makes a warm
    rebuild a no-op) is what runs below.
  * `moybyte_stage_native` is `board_config.py stage-native` plus the web blob,
    both of which have their own suites (`test_staging_closure.py`,
    `test_gen_web_blob.py`), and running it writes into a board's staging tree.

`moybyte_patch_repr_c` IS reachable, and matters most of the three: REPR_C is
part of the netplay lockstep contract (a board that cannot take it cannot join a
match), and its own comment says the guard matters more than the edit.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "tools" / "esp32_build_lib.sh"

FRAGMENT = """\
# The flash this board actually carries.
CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y
CONFIG_ESPTOOLPY_FLASHMODE_QIO=

# The dual-OTA table (#53). Resolves relative to ports/esp32.
CONFIG_PARTITION_TABLE_CUSTOM=y
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-test.csv"

# Rollback, so a bad image self-heals.
CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y
CONFIG_BT_HCI_LOG_DEBUG_EN=n

# The tick the frame pacing assumes.
CONFIG_FREERTOS_HZ=100
"""

CSV = """\
# Name,   Type, SubType, Offset,   Size
nvs,      data, nvs,     0x9000,   0x6000,
otadata,  data, ota,     0xd000,   0x2000,
factory,  app,  factory, 0x20000,  0x100000,
ota_0,    app,  ota_0,   0x20000,  0x400000,
ota_1,    app,  ota_1,   0x420000, 0x400000,
"""


def _env(**over):
    """A clean environment: the real one may carry `CI`, which is one of the
    switches under test."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.environ.get("HOME", "/tmp"),
           "LANG": "C"}
    env.update({k: str(v) for k, v in over.items()})
    return env


def sh(script, **env):
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\nsource '%s'\n%s" % (LIB, script)],
        cwd=str(ROOT), env=_env(**env), capture_output=True, text=True)


@pytest.fixture
def board(tmp_path):
    """A board def dir + a MicroPython tree, in the shapes the guard reads."""
    bd = tmp_path / "boards" / "MOYBYTE_TEST"
    bd.mkdir(parents=True)
    (bd / "sdkconfig.board").write_text(FRAGMENT, encoding="utf-8")
    (bd / "mpconfigboard.cmake").write_text("set(IDF_TARGET esp32s3)\n",
                                            encoding="utf-8")
    (bd / "partitions-test.csv").write_text(CSV, encoding="utf-8")
    mpy = tmp_path / "mpy"
    (mpy / "ports" / "esp32").mkdir(parents=True)
    return bd, mpy


def guard(board, mpy, gen, tag="v1.28", **env):
    return sh("moybyte_sdkconfig_guard '%s' '%s'; echo \"CSV=${BOARD_PARTITION_CSV}\""
              % (board, gen),
              REPO_ROOT=str(ROOT), MPY_DIR=str(mpy), MPY_TAG=tag,
              BUILD_PYTHON=sys.executable, **env)


# -- the build interpreter ------------------------------------------------------


def test_the_build_python_prefers_the_venv_but_needs_no_venv(tmp_path):
    """`board_config.py` is stdlib-only ON PURPOSE so a board is buildable with
    nothing but the system python3; that promise is this function."""
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    venv = tmp_path / ".venv" / "bin" / "python"
    venv.write_text("#!/bin/sh\n", encoding="utf-8")
    venv.chmod(0o755)
    r = sh("moybyte_resolve_build_python; echo \"${BUILD_PYTHON}\"",
           REPO_ROOT=str(tmp_path))
    assert r.stdout.strip() == str(venv)

    r = sh("moybyte_resolve_build_python; echo \"${BUILD_PYTHON}\"",
           REPO_ROOT=str(tmp_path / "nope"))
    assert r.stdout.strip() == "python3"

    r = sh("moybyte_resolve_build_python; echo \"${BUILD_PYTHON}\"",
           REPO_ROOT=str(tmp_path), MOYBYTE_BUILD_PYTHON="/usr/bin/python3.11")
    assert r.stdout.strip() == "/usr/bin/python3.11"


# -- the IDF component list -----------------------------------------------------


def _common_cmake(tmp_path, body):
    p = tmp_path / "ports" / "esp32"
    p.mkdir(parents=True)
    (p / "esp32_common.cmake").write_text(body, encoding="utf-8")
    return p / "esp32_common.cmake"


def test_a_component_is_appended_to_the_ports_own_list(tmp_path):
    """USER_C_MODULES is skipped during idf.py's early expansion -- exactly when
    REQUIRES are collected -- so the port's list is the only place this can go."""
    f = _common_cmake(tmp_path, "list(APPEND IDF_COMPONENTS\n    esp_lcd\n)\n")
    sh("moybyte_idf_component esp_wifi_remote", MPY_DIR=str(tmp_path))
    assert f.read_text(encoding="utf-8") == \
        "list(APPEND IDF_COMPONENTS\n    esp_wifi_remote\n    esp_lcd\n)\n"


def test_appending_the_same_component_twice_is_a_no_op(tmp_path):
    f = _common_cmake(tmp_path, "list(APPEND IDF_COMPONENTS\n    esp_lcd\n)\n")
    for _ in range(3):
        sh("moybyte_idf_component esp_lcd", MPY_DIR=str(tmp_path))
    assert f.read_text(encoding="utf-8").count("esp_lcd") == 1


def test_a_component_whose_name_prefixes_another_is_still_added(tmp_path):
    """The guard is a whole-line match; `esp_wifi` must not read as already
    present because `esp_wifi_remote` is."""
    f = _common_cmake(tmp_path,
                      "list(APPEND IDF_COMPONENTS\n    esp_wifi_remote\n)\n")
    sh("moybyte_idf_component esp_wifi", MPY_DIR=str(tmp_path))
    assert "\n    esp_wifi\n" in f.read_text(encoding="utf-8")


# -- REPR_C: the float width the lockstep contract rests on ---------------------


def _mpconfig(tmp_path, line):
    p = tmp_path / "ports" / "esp32"
    p.mkdir(parents=True)
    (p / "mpconfigport.h").write_text("#define X 1\n%s\n" % line, encoding="utf-8")
    return p / "mpconfigport.h"


def test_the_repr_C_patch_rewrites_the_object_representation(tmp_path):
    f = _mpconfig(tmp_path, "#define MICROPY_OBJ_REPR    (MICROPY_OBJ_REPR_A)")
    r = sh("moybyte_patch_repr_c", MPY_DIR=str(tmp_path))
    assert r.returncode == 0
    assert "MICROPY_OBJ_REPR_C" in f.read_text(encoding="utf-8")
    assert "MICROPY_OBJ_REPR_A" not in f.read_text(encoding="utf-8")


def test_the_repr_C_patch_is_idempotent_on_a_warm_tree(tmp_path):
    f = _mpconfig(tmp_path, "#define MICROPY_OBJ_REPR    (MICROPY_OBJ_REPR_C)")
    before = f.read_text(encoding="utf-8")
    r = sh("moybyte_patch_repr_c", MPY_DIR=str(tmp_path))
    assert r.returncode == 0 and f.read_text(encoding="utf-8") == before


def test_a_repr_line_that_changed_shape_FAILS_rather_than_no_ops(tmp_path):
    """The guard is the point: a silent no-op is a board quietly running boxed
    floats again, which costs a 130-175ms GC hitch -- and, since the ESP-NOW
    work, makes that board unable to hold a match at all, because the two sims
    would diverge on float width alone."""
    f = _mpconfig(tmp_path, "#define MICROPY_OBJ_REPR (MICROPY_OBJ_REPR_B)")
    r = sh("moybyte_patch_repr_c", MPY_DIR=str(tmp_path))
    assert r.returncode != 0
    assert "REPR_C patch did not apply" in r.stderr
    assert "MICROPY_OBJ_REPR_B" in f.read_text(encoding="utf-8")


def test_the_PSRAM_retune_patch_is_inert_where_the_file_does_not_exist(tmp_path):
    """S3-only: it patches the S3 port of the MSPI timing tuner, so on the P4
    there is nothing to patch and that must be silence, not a failure."""
    (tmp_path / "components").mkdir()
    r = sh("moybyte_patch_psram_retune",
           IDF_DIR=str(tmp_path), REPO_ROOT=str(ROOT))
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_the_native_code_free_patch_skips_an_already_patched_tree(tmp_path):
    f = _mpconfig(tmp_path, "#define moybyte_native_code_free 1")
    r = sh("moybyte_patch_native_code_free",
           MPY_DIR=str(tmp_path), REPO_ROOT=str(ROOT))
    assert r.returncode == 0 and r.stdout.strip() == ""
    assert "moybyte_native_code_free" in f.read_text(encoding="utf-8")


def test_the_espnow_ring_patch_skips_an_already_patched_tree(tmp_path):
    p = tmp_path / "ports" / "esp32"
    p.mkdir(parents=True)
    (p / "modespnow.c").write_text("/* Moybyte espnow_ring_race */\n",
                                   encoding="utf-8")
    r = sh("moybyte_patch_espnow_ring_race",
           MPY_DIR=str(tmp_path), REPO_ROOT=str(ROOT))
    assert r.returncode == 0 and r.stdout.strip() == ""


# -- the OTA identity stamp -----------------------------------------------------


def _ota_py(tmp_path, version=7, name='"0.9"'):
    p = tmp_path / "moy_ota.py"
    body = "FIRMWARE_VERSION = %d\n" % version
    if name is not None:
        body += "FIRMWARE_NAME = %s\n" % name
    p.write_text(body, encoding="utf-8")
    return p


def _stamp(tmp_path, ota, board="tdeck", **env):
    mods = tmp_path / "modules"
    dist = tmp_path / "dist"
    mods.mkdir(exist_ok=True)
    r = sh("moybyte_ota_identity %s '%s'" % (board, ota),
           MODULES_DIR=str(mods), DIST_DIR=str(dist), **env)
    assert r.returncode == 0, r.stderr
    ns = {}
    exec(compile((mods / "_ota_build.py").read_text(encoding="utf-8"),
                 "_ota_build.py", "exec"), ns)
    return r, ns, json.loads((dist / "ota_build.json").read_text(encoding="utf-8"))


def test_a_stable_build_is_stamped_with_the_release_name_not_the_counter(tmp_path):
    """`FIRMWARE_VERSION` is an opaque ordering int nobody reads; the update
    screen and the manifest show `FIRMWARE_NAME`."""
    _r, ns, js = _stamp(tmp_path, _ota_py(tmp_path, 7, '"0.9"'))
    assert (ns["CHANNEL"], ns["VERSION"], ns["LABEL"], ns["BOARD"]) == \
        ("stable", 7, "0.9", "tdeck")
    assert js == {"channel": "stable", "version": 7,
                  "label": "0.9", "board": "tdeck"}


@pytest.mark.xfail(strict=True, reason=(
    "BUG, found by this test and left unfixed (#208): the documented "
    "`${ota_name:-v${OTA_VERSION}}` fallback is UNREACHABLE. Every board's "
    "build.sh runs `set -euo pipefail`, so the grep that finds no "
    "FIRMWARE_NAME exits 1, pipefail carries it out of the pipeline, and the "
    "command-substitution assignment aborts the whole build -- silently, with "
    "an empty stderr. The `:-1` fallback on OTA_VERSION two lines up has the "
    "same shape."))
def test_a_release_with_no_name_falls_back_to_the_counter(tmp_path):
    _r, ns, _js = _stamp(tmp_path, _ota_py(tmp_path, 12, None))
    assert ns["LABEL"] == "v12"


def test_a_beta_stamps_the_build_epoch_so_every_publish_is_newer(tmp_path):
    """A beta has no release name, so its version IS the clock -- which is what
    makes an already-beta board see the next publish as an upgrade."""
    _r, ns, js = _stamp(tmp_path, _ota_py(tmp_path, 7),
                        MOYBYTE_OTA_CHANNEL="unstable")
    assert ns["CHANNEL"] == "unstable"
    assert ns["VERSION"] > 1_700_000_000            # an epoch, not the counter
    assert ns["LABEL"].startswith("beta ")
    assert js["version"] == ns["VERSION"]


def test_an_explicit_version_wins_over_both_channels(tmp_path):
    for channel in ("stable", "unstable"):
        _r, ns, _js = _stamp(tmp_path, _ota_py(tmp_path, 7),
                             MOYBYTE_OTA_CHANNEL=channel,
                             MOYBYTE_OTA_VERSION="4242")
        assert ns["VERSION"] == 4242


def test_the_board_is_inside_the_stamp_on_both_sides(tmp_path):
    """An OTA payload is an app-partition image for one architecture, so the
    board is part of the signed identity -- and CI reads the JSON back out of
    the artifact rather than re-deriving it. The two must agree."""
    _r, ns, js = _stamp(tmp_path, _ota_py(tmp_path), board="p4")
    assert ns["BOARD"] == js["board"] == "p4"


def test_the_stamp_creates_the_dist_directory_it_writes_into(tmp_path):
    """The P4's lives outside `firmware/`, and CI collected only the T-Deck's
    for a while -- every P4 beta then shipped under a manifest claiming the
    committed counter, so an already-beta P4 was never offered a newer one."""
    ota = _ota_py(tmp_path)
    dist = tmp_path / "nested" / "dist"
    r = sh("moybyte_ota_identity p4 '%s'" % ota,
           MODULES_DIR=str(tmp_path), DIST_DIR=str(dist))
    assert r.returncode == 0
    assert (dist / "ota_build.json").exists()


# -- the frozen manifest --------------------------------------------------------


def _manifest(tmp_path):
    mods = tmp_path / "modules"
    mods.mkdir(exist_ok=True)
    out = tmp_path / "manifest.py"
    r = sh("moybyte_frozen_manifest '%s'" % out, MODULES_DIR=str(mods))
    assert r.returncode == 0, r.stderr
    return mods, out.read_text(encoding="utf-8")


def test_the_manifest_freezes_the_board_tree_at_opt_3(tmp_path):
    mods, text = _manifest(tmp_path)
    assert 'include("$(PORT_DIR)/boards/manifest.py")' in text
    assert 'freeze("%s", opt=3)' % mods in text


def test_the_fingerprint_moves_when_any_frozen_source_changes(tmp_path):
    """ninja rests custom commands on identical manifest TEXT, so without this
    a changed .py silently ships as a stale .mpy."""
    mods = tmp_path / "modules"
    mods.mkdir()
    (mods / "console.py").write_text("A = 1\n", encoding="utf-8")
    _m, first = _manifest(tmp_path)
    (mods / "console.py").write_text("A = 2\n", encoding="utf-8")
    _m, second = _manifest(tmp_path)
    assert first != second

    (mods / "console.py").unlink()                  # a DELETED module too
    _m, third = _manifest(tmp_path)
    assert third not in (first, second)


def test_stale_bytecode_is_swept_out_of_the_staged_tree(tmp_path):
    """`modules/` is gitignored and never cleaned, and the freeze takes the
    whole DIRECTORY."""
    mods = tmp_path / "modules"
    (mods / "__pycache__").mkdir(parents=True)
    (mods / "__pycache__" / "old.pyc").write_bytes(b"stale")
    (mods / "moybyte" / "__pycache__").mkdir(parents=True)
    _manifest(tmp_path)
    assert not (mods / "__pycache__").exists()
    assert not (mods / "moybyte" / "__pycache__").exists()


# -- the sdkconfig guard: the partition table, named once -----------------------


def test_the_partition_table_is_read_out_of_the_setting_that_names_it(board):
    bd, mpy = board
    r = guard(bd, mpy, mpy / "build" / "sdkconfig")
    assert r.returncode == 0, r.stderr
    assert "CSV=%s" % (bd / "partitions-test.csv") in r.stdout
    staged = mpy / "ports" / "esp32" / "partitions-test.csv"
    assert staged.read_text(encoding="utf-8") == CSV


def test_a_fragment_naming_a_table_that_is_not_there_fails_the_build(board):
    bd, mpy = board
    (bd / "partitions-test.csv").unlink()
    r = guard(bd, mpy, mpy / "build" / "sdkconfig")
    assert r.returncode != 0
    assert "which is not in" in r.stderr


@pytest.mark.parametrize("missing", ["sdkconfig.board", "mpconfigboard.cmake"])
def test_a_board_def_missing_either_input_fails_by_name(board, missing):
    bd, mpy = board
    (bd / missing).unlink()
    r = guard(bd, mpy, mpy / "build" / "sdkconfig")
    assert r.returncode != 0
    assert missing in r.stderr


# -- the sdkconfig guard: is this tree stale? -----------------------------------


def _configured(board, tag="v1.28"):
    """The state a WARM rebuild starts in: the guard has run once against these
    inputs (so the stamp is down) and IDF has since generated an sdkconfig that
    carries every required line."""
    bd, mpy = board
    gen = mpy / "build" / "sdkconfig"
    r = guard(bd, mpy, gen, tag=tag)
    assert r.returncode == 0, r.stderr
    gen.write_text("\n".join([
        "CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y",
        "CONFIG_PARTITION_TABLE_CUSTOM=y",
        'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions-test.csv"',
        "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y",
        "CONFIG_FREERTOS_HZ=100",
        "# CONFIG_ESPTOOLPY_FLASHMODE_QIO is not set",
        "# CONFIG_BT_HCI_LOG_DEBUG_EN is not set",
    ]) + "\n", encoding="utf-8")
    return gen


def test_an_unchanged_tree_keeps_its_generated_config(board):
    bd, mpy = board
    gen = _configured(board)
    r = guard(bd, mpy, gen)
    assert r.returncode == 0
    assert gen.exists()
    assert "regenerating" not in r.stdout


def test_a_changed_setting_drops_the_generated_config(board):
    bd, mpy = board
    gen = _configured(board)
    (bd / "sdkconfig.board").write_text(
        FRAGMENT.replace("16MB=y", "8MB=y"), encoding="utf-8")
    r = guard(bd, mpy, gen)
    assert not gen.exists()
    assert "inputs changed" in r.stdout


def test_a_DELETED_setting_drops_it_too(board):
    """The reason this is a stamp and not a grep list. `082fb9e` hit exactly
    this, twice in one commit: a line removed from the fragment left a warm
    tree still carrying it, and nothing said so."""
    bd, mpy = board
    gen = _configured(board)
    lines = [l for l in FRAGMENT.splitlines(True)
             if "ROLLBACK" not in l]
    (bd / "sdkconfig.board").write_text("".join(lines), encoding="utf-8")
    r = guard(bd, mpy, gen)
    assert not gen.exists()
    assert "inputs changed" in r.stdout


def test_a_changed_board_cmake_drops_it(board):
    """`mpconfigboard.cmake` decides which UPSTREAM fragments the board pulls
    in -- CONFIG_SPIRAM_MODE_OCT is MicroPython's value, not ours -- so a change
    there changes settings this fragment never mentions."""
    bd, mpy = board
    gen = _configured(board)
    (bd / "mpconfigboard.cmake").write_text(
        "set(IDF_TARGET esp32s3)\nset(SDKCONFIG_DEFAULTS x)\n", encoding="utf-8")
    guard(bd, mpy, gen)
    assert not gen.exists()


def test_a_micropython_tag_bump_drops_it(board):
    bd, mpy = board
    gen = _configured(board, tag="v1.28")
    guard(bd, mpy, gen, tag="v1.29")
    assert not gen.exists()


def test_the_stamp_is_written_even_on_a_cold_tree(board):
    """No generated config yet: nothing to check, but the next run must be able
    to tell whether these were the inputs."""
    bd, mpy = board
    gen = mpy / "build" / "sdkconfig"
    guard(bd, mpy, gen)
    stamp = gen.parent / ".moybyte-sdkconfig-stamp"
    assert stamp.exists() and len(stamp.read_text(encoding="utf-8").strip()) == 32


# -- the sdkconfig guard: did Kconfig honour it? --------------------------------


def _inert(board, drop, **env):
    bd, mpy = board
    gen = _configured(board)
    gen.write_text("\n".join(l for l in gen.read_text(encoding="utf-8").splitlines()
                             if drop not in l) + "\n", encoding="utf-8")
    return guard(bd, mpy, gen, **env)


def test_a_setting_kconfig_refused_is_reported_with_the_boards_own_prose(board):
    """The fragment's comment block IS the explanation, so the report does not
    restate it -- it reads it out of the file that decided the setting."""
    r = _inert(board, "ROLLBACK")
    assert "asks for CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y" in r.stderr
    assert "so a bad image self-heals" in r.stderr
    assert "no such option" in r.stderr


def test_an_inert_setting_only_WARNS_locally(board):
    """A local build must still finish: the developer is often mid-edit, and
    failing here would make an unrelated build unbuildable."""
    r = _inert(board, "ROLLBACK")
    assert r.returncode == 0


@pytest.mark.parametrize("env", [{"CI": "true"},
                                 {"MOYBYTE_REQUIRE_SDKCONFIG": "1"}])
def test_an_inert_setting_FAILS_where_the_image_gets_published(board, env):
    r = _inert(board, "ROLLBACK", **env)
    assert r.returncode != 0
    assert "Kconfig refused" in r.stderr


def test_a_build_that_honoured_every_setting_says_nothing(board):
    bd, mpy = board
    gen = _configured(board)
    r = guard(bd, mpy, gen, CI="true")
    assert r.returncode == 0
    assert "asks for" not in r.stderr


def test_a_DISABLE_is_never_demanded_of_the_generated_config(board):
    """`CONFIG_X=` and `CONFIG_X=n` render as "is not set" -- or, for a hidden
    choice member, are absent entirely -- so grepping for the literal line
    false-alarms. `CONFIG_BT_HCI_LOG_DEBUG_EN=n` failed every CI p4 build on
    2026-08-25 while local builds only warned; both spellings are in the
    fragment above and neither may be asked for."""
    bd, mpy = board
    gen = _configured(board)
    gen.write_text("\n".join(l for l in gen.read_text(encoding="utf-8").splitlines()
                             if "is not set" not in l) + "\n", encoding="utf-8")
    r = guard(bd, mpy, gen, CI="true")
    assert r.returncode == 0, r.stderr


def test_a_longer_option_name_does_not_satisfy_a_shorter_one(board):
    """A whole-line match: `CONFIG_PARTITION_TABLE_CUSTOM=y` must not be read as
    present because `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=...` is."""
    bd, mpy = board
    gen = _configured(board)
    gen.write_text(gen.read_text(encoding="utf-8")
                   .replace("CONFIG_PARTITION_TABLE_CUSTOM=y\n", ""),
                   encoding="utf-8")
    r = guard(bd, mpy, gen, CI="true")
    assert r.returncode != 0
    assert "asks for CONFIG_PARTITION_TABLE_CUSTOM=y" in r.stderr


def test_a_value_that_merely_starts_the_same_does_not_satisfy_the_demand(board):
    """Where the whole-line match earns its keep: a build running the tick at
    1000Hz would otherwise read as carrying a demand for 100."""
    bd, mpy = board
    gen = _configured(board)
    gen.write_text(gen.read_text(encoding="utf-8")
                   .replace("CONFIG_FREERTOS_HZ=100", "CONFIG_FREERTOS_HZ=1000"),
                   encoding="utf-8")
    r = guard(bd, mpy, gen, CI="true")
    assert r.returncode != 0
    assert "asks for CONFIG_FREERTOS_HZ=100" in r.stderr


def test_the_value_is_matched_literally_not_as_a_pattern(board):
    """Filenames carry dots. A regex match would accept
    `partitions-testXcsv` for `partitions-test.csv`."""
    bd, mpy = board
    gen = _configured(board)
    gen.write_text(gen.read_text(encoding="utf-8")
                   .replace("partitions-test.csv", "partitions-testXcsv"),
                   encoding="utf-8")
    r = guard(bd, mpy, gen, CI="true")
    assert r.returncode != 0


# -- the #168 size guard --------------------------------------------------------


def _size_guard(tmp_path, nbytes, **env):
    csv = tmp_path / "partitions.csv"
    csv.write_text(CSV, encoding="utf-8")
    app = tmp_path / "app.bin"
    app.write_bytes(b"\0" * nbytes)
    return sh("moybyte_app_size_guard '%s' '%s'" % (csv, app), **env)


def test_the_slot_size_is_read_out_of_the_boards_own_table(tmp_path):
    """Read, not restated, so the check cannot drift from the layout it checks
    -- and it is ota_0's row, not the factory row above it."""
    r = _size_guard(tmp_path, 1024)
    assert "of a 4194304-byte ota_0 slot" in r.stdout
    assert r.returncode == 0


def test_an_image_that_does_not_fit_FAILS_the_build(tmp_path):
    """The alternatives to stopping here are esptool refusing it later, or a
    published payload no board can take."""
    r = _size_guard(tmp_path, 4 * 1024 * 1024 + 4096)
    assert r.returncode != 0
    assert "OVERFLOW: 4096 bytes" in r.stderr
    assert "BUILD FAILED" in r.stderr


def test_a_tight_fit_warns_without_failing(tmp_path):
    r = _size_guard(tmp_path, 4 * 1024 * 1024 - 1024)
    assert r.returncode == 0
    assert "under 200KB of OTA-slot headroom" in r.stderr


def test_a_comfortable_image_says_nothing_alarming(tmp_path):
    r = _size_guard(tmp_path, 3 * 1024 * 1024)
    assert r.returncode == 0 and r.stderr.strip() == ""
    assert "1048576 bytes headroom (1024 KB)" in r.stdout


def test_the_slot_and_the_warning_threshold_are_both_overridable(tmp_path):
    """A what-if against a table change, without editing the table."""
    r = _size_guard(tmp_path, 3 * 1024 * 1024,
                    MOYBYTE_APP_SLOT_BYTES=str(2 * 1024 * 1024))
    assert r.returncode != 0
    r = _size_guard(tmp_path, 3 * 1024 * 1024,
                    MOYBYTE_APP_HEADROOM_WARN_BYTES=str(4 * 1024 * 1024))
    assert r.returncode == 0 and "headroom left" in r.stderr


# -- the greps and the executable lane must point at the same file --------------


def test_every_board_build_sources_this_library(board):
    """`test_board_toml.py` and `test_ble_keyboard.py` assert
    `"moybyte_sdkconfig_guard" in build_script` and cannot say what the guard
    does; the tests above run it. Both are aimed at the same file only for as
    long as every board still sources it."""
    for sub in ("lilygo_t_deck_plus_mainline", "esp32_p4_wifi6_touch_lcd_7b",
                "guition_jc3248w535"):
        src = (ROOT / "firmware" / sub / "build.sh").read_text(encoding="utf-8")
        assert "tools/esp32_build_lib.sh" in src
        assert "moybyte_sdkconfig_guard" in src
