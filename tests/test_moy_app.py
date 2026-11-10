"""native/moy_app, the app ABI's kernel half, against the Python it replaced.

One script drives the grants, the policy and the C rows -- damage, the surface
rows over a pointer, the token table, prefs and the clipboard -- and logs every
answer; it runs over three implementations and
the logs must be equal line for line (tests/test_gfx_binding.py's pattern):

  * the oracle, tests/app_twin.py over tests/spine_twin.py's Settings: what
    `Damage`, `Prefs`, `Clipboard` and system_api's policy did before step 4;
  * the C over the host's ctypes binding (runtime/host/moy_app_binding.py), the one
    the CPython console runs;
  * the C as the boards' native module, on the desktop MicroPython in the
    boards' object model (32-bit, REPR_C), over its native moy_spine.

The handle VALUES are in the log too: a grant is a kind-12 handle
(moy_htab.h's MOY_KIND_GRANT), slot and generation, as the oracle mints them.
"""

import os
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

    def set(self, g, name, variant=None):
        self.say("set", name, variant)

    def set_variant(self, g, v):
        self.say("set_variant", v)

    # every shell row of the other roles: the grant's id, the arguments back
    def __getattr__(self, verb):
        if verb.startswith("_"):
            raise AttributeError(verb)

        def row(g, *a, **kw):
            return (verb, self.app.grant_id(g), a, sorted(kw.items()))
        return row


def run(sp, ap, say, mi, root):
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
        app.grant("x", ("prefs",), kind="stories")
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
        s.windowed(), s.bar_h(), s.content_rect(), s.pointer())
    cv = _Canvas()
    say("write", app.surface_write(cv, 320, 240, 2, 3, True, (0, 18, 320, 222),
                                   10, 20))
    say("surface", s.canvas() is cv, s.size(), s.font_scale(), s.chrome_scale(),
        s.windowed(), s.bar_h(), s.content_rect())
    app.surface_write(cv, 320, 240, 2, 3, False, (0, 0, 320, 222), 10, 20)
    say("bottom", s.bar_h(), s.content_rect())
    late = app.grant("late", ("surface",))
    say("late", ap.Surface(app, late).canvas(), ap.Surface(app, late).size())
    plain = _Plain()
    app.surface_write(plain, 800, 480, 1, 1, False, (0, 0, 800, 480))
    say("plain", ap.Surface(app, late).canvas() is plain, s.canvas() is plain,
        s.size(), s.windowed(), ap.Surface(app, g).__class__ is ap.Surface)
    app.surface_write(cv, 320, 240, 2, 3, True, (0, 18, 320, 222), 10, 20)
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
    app.serve("theme", srv)

    # -- the theme rows
    t = ap.Theme(app, sg)
    say("blank", t.colors(), t.gen(), t.light(), t.name(), t.variant(), t.token(0))
    app.theme_write("night", "dark", {"panel": 60, "edge": 13, "surface_light": False})
    d = t.colors()
    say("theme", sorted(d.items()), t.colors() is d, t.gen(), t.light(), t.name(),
        t.variant(), t.token(0), t.token(1), t.token(9), t.token(20))
    wrote = app.theme_write("machine", "light", {"surface_light": True, "panel": 1,
                                                 "bar_light": True})
    say("wrote gen", wrote == t.gen())
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
    say("served", sorted(app.served().items()))

    # -- the roles served in Python: the grant checked, the row counted, the
    # server called with the grant first
    every = app.grant("tracer", ("files", "carts", "nav", "notify", "wallpaper",
                                 "install", "artwork"))
    srv2 = _Servers(say, ap, app)
    for role in ("carts", "nav", "notify", "wallpaper", "install"):
        app.serve(role, srv2)
    say("carts", ap.Carts(app, every).journal("c", "m", "s", grad=1))
    say("nav", ap.Nav(app, every).play("cart"), ap.Notify(app, every).award("first_open"),
        ap.Wallpaper(app, every).thumbnail(4, 3), ap.Install(app, every).op(None))
    try:
        ap.Files(app, sg).list("docs")
        say("files allowed")
    except ValueError as e:
        say("files denied", str(e))
    say("served", sorted(app.served().items()))

    # -- files: C rows over the user-files store the console binds
    f = ap.Files(app, every)
    say("unbound", f.readable(), f.ready(), f.begin(), f.load("docs", "a"),
        f.sig("abc"), f.encode_text("t"), f.decode_text("a"))
    app.store_bind(root, True, False, "NOSTORE")
    say("read only", f.readable(), f.ready(), f.begin(), f.save("docs", "a", "x"),
        f.list("docs"), f.count("docs"))
    app.store_bind(root, True, True, "NOSTORE")
    say("files", f.begin(), f.begin(), f.save("docs", "My Note", "hello"),
        f.list("docs"), f.load("docs", "my_note"), f.load("docs", "nope"),
        f.count("docs"), app.files_end_all(), app.files_end_all())
    f.begin()
    f.end()
    say("names", f.new_name("docs"), f.new_name("docs", "todo.txt"),
        f.rename("docs", "my_note", "Renamed"), f.duplicate("docs", "renamed"))
    h = f.history("docs", "renamed")
    say("history", f.history_commit("docs", "renamed", [{"i": 1}], keyframe={"d": 2}),
        [sorted(r.items()) for r in f.history("docs", "renamed")[0]], h,
        f.history_ops("docs", "renamed"), f.history_commit("docs", "renamed", []))
    say("trash", f.delete("docs", "renamed"), f.trash_list(), f.restore("docs", "renamed"),
        f.empty_trash(), f.trash_list(), f.delete("docs", "nope")[1] is not None)
    say("codecs", f.decode_image(f.encode_image(2, 1, b"\x01\x02")), f.sig("abc"),
        f.stamp('{"a": 1}', "docs", "n", 7), f.provenance(f.stamp("{}", "docs", "n", 9)),
        f.stamp("[1]", "docs", "n", 9), f.encode_text("t"), f.decode_text("a\nb"),
        f.decode_text(""), f.decode_image("no"), f.decode_image(""), f.provenance("x"))
    cov = f.decode_cover(f.encode_cover(bytes(range(64)) * 256))
    say("cover", cov[0], cov[1], cov[2] == bytes(range(64)) * 256, f.decode_cover(b"no"))
    try:
        f.encode_image(2, 2, b"abc")
        say("encoded")
    except ValueError as e:
        say("bad size", str(e))
    say("refused", f.list("selfies"), f.load("project:", "x"), f.save("recordings", "a", "b"))
    notes = app.grant("local.notes2", ("files",), kind="docs", run=True)
    scoped = ap.Files(app, notes)
    # the two docs may differ in mtime by a second: the order is the store's, not this test's
    names, err = scoped.list("docs")
    say("scoped", (sorted(names), err), scoped.save("docs", "note", "n"))
    try:
        scoped.list("drawings")
        say("scoped allowed")
    except ValueError as e:
        say("scoped denied", str(e))
    app.end(notes)
    wp = ap.Wallpaper(app, every)
    say("copy", app.copy_gen(), wp.load_copy(), wp.save_copy("pic"), wp.load_copy(),
        app.copy_gen())
    app.store_bind(root, True, False, "NOSTORE")
    say("copy ro", wp.save_copy("x"), app.copy_gen(), wp.load_copy())
    app.store_bind(None, False, False, None)
    say("unbound again", f.list("docs"), wp.load_copy(), f.sig("x"))

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

    # -- a permission changed: every run row of the app recomputed, a
    # foreground run's keeping its surface and theme, a service's neither
    fg = app.grant("local.re", ("prefs", "clipboard", "surface", "theme"), run=True, owner=3)
    svc = app.grant("local.re", ("prefs", "clipboard"), run=True, owner=4)
    other = app.grant("local.re2", ("prefs",), run=True, owner=5)
    say("regrant", app.regrant("local.re", ("clipboard",), ("clipboard", "nav")),
        app.regrant("local.none", (), ()))
    for h in (fg, svc, other):
        for mk, call in ((ap.Prefs, lambda o: o.get("k")),
                         (ap.Clipboard, lambda o: o.text()),
                         (ap.Theme, lambda o: o.token(0))):
            try:
                call(mk(app, h))
                say("held")
            except ValueError as e:
                say("not held", str(e))
    say("regrant on", app.regrant("local.re", ("prefs", "clipboard"), ("prefs",)))
    try:
        ap.Prefs(app, svc).get("k")
        say("held")
    except ValueError as e:
        say("not held", str(e))
    for bad in (("", ()), ("x" * 64, ()), ("local.re", ("nope",))):
        try:
            app.regrant(bad[0], bad[1], ())
            say("regranted", bad[0][:4])
        except ValueError:
            say("refused", bad[0][:4])
    for h in (fg, svc, other):
        app.end(h)

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
                  ["prefs", "files:documents", "launch", "appearance", "clipboard"],
                  ["files:nonsense"], ["files", "files:music"], None, [3]):
        say("policy", perms, ap.policy(perms), ap.manifest_error(perms))
    for cid, title in (("moybyte.notes", "Notes"), (None, "My Notes!"),
                       (None, None), ("", ""), (None, "!!!"),
                       (None, "Caf\u00e9 9-b_c")):
        say("id", cid, title, ap.id_for(cid, title))
    say("perms", list(ap.perms()), list(ap.kinds()), list(ap.roles()))

    # -- the crypto rows (native/moy_net/moy_crypto.h): FIPS 180-2's "abc",
    # RFC 4231's cases 1, 2 and 6, the GCM spec's test cases 3 and 15 (no
    # AAD, the nonce first), RFC 7748 section 6.1's two keys; and the lengths
    # each refuses
    import binascii
    h = binascii.unhexlify

    def x(b):
        return b if b is None else binascii.hexlify(b).decode()

    say("sha256", x(ap.sha256(b"abc")), x(ap.sha256("")), x(ap.sha256(bytearray(b"abc"))))
    say("hmac", x(ap.hmac_sha256(b"\x0b" * 20, b"Hi There")),
        x(ap.hmac_sha256("Jefe", "what do ya want for nothing?")),
        x(ap.hmac_sha256(b"\xaa" * 131, b"Test Using Larger Than Block-Size Key - Hash Key First")))
    iv = h("cafebabefacedbaddecaf888")
    plain = h("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
              "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255")
    k128, k256 = h("feffe9928665731c6d6a8f9467308308"), h("feffe9928665731c6d6a8f9467308308" * 2)
    c3 = iv + h("42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
                "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091473f5985"
                "4d5c2af327cd64a62cf35abd2ba6fab4")
    c15 = iv + h("522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
                 "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662898015ad"
                 "b094dac5d93471bdec1a502270e3cc6c")
    say("gcm open", ap.aes_gcm_open(k128, c3) == plain, ap.aes_gcm_open(k256, c15) == plain,
        ap.aes_gcm_open(k256, c3), ap.aes_gcm_open(k128, c3[:-1] + b"\x00"),
        ap.aes_gcm_open(k128, b"short"))
    sealed = ap.aes_gcm_seal(k128, b"moybyte")
    say("gcm seal", len(sealed), ap.aes_gcm_open(k128, sealed), ap.aes_gcm_open(k128, ap.aes_gcm_seal(k128, b"")),
        sealed[:12] != ap.aes_gcm_seal(k128, b"moybyte")[:12])
    alice = h("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    bob = h("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
    say("x25519", x(ap.x25519(alice)), x(ap.x25519(bob, None)),
        x(ap.x25519(alice, ap.x25519(bob))), ap.x25519(bob, ap.x25519(alice)) == ap.x25519(alice, ap.x25519(bob)))
    # RFC 8032 section 7.1's TEST 2 and TEST SHA(abc): the public key, the
    # signature, the check; a changed byte, another key; the lengths refused
    for sk, m in (("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "72"),
                  ("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
                   "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
                   "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f")):
        sk, m = h(sk), h(m)
        pk, sig = ap.ed25519_public(sk), ap.ed25519_sign(sk, m)
        say("ed25519", x(pk), x(sig), ap.ed25519_verify(pk, m, sig),
            ap.ed25519_verify(pk, m + b"!", sig), ap.ed25519_verify(ap.ed25519_public(alice), m, sig),
            ap.ed25519_sign(bytearray(sk), m) == sig)
    say("ed25519 empty", x(ap.ed25519_sign(alice, b"")), x(ap.ed25519_sign(alice, "moybyte")))
    # AES-CTR: NIST SP 800-38A F.5.1 and RFC 3686's vector 2; AES-CCM: NIST
    # CAVP's DVPT128 (13-byte nonce, no associated data, a 4-byte tag) and
    # Meshtastic's PKI packet (an 8-byte tag), opened and changed
    k38a = h("2b7e151628aed2a6abf7158809cf4f3c")
    blk = h("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
    p38a = h("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51")
    say("ctr", x(ap.aes_ctr(k38a, blk + p38a)), x(ap.aes_ctr(k38a, blk)),
        x(ap.aes_ctr(h("ae6852f8121067cc4bf7a5765577f39e"),
                     h("00000030000000000000000000000001") + b"Single block msg")),
        ap.aes_ctr(k38a, blk + ap.aes_ctr(k38a, blk + p38a)) == p38a)
    kd, nd = h("197afb02ffbd8f699dacae87094d5243"), h("5a8aa485c316e9403aff859fbb")
    pd = h("a16a2e741f1cd9717285b6d882c1fc53655e9773761ad697")
    sd = ap.aes_ccm_seal(kd, nd + pd, 4)
    say("ccm", x(sd), ap.aes_ccm_open(kd, nd + sd, 4) == pd, x(ap.aes_ccm_seal(kd, nd, 16)),
        ap.aes_ccm_open(kd, nd + sd[:-1] + b"\x00", 4), ap.aes_ccm_open(kd, nd + sd, 6),
        ap.aes_ccm_open(kd, nd, 4))
    km = h("777b1545c9d6f9a258ee0fd71153c78a50c6bb56c1f4fff2ea0ef28094de0186")
    nm = h("62d6b213036a792b2909000000")
    say("ccm mesh", x(ap.aes_ccm_open(km, nm + h("40df24abfcc30a17a3d9046726099e796a1c"), 8)),
        x(ap.aes_ccm_seal(km, nm + h("08011204746573744800"), 8)))
    r = ap.random(16)
    say("random", len(r), len(ap.random(0)), len(ap.random(4096)), r != ap.random(16))
    for what, fn, args in (("random 4097", ap.random, (4097,)), ("random -1", ap.random, (-1,)),
                           ("seal key", ap.aes_gcm_seal, (b"short", b"")),
                           ("open key", ap.aes_gcm_open, (b"", c3)),
                           ("x25519 secret", ap.x25519, (b"x",)),
                           ("x25519 peer", ap.x25519, (alice, b"y")),
                           ("x25519 small", ap.x25519, (alice, bytes(32))),
                           ("ed25519 secret", ap.ed25519_public, (b"x",)),
                           ("ed25519 sign secret", ap.ed25519_sign, (bytes(33), b"")),
                           ("ed25519 pub", ap.ed25519_verify, (bytes(31), b"", bytes(64))),
                           ("ed25519 sig", ap.ed25519_verify, (alice, b"", bytes(63))),
                           ("ctr key", ap.aes_ctr, (b"short", blk)),
                           ("ctr block", ap.aes_ctr, (k38a, blk[:15])),
                           ("ccm nonce", ap.aes_ccm_seal, (kd, nd[:12], 4)),
                           ("ccm tag", ap.aes_ccm_seal, (kd, nd, 5)),
                           ("ccm tag big", ap.aes_ccm_open, (kd, nd + sd, 18)),
                           ("ccm key", ap.aes_ccm_open, (b"short", nd + sd, 4))):
        try:
            fn(*args)
            say(what, "answered")
        except ValueError as e:
            say(what, str(e))
'''

VM_MAIN = r'''
import moy_input
import moy_spine
import moy_app
LOG = []


def say(*a):
    print("L", " ".join(repr(x) if not isinstance(x, str) else x for x in a))


run(moy_spine, moy_app, say, moy_input, ROOT)
print("DONE")
'''


def _log(sp, ap):
    import tempfile
    out = []

    def say(*a):
        out.append(" ".join(repr(x) if not isinstance(x, str) else x for x in a))

    from runtime import moy_input
    ns = {}
    exec(SCRIPT, ns)
    with tempfile.TemporaryDirectory() as t:
        ns["run"](sp, ap, say, moy_input, os.path.join(t, "sd", "carts"))
    return out


def _oracle():
    return _log(spine_twin, app_twin)


def test_the_host_binding_is_the_oracle():
    from runtime import moy_app_binding, moy_spine_binding
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
    script.write_text(SCRIPT + "ROOT = %r\n" % str(tmp_path / "sd" / "carts") + VM_MAIN)
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
