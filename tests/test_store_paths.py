"""Only the store composes a cart's path (docs/kernel_store_2026-10.md
section 4).

A cart's path is its store root and its folder, and `moy_store_base` is the
one place they are put together: `cart_path(root, folder)`, the folder a name
is stored in (`cart_folder`), and a store-relative path as the sync wire and
the webhost name one (`store_path`). The native store keys its index by (root,
folder) and a sharded store (#235) moves a cart below its root; a caller that
built `root + "/" + folder` itself would then name a folder that is not there.

This reads every module the console runs -- `runtime/`, `device/`, each
board's tracked `modules/` and the browser's -- and fails any of them, the
composer's own module aside, that:

  * appends a path segment to a store root (`root + "/" + x`, `os.path.join(
    root, x)`), where the root is a name the store's roots go by and `x` is not
    a fixed file name (an UPPER_CASE constant or a literal);
  * puts the cart extension on a name to make a folder of it (`slug(t) +
    ".moy"`, `x + "/" + y + ".moy"`, `"%s/%s.moy" % ...`).

A caller that holds a cart's path (`cart["path"]`, `moy_catalogue.path(h)`)
and names a file inside it is not composing one, and is not flagged.
"""

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COMPOSER = "runtime/moy_store_base.py"

# The names a store root goes by in the console's code.
ROOT_NAMES = {"root", "carts_root", "carts_dir", "CARTS_DIR"}

EXT = ".moy"


def _sources():
    out = subprocess.run(
        ["git", "ls-files", "runtime/*.py", "device/*.py", "device/**/*.py",
         "firmware/*/modules/*.py", "firmware/web_runner/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return sorted(set(out) - {COMPOSER})


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _terms(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _terms(node.left) + _terms(node.right)
    return [node]


def _text(node):
    return node.value if (isinstance(node, ast.Constant)
                          and isinstance(node.value, str)) else None


def _fixed(node):
    """A fixed file name: a literal, or a module constant in UPPER_CASE."""
    name = _name(node)
    return _text(node) is not None or (
        name is not None and name.strip("_").isupper())


def _composes(node):
    """Why `node` composes a cart path, or None."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        terms = _terms(node)
        texts = [_text(t) for t in terms]
        if (_name(terms[0]) in ROOT_NAMES and len(terms) > 2
                and texts[1] is not None and texts[1].startswith("/")
                and not _fixed(terms[2])):
            return "a segment appended to a store root"
        if any(t is not None and t.endswith(EXT) for t in texts[1:]):
            first = terms[0]
            if (any(t is not None and "/" in t for t in texts)
                    or (isinstance(first, ast.Call)
                        and _name(first.func) == "slug")):
                return "a folder named by hand"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        fmt = _text(node.left)
        if fmt is not None and "/" in fmt and EXT in fmt:
            return "a folder named by hand"
    if (isinstance(node, ast.Call) and _name(node.func) == "join"
            and isinstance(node.func, ast.Attribute)
            and _name(node.func.value) == "path" and node.args
            and _name(node.args[0]) in ROOT_NAMES):
        return "a segment joined to a store root"
    return None


def _violations(path, text):
    out = []
    seen = set()
    for node in ast.walk(ast.parse(text, path)):
        why = _composes(node)
        if why and node.lineno not in seen:
            seen.add(node.lineno)
            out.append("%s:%d: %s: %s" % (path, node.lineno, why,
                                          ast.get_source_segment(text, node)))
    return out


def test_only_the_store_composes_a_cart_path():
    found = []
    for rel in _sources():
        found += _violations(rel, (ROOT / rel).read_text())
    assert not found, ("compose a cart's path through moy_store_base "
                       "(cart_path, cart_folder, store_path):\n  "
                       + "\n  ".join(found))


def test_the_check_sees_each_shape():
    """Each shape the check names, and the ones it leaves alone."""
    caught = [
        'p = root + "/" + folder',
        'p = self.carts_root + "/" + top + "/manifest.json"',
        'p = os.path.join(carts_dir, name)',
        'p = moy_carts.slug(t) + ".moy"',
        'p = base + "/" + slug(t) + ".moy"',
        'p = "%s/%s.moy" % (root, name)',
    ]
    left = [
        'p = cart["path"] + "/" + name',
        'p = root + "/" + _PUBLISH',
        'p = root + "/" + "manifest.json"',
        'ok = folder == want + ".moy"',
        'p = cart_path(root, cart_folder(t))',
    ]
    for src in caught:
        assert _violations("x.py", src), src
    for src in left:
        assert not _violations("x.py", src), src
