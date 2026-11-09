"""moy-app: Moybyte's apps, from moy-spec's `moy` (sdk/README.md).

moy-spec's `moy` runs `moy-<x>` from PATH for a subcommand `<x>` it does not
have, so with this package installed (`pip install -e .` in moybyte puts
`moy-app` on PATH) `moy app <verb>` is this:

    moy app new <name> [--tier python|lua|wasm|wasi|c] [--service TIER]
                       [--type app|service|command|game]
        a cart folder, <name>.moy, from a template per tier, its manifest
        carrying Moybyte's fields (docs/app_api_v1.md section 2); --service
        gives the app its own service, `entries`, in that tier
    moy app check <cart> [--strict]
        the manifest as the console's readers take it, and the permissions
        the cart's programs use against the ones it declares: each name a
        module imports (wasm), each row a native module links (its
        .moy.rows), each name a script reaches (Python, Lua: a best-effort
        scan), from native/os_calls.json; --strict fails on a warning too
    moy app build <cart> [--chip esp32s3|esp32p4|host ...] [--unsigned]
        the cart's compiled programs into its folder, from src/: a wasm main
        or service (tools/wasm_cart.py: the portable .wasm, and a board
        chip's AOT module), a native one (tools/elf_cart.py: a module per
        chip, which a native cart must name); a Python or Lua cart runs from
        its source and has nothing to build
    moy app stubs <cart>
        the headers or stubs for the cart's tier, for an editor or a build:
        C include/moybyte/*.h and compile_flags.txt, Lua moybyte_os.lua,
        Python __builtins__.pyi and moybyte.pyi
    moy app push <cart> [--board ID | --store DIR]
        the cart, its stubs left behind, onto a board's store
        (tools/board.py ID push) or the simulator's (~/.moybyte/projects,
        where tools/simulate_desktop.py runs it from)

The tiers are the start-here table's: `wasm` is an app compiled to wasm
(moybyte.app imports), `wasi` a WASI command or service, `c` a native (ELF)
module.
"""

import argparse
import glob
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from runtime.moy_store_base import VENDOR_KEY, VENDOR_FIELDS, is_cart_id, slug  # noqa: E402
from runtime import app_perms, scheduler  # noqa: E402

from . import os_table, templates  # noqa: E402

PROG = "moy app"
TIERS = ("python", "lua", "wasm", "wasi", "c")
# A tier's name in native/os_calls.json.
TABLE_TIER = {"python": "python", "lua": "lua", "wasm": "wasm", "wasi": "wasm", "c": "elf"}
TYPES = ("game", "app", "service", "command")
# The rest the console reads (a seed's, the shell's own): a cart a bar tool or
# a script (runtime/run/player.py), a wallpaper, a theme package.
OTHER_TYPES = ("tool", "script", "wallpaper", "theme")
TIER_TYPES = {"python": TYPES, "lua": TYPES, "wasm": ("app", "game"),
              "wasi": ("command", "service"), "c": ("command", "service")}
SERVICE_TIERS = ("python", "lua", "wasi", "c")
SERVICE_FILE = {"python": "service.py", "lua": "service.lua", "wasi": "service.wasm",
                "c": "service.elf"}
CHIPS = ("esp32s3", "esp32p4", "host")
# A theme package (runtime/carts/cart_index.py): data, no program.
THEME = "theme"
WASM_APP = "moybyte.app"
WASM_OS = "moybyte.os"
# A WASI program's own files: WASI's path calls, which reach only the folder
# its `files` permission names, beside its moybyte.os `files_*` rows; read
# from its source, the C library's file calls.
WASI_PREVIEW1 = "wasi_snapshot_preview1"
WASI_FILE_CALLS = ("fopen", "freopen", "open", "opendir", "mkdir", "rmdir", "rename",
                   "unlink", "remove", "stat", "access")
# Answers a call only from the background: an app in front opens, shares and
# picks without it (docs/app_api_v1.md section 6).
BACKGROUND_ONLY = ("front",)
# Used with no call of the cart's own: the console arms the two-console
# match for a cart that declares it (runtime/run/player.py, start).
USED_UNCALLED = ("multiplayer",)
SIM_STORE = os.path.expanduser("~/.moybyte/projects")
SDK_INCLUDE = os.path.join(ROOT, "sdk", "include")
STUBS = os.path.join(HERE, "stubs")
LIBMOY = os.path.join(ROOT, "native", "moy_gfx", "libmoy")
# What `stubs` writes per tier: (path in the cart, its source).
STUB_FILES = {
    "python": (("__builtins__.pyi", os.path.join(STUBS, "__builtins__.pyi")),
               ("moybyte.pyi", os.path.join(STUBS, "moybyte.pyi"))),
    "lua": (("moybyte_os.lua", os.path.join(STUBS, "moybyte_os.lua")),),
    "wasm": (("include/moybyte/app.h", os.path.join(SDK_INCLUDE, "moybyte", "app.h")),
             ("include/moybyte/os.h", os.path.join(SDK_INCLUDE, "moybyte", "os.h"))),
    "wasi": (("include/moybyte/os.h", os.path.join(SDK_INCLUDE, "moybyte", "os.h")),),
    "c": (("include/moybyte/os.h", os.path.join(SDK_INCLUDE, "moybyte", "os.h")),
          ("include/moybyte/game.h", os.path.join(SDK_INCLUDE, "moybyte", "game.h")),
          ("include/moy.h", os.path.join(LIBMOY, "moy.h"))),
}
COMPILE_FLAGS = {"wasm": "--target=wasm32-wasip1\n-Iinclude\n",
                 "wasi": "--target=wasm32-wasip1\n-Iinclude\n",
                 "c": "-Iinclude\n"}
# Every file `stubs` may write, which a push leaves behind.
STUB_PATHS = sorted({p for fs in STUB_FILES.values() for p, _s in fs} | {"compile_flags.txt"})


class Refused(Exception):
    pass


def die(msg):
    print("moy app: " + msg, file=sys.stderr)
    return 1


# -- the cart ----------------------------------------------------------------

def read_manifest(cart):
    path = os.path.join(cart, "manifest.json")
    try:
        with open(path, encoding="utf-8") as f:
            man = json.load(f)
    except OSError:
        raise Refused("%s has no manifest.json" % cart)
    except ValueError as e:
        raise Refused("%s: manifest.json does not parse: %s" % (cart, e))
    if not isinstance(man, dict):
        raise Refused("%s: manifest.json is not an object" % cart)
    return man


def vendor(man):
    v = man.get(VENDOR_KEY)
    return v if isinstance(v, dict) else {}


def tier_of(man, cart):
    """The cart's tier, by the start-here table: a native cart (its chips, or
    C source and no runtime yet), a wasm app or a WASI program, else its
    runtime, Python by default."""
    ven = vendor(man)
    if isinstance(ven.get("native"), list):
        return "c"
    if "runtime" not in man and "main" not in man and glob.glob(os.path.join(cart, "src", "*.c")):
        return "c"
    rt = man.get("runtime") or "python"
    if rt == THEME:
        return THEME
    if rt == "wasm":
        return "wasi" if ven.get("type") in ("command", "service") else "wasm"
    return rt


def _scripts(man, ext):
    """The cart's own scripts of a script tier: main and its `sources`."""
    out = []
    for n in [man.get("main")] + list(man.get("sources") or ()):
        if isinstance(n, str) and n.endswith(ext) and n not in out:
            out.append(n)
    return out


# -- what a program uses -------------------------------------------------------

def _leb(b, i):
    n = shift = 0
    while True:
        x = b[i]
        i += 1
        n |= (x & 0x7F) << shift
        shift += 7
        if not x & 0x80:
            return n, i


def wasm_imports(data):
    """[(module, name)] of a wasm module's imports."""
    if data[:4] != b"\0asm":
        raise Refused("not a wasm module")
    i, out = 8, []
    while i < len(data):
        sid = data[i]
        size, i = _leb(data, i + 1)
        end = i + size
        if sid == 2:
            count, i = _leb(data, i)
            for _ in range(count):
                names = []
                for _k in range(2):
                    n, i = _leb(data, i)
                    names.append(data[i:i + n].decode("utf-8", "replace"))
                    i += n
                kind = data[i]
                i += 1
                if kind in (0, 4):              # a function's type, a tag's
                    i += 1 if kind == 4 else 0
                    _t, i = _leb(data, i)
                elif kind in (1, 2):            # a table's reftype, then limits
                    i += 1 if kind == 1 else 0
                    flags = data[i]
                    _m, i = _leb(data, i + 1)
                    if flags & 1:
                        _m, i = _leb(data, i)
                elif kind == 3:                 # a global's type and mutability
                    i += 2
                out.append(tuple(names))
            return out
        i = end
    return out


def elf_rows(data):
    """The ordinals a native module links (its .moy.rows section, one 4-byte
    word a row its stubs call), or None where it has no such section."""
    if data[:4] != b"\x7fELF":
        raise Refused("not an ELF file")
    wide, end = data[4] == 2, "<" if data[5] == 1 else ">"
    if wide:
        shoff, = struct.unpack_from(end + "Q", data, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from(end + "HHH", data, 0x3A)
    else:
        shoff, = struct.unpack_from(end + "I", data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from(end + "HHH", data, 0x2E)

    def section(k):
        at = shoff + k * shentsize
        if wide:
            name, _t, _f, _a, off, size = struct.unpack_from(end + "IIQQQQ", data, at)
        else:
            name, _t, _f, _a, off, size = struct.unpack_from(end + "IIIIII", data, at)
        return name, off, size

    _n, stroff, _s = section(shstrndx)
    for k in range(shnum):
        name, off, size = section(k)
        label = data[stroff + name:data.index(b"\0", stroff + name)]
        if label == b".moy.rows":
            return sorted(set(struct.unpack_from(end + "%dI" % (size // 4), data, off)))
    return None


def _strip_c(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


def _c_calls(text):
    return set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", _strip_c(text)))


def _chains(names, table):
    """{name: permissions} of the table's names among the dotted `names`,
    each matched by a prefix of its parts (`files.load` and `files`)."""
    out = {}
    for chain in names:
        parts = chain.split(".")
        for k in range(1, len(parts) + 1):
            n = ".".join(parts[:k])
            if n in table:
                out[n] = table[n]
    return out


def py_names(text):
    """The dotted names a Python script spells, out of its comments and
    strings."""
    out, cur, prev = set(), None, None
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        toks = []
    for t in toks:
        if t.type == tokenize.NAME:
            if prev == ".":
                if cur is not None:
                    cur.append(t.string)
            else:
                if cur:
                    out.add(".".join(cur))
                cur = [t.string]
        elif not (t.type == tokenize.OP and t.string == "." and cur is not None):
            if cur:
                out.add(".".join(cur))
            cur = None
        prev = t.string if t.type in (tokenize.NAME, tokenize.OP) else None
    if cur:
        out.add(".".join(cur))
    return out


def lua_names(text):
    """The dotted names a Lua script spells, out of its comments and strings."""
    text = re.sub(r"--\[(=*)\[.*?\]\1\]", " ", text, flags=re.S)
    text = re.sub(r"--[^\n]*", " ", text)
    text = re.sub(r"\[(=*)\[.*?\]\1\]", '""', text, flags=re.S)
    text = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', text)
    return set(re.findall(r"(?<![\w.:])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", text))


class Program:
    """One program of a cart: what it is, the names it uses with the
    permissions each answers under, and whether that came from a built
    module (exact) or a scan of its source."""

    def __init__(self, label, tier, background):
        self.label, self.tier, self.background = label, tier, background
        self.used = {}
        self.linked = {}                # rows the SDK's C library links, perhaps unused
        self.exact = True
        self.errors = []
        self.missing = False


def _read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def _wasm_program(prog, cart, stem, app, man):
    built = os.path.join(cart, stem + ".wasm")
    wat = os.path.join(cart, "src", stem + ".wat")
    c = os.path.join(cart, "src", stem + ".c")
    if os.path.isfile(built) or os.path.isfile(wat):
        if os.path.isfile(built):
            data = _read(built, "rb")
        else:
            from tools import wat as _wat
            data = _wat.assemble(_read(wat))
        mods = {"app": os_table.WASM_APP, "os": os_table.WASM_OS}
        for module, name in wasm_imports(data):
            if module == WASI_PREVIEW1 and not app and name.startswith("path_"):
                prog.used["%s.%s" % (module, name)] = ("files",)
                continue
            if module == WASM_APP:
                table = mods["app"]
            elif module == WASM_OS:
                table = mods["os"]
            else:
                continue
            if name not in table:
                prog.errors.append('imports %s "%s", which no console serves (table %d)'
                                   % (module, name, os_table.VERSION))
                continue
            prog.used["%s.%s" % (module, name)] = table[name]
            if module == WASM_APP and WASM_APP not in (man.get("extensions") or ()):
                prog.errors.append('imports %s "%s" and does not declare the extension '
                                   '("extensions": ["%s"])' % (module, name, WASM_APP))
    elif os.path.isfile(c):
        prog.exact = False
        calls = _c_calls(_read(c))
        if app:
            prog.used = {"%s.%s" % (WASM_APP, n): p for n, p in os_table.WASM_APP.items()
                         if n in calls}
        else:
            prog.used = {"%s.%s" % (WASM_OS, n): p for n, p in os_table.WASM_OS.items()
                         if "moy_" + n in calls}
            prog.used.update((n, ("files",)) for n in WASI_FILE_CALLS if n in calls)
    else:
        prog.missing = True


def libc_rows():
    """The rows the SDK's C library calls (sdk/libc/): a module that links
    stdio links them whether its program reaches them or not."""
    calls = set()
    for f in glob.glob(os.path.join(ROOT, "sdk", "libc", "*.c")):
        calls |= _c_calls(_read(f))
    return {n for n, _p in os_table.ELF.values() if n in calls}


def _elf_program(prog, cart, stem, src_dir):
    mods = sorted(glob.glob(os.path.join(cart, glob.escape(stem) + ".*.elf")))
    srcs = sorted(glob.glob(os.path.join(src_dir, "*.c")))
    calls = set()
    for s in srcs:
        calls |= _c_calls(_read(s))
    if mods:
        libc = libc_rows()
        for m in mods:
            rows = elf_rows(_read(m, "rb"))
            if rows is None:
                prog.errors.append("%s has no .moy.rows: not built by the SDK"
                                   % os.path.basename(m))
                continue
            for o in rows:
                if o in os_table.ELF:
                    name, perms = os_table.ELF[o]
                    if name in libc and name not in calls:
                        prog.linked[name] = perms
                    else:
                        prog.used[name] = perms
                elif o in os_table.RETIRED:
                    prog.errors.append("%s calls row %d, retired: rebuild it"
                                       % (os.path.basename(m), o))
                else:
                    prog.errors.append("%s calls row %d, past table %d: it needs a newer "
                                       "console" % (os.path.basename(m), o, os_table.VERSION))
        return
    if not srcs:
        prog.missing = True
        return
    prog.exact = False
    by_name = {n: p for n, p in os_table.ELF.values()}
    prog.used = {n: p for n, p in by_name.items() if n in calls}


def _script_program(prog, cart, files, tier):
    prog.exact = False
    names = set()
    for n in files:
        path = os.path.join(cart, n)
        if not os.path.isfile(path):
            prog.missing = True
            continue
        text = _read(path)
        names |= py_names(text) if tier == "python" else lua_names(text)
    prog.used = _chains(names, os_table.PY if tier == "python" else os_table.LUA)


def programs(man, cart):
    """The cart's programs: its main, and its own service where it declares
    one (scheduler.entry_of)."""
    tier = tier_of(man, cart)
    if tier == THEME:
        return []
    ven = vendor(man)
    bg = ven.get("type") == "service"
    out = []
    main = Program("main", tier, bg)
    if tier in ("python", "lua"):
        _script_program(main, cart, _scripts(man, ".py" if tier == "python" else ".lua"), tier)
    elif tier in ("wasm", "wasi"):
        stem = (man.get("main") or "main.wasm").rsplit(".", 1)[0]
        _wasm_program(main, cart, stem, tier == "wasm", man)
    else:
        _elf_program(main, cart, "main", os.path.join(cart, "src"))
    out.append(main)
    got = scheduler.entry_of({"entries": ven.get("entries")})
    if got is not None:
        f, etier = got
        stem = f.rsplit(".", 1)[0]
        name = {"python": "python", "lua": "lua", "wasm": "wasi", "elf": "c"}[etier]
        svc = Program("service (%s)" % f, name, True)
        if etier in ("python", "lua"):
            _script_program(svc, cart, [f], etier)
        elif etier == "wasm":
            _wasm_program(svc, cart, stem, False, man)
        else:
            _elf_program(svc, cart, stem, os.path.join(cart, "src", stem))
        out.append(svc)
    return out


# -- check ----------------------------------------------------------------------

def _base(p):
    """A declared permission as the table names it: `files:<folder>` is
    files, `lease:<row>` lease."""
    if isinstance(p, str) and p.startswith(("files:", "lease:")):
        return p.split(":", 1)[0]
    return p


def _inferable(tier):
    """The permissions some name of `tier` answers under: the ones a use
    can show."""
    tables = {"python": [os_table.PY], "lua": [os_table.LUA],
              "wasm": [os_table.WASM_APP, os_table.WASM_OS], "wasi": [os_table.WASM_OS],
              "c": [{n: p for n, p in os_table.ELF.values()}]}[tier]
    return {p for t in tables for ps in t.values() for p in ps}


def _strs(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def check_manifest(man, cart, errors, warnings):
    ven_raw = man.get(VENDOR_KEY)
    for k in VENDOR_FIELDS:
        if k in man:
            errors.append('"%s" is at the top level, where it reads as absent: it goes '
                          'under "%s"' % (k, VENDOR_KEY))
    if ven_raw is None:
        warnings.append('no "%s" object: a game with no permissions' % VENDOR_KEY)
    elif not isinstance(ven_raw, dict):
        errors.append('"%s" is not an object: every field in it reads as absent' % VENDOR_KEY)
    ven = vendor(man)
    for k in ven:
        if k not in VENDOR_FIELDS:
            errors.append('"%s.%s" is no Moybyte field: it reads as absent' % (VENDOR_KEY, k))
    if not is_cart_id(man.get("id")):
        warnings.append('no "id" of the form <author>.<name>: the cart is keyed by its '
                        'folder name')
    rt = man.get("runtime")
    if rt is not None and rt not in ("python", "lua", "wasm", THEME):
        errors.append('"runtime" "%s" runs on no Moybyte tier' % rt)
    tier = tier_of(man, cart)
    typ = ven.get("type", "game")
    if tier == THEME:
        if typ != THEME:
            errors.append('a theme package\'s "type" is "%s"' % THEME)
        return []
    if typ not in TYPES + OTHER_TYPES:
        errors.append('"type" "%s" is none of %s' % (typ, ", ".join(TYPES)))
    main = man.get("main")
    if tier in ("python", "lua"):
        if not isinstance(main, str) or not os.path.isfile(os.path.join(cart, main)):
            errors.append('"main" names no file in the cart: %r' % (main,))
    elif tier in ("wasm", "wasi"):
        stem = (main or "main.wasm").rsplit(".", 1)[0]
        if not os.path.isfile(os.path.join(cart, main or "main.wasm")):
            if glob.glob(os.path.join(cart, "src", stem + ".*")):
                warnings.append("%s is not built: `%s build` builds it from src/"
                                % (main or "main.wasm", PROG))
            else:
                errors.append('"main" %r is no file, and src/ has nothing to build it from'
                              % (main,))
    else:
        chips = ven.get("native")
        if chips is None:
            warnings.append("not built: `%s build --chip CHIP` builds main.<chip>.elf from "
                            "src/" % PROG)
        elif not _strs(chips) or any(c not in CHIPS for c in chips):
            errors.append('"native" is not a list of chips (%s)' % ", ".join(CHIPS))
        else:
            for c in chips:
                if not os.path.isfile(os.path.join(cart, "main.%s.elf" % c)):
                    errors.append('"native" names %s and main.%s.elf is missing' % (c, c))
    perms = ven.get("permissions", [])
    if not _strs(perms):
        errors.append('"permissions" is not a list of names: the cart holds none')
        perms = []
    tiers = [TABLE_TIER[tier]]
    entry = None
    if "entries" in ven:
        entry = scheduler.entry_of({"entries": ven.get("entries")})
        bad = not isinstance(ven["entries"], dict) or set(ven["entries"]) != {"service"}
        if entry is None or bad:
            errors.append('"entries" does not read: {"service": "<file>.py|.lua|.wasm|.elf"}, '
                          "a file in the cart")
        if typ != "app":
            errors.append('"entries" is an app\'s: a "%s" cart has no service of its own' % typ)
    if entry is not None:
        f, etier = entry
        tiers.append(etier)
        stem = f.rsplit(".", 1)[0]
        if etier in ("python", "lua") and not os.path.isfile(os.path.join(cart, f)):
            errors.append("the service entry %s is no file in the cart" % f)
        elif etier == "wasm" and not os.path.isfile(os.path.join(cart, f)):
            if glob.glob(os.path.join(cart, "src", stem + ".*")):
                warnings.append("%s is not built: `%s build` builds it from src/" % (f, PROG))
            else:
                errors.append("the service entry %s is no file, and src/%s.* is none" % (f, stem))
        elif etier == "elf" and not glob.glob(os.path.join(cart, glob.escape(stem) + ".*.elf")):
            if glob.glob(os.path.join(cart, "src", stem, "*.c")):
                warnings.append("%s is not built: `%s build --chip CHIP` builds %s.<chip>.elf "
                                "from src/%s/" % (f, PROG, stem, stem))
            else:
                errors.append("the service entry %s has no %s.<chip>.elf and no src/%s/*.c"
                              % (f, stem, stem))
    for p in perms:
        r = app_perms.row(p)
        if r is None:
            errors.append('"%s" is no permission (native/os_calls.json): it grants nothing' % p)
        elif not any(t in r[app_perms.TIERS] for t in tiers):
            warnings.append('"%s" is never granted on the %s tier' % (p, tier))
    req = ven.get("required", [])
    if not _strs(req):
        errors.append('"required" is not a list of names')
    else:
        for p in req:
            if p not in perms:
                errors.append('"required" names "%s", which "permissions" does not ask for' % p)
    if (tier == "c" or (entry and entry[1] == "elf")) and "full_access" not in perms:
        errors.append('a native module runs only with "full_access" in "permissions"')
    for k in ("receives", "handles"):
        if k in ven and not _strs(ven[k]):
            errors.append('"%s" is not a list of names' % k)
    if "animates" in ven and not isinstance(ven["animates"], bool):
        errors.append('"animates" is not true or false')
    return perms


def check_cart(cart):
    """(errors, warnings, notes) for the cart folder `cart`."""
    errors, warnings, notes = [], [], []
    man = read_manifest(cart)
    perms = check_manifest(man, cart, errors, warnings)
    declared = {_base(p) for p in perms}
    used_any = set()
    inferable = set()
    for prog in programs(man, cart):
        errors += ["%s: %s" % (prog.label, e) for e in prog.errors]
        if prog.missing:
            continue
        inferable |= _inferable(prog.tier)
        how = "" if prog.exact else " (read from its source: build it for the exact list)"
        by_perm = {}
        for name, ps in sorted(prog.used.items()):
            need = [p for p in ps if prog.background or p not in BACKGROUND_ONLY]
            used_any |= set(ps)
            if need:
                by_perm.setdefault(tuple(need), []).append(name)
            if need and not any(p in declared for p in need):
                warnings.append('%s uses %s, which needs %s: not in "permissions"%s'
                                % (prog.label, name, " or ".join('"%s"' % p for p in need), how))
        for ps, names in sorted(by_perm.items()):
            notes.append("%s uses %s: %s" % (prog.label, " or ".join(ps), ", ".join(names)))
        linked = {}
        for name, ps in sorted(prog.linked.items()):
            used_any |= set(ps)
            for p in ps:
                linked.setdefault(p, []).append(name)
        for p, names in sorted(linked.items()):
            if p not in declared and not any(p in ps for ps in by_perm):
                notes.append('%s links %s through the C library: "%s" where it reaches them'
                             % (prog.label, ", ".join(names), p))
    for p in perms:
        b = _base(p)
        if b in inferable and b not in used_any and b not in USED_UNCALLED:
            warnings.append('"%s" is declared and nothing the cart calls needs it' % p)
    return errors, warnings, notes


def cmd_check(a):
    try:
        errors, warnings, notes = check_cart(a.cart)
    except Refused as e:
        return die(str(e))
    for n in notes:
        print("  " + n)
    for w in warnings:
        print("warning: " + w)
    for e in errors:
        print("error: " + e)
    if errors or (a.strict and warnings):
        return 1
    print("%s: ok%s" % (a.cart, " (%d warnings)" % len(warnings) if warnings else ""))
    return 0


# -- new ------------------------------------------------------------------------

def _c_str(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def new_cart(name, tier="python", service=None, typ=None, into="."):
    """The path of a new cart folder `<name>.moy` under `into`."""
    if tier not in TIERS:
        raise Refused("--tier is one of %s" % ", ".join(TIERS))
    title = os.path.basename(name[:-4] if name.endswith(".moy") else name)
    folder = os.path.join(into, slug(title) + ".moy" if not name.endswith(".moy")
                          else os.path.basename(name))
    if os.path.exists(folder):
        raise Refused("%s exists" % folder)
    typ = typ or TIER_TYPES[tier][1 if tier in ("python", "lua") else 0]
    if typ not in TIER_TYPES[tier]:
        raise Refused("a %s cart is a %s" % (tier, " or ".join(TIER_TYPES[tier])))
    if service is not None:
        if service not in SERVICE_TIERS:
            raise Refused("--service is one of %s" % ", ".join(SERVICE_TIERS))
        if typ != "app":
            raise Refused("--service gives an app its own service; this cart is a %s" % typ)
    subs = {"title_c": _c_str(title), "title_str": json.dumps(title, ensure_ascii=False)}
    files = {}
    ven = {"type": typ, "permissions": []}
    man = {"format": "moy-1", "title": title, "id": "local." + slug(title)}
    if tier in ("python", "lua"):
        ext = ".py" if tier == "python" else ".lua"
        man.update({"runtime": tier, "main": "main" + ext})
        if typ == "service":
            files["main" + ext] = templates.PY_SERVICE if tier == "python" else templates.LUA_SERVICE
            ven["receives"] = ["ping"]
        else:
            files["main" + ext] = templates.PY_APP if tier == "python" else templates.LUA_APP
            ven["permissions"] = ["prefs"]
    elif tier == "wasm":
        man.update({"runtime": "wasm", "main": "main.wasm", "memory": 2,
                    "extensions": [WASM_APP]})
        files["src/main.c"] = templates.WASM_APP
        ven["permissions"] = ["prefs"]
    else:
        if tier == "wasi":
            man.update({"runtime": "wasm", "main": "main.wasm"})
        else:
            del man["format"]
            ven["permissions"] = ["full_access"]
        files["src/main.c"] = templates.C_SERVICE if typ == "service" else templates.C_COMMAND
        if typ == "service":
            ven["receives"] = ["ping"]
    if service is not None:
        f = SERVICE_FILE[service]
        ven["entries"] = {"service": f}
        ven["receives"] = ["ping"]
        if service == "python":
            files[f] = templates.PY_SERVICE
        elif service == "lua":
            files[f] = templates.LUA_SERVICE
        elif service == "wasi":
            files["src/service.c"] = templates.C_SERVICE
        else:
            files["src/service/main.c"] = templates.C_SERVICE
            if "full_access" not in ven["permissions"]:
                ven["permissions"].append("full_access")
    man[VENDOR_KEY] = ven
    files["manifest.json"] = json.dumps(man, indent=2, ensure_ascii=False) + "\n"
    for rel, text in files.items():
        path = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text % subs if rel != "manifest.json" else text)
    write_stubs(folder)
    return folder


def _build_hint(folder, tier, service):
    if tier == "c" or service == "c":
        return ["%s build %s --chip esp32s3 --chip esp32p4" % (PROG, folder)]
    if tier in ("wasm", "wasi") or service == "wasi":
        return ["%s build %s" % (PROG, folder)]
    return []


def cmd_new(a):
    try:
        folder = new_cart(a.name, a.tier, a.service, a.type, a.dir)
    except Refused as e:
        return die(str(e))
    print("wrote %s" % folder)
    for h in _build_hint(folder, a.tier, a.service):
        print("  build: " + h)
    print("  next: %s check %s" % (PROG, folder))
    return 0


# -- build ----------------------------------------------------------------------

def _compiled(man, cart):
    """(tier, wasm, elf): the cart's tier, and whether it has a program to
    compile to wasm (its main, or its service entry) and one to a native
    module."""
    tier = tier_of(man, cart)
    entry = scheduler.entry_of({"entries": vendor(man).get("entries")})
    etier = entry[1] if entry else None
    return tier, tier in ("wasm", "wasi") or etier == "wasm", tier == "c" or etier == "elf"


def _compiler_failed(e):
    err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
    return Refused("the compiler failed:\n" + err.strip())


def build_cart(cart, chips=(), signed=True):
    """Build the cart's compiled programs into its own folder, from its src/:
    a wasm main or service by tools/wasm_cart.py (the portable .wasm, and an
    AOT module per board chip in `chips`), a native main or service by
    tools/elf_cart.py (a module per chip in `chips`, `host` among them).
    Signed unless `signed` is False, as those tools sign. Returns
    [(what, path)], empty for a cart that runs from its source."""
    man = read_manifest(cart)
    tier, wasm, elf = _compiled(man, cart)
    if not (wasm or elf):
        return []
    for c in chips:
        if c not in CHIPS:
            raise Refused("--chip is one of %s" % ", ".join(CHIPS))
    if elf and not chips:
        raise Refused("a native module is built per chip: --chip %s" % " --chip ".join(CHIPS))
    from tools import elf_cart, wasm_cart
    out = []
    if wasm:
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, os.path.basename(os.path.abspath(cart)))
            try:
                got = wasm_cart.build(cart, dst, [c for c in chips if c != "host"],
                                      signed=signed)
            except wasm_cart.ToolchainMissing as e:
                raise Refused("cannot build %s: %s" % (cart, e))
            except subprocess.CalledProcessError as e:
                raise _compiler_failed(e)
            for what, path in sorted(got.items()):
                to = os.path.join(cart, os.path.relpath(path, dst))
                shutil.copyfile(path, to)
                out.append((what, to))
    if elf:
        try:
            if tier == "c":
                got = elf_cart.build(cart, cart, chips, signed=signed)
            else:
                stem = elf_cart.service_entry(man)
                srcs = sorted(glob.glob(os.path.join(cart, "src", glob.escape(stem), "*.c")))
                if not srcs:
                    raise Refused("%s has no src/%s/*.c for its service" % (cart, stem))
                got = {"service." + c: elf_cart.build_module(
                    srcs, c, os.path.join(cart, "%s.%s.elf" % (stem, c)), signed=signed)
                    for c in chips}
        except elf_cart.ToolError as e:
            raise Refused(str(e))
        except subprocess.CalledProcessError as e:
            raise _compiler_failed(e)
        out += sorted(got.items())
    return out


def cmd_build(a):
    try:
        man = read_manifest(a.cart)
        built = build_cart(a.cart, a.chip, signed=not a.unsigned)
    except Refused as e:
        return die(str(e))
    if not built:
        tier = tier_of(man, a.cart)
        print("%s: %s" % (a.cart, "a theme package has no program" if tier == THEME else
                          "a %s cart runs from its source: nothing to build" % tier))
        return 0
    for what, path in built:
        print("%-16s %s (%d bytes)" % (what, path, os.path.getsize(path)))
    print("  next: %s check %s" % (PROG, a.cart))
    return 0


# -- stubs ----------------------------------------------------------------------

def stub_tiers(man, cart):
    """The tiers whose stubs the cart wants: its own and its service's."""
    tiers = [tier_of(man, cart)]
    if tiers == [THEME]:
        return []
    got = scheduler.entry_of({"entries": vendor(man).get("entries")})
    if got is not None:
        t = {"python": "python", "lua": "lua", "wasm": "wasi", "elf": "c"}[got[1]]
        if t not in tiers:
            tiers.append(t)
    return tiers


def write_stubs(cart):
    """[path in the cart] of the stubs written for the cart's tiers."""
    man = read_manifest(cart)
    out = []
    flags = None
    for t in stub_tiers(man, cart):
        for rel, src in STUB_FILES[t]:
            if rel in out:
                continue
            dst = os.path.join(cart, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            out.append(rel)
        flags = flags or COMPILE_FLAGS.get(t)
    if flags:
        with open(os.path.join(cart, "compile_flags.txt"), "w") as f:
            f.write(flags)
        out.append("compile_flags.txt")
    return out


def cmd_stubs(a):
    try:
        wrote = write_stubs(a.cart)
    except Refused as e:
        return die(str(e))
    for rel in wrote:
        print("wrote %s" % os.path.join(a.cart, rel))
    return 0


# -- push -----------------------------------------------------------------------

def _ignore_stubs(cart):
    stubs = {os.path.normpath(os.path.join(cart, p)) for p in STUB_PATHS}

    def ignore(d, names):
        return [n for n in names if os.path.normpath(os.path.join(d, n)) in stubs]
    return ignore


def stage(cart, into):
    """The cart copied into folder `into` under its own name, less its stubs."""
    dst = os.path.join(into, os.path.basename(os.path.normpath(cart)))
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(cart, dst, ignore=_ignore_stubs(cart))
    for d in ("include/moybyte", "include"):
        try:
            os.rmdir(os.path.join(dst, d))
        except OSError:
            pass
    return dst


def cmd_push(a):
    try:
        errors, warnings, _n = check_cart(a.cart)
    except Refused as e:
        return die(str(e))
    for w in warnings:
        print("warning: " + w, file=sys.stderr)
    if errors:
        for e in errors:
            print("error: " + e, file=sys.stderr)
        return die("%s does not check: not pushed" % a.cart)
    if a.board:
        with tempfile.TemporaryDirectory() as tmp:
            staged = stage(a.cart, tmp)
            return subprocess.call([sys.executable, os.path.join(ROOT, "tools", "board.py"),
                                    a.board, "push", staged])
    store = a.store or SIM_STORE
    os.makedirs(store, exist_ok=True)
    dst = stage(a.cart, store)
    print("pushed %s to the simulator's store: %s" % (a.cart, dst))
    print("  run: python3 %s --cart %s"
          % (os.path.join(ROOT, "tools", "simulate_desktop.py"), dst))
    return 0


# -- the command line -----------------------------------------------------------

def parser():
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n\n".join(__doc__.split("\n\n")[2:]))
    sub = ap.add_subparsers(dest="verb", metavar="{new,check,build,stubs,push}")
    p = sub.add_parser("new", help="a cart folder from a template")
    p.add_argument("name")
    p.add_argument("--tier", choices=TIERS, default="python")
    p.add_argument("--service", choices=SERVICE_TIERS,
                   help="the app's own service, in this tier (entries)")
    p.add_argument("--type", choices=TYPES)
    p.add_argument("--dir", default=".", help="where the folder goes")
    p = sub.add_parser("check", help="the manifest, and the permissions the programs use")
    p.add_argument("cart")
    p.add_argument("--strict", action="store_true", help="a warning fails too")
    p = sub.add_parser("build", help="the cart's compiled programs, from its src/")
    p.add_argument("cart")
    p.add_argument("--chip", action="append", default=[], choices=CHIPS,
                   help="a module for this chip too (a native cart needs one); repeatable")
    p.add_argument("--unsigned", action="store_true",
                   help="leave the modules' signatures off (a board runs them only "
                        "for a cart granted \"full_access\")")
    p = sub.add_parser("stubs", help="the headers or stubs for the cart's tier")
    p.add_argument("cart")
    p = sub.add_parser("push", help="onto a board's store or the simulator's")
    p.add_argument("cart")
    to = p.add_mutually_exclusive_group()
    to.add_argument("--board", help="a board id (tools/board.py ports)")
    to.add_argument("--store", help="a simulator store folder (default %s)" % SIM_STORE)
    return ap


def main(argv=None):
    ap = parser()
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.verb is None:
        ap.print_help()
        return 0
    return {"new": cmd_new, "check": cmd_check, "build": cmd_build, "stubs": cmd_stubs,
            "push": cmd_push}[a.verb](a)


if __name__ == "__main__":
    sys.exit(main())
