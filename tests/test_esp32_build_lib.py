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


# -- the map-lookup cache index, a consequence of REPR_C ------------------------

_MAP_C_STOCK = "#define MAP_CACHE_OFFSET(index) ((((uintptr_t)(index)) >> 2) % MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE)"


def _map_c(tmp_path, line):
    p = tmp_path / "py"
    p.mkdir(parents=True, exist_ok=True)
    (p / "map.c").write_text("// header\n%s\n" % line, encoding="utf-8")
    return p / "map.c"


def test_the_map_cache_patch_shifts_by_the_tag_width_repr_C_uses(tmp_path):
    """REPR_C tags a qstr in four bits; the stock `>> 2` leaves two constant
    bits in the slot index and a qstr key reaches 32 of 128 slots (#77)."""
    _mpconfig(tmp_path, "#define MICROPY_OBJ_REPR    (MICROPY_OBJ_REPR_C)")
    f = _map_c(tmp_path, _MAP_C_STOCK)
    r = sh("moybyte_patch_map_cache_for_repr_c", MPY_DIR=str(tmp_path))
    assert r.returncode == 0, r.stderr
    body = f.read_text(encoding="utf-8")
    assert ">> 4) % MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE" in body
    assert ">> 2)" not in body


def test_the_map_cache_patch_is_idempotent_on_a_warm_tree(tmp_path):
    _mpconfig(tmp_path, "#define MICROPY_OBJ_REPR    (MICROPY_OBJ_REPR_C)")
    f = _map_c(tmp_path, _MAP_C_STOCK)
    sh("moybyte_patch_map_cache_for_repr_c", MPY_DIR=str(tmp_path))
    once = f.read_text(encoding="utf-8")
    r = sh("moybyte_patch_map_cache_for_repr_c", MPY_DIR=str(tmp_path))
    assert r.returncode == 0 and f.read_text(encoding="utf-8") == once


def test_a_map_cache_line_that_changed_shape_FAILS_rather_than_no_ops(tmp_path):
    """A silent no-op here is a REPR_C board back on 32 reachable slots with
    nothing naming the cause -- the whole lever gone, and the frame A/B that
    found it the only thing that could tell."""
    _mpconfig(tmp_path, "#define MICROPY_OBJ_REPR    (MICROPY_OBJ_REPR_C)")
    f = _map_c(tmp_path, "#define MAP_CACHE_OFFSET(index) (((index) >> 2) & 127)")
    r = sh("moybyte_patch_map_cache_for_repr_c", MPY_DIR=str(tmp_path))
    assert r.returncode != 0
    assert "map-cache shift patch did not apply" in r.stderr
    assert ">> 2" in f.read_text(encoding="utf-8")


def test_the_map_cache_patch_REFUSES_a_tree_that_is_not_repr_C(tmp_path):
    """On REPR_A the stock index already reaches every slot and `>> 4` would
    fold four qstrs into one: the patch is a consequence of REPR_C and must
    not be takeable without it, which is what lets the Zero decline both."""
    _mpconfig(tmp_path, "#define MICROPY_OBJ_REPR    (MICROPY_OBJ_REPR_A)")
    f = _map_c(tmp_path, _MAP_C_STOCK)
    r = sh("moybyte_patch_map_cache_for_repr_c", MPY_DIR=str(tmp_path))
    assert r.returncode != 0
    assert "not REPR_C" in r.stderr
    assert f.read_text(encoding="utf-8").count(_MAP_C_STOCK) == 1


# -- size-class run hints for gc_alloc -----------------------------------------
#
# The stock lines tools/patch_gc_run_hints.py anchors on, in stock order, so a
# shape change upstream turns red here before it turns into a board build that
# quietly ships the stock allocator.

_GC_C_STOCK = """\
// gc.c
    area->gc_last_free_atb_index = 0;
    area->gc_last_used_block = 0;

void gc_collect_end(void) {
    for (mp_state_mem_area_t *area = &MP_STATE_MEM(area); area != NULL; area = NEXT_AREA(area)) {
        area->gc_last_free_atb_index = 0;
    }
}

void *gc_alloc(size_t n_bytes, unsigned int alloc_flags) {
            for (i = area->gc_last_free_atb_index; i < area->gc_alloc_table_byte_len; i++) {
            }
            #if MICROPY_GC_SPLIT_HEAP
            if (n_blocks == 1) {
                area->gc_last_free_atb_index = (i + 1) / BLOCKS_PER_ATB; // or (size_t)-1
            }
            #endif
found:
    if (n_free == 1) {
        #if MICROPY_GC_SPLIT_HEAP
        MP_STATE_MEM(gc_last_free_area) = area;
        #endif
        area->gc_last_free_atb_index = (i + 1) / BLOCKS_PER_ATB;
    }
}

void gc_free(void *ptr) {
    // free head and all of its tail blocks
    do {
        ATB_ANY_TO_FREE(area, block);
        block += 1;
    } while (ATB_GET_KIND(area, block) == AT_TAIL);
}

void *gc_realloc(void *ptr_in, size_t n_bytes, bool allow_move) {
        for (size_t bl = block + new_blocks, count = n_blocks - new_blocks; count > 0; bl++, count--) {
            ATB_ANY_TO_FREE(area, bl);
        }
}
"""

_MPSTATE_H_STOCK = """\
// mpstate.h
typedef struct _mp_state_mem_area_t {
    size_t gc_last_free_atb_index;
    size_t gc_last_used_block; // The block ID of the highest block allocated in the area
} mp_state_mem_area_t;
"""


def _gc_tree(tmp_path, gc_c=_GC_C_STOCK, mpstate_h=_MPSTATE_H_STOCK):
    p = tmp_path / "py"
    p.mkdir(parents=True, exist_ok=True)
    (p / "gc.c").write_text(gc_c, encoding="utf-8")
    (p / "mpstate.h").write_text(mpstate_h, encoding="utf-8")
    return p / "gc.c", p / "mpstate.h"


def _run_hints(tmp_path):
    return sh("moybyte_patch_gc_run_hints", MPY_DIR=str(tmp_path),
              REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)


def _both(gc_c, mpstate_h):
    return gc_c.read_text(encoding="utf-8"), mpstate_h.read_text(encoding="utf-8")


def test_the_run_hints_patch_lands_every_hunk_in_both_files(tmp_path):
    """The field, its two resets, the scan start, the two raises, the two
    lowers, and the byte-index fix on the area-full marker: each is a hunk,
    and a tree missing any one of them is a different allocator."""
    gc_c, mpstate_h = _gc_tree(tmp_path)
    r = _run_hints(tmp_path)
    assert r.returncode == 0, r.stderr
    c, h = _both(gc_c, mpstate_h)
    assert "#define MOYBYTE_GC_RUN_CLASSES (31)" in h
    assert "size_t gc_last_free_run_index[MOYBYTE_GC_RUN_CLASSES];" in h
    assert "for (i = MOYBYTE_GC_SCAN_START(area, n_blocks);" in c
    assert c.count("memset(area->gc_last_free_run_index, 0,") == 2
    assert c.count("moybyte_gc_run_hints_raise(area, ") == 2
    assert c.count("moybyte_gc_run_hints_lower(area, ") == 2
    assert "static void moybyte_gc_run_hints_raise(" in c
    assert "static void moybyte_gc_run_hints_lower(" in c
    assert "area->gc_last_free_atb_index = i; // Moybyte" in c
    assert "// or (size_t)-1" not in c


def test_the_run_hints_patch_is_idempotent_on_a_warm_tree(tmp_path):
    gc_c, mpstate_h = _gc_tree(tmp_path)
    assert _run_hints(tmp_path).returncode == 0
    once = _both(gc_c, mpstate_h)
    r = _run_hints(tmp_path)
    assert r.returncode == 0 and _both(gc_c, mpstate_h) == once


def test_a_run_hints_line_that_changed_shape_FAILS_and_writes_nothing(tmp_path):
    """One hunk missing would be a tree with the helpers but not the scan
    that reads them: nothing is written to either file, the exit names the
    hunk, and the board build stops there."""
    gc_c, mpstate_h = _gc_tree(tmp_path, gc_c=_GC_C_STOCK.replace(
        "for (i = area->gc_last_free_atb_index; i < area->gc_alloc_table_byte_len; i++) {",
        "for (i = area->gc_last_free_atb_index; i < len; i++) {"))
    before = _both(gc_c, mpstate_h)
    r = _run_hints(tmp_path)
    assert r.returncode != 0
    assert "did not apply" in r.stderr and "scan start" in r.stderr
    assert _both(gc_c, mpstate_h) == before


def test_a_half_applied_run_hints_tree_is_REFUSED(tmp_path):
    """A header carrying the field beside a stock gc.c is a build that
    compiles and never reads it; it is refused rather than patched over."""
    gc_c, mpstate_h = _gc_tree(tmp_path)
    assert _run_hints(tmp_path).returncode == 0
    _gc_tree(tmp_path, mpstate_h=mpstate_h.read_text(encoding="utf-8"))
    r = _run_hints(tmp_path)
    assert r.returncode != 0 and "half-applied" in r.stderr


# -- the gc meters: gc.pauses() and gc.areas() --------------------------------
#
# The stock lines tools/patch_gc_meters.py and tools/patch_gc_census.py
# anchor on, in stock order: moybyte_patch_gc_meters applies both.

_GC_C_METERS_STOCK = """\
#include "py/gc.h"
#include "py/runtime.h"

#if MICROPY_GC_SPLIT_HEAP
void gc_add(void *start, void *end) {
}

static bool gc_try_add_heap(size_t failed_alloc) {
    gc_add(new_heap, (void *)new_heap + to_alloc);

    return true;
}
#endif

void gc_collect_start(void) {
    gc_collect_start_common();
}

void gc_collect_end(void) {
    MP_STATE_THREAD(gc_lock_depth) &= ~GC_COLLECT_FLAG;
    GC_EXIT();
    #if MICROPY_PY_WEAKREF
    gc_weakref_sweep();
    #endif
}

static void gc_sweep_free_blocks(void) {
        if (last_used_block == 0 && prev_area != NULL) {
            DEBUG_printf("gc_sweep_free_blocks free empty area %p\\n", area);
            NEXT_AREA(prev_area) = NEXT_AREA(area);
            MP_PLAT_FREE_HEAP(area);
        }
}
"""

_MODGC_C_STOCK = """\
#if MICROPY_GC_ALLOC_THRESHOLD
static mp_obj_t gc_threshold(size_t n_args, const mp_obj_t *args) {
}
#endif

static const mp_rom_map_elem_t mp_module_gc_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_mem_free), MP_ROM_PTR(&gc_mem_free_obj) },
    { MP_ROM_QSTR(MP_QSTR_mem_alloc), MP_ROM_PTR(&gc_mem_alloc_obj) },
};
"""


def _meters_tree(tmp_path, gc_c=_GC_C_METERS_STOCK, modgc_c=_MODGC_C_STOCK):
    p = tmp_path / "py"
    p.mkdir(parents=True, exist_ok=True)
    (p / "gc.c").write_text(gc_c, encoding="utf-8")
    (p / "modgc.c").write_text(modgc_c, encoding="utf-8")
    return p / "gc.c", p / "modgc.c"


def _meters(tmp_path):
    return sh("moybyte_patch_gc_meters", MPY_DIR=str(tmp_path),
              REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)


def test_the_gc_meters_patch_times_every_collect_and_registers_both_reads(
        tmp_path):
    """The clock at the top of gc_collect_start and the bottom of
    gc_collect_end, both behind MICROPY_PY_GC (mpy-cross compiles gc.c with no
    clock to link), and both functions in the gc module's table."""
    gc_c, modgc_c = _meters_tree(tmp_path)
    r = _meters(tmp_path)
    assert r.returncode == 0, r.stderr
    c, m = _both(gc_c, modgc_c)
    assert '#if MICROPY_PY_GC // Moybyte: gc meters\n#include "py/mphal.h"' in c
    assert ("void gc_collect_start(void) {\n    MOYBYTE_GC_PAUSE_START();"
            in c)
    assert "    MOYBYTE_GC_PAUSE_END(); // Moybyte: gc meters\n}\n" in c
    assert c.index("#define MOYBYTE_GC_PAUSE_END()") < c.index(
        "void gc_collect_start(void)")
    assert "extern uint32_t moybyte_gc_pause[3];" in m
    assert "MP_ROM_QSTR(MP_QSTR_pauses), MP_ROM_PTR(&gc_pauses_obj)" in m
    assert "MP_ROM_QSTR(MP_QSTR_areas), MP_ROM_PTR(&gc_areas_obj)" in m
    assert m.index("gc_areas_obj, gc_areas)") < m.index("mp_module_gc_globals_table[]")


def test_the_gc_meters_patch_is_idempotent_and_independent_of_the_run_hints(
        tmp_path):
    """Neither patch touches the other's lines, so a tree takes both in either
    order and comes out the same."""
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _meters_tree(d, gc_c=_GC_C_METERS_STOCK + _GC_C_STOCK)
        (d / "py" / "mpstate.h").write_text(_MPSTATE_H_STOCK, encoding="utf-8")
    assert _meters(a).returncode == 0 and _run_hints(a).returncode == 0
    assert _run_hints(b).returncode == 0 and _meters(b).returncode == 0
    texts = [(d / "py" / f).read_text(encoding="utf-8")
             for d in (a, b) for f in ("gc.c", "modgc.c", "mpstate.h")]
    assert texts[:3] == texts[3:]
    assert _meters(a).returncode == 0
    assert [(a / "py" / f).read_text(encoding="utf-8")
            for f in ("gc.c", "modgc.c", "mpstate.h")] == texts[:3]


def test_the_gc_census_patch_records_every_area_and_registers_both_reads(
        tmp_path):
    """An area added notes the request that grew the heap, an area a sweep
    frees notes itself, and area_map/growths sit in the gc module's table
    between entries the meters patch does not anchor on."""
    gc_c, modgc_c = _meters_tree(tmp_path)
    r = _meters(tmp_path)
    assert r.returncode == 0, r.stderr
    c, m = _both(gc_c, modgc_c)
    assert ("    gc_add(new_heap, (void *)new_heap + to_alloc);\n"
            "    MOYBYTE_GC_NOTE(failed_alloc, to_alloc);") in c
    assert ("    MOYBYTE_GC_NOTE(0, area->gc_pool_end - (byte *)area);"
            in c)
    assert c.index("size_t moybyte_gc_area_map(") < c.index("void gc_add(")
    assert c.index("#define MOYBYTE_GC_NOTE(") < c.index("gc_try_add_heap(")
    assert ("MP_QSTR_mem_free), MP_ROM_PTR(&gc_mem_free_obj) },\n"
            "    { MP_ROM_QSTR(MP_QSTR_area_map), MP_ROM_PTR(&gc_area_map_obj) }"
            in m)
    assert "MP_ROM_QSTR(MP_QSTR_growths), MP_ROM_PTR(&gc_growths_obj)" in m
    assert "MP_ROM_QSTR(MP_QSTR_refs), MP_ROM_PTR(&gc_refs_obj)" in m
    assert c.index("size_t moybyte_gc_refs(") < c.index("void gc_add(")
    assert m.index("gc_growths_obj, gc_growths)") < m.index("gc_threshold(")


def test_the_gc_census_and_meters_patches_land_the_same_in_either_order(
        tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        _meters_tree(d)
    run = [sys.executable, str(ROOT / "tools" / "patch_gc_census.py")]
    meters = [sys.executable, str(ROOT / "tools" / "patch_gc_meters.py")]
    for d, order in ((a, (run, meters)), (b, (meters, run))):
        for cmd in order:
            assert subprocess.run(cmd + [str(d)]).returncode == 0
    assert _both(*_meters_tree_paths(a)) == _both(*_meters_tree_paths(b))


def _meters_tree_paths(d):
    return d / "py" / "gc.c", d / "py" / "modgc.c"


def test_a_gc_census_line_that_changed_shape_FAILS_and_writes_nothing(tmp_path):
    gc_c, modgc_c = _meters_tree(tmp_path, gc_c=_GC_C_METERS_STOCK.replace(
        "            NEXT_AREA(prev_area) = NEXT_AREA(area);\n",
        "            prev_area->next = area->next;\n"))
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "patch_gc_census.py"),
                        str(tmp_path)], capture_output=True, text=True)
    assert r.returncode != 0
    assert "did not apply" in r.stderr and "area freed" in r.stderr
    assert "gc census" not in gc_c.read_text(encoding="utf-8")
    assert "gc census" not in modgc_c.read_text(encoding="utf-8")


def test_a_gc_meters_line_that_changed_shape_FAILS_and_writes_nothing(tmp_path):
    gc_c, modgc_c = _meters_tree(tmp_path, gc_c=_GC_C_METERS_STOCK.replace(
        "    gc_weakref_sweep();\n", "    gc_weakref_sweep_all();\n"))
    before = _both(gc_c, modgc_c)
    r = _meters(tmp_path)
    assert r.returncode != 0
    assert "did not apply" in r.stderr and "collect end" in r.stderr
    assert _both(gc_c, modgc_c) == before


def test_a_half_applied_gc_meters_tree_is_REFUSED(tmp_path):
    gc_c, modgc_c = _meters_tree(tmp_path)
    assert _meters(tmp_path).returncode == 0
    _meters_tree(tmp_path, gc_c=gc_c.read_text(encoding="utf-8"))
    r = _meters(tmp_path)
    assert r.returncode != 0 and "half-applied" in r.stderr


# -- the stdin ring for a UART console -----------------------------------------

_MPHALPORT_STOCK = """\
TaskHandle_t mp_main_task_handle;

static uint8_t stdin_ringbuf_array[260];
ringbuf_t stdin_ringbuf = {stdin_ringbuf_array, sizeof(stdin_ringbuf_array), 0, 0};
"""


_UART_STOCK = """\
// RXFIFO Full interrupt threshold. Set the same as the ESP-IDF UART driver
#define RXFIFO_FULL_THR (SOC_UART_FIFO_LEN - 8)

void uart_stdout_init(void) {
    uart_hal_ena_intr_mask(&repl_hal, UART_INTR_RXFIFO_FULL | UART_INTR_RXFIFO_TOUT);
}

static void IRAM_ATTR uart_irq_handler(void *arg) {
    uint8_t rbuf[SOC_UART_FIFO_LEN];
    int len;
    len = uart_hal_get_rxfifo_len(&repl_hal);
    uart_hal_read_rxfifo(&repl_hal, rbuf, &len);

    for (int i = 0; i < len; i++) {
        if (rbuf[i] == mp_interrupt_char) {
            mp_sched_keyboard_interrupt();
        } else {
            // this is an inline function so will be in IRAM
            ringbuf_put(&stdin_ringbuf, rbuf[i]);
        }
    }
}

#endif // MICROPY_HW_ENABLE_UART_REPL
"""


def _mphalport(tmp_path, text=_MPHALPORT_STOCK, uart=_UART_STOCK):
    p = tmp_path / "ports" / "esp32"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "mphalport.c"
    f.write_text(text, encoding="utf-8")
    (p / "uart.c").write_text(uart, encoding="utf-8")
    return f


def _stdin_ring(tmp_path):
    return sh("moybyte_patch_stdin_ring", MPY_DIR=str(tmp_path),
              REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)


def test_the_stdin_ring_patch_sizes_the_array_the_ring_is_built_over(tmp_path):
    """The ring takes its size from `sizeof` the array, so the array is the
    whole edit -- the only place the number lives -- and its section is TCM,
    where it leaves the L2MEM heap's layout alone."""
    f = _mphalport(tmp_path)
    r = _stdin_ring(tmp_path)
    assert r.returncode == 0, r.stderr
    c = f.read_text(encoding="utf-8")
    assert "static uint8_t TCM_DRAM_ATTR stdin_ringbuf_array[4096];" in c
    assert "#include \"esp_attr.h\"" in c
    assert "[260]" not in c
    assert "{stdin_ringbuf_array, sizeof(stdin_ringbuf_array), 0, 0}" in c


def test_the_uart_rx_isr_wakes_the_reader(tmp_path):
    """The USB-Serial/JTAG ISR notifies the MicroPython task when bytes land
    and the UART's did not, so a reader waiting on an empty ring slept out
    every tick. The notify goes after the ring is fed, once per interrupt."""
    _mphalport(tmp_path)
    assert _stdin_ring(tmp_path).returncode == 0
    c = (tmp_path / "ports" / "esp32" / "uart.c").read_text(encoding="utf-8")
    put = c.index("ringbuf_put(&stdin_ringbuf, rbuf[i]);")
    wake = c.index("vTaskNotifyGiveFromISR(mp_main_task_handle, &woken);")
    assert put < wake < c.index("#endif // MICROPY_HW_ENABLE_UART_REPL")
    assert "portYIELD_FROM_ISR();" in c


def test_the_uart_isr_drains_early_and_resets_an_overflowed_fifo(tmp_path):
    """A payload at megabits leaves the stock threshold 40 us of slack; a
    quarter-full threshold leaves ~0.5 ms, and an overflow that happens anyway
    resets the FIFO before it is read, so a lost byte is a short window."""
    _mphalport(tmp_path)
    assert _stdin_ring(tmp_path).returncode == 0
    c = (tmp_path / "ports" / "esp32" / "uart.c").read_text(encoding="utf-8")
    assert "#define RXFIFO_FULL_THR (SOC_UART_FIFO_LEN / 4)" in c
    assert "UART_INTR_RXFIFO_TOUT | UART_INTR_RXFIFO_OVF);" in c
    reset = c.index("uart_ll_rxfifo_rst(repl_hal.dev);")
    assert reset < c.index("len = uart_hal_get_rxfifo_len(&repl_hal);")


def test_the_stdin_ring_patch_is_idempotent_on_a_warm_tree(tmp_path):
    f = _mphalport(tmp_path)
    assert _stdin_ring(tmp_path).returncode == 0
    once = f.read_text(encoding="utf-8")
    uart = (tmp_path / "ports" / "esp32" / "uart.c").read_text(encoding="utf-8")
    r = _stdin_ring(tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""
    assert f.read_text(encoding="utf-8") == once
    assert (tmp_path / "ports" / "esp32" / "uart.c").read_text(
        encoding="utf-8") == uart


def test_a_tree_with_the_ring_but_not_the_wake_gets_the_wake(tmp_path):
    """Each file carries its own marker: a build tree that took the ring
    before the wake existed takes the wake and leaves the ring alone."""
    f = _mphalport(tmp_path)
    assert _stdin_ring(tmp_path).returncode == 0
    ring = f.read_text(encoding="utf-8")
    (tmp_path / "ports" / "esp32" / "uart.c").write_text(_UART_STOCK,
                                                         encoding="utf-8")
    assert _stdin_ring(tmp_path).returncode == 0
    assert f.read_text(encoding="utf-8") == ring
    assert "vTaskNotifyGiveFromISR" in (
        tmp_path / "ports" / "esp32" / "uart.c").read_text(encoding="utf-8")


def test_a_stdin_ring_line_that_changed_shape_FAILS_and_writes_nothing(tmp_path):
    """A silent no-op is a board back on 260 bytes, which drops a long line
    whenever a collection lands in it and names nothing."""
    f = _mphalport(tmp_path, _MPHALPORT_STOCK.replace("[260]", "[512]"))
    before = f.read_text(encoding="utf-8")
    r = _stdin_ring(tmp_path)
    assert r.returncode != 0
    assert "did not apply" in r.stderr and "ring array" in r.stderr
    assert f.read_text(encoding="utf-8") == before


# -- machine.SDCard in multi-block runs ----------------------------------------

_SDCARD_STOCK = """\
#include "sdmmc_cmd.h"
#define _SECTOR_SIZE(self) (self->card.csd.sector_size)

static mp_obj_t machine_sdcard_readblocks(mp_obj_t self_in, mp_obj_t block_num, mp_obj_t buf) {
    mp_get_buffer_raise(buf, &bufinfo, MP_BUFFER_WRITE);
    err = sdmmc_read_sectors(&(self->card), bufinfo.buf, mp_obj_get_int(block_num), bufinfo.len / _SECTOR_SIZE(self));
    return mp_obj_new_bool(err == ESP_OK);
}

static mp_obj_t machine_sdcard_writeblocks(mp_obj_t self_in, mp_obj_t block_num, mp_obj_t buf) {
    mp_get_buffer_raise(buf, &bufinfo, MP_BUFFER_READ);
    err = sdmmc_write_sectors(&(self->card), bufinfo.buf, mp_obj_get_int(block_num), bufinfo.len / _SECTOR_SIZE(self));
    return mp_obj_new_bool(err == ESP_OK);
}
"""


def _sdcard(tmp_path, text=_SDCARD_STOCK):
    p = tmp_path / "ports" / "esp32"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "machine_sdcard.c"
    f.write_text(text, encoding="utf-8")
    return f, sh("moybyte_patch_sdcard_runs", MPY_DIR=str(tmp_path),
                 REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)


def test_the_sdcard_patch_routes_both_block_verbs_through_the_runs(tmp_path):
    """IDF moves a PSRAM buffer one single-block command per sector; both
    verbs go through the run helper instead, and the helper is defined before
    the first of them."""
    f, r = _sdcard(tmp_path)
    assert r.returncode == 0, r.stderr
    c = f.read_text(encoding="utf-8")
    assert "sdmmc_read_sectors(&(self->card), bufinfo.buf" not in c
    assert "sdmmc_write_sectors(&(self->card), bufinfo.buf" not in c
    helper = c.index("static esp_err_t moybyte_sd_xfer(")
    assert helper < c.index("moybyte_sd_xfer(&(self->card), bufinfo.buf, "
                            "mp_obj_get_int(block_num), bufinfo.len / "
                            "_SECTOR_SIZE(self), false);")
    assert helper < c.index("_SECTOR_SIZE(self), true);")


def test_the_sdcard_patch_is_idempotent_and_refuses_a_changed_line(tmp_path):
    f, r = _sdcard(tmp_path)
    once = f.read_text(encoding="utf-8")
    r = sh("moybyte_patch_sdcard_runs", MPY_DIR=str(tmp_path),
           REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)
    assert r.returncode == 0 and f.read_text(encoding="utf-8") == once
    g, r = _sdcard(tmp_path / "b", _SDCARD_STOCK.replace("bufinfo.len /", "n /"))
    assert r.returncode != 0 and "did not apply" in r.stderr
    assert g.read_text(encoding="utf-8") == _SDCARD_STOCK.replace(
        "bufinfo.len /", "n /")


# -- LittleFS's sizes on a flash store ------------------------------------------

_LFS_STOCK = """\
static const mp_arg_t lfs_make_allowed_args[] = {
    { MP_QSTR_, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = MP_OBJ_NULL} },
    { MP_QSTR_readsize, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 32} },
    { MP_QSTR_progsize, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 32} },
    { MP_QSTR_lookahead, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 32} },
};
"""


def _lfs(tmp_path, text=_LFS_STOCK):
    p = tmp_path / "extmod"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "vfs_lfs.c"
    f.write_text(text, encoding="utf-8")
    return f, sh("moybyte_patch_lfs_sizes", MPY_DIR=str(tmp_path),
                 REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)


def test_the_lfs_patch_moves_two_defaults_and_not_the_read_size(tmp_path):
    """The defaults are the whole change: `_boot.py` mounts the store with no
    sizes named. The read size stays stock."""
    f, r = _lfs(tmp_path)
    assert r.returncode == 0, r.stderr
    c = f.read_text(encoding="utf-8")
    assert "{ MP_QSTR_progsize, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 256} }," in c
    assert "{ MP_QSTR_lookahead, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 256} }," in c
    assert "{ MP_QSTR_readsize, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 32} }," in c


def test_the_lfs_patch_is_idempotent_and_refuses_a_changed_line(tmp_path):
    f, _r = _lfs(tmp_path)
    once = f.read_text(encoding="utf-8")
    r = sh("moybyte_patch_lfs_sizes", MPY_DIR=str(tmp_path),
           REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)
    assert r.returncode == 0 and f.read_text(encoding="utf-8") == once
    changed = _LFS_STOCK.replace("lookahead, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 32}",
                                 "lookahead, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 64}")
    g, r = _lfs(tmp_path / "b", changed)
    assert r.returncode != 0 and "did not apply" in r.stderr
    assert g.read_text(encoding="utf-8") == changed


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
                  "label": "0.9", "board": "tdeck", "commit": "unknown"}


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


def test_the_stamp_names_the_commit_the_image_was_built_from(tmp_path):
    """`tools/board.py pass` prints it as the image's commit; `+` is a tree
    with tracked changes on top of it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, PATH=os.environ["PATH"])
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=str(repo), env=env,
                                    check=True, capture_output=True, text=True)
    (repo / "f").write_text("a\n")
    run("init", "-q")
    run("add", "f")
    run("commit", "-q", "-m", "c")
    sha = run("rev-parse", "--short=8", "HEAD").stdout.strip()
    ota = _ota_py(tmp_path)
    assert _stamp(tmp_path, ota, REPO_ROOT=str(repo))[2]["commit"] == sha
    (repo / "f").write_text("b\n")
    assert _stamp(tmp_path, ota, REPO_ROOT=str(repo))[2]["commit"] == sha + "+"
    assert _stamp(tmp_path, ota)[2]["commit"] == "unknown"


def _ccache_env(tmp_path, **env):
    """What moybyte_ccache exports, with a stand-in `ccache` on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "ccache").write_text("#!/bin/sh\n")
    (bin_dir / "ccache").chmod(0o755)
    path = "%s:%s" % (bin_dir, os.environ.get("PATH", "/usr/bin:/bin"))
    r = sh("moybyte_ccache; env | grep -E '^(IDF_CCACHE_ENABLE|CCACHE_[A-Z]+|IDF_PATH)=' || true",
           REPO_ROOT=str(ROOT), IDF_DIR="/x/esp-idf", PATH=path, **env)
    assert r.returncode == 0, r.stderr
    return dict(ln.split("=", 1) for ln in r.stdout.split())


def test_a_local_build_shares_one_compiler_cache_rooted_at_the_main_checkout(tmp_path):
    common = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute",
         "--git-common-dir"], capture_output=True, text=True, check=True).stdout
    got = _ccache_env(tmp_path)
    assert got == {"IDF_CCACHE_ENABLE": "1",
                   "CCACHE_BASEDIR": os.path.dirname(common.strip()),
                   "CCACHE_NOHASHDIR": "1", "IDF_PATH": "/x/esp-idf"}
    assert _ccache_env(tmp_path, IDF_CCACHE_ENABLE="0")["IDF_CCACHE_ENABLE"] == "0"


def test_ci_keeps_its_own_cache_settings(tmp_path):
    assert _ccache_env(tmp_path, CI="true") == {}


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
                "guition_jc3248w535", "guition_jc8012p4a1c"):
        src = (ROOT / "firmware" / sub / "build.sh").read_text(encoding="utf-8")
        assert "tools/esp32_build_lib.sh" in src
        assert "moybyte_sdkconfig_guard" in src


# -- the staged native tree: one owner of where it is ---------------------------


def test_the_lib_asks_where_the_native_tree_was_staged(tmp_path):
    """`moybyte_stage_native` generates the web blob INTO the staged tree, so it
    has to find it -- and it had `native/.staged` typed on its own side while
    board.toml's `[native] dest` decides. A board moving its dest would have
    left the blob ungenerated: no error, an image with no console."""
    lib = LIB.read_text(encoding="utf-8")
    body = lib.split("moybyte_stage_native() {", 1)[1].split("\n}", 1)[0]
    assert "native/.staged" not in body, \
        "the lib restates the staging dest instead of asking board_config"
    assert "native-dest" in body

    sys.path.insert(0, str(ROOT / "tools"))
    import board_config
    board = tmp_path / "board"
    (board / "boards" / "X").mkdir(parents=True)
    (board / "board.toml").write_text('[native]\ndest = "native/elsewhere"\n',
                                      encoding="utf-8")
    assert board_config.native_dest(board) == "native/elsewhere"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "board_config.py"),
                        "native-dest", str(board)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "native/elsewhere", r.stderr


def test_a_board_that_says_nothing_gets_the_default_dest(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import board_config
    board = tmp_path / "board"
    board.mkdir()
    (board / "board.toml").write_text("[board]\nota = \"x\"\n", encoding="utf-8")
    assert board_config.native_dest(board) == "native/.staged"


# -- the USB-Serial/JTAG console's start (tools/patch_usj_rx_init.py) -----------

_USJ_STOCK = """\
static void usb_serial_jtag_handle_rx(void) {
    size_t len = usb_serial_jtag_ll_read_rxfifo(rx_buf, req_len);
}

void usb_serial_jtag_init(void) {
    usb_serial_jtag_ll_clr_intsts_mask(USB_SERIAL_JTAG_INTR_SERIAL_OUT_RECV_PKT |
        USB_SERIAL_JTAG_INTR_SOF);
    usb_serial_jtag_ll_ena_intr_mask(USB_SERIAL_JTAG_INTR_SERIAL_OUT_RECV_PKT |
        USB_SERIAL_JTAG_INTR_SOF | USB_SERIAL_JTAG_INTR_SERIAL_IN_EMPTY);
    ESP_ERROR_CHECK(esp_intr_alloc(ETS_USB_SERIAL_JTAG_INTR_SOURCE, ESP_INTR_FLAG_LEVEL1,
        usb_serial_jtag_isr_handler, NULL, NULL));
}

void usb_serial_jtag_poll_rx(void) {
}
"""


def _usj(tmp_path, text=_USJ_STOCK):
    p = tmp_path / "ports" / "esp32"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "usb_serial_jtag.c"
    f.write_text(text, encoding="utf-8")
    r = sh("moybyte_patch_usj_rx_init", MPY_DIR=str(tmp_path),
           REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)
    return f, r


def test_the_usj_console_reads_its_fifo_once_its_interrupt_is_up(tmp_path):
    """The stock init clears the interrupt of a packet that landed during the
    bootloader; the read after the ISR is installed is what takes it, so it
    goes inside init, after esp_intr_alloc."""
    f, r = _usj(tmp_path)
    assert r.returncode == 0, r.stderr
    c = f.read_text(encoding="utf-8")
    init = c.index("void usb_serial_jtag_init(void) {")
    alloc = c.index("usb_serial_jtag_isr_handler, NULL, NULL));", init)
    read = c.index("    usb_serial_jtag_handle_rx();\n", alloc)
    assert read < c.index("void usb_serial_jtag_poll_rx(void)")
    again = sh("moybyte_patch_usj_rx_init", MPY_DIR=str(tmp_path),
               REPO_ROOT=str(ROOT), BUILD_PYTHON=sys.executable)
    assert again.returncode == 0 and again.stdout.strip() == ""
    assert f.read_text(encoding="utf-8") == c


def test_a_usj_init_that_changed_shape_FAILS_and_writes_nothing(tmp_path):
    """A silent no-op is a board whose boot a host's early line still breaks."""
    text = _USJ_STOCK.replace("ESP_INTR_FLAG_LEVEL1", "ESP_INTR_FLAG_LEVEL2")
    f, r = _usj(tmp_path, text)
    assert r.returncode != 0 and "did not apply" in r.stderr
    assert f.read_text(encoding="utf-8") == text
