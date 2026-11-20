"""Parity between a surface and its `uitree` port, frame by frame.

A port lands with today's pixels. The shell goldens hash one settled frame
per surface; parity drives a surface through an input SCRIPT on a golden
configuration (`test_shell_goldens.CONFIGS`) and settles a frame after the
opening and after every step, so focus moves, pushed pages, dialogs and
typed text are compared too.

    frames(config, open_surface, script, carts_dir)   -> [Frame]
    compare(old, new, out_dir)                         -> None, or AssertionError

`open_surface(ws)` opens the surface on a fresh workstation (`ws.open_settings()`
then `ws.settings_layer.open_page("permissions")`, `ws.spine.open_app(...)`).

A step of a script, one string each:

    k:NAME     a button press (up down left right a b)
    t:TEXT     typed characters
    enter      Enter
    del        Delete
    back       Esc: the console's back
    f:N        N more frames

`compare` reports the FIRST step whose frame differs: its index and step,
the bounding box of the difference, the count of pixels, and a PNG of the
old frame, the new one and the difference marked red, side by side.

The two halves of a port's run usually live in one commit only while it is
being ported. `record` keeps the old surface's frames as hashes
(tests/uitree_parity/NAME.json, with the commit they were taken at) so the
port is held to them after the old code is gone; `check_recorded` runs the
port against them and, when a step moved, writes the port's frame and says
how to see the old one:

    .venv/bin/python tests/uitree_parity.py show NAME     # old (at its commit) | new | diff
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "tests" / "uitree_parity"
OUT = ROOT / ".build" / "uitree_parity"
DT = 1.0 / 30.0
SETTLE = 8                  # frames a step may take to settle


class Frame:
    __slots__ = ("step", "buf", "w", "h")

    def __init__(self, step, buf, w, h):
        self.step, self.buf, self.w, self.h = step, buf, w, h

    def digest(self):
        return hashlib.sha256(self.buf).hexdigest()


def _goldens():
    sys.path.insert(0, str(ROOT / "tests"))
    import test_shell_goldens
    return test_shell_goldens


def _settle(ws, g, driver):
    prev = None
    for _ in range(SETTLE):
        g._quiesce(ws)
        ws.mark_dirty()
        driver.frame(DT)
        cur = bytes(ws.glass_canvas._buf)
        if cur == prev:
            return cur
        prev = cur
    return prev


def _step(ws, driver, step):
    op, _, arg = step.partition(":")
    if op == "k":
        driver.press(arg)
    elif op == "t":
        for c in arg:
            driver.type_char(ord(c))
            driver.frame(DT)
    elif op == "enter":
        driver.type_char(13)
    elif op == "del":
        driver.type_char(127)
    elif op == "back":
        driver.escape()
    elif op == "f":
        for _ in range(int(arg)):
            ws.mark_dirty()
            driver.frame(DT)
    else:
        raise ValueError("parity step %r: one of k:NAME t:TEXT enter del back f:N" % step)


def frames(config, open_surface, script, carts_dir=None):
    """The settled frame after the opening and after each step of `script`,
    on a fresh workstation of golden configuration `config`."""
    g = _goldens()
    from runtime.host_api import ConsoleDriver
    carts_dir = carts_dir or tempfile.mkdtemp(prefix="uitree_parity_")
    ws = g._build(g.CONFIGS[config], carts_dir)
    out = []
    try:
        open_surface(ws)
        driver = ConsoleDriver(ws)
        cv = ws.glass_canvas
        out.append(Frame("(open)", _settle(ws, g, driver), cv.w, cv.h))
        for step in script:
            _step(ws, driver, step)
            out.append(Frame(step, _settle(ws, g, driver), cv.w, cv.h))
    finally:
        ws.home.go_home()
        from runtime import scheduler
        scheduler.SCHED.reset()
        g._ui().theme_uninstall()
    return out


def _rgb(f):
    import pixel_goldens
    return pixel_goldens.rgb565_rows(f.buf, f.w, f.h)


def side_by_side(old, new, path):
    """old | new | the difference (red where they differ, the old dimmed elsewhere)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import pngwrite
    a, b = _rgb(old), _rgb(new)
    w, h = old.w, old.h
    rows = []
    for y in range(h):
        ra, rb = a[y], b[y]
        diff = bytearray(w * 3)
        for x in range(w):
            j = 3 * x
            if ra[j:j + 3] != rb[j:j + 3]:
                diff[j:j + 3] = b"\xff\x00\x00"
            else:
                diff[j:j + 3] = bytes(v // 4 for v in ra[j:j + 3])
        rows.append(ra + b"\xff\x00\xff" * 4 + rb + b"\xff\x00\xff" * 4 + bytes(diff))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pngwrite.write_png(str(path), rows, 3 * w + 8, h)
    return path


def _box(old, new):
    a, b = old.buf, new.buf
    xs, ys, n = [], [], 0
    for i in range(0, len(a), 2):
        if a[i:i + 2] != b[i:i + 2]:
            p = i // 2
            xs.append(p % old.w)
            ys.append(p // old.w)
            n += 1
    return (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1), n


def compare(old, new, out_dir=None, name="parity"):
    """Raise at the first step whose frames differ, with a side-by-side PNG."""
    out_dir = Path(out_dir or OUT)
    if [f.step for f in old] != [f.step for f in new]:
        raise AssertionError("the two runs took different scripts: %r / %r"
                             % ([f.step for f in old], [f.step for f in new]))
    for i, (o, n) in enumerate(zip(old, new)):
        if (o.w, o.h) != (n.w, n.h):
            raise AssertionError("step %d %r: frames of %dx%d and %dx%d" % (i, o.step, o.w, o.h, n.w, n.h))
        if o.buf != n.buf:
            box, count = _box(o, n)
            png = side_by_side(o, n, out_dir / ("%s_step%02d.png" % (name, i)))
            raise AssertionError(
                "PARITY: step %d (%r) is the first that differs: %d px in the box x=%d y=%d w=%d h=%d\n"
                "  old | new | difference: %s" % (i, o.step, count, box[0], box[1], box[2], box[3], png))


def host(ws, layer_cls, min_size=(160, 120)):
    """Register and open an app no cart carries (a test's), the way the
    console registers a system app (`Spine._declared_apps`)."""
    from runtime.ui_draw import AppHost
    ctx = ws.app_context(layer_cls.id, layer_cls.NEEDS)
    ctx.ui = AppHost(layer_cls.id, ctx.surface)
    ws._app_hosts[layer_cls.id] = ctx.ui
    app = layer_cls(ctx)
    ws.spine.register_app(app, min_size=min_size, cart=False)
    app.open()
    ws.wm.activate(app.id)
    return app


# -- recorded runs -------------------------------------------------------------

def _head():
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=9", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def record(name, config, open_surface, script):
    """Keep the old surface's frames as hashes, with the commit they came from."""
    fs = frames(config, open_surface, script)
    RECORDS.mkdir(parents=True, exist_ok=True)
    body = {"config": config, "commit": _head(), "script": list(script),
            "frames": [[f.step, f.digest()] for f in fs]}
    (RECORDS / (name + ".json")).write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    return body


def load(name):
    return json.loads((RECORDS / (name + ".json")).read_text(encoding="utf-8"))


def check_recorded(name, open_surface):
    """Run the port through the recorded script; raise at the first step
    whose frame is not the recorded one."""
    body = load(name)
    fs = frames(body["config"], open_surface, body["script"])
    for i, (f, (step, want)) in enumerate(zip(fs, body["frames"])):
        if f.digest() != want:
            sys.path.insert(0, str(ROOT / "tools"))
            import pngwrite
            path = OUT / name / ("new_step%02d.png" % i)
            path.parent.mkdir(parents=True, exist_ok=True)
            pngwrite.write_png(str(path), _rgb(f), f.w, f.h)
            raise AssertionError(
                "PARITY %s: step %d (%r) moved from the frame recorded at %s (config %s)\n"
                "  the port's frame: %s\n"
                "  old | new | difference:  .venv/bin/python tests/uitree_parity.py show %s"
                % (name, i, step, body["commit"], body["config"], path, name))
    return fs


# -- the command -----------------------------------------------------------------

def _surfaces():
    sys.path.insert(0, str(ROOT / "tests"))
    import test_uitree_ports
    return test_uitree_ports.SURFACES


def _show(name):
    """The old frames from the recorded commit's tree, the new from this
    one's, compared: the first differing step's side-by-side."""
    body = load(name)
    opener = _surfaces()[name]
    with tempfile.TemporaryDirectory(prefix="uitree_old_") as old_tree:
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", old_tree, body["commit"]],
                       check=True, capture_output=True)
        try:
            code = ("import sys, json, pickle; sys.path.insert(0, %r); sys.path.insert(0, %r);"
                    "import uitree_parity as P, test_uitree_ports as T;"
                    "fs = P.frames(%r, T.SURFACES[%r], %r);"
                    "sys.stdout.buffer.write(pickle.dumps([(f.step, f.buf, f.w, f.h) for f in fs]))"
                    % (str(Path(old_tree) / "tests"), old_tree, body["config"], name, body["script"]))
            env = dict(os.environ, PYTHONPATH=old_tree)
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, cwd=old_tree)
            if r.returncode:
                raise SystemExit(r.stderr.decode()[-2000:])
            import pickle
            old = [Frame(*t) for t in pickle.loads(r.stdout)]
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", old_tree],
                           capture_output=True)
    new = frames(body["config"], opener, body["script"])
    try:
        compare(old, new, OUT / name, name)
    except AssertionError as e:
        print(e)
        return 1
    print("%s: every step equal" % name)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "show":
        sys.path.insert(0, str(ROOT))
        sys.exit(_show(sys.argv[2]))
    print(__doc__)
    sys.exit(2)
