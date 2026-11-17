# Map (grep -n a name to jump there):
#   PageError        a page that cannot be published, said so a person can act
#   digest_of        the pack's body digest, checked against its own head
#   page_beside      the pack beside an app image, and the digest the image names
#   publish_page     copy it into a published folder under that name
#   cart_manifest    the add-on cart's manifest.json for a page
#   publish_cart     the add-on cart for a page, packed beside it
#   index_doc        the index that lists the add-on cart for one board
#   publish_addon    the page, the cart, its licence and its index, in one folder
"""The browser console add-on, as a release and a LAN mirror publish it.

The browser console is a first-party cart a console installs into its store,
not part of the firmware: every board's build writes the console as
`moybyte_web.pack` beside the app image (tools/gen_web_blob.py --pack), and
the image carries only the sha256 of the pack's body, the page it fits.

Beside every payload, under names keyed by that digest and kept for the
images that still name them:
    moybyte_web-<digest>.moy.zip   the add-on cart (CANONICAL): web_console.moy/
                                   with manifest.json, whose moybyte.fits names
                                   the digest, the pack and its licence
    moybyte_web-<digest>.pack      the pack alone, the build's output, as
                                   device/moy_ota.py's page_url names it
    carts-<board>.json             an index in moy-spec's format (cartindex.py,
                                   version 1) listing the cart for the board's
                                   current image, signed as a carts index is
                                   when the release has the key
    moybyte_web-licence.md         the licence the index names
whether or not any console has the add-on installed.
"""

import hashlib
import json
import os
import shutil
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_web_blob as gwb  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_FILE = "moybyte_web.pack"     # the build's pack beside its image, and in the cart
PAGE_NAME = "moybyte_web-%s.pack"  # device/moy_ota.py's PAGE_NAME
CART_ID = "web_console"
CART_FOLDER = CART_ID + ".moy"
CART_TITLE = "Browser console"
CART_RUNTIME = "web"
CART_NAME = "moybyte_web-%s.moy.zip"
INDEX_NAME = "carts-%s.json"
LICENCE_NAME = "moybyte_web-licence.md"
LICENCE_SRC = os.path.join(ROOT, "LICENSES", "FSL-1.1-MIT.md")
LICENCE = {"spdx": "FSL-1.1-MIT", "name": "Functional Source License, Version 1.1, MIT Future License"}
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class PageError(Exception):
    pass


def digest_of(data):
    """The 64 hex of the pack's body digest. Raises PageError when the pack is
    not one: wrong magic, a size its head does not name, or a body that does
    not hash to the digest its head carries."""
    if len(data) < gwb.PACK_HEAD:
        raise PageError("%d B is shorter than a pack's head" % len(data))
    magic, _count, total, want = struct.unpack_from("<8sII32s", data, 0)
    if magic != gwb.PACK_MAGIC:
        raise PageError("not a pack (magic %r)" % magic)
    if total != len(data):
        raise PageError("its head names %d B and it is %d B" % (total, len(data)))
    body = hashlib.sha256(data[gwb.PACK_HEAD:]).digest()
    if body != want:
        raise PageError("its body does not hash to the digest its head names")
    return body.hex()


def page_beside(image, board):
    """(pack path, digest) of the page an app image names.

    The pack is the one its build wrote beside `image`, and the image must
    carry its digest (as the 32 bytes or their 64 hex), else PageError: a
    manifest offering that image would name a page no console can fetch."""
    pack = os.path.join(os.path.dirname(os.path.abspath(image)), PACK_FILE)
    if not os.path.exists(pack):
        raise PageError("%s's build left no %s beside %s"
                        % (board, PACK_FILE, image))
    with open(pack, "rb") as f:
        digest = digest_of(f.read())
    with open(image, "rb") as f:
        data = f.read()
    if bytes.fromhex(digest) not in data and digest.encode() not in data:
        raise PageError("%s's image does not name the %s beside it "
                        "(digest %s): they are from different builds"
                        % (board, PACK_FILE, digest[:12]))
    return pack, digest


def publish_page(page, folder):
    """Copy a page_beside() result into `folder` under its published name and
    return that name. A page already there is the same bytes by its name, so
    it is left as it is; nothing earlier is removed."""
    pack, digest = page
    name = PAGE_NAME % digest
    dest = os.path.join(folder, name)
    if not os.path.exists(dest):
        shutil.copyfile(pack, dest)
    return name


def _sized(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def cart_manifest(digest):
    """The add-on cart's manifest.json: `moybyte.fits` is the digest of the
    page an image names, which the pack in the cart hashes to."""
    return {
        "format": "moy-1",
        "title": CART_TITLE,
        "runtime": CART_RUNTIME,
        "main": PACK_FILE,
        "moybyte": {"type": "web", "fits": digest},
    }


def _cart_files(pack):
    """{file name: bytes} of the cart for the pack at `pack`."""
    with open(pack, "rb") as f:
        data = f.read()
    with open(LICENCE_SRC, "rb") as f:
        licence = f.read()
    manifest = json.dumps(cart_manifest(digest_of(data)), indent=2,
                          sort_keys=True).encode() + b"\n"
    return {"LICENSE.md": licence, "manifest.json": manifest, PACK_FILE: data}


def publish_cart(page, folder):
    """Pack the add-on cart for a page_beside() result into `folder` (a
    STORED zip of web_console.moy/<file>, sorted, at a fixed time, so one
    page is one cart byte for byte) and return (name, its files' sizes and
    sha256s). A cart already there is left as it is."""
    pack, digest = page
    files = _cart_files(pack)
    name = CART_NAME % digest
    dest = os.path.join(folder, name)
    if not os.path.exists(dest):
        tmp = dest + ".part"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for fn in sorted(files):
                info = zipfile.ZipInfo("%s/%s" % (CART_FOLDER, fn), ZIP_TIME)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                z.writestr(info, files[fn])
        os.replace(tmp, dest)
    return name, {fn: _sized(data) for fn, data in files.items()}


def index_doc(board, version, digest, cart, folder):
    """The bytes of `carts-<board>.json`: an index listing the add-on cart
    `cart` (publish_cart's answer, in `folder`) that fits the board's current
    image, which names `digest`; `version` is the entry's (the image's). Its
    references are relative, so the index, the cart and the licence move
    together to any mirror."""
    name, files = cart
    with open(os.path.join(folder, name), "rb") as f:
        asset = _sized(f.read())
    with open(os.path.join(folder, LICENCE_NAME), "rb") as f:
        licence = _sized(f.read())
    entry = {
        "id": CART_ID,
        "name": CART_TITLE,
        "version": int(version),
        "folder": CART_FOLDER,
        "runtime": CART_RUNTIME,
        "description": "Open this console from a browser on the same network.",
        "licence": dict(LICENCE, url=LICENCE_NAME, **licence),
        "assets": [dict(name=name, url=name, files=files, **asset)],
        "moybyte": {"fits": digest},
    }
    doc = {"version": 1, "name": "Moybyte %s" % board,
           "home": "https://github.com/moybyte-org/moybyte", "carts": [entry]}
    return json.dumps(doc, indent=2, sort_keys=True).encode() + b"\n"


def publish_addon(page, folder, board, version, key_pem=None):
    """Everything a console installs the add-on from, into `folder`: the
    page, its cart, the licence and `carts-<board>.json` (signed with
    `key_pem` when given). Returns {"page", "cart", "index"}: their names."""
    page_name = publish_page(page, folder)
    shutil.copyfile(LICENCE_SRC, os.path.join(folder, LICENCE_NAME))
    cart = publish_cart(page, folder)
    doc = index_doc(board, version, page[1], cart, folder)
    if key_pem:
        import ota_sign
        doc = ota_sign.sign_index(doc, key_pem)
    index = INDEX_NAME % board
    with open(os.path.join(folder, index), "wb") as f:
        f.write(doc)
    return {"page": page_name, "cart": cart[0], "index": index}
