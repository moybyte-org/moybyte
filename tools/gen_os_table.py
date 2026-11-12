#!/usr/bin/env python3
"""Write every tier's table of Moybyte's app and OS calls from their one
source, native/os_calls.json (docs/os_2026-11/tiers.md section 1):

  the ELF tier (the OS function table, by ordinal)
    native/moy_elf/moy_os_table.h   the rows' ordinals, the version, the table type
    native/moy_elf/moy_os_rows.h    the kernel's prototype of each row (moy_os_<name>)
    native/moy_elf/moy_os_table.c   the table, a retired row's typed stub, and the
                                    body of each row the role door serves
    sdk/include/moybyte/os.h        a program's header: the moybyte rows, the event
                                    descriptor and its record (from
                                    native/moy_kernel/moy_evq.h), a WASI program's
                                    moybyte.os imports
    sdk/src/moyos_stubs.c           a module's side of every row: the function by
                                    its C library name, calling the table by ordinal
    native/moy_elf/moy_os_check.c   each row's argument rule (u), checked before a
                                    U-mode module's call runs (the P4s, moy_umode.h)
  the WASI tier
    native/moy_wasm/moy_wasi_os_rows.h  a WASI program's moybyte.os imports
  the app ABI (native/moy_app)
    native/moy_app/moy_app_rows.h   the roles, the C rows, the door's table, the
                                    permission map
    native/moy_app/moy_app_wasm_rows.h  the moybyte.app imports: each native
    native/moy_ui/moy_ui_wasm_rows.h    the ui imports, served by native/moy_ui
    native/moy_app/moy_app_lua_rows.h   the names a Lua run and a Lua service hold
  the Python console, between GENERATED markers
    runtime/shell/app_context.py    ROLES
    runtime/shell/app_perms.py      TABLE, each permission's tiers derived
    runtime/shell/shell_servers.py  DOOR_ARGS
    runtime/system/scheduler.py     _CALLS
    runtime/run/lua_ext.py          the Lua run's role names and console calls
  docs/app_api_v1.md                the permission table, the call table and
                                    the role table (three regions)
  the moy-app plugin (sdk/moybyte_sdk, `moy app`)
    sdk/moybyte_sdk/os_table.py         what each tier's module or script reaches, with
                                    the permissions each answers under
    sdk/include/moybyte/app.h       a C wasm app's moybyte.app imports
    sdk/moybyte_sdk/stubs/              the names a Lua and a Python app reach, for editors

The generator is the package tools/gen_os/: table.py (the source, its views
and its rules), cproto.py (a row's C prototype, parsed), and one emitter per
tier (elf.py, wasi.py, app.py, lua.py, py.py, docs.py, sdk.py). This file is
its entry point and outputs() its one list.

The output is committed: no build runs this, and tests/test_os_table.py
regenerates it and fails on any difference.

    python3 tools/gen_os_table.py [--check]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_os import TableError  # noqa: E402,F401
from gen_os.table import *  # noqa: E402,F401,F403
from gen_os.table import (APP_CONTEXT, APP_DOC, APP_H, APP_PERMS, APP_WASM_SDK_H, CHECK_C,  # noqa: E402
                          KERNEL_C, KERNEL_H, LUA_EXT, LUA_H, LUA_STUB, PLUGIN_TABLE, PY_STUBS, ROOT,
                          ROWS_H, SCHEDULER, SDK_H, SHELL_SERVERS, STUBS_C, UI_H, UI_SDK_H, WASI_OS_H,
                          WASM_H, WILD_H, check, elf, load)
from gen_os.elf import (evq_block, gen_check_c, gen_kernel_c, gen_kernel_h, gen_rows_h,  # noqa: E402,F401
                        gen_sdk_h, gen_stubs_c, gen_wild_h)
from gen_os.wasi import gen_wasi_h  # noqa: E402
from gen_os.app import gen_app_h, gen_ui_h, gen_wasm_h  # noqa: E402
from gen_os.lua import gen_lua_h, lua_names, lua_own, lua_run_calls  # noqa: E402,F401
from gen_os.py import gen_app_context, gen_app_perms, gen_calls, gen_door_args, gen_lua_ext  # noqa: E402
from gen_os.docs import gen_doc  # noqa: E402
from gen_os.sdk import (gen_app_wasm_h, gen_lua_stub, gen_plugin_table, gen_py_stubs,  # noqa: E402
                        gen_sdk_ui_h)


def outputs(src=None):
    """[(path, text)] for every generated file."""
    if src is None:
        src = load()
    check(src)
    table = elf(src)
    return [(KERNEL_H, gen_kernel_h(table)), (ROWS_H, gen_rows_h(table)),
            (KERNEL_C, gen_kernel_c(table)), (SDK_H, gen_sdk_h(table)),
            (STUBS_C, gen_stubs_c(table)), (CHECK_C, gen_check_c(table)),
            (WILD_H, gen_wild_h(table)),
            (APP_H, gen_app_h(src)), (WASM_H, gen_wasm_h(src)), (UI_H, gen_ui_h(src)),
            (LUA_H, gen_lua_h(src)), (WASI_OS_H, gen_wasi_h(src)),
            (APP_CONTEXT, gen_app_context(src)), (APP_PERMS, gen_app_perms(src)),
            (SHELL_SERVERS, gen_door_args(src)), (SCHEDULER, gen_calls(src)),
            (LUA_EXT, gen_lua_ext(src)), (APP_DOC, gen_doc(src)),
            (PLUGIN_TABLE, gen_plugin_table(src)), (APP_WASM_SDK_H, gen_app_wasm_h(src)),
            (UI_SDK_H, gen_sdk_ui_h(src)),
            (LUA_STUB, gen_lua_stub(src))] + list(zip(PY_STUBS, gen_py_stubs(src)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 when a generated file is stale")
    args = ap.parse_args(argv)
    stale = 0
    for path, text in outputs():
        try:
            with open(path) as f:
                have = f.read()
        except OSError:
            have = None
        if have == text:
            continue
        if args.check:
            print("%s is stale: run tools/gen_os_table.py" % os.path.relpath(path, ROOT))
            stale = 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        print("wrote %s" % os.path.relpath(path, ROOT))
    return stale


if __name__ == "__main__":
    sys.exit(main())
