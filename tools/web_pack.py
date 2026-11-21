# Map (grep -n a name to jump there):
#   PageError        a page that cannot be published, said so a person can act
#   digest_of        the pack's body digest, checked against its own head
#   page_beside      the pack beside an app image, and the digest the image names
#   cart_id          the add-on cart's id for a page, keyed by its digest
#   cart_manifest    the add-on cart's manifest.json for a page
#   publish_cart     the add-on cart for a page, packed beside it
#   index_entry      its entry in the board's index
#   official_carts   the seed carts a release publishes: all but the test fixtures
#   built_seed       a compiled seed cart built from its source, for its entry
#   board_modules    a board build's native modules of the official compiled carts
#   collected_modules the modules the boards of one run built, per cart
#   official_entries every official cart as an entry of the release
#   index_doc        the board's index: the official entries, this add-on, then every earlier add-on
#   publish_addon    the carts, their licence and the board's index, in one folder
"""The browser console add-on and the official carts, as a release and a LAN
mirror publish them.

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
    <cart id>-<12 hex>.zip         each seed cart (system_carts/) but a test
                                   fixture, an OFFICIAL cart: the folder as the
                                   tree holds it, named
                                   by the zip's digest, so one cart is one
                                   asset on every board's index and release
    <cart id>-<12 hex>.png         its cover; a release keeps the ones its
                                   indexes reference, now and before this
                                   publish (official_assets, the publisher's
                                   prune_carts)
    carts-<board>.json             an index in moy-spec's format (cartindex.py,
                                   version 1): an entry per official cart of
                                   THIS build, naming its API level in
                                   moybyte.api, then one per add-on ever
                                   published for the board, newest first, each
                                   naming its page's digest in moybyte.fits;
                                   signed as a carts index is when the key is
                                   there
    moybyte_web-licence.md         the licence the entries name
An official compiled cart carries its source alone. A release builds its
main.wasm, and each board's firmware build, which has its chip's toolchain,
builds its native modules for that chip, unsigned, beside the images as
`<cart id>--<module>` (board_modules: `moybyte.sakura_c--main.esp32p4.elf`).
The publisher collects the ones its run carries (collected_modules), signs
them and puts them in the cart, whose `native` names their chips: a chip no
board of the run built a module for has none, and its consoles run the wasm
one.

A console installs the add-on entry whose moybyte.fits is the digest its
running image names. An index lists an id once, so each add-on entry's id
carries its digest's first 12 hex (cart_id).

An official entry is the seed cart of the build that published it: an app is
Python against the shell's own API, so its `moybyte.api` is that build's
console API level (moy_store_base.API_LEVEL, docs/app_api_v1.md "The API
level"). A console at that level or above lists it and can get it; one below
lists it as needing a newer firmware (cart_index.newer_why). The add-on alone
is gated on its page's digest, which is identity: the webhost serves only the
page its image names. The index keeps no earlier build's official carts. `moybyte.core` marks the ones a console never removes
(moy_seed.CORE). A test fixture (`"moybyte": {"fixture": true}`,
gen_device_carts.is_fixture: Bench and the others the perf ledger, the
on-glass suites and the tests run) is seeded on the host alone and is no
official cart, so no image or bundle carries it and no index lists it. An
official cart no store is seeded with (`"moybyte": {"seed": false}`,
gen_device_carts.is_unseeded) is listed like any other: the index is the one
place a console gets it. A console trusts the built-ins' namespace only from its own
release's index with the signature verified (cart_index.parse_index).
"""

import hashlib
import json
import os
import re
import shutil
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_config  # noqa: E402
import gen_device_carts  # noqa: E402
import gen_web_blob as gwb  # noqa: E402
from runtime.moy_store_base import API_LEVEL, api_above, api_of  # noqa: E402

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
SEED_DIR = os.path.join(ROOT, "system_carts")
SEED_NAME = "%s-%s.zip"            # cart id, the zip's first ID_HEX hex
COVER_NAME = "%s-%s.png"
SEED_SKIP = ("thumbs", "__pycache__")
# A seed cart's zip or cover by its name: an id with a namespace, the digest.
OFFICIAL_ASSET = re.compile(r"^[a-z0-9_]+\.[A-Za-z0-9_.]+-[0-9a-f]{%d}\.(zip|png)$" % ID_HEX)
LISTING = ("description", "genre", "tags")


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
        with open(tmp, "wb") as f:
            f.write(io_zip(cart_id(digest) + ".moy", files))
        os.replace(tmp, dest)
    return name, {fn: _sized(data) for fn, data in files.items()}


def seed_files(src):
    """{path in the cart: bytes} of the seed cart folder `src`, what an
    official entry installs: every file but the regenerable ones
    (SEED_SKIP)."""
    out = {}
    for base, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SEED_SKIP and not d.startswith("."))
        for n in sorted(names):
            if n.startswith("."):
                continue
            path = os.path.join(base, n)
            rel = os.path.relpath(path, src).replace(os.sep, "/")
            with open(path, "rb") as f:
                out[rel] = f.read()
    return out


BOARD_CHIPS = ("esp32s3", "esp32p4")
NATIVE_ASSET = "%s--%s"            # cart id, its module's file in the cart
NATIVE_FILE = re.compile(r"^(?P<cid>[^/]+)--(?P<file>[A-Za-z0-9_]+\.(?P<chip>%s)\.elf)$"
                         % "|".join(BOARD_CHIPS))


def _seed_manifest(src):
    with open(os.path.join(src, "manifest.json"), "rb") as f:
        return json.loads(f.read().decode())


def _from_source(src, man):
    """Whether the seed cart folder `src` is a compiled cart carrying its
    source alone, which a release builds."""
    main = man.get("main") or ""
    return (man.get("runtime") == "wasm" and not os.path.exists(os.path.join(src, main))
            and os.path.isdir(os.path.join(src, "src")))


def _has_native(man):
    return isinstance((man.get("moybyte") or {}).get("native"), list)


def _tools():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from tools import elf_cart, wasm_cart, wasm_module
    return elf_cart, wasm_cart, wasm_module


def built_seed(src, dst, modules=None):
    """The folder a seed cart's official entry is made from: `src` itself, or
    for a compiled seed cart, which carries its source alone, `dst` holding
    it built from its src/ (sdk/README.md, `moy app build`): its main.wasm by
    the pinned wasi-sdk (fetched when absent), its AOT module per board chip
    where the pinned compilers are here, and its native modules (its
    manifest's `native` names their chips), each signed with the OTA key
    where there is one. The native modules are `modules`, {file in the cart:
    path} (a release's, collected_modules), or with `modules` None the ones
    this host builds per board chip whose toolchain is here
    (tools/elf_cart.py). A console with no native module for its chip runs
    the wasm one (app_perms.tier_of). The same source and toolchains build
    the same bytes."""
    man = _seed_manifest(src)
    if not _from_source(src, man):
        return src
    elf_cart, wasm_cart, wasm_module = _tools()
    key = wasm_module.signing_key()
    aot = all(os.path.isfile(os.path.join(wasm_module.DIST, c["file"]))
              for c in wasm_module.COMPILERS.values())
    try:
        wasm_cart.build(src, dst, BOARD_CHIPS if aot else (), signed=key is not None)
    except wasm_cart.ToolchainMissing as exc:
        raise PageError("%s: %s to build its main.wasm" % (os.path.basename(src), exc))
    if not _has_native(man):
        return dst
    if modules is None:
        chips = [c for c in BOARD_CHIPS if elf_cart.available(c)]
        if chips:
            elf_cart.build(dst, dst, chips, signed=key is not None)
        return dst
    for name, path in sorted(modules.items()):
        chip = name.rsplit(".", 2)[1]
        with open(path, "rb") as f:
            data = wasm_module.split(f.read())[0]
        if key is not None:
            data = wasm_module.sign(data, chip, key, scheme=elf_cart.sig_scheme())
        with open(os.path.join(dst, name), "wb") as f:
            f.write(data)
    man_path = os.path.join(dst, "manifest.json")
    built = _seed_manifest(dst)
    built.setdefault("moybyte", {})["native"] = [
        c for c in BOARD_CHIPS if os.path.exists(os.path.join(dst, elf_cart.module_name(c)))]
    with open(man_path, "w") as f:
        json.dump(built, f, indent=2)
        f.write("\n")
    return dst


def board_modules(chip, out, seeds=SEED_DIR):
    """Build, for `chip`, the native modules of every official compiled cart
    under `seeds` into the folder `out`, UNSIGNED (the release signs them),
    each named NATIVE_ASSET. Returns the names, none for a chip no native
    module is built for. Raises tools/elf_cart.py's ToolError, and the
    compiler's CalledProcessError."""
    if chip not in BOARD_CHIPS:
        return []
    elf_cart = _tools()[0]
    import tempfile
    os.makedirs(out, exist_ok=True)
    got = []
    for n in official_carts(seeds):
        src = os.path.join(seeds, n)
        man = _seed_manifest(src)
        if not (_from_source(src, man) and _has_native(man)):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            built = elf_cart.build(src, os.path.join(tmp, n), [chip], signed=False)
            for path in built.values():
                name = NATIVE_ASSET % (man["id"], os.path.basename(path))
                shutil.copyfile(path, os.path.join(out, name))
                got.append(name)
    return sorted(got)


def collected_modules(folders):
    """{cart id: {file in the cart: path}} of the native modules
    (NATIVE_ASSET) in `folders`, one run's board builds. Boards of one chip
    build the same module; the first folder's is taken, and one that differs
    from it is named on stderr."""
    out = {}
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for n in sorted(os.listdir(folder)):
            m = NATIVE_FILE.match(n)
            if m is None:
                continue
            path = os.path.join(folder, n)
            have = out.setdefault(m.group("cid"), {})
            first = have.setdefault(m.group("file"), path)
            if first != path and _file_sha(first) != _file_sha(path):
                print("::warning::%s differs from %s; publishing the first"
                      % (path, first), file=sys.stderr)
    return out


def _file_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def seed_entry(src, folder, core=(), modules=None):
    """The official entry of the seed cart folder `src`, its zip and its
    cover written into `folder` (a name already there is the same bytes, by
    its digest). `core` is moy_seed.CORE; `modules` is collected_modules'
    answer, or None (built_seed). Its `moybyte.api` is this build's
    API_LEVEL; a seed cart naming a higher one is a PageError."""
    man = _seed_manifest(src)
    cid = man["id"]
    if api_above(api_of(man)) is not None:
        raise PageError("%s needs API level %d and this build is at %d"
                        % (cid, api_of(man), API_LEVEL))
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        files = seed_files(built_seed(src, os.path.join(tmp, cid + ".moy"),
                                      None if modules is None else modules.get(cid, {})))
    data = io_zip(cid + ".moy", files)
    name = SEED_NAME % (cid, hashlib.sha256(data).hexdigest()[:ID_HEX])
    dest = os.path.join(folder, name)
    if not os.path.exists(dest):
        with open(dest, "wb") as f:
            f.write(data)
    with open(os.path.join(folder, LICENCE_NAME), "rb") as f:
        licence = _sized(f.read())
    entry = {
        "id": cid,
        "name": man["title"],
        "version": int(man.get("version", 0)),
        "folder": cid + ".moy",
        "runtime": man.get("runtime", "python"),
        "licence": dict(LICENCE, url=LICENCE_NAME, **licence),
        "assets": [dict(name=name, url=name, files={fn: _sized(b) for fn, b in files.items()},
                        **_sized(data))],
        "moybyte": {"api": API_LEVEL, "core": cid in core},
    }
    for key in LISTING:
        if man.get(key):
            entry[key] = man[key]
    cover = files.get("cover.png")
    if cover is not None and cover[16:24] == bytes.fromhex("0000008000000080"):
        cname = COVER_NAME % (cid, hashlib.sha256(cover).hexdigest()[:ID_HEX])
        with open(os.path.join(folder, cname), "wb") as f:
            f.write(cover)
        entry["cover"] = dict(url=cname, w=128, h=128, **_sized(cover))
    return entry


def io_zip(top, files):
    """The bytes of the STORED zip of `files` under the folder `top`."""
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for fn in sorted(files):
            info = zipfile.ZipInfo("%s/%s" % (top, fn), ZIP_TIME)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, files[fn])
    return buf.getvalue()


def official_carts(seeds=SEED_DIR):
    """The folders of the official carts under `seeds`, in folder order:
    every seed cart but a test fixture (gen_device_carts.is_fixture)."""
    out = []
    for n in sorted(os.listdir(seeds)):
        man = os.path.join(seeds, n, "manifest.json")
        if n.endswith(".moy") and os.path.isfile(man):
            with open(man, "rb") as f:
                if not gen_device_carts.is_fixture(json.loads(f.read().decode())):
                    out.append(n)
    return out


def official_entries(folder, seeds=SEED_DIR, modules=None):
    """The official entry of every official cart under `seeds`
    (official_carts), in folder order, their zips and covers written into
    `folder` (which holds the licence already), a compiled one's native
    modules `modules` (seed_entry)."""
    core = board_config.core_carts()
    return [seed_entry(os.path.join(seeds, n), folder, core, modules)
            for n in official_carts(seeds)]


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


def _index_carts(data):
    import ota_sign
    try:
        doc = json.loads(ota_sign.index_unsigned(data).decode())
    except (ValueError, UnicodeError):
        return None
    carts = doc.get("carts") if isinstance(doc, dict) else None
    return carts if isinstance(carts, list) else None


def earlier_entries(data):
    """The add-on entries of an index this module wrote before (its bytes,
    signed or not), or [] for None or a document that is not one."""
    carts = _index_carts(data) if data else None
    return [c for c in carts or () if isinstance(c, dict) and c.get("runtime") == CART_RUNTIME
            and isinstance(c.get("moybyte"), dict) and c["moybyte"].get("fits")]


def referenced(data):
    """The set of names the index `data` (its bytes, signed or not)
    references: every entry's assets, cover and licence. None for a
    document that is not an index."""
    carts = _index_carts(data) if data else None
    if carts is None:
        return None
    out = set()
    for c in carts:
        if not isinstance(c, dict):
            continue
        refs = [a for a in c.get("assets") or () if isinstance(a, dict)]
        refs += [r for r in (c.get("cover"), c.get("licence")) if isinstance(r, dict)]
        out |= {r["url"] for r in refs if isinstance(r.get("url"), str)}
    return out


def official_assets(names, indexes):
    """The official carts' zips and covers among the release asset `names`
    that none of the `indexes` (their bytes) references: what a release can
    delete. Raises ValueError when one of them is not an index, which keeps
    unknown what it references."""
    keep = set()
    for data in indexes:
        refs = referenced(data)
        if refs is None:
            raise ValueError("an index that is not one")
        keep |= refs
    return sorted(n for n in names if OFFICIAL_ASSET.match(n) and n not in keep)


def index_doc(board, entry, earlier=None, official=()):
    """The bytes of `carts-<board>.json`: the `official` entries (this
    build's official carts), the add-on `entry`, then every add-on entry of the
    board's earlier index (`earlier`, its bytes) for another page, so a
    console a step behind its channel still finds the add-on its image fits."""
    carts = list(official) + [entry] + [
        c for c in earlier_entries(earlier)
        if c["moybyte"]["fits"] != entry["moybyte"]["fits"]]
    doc = {"version": 1, "name": "Moybyte %s" % board,
           "home": "https://github.com/moybyte-org/moybyte", "carts": carts}
    return json.dumps(doc, indent=2, sort_keys=True).encode() + b"\n"


def publish_addon(page, folder, board, version, key_pem=None, earlier=None,
                  seeds=SEED_DIR, modules=None):
    """Everything a console installs the add-on and the official carts from,
    into `folder`: the page's cart, every official cart under `seeds`
    (None: none), a compiled one with the native modules `modules`
    (collected_modules' answer; None builds them here, built_seed), the
    licence and `carts-<board>.json` over the board's `earlier`
    index (its bytes; None reads the one already in `folder`), signed with
    `key_pem` when given. Returns {"cart", "index", "official"}: the add-on's
    and the index's names, and the official entries' count."""
    index = INDEX_NAME % board
    path = os.path.join(folder, index)
    if earlier is None and os.path.exists(path):
        with open(path, "rb") as f:
            earlier = f.read()
    shutil.copyfile(LICENCE_SRC, os.path.join(folder, LICENCE_NAME))
    cart = publish_cart(page, folder)
    official = official_entries(folder, seeds, modules) if seeds else []
    doc = index_doc(board, index_entry(page[1], version, cart, folder), earlier,
                    official)
    if key_pem:
        import ota_sign
        doc = ota_sign.sign_index(doc, key_pem)
    with open(path, "wb") as f:
        f.write(doc)
    return {"cart": cart[0], "index": index, "official": len(official)}


def main(argv=None):
    """`web_pack.py modules BOARD_DIR OUT`: a board build's native modules of
    the official compiled carts, for its chip (board_modules)."""
    import argparse
    ap = argparse.ArgumentParser(description=main.__doc__.splitlines()[0])
    ap.add_argument("verb", choices=["modules"])
    ap.add_argument("board_dir", help="the board's firmware folder (its board.toml)")
    ap.add_argument("out", help="the folder the modules go to")
    args = ap.parse_args(argv)
    chip = board_config.load(args.board_dir)["board"]["chip"]
    names = board_modules(chip, args.out)
    for n in names:
        print("%s %d" % (n, os.path.getsize(os.path.join(args.out, n))))
    if not names:
        print("no native modules for %s" % chip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
