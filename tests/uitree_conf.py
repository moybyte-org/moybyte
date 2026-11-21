"""The conformance harness: one app written in Python and in Lua
(tests/uitree_conformance/NAME/app.py and NAME/NAME.moy), driven by one input
script on one golden configuration per size, giving per layer

- the trace, each line `SOURCE NAME DETAIL` (the timestamp and the column
  padding dropped, every quote one `'`: the spec leaves the ring, the clock
  and the quoting open, F-30, F-41);
- the frames, one settled frame after the opening and after each step.

The text dump of the built tree comes from each layer's checker run
(`dump`). Run by hand to see both sides:

    .venv/bin/python tests/uitree_conf.py NAME [CONFIG]
"""
import importlib.util
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)
import runtime  # noqa: E402,F401  -- the flat module namespace

FIX = os.path.join(HERE, "uitree_conformance")
# one configuration per size: the console's 320x240 and a desk window
CONFIGS = ("tdeck_320x240_fs1_dark", "p4_1024x600_fs2_windowed")


def fixture(name):
    d = os.path.join(FIX, name)
    return os.path.join(d, "app.py"), os.path.join(d, name + ".moy")


def _module(path):
    name = "conf_" + os.path.basename(os.path.dirname(path))
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _title(cart):
    import json
    with open(os.path.join(cart, "manifest.json")) as f:
        return json.load(f)["title"]


TRACE_LINE = re.compile(r"^\d\d:\d\d:\d\d\.\d\d\d\s+(\S+)\s+(\S+)\s*(.*)$")


def norm_trace(lines):
    out = []
    for line in lines:
        m = TRACE_LINE.match(line.strip())
        if not m:
            continue
        src, name, detail = m.groups()
        detail = re.sub(r"\s+", " ", detail.replace('"', "'")).strip()
        out.append(("%s %s %s" % (src, name, detail)).strip())
    return out


def run_python(name, config, script):
    """(trace lines, frames) of the Python half."""
    import uitree_parity as P
    import uitree_app
    app_py, cart = fixture(name)
    mod = _module(app_py)
    cls = uitree_app.app_layer("conf_" + name, _title(cart), mod.build)
    seen = {}

    def opened(ws):
        seen["app"] = P.host(ws, cls)
    frames = P.frames(config, opened, script)
    return norm_trace(seen["app"].engine.trace), frames


def run_lua(name, config, script):
    """(trace lines, frames) of the Lua half: the cart staged in the run's
    store and opened by its title; the trace is the layer's log lines."""
    import uitree_parity as P
    from runtime.host import moy_log
    from ws_helpers import open_cart
    _, cart = fixture(name)
    seq = {}

    def prepare(carts_dir):
        shutil.copytree(cart, os.path.join(carts_dir, os.path.basename(cart)))

    def opened(ws):
        seq["0"] = moy_log.next()
        open_cart(ws, _title(cart))
    frames = P.frames(config, P.Surface(opened, prepare), script)
    logs = [r[4] for r in moy_log.read(seq["0"], 4000)]
    lines = [l.split("[trace] ", 1)[1] for l in logs if "[trace] " in l]
    return norm_trace(lines), frames


def wasm_fixture(name):
    """The compiled half's cart (NAME/NAME_wasm.moy), or None where the fixture has none."""
    cart = os.path.join(FIX, name, name + "_wasm.moy")
    return cart if os.path.isdir(cart) else None


def run_wasm(name, config, script):
    """(trace lines, frames) of the compiled half: the cart built from its C
    (tools/wasm_cart.py) into the run's store and opened by its title; the
    trace is the engine face's log lines."""
    import uitree_parity as P
    from runtime.host import moy_log
    from tools import wasm_cart
    from ws_helpers import open_cart
    cart = wasm_fixture(name)
    seq = {}

    def prepare(carts_dir):
        wasm_cart.build(cart, os.path.join(carts_dir, os.path.basename(cart)))

    def opened(ws):
        seq["0"] = moy_log.next()
        open_cart(ws, _title(cart))
    frames = P.frames(config, P.Surface(opened, prepare), script)
    logs = [r[4] for r in moy_log.read(seq["0"], 4000)]
    lines = [l.split("[trace] ", 1)[1] for l in logs if "[trace] " in l]
    return norm_trace(lines), frames


def dump_python(name):
    import uitree_check
    app_py, _ = fixture(name)
    r = uitree_check.check(app_py, snap=True)
    return [e.text() for e in r.errors], "\n".join(r.snapshots)


def dump_lua(name):
    import uitree_lua_check
    _, cart = fixture(name)
    out = uitree_lua_check.check(cart, snapshot=True)
    return [l for l in out if l.startswith("error[")], "\n".join(l for l in out if not l.startswith("error["))


def differing(a, b):
    """The box the two frames differ in, and the count of pixels."""
    if a.buf == b.buf:
        return None, 0
    w = a.w
    bpp = len(a.buf) // (a.w * a.h)
    n, box = 0, None
    for i in range(0, len(a.buf), bpp):
        if a.buf[i:i + bpp] != b.buf[i:i + bpp]:
            n += 1
            x, y = (i // bpp) % w, (i // bpp) // w
            box = (x, y, x + 1, y + 1) if box is None else (min(box[0], x), min(box[1], y),
                                                             max(box[2], x + 1), max(box[3], y + 1))
    return box, n


if __name__ == "__main__":
    import json
    name = sys.argv[1]
    config = sys.argv[2] if len(sys.argv) > 2 else CONFIGS[0]
    with open(os.path.join(FIX, name, "script.json")) as f:
        script = json.load(f)
    tp, fp = run_python(name, config, script)
    tl, fl = run_lua(name, config, script)
    print("== python trace\n" + "\n".join(tp))
    print("== lua trace\n" + "\n".join(tl))
    for a, b in zip(fp, fl):
        print("frame", a.step, differing(a, b))
    out = os.path.join(ROOT, ".build", "uitree_conf")
    os.makedirs(out, exist_ok=True)
    import uitree_parity as P
    for i, (a, b) in enumerate(zip(fp, fl)):
        if a.buf != b.buf:
            P.side_by_side(a, b, os.path.join(out, "%s_%s_%02d.png" % (name, config, i)))
    print("== python dump", dump_python(name))
    print("== lua dump", dump_lua(name))
