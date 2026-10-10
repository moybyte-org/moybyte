"""native/moy_app, the app ABI's kernel half, against the Python it replaced.

One script drives the grants, the policy and the C rows -- damage, the surface
rows over a pointer, the token table, prefs and the clipboard -- and logs every
answer; it runs over three implementations and
the logs must be equal line for line (tests/test_gfx_binding.py's pattern):

  * the oracle, tests/app_twin.py over tests/spine_twin.py's Settings: what
    `Damage`, `Prefs`, `Clipboard` and system_api's policy did before step 4;
  * the C over the host's ctypes binding (tools/moy_app_binding.py), the one
    the CPython console runs;
  * the C as the boards' native module, on the desktop MicroPython in the
    boards' object model (32-bit, REPR_C), over its native moy_spine.

The handle VALUES are in the log too: a grant is a kind-12 handle
(moy_htab.h's MOY_KIND_GRANT), slot and generation, as the oracle mints them.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import app_twin                                   # noqa: E402
import spine_twin                                 # noqa: E402
from unix_mp import require_unix_mp               # noqa: E402

SCRIPT = r'''
class _Row:
    h = 0x3005


class _Canvas:
    _crow = _Row()


class _Plain:
    pass


class _Servers:
    def __init__(self, say, ap=None, app=None):
        self.say, self.ap, self.app = say, ap, app

    def glyph(self, g, kind, rect, c, cv=None):
        self.say("glyph", g > 0, kind, rect, c, cv)

    def set(self, g, name, variant=None):
        self.say("set", name, variant)

    def set_variant(self, g, v):
        self.say("set_variant", v)

    def set_skin(self, g, name):
        self.say("set_skin", name)
        return 7

    # every shell row of the other roles: the grant's id, the arguments back
    def __getattr__(self, verb):
        if verb.startswith("_"):
            raise AttributeError(verb)

        def row(g, *a, **kw):
            return (verb, self.app.grant_id(g), a, sorted(kw.items()))
        return row


def run(sp, ap, say, mi):
    saved = []
    rows = sp.Settings(saved.append)
    app = ap.App(rows)
    g = app.grant("calc", ("damage", "prefs", "clipboard"), ns="calc")
    say("grant", g, app.count(), app.grant("calc", ("prefs",)) == g, app.count())
    g = app.grant("calc", ("damage", "prefs", "clipboard"), ns="calc")
    r = app.grant("local.notes", ("prefs", "files"), kind="docs", run=True)
    say("run", r, app.count(), app.end(r), app.end(r), app.count())
    r2 = app.grant("local.notes", ("prefs",), run=True)
    say("again", r2, r2 != r)
    for bad in (("", ("prefs",)), ("x" * 64, ("prefs",)), ("x", ("nope",))):
        try:
            app.grant(bad[0], bad[1])
            say("granted", bad[0][:4])
        except ValueError:
            say("refused", bad[0][:4])
    try:
        app.grant("x", ("prefs",), kind="recordings")
    except ValueError:
        say("refused kind")

    # -- damage
    d = ap.Damage(app, g)
    say("damage", app.damage_take())
    d.all()
    say("all", app.damage_take(), app.damage_take())
    d.all()
    d.again()
    app.damage_drop()
    say("drop", app.damage_take())

    # -- prefs
    p = ap.Prefs(app, g)
    say("absent", p.get("k"), p.get("k", 5))
    p.set("k", [1, "two", {"x": None}])
    say("set", p.get("k"), rows.text("calc_k"), saved[-1])
    p.set("n", -3)
    p.set("s", "a\"b")
    say("more", p.get("n"), p.get("s"), rows.keys())
    p.clear("k")
    say("clear", p.get("k", "gone"), saved[-1])
    p.clear("nothing")
    say("clear absent", len(saved))
    for bad in ("", "y" * 200):
        try:
            p.set(bad, 1)
            say("set", repr(bad[:3]))
        except ValueError:
            say("refused key", len(bad))
    big = "z" * 3000
    p.set("big", big)
    say("big", len(p.get("big")))

    # -- the clipboard
    c = ap.Clipboard(app, g)
    say("clip", repr(c.text()), c.kind(), c.seq())
    say("put", c.put_text("hello"), c.text(), c.kind(), c.seq())
    say("put utf8", c.put_text("h\u00e9"), c.text(), c.seq())
    say("long", c.put_text("x" * (ap.CLIP_MAX + 1)), c.text(), c.seq())
    say("full", c.put_text("y" * ap.CLIP_MAX), len(c.text()), c.seq())
    other = app.grant("files", ("clipboard",))
    say("shared", len(ap.Clipboard(app, other).text()))

    # -- the surface rows
    sg = app.grant("paint", ("surface", "theme"))
    s = ap.Surface(app, sg)
    say("unwritten", s.canvas(), s.size(), s.font_scale(), s.chrome_scale(),
        s.windowed(), s.bar_h(), s.pointer())
    cv = _Canvas()
    say("write", app.surface_write(cv, 320, 240, 2, 3, True, 18, 10, 20))
    say("surface", s.canvas() is cv, s.size(), s.font_scale(), s.chrome_scale(),
        s.windowed(), s.bar_h())
    late = app.grant("late", ("surface",))
    say("late", ap.Surface(app, late).canvas(), ap.Surface(app, late).size())
    plain = _Plain()
    app.surface_write(plain, 800, 480, 1, 1, False, 0)
    say("plain", ap.Surface(app, late).canvas() is plain, s.canvas() is plain,
        s.size(), s.windowed(), ap.Surface(app, g).__class__ is ap.Surface)
    app.surface_write(cv, 320, 240, 2, 3, True, 18, 10, 20)
    ptr = mi.Pointer(320, 240)
    ptr.place(50, 60)
    ptr.down = True
    say("bind", app.bind_pointer(ptr))
    pt = s.pointer()
    say("pointer", tuple(pt), pt.down, pt.click, pt.visible, pt.x, pt.y)
    ptr.down = False
    ptr.place(11, 25)
    say("live", tuple(s.pointer()))
    say("unbind", app.bind_pointer(None), s.pointer())
    srv = _Servers(say)
    app.serve("surface", srv)
    app.serve("theme", srv)
    s.glyph("close", (1, 2, 3, 4), 7)
    s.glyph("play", (0, 0, 8, 8), 9, cv=None)

    # -- the theme rows
    t = ap.Theme(app, sg)
    say("blank", t.colors(), t.gen(), t.light(), t.name(), t.variant(), t.skin(),
        t.token(0))
    app.theme_write("night", "dark", {"panel": 60, "edge": 13, "surface_light": False})
    d = t.colors()
    say("theme", sorted(d.items()), t.colors() is d, t.gen(), t.light(), t.name(),
        t.variant(), t.token(0), t.token(1), t.token(9), t.token(20))
    app.theme_write_skin("outline")
    say("skin", t.skin(), t.gen(), t.colors() is d, t.colors() == d)
    app.theme_write("machine", "light", {"surface_light": True, "panel": 1,
                                         "bar_light": True})
    say("light", t.light(), sorted(t.colors().items()), t.name(), t.variant())
    for bad in ({"nope": 1}, ):
        try:
            app.theme_write("x", "dark", bad)
            say("wrote", bad)
        except ValueError:
            say("refused token")
    try:
        app.theme_write("y" * 40, "dark", {})
        say("wrote long")
    except ValueError:
        say("refused name")
    try:
        t.token(99)
        say("token 99")
    except ValueError:
        say("refused role")
    say("kept", t.name(), t.gen())
    t.set("berry", variant="light")
    t.set_variant("dark")
    say("served", t.set_skin("outline"), sorted(app.served().items()))

    # -- the roles served in Python: the grant checked, the row counted, the
    # server called with the grant first
    every = app.grant("tracer", ("files", "carts", "nav", "notify", "wallpaper",
                                 "install", "artwork"))
    srv2 = _Servers(say, ap, app)
    for role in ("files", "carts", "nav", "notify", "wallpaper", "install"):
        app.serve(role, srv2)
    say("files", ap.Files(app, every).load("docs", "a"),
        ap.Files(app, every).begin(), ap.Carts(app, every).journal("c", "m", "s", grad=1))
    say("nav", ap.Nav(app, every).play("cart"), ap.Notify(app, every).achieve("open"),
        ap.Wallpaper(app, every).thumbnail(4, 3), ap.Install(app, every).op(None))
    try:
        ap.Files(app, sg).list("docs")
        say("files allowed")
    except ValueError as e:
        say("files denied", str(e))
    say("served", sorted(app.served().items()))

    # -- artwork: Paint's open picture, its rows
    art = ap.Artwork(app, every)
    say("artwork none", art.current(), art.follow("drawings", "a", "b"))
    rows.set("paint_doc", "pic 1")
    say("artwork", art.current(), art.follow("drawings", "nope", "x"),
        art.follow("docs", "pic 1", "x"), art.current())
    say("follow", art.follow("drawings", "pic 1", 'pic "2"'), art.current(),
        rows.get("paint_doc"))
    rows.set("paint_doc_kind", "project:caf\u00e9.moy")
    rows.set("paint_doc", "caf\u00e9 \U0001f600")
    say("unicode", art.current() == ("project:caf\u00e9.moy", "caf\u00e9 \U0001f600"),
        art.follow("project:caf\u00e9.moy", "caf\u00e9 \U0001f600", "n\u00e9w"),
        art.current() == ("project:caf\u00e9.moy", "n\u00e9w"))
    for bad in ("", "a\nb", "x" * 256):
        try:
            art.follow("project:caf\u00e9.moy", "n\u00e9w", bad)
            say("followed", len(bad))
        except ValueError:
            say("refused", len(bad))
    rows.set("paint_doc", 7)
    say("not a name", art.current())
    rows.delete("paint_doc")
    rows.delete("paint_doc_kind")
    say("tokens", list(ap.tokens()))

    # -- denied and stale
    n = app.grant("nav_only", ("nav",))
    for mk, call in ((ap.Prefs, lambda o: o.get("k")),
                     (ap.Clipboard, lambda o: o.text()),
                     (ap.Surface, lambda o: o.canvas()),
                     (ap.Theme, lambda o: o.colors()),
                     (ap.Damage, lambda o: o.all())):
        try:
            call(mk(app, n))
            say("allowed")
        except ValueError as e:
            say("denied", str(e))
    app.end(n)
    try:
        ap.Prefs(app, n).get("k")
    except ValueError as e:
        say("stale", str(e))

    # -- the counters
    say("counts", list(app.counts()))
    say("rows", list(ap.rows()))

    # -- the policy
    for perms in (["graphics"], ["files"], ["files:music", "prefs"],
                  ["prefs", "files:docs", "launch", "appearance", "clipboard"],
                  ["files:nonsense"], ["files", "files:music"], None, [3]):
        say("policy", perms, ap.policy(perms), ap.manifest_error(perms))
    for cid, title in (("moybyte.notes", "Notes"), (None, "My Notes!"),
                       (None, None), ("", ""), (None, "!!!"),
                       (None, "Caf\u00e9 9-b_c")):
        say("id", cid, title, ap.id_for(cid, title))
    say("perms", list(ap.perms()), list(ap.kinds()), list(ap.roles()))
'''

VM_MAIN = r'''
import moy_input
import moy_spine
import moy_app
LOG = []


def say(*a):
    print("L", " ".join(repr(x) if not isinstance(x, str) else x for x in a))


run(moy_spine, moy_app, say, moy_input)
print("DONE")
'''


def _log(sp, ap):
    out = []

    def say(*a):
        out.append(" ".join(repr(x) if not isinstance(x, str) else x for x in a))

    from runtime import moy_input
    ns = {}
    exec(SCRIPT, ns)
    ns["run"](sp, ap, say, moy_input)
    return out


def _oracle():
    return _log(spine_twin, app_twin)


def test_the_host_binding_is_the_oracle():
    from tools import moy_app_binding, moy_spine_binding
    sp, ap = moy_spine_binding.binding(), moy_app_binding.binding()
    if sp is None or ap is None:
        pytest.skip("no C compiler: no host moy_app")
    want = _oracle()
    got = _log(sp, ap)
    assert got == want, _diff(got, want)


def test_the_native_module_is_the_oracle(tmp_path):
    exe = require_unix_mp(
        "moy_app", "moy_spine", "moy_input", board_model=True,
        why="native/moy_app as the boards compile it, against the Python it "
            "replaced.")
    script = tmp_path / "app_parity.py"
    script.write_text(SCRIPT + VM_MAIN)
    proc = subprocess.run([exe, str(script)], capture_output=True, text=True,
                          timeout=60)
    lines = proc.stdout.splitlines()
    assert lines and lines[-1] == "DONE", proc.stdout[-3000:] + proc.stderr
    got = [ln[2:] for ln in lines if ln.startswith("L ")]
    want = _oracle()
    assert got == want, _diff(got, want)


def _diff(got, want):
    for i, (a, b) in enumerate(zip(got, want)):
        if a != b:
            return "line %d:\n  got  %s\n  want %s" % (i, a, b)
    return "lengths %d vs %d" % (len(got), len(want))
