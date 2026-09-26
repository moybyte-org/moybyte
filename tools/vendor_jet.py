#!/usr/bin/env python3
"""Re-vendor Jet, and the teapot scene's model and notices, into ports/jet.

    make vendor-jet                                   # the clones at the pins below
    make vendor-jet EXAMPLES_COMMIT=<sha>             # another JetExamples commit
    python3 tools/vendor_jet.py --check               # what would change

The showcase cart (ports/jet/README.md) compiles Jet -- CubeCoders' software
rasteriser, https://github.com/CubeCoders/Jet, MIT -- into a compiled cart,
and ports one of Jet's own example scenes, JetExamples' Utah teapot
(https://github.com/CubeCoders/JetExamples, MIT). ONE pin decides both: the
JetExamples commit. Jet's commit is the one that commit carries as its
`components/Jet` submodule, so the engine is the one the example was written
and measured against, and the two cannot drift apart.

What crosses:

  * from Jet: the sources the cart compiles and the headers they include
    (FILES), and the licence, into ports/jet/jet/ under upstream's paths;
  * from JetExamples, DERIVED rather than copied, into the cart's source
    folder ports/jet/teapot.moy/:
      - teapot.obj: the example's generated mesh (esp32-lighting-teapot/main/
        TeapotMesh.hpp -- 822 vertices with smooth normals, 1,560 triangles)
        written as an OBJ the cart reads through `read` and Jet's own loader.
        Positions are the example's integers over 128 and normals over 1024
        -- the scales Jet's loader multiplies back by -- so every value
        survives the round trip exactly;
      - LICENSES.txt: Jet's licence, which JetExamples carries word for word,
        and the permission notice of the freeglut teapot data the mesh was
        generated from (esp32-lighting-teapot/assets/fg_teapot_data.h).

Files are read from each clone's GIT OBJECTS at the pinned commit, never its
working tree (tools/vendor_wamr.py's rule). Why vendor instead of fetching in
the build: tools/vendor_libmoy.py's answer -- a build that fetches is a build
that needs the network.

The stamp is ports/jet/jet_vendor.json (both commits and a sha256 per file);
tests/test_jet_vendor.py holds the copy to it, and re-derives everything from
the clones when they are at hand.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = os.path.join(ROOT, "ports", "jet")
DEST = os.path.join(PORT, "jet")
CART = os.path.join(PORT, "teapot.moy")
MANIFEST = os.path.join(PORT, "jet_vendor.json")
CLONES = os.path.join(ROOT, ".build", "jet")

JET_REPO = "CubeCoders/Jet"
EXAMPLES_REPO = "CubeCoders/JetExamples"
EXAMPLES_COMMIT = "57b05a240f5bcaa02a18d62a1206ce7053dc09dc"
SUBMODULE = "components/Jet"

# What the cart compiles, and every header those files and the cart's own
# sources include: the linker needs these translation units and no others
# (Texture.cpp, Primitives.cpp, the particles, sprites' helpers and the rest
# of the engine stay behind).
FILES = [
    "LICENSE",
    "src/BlendSpans.cpp",
    "src/BlendSpans.hpp",
    "src/Camera.cpp",
    "src/Camera.hpp",
    "src/DepthBuckets.hpp",
    "src/FastMath.hpp",
    "src/Light.cpp",
    "src/Light.hpp",
    "src/Material.cpp",
    "src/Material.hpp",
    "src/ObjLoader.h",
    "src/Object.cpp",
    "src/Object.hpp",
    "src/Picking.hpp",
    "src/PostFX.cpp",
    "src/PostFX.hpp",
    "src/Renderer.cpp",
    "src/Renderer.hpp",
    "src/Scene.cpp",
    "src/Scene.hpp",
    "src/Shader.hpp",
    "src/Specular.hpp",
    "src/Sprite2D.cpp",
    "src/Sprite2D.hpp",
    "src/Texture.hpp",
    "src/TextureSpans.hpp",
    "src/TriangleSpans.hpp",
    "src/TrigLUT.cpp",
    "src/TrigLUT.hpp",
]

TEAPOT_MESH = "esp32-lighting-teapot/main/TeapotMesh.hpp"
TEAPOT_DATA = "esp32-lighting-teapot/assets/fg_teapot_data.h"
EXAMPLES_LICENSE = "LICENSE"

# The scales Jet's OBJ loader (ObjLoader.h) multiplies by: positions by
# FIXED_POINT_SCALE / 8, normals by FIXED_POINT_SCALE.
POSITION_SCALE = 128
NORMAL_SCALE = 1024


class VendorError(RuntimeError):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def git(clone, *args, binary=False):
    out = subprocess.check_output(("git", "-C", clone) + args,
                                  stderr=subprocess.DEVNULL)
    return out if binary else out.decode().strip()


def resolve(clone, commit):
    """(full commit, commit date) in the clone."""
    try:
        full = git(clone, "rev-parse", "--verify", commit + "^{commit}")
    except subprocess.CalledProcessError:
        raise VendorError("%s is not a commit in %s" % (commit, clone))
    return full, git(clone, "log", "-1", "--format=%cs", full)


def blob(clone, commit, rel):
    try:
        return git(clone, "show", "%s:%s" % (commit, rel), binary=True)
    except subprocess.CalledProcessError:
        raise VendorError("%s is not in %s at %s" % (rel, clone, commit[:12]))


def pinned_jet(examples_clone, examples_commit):
    """The Jet commit JetExamples carries as its submodule at that commit."""
    line = git(examples_clone, "ls-tree", examples_commit, SUBMODULE)
    m = re.match(r"160000 commit ([0-9a-f]{40})\t", line)
    if not m:
        raise VendorError("%s at %s has no %s submodule"
                          % (EXAMPLES_REPO, examples_commit[:12], SUBMODULE))
    return m.group(1)


# -- what is derived ------------------------------------------------------------


def _decimal(n, scale):
    """n / scale written exactly: a power-of-two denominator always ends."""
    text = repr(n / scale)
    return text[:-2] if text.endswith(".0") else text


def teapot_obj(mesh_hpp, source_commit):
    """The OBJ text for the example's generated TeapotMesh.hpp."""
    text = mesh_hpp.decode()
    verts = re.search(r"vertices\[\]\[6\] = \{(.*?)\n\};", text, re.S)
    tris = re.search(r"triangles\[\]\[3\] = \{(.*?)\n\};", text, re.S)
    if not verts or not tris:
        raise VendorError("%s no longer has the vertices/triangles tables" % TEAPOT_MESH)
    vertices = [tuple(int(v) for v in row.split(","))
                for row in re.findall(r"\{([-\d,]+)\}", verts.group(1))]
    triangles = [tuple(int(v) for v in row.split(","))
                 for row in re.findall(r"\{([\d,]+)\}", tris.group(1))]
    if not vertices or any(len(v) != 6 for v in vertices) \
            or not triangles or any(len(t) != 3 for t in triangles):
        raise VendorError("%s: unexpected table shapes" % TEAPOT_MESH)
    lines = [
        "# The Utah teapot as JetExamples' esp32-lighting-teapot renders it: its",
        "# %s at %s," % (TEAPOT_MESH, source_commit[:12]),
        "# written as OBJ by tools/vendor_jet.py. %d vertices, %d triangles."
        % (len(vertices), len(triangles)),
        "# Generated from freeglut's teapot control points; LICENSES.txt carries",
        "# their notice. Do not edit: re-run `make vendor-jet`.",
    ]
    lines += ["v %s %s %s" % tuple(_decimal(c, POSITION_SCALE) for c in v[:3])
              for v in vertices]
    lines.append("vt 0 0")
    lines += ["vn %s %s %s" % tuple(_decimal(c, NORMAL_SCALE) for c in v[3:])
              for v in vertices]
    lines += ["f %d/1/%d %d/1/%d %d/1/%d" % (a + 1, a + 1, b + 1, b + 1, c + 1, c + 1)
              for a, b, c in triangles]
    return ("\n".join(lines) + "\n").encode()


def freeglut_notice(data_h):
    """The permission notice at the head of fg_teapot_data.h, uncommented."""
    text = data_h.decode("latin-1")
    end = text.find("DEALINGS IN THE SOFTWARE.")
    if not text.startswith("/*") or end < 0:
        raise VendorError("%s no longer opens with its permission notice" % TEAPOT_DATA)
    out = []
    for line in text[2:end + len("DEALINGS IN THE SOFTWARE.")].splitlines():
        line = line.strip()
        if line.startswith("*"):
            line = line[1:]
        out.append(line[1:] if line.startswith(" ") else line)
    return "\n".join(out).strip("\n") + "\n"


def licenses_txt(jet_license, examples_license, data_h, jet_commit, examples_commit):
    if jet_license != examples_license:
        raise VendorError("JetExamples' LICENSE is no longer Jet's; LICENSES.txt "
                          "must carry both")
    return ("This cart compiles Jet, a software 3D rasteriser, and ports a scene\n"
            "from Jet's examples. Both are CubeCoders Limited's, under the licence\n"
            "below:\n\n"
            "  https://github.com/%s at %s\n"
            "  https://github.com/%s at %s\n\n"
            "%s\n"
            "----------------------------------------------------------------------\n\n"
            "teapot.obj is the Utah teapot generated from the teapot data of the\n"
            "freeglut library (%s in JetExamples), which\n"
            "carries this notice:\n\n%s"
            % (JET_REPO, jet_commit, EXAMPLES_REPO, examples_commit,
               jet_license.decode(), TEAPOT_DATA, freeglut_notice(data_h))).encode()


def derive(read_jet, read_examples, jet_commit, examples_commit):
    """{repo-relative path: bytes} of everything the vendor writes, from two
    readers of upstream paths at the pinned commits."""
    out = {}
    for rel in FILES:
        out[_rel(os.path.join(DEST, rel))] = read_jet(rel)
    out[_rel(os.path.join(CART, "teapot.obj"))] = teapot_obj(
        read_examples(TEAPOT_MESH), examples_commit)
    out[_rel(os.path.join(CART, "LICENSES.txt"))] = licenses_txt(
        read_jet("LICENSE"), read_examples(EXAMPLES_LICENSE),
        read_examples(TEAPOT_DATA), jet_commit, examples_commit)
    return out


def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def vendored_paths():
    """Every file this script owns, repo-relative."""
    return sorted([_rel(os.path.join(DEST, rel)) for rel in FILES]
                  + [_rel(os.path.join(CART, "teapot.obj")),
                     _rel(os.path.join(CART, "LICENSES.txt"))])


def from_clones(jet_clone, examples_clone, examples_commit=EXAMPLES_COMMIT):
    """(files, upstream) read from the two clones at the pin."""
    examples_commit, examples_date = resolve(examples_clone, examples_commit)
    jet_commit, jet_date = resolve(jet_clone, pinned_jet(examples_clone, examples_commit))
    files = derive(lambda rel: blob(jet_clone, jet_commit, rel),
                   lambda rel: blob(examples_clone, examples_commit, rel),
                   jet_commit, examples_commit)
    upstream = {"repo": JET_REPO, "commit": jet_commit, "date": jet_date,
                "examples": {"repo": EXAMPLES_REPO, "commit": examples_commit,
                             "date": examples_date}}
    return files, upstream


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jet", default=os.path.join(CLONES, "Jet"),
                    help="a clone of %s (default: .build/jet/Jet)" % JET_REPO)
    ap.add_argument("--examples", default=os.path.join(CLONES, "JetExamples"),
                    help="a clone of %s (default: .build/jet/JetExamples)" % EXAMPLES_REPO)
    ap.add_argument("--examples-commit", default=EXAMPLES_COMMIT,
                    help="the JetExamples commit to vendor from (default: the pin)")
    ap.add_argument("--check", action="store_true",
                    help="report what would change; write nothing")
    args = ap.parse_args(argv)

    for path, repo in ((args.jet, JET_REPO), (args.examples, EXAMPLES_REPO)):
        if not os.path.isdir(os.path.join(path, ".git")):
            print("vendor-jet: no clone at %s\n  git clone https://github.com/%s.git %s"
                  % (path, repo, path), file=sys.stderr)
            return 2
    try:
        files, upstream = from_clones(args.jet, args.examples, args.examples_commit)
    except VendorError as exc:
        print("vendor-jet: %s" % exc, file=sys.stderr)
        return 2
    print("vendor-jet: JetExamples @ %s, Jet @ %s (its %s)"
          % (upstream["examples"]["commit"][:12], upstream["commit"][:12], SUBMODULE))

    changed = []
    for rel, data in sorted(files.items()):
        dst = os.path.join(ROOT, rel)
        if os.path.isfile(dst) and sha256_file(dst) == sha256_bytes(data):
            continue
        changed.append(rel)
        if not args.check:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)

    # Nothing under jet/ is authored here, so anything not in the table goes.
    for dirpath, _dirs, names in os.walk(DEST):
        for name in names:
            rel = _rel(os.path.join(dirpath, name))
            if rel not in files:
                changed.append(rel + " (removed)")
                if not args.check:
                    os.remove(os.path.join(ROOT, rel))

    for rel in changed:
        print("  %s %s" % ("would update" if args.check else "updated", rel))
    if not changed:
        print("  already up to date")
    if args.check:
        return 1 if changed else 0

    stamp = {"upstream": upstream,
             "files": {rel: sha256_bytes(data) for rel, data in files.items()}}
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(stamp, f, indent=2, sort_keys=True)
        f.write("\n")
    print("  stamped %s" % _rel(MANIFEST))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
