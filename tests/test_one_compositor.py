"""The canvas leftovers' guard (docs/refactor_2026-11.md, phase 14).

One class is the one-buffer compositor: `runtime/toolkit/one_buffer.py`'s
`OneBufferCompositor`, the browser's and the host's, its raster module handed
in. The host's subclasses add a test's ring of buffers and the desk's variant
and define none of the verbs.

One C body composites a game canvas onto a P4's glass
(`native/moy_glass/moy_present_game.c`): the console's canvas, the rotated
compositor's paint route and the kernel's front all call it, and
`device/p4_canvas.py` holds no branch on crisp or smooth around a blit.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERBS = ("size", "framebuffer", "back_buffer", "gfx", "copy_last", "shift_last",
         "copy_last_into")


def _classes_defining(path, names):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            defs = {f.name for f in node.body if isinstance(f, ast.FunctionDef)}
            if defs & set(names):
                out.append((node.name, sorted(defs & set(names))))
    return out


def test_one_class_defines_the_one_buffer_compositors_verbs():
    from runtime.one_buffer import OneBufferCompositor
    for v in VERBS:
        assert v in OneBufferCompositor.__dict__, v
    found = []
    for base in (ROOT / "runtime", ROOT / "firmware" / "web_runner"):
        for path in sorted(base.rglob("*.py")):
            if ".build" in path.parts:
                continue
            for name, verbs in _classes_defining(path, ("back_buffer",)):
                found.append((path.relative_to(ROOT).as_posix(), name, verbs))
    assert found == [("runtime/toolkit/one_buffer.py", "OneBufferCompositor",
                      ["back_buffer"])], found


def test_the_hosts_compositors_are_it():
    from runtime import host_canvas
    from runtime.one_buffer import OneBufferCompositor
    for cls in (host_canvas.HostCompositor, host_canvas.DeskCompositor):
        assert issubclass(cls, OneBufferCompositor)
        assert not set(VERBS) & set(cls.__dict__), cls
    cv = host_canvas.make_system_canvas(16, 8)
    assert isinstance(cv._comp, OneBufferCompositor)


def test_the_webs_compositor_is_it():
    import importlib
    from runtime import host_canvas
    from runtime.one_buffer import OneBufferCompositor
    host_canvas.install()
    runner = str(ROOT / "firmware" / "web_runner")
    sys.path.append(runner)
    try:
        web_canvas = importlib.import_module("web_canvas")
    finally:
        sys.path.remove(runner)
    for comp in (web_canvas.make_canvas(16, 8)._comp, web_canvas.compositor(4, 4)):
        assert type(comp).__name__ == OneBufferCompositor.__name__
        assert type(comp).__module__.rsplit(".", 1)[-1] == "one_buffer"
    src = (ROOT / "firmware" / "web_runner" / "web_canvas.py").read_text(encoding="utf-8")
    assert "Compositor:" not in src, "the browser defines no compositor class"


def test_the_p4_canvas_holds_no_branch_on_crisp_or_smooth():
    src = (ROOT / "device" / "p4_canvas.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    def names(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                yield n.id
            elif isinstance(n, ast.Attribute):
                yield n.attr

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            hit = [n for n in names(node.test)
                   if "smooth" in n.lower() or "crisp" in n.lower()]
            assert not hit, "p4_canvas.py:%d branches on %s" % (node.lineno, hit)
    assert "blit_crisp" not in src, "the crisp composite is moy_glass's"


def test_one_c_body_composites_the_game_onto_a_p4():
    glass = ROOT / "native" / "moy_glass"
    body = (glass / "moy_present_game.c").read_text(encoding="utf-8")
    assert "moy_ppa_k_crisp(" in body and "moy_ppa_k_scale(" in body
    for name in ("modmoy_glass.c", "modmoy_glass_rot.c", "modmoy_glass_dsi.c"):
        src = (glass / name).read_text(encoding="utf-8")
        assert "moy_ppa_k_scale(" not in src.replace("moy_ppa_k_scale(uint16_t", ""), name
        assert "moy_ppa_k_crisp(" not in src, name
    front = (glass / "modmoy_glass.c").read_text(encoding="utf-8")
    rot = (glass / "modmoy_glass_rot.c").read_text(encoding="utf-8")
    assert "glass_game(" in front and "glass_game(" in rot


# -- the game composite, executed ------------------------------------------------
#
# Neither the host nor the desktop MicroPython has a PPA, so both take the CPU
# kernel's nearest scale whatever the mode: the composite is held to
# moy_gfx.blit565_scale byte for byte, through the host's binding, the boards'
# C binding and the rotated compositor's paint route.

GAME_SCRIPT = r'''
def check(glass, gfx):
    sw, sh, dw, dh = 8, 6, 40, 30
    src = bytearray(sw * sh * 2)
    for i in range(len(src)):
        src[i] = (i * 37 + 11) & 255
    out = []
    for crisp in (False, True):
        glass.game_crisp(crisp)
        for dx, dy, scale, defer in ((3, 2, 3, False), (0, 0, 5, True),
                                     (-5, 20, 3, False), (30, 1, 2, True)):
            a = bytearray(dw * dh * 2)
            b = bytearray(dw * dh * 2)
            r = glass.game_composite(a, dw, dh, dx, dy, src, sw, sh, scale, defer)
            gfx.blit565_scale(b, dw, dh, dx, dy, src, sw, sh, scale)
            out.append((crisp, dx, dy, scale, r, a == b))
    glass.game_crisp(False)
    return out
'''


def _expect_game(rows):
    assert len(rows) == 8
    for crisp, dx, dy, scale, r, same in rows:
        assert r == 0 and same, (crisp, dx, dy, scale, r)


def test_the_game_composite_is_the_cpu_kernels_scale_on_the_host():
    from runtime import gfx_binding, glass_binding
    env = {}
    exec(GAME_SCRIPT, env)                           # noqa: S102 -- fixed text
    _expect_game(env["check"](glass_binding, gfx_binding))


def test_the_game_composite_runs_in_the_boards_c_binding(tmp_path):
    import subprocess
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from unix_mp import require_unix_mp
    exe = require_unix_mp("moy_glass", "moy_gfx",
                          why="the boards' game composite binding (modmoy_glass.c)")
    script = tmp_path / "game.py"
    script.write_text(GAME_SCRIPT + "\nimport moy_glass, moy_gfx\n"
                      "for r in check(moy_glass, moy_gfx):\n    print(repr(r))\n")
    proc = subprocess.run([exe, "-X", "heapsize=16M", str(script)], capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    _expect_game([eval(line) for line in proc.stdout.splitlines()])   # noqa: S307


def test_the_rotated_paint_route_composites_the_game_canvas_itself():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from runtime import gfx_binding
    from test_p4_display import rotated
    sw, sh = 32, 24
    game = bytearray((i * 29 + 3) & 255 for i in range(sw * sh * 2))
    with rotated() as (mod, comp, dsi, ppa, lit):
        paint = comp.framebuffer()
        before = bytes(paint)
        comp.mark_game(game, sw, sh, 10, 8, 2, None, lambda: False, None)
        assert comp._r.game.direct is False, "no PPA: never a direct scale"
        comp.flush()
        want = bytearray(before)
        gfx_binding.blit565_scale(want, comp._w, comp._h, 10, 8, game, sw, sh, 2)
        assert bytes(paint) == bytes(want)
