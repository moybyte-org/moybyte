# Map (grep -n a name to jump there):
#   PageError        a page that cannot be published, said so a person can act
#   digest_of        the pack's body digest, checked against its own head
#   page_beside      the pack beside an app image, and the digest the image names
#   cart_id          the add-on cart's id for a page, keyed by its digest
#   cart_manifest    the add-on cart's manifest.json for a page
#   publish_cart     the add-on cart for a page, packed beside it
#   index_entry      its entry in the board's index
#   index_doc        the board's index: this entry first, then every earlier one
#   publish_addon    the cart, its licence and the board's index, in one folder
"""The browser console add-on, as a release and a LAN mirror publish it.

The browser console is a first-party cart a console installs into its store,
not part of the firmware: every board's build writes the console as
`moybyte_web.pack` beside the app image (tools/gen_web_blob.py --pack), and
the image carries only the sha256 of the pack's body, the page it fits.

Beside every payload, whether or not any console has the add-on installed:
    moybyte_web-<digest>.moy.zip   the add-on cart, one per page, kept for the
                                   images that still name it: the folder
                                   web_console_<12 hex>.moy, holding manifest.json
                                   (moybyte.fits: the digest), the pack and its
                                   licence
    carts-<board>.json             an index in moy-spec's format (cartindex.py,
                                   version 1) with one entry per add-on ever
                                   published for the board, newest first, each
                                   naming its digest in moybyte.fits; signed as
                                   a carts index is when the key is there
    moybyte_web-licence.md         the licence the entries name
A console installs the entry whose moybyte.fits is the digest its running
image names. An index lists an id once, so each entry's id carries its
digest's first 12 hex (cart_id).
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
CART_ID = "web_console_%s"         # the digest's first ID_HEX hex
ID_HEX = 12
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


def cart_id(digest):
    """The add-on's cart id for the page `digest`; its folder is this + .moy."""
    return CART_ID % digest[:ID_HEX]


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
    STORED zip of <cart id>.moy/<file>, sorted, at a fixed time, so one page
    is one cart byte for byte) and return (name, its files' sizes and
    sha256s). A cart already there is left as it is."""
    pack, digest = page
    files = _cart_files(pack)
    name = CART_NAME % digest
    dest = os.path.join(folder, name)
    if not os.path.exists(dest):
        tmp = dest + ".part"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for fn in sorted(files):
                info = zipfile.ZipInfo("%s.moy/%s" % (cart_id(digest), fn), ZIP_TIME)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                z.writestr(info, files[fn])
        os.replace(tmp, dest)
    return name, {fn: _sized(data) for fn, data in files.items()}


def index_entry(digest, version, cart, folder):
    """The index entry for the add-on cart `cart` (publish_cart's answer, in
    `folder`) of the page `digest`; `version` is that of the image it is
    published with. Its references are relative, so an index, its carts and
    the licence move together to any mirror."""
    name, files = cart
    with open(os.path.join(folder, name), "rb") as f:
        asset = _sized(f.read())
    with open(os.path.join(folder, LICENCE_NAME), "rb") as f:
        licence = _sized(f.read())
    return {
        "id": cart_id(digest),
        "name": CART_TITLE,
        "version": int(version),
        "folder": cart_id(digest) + ".moy",
        "runtime": CART_RUNTIME,
        "description": "Open this console from a browser on the same network.",
        "licence": dict(LICENCE, url=LICENCE_NAME, **licence),
        "assets": [dict(name=name, url=name, files=files, **asset)],
        "moybyte": {"fits": digest},
    }


def earlier_entries(data):
    """The entries of an index this module wrote before (its bytes, signed or
    not), or [] for None or a document that is not one."""
    if not data:
        return []
    import ota_sign
    try:
        doc = json.loads(ota_sign.index_unsigned(data).decode())
    except (ValueError, UnicodeError):
        return []
    carts = doc.get("carts") if isinstance(doc, dict) else None
    return [c for c in carts or () if isinstance(c, dict)
            and isinstance(c.get("moybyte"), dict) and c["moybyte"].get("fits")]


def index_doc(board, entry, earlier=None):
    """The bytes of `carts-<board>.json`: `entry` first, then every entry of
    the board's earlier index (`earlier`, its bytes) for another page, so a
    console a step behind its channel still finds the add-on its image fits."""
    carts = [entry] + [c for c in earlier_entries(earlier)
                       if c["moybyte"]["fits"] != entry["moybyte"]["fits"]]
    doc = {"version": 1, "name": "Moybyte %s" % board,
           "home": "https://github.com/moybyte-org/moybyte", "carts": carts}
    return json.dumps(doc, indent=2, sort_keys=True).encode() + b"\n"


def publish_addon(page, folder, board, version, key_pem=None, earlier=None):
    """Everything a console installs the add-on from, into `folder`: the
    page's cart, the licence and `carts-<board>.json` over the board's
    `earlier` index (its bytes; None reads the one already in `folder`),
    signed with `key_pem` when given. Returns {"cart", "index"}: their names."""
    index = INDEX_NAME % board
    path = os.path.join(folder, index)
    if earlier is None and os.path.exists(path):
        with open(path, "rb") as f:
            earlier = f.read()
    shutil.copyfile(LICENCE_SRC, os.path.join(folder, LICENCE_NAME))
    cart = publish_cart(page, folder)
    doc = index_doc(board, index_entry(page[1], version, cart, folder), earlier)
    if key_pem:
        import ota_sign
        doc = ota_sign.sign_index(doc, key_pem)
    with open(path, "wb") as f:
        f.write(doc)
    return {"cart": cart[0], "index": index}
