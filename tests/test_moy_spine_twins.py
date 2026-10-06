"""The kernel spine's native twin beyond the interface suite: the part of
sprint 2's harness (tools/moy_index_spike.py --component spine) CI runs.

  * tests/test_moy_spine.py itself, run on the boards' VM -- the desktop
    MicroPython, both object models -- over the native module and over the
    Python twin, under a stand-in for the little of pytest it uses
    (tests/vm_suite.py);
  * the native module's own properties on that VM: the frame path's calls
    (top, has, index) allocate nothing, and what an object holds in C goes back
    when the collector drops it;
  * a seeded random walk of the whole API against the Python twin, every answer
    and every exception compared, for each native binding on the host;
  * the settings scanner against CPython's json: generated and corrupted text,
    accepted and refused as json.loads does, up to the three limits the store
    sets (an empty key, a lone surrogate in a key, 31 nested containers);
  * the API-sequence fuzz (native/moy_spine/fuzz_spine.c) under AddressSanitizer
    and UndefinedBehaviorSanitizer: #224's containment;
  * the placement rule of docs/native_kernel_2026-09.md section 4.6, asserted
    in the sources: kernel data is PSRAM.

The spine trace over the native module is tests/test_semantic_traces.py's.
"""

import json
import os
import random
import re
import subprocess

import pytest

import vm_suite
from runtime import moy_spine
from tools import moy_index_spike, moy_spine_binding
from unix_mp import find_unix_mp, require_unix_mp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NATIVE = moy_spine_binding.host_bindings()
SPINE_DIR = os.path.join(ROOT, "native", "moy_spine")

# Tests that need the host (a tmp store, the workstation), not a VM.
HOST_ONLY = ("test_the_store_pushes_only_what_its_mirror_changed",)


def _vm_suite(exe, tmp_path, native):
    """tests/test_moy_spine.py on `exe`: (passed, failed, output)."""
    return vm_suite.run(exe, tmp_path, "moy_spine", "test_moy_spine.py", native,
                        "moy_spine", skip=HOST_ONLY, extra=("moy_index",))


@pytest.mark.parametrize("native", [True, False], ids=["native", "python"])
def test_the_suite_passes_on_the_vm(tmp_path, native):
    exe = require_unix_mp(
        "moy_spine",
        why="tests/test_moy_spine.py on the VM a board runs, over the native "
            "spine (sprint 2's twin).")
    passed, failed, text = _vm_suite(exe, tmp_path, native)
    assert failed == 0 and passed >= 20, text
    board = find_unix_mp("moy_spine", board_model=True)
    if board is not None:
        passed, failed, text = _vm_suite(board, tmp_path / "r32", native)
        assert failed == 0 and passed >= 20, text


# What the frame loop calls per frame, on the VM: nothing here may allocate.
FRAME_PATH = '''
import gc, sys
sys.path[:] = []
import moy_spine as sp
assert not hasattr(sp, "__file__"), "no native moy_spine in this binary"
st = sp.BackStack()
st.goto("menu")
st.goto("desktop")
for _ in range(3):
    st.top(); st.has("menu"); st.has("nope"); st.index("desktop"); st.depth()
gc.collect()
before = gc.mem_alloc()
for _ in range(300):
    st.top()
    st.has("menu")
    st.has("nope")
    st.index("desktop")
    st.depth()
    st.has(None)
print("ALLOC", gc.mem_alloc() - before)
'''

# Make and drop objects that hold C memory; the process must not grow.
FINALISERS = '''
import gc, sys
sys.path[:] = []
import moy_spine as sp
assert not hasattr(sp, "__file__"), "no native moy_spine in this binary"
TEXT = "{" + ", ".join('"key%d": "%s"' % (i, "v" * 90) for i in range(20)) + "}"


def pages():
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1])


def round_():
    s = sp.Settings()
    s.load(TEXT)
    reg = sp.AppRegistry()
    for i in range(8):
        reg.register("app%d" % i, "App %d" % i, False, (i, i))
    rt = sp.Returns(reg)
    rt.run("app1")
    t = sp.Table(1, "x", 256)
    for i in range(100):
        t.new(i)
    st = sp.BackStack()
    st.goto("menu")
    sp.Leases().hold("web")


for _ in range(300):
    round_()
gc.collect()
base = pages()
for i in range(3000):
    round_()
    if i % 50 == 0:
        gc.collect()
gc.collect()
print("GROWTH", (pages() - base) * 4096)
'''


def _vm_script(exe, tmp_path, name, source):
    script = tmp_path / (name + ".py")
    script.write_text(source)
    # The sanitizer run preloads libasan into pytest; the VM is not built with
    # it, and ASan's delayed frees would read as growth.
    env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}
    out = subprocess.run([exe, str(script)], capture_output=True, text=True,
                         timeout=300, env=env)
    assert out.returncode == 0, out.stderr or out.stdout
    return out.stdout


def _exes():
    exe = require_unix_mp(
        "moy_spine",
        why="The native spine on the VM a board runs: what its frame-path "
            "calls allocate and what its objects give back.")
    out = [("64-bit", exe)]
    board = find_unix_mp("moy_spine", board_model=True)
    if board is not None:
        out.append(("boards' model", board))
    return out


def test_the_frame_path_allocates_nothing(tmp_path):
    """top(), has() and index() hand out interned strs and ints: no object is
    made per call, so a frame that asks where it is costs the collector
    nothing (tests/test_frame_alloc.py is the same rule over the shell)."""
    for tag, exe in _exes():
        out = _vm_script(exe, tmp_path, "frame_" + tag[:2], FRAME_PATH)
        assert "ALLOC 0" in out, "%s: %s" % (tag, out)


def test_what_an_object_holds_in_c_goes_back_when_it_is_dropped(tmp_path):
    if not os.path.exists("/proc/self/statm"):
        pytest.skip("no /proc to read the process's size from")
    for tag, exe in _exes():
        out = _vm_script(exe, tmp_path, "final_" + tag[:2], FINALISERS)
        growth = int(re.search(r"GROWTH (-?\d+)", out).group(1))
        # 3000 rounds hold about 5 KB each in C: a missing finaliser is 15 MB
        assert growth < 3 << 20, "%s: grew %d bytes\n%s" % (tag, growth, out)


# -- a random walk against the Python twin -----------------------------------------

def _outcome(fn, *a):
    try:
        return ("ok", fn(*a))
    except TypeError:
        return ("type",)
    except OSError as e:
        return ("oserror", e.args[0])
    except MemoryError:
        return ("memory",)
    except ValueError as e:
        # StaleHandle is a ValueError in each module; JSON's decode error
        # is one too, and the twin says "value" for both of those.
        return ("stale",) if type(e).__name__ == "StaleHandle" else ("value",)


KINDS = ["launcher", "menu", "desk", "desktop", "settings", "files", "artwork",
         "calc"] + ["k%d" % i for i in range(40)] + ["", "x" * 16, "éé",
                                                       "x" * 15, None, 3]
TAGS = list(moy_spine.LEASE_TAGS) + ["", "wasm", "we", None, 4]
KEYS = ["a", "b", "theme", "fs", "app_guard", 'q"q', "b\\s", "n\nl", "é",
        "\U0001f600", "k\x7f", "x y", "", None, 5]
VALUES = ["1", "-0", "0.5e+3", "true", "null", "NaN", '"s"', '"\\u00e9\\n"',
          "[]", "{}", '[1, 2, {"a": null}]', " 7 ", "01", "[1,]", "", "'x'",
          "[" * 31 + "]" * 31, "[" * 32 + "]" * 32, 7, None]


def _canon(settings):
    """A settings store's file as JSON would read it, for comparing twins whose
    key escapes differ in spelling."""
    return json.loads(settings.dump())


@pytest.mark.parametrize("name", sorted(NATIVE))
def test_a_random_walk_agrees_with_the_python_twin(name):
    """Tens of thousands of calls in a seeded order across every component,
    with kinds that repeat, handles live, released and forged, tables filled to
    the brim and drained, and text the store refuses: the native twin answers
    each exactly as runtime/moy_spine.py does."""
    rnd = random.Random(1)
    nat = NATIVE[name]
    ref = moy_spine

    def pair(make):
        return make(ref), make(nat)

    tables = []

    def new_table():
        kind, slots = rnd.choice([(1, 4), (2, 256), (3, 1), (15, 8), (7, 3)])
        tables.append(pair(lambda m: m.Table(kind, "t%d" % kind, slots)))

    new_table()
    apps = pair(lambda m: m.AppRegistry())
    back = pair(lambda m: m.BackStack())
    returns = (ref.Returns(apps[0]), nat.Returns(apps[1]))
    leases = pair(lambda m: m.Leases())
    sets = pair(lambda m: m.Settings())
    seen = [0, -1, 1 << 40, None, "7", 2.0, 1 << 30, (1 << 30) - 1]

    def both(objs, op, *args):
        got = (_outcome(getattr(objs[0], op), *args),
               _outcome(getattr(objs[1], op), *args))
        return got

    def agree(got, step):
        assert got[0] == got[1], (step, got)

    for step in range(40000):
        k = rnd.random()
        if k < 0.17:                                     # the handle tables
            if rnd.random() < 0.01:
                new_table()
            objs = rnd.choice(tables)
            hs = objs[0].handles()
            h = (rnd.choice(hs) if hs and rnd.random() < 0.6
                 else rnd.choice(seen))
            op = rnd.choice(("new", "new", "get", "valid", "release", "put"))
            args = ({"new": (step,), "put": (h, step)}).get(op, (h,))
            got = both(objs, op, *args)
            if op == "new" and got[0][0] == "ok":
                got = tuple(("ok", None) if g[0] == "ok" else g for g in got)
                # the two tables handed out the same handle
                assert objs[0].handles() == objs[1].handles(), step
            if op == "release" and got[0][0] == "ok":
                seen.append(h)
            agree(got, step)
            if got[0][0] == "ok" and isinstance(got[0][1], int) \
                    and op != "get":
                seen.append(got[0][1])
        elif k < 0.30:                                   # the registry
            op = rnd.choice(("register", "register", "find", "get"))
            if op == "register":
                i = rnd.randrange(80)
                aid = rnd.choice(["app%d" % i] * 3 + KINDS[:6] + [None, "", 3])
                title = rnd.choice(["T%d" % i, "é", "", 42, None])
                size = rnd.choice([None, (i, i + 1), [3, 4], (-2, 1 << 31),
                                   (1 << 31, 0)])
                got = both(apps, "register", aid, title, rnd.random() < 0.5,
                           size)
                if got[0][0] == "ok":
                    assert got[0] == got[1], (step, got)
            elif op == "find":
                got = both(apps, "find", rnd.choice(["app%d" % rnd.randrange(80),
                                                     "menu", "", None, 7]))
            else:
                hs = apps[0].handles()
                h = (rnd.choice(hs) if hs and rnd.random() < 0.7
                     else rnd.choice(seen))
                for meth in ("app_id", "title", "text_mode", "min_size",
                             "valid"):
                    agree(both(apps, meth, h), (step, meth))
                continue
            agree(got, step)
            assert apps[0].handles() == apps[1].handles()
        elif k < 0.50:                                   # the back-stack
            op = rnd.choice(("goto", "goto", "goto", "remove", "has", "index"))
            kind = rnd.choice(KINDS)
            if rnd.random() < 0.02:
                for i in range(40):
                    agree(both(back, "goto", "z%d" % i), (step, i))
            agree(both(back, op, kind), step)
            if step % 7 == 0:
                assert back[0].kinds() == back[1].kinds(), step
                assert both(back, "top")[0] == both(back, "top")[1]
                assert back[0].depth() == back[1].depth()
        elif k < 0.62:                                   # the return records
            op = rnd.choice(("run", "spend", "route", "note", "take_back",
                             "caller", "back"))
            if op == "run":
                a = rnd.choice(KINDS + ["app%d" % rnd.randrange(80)] + [None])
                agree((_outcome(returns[0].run, a), _outcome(returns[1].run, a)),
                      step)
            elif op in ("route", "note"):
                a = (rnd.random() < 0.5,) if op == "route" else (
                    rnd.choice(KINDS + ["app%d" % rnd.randrange(80)]),)
                agree(both(returns, op, *a), step)
            else:
                agree(both(returns, op), step)
            agree(both(returns, "caller"), (step, "caller"))
            agree(both(returns, "back"), (step, "back"))
        elif k < 0.68:                                   # the leases
            op = rnd.choice(("hold", "release", "held"))
            agree(both(leases, op, rnd.choice(TAGS)), step)
            assert leases[0].mask() == leases[1].mask()
            assert leases[0].holders() == leases[1].holders()
        else:                                            # the settings rows
            op = rnd.choice(("set", "set", "set", "get", "delete", "load",
                             "adopt"))
            key = rnd.choice(KEYS)
            if op == "set":
                got = both(sets, "set", key, rnd.choice(VALUES))
            elif op == "load":
                members = {rnd.choice(KEYS[:12]): rnd.choice(
                    [1, "x", None, [1, 2], {"a": [True]}, 1.5, "é"])
                    for _ in range(rnd.randrange(5))}
                got = both(sets, "load", json.dumps(members))
            elif op == "adopt":
                members = {rnd.choice(KEYS[:12]): rnd.choice(
                    [1, "x", None, [1, 2], {"a": [True]}, 1.5, "é"])
                    for _ in range(rnd.randrange(5))}
                got = both(sets, "adopt", members)
            else:
                got = both(sets, op, key)
            agree(got, step)
            assert sets[0].keys() == sets[1].keys(), step
            assert _canon(sets[0]) == _canon(sets[1]), step
            for kk in sets[0].keys():
                assert sets[0].get(kk) == sets[1].get(kk), (step, kk)
    for objs in tables:
        assert objs[0].handles() == objs[1].handles()
    assert apps[0].handles() == apps[1].handles()
    assert back[0].kinds() == back[1].kinds()


# -- the settings scanner against CPython's json ---------------------------------

def _json_values(rnd, depth=0):
    k = rnd.random()
    if depth > 6 or k < 0.3:
        return rnd.choice([
            None, True, False, 0, -1, 7, 2 ** 70, 1.5, -0.0, 1e300, float("nan"),
            float("inf"), "", "a", "café", "q\"uote", "back\\slash",
            "nl\nx", "\x00\x1f", "\U0001f600", " ", "\ud800", "\x7f"])
    if k < 0.65:
        return [_json_values(rnd, depth + 1) for _ in range(rnd.randrange(4))]
    return {rnd.choice(["a", "b", "k", "café", "q\"", "\U0001f600", "n\n",
                        "\x01", "x" * rnd.randrange(1, 6)]):
            _json_values(rnd, depth + 1) for _ in range(rnd.randrange(4))}


def _depth(v):
    if isinstance(v, dict):
        v = list(v.values())
    elif not isinstance(v, list):
        return 0
    return 1 + max([_depth(x) for x in v] or [0])


def _whitespaced(rnd, text):
    """`text` with spaces, tabs and newlines between its tokens, outside strings."""
    out, in_str, esc = [], False, False
    for ch in text:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{}[],:" and rnd.random() < 0.3:
            out.append(rnd.choice([" ", "\t", "\n", "\r", "  "]))
        out.append(ch)
    return "".join(out)


def _corrupt(rnd, text):
    if not text:
        return text
    at = rnd.randrange(len(text))
    how = rnd.randrange(5)
    if how == 0:
        return text[:at]
    if how == 1:
        return text[:at] + text[at + 1:]
    if how == 2:
        return text[:at] + rnd.choice('{}[]",:\\ \t-+.eE0x') + text[at:]
    if how == 3:
        return text[:at] + rnd.choice('{}[]",:\\ae0\n\x01') + text[at + 1:]
    return text + rnd.choice(["x", " ", ",", "}", "{}", "\x00"])


def _expected(text):
    """What the store must do with `text`: the rows (key, value) it holds, or
    None for a refusal -- json.loads, less the three limits."""
    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        return None             # a str with a surrogate in it has no UTF-8
    try:
        d = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if not k or any(0xD800 <= ord(c) <= 0xDFFF for c in k):
            return None
        if _depth(v) > 31:
            return None
    return d


@pytest.mark.parametrize("name", sorted(NATIVE))
def test_the_scanner_agrees_with_cpythons_json(name):
    """Generated objects, written every way json.dumps can and with whitespace
    in every gap, and the same text with a byte broken: the scanner takes
    exactly what json.loads takes (less an empty key, a lone surrogate in a
    key and a value nested past 31 containers, and a str that has no UTF-8), holds each value as text that
    parses to what json.loads made of it, and a refusal changes nothing."""
    rnd = random.Random(2)
    sp = NATIVE[name]
    accepted = refused = 0
    for case in range(4000):
        members = {rnd.choice(["a", "b", "theme", "café", "q\"", "n\n",
                               "\U0001f600", "\x01", "k%d" % rnd.randrange(30)]):
                   _json_values(rnd) for _ in range(rnd.randrange(6))}
        if rnd.random() < 0.05:
            members[rnd.choice(["", "\ud800", "\udc00"])] = 1
        if rnd.random() < 0.05:
            members["deep"] = _nest(rnd.randrange(28, 36))
        text = json.dumps(members, ensure_ascii=rnd.random() < 0.5,
                          indent=rnd.choice([None, None, 1, "\t"]),
                          separators=rnd.choice([None, (",", ":"), (" , ", " : ")]))
        if rnd.random() < 0.4:
            text = _whitespaced(rnd, text)
        if rnd.random() < 0.5:
            text = _corrupt(rnd, text)
        want = _expected(text)
        s = sp.Settings()
        s.set("before", "1")
        got = _outcome(s.load, text)
        if want is None:
            assert got[0] in ("value", "type"), (case, text, got)
            assert s.keys() == ["before"] and s.get("before") == "1", (case, text)
            refused += 1
            continue
        accepted += 1
        assert got == ("ok", len(want)), (case, text, got)
        assert s.keys() == list(want), (case, text)
        for k, v in want.items():
            assert json.dumps(json.loads(s.get(k))) == json.dumps(v), (case, k)
        # the file it writes is the same object, and loads to itself
        assert json.dumps(json.loads(s.dump())) == json.dumps(want)
        again = sp.Settings()
        assert again.load(s.dump()) == len(want)
        assert again.dump() == s.dump()
    assert accepted > 800 and refused > 800, (accepted, refused)


def _nest(n):
    return json.loads("[" * n + "]" * n)


# -- the fuzz, under the sanitizers ------------------------------------------------

def test_the_api_fuzz_runs_clean_under_the_sanitizers():
    """fuzz_spine's seeded programs, built with ASan and UBSan: every answer
    checked against its model, every byte returned."""
    got = moy_index_spike.fuzz_seeded("c", seed=1, runs=150,
                                      comp=moy_index_spike.SPINE)
    if got is None:
        if os.environ.get("CI"):
            pytest.fail("no C compiler with AddressSanitizer here")
        pytest.skip("no C compiler with AddressSanitizer here")
    ok, secs, out = got
    assert ok, out[-4000:]
    assert "150 programs" in out, out


# -- the placement rule --------------------------------------------------------------

def _code(path):
    """A C file's code with comments and string literals blanked."""
    with open(path) as f:
        text = f.read()
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.sub(r'"(\\.|[^"\\])*"', '""', text)


ALLOC_CALLS = re.compile(r"\b(malloc|calloc|realloc|free|heap_caps_\w+|m_malloc"
                         r"\w*|m_new\w*|m_del\w*|m_realloc\w*)\s*\(")


def test_kernel_data_is_allocated_from_psram():
    """docs/native_kernel_2026-09.md section 4.6: kernel data is
    heap_caps(MALLOC_CAP_SPIRAM) unless it is latency-bound. The components
    allocate only through the table's allocator; the binding's allocator asks
    for PSRAM first and falls to the default heap only on a board with none;
    and the one thing the binding keeps on the VM's heap is a Table's row
    objects, which the collector must see."""
    for name in ("moy_htab.c", "moy_route.c", "moy_settings.c"):
        hits = ALLOC_CALLS.findall(_code(os.path.join(SPINE_DIR, name)))
        assert not hits, "%s allocates by itself: %s" % (name, hits)
    binding = _code(os.path.join(SPINE_DIR, "modmoy_spine.c"))
    m = re.search(r"#ifdef ESP_PLATFORM\s+static void \*spine_alloc.*?#else",
                  binding, re.S)
    assert m, "no board allocator in modmoy_spine.c"
    alloc = m.group(0)
    first = alloc.index("heap_caps_calloc")
    assert "MALLOC_CAP_SPIRAM" in alloc[first:alloc.index(";", first)]
    assert alloc.count("heap_caps_calloc") == 2
    assert "heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0" in alloc
    outside = ALLOC_CALLS.findall(binding.replace(alloc, " "))
    # spine_alloc / spine_release off the board (calloc, free), the rows array
    assert sorted(outside) == ["calloc", "free", "m_new0"], outside
