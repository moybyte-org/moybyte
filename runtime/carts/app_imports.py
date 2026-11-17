"""What an installed Python cart may import, and the check the installer
makes (docs/os_2026-11/processes.md §2).

A Python cart runs in the console's one VM, where every module the console
is built from can be imported by its name. Two of them are the app API
(docs/app_api_v1.md): `moybyte`, the app's verbs and its device objects, and
`ui`, the public UI API. The rest of the console is not, and a cart that
imports it is refused at install, the module named. Beside the two, a cart
imports the language's own library, the names in STANDARD.

This is a statement of what the console will not rename under an app, not a
boundary: Python is trusted, and `__import__`, `exec` or `getattr` walk past
a scan of the source. The scan reads the source without `ast`, which
MicroPython lacks: strings and comments are skipped, and every statement,
wherever it sits, that begins with `import` or `from ... import` names its
module (tests/test_app_imports.py holds it to `ast` over the whole tree).
"""

# The app API: the console's modules an app imports.
PUBLIC = ("moybyte", "ui")

# The language's library, as MicroPython carries it (the `u` spellings
# too), and the standard names the OS documents promise: `machine` for a
# lease (docs/os_2026-11/hardware.md §1), `asyncio` on the scheduler
# (processes.md §3), `os`, `socket`, `requests` and `logging`.
STANDARD = (
    "array", "asyncio", "binascii", "builtins", "cmath", "collections",
    "deflate", "errno", "framebuf", "gc", "hashlib", "heapq", "io", "json",
    "logging", "machine", "math", "micropython", "os", "random", "re",
    "requests", "select", "socket", "ssl", "struct", "sys", "time", "zlib",
    "uarray", "uasyncio", "ubinascii", "ucollections", "uerrno", "uhashlib",
    "uheapq", "uio", "ujson", "uos", "urandom", "ure", "uselect", "usocket",
    "ussl", "ustruct", "usys", "utime", "uzlib",
)

ALLOWED = frozenset(PUBLIC + STANDARD)

_OPEN = "([{"
_CLOSE = ")]}"


def _statements(src):
    """(line, text) for each simple statement of `src`: strings blanked to
    `""`, comments dropped, continued lines joined, and a line split at `;`
    and at a `:` outside brackets, so the body of `if x: import y` is a
    statement of its own."""
    out = []
    buf = []
    depth = 0
    line = 1
    start = 1
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "'" or c == '"':
            q = src[i:i + 3] if src[i:i + 3] in ("'''", '"""') else c
            i += len(q)
            while i < n and src[i:i + len(q)] != q:
                if src[i] == "\\":
                    i += 1
                if i < n and src[i] == "\n":
                    line += 1
                i += 1
            i += len(q)
            buf.append('""')
            continue
        if c == "\\" and src[i + 1:i + 2] == "\n":
            i += 2
            line += 1
            buf.append(" ")
            continue
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth = max(0, depth - 1)
        if c == "\n":
            line += 1
            if depth:
                buf.append(" ")
                i += 1
                continue
        if (c == "\n" or c == ";") or (c == ":" and not depth):
            out.append((start, "".join(buf).strip()))
            buf = []
            start = line
            i += 1
            continue
        buf.append(c)
        i += 1
    out.append((start, "".join(buf).strip()))
    return out


def _words(text):
    return text.replace("(", " ").replace(")", " ").replace(",", " , ").split()


def imports(src):
    """(line, module) for every module `src` imports, `module` the name
    before its first dot; a relative import names "."."""
    out = []
    for line, text in _statements(src):
        w = _words(text)
        if not w:
            continue
        if w[0] == "import":
            expect = True
            for word in w[1:]:
                if expect:
                    out.append((line, word.split(".")[0]))
                    expect = False
                elif word == ",":
                    expect = True
        elif w[0] == "from" and len(w) > 2 and "import" in w[2:]:
            mod = w[1]
            out.append((line, "." if mod.startswith(".") else mod.split(".")[0]))
    return out


def refused(src):
    """(line, module) for every import of `src` an app may not make."""
    return [(line, mod) for line, mod in imports(src) if mod not in ALLOWED]
