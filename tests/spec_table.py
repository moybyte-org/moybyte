"""moy-spec's verb table as this repo vendors it, parsed from the vendored C.

`verbs()` is the table: the globals libmoy's Lua binding installs
(native/moycore/libmoy/moy_lua.c, its VERBS table and open_host_verbs), which
moy-spec's own check holds equal to wasm-imports.json less the compiled
binding's own rows. `wasm_imports()` is the compiled binding's import table
(native/moycore/libmoy/moy_wasm.c's NATIVES, wasm-imports.json's rows in its
order), the table every tier's wasm runtime registers. A re-vendor that moves
either moves every test that reads it.
"""

import re
from pathlib import Path

LIBMOY = Path(__file__).resolve().parent.parent / "native" / "moycore" / "libmoy"

# SPEC.md 9's canvas size: integers, not verbs, so in no verb table.
VALUES = frozenset(("W", "H"))


def verbs():
    """The global names libmoy's Lua binding installs as verbs."""
    src = (LIBMOY / "moy_lua.c").read_text(encoding="utf-8")
    head = src.index("static const luaL_Reg VERBS[] = {")
    table = src[head:]
    table = table[:table.index("{NULL, NULL}")]
    host = src[src.index("static void open_host_verbs"):head]
    return frozenset(re.findall(r'\{"(\w+)",', table)) | frozenset(
        re.findall(r'lua_setglobal\(L, "(\w+)"\)', host))


def wasm_imports():
    """The names of the compiled binding's import table, in its order."""
    src = (LIBMOY / "moy_wasm.c").read_text(encoding="utf-8")
    block = src[src.index("static const NativeSymbol NATIVES[] = {"):]
    block = block[:block.index("};")]
    return re.findall(r'\{"(\w+)",\s*FN\(w_\w+\)', block)
