"""The ROLE door (docs/kernel_appabi_2026-10.md section 2.1): the upcall that
carries arguments, a compiled app's one way to a role row.

One script drives `moy_app_role` over a grant -- the C rows it runs itself,
their packed arguments refused when torn, the shell rows it hands to the
kernel's loop (`moy_loop_role`, counted ROLE), NEEDS_VM with no VM and with no
door, DENIED before any crossing -- and logs every answer. It runs over the
host's ctypes bindings and over the native modules on the desktop MicroPython
in the boards' object model, and the two logs must be equal line for line.

Then the door over a real console: the shell rows a compiled app's grant
reaches land on the console's servers through `RoleDoor`, a cart named by its
folder; and a files row's blob, which the user-files layer allocates, comes
back whole.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from unix_mp import require_unix_mp               # noqa: E402

SCRIPT = r'''
OK, FULL, DENIED, IO, BAD, ABSENT, NEEDS_VM = 0, -2, -4, -6, -7, -8, -9


def field(b):
    if isinstance(b, str):
        b = b.encode()
    n = len(b)
    return bytes((n & 255, n >> 8 & 255, n >> 16 & 255, n >> 24 & 255)) + b


def num(v):
    v &= 0xFFFFFFFF
    return field(bytes((v & 255, v >> 8 & 255, v >> 16 & 255, v >> 24 & 255)))


def run(sp, ap, lp, say):
    table = ap.table()
    row = {name: i for i, name in enumerate(table)}
    say("table", len(table), table[0], table[-1])
    app = ap.App(sp.Settings(lambda b: None))
    g = app.grant("local.door", ("prefs", "clipboard", "theme", "damage", "nav"), ns="door")
    bare = app.grant("local.bare", ("prefs",), ns="bare")

    def door(name, args=b"", cap=64, h=None):
        return app.role(g if h is None else h, row[name], args, cap)

    # C rows, run in C
    say("set", door("prefs.set", field("hi") + field("[1, 2]")))
    say("get", door("prefs.get", field("hi")))
    say("get small", door("prefs.get", field("hi"), cap=3))
    say("get absent", door("prefs.get", field("nope")))
    say("clear", door("prefs.clear", field("hi")), door("prefs.get", field("hi")))
    say("put", door("clipboard.put_text", field("copied")))
    say("text", door("clipboard.text"), door("clipboard.kind"), door("clipboard.seq"))
    say("put long", door("clipboard.put_text", field("x" * 4097)), door("clipboard.text"))
    tok = ap.tokens()[0]
    app.theme_write("dusk", "dark", {tok: 0x1234})
    say("theme", door("theme.name"), door("theme.variant"), door("theme.gen")[0] > 0,
        door("theme.light"))
    say("token", door("theme.token", num(0)), door("theme.token", num(1)),
        door("theme.token", num(999)))
    say("damage", door("damage.all"), app.damage_take())
    # the arguments refused
    say("torn", door("prefs.get", b"\x05\x00\x00\x00hi"))
    say("extra", door("prefs.get", field("hi") + field("x")))
    say("missing", door("prefs.set", field("hi")))
    say("noargs", door("clipboard.kind", field("x")))
    say("number", door("theme.token", field("ab")))
    say("objects", door("theme.colors"), door("surface.canvas"))
    say("past", app.role(g, len(table), b"", 8))
    # the grant
    say("denied", door("clipboard.text", h=bare), door("theme.set_variant", field("x"), h=bare))
    # shell rows: no door bound, then the kernel's loop with no dispatcher
    ap.door_bind(False)
    t0 = lp.upcalls()[1]
    say("no door", door("theme.set_variant", field("light")))
    ap.door_bind(True)
    lp.role(None)
    say("no dispatcher", door("theme.set_variant", field("light")))
    t1 = lp.upcalls()[1]
    say("refused", t1[4] - t0[4], t1[5] - t0[5])
    seen = []

    def disp(grant, r, args):
        seen.append((grant == g, table[r], args))
        if table[r] == "nav.open_app":
            return "x" * 100
        if table[r] == "nav.text_mode":
            raise RuntimeError("the server raised")
        if table[r] == "theme.set_skin":
            return -8
        return b"ok"
    lp.role(disp)
    say("crossed", door("theme.set_variant", field("light")), seen[-1])
    say("full", door("nav.open_app", field("calc")))
    say("raised", door("nav.text_mode", num(1)))
    say("code", door("theme.set_skin", field("x")))
    t2 = lp.upcalls()[1]
    say("role", t2[5] - t1[5], t2[4] - t1[4])
    lp.role(None)
    ap.door_bind(False)
    say("done", len(seen))
'''

VM_MAIN = r'''
import moy_spine
import moy_app
import moy_loop


def say(*a):
    print("L", " ".join(repr(x) if not isinstance(x, str) else x for x in a))


run(moy_spine, moy_app, moy_loop, say)
print("DONE")
'''


def _host_log():
    from tools import moy_app_binding, moy_spine_binding
    from runtime import moy_loop
    sp, ap = moy_spine_binding.binding(), moy_app_binding.binding()
    if sp is None or ap is None:
        pytest.skip("no C compiler: no host moy_app")
    out = []

    def say(*a):
        out.append(" ".join(repr(x) if not isinstance(x, str) else x for x in a))

    ns = {}
    exec(SCRIPT, ns)
    try:
        ns["run"](sp, ap, moy_loop, say)
    finally:
        moy_loop.role(None)
        ap.door_bind(False)
    return out


def test_the_door_on_the_host():
    log = _host_log()
    want = {
        "set": "set (0, b'')",
        "get": "get (6, b'[1, 2]')",
        "get small": "get small (-2, b'')",
        "get absent": "get absent (-8, b'')",
        "put long": "put long (-7, b'') (6, b'copied')",
        "torn": "torn (-7, b'')",
        "extra": "extra (-7, b'')",
        "missing": "missing (-7, b'')",
        "noargs": "noargs (-7, b'')",
        "objects": "objects (-7, b'') (-7, b'')",
        "denied": "denied (-4, b'') (-4, b'')",
        "no door": "no door (-9, b'')",
        "no dispatcher": "no dispatcher (-9, b'')",
        "refused": "refused 1 0",
        "full": "full (-2, b'')",
        "raised": "raised (-6, b'')",
        "code": "code (-8, b'')",
        "role": "role 4 0",
    }
    by = {}
    for line in log:
        for k in want:
            if line.startswith(k + " ") and k not in by:
                by[k] = line
    for k, v in want.items():
        assert by.get(k) == v, (k, by.get(k))
    crossed = [ln for ln in log if ln.startswith("crossed ")]
    assert crossed == ["crossed (2, b'ok') (True, 'theme.set_variant', b'\\x05\\x00\\x00\\x00light')"]


def test_the_native_door_is_the_hosts(tmp_path):
    exe = require_unix_mp(
        "moy_app", "moy_spine", "moy_loop", board_model=True,
        why="the ROLE door as the boards compile it, against the host's.")
    script = tmp_path / "role_door.py"
    script.write_text(SCRIPT + VM_MAIN)
    proc = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=60)
    lines = proc.stdout.splitlines()
    assert lines and lines[-1] == "DONE", proc.stdout[-3000:] + proc.stderr
    got = [ln[2:] for ln in lines if ln.startswith("L ")]
    want = _host_log()
    for i, (a, b) in enumerate(zip(got, want)):
        assert a == b, "line %d:\n  native %s\n  host   %s" % (i, a, b)
    assert len(got) == len(want)


# -- the door over a console ---------------------------------------------------------

def _field(b):
    if isinstance(b, str):
        b = b.encode()
    return len(b).to_bytes(4, "little") + b


def test_the_shell_rows_reach_the_consoles_servers(tmp_path):
    from ws_helpers import build_ws
    from runtime import moy_loop
    import moy_app
    ws = build_ws(tmp_path)
    app = ws.app_abi
    table = moy_app.table()
    row = {n: i for i, n in enumerate(table)}
    g = app.grant("local.door", ("theme", "nav", "wallpaper"), run=True)
    try:
        t0 = moy_loop.upcalls()[1][5]
        r, cur = app.role(g, row["wallpaper.current"], b"", 64)
        assert r == len(ws.look.wallpaper_id) and cur == ws.look.wallpaper_id.encode()
        r, fills = app.role(g, row["wallpaper.fills"], b"", 256)
        assert fills.split(b"\0")[:-1] == [f.encode() for f in ws.look.FILL_WALLPAPERS]
        r, projects = app.role(g, row["nav.projects"], b"", 4096)
        assert r >= 0
        folders = [p for p in projects.split(b"\0") if p]
        crossed = 5 + bool(folders)
        if folders:
            # a cart named by its folder resolves to the live list's own entry
            r, _ = app.role(g, row["nav.is_system_app"], _field(folders[0]), 8)
            assert r == 0
        r, _ = app.role(g, row["nav.is_system_app"], _field("no.such.folder"), 8)
        assert r == -8, "a folder the live list does not hold is ABSENT"
        r, _ = app.role(g, row["wallpaper.preview"], b"", 8)
        assert r == -7, "an object-shaped row has no door"
        assert moy_loop.upcalls()[1][5] - t0 == crossed, "each shell row crossed once, counted ROLE"
    finally:
        app.end(g)


def test_a_files_blob_comes_back_whole(tmp_path):
    from ws_helpers import build_ws
    import moy_app
    ws = build_ws(tmp_path)
    app = ws.app_abi
    row = {n: i for i, n in enumerate(moy_app.table())}
    g = app.grant("local.door", ("files",), kind="docs", run=True)
    try:
        text = "a note\n" * 300
        r, name = app.role(g, row["files.save"], _field("docs") + _field("note") + _field(text), 300)
        assert r > 0, r
        r, blob = app.role(g, row["files.load"], _field("docs") + _field(name), 4096)
        assert r == len(text.encode()) and blob == text.encode()
        r, _ = app.role(g, row["files.load"], _field("docs") + _field(name), 16)
        assert r == -2, "a blob longer than the buffer is FULL"
        r, names = app.role(g, row["files.list"], _field("docs"), 4096)
        assert name + b"\0" in names
        r, _ = app.role(g, row["files.list"], _field("drawings"), 64)
        assert r == -4, "a grant made with a files kind reaches that kind alone"
    finally:
        app.end(g)
