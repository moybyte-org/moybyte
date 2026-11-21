"""Parity between a surface and its `uitree` port, frame by frame.

A port lands with today's pixels. The shell goldens hash one settled frame
per surface; parity drives a surface through an input SCRIPT on a golden
configuration (`test_shell_goldens.CONFIGS`) and settles a frame after the
opening and after every step, so focus moves, pushed pages, dialogs and
typed text are compared too.

    frames(config, open_surface, script, carts_dir)   -> [Frame]
    compare(old, new, out_dir)                         -> None, or AssertionError

`surface` (a `Surface`, or a function of the workstation) opens the surface on a fresh workstation (`ws.open_settings()`
then `ws.settings_layer.open_page("permissions")`, `ws.spine.open_app(...)`).

A step of a script, one string each:

    k:NAME     a button press (up down left right a b)
    t:TEXT     typed characters
    enter      Enter
    del        Delete
    back       Esc: the console's back
    f:N        N more frames
    key:SPEC   a desk keyboard's key with its modifiers: `C-` Ctrl, `S-` Shift,
               `A-` Alt before a name (Left Right Up Down Tab Enter F6) or one
               character (`key:C-3`, `key:C-A-Right`, `key:S-F6`)
    div:N:D    the desk Editor's divider N (in reading order) dragged D px
               along its axis by touch, and let go
    pane:N     a tap at the centre of the desk Editor's pane N's body
    swipe:D    a touch at the centre of the content rect dragged D px down (up
               when negative) in SWIPE_MOVES moves, a frame each, and let go:
               the drag and the fling a finger gives the page

`compare` reports the FIRST step whose frame differs: its index and step,
the bounding box of the difference, the count of pixels, and a PNG of the
old frame, the new one and the difference marked red, side by side.

The two halves of a port's run usually live in one commit only while it is
being ported. `record` keeps the old surface's frames as hashes
(tests/uitree_parity/NAME.json, with the commit they were taken at) so the
port is held to them after the old code is gone; `check_recorded` runs the
port against them and, when a step moved, writes the port's frame and says
how to see the old one:

    .venv/bin/python tests/uitree_parity.py show NAME          # old (at its commit) | new | diff
    .venv/bin/python tests/uitree_parity.py record NAME REF    # the record, from the old code at REF
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
SWIPE_MOVES = 12


class Surface:
    """What a parity run opens: `open(ws)` on the built workstation, after
    `prepare(carts_dir)` wrote what its store needs (carts that ask for
    permissions, a document). `probe(ws)`, where given, says the surface's
    state after each step in one line (its text, caret, what is open): a
    behaviour run compares those and not the pixels."""

    def __init__(self, open, prepare=None, probe=None):
        self.open, self.prepare, self.probe = open, prepare, probe


# The fifth field of a run whose surface takes the engine's layout (K5,
# owner 2026-11-20): its steps are held to the recorded state, not pixels.
BEHAVIOUR = "behaviour"


class Frame:
    __slots__ = ("step", "buf", "w", "h", "state")

    def __init__(self, step, buf, w, h, state=""):
        self.step, self.buf, self.w, self.h, self.state = step, buf, w, h, state

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
    elif op == "zone":
        _zone_tap(ws, driver, int(arg))
    elif op == "cell":
        _cell_tap(ws, driver, arg)
    elif op == "key":
        _key(driver, arg)
    elif op == "div":
        n, _, d = arg.partition(":")
        _div_drag(ws, driver, int(n), int(d))
    elif op == "pane":
        _pane_tap(ws, driver, int(arg))
    elif op == "swipe":
        _swipe(ws, driver, int(arg))
    else:
        raise ValueError("parity step %r: one of k:NAME t:TEXT enter del back f:N zone:N cell:NAME "
                         "key:SPEC div:N:D pane:N swipe:D" % step)


_KEYS = {"Left": 0x110, "Right": 0x111, "Up": 0x112, "Down": 0x113, "Tab": 9, "Enter": 13,
         "F6": 0x101 + 5}
_MODS = {"C": 1, "S": 2, "A": 4}


def _key(driver, spec):
    """A desk keyboard's key `spec` (`C-A-Right`) down and up, its
    modifiers held round it, a frame each."""
    *mods, name = spec.split("-")
    m = 0
    for t in mods:
        m |= _MODS[t]
    code = _KEYS[name] if name in _KEYS else ord(name)
    if m:
        driver.set_mods(m)
        driver.frame(DT)
    driver.key_down(code, m)
    driver.frame(DT)
    driver.key_up(code, m)
    driver.frame(DT)
    if m:
        driver.set_mods(0)
        driver.frame(DT)


def _desk_origin(ws):
    """Where the desk Editor's surface sits on the glass."""
    return ws.editor_desk._origin()


def _div_drag(ws, driver, n, d):
    """Divider `n` of the workspace on show, in its placement's order,
    pressed at its centre, dragged `d` px along its axis in four moves, and
    let go."""
    desk = ws.editor_desk
    _path, _i, axis, (x, y, w, h) = desk.placed()[1][n]
    ox, oy = _desk_origin(ws)
    gx, gy = ox + x + w // 2, oy + y + h // 2
    dx, dy = (d, 0) if axis == "h" else (0, d)
    driver.touch(gx, gy)
    driver.frame(DT)
    for k in range(1, 5):
        driver.touch_drag(gx + dx * k // 4, gy + dy * k // 4)
        driver.frame(DT)
    driver.touch_up()
    driver.frame(DT)


def _swipe(ws, driver, d):
    """A finger down at the centre of the content rect, moved `d` px along y
    in SWIPE_MOVES even moves, a frame each, and lifted."""
    lay = ws.layout
    x, y = lay.cx + lay.cw // 2, lay.cy + lay.ch // 2
    driver.touch(x, y)
    driver.frame(DT)
    for k in range(1, SWIPE_MOVES + 1):
        driver.touch_drag(x, y + d * k // SWIPE_MOVES)
        driver.frame(DT)
    driver.touch_up()
    driver.frame(DT)


def _pane_tap(ws, driver, n):
    """A tap at the centre of pane `n`'s body (the panes shown, in
    reading order)."""
    desk = ws.editor_desk
    for k, pane, r in desk._shown_panes():
        if k == n:
            break
    else:
        raise KeyError("no pane %d on show" % n)
    bx, by, bw, bh = desk._body(r, desk._unit(), pane)
    ox, oy = _desk_origin(ws)
    driver.touch(ox + bx + bw // 2, oy + by + bh // 2)
    driver.frame(DT)
    driver.touch_up()
    driver.frame(DT)


def _cell_tap(ws, driver, name):
    """A tap on the Editor's bar cell `name` at its centre, through the
    zone's owner (the bar's own tap translation is the handheld runs'
    `zone:N`): a desk workspace "ws:KEY", a tab, "undo", "redo", "play",
    "restart", "stop". The cells are the Editor's (`EditorApp.zone_cells`),
    or, at a commit before its desk row was lent, that row's own layout."""
    bl = ws.bar_layer
    zone = bl.lent_zone("menu", bl.cell("menu", "zone")[0])
    ea = ws.editor_app
    cells = {}
    if hasattr(ea, "_row_layout") and ea._desk() is not None:
        tabs, undo_r, redo_r, _line, play_r = ea._row_layout(zone, ea._desk())
        cells = {t: r for t, _l, _k, r, _o in tabs}
        cells.update(undo=undo_r, redo=redo_r, play=play_r)
        cells["restart"], cells["stop"] = ea._run_rects(play_r)
    else:
        names = {v: k for k, v in ea._HEAD_TAB.items()}
        for t, _icon, r in ea.zone_cells(zone):
            cells[names.get(t, t)] = r
    if name not in cells:
        raise KeyError("%r is not on the bar: %r" % (name, sorted(map(str, cells))))
    r = cells[name]
    (ea.bar_zone or ea).zone_tap(r[0] + r[2] // 2, r[1] + r[3] // 2, zone)
    driver.frame(DT)


def _zone_tap(ws, driver, i):
    """A tap on the Editor bar zone's target `i` (its owner's `zone_targets`,
    left to right: the tabs, undo, redo, Play), at its centre."""
    bl = ws.bar_layer
    bar = bl.cell("menu", "zone")[0]
    zone = bl.lent_zone("menu", bar)
    r = bl._zone_owner("menu").zone_targets(zone)[i]
    driver.touch(bar[1][0] + r[0] + r[2] // 2, bar[1][1] + r[1] + r[3] // 2)
    driver.frame(DT)
    driver.touch_up()
    driver.frame(DT)


MASK = b"\x1f\xf8"           # the one colour the WM's chrome is hashed as


def _chrome_rects(ws):
    """[(key, (x, y, w, h))] the window manager's own chrome takes on the glass, in
    z-order: each framed window's title strip, and each full-screen row's
    head and controls (the band's lent zone between them is the app's). A
    parity run holds a SURFACE to its old self; the WM draws these, so both
    halves hash them as one colour."""
    wm = getattr(ws, "wm", None)
    order = getattr(wm, "z_order", None)
    if not order:
        return []
    out = []
    for key in order:
        win = wm.windows.get(key)
        if win is None or win.minimized or win.docked is not None:
            continue
        if win.saved is None:
            out.append((key, (win.x + 1, win.y + 1, win.w - 2, win.title_h)))
        elif win.row:
            out.append((key, (win.x, win.y, win.w, win.row)))
        elif not wm._is_game(win) and wm._band_is_row(win.kind):
            h = wm._row_h()
            span = wm.band_span() if wm._installed is win else None
            if span is None:
                out.append((key, (win.x, win.y, win.w, h)))
            else:
                out.append((key, (win.x, win.y, span[0] - win.x, h)))
                out.append((key, (span[1], win.y, win.x + win.w - span[1], h)))
    return out


def _masked(ws, buf, w, h):
    """`buf` with the WM's chrome (`_chrome_rects`) filled with MASK, each
    rect clipped by the windows above its own."""
    wm = getattr(ws, "wm", None)
    rects = _chrome_rects(ws)
    if not rects:
        return buf
    order = list(wm.z_order)
    out = bytearray(buf)
    for key, (rx, ry, rw, rh) in rects:
        above = [(win.x, win.y, win.w, win.h) for win in
                 (wm.windows.get(k) for k in order[order.index(key) + 1:])
                 if win is not None and not win.minimized and win.docked is None]
        for y in range(max(0, ry), min(h, ry + rh)):
            spans = [(max(0, rx), min(w, rx + rw))]
            for ax, ay, aw, ah in above:
                if ay <= y < ay + ah:
                    nxt = []
                    for a, b in spans:
                        if ax > a:
                            nxt.append((a, min(b, ax)))
                        if ax + aw < b:
                            nxt.append((max(a, ax + aw), b))
                    spans = [(a, b) for a, b in nxt if a < b]
            for a, b in spans:
                if a < b:
                    out[(y * w + a) * 2:(y * w + b) * 2] = MASK * (b - a)
    return bytes(out)


def _not_paged(edge, cells):
    return False


def _old_packing():
    from runtime import shell_layout
    return shell_layout.pack


def frames(config, surface, script, carts_dir=None):
    """The settled frame after the opening and after each step of `script`,
    on a fresh workstation of golden configuration `config`. `surface` is a
    Surface, or the function that opens one."""
    g = _goldens()
    from runtime.host_api import ConsoleDriver
    if not isinstance(surface, Surface):
        surface = Surface(surface)
    # the console's system store (settings, consent) lies beside the carts:
    # a run of its own, never a neighbour's
    carts_dir = str(carts_dir or os.path.join(tempfile.mkdtemp(prefix="uitree_parity_"), "carts"))
    if surface.prepare is not None:
        os.makedirs(carts_dir, exist_ok=True)
        surface.prepare(carts_dir)
    # a run compares a page with its old self: the desk's bar stays on the
    # old painters and the old packing here (the bar page's own pictures are
    # the shell goldens'; a commit before the bar's page has neither hook)
    from runtime import bar_layer, chrome
    paged, packing = getattr(bar_layer, "_paged", None), getattr(chrome, "_bar_pack", None)
    if paged is not None:
        bar_layer._paged = _not_paged
    if packing is not None:
        chrome._bar_pack = _old_packing
    out = []
    ws = None
    try:
        ws = g._build(g.CONFIGS[config], carts_dir)
        surface.open(ws)
        driver = ConsoleDriver(ws)
        cv = ws.glass_canvas
        probe = surface.probe or (lambda ws: "")
        out.append(Frame("(open)", _masked(ws, _settle(ws, g, driver), cv.w, cv.h),
                         cv.w, cv.h, probe(ws)))
        for step in script:
            _step(ws, driver, step)
            out.append(Frame(step, _masked(ws, _settle(ws, g, driver), cv.w, cv.h),
                             cv.w, cv.h, probe(ws)))
    finally:
        if paged is not None:
            bar_layer._paged = paged
        if packing is not None:
            chrome._bar_pack = packing
        if ws is not None:
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


def load(name):
    return json.loads((RECORDS / (name + ".json")).read_text(encoding="utf-8"))


def check_recorded(name, surface, carts_dir=None, moved=None):
    """Run the port through the recorded script; raise at the first step
    whose frame is not the recorded one. `moved` names the steps the port
    draws differently on purpose, {step index: the cause}: each must still
    differ, so an entry goes when its cause does."""
    body = load(name)
    moved = moved or {}
    fs = frames(body["config"], surface, body["script"], carts_dir)
    if moved == BEHAVIOUR:
        for i, (f, (step, want)) in enumerate(zip(fs, body["frames"])):
            if f.state != body["states"][i]:
                raise AssertionError("BEHAVIOUR %s: step %d (%r) left another state than the one "
                                     "recorded at %s (config %s)\n  recorded: %s\n  the port: %s"
                                     % (name, i, step, body["commit"], body["config"],
                                        body["states"][i], f.state))
        return fs
    for i, (f, (step, want)) in enumerate(zip(fs, body["frames"])):
        if i in moved:
            if f.digest() == want:
                raise AssertionError("PARITY %s: step %d (%r) is listed as moved (%s) and draws its "
                                     "recorded frame again: drop it from the run's moved steps"
                                     % (name, i, step, moved[i]))
            continue
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


def frames_at(commit, surface_name, config, script):
    """The frames of `script` drawn by the runtime of `commit` (a detached
    tree beside this one, with its toolchains linked), the surface opened by
    this tree's SURFACES: the old half of a port, after or before it lands."""
    with tempfile.TemporaryDirectory(prefix="uitree_old_") as old_tree:
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", old_tree, commit],
                       check=True, capture_output=True)
        sys.path.insert(0, str(ROOT / "tools"))
        import worktree                 # the old tree takes this one's toolchains and built trees
        for rel in worktree.links(str(ROOT)) + worktree.mpy_trees(str(ROOT)):
            worktree.link(str(ROOT), old_tree, rel)
        try:
            code = ("import sys, pickle; sys.path.insert(0, %r); sys.path.insert(0, %r);"
                    "import runtime;"
                    "assert runtime.__file__.startswith(%r), runtime.__file__;"
                    "import uitree_parity as P, test_uitree_ports as T;"
                    "fs = P.frames(%r, T.SURFACES[%r], %r);"
                    "import runtime as R2; assert R2.__file__.startswith(%r), R2.__file__;"
                    "sys.stdout.buffer.write(pickle.dumps([(f.step, f.buf, f.w, f.h, f.state) for f in fs]))"
                    % (str(ROOT / "tests"), old_tree, old_tree, config, surface_name, list(script), old_tree))
            env = dict(os.environ, PYTHONPATH=old_tree)
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, cwd=old_tree)
            if r.returncode:
                raise SystemExit(r.stderr.decode()[-2000:])
            import pickle
            return [Frame(*t) for t in pickle.loads(r.stdout)]
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", old_tree],
                           capture_output=True)


def _run(name):
    sys.path.insert(0, str(ROOT / "tests"))
    import test_uitree_ports
    run = next((r for r in test_uitree_ports.RUNS if r[0] == name), None)
    if run is None:
        raise SystemExit("no run %r in tests/test_uitree_ports.py RUNS" % name)
    return run


def record_at(name, commit):
    """Record run `name` of tests/test_uitree_ports.py RUNS from the old code at `commit`."""
    _n, surface_name, config, script = _run(name)[:4]
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=9", commit],
                         capture_output=True, text=True, check=True).stdout.strip()
    fs = frames_at(sha, surface_name, config, script)
    RECORDS.mkdir(parents=True, exist_ok=True)
    body = {"surface": surface_name, "config": config, "commit": sha, "script": list(script),
            "frames": [[f.step, f.digest()] for f in fs]}
    if any(f.state for f in fs):
        body["states"] = [f.state for f in fs]
    (RECORDS / (name + ".json")).write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    print("recorded %s at %s: %d frames" % (name, sha, len(fs)))
    return 0


def _show(name):
    """The old frames from the recorded commit's runtime, the new from this
    tree's, both opened by this tree's SURFACES, compared: the first
    differing step's side-by-side."""
    body = load(name)
    old = frames_at(body["commit"], body["surface"], body["config"], body["script"])
    new = frames(body["config"], _surfaces()[body["surface"]], body["script"])
    try:
        compare(old, new, OUT / name, name)
    except AssertionError as e:
        print(e)
        return 1
    print("%s: every step equal" % name)
    return 0


def _main(argv):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    import runtime  # noqa: F401  -- the flat module namespace
    import uitree_parity            # one module object for the surfaces' Surface class
    if len(argv) == 2 and argv[0] == "show":
        return uitree_parity._show(argv[1])
    if len(argv) == 3 and argv[0] == "record":
        return uitree_parity.record_at(argv[1], argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
