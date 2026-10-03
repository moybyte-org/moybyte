#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Re-vendor Jet and ESP 88's film code into ports/jet; derive the Jet carts' data.

    make vendor-jet                                   # the clones at the pins below
    make vendor-jet EXAMPLES_COMMIT=<sha>             # another JetExamples commit
    python3 tools/vendor_jet.py --check               # what would change

The Jet carts (ports/jet/README.md) compile Jet -- CubeCoders' software
rasteriser, https://github.com/CubeCoders/Jet, MIT -- into compiled carts,
each porting one of Jet's own examples from JetExamples
(https://github.com/CubeCoders/JetExamples, MIT): the Utah teapot, and ESP 88,
the neon city film. ONE pin decides all of it: the JetExamples commit. Jet's
commit is the one that commit carries as its `components/Jet` submodule, so
the engine is the one the examples were written and measured against, and the
two cannot drift apart.

What crosses into ports/jet:

  * from Jet: the sources the cart compiles and the headers they include
    (FILES), and the licence, into ports/jet/jet/ under upstream's paths;
  * from JetExamples' esp32-neon-film, the film's own code into
    ports/jet/examples/ under upstream's paths (FILM_FILES), World.hpp with
    the one change PATCHES records.

The carts themselves are moybyte-org/carts', and ports/jet/<cart>.moy/ is
tools/vendor_jet_carts.py's stamped copy of them. Their data is still DERIVED
here from JetExamples, which is how it was made and how the tests prove it is
still upstream's: `--carts DIR` writes it into a carts checkout's carts/,
and tests/test_jet_vendor.py re-derives it and compares. The data:

  * for the teapot (carts/teapot/):
      - teapot.obj: the example's generated mesh (esp32-lighting-teapot/main/
        TeapotMesh.hpp -- 822 vertices with smooth normals, 1,560 triangles)
        written as an OBJ the cart reads through `read` and Jet's own loader.
        Positions are the example's integers over 128 and normals over 1024
        -- the scales Jet's loader multiplies back by -- so every value
        survives the round trip exactly;
      - LICENSES.txt: Jet's licence, which JetExamples carries word for word,
        and the permission notice of the freeglut teapot data the mesh was
        generated from (esp32-lighting-teapot/assets/fg_teapot_data.h);
  * for ESP 88 (carts/esp88/):
      - assets.bin: the film's artwork as the cart reads it through `read`
        into zeroed arrays, rather than as initialised data compiled into the
        module (see ASSETS);
      - LICENSES.txt: the same licence, and where the film and its artwork
        come from.

Files are read from each clone's GIT OBJECTS at the pinned commit, never its
working tree (tools/vendor_wamr.py's rule). Why vendor instead of fetching in
the build: tools/vendor_libmoy.py's answer -- a build that fetches is a build
that needs the network.

The stamp is ports/jet/jet_vendor.json (both commits and a sha256 per file it
writes); tests/test_jet_vendor.py holds the copy to it, and re-derives
everything -- the carts' data included -- from the clones when they are at
hand.
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
FILM_CART = os.path.join(PORT, "esp88.moy")
EXAMPLES_DEST = os.path.join(PORT, "examples")
MANIFEST = os.path.join(PORT, "jet_vendor.json")
CLONES = os.path.join(ROOT, ".build", "jet")

JET_REPO = "CubeCoders/Jet"
EXAMPLES_REPO = "CubeCoders/JetExamples"
EXAMPLES_COMMIT = "57b05a240f5bcaa02a18d62a1206ce7053dc09dc"
SUBMODULE = "components/Jet"

# What the carts compile, and every header those files and the carts' own
# sources include: the linkers need these translation units and no others
# (the lens flare, the examples' samples and the rest of the engine stay
# behind).
FILES = [
    "LICENSE",
    "src/BlendSpans.cpp",
    "src/BlendSpans.hpp",
    "src/Camera.cpp",
    "src/Camera.hpp",
    "src/DepthBuckets.hpp",
    "src/EnvironmentMapping.hpp",
    "src/FastMath.hpp",
    "src/Light.cpp",
    "src/Light.hpp",
    "src/Material.cpp",
    "src/Material.hpp",
    "src/Math.hpp",
    "src/ObjLoader.h",
    "src/Object.cpp",
    "src/Object.hpp",
    "src/ParticleSystem.hpp",
    "src/Picking.hpp",
    "src/PostFX.cpp",
    "src/PostFX.hpp",
    "src/Primitives.cpp",
    "src/Primitives.hpp",
    "src/Renderer.cpp",
    "src/Renderer.hpp",
    "src/Scene.cpp",
    "src/Scene.hpp",
    "src/Shader.hpp",
    "src/Specular.hpp",
    "src/Sprite2D.cpp",
    "src/Sprite2D.hpp",
    "src/Texture.cpp",
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

# ESP 88: the film's code, copied under upstream's paths. Its two generated
# data headers stay behind (Assets.hpp, CreditMask.hpp): assets.bin carries
# what the cart needs of them, and the cart's own headers of the same names
# declare the arrays that file fills.
FILM_DIR = "esp32-neon-film/main"
FILM_FILES = ["City.hpp", "Film.hpp", "RoadTrack.hpp", "Vehicle.hpp", "World.hpp",
              "firmware/JetConfig.hpp"]
FILM_ASSETS = FILM_DIR + "/Assets.hpp"
FILM_CREDITS = FILM_DIR + "/CreditMask.hpp"

# The one change to the film's code. The film fixes its frame at 480 x 320
# times an integer scale; the cart renders it at two thirds of that, the
# console's 320-wide frame, so the size becomes a default the build can
# override. Every screen-space figure the film computes from the size follows
# it; the few written in 480-wide pixels are the cart's to adapt (the glow and
# the credits, below, and main.cpp).
PATCHES = {
    FILM_DIR + "/World.hpp": [(
        "inline constexpr int renderWidth=480*renderScale,renderHeight=320*renderScale;\n",
        "#ifndef FILM_RENDER_WIDTH\n"
        "#define FILM_RENDER_WIDTH (480*renderScale)\n"
        "#endif\n"
        "#ifndef FILM_RENDER_HEIGHT\n"
        "#define FILM_RENDER_HEIGHT (320*renderScale)\n"
        "#endif\n"
        "inline constexpr int renderWidth=FILM_RENDER_WIDTH,renderHeight=FILM_RENDER_HEIGHT;\n",
    )],
}

# The console's frame against the film's: 320 of its 480 columns.
FILM_SCALE = (2, 3)
# The closing credits at the film's own size (CreditMask.hpp at scale 1) and
# the finest mask it carries (scale 6), which the cart's is box-filtered from.
CREDITS_W, CREDITS_H = 360, 168
CREDITS_FINE = 6
# assets.bin: magic, record count, then per record a NUL-padded name, the
# offset and the length (little-endian u32s), then the bytes.
ASSETS_MAGIC = b"MOYJETA1"
ASSETS_NAME = 20


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


def patched(rel, data):
    """`data`, the upstream file at `rel`, with PATCHES applied: each old text
    must occur exactly once, so a moved upstream fails here, not in a build."""
    text = data.decode()
    for old, new in PATCHES.get(rel, ()):
        if text.count(old) != 1:
            raise VendorError("%s: the text PATCHES replaces occurs %d times"
                              % (rel, text.count(old)))
        text = text.replace(old, new)
    return text.encode()


def _arrays(text, source):
    """{name: (C type, [values])} of every array a generated header defines."""
    out = {}
    for ctype, name, body in re.findall(
            r"inline (?:const |constexpr )?(uint8_t|uint16_t) (\w+)\[\]=\{(.*?)\};",
            text, re.S):
        if name in out:
            raise VendorError("%s defines %s twice" % (source, name))
        out[name] = (ctype, [int(v) for v in re.findall(r"\d+", body)])
    if not out:
        raise VendorError("%s no longer defines its arrays" % source)
    return out


def _scaled_glow(glow):
    """The film's 16 x 16 glow quarter (Sprite2D mirrors it into 32 x 32, its
    peak at the corner the mirror joins) with its falloff shrunk about that
    corner to FILM_SCALE: the halo the film draws at a given sprite scale
    comes out two thirds the size, as everything else in the frame does, and
    centred where the film puts it. Channels are interpolated bilinearly."""
    num, den = FILM_SCALE
    if len(glow) != 256:
        raise VendorError("%s: the glow is no longer 16 x 16" % FILM_ASSETS)

    def chan(c, shift, bits):
        return (c >> shift) & ((1 << bits) - 1)

    out = []
    for y in range(16):
        for x in range(16):
            sx = 15.5 - (15.5 - x) * den / num
            sy = 15.5 - (15.5 - y) * den / num
            if sx < 0 or sy < 0:
                out.append(0)
                continue
            x0, y0 = int(sx), int(sy)
            fx, fy = sx - x0, sy - y0
            x1, y1 = min(x0 + 1, 15), min(y0 + 1, 15)
            word = 0
            for shift, bits in ((11, 5), (5, 6), (0, 5)):
                c = [chan(glow[yy * 16 + xx], shift, bits)
                     for yy, xx in ((y0, x0), (y0, x1), (y1, x0), (y1, x1))]
                v = ((c[0] * (1 - fx) + c[1] * fx) * (1 - fy)
                     + (c[2] * (1 - fx) + c[3] * fx) * fy)
                word |= int(v + 0.5) << shift
            out.append(word)
    return out


def credits_coverage(mask_h):
    """The closing credits for the cart's frame: the film's finest one-bit
    mask (CreditMask.hpp at FILM_RENDER_SCALE 6, 2160 x 1008) box-filtered to
    FILM_SCALE of the film's own size, 240 x 112, one byte of coverage per
    pixel (0-255)."""
    text = mask_h.decode()
    head = "#if FILM_RENDER_SCALE == %d\n" % CREDITS_FINE
    if text.count(head) != 1:
        raise VendorError("%s no longer carries its scale-%d mask" % (FILM_CREDITS,
                                                                      CREDITS_FINE))
    fine = _arrays(text[text.index(head):text.index("#else")], FILM_CREDITS)
    bits = bytes(fine["creditMask"][1])
    w, h = CREDITS_W * CREDITS_FINE, CREDITS_H * CREDITS_FINE
    if len(bits) != w * h // 8:
        raise VendorError("%s: the scale-%d mask is not %d x %d"
                          % (FILM_CREDITS, CREDITS_FINE, w, h))
    num, den = FILM_SCALE
    ow, oh = CREDITS_W * num // den, CREDITS_H * num // den
    step = w // ow
    if step * ow != w or step * oh != h:
        raise VendorError("the credits do not divide evenly to %d x %d" % (ow, oh))
    row_bytes = w // 8
    cols = [[0] * ow for _ in range(h)]
    for y in range(h):
        row = bits[y * row_bytes:(y + 1) * row_bytes]
        counts = cols[y]
        for x in range(w):
            if row[x >> 3] & (0x80 >> (x & 7)):
                counts[x // step] += 1
    out = bytearray(ow * oh)
    full = step * step
    for oy in range(oh):
        for ox in range(ow):
            n = sum(cols[oy * step + k][ox] for k in range(step))
            out[oy * ow + ox] = (n * 255 + full // 2) // full
    return bytes(out)


def assets_bin(assets_h, mask_h):
    """assets.bin: every array Assets.hpp defines, as the cart's Assets.hpp
    declares it, the glow scaled (_scaled_glow), and the credits' coverage
    (credits_coverage). Names sort, so the file is one function of its
    inputs."""
    arrays = _arrays(assets_h.decode("latin-1"), FILM_ASSETS)
    records = {}
    for name, (ctype, values) in arrays.items():
        if name == "glow":
            values = _scaled_glow(values)
        width = 1 if ctype == "uint8_t" else 2
        if any(v >> (8 * width) for v in values):
            raise VendorError("%s: %s holds a value wider than %s" % (FILM_ASSETS, name,
                                                                      ctype))
        records[name] = b"".join(v.to_bytes(width, "little") for v in values)
    records["credits"] = credits_coverage(mask_h)
    names = sorted(records)
    for name in names:
        if len(name.encode()) >= ASSETS_NAME:
            raise VendorError("an asset name longer than %d bytes: %s" % (ASSETS_NAME - 1,
                                                                         name))
    table = bytearray(ASSETS_MAGIC + len(names).to_bytes(4, "little"))
    at = len(table) + len(names) * (ASSETS_NAME + 8)
    body = bytearray()
    for name in names:
        data = records[name]
        table += name.encode().ljust(ASSETS_NAME, b"\0")
        table += (at + len(body)).to_bytes(4, "little") + len(data).to_bytes(4, "little")
        body += data
        body += b"\0" * (-len(body) % 4)
    return bytes(table + body)


def read_assets(data):
    """{name: bytes} of an assets.bin: the reader the cart's is written to."""
    if data[:8] != ASSETS_MAGIC:
        raise VendorError("not an assets.bin")
    n = int.from_bytes(data[8:12], "little")
    out = {}
    for i in range(n):
        rec = data[12 + i * (ASSETS_NAME + 8):12 + (i + 1) * (ASSETS_NAME + 8)]
        name = rec[:ASSETS_NAME].rstrip(b"\0").decode()
        at = int.from_bytes(rec[ASSETS_NAME:ASSETS_NAME + 4], "little")
        length = int.from_bytes(rec[ASSETS_NAME + 4:], "little")
        out[name] = data[at:at + length]
    return out


def film_licenses_txt(jet_license, examples_license, jet_commit, examples_commit):
    if jet_license != examples_license:
        raise VendorError("JetExamples' LICENSE is no longer Jet's; LICENSES.txt "
                          "must carry both")
    return ("This cart compiles Jet, a software 3D rasteriser, and ports ESP 88, a\n"
            "film from Jet's examples (esp32-neon-film). Both are CubeCoders\n"
            "Limited's, under the licence below:\n\n"
            "  https://github.com/%s at %s\n"
            "  https://github.com/%s at %s\n\n"
            "%s\n"
            "----------------------------------------------------------------------\n\n"
            "The film's car, city and artwork are original procedural work of that\n"
            "example: its code builds the geometry, and its tools/prepare_assets.py\n"
            "and tools/prepare_credits.py generated the textures and the closing\n"
            "credits. assets.bin holds that artwork as this cart reads it: the\n"
            "textures as the example ships them, the glow and the credits scaled\n"
            "to two thirds, the console's frame against the film's.\n"
            % (JET_REPO, jet_commit, EXAMPLES_REPO, examples_commit,
               jet_license.decode())).encode()


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
    out[_rel(os.path.join(EXAMPLES_DEST, EXAMPLES_LICENSE))] = read_examples(
        EXAMPLES_LICENSE)
    for name in FILM_FILES:
        rel = FILM_DIR + "/" + name
        out[_rel(os.path.join(EXAMPLES_DEST, rel))] = patched(rel, read_examples(rel))
    out[_rel(os.path.join(FILM_CART, "assets.bin"))] = assets_bin(
        read_examples(FILM_ASSETS), read_examples(FILM_CREDITS))
    out[_rel(os.path.join(FILM_CART, "LICENSES.txt"))] = film_licenses_txt(
        read_jet("LICENSE"), read_examples(EXAMPLES_LICENSE), jet_commit, examples_commit)
    return out


def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def cart_data():
    """The derived files that are the carts' data, by where they sit in the
    vendored copy: {repo-relative path: (carts repository cart id, file name)}."""
    return {_rel(os.path.join(CART, "teapot.obj")): ("teapot", "teapot.obj"),
            _rel(os.path.join(CART, "LICENSES.txt")): ("teapot", "LICENSES.txt"),
            _rel(os.path.join(FILM_CART, "assets.bin")): ("esp88", "assets.bin"),
            _rel(os.path.join(FILM_CART, "LICENSES.txt")): ("esp88", "LICENSES.txt")}


def vendored_paths():
    """Every file this script writes here, repo-relative: Jet and the film's
    code, not the carts' data (cart_data()), which is the carts repository's."""
    return sorted([_rel(os.path.join(DEST, rel)) for rel in FILES]
                  + [_rel(os.path.join(EXAMPLES_DEST, EXAMPLES_LICENSE))]
                  + [_rel(os.path.join(EXAMPLES_DEST, FILM_DIR, n)) for n in FILM_FILES])


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
    ap.add_argument("--carts", metavar="DIR",
                    help="also write the carts' derived data into DIR/<cart id>/ "
                    "(a moybyte-org/carts checkout's carts/)")
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
    data = cart_data()
    derived = dict((rel, files.pop(rel)) for rel in data)
    for rel, blob_ in sorted(derived.items()):
        if not args.carts:
            break
        dst = os.path.join(args.carts, *data[rel])
        if os.path.isfile(dst) and sha256_file(dst) == sha256_bytes(blob_):
            continue
        changed.append(dst)
        if not args.check:
            with open(dst, "wb") as f:
                f.write(blob_)
    for rel, data in sorted(files.items()):
        dst = os.path.join(ROOT, rel)
        if os.path.isfile(dst) and sha256_file(dst) == sha256_bytes(data):
            continue
        changed.append(rel)
        if not args.check:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)

    # Nothing under jet/ or examples/ is authored here, so anything not in the
    # table goes.
    for top in (DEST, EXAMPLES_DEST):
        for dirpath, _dirs, names in os.walk(top):
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
