#!/usr/bin/env python3
"""Before, after and a marked diff of every pixel golden that moved.

    tools/golden_diff.py REF [--out DIR]

A hash-pinned pixel test that fails writes each moved frame as a PNG under
`.build/goldens/<test file>/<test>/` (`tests/pixel_goldens.py`): that is the
"after". This renders the same tests at REF (a commit, a branch, `HEAD` for
uncommitted work) in a temporary worktree, which is the "before":

  * a shell goldens configuration through that tree's `tools/golden_png.py`,
    which wraps the test's `_render`, so any REF that has the tool has the
    picture;
  * any other pixel test by running it there under `MOYBYTE_GOLDEN_DUMP`,
    which writes every frame it checks, for a REF that has
    `tests/pixel_goldens.py`.

It writes `<out>/<test>/<frame>.{before,after,diff}.png` and `<out>/index.html`
(default out `.build/golden_diff`). The diff is the after picture faded, with
every changed pixel in magenta and the changed area boxed. A frame with no
before (a new surface, a REF without the tool) is shown alone and said so.
"""

import argparse
import html
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import pixel_goldens  # noqa: E402

SHELL_FILE = "test_shell_goldens"
SHELL_TEST = "test_shell_surfaces_match_goldens["
MARK = (255, 0, 255)


def moved_tests(after_root):
    """[(node id, folder)] of every test that left moved frames."""
    out = []
    for node_file in sorted(after_root.glob("*/*/" + pixel_goldens.NODE_FILE)):
        node = node_file.read_text(encoding="utf-8").strip()
        out.append((node, node_file.parent))
    return out


def _git(*args, cwd=ROOT):
    return subprocess.run(["git"] + list(args), cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _link_caches(tree):
    """The host build caches are keyed by their inputs, so the temporary tree
    shares this one's as a worktree does (tools/worktree.py); the MicroPython
    tree is read for its inflater's sources only."""
    import worktree
    for rel in [os.path.join(".build", c) for c in worktree.SHARED_CACHES] + [
            os.path.join(".build", "unix_micropython", "micropython")]:
        src = ROOT / rel
        if src.is_dir():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.path.realpath(src), tree / rel)


def render_before(tree, tests, before):
    """Render each test at `tree` into `before/<test folder>/`; returns
    {folder: note} for the tests that could not be rendered there."""
    notes = {}
    py = sys.executable
    for node, folder in tests:
        rel = folder.relative_to(folder.parent.parent)
        dst = before / rel
        dst.mkdir(parents=True, exist_ok=True)
        if folder.parent.name == SHELL_FILE and rel.name.startswith(SHELL_TEST):
            config = rel.name[len(SHELL_TEST):-1]
            tool = tree / "tools" / "golden_png.py"
            if not tool.is_file():
                notes[rel] = "REF has no tools/golden_png.py"
                continue
            cmd = [py, str(tool), config, "--out", str(dst)]
            env = dict(os.environ)
        else:
            if not (tree / "tests" / "pixel_goldens.py").is_file():
                notes[rel] = "REF has no tests/pixel_goldens.py to dump the frames"
                continue
            cmd = [py, "-m", "pytest", "-q", "-p", "no:xdist", "-p",
                   "no:cacheprovider", node]
            env = dict(os.environ, **{pixel_goldens.DUMP_ENV: str(before)})
        print("before: %s" % node, flush=True)
        r = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True)
        if not any(dst.glob("*.png")):
            notes[rel] = "nothing rendered at REF:\n" + (r.stdout + r.stderr)[-1500:]
    return notes


def _load(path):
    from PIL import Image
    return Image.open(path).convert("RGB") if path and path.is_file() else None


def marked_diff(before, after):
    """(diff image, changed pixel count, bbox or None)."""
    from PIL import Image, ImageChops, ImageDraw
    if before is None or before.size != after.size:
        return None, None, None
    delta = ImageChops.difference(before, after).convert("L").point(
        lambda v: 255 if v else 0)
    bbox = delta.getbbox()
    changed = delta.histogram()[255]
    faded = Image.blend(after, Image.new("RGB", after.size, (255, 255, 255)), 0.7)
    faded.paste(Image.new("RGB", after.size, MARK), mask=delta)
    if bbox:
        x0, y0, x1, y1 = bbox
        ImageDraw.Draw(faded).rectangle(
            (max(0, x0 - 2), max(0, y0 - 2), min(after.width - 1, x1 + 1),
             min(after.height - 1, y1 + 1)), outline=MARK)
    return faded, changed, bbox


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Moved goldens</title>
<style>
:root { --bg: #fff; --ink: #1d1d1f; --dim: #6e6e73; --line: #d2d2d7; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161617; --ink: #f5f5f7; --dim: #a1a1a6; --line: #3a3a3c; } }
body { background: var(--bg); color: var(--ink); margin: 16px;
       font: 14px/1.4 system-ui, sans-serif; }
h1 { font-size: 20px; } h2 { font-size: 15px; margin: 28px 0 4px; }
.meta { color: var(--dim); } pre { white-space: pre-wrap; color: var(--dim); }
.row { display: flex; flex-wrap: wrap; gap: 12px; }
figure { margin: 0; } figcaption { color: var(--dim); font-size: 12px; }
img { max-width: 100%%; image-rendering: pixelated; border: 1px solid var(--line); }
</style></head><body>
<h1>Moved goldens: %(ref)s &rarr; this tree</h1>
<p class="meta">%(count)d frame(s) in %(tests)d test(s). Magenta: every pixel that differs.</p>
%(body)s
</body></html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ref", help="the commit the goldens held at (HEAD for uncommitted work)")
    ap.add_argument("--out", default=str(ROOT / ".build" / "golden_diff"))
    ap.add_argument("--after", default=str(pixel_goldens.OUT),
                    help="the moved frames (default .build/goldens)")
    a = ap.parse_args(argv)
    after_root, out = Path(a.after), Path(a.out)
    tests = moved_tests(after_root)
    if not tests:
        print("no moved frames under %s: run the failing pixel test first" % after_root,
              file=sys.stderr)
        return 1
    sha = _git("rev-parse", "--verify", a.ref + "^{commit}")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix="golden-diff-"))
    tree = tmp / "ref"
    before = tmp / "before"
    _git("worktree", "add", "-q", "--detach", str(tree), sha)
    try:
        _link_caches(tree)
        notes = render_before(tree, tests, before)
    finally:
        _git("worktree", "remove", "--force", str(tree))
    body, count = [], 0
    for node, folder in tests:
        rel = folder.relative_to(folder.parent.parent)
        body.append("<h2>%s</h2>" % html.escape(node))
        if rel in notes:
            body.append("<pre>%s</pre>" % html.escape(notes[rel]))
        for png in sorted(folder.glob("*.png")):
            count += 1
            stem = png.stem
            dst = out / rel
            dst.mkdir(parents=True, exist_ok=True)
            aft = _load(png)
            bef = _load(before / rel / png.name)
            aft.save(dst / (stem + ".after.png"))
            figs = []
            if bef is not None:
                bef.save(dst / (stem + ".before.png"))
                figs.append(("before", stem + ".before.png"))
            figs.append(("after", stem + ".after.png"))
            diff, changed, bbox = marked_diff(bef, aft)
            if diff is not None:
                diff.save(dst / (stem + ".diff.png"))
                figs.append(("diff", stem + ".diff.png"))
                what = ("%d pixel(s) changed in %s" % (changed, bbox)) if bbox \
                    else "no pixel changed against REF"
            elif bef is None:
                what = "no before picture at %s" % a.ref
            else:
                what = "size changed: %dx%d -> %dx%d" % (bef.size + aft.size)
            body.append("<p><b>%s</b> <span class=meta>%s</span></p><div class=row>%s</div>"
                        % (html.escape(stem), html.escape(what), "".join(
                            '<figure><img src="%s/%s"><figcaption>%s</figcaption></figure>'
                            % (html.escape(rel.as_posix()), html.escape(f), k)
                            for k, f in figs)))
    shutil.rmtree(tmp, ignore_errors=True)
    index = out / "index.html"
    index.write_text(PAGE % dict(ref=html.escape("%s (%s)" % (a.ref, sha[:9])),
                                 count=count, tests=len(tests), body="\n".join(body)),
                     encoding="utf-8")
    print(index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
