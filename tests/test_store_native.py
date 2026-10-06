"""The native store's catalogue and JSON scanner (native/moy_store/moy_cat.c,
native/moy_spine/moy_json.c; docs/kernel_store_2026-10.md sections 6 and 10,
slice 3) held to their twins: the desktop MicroPython, whose moy_carts reads
through the C, against CPython over runtime/moy_carts.py.

  * the shelf, key for key and icon for icon, over every seed, fixture and
    port cart and the sheet and folder shapes test_catalogue.py builds;
  * the same over generated folders: manifests valid, broken and odd, sheets,
    scenes, listings that prove a file absent and ones that cannot;
  * moy_json against CPython's json: json.loads and json.dumps(json.loads())
    over generated texts, refused where json refuses.

What the C leaves out, by decision: a manifest's lone surrogate escape reads
as U+FFFD, a sheet is ASCII, a "broken" reason is the C's own words (the
twin's is the exception's), so the comparison reads that field as present,
and a text reads as its bytes, as MicroPython's open() reads it, where
CPython's text mode turns CRLF into LF.
"""

import json
import os
import random
import subprocess
import sys

import pytest

from test_catalogue import _store
from unix_mp import require_unix_mp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DRIVER = r'''
import sys, json
sys.path.insert(0, @RUNTIME@)
import moy_carts
try:
    import moy_store
except ImportError:
    moy_store = None

def ser(o):
    if isinstance(o, dict):
        return "{" + ", ".join(ser(k) + ": " + ser(o[k])
                               for k in sorted(o)) + "}"
    if isinstance(o, tuple):
        return "(" + ", ".join(ser(x) for x in o) + ")"
    if isinstance(o, list):
        return "[" + ", ".join(ser(x) for x in o) + "]"
    if isinstance(o, float):
        return "f%.12g" % o
    if isinstance(o, str):
        o = o.replace("\r\n", "\n")     # CPython's text mode reads universal newlines
        if len(o) > 48:
            import binascii
            return "S%d:%d" % (len(o), binascii.crc32(o.encode()))
        return "s" + ",".join(str(ord(c)) for c in o)
    return json.dumps(o)

def scrub(e):
    if isinstance(e, dict) and "broken" in e:
        e["broken"] = True
    return e

print("native", getattr(moy_carts, "_store", None) is not None)
with open(@OPS@) as f:
    ops = json.loads(f.read())
for op in ops:
    kind, arg = op[0], op[1]
    if kind == "cat":
        items = moy_carts.entries(arg)
        print("cat", None if items is None else len(items))
        for e in items or ():
            print(ser(scrub(e)))
    elif kind == "entry":
        print("entry", ser(scrub(moy_carts.entry(arg))))
    elif kind == "load":
        print("load", ser(scrub(moy_carts.load(arg))))
    elif kind == "loads":
        try:
            v = moy_store.loads(arg) if moy_store else json.loads(arg)
            print("loads", ser(v))
        except ValueError:
            print("loads refused")
    elif kind == "canon":
        try:
            v = (moy_store.canon(arg) if moy_store
                 else json.dumps(json.loads(arg)))
            print("canon", v)
        except ValueError:
            print("canon refused")
'''


def _run(cmd, ops, tmp):
    opsf = os.path.join(str(tmp), "ops-%d.json" % len(os.listdir(str(tmp))))
    with open(opsf, "w", encoding="utf-8") as f:
        json.dump(ops, f, ensure_ascii=False)     # MicroPython's json pairs no surrogates
    script = (DRIVER.replace("@RUNTIME@", repr(os.path.join(ROOT, "runtime")))
              .replace("@OPS@", repr(opsf)))
    out = subprocess.run(cmd + ["-c", script], capture_output=True, text=True,
                         timeout=600)
    assert out.returncode == 0, (out.stdout + out.stderr)[-3000:]
    return [ln for ln in out.stdout.splitlines()
            if not ln.startswith("Moybyte cart ")]


def _vm():
    return require_unix_mp(
        "moy_store",
        why="the catalogue on the VM a board runs, through the native store "
            "(docs/kernel_store_2026-10.md section 10, slice 3).")


def _both(ops, tmp):
    vm = _run([_vm(), "-X", "heapsize=64M"], ops, tmp)
    cp = _run([sys.executable], ops, tmp)
    assert vm[0] == "native True" and cp[0] == "native False"
    return vm[1:], cp[1:]


def test_the_native_shelf_is_the_twins_over_every_cart(tmp_path):
    root = _store(tmp_path)
    ops = [["cat", root], ["cat", root + "/nowhere"]]
    for name in sorted(os.listdir(root)):
        ops.append(["entry", root + "/" + name])
        ops.append(["load", root + "/" + name])
    vm, cp = _both(ops, tmp_path)
    assert int(cp[0].split()[1]) > 40
    for a, b in zip(vm, cp):
        assert a == b
    assert len(vm) == len(cp)


# -- generated folders ----------------------------------------------------------------

_KEYS = ("title", "author", "type", "runtime", "main", "format", "version",
         "memory", "writable", "graduated", "fps", "palette", "extensions",
         "icon", "edit", "permissions", "input", "canvas", "assets", "id",
         "config", "sources")


def _value(rng, depth=0):
    k = rng.randrange(12 if depth < 3 else 7)
    if k == 0:
        return None
    if k == 1:
        return rng.choice([True, False])
    if k == 2:
        return rng.choice([0, 1, -1, 3, 7, 16, 200, 511, 600, 2 ** 40, -2 ** 33])
    if k == 3:
        return rng.choice([0.0, -0.0, 1.5, 3.0, 1e-7, 2.5e17, 1e300, -4.25])
    if k == 4:
        return rng.choice(["", "x", "moy-1", "wasm", "lua", "main.py", "main.lua",
                           "320x240", "128x128", "64x64", " 12 ", "+7", "1_000",
                           "1__0", "0x10", "3.0", "buttons", "touch", "keyboard",
                           "café", "中文", "tab\there", "q\"uote",
                           "\U0001f600", "a/b", "MAIN.PY"])
    if k == 5:
        return rng.choice([[1, 2, 2], [5], [0, 4, 4], [3, 5, 1], ["7", True, 2.9],
                           [1, 2, 3, 4], [], [-1, 1, 1], [None]])
    if k == 6:
        return rng.choice([{"width": 160, "height": 120}, {"width": "128",
                           "height": 128.0}, {"width": 1}, {}])
    if k in (7, 8):
        return [_value(rng, depth + 1) for _ in range(rng.randrange(4))]
    return {rng.choice(_KEYS + ("scenes", "x", "")): _value(rng, depth + 1)
            for _ in range(rng.randrange(4))}


def _manifest(rng):
    roll = rng.random()
    if roll < 0.06:
        return rng.choice(["{ not json", "", "[1, 2]", "\"text\"", "{\"a\": 1,}",
                           "{\"title\": \"x\"} trailing", "nul\x00l", "{\"a\": NaN}"])
    man = {}
    for key in rng.sample(_KEYS, rng.randrange(len(_KEYS))):
        man[key] = _value(rng)
        if key == "main" and rng.random() < 0.7:
            man[key] = rng.choice(["main.py", "main.lua", "game.py", "main.wasm"])
        if key == "assets" and rng.random() < 0.6:
            man[key] = {"scenes": rng.choice([["b", "a"], ["a", "zz", 3], ["q"],
                                              [["x"]], "a"])}
        if key == "sources" and rng.random() < 0.7:
            man[key] = rng.choice([["p8.lua", "main.py"], ["main.py", "perf.lua"],
                                   ["p8.lua", "main.lua", "perf.lua"], ["gone.lua", "main.py"],
                                   ["p8.lua"], "main.py", [3, "main.py"]])
        if key == "config" and rng.random() < 0.7:
            man[key] = rng.choice([{"speed": 1, "bg": "x"}, [], [["k", 2]], None, "", 5])
        if key == "icon" and rng.random() < 0.5:
            man[key] = rng.choice([[rng.randrange(-2, 520), rng.randrange(0, 6),
                                    rng.randrange(0, 6)], rng.randrange(600)])
    text = json.dumps(man, ensure_ascii=rng.random() < 0.5,
                      indent=rng.choice([None, 1]))
    if rng.random() < 0.2 and text.startswith("{\"") and len(man) > 1:
        k = next(iter(man))
        text = "{%s: \"dup\", %s" % (json.dumps(k), text[1:])   # a repeated key
    return text


def _sheet(rng):
    lines = []
    for y in range(rng.randrange(0, 260)):
        if rng.random() < 0.05:
            lines.append(rng.choice(["", "   ", "\t"]))
            continue
        ink = rng.random() < 0.3
        w = rng.choice([128, 128, 128, 40, 200, 3])
        lines.append("".join(rng.choice("0123456789abcdef" if ink else "0")
                             for _ in range(w)))
    return rng.choice(["\n", "\r\n"]).join(lines) + rng.choice(["", "\n"])


def _folders(root, rng, n):
    os.makedirs(root)
    for i in range(n):
        d = os.path.join(root, "c%03d%s.moy" % (i, rng.choice(["", "_x", ".v2"])))
        os.makedirs(d)
        text = _manifest(rng)
        if rng.random() < 0.95:
            with open(os.path.join(d, "manifest.json"), "w") as f:
                f.write(text)
        for main in ("main.py", "main.lua", "game.py", "main.wasm"):
            if rng.random() < 0.45:
                with open(os.path.join(d, main), "w") as f:
                    f.write("x = 1\n")
        if rng.random() < 0.6:
            with open(os.path.join(d, "sprites.moygfx"), "w") as f:
                f.write(_sheet(rng))
        for name, body in (("config.json", rng.choice(['{"speed": 4}', "[[\"a\", 1]]",
                                                       "7", "{bad", "[]", "\"\""])),
                           ("flags.moyflags", "01" * 512),
                           ("map.moymap", "2 1\n0102\n"),
                           ("sounds.json", rng.choice(['{"sfx": []}', "nope"])),
                           ("blocks.json", rng.choice(['{"blocks": [1.5]}', "["])),
                           ("p8.lua", "-- shim\n"), ("perf.lua", "-- after\n")):
            if rng.random() < 0.35:
                with open(os.path.join(d, name), "w") as f:
                    f.write(body)
        if rng.random() < 0.05:
            with open(os.path.join(d, "map.moymap"), "wb") as f:
                f.write(b"\xff\xfe not utf-8")
        if rng.random() < 0.25:
            os.makedirs(os.path.join(d, "images"))
            for im in rng.sample(["wall", "sky", "x y"], rng.randrange(3)):
                with open(os.path.join(d, "images", im + ".moyimg"), "w") as f:
                    f.write("moyimg " + im)
        if rng.random() < 0.15:
            os.makedirs(os.path.join(d, "src"))
            for sname in rng.sample(["b.c", "a.rs", "z.txt"], rng.randrange(3)):
                with open(os.path.join(d, "src", sname), "w") as f:
                    f.write("// " + sname)
        if rng.random() < 0.4:
            os.makedirs(os.path.join(d, "scenes"))
            for s in rng.sample(["a", "b", "zz", "q", "c d"], rng.randrange(4)):
                with open(os.path.join(d, "scenes", s + ".moyscene"), "w") as f:
                    f.write("[]")
        elif rng.random() < 0.1:
            with open(os.path.join(d, "scenes"), "w") as f:
                f.write("a file, not a folder")
    with open(os.path.join(root, "loose.moy"), "w") as f:
        f.write("an archive")


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_native_shelf_is_the_twins_over_generated_folders(tmp_path, seed):
    rng = random.Random(seed)
    root = str(tmp_path / "carts")
    _folders(root, rng, 120)
    ops = [["cat", root]]
    for n in sorted(os.listdir(root)):
        ops += [["entry", os.path.join(root, n)], ["load", os.path.join(root, n)]]
    vm, cp = _both(ops, tmp_path)
    for a, b in zip(vm, cp):
        assert a == b
    assert len(vm) == len(cp)


# -- moy_json against CPython's json ----------------------------------------------------


def _text(rng):
    v = _value(rng)
    t = json.dumps(v, ensure_ascii=rng.random() < 0.5,
                   separators=rng.choice([(",", ":"), (", ", ": "), (" ,", " : ")]))
    roll = rng.random()
    if roll < 0.15:                         # damaged
        i = rng.randrange(len(t) + 1)
        t = t[:i] + rng.choice(["", "}", "]", ",", "\"", "\\", "x", "\x01"]) + t[i + 1:]
    elif roll < 0.25:
        t = rng.choice(["NaN", "-Infinity", "[Infinity]", "1e400", "-0", "0.0",
                        "1E5", "123456789012345678901234567890", "1.0e-10",
                        "{\"a\": 1, \"a\": 2, \"b\": 3}", "\"\\u00e9\\ud83d\\ude00\"",
                        "\"\\/\\b\\f\"", "[" * 33 + "]" * 33, "[" * 31 + "]" * 31,
                        " \t\n 7 \r", "01", "-", "\"\x7f\"", "tru", "[1,]"])
    return t


def test_moy_json_reads_and_writes_as_cpythons_json(tmp_path):
    rng = random.Random(7)
    texts = [_text(rng) for _ in range(600)]
    ops = [["loads", t] for t in texts] + [["canon", t] for t in texts]
    vm, cp = _both(ops, tmp_path)
    assert len(vm) == len(cp)
    for t, a, b in zip(texts + texts, vm, cp):
        if _depth(t) > 32:              # MOY_JSON_DEPTH: refused, by decision
            assert a.endswith(" refused"), t
            continue
        assert a == b, t


def _depth(t):
    d = top = 0
    quoted = esc = False
    for c in t:
        if quoted:
            esc, quoted = (False, True) if esc else (c == "\\", c != '"')
        elif c == '"':
            quoted = True
        elif c in "[{":
            d += 1
            top = max(top, d)
        elif c in "]}":
            d -= 1
    return top


# -- the seed (slice 4) --------------------------------------------------------------------

SEED_DRIVER = r'''
import sys, json, os
sys.path.insert(0, @RUNTIME@)
import moy_carts, moy_catalogue
D = @DIR@
with open(D + "/roster.json") as f:
    meta = json.loads(f.read())
roster = []
for i, (title, version) in enumerate(meta):
    with open(D + "/blob%d" % i, "rb") as f:
        roster.append((title, version, f.read()))
root = @ROOT@
moy_carts.ensure_dirs(root)
seen = []
def progress(i, n, title):
    seen.append(i)
    if i == 3:
        raise ValueError("a broken hook is dropped")
step = @STEP@
if step == "cold":
    wrote = moy_carts.seed_any(roster, root, {}, progress)
elif step == "warm":
    shelf = moy_catalogue.catalogue(root)
    wrote = moy_carts.seed_any(roster, root,
                               dict((e["path"][len(root) + 1:], e["version"]) for e in shelf),
                               progress)
else:
    wrote = moy_carts.seed_any(roster, root, {}, progress)
print("native", getattr(moy_carts, "_store", None) is not None)
print("wrote", sorted(wrote))
print("progress", seen)
'''


def _tree(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


def _seed_step(cmd, tmp, root, step):
    script = (SEED_DRIVER.replace("@RUNTIME@", repr(os.path.join(ROOT, "runtime")))
              .replace("@DIR@", repr(str(tmp))).replace("@ROOT@", repr(root))
              .replace("@STEP@", repr(step)))
    out = subprocess.run(cmd + ["-c", script], capture_output=True, text=True,
                         timeout=600)
    assert out.returncode == 0, (out.stdout + out.stderr)[-3000:]
    return out.stdout.splitlines()


def test_the_native_seed_writes_the_twins_tree(tmp_path):
    """The packed roster seeded by the C (moy_seed.c, under the desktop
    MicroPython) and by seed_builtins (CPython): cold, warm, and over a store
    whose built-ins are older and hold a kid's saves and config -- the same
    files, byte for byte, every time."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import gen_device_carts
    packed = gen_device_carts.build_packed(os.path.join(ROOT, "system_carts"))
    with open(str(tmp_path / "roster.json"), "w") as f:
        json.dump([[t, v] for t, v, _ in packed], f)
    for i, (_t, _v, blob) in enumerate(packed):
        with open(str(tmp_path / ("blob%d" % i)), "wb") as f:
            f.write(blob)
    vm_root, cp_root = str(tmp_path / "vm" / "carts"), str(tmp_path / "cp" / "carts")
    exe = _vm()
    trees = []
    for step in ("cold", "warm", "bump"):
        if step == "bump":
            for root in (vm_root, cp_root):
                for name in sorted(os.listdir(root))[:4]:
                    man = os.path.join(root, name, "manifest.json")
                    with open(man) as f:
                        m = json.load(f)
                    m["version"] = 0
                    with open(man, "w") as f:
                        json.dump(m, f)
                    with open(os.path.join(root, name, "pmem.json"), "w") as f:
                        f.write("[1, 2, 3]")
                    with open(os.path.join(root, name, "config.json"), "w") as f:
                        f.write('{"kid": "tuned"}')
        vm = _seed_step([exe], tmp_path, vm_root, step)
        cp = _seed_step([sys.executable], tmp_path, cp_root, step)
        assert vm[0] == "native True" and cp[0] == "native False"
        assert vm[1:] == cp[1:], step
        a, b = _tree(vm_root), _tree(cp_root)
        assert sorted(a) == sorted(b), step
        for k in a:
            assert a[k] == b[k], (step, k)
        trees.append(a)
    assert len(trees[0]) > 100
    assert all(n.split("/")[0].startswith("moybyte.") for n in trees[0]
               if "/" in n)
    assert "wrote []" in _seed_step([exe], tmp_path, vm_root, "warm")


# -- the journal (slice 6) -------------------------------------------------------------------

JOURNAL_DRIVER = r'''
import sys, json, os
sys.path.insert(0, @RUNTIME@)
import moy_carts, moy_journal
moy_journal._journal_ts = lambda: 1700000000
D = @DIR@
root = D + "/carts"
moy_carts.ensure_dirs(root)
cart = moy_carts.create("Jot", root, src="print(0)\n")["path"]
seed = @SEED@
state = seed
def rnd(n):
    global state
    state = (state * 1103515245 + 12345) & 0x7fffffff
    return state % n
files = ["main.py", "sprites.moygfx", "scenes/a.moyscene"]
os.mkdir(cart + "/scenes")
texts = ["x = %d\n" % i for i in range(9)] + ["café \U0001f600\n", ""]
for step in range(@STEPS@):
    op = rnd(12)
    f = files[rnd(3)]
    scope = [None, [f], ["main.py", "sprites.moygfx"]][rnd(3)]
    if op < 5:
        t = texts[rnd(len(texts))] * (1 + rnd(400 if rnd(8) == 0 else 3))
        moy_carts._write_atomic(cart + "/" + f, t)
        grad = [None, 0, 1][rnd(3)] if f == "main.py" else None
        ops = [None, [], [["ins", rnd(9), "qé"], {"op": [1, 2.5]}]][rnd(3)]
        print("append", f, moy_journal.journal_append(cart, f, t, grad=grad, ops=ops))
    elif op < 7:
        print("undo", moy_journal.journal_undo(cart, scope))
    elif op < 9:
        print("redo", moy_journal.journal_redo(cart, scope))
    elif op == 9:
        print("can", moy_journal.journal_can_undo(cart, scope),
              moy_journal.journal_can_redo(cart, scope))
    elif op == 10:
        es = moy_journal.journal_list(cart, f if rnd(2) else None)
        print("list", len(es), [e["seq"] for e in es])
        if es:
            s = es[rnd(len(es))]["seq"]
            snap = moy_journal.journal_snap(cart, s)
            print("snap", s, None if snap is None else len(snap))
            if rnd(3) == 0:
                print("restore", moy_journal.journal_restore(cart, s))
    else:
        print("compact", moy_journal.journal_compact(cart))
    if rnd(9) == 0 and moy_carts._exists(cart + "/journal/journal.jsonl"):
        with open(cart + "/journal/journal.jsonl", "a") as fh:
            fh.write('{"seq": "torn')         # a torn last line
man = json.loads(moy_carts._read(cart + "/manifest.json"))
print("manifest", sorted(man.items()))
'''


def test_the_native_journal_writes_the_twins_files(tmp_path):
    """A seeded session of commits, undos, redos, compactions and #136's
    reads and restores, played by the C journal (desktop MicroPython) and the
    twin (CPython): the same answers and the same files, byte for byte."""
    exe = _vm()
    for seed in (1, 2, 3, 4):
        outs, trees = [], []
        for name, cmd in (("vm", [exe, "-X", "heapsize=64M"]), ("cp", [sys.executable])):
            d = tmp_path / ("%s%d" % (name, seed))
            d.mkdir()
            script = (JOURNAL_DRIVER.replace("@RUNTIME@", repr(os.path.join(ROOT, "runtime")))
                      .replace("@DIR@", repr(str(d))).replace("@SEED@", str(seed))
                      .replace("@STEPS@", "160"))
            out = subprocess.run(cmd + ["-c", script], capture_output=True, text=True,
                                 timeout=600)
            assert out.returncode == 0, (out.stdout + out.stderr)[-3000:]
            outs.append(out.stdout.splitlines())
            trees.append(_tree(str(d / "carts")))
        assert outs[0] == outs[1], seed
        a, b = trees
        assert sorted(a) == sorted(b), seed
        for k in a:
            if "manifest.json" in k:    # created by each tier's own json.dumps
                continue
            assert a[k] == b[k], (seed, k)
        assert any(k.endswith("journal.jsonl") for k in a)


# -- the archive and adopt (slice 7) ----------------------------------------------------------

PACK_DRIVER = r'''
import sys
sys.path.insert(0, @RUNTIME@)
import moy_carts
D = @DIR@
print("packed", moy_carts.pack(D + "/src/hop.moy", D + "/@TAG@.moy", history=@HIST@))
for name in ("browser.zip", "deflated.zip", "flat.zip"):
    n, top = moy_carts.unpack(D + "/" + name, D + "/@TAG@-" + name[:-4])
    print("unpacked", name, n, repr(top))
moy_carts.ensure_dirs(D + "/shelf/carts")
print("import", moy_carts.import_archive(D + "/browser.zip", D + "/shelf/carts")[len(D):])
print("import", moy_carts.import_archive(D + "/browser.zip", D + "/shelf/carts")[len(D):])
'''

NODE_DRIVER = r'''
import * as store from "@MJS@";
import fs from "fs";
const mode = process.argv[2];
if (mode === "zip") {
    const files = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).map(
        (f) => ({ name: f[0], data: Buffer.from(f[1], "base64") }));
    fs.writeFileSync(process.argv[4], store.zipStore(files));
} else {
    const entries = await store.unzip(new Uint8Array(fs.readFileSync(process.argv[3])));
    console.log(JSON.stringify(entries.map((e) => [e.name, Buffer.from(e.data).toString("base64")])));
}
'''


def _cart_tree(d):
    files = {
        "manifest.json": b'{"title": "Hop", "main": "main.py"}',
        "main.py": "print('hé')\n".encode(), "cover.png": bytes(range(256)) * 3,
        "scenes/a.moyscene": b"[]", "images/deep/x/y.moyimg": b"img",
        "main.py.bak": b"crash", "journal/journal.jsonl": b"{}\n",
        "journal/s/0001-main.py": b"x", "thumbs/t.mct": b"t", "x.tmp": b"t",
    }
    for rel, data in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    return files


def test_the_archive_round_trips_with_the_browsers_codec(tmp_path):
    """moy_pack against firmware/web_runner/moy_store.mjs: the C archive is the
    twin's byte for byte and unzips in the browser's reader to the cart less
    what the wire skips; the browser's zipStore, a deflated zip and one with no
    top folder unpack in the C to the same files; adopt and the import put a
    cart on the shelf under the browser's naming rule."""
    import base64
    import shutil
    import zipfile
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    exe = _vm()
    src = str(tmp_path / "src" / "hop.moy")
    files = _cart_tree(src)
    kept = {"hop.moy/" + k: v for k, v in files.items()
            if not (k.endswith((".bak", ".tmp")) or k.startswith(("journal", "thumbs")))}
    with_history = dict(kept, **{"hop.moy/" + k: v for k, v in files.items()
                                 if k.startswith("journal")})
    lst = str(tmp_path / "files.json")
    with open(lst, "w") as f:
        json.dump([[k, base64.b64encode(v).decode()] for k, v in sorted(kept.items())], f)
    mjs = str(tmp_path / "drv.mjs")
    with open(mjs, "w") as f:
        f.write(NODE_DRIVER.replace("@MJS@", os.path.join(
            ROOT, "firmware", "web_runner", "moy_store.mjs")))
    subprocess.run(["node", mjs, "zip", lst, str(tmp_path / "browser.zip")], check=True)
    with zipfile.ZipFile(str(tmp_path / "deflated.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in kept.items():
            z.writestr(k, v)
    with zipfile.ZipFile(str(tmp_path / "flat.zip"), "w") as z:
        for k, v in kept.items():
            z.writestr(k[len("hop.moy/"):], v)
    outs = {}
    for tag, cmd in (("vm", [exe]), ("cp", [sys.executable])):
        for hist in (False, True):
            t = tag + ("h" if hist else "")
            script = (PACK_DRIVER.replace("@RUNTIME@", repr(os.path.join(ROOT, "runtime")))
                      .replace("@DIR@", repr(str(tmp_path))).replace("@TAG@", t)
                      .replace("@HIST@", repr(hist)))
            out = subprocess.run(cmd + ["-c", script], capture_output=True, text=True,
                                 timeout=300)
            assert out.returncode == 0, out.stdout + out.stderr
            outs[t] = out.stdout
            shutil.rmtree(str(tmp_path / "shelf"))
    assert outs["vm"] == outs["cp"] and outs["vmh"] == outs["cph"]
    for t, want in (("vm", kept), ("vmh", with_history)):
        with open(str(tmp_path / (t + ".moy")), "rb") as f:
            a = f.read()
        with open(str(tmp_path / (t.replace("vm", "cp") + ".moy")), "rb") as f:
            assert a == f.read()
        got = json.loads(subprocess.run(["node", mjs, "unzip", str(tmp_path / (t + ".moy"))],
                                        capture_output=True, text=True, check=True).stdout)
        assert {k: base64.b64decode(v) for k, v in got} == want
    for name in ("browser", "deflated", "flat"):
        for t in ("vm", "cp"):
            assert _tree(str(tmp_path / ("%s-%s" % (t, name)))) == {
                k[len("hop.moy/"):]: v for k, v in kept.items()}
    assert "import /shelf/carts/hop.moy" in outs["vm"]
    assert "import /shelf/carts/hop_2.moy" in outs["vm"]
