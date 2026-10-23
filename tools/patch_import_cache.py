#!/usr/bin/env python3
"""An extensible built-in module is entered in sys.modules once it is found.

    python3 tools/patch_import_cache.py <micropython checkout>

MicroPython lets a file on the path override an EXTENSIBLE built-in (`array`,
`json`, `os`, `time`, `struct`, `random`, `select`, ... -- every module
registered with MP_REGISTER_EXTENSIBLE_MODULE). So `process_import_at_level`
(py/builtinimport.c) stats every sys.path entry for the name before it falls
back to the built-in, and the built-in it returns is never entered in
sys.modules, so the next `import` of the name searches again. On a board each
search is a filesystem stat per path entry and suffix: 24 ms a miss on a
T-Deck, measured 2026-10-23 -- paid by every function-level `import json` on
every call, and by every module whose top line is `from array import array`
at its first import (the Editor's map, music and sprite tabs each paid it on
their first switch).

The patch enters the built-in in sys.modules at that fallback, under the name
it was imported by. The first import of a name still searches the path, so a
file that overrides a built-in still wins where it is present at that first
import; every later import is a dictionary lookup, as for any other module.

Applied by `moybyte_patch_import_cache` in tools/esp32_build_lib.sh, to
`py/builtinimport.c`. All-or-nothing: the hunk must match exactly once or
nothing is written and the exit is non-zero. A tree already carrying the
marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: a found extensible built-in enters sys.modules"
FILE = "py/builtinimport.c"

STOCK = (
    "        if (stat == MP_IMPORT_STAT_NO_EXIST) {\n"
    "            module_obj = mp_module_get_builtin(level_mod_name, true);\n"
    "            if (module_obj != MP_OBJ_NULL) {\n"
    "                return module_obj;\n"
    "            }\n"
    "        }\n"
)

PATCHED = (
    "        if (stat == MP_IMPORT_STAT_NO_EXIST) {\n"
    "            module_obj = mp_module_get_builtin(level_mod_name, true);\n"
    "            if (module_obj != MP_OBJ_NULL) {\n"
    "                // %s (tools/patch_import_cache.py):\n"
    "                // the path was searched once; the next import is a lookup.\n"
    "                mp_map_lookup(&MP_STATE_VM(mp_loaded_modules_dict).map,\n"
    "                    MP_OBJ_NEW_QSTR(full_mod_name), MP_MAP_LOOKUP_ADD_IF_NOT_FOUND)->value = module_obj;\n"
    "                return module_obj;\n"
    "            }\n"
    "        }\n" % MARKER
)


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit when the stock fallback does not match exactly once."""
    path = os.path.join(mpy_dir, FILE)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        return "already"
    n = text.count(STOCK)
    if n != 1:
        sys.exit("!! import-cache patch did not apply -- %s's extensible built-in "
                 "fallback matches %d times (the stock lines changed shape); nothing "
                 "written" % (FILE, n))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(STOCK, PATCHED))
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its "
                                    "py/builtinimport.c is what changes)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched py/builtinimport.c: a found extensible built-in enters sys.modules")


if __name__ == "__main__":
    main()
