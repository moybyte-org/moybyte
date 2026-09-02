#!/usr/bin/env python3
"""Generate the device's embedded fallback carts from `system_carts/`, and read
the SYSTEM-CART DECLARATIONS every other consumer of that folder needs.

The MicroPython firmware does NOT ship the `system_carts/` folder; it seeds the
built-in carts onto SD on first boot from an embedded `CARTS` list and falls
back to it when SD is unavailable. That list used to be ~1800 lines of cart
sources/sprite-sheets hand-copied into `moy_runtime.py` and pinned by a parity
test -- a duplication that drifted (stale art, wrong colorkey) and made every
cart edit a two-place chore.

This script makes `system_carts/` the single source of truth: `build.sh` runs it
to emit `modules/carts_data.py` (gitignored, frozen into the firmware), and
`moy_runtime` just does `from carts_data import CARTS`. Edit `system_carts/`;
the device follows automatically.

**It is also the ONE reader of the manifests' declarations** (the UI refactor's
Phase 5, 2026-08-19). A system cart declares, in its own `manifest.json`:

    "system": true          -- this folder IS a seed cart (required; a folder
                               that forgets it is an ERROR here, never a
                               silently-unseeded cart)
    "order":  120           -- its position in the seed / embedded-fallback
                               list (required, unique; sparse by 10 so an
                               insertion needs no renumbering)
    "targets": ["host", "device"]   -- optional; where the cart SHIPS. Absent
                               means everywhere. The web runner's bundle roster
                               is derived from this, not from a list in its
                               build.sh.
    "app": {...}            -- optional; a SYSTEM APP (docs/app_api_v1.md):
                               id / entry ("module:Class") / text_mode /
                               order (registration precedence) / min_size.

Five hand-maintained lists used to hold that same information -- `CART_ORDER`
here, the title->folder map in tests/test_device_seed_parity.py, the web
runner's `ROSTER=` string, console.py's import + construct + register_app
block, and host_app.py's alias table. Four of the five failed SILENTLY and on
device only: forget `CART_ORDER` and the identity cart never seeds, so `is_app`
never claims it, so the app is simply unreachable on hardware while working
perfectly on the host. They are all
derived from the declarations now (`runtime/app_decls.py` is this file's
generated output for the frozen tiers, which have no `system_carts/`).

Pure stdlib so it runs under any Python the build has.

TWO PAYLOAD REPRESENTATIONS, ONE READER OF THE DECLARATIONS. `carts_data.py`
comes in a PLAIN form (`CARTS = [...]`, which nothing freezes any more) and a
PACKED form (`CARTS_Z = [(title, version, <raw deflate>)]`, what every board
freezes since 2026-08-30). Everything above this line -- the manifests'
`system`/`order`/`targets`/`app` declarations and the cart bodies built from
them -- is identical in both; only how the bytes are spelled differs. The packed
form is 3.6x smaller (201,716 B against 731,592 B, measured on the 35-cart
roster) and it is what let the Zero carry a roster at all. BOTH FORMS WERE
BUILT rather than inferred from the source, and the interesting answer is that
the plain one fits: 2,830,672 B of that board's 2,883,584 B OTA slot, 51 KB
left -- under the #168 warning floor, one cart from a build failure, in a slot
paid for twice. The packed image is 2,399,232 B and leaves 473 KB.

Why a Python bytes literal and not a second `.incbin` beside the web bundle's:
`moy_web` exists because a memoryview straight at flash goes to lwIP with
nothing in between, and NOTHING here can use that -- `deflate.DeflateIO` reads a
STREAM, so a board pays one copy of one cart's compressed bytes whichever way
the blob is spelled. What a literal buys instead is that this file stays the
only thing between `system_carts/` and a board, with no C module to compile, no
`[native]` entry to deny, and no fourth generated translation unit. The frozen
bytes land in ROM either way (`frozen_content.c` emits them as a `static const
mp_obj_str_t`), which is the property that actually mattered.

  read_manifests(dir) -> [(folder, manifest)]  every system cart, in seed order
  cart_order(dir) -> [folder]      the seed / embedded-fallback order
  title_to_folder(dir) -> {title: folder}
  roster(dir, target) -> [folder]  the carts that ship on `target`
  app_decls(dir) -> [dict]         the system-app declarations, in reg. order
  build_carts(dir) -> list[dict]   the CARTS structure (what gets frozen)
  render_module(carts) -> str      a freezable carts_data.py source
  pack_cart(cart) -> bytes         one cart's JSON, raw-deflated
  build_packed(dir) -> [(title, version, blob)]   the packed roster
  render_packed_module(packed) -> str   a freezable PACKED carts_data.py
  render_app_decls(decls) -> str   a freezable runtime/app_decls.py source
  as_module(dir) -> ModuleType     an in-memory carts_data (for host tests)
  main(argv)                       CLI: write the module (or stdout)
"""

import json
import os
import sys

DEFAULT_FORMAT = "moybyte-cart-v1"

# Every consumer of system_carts/ reads its declarations through the four
# functions below. Adding a system cart is an edit to its OWN manifest.json --
# there is no list here, in the tests, in the web build or in console.py to keep
# in step with it.

CART_SUFFIX = ".moy"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_manifests(system_carts_dir=None):
    """Every system cart as `(folder, manifest)`, in SEED order.

    Strict on purpose. A `.moy` folder here that does not declare
    `"system": true` and a unique integer `"order"` raises, because the failure
    it replaces was invisible: a cart missing from the old hand-written
    CART_ORDER simply never reached the device, and the host kept working."""
    root = system_carts_dir or _default_system_carts()
    out = []
    seen = {}
    for name in sorted(os.listdir(root)):
        if not name.endswith(CART_SUFFIX):
            continue
        base = os.path.join(root, name)
        if not os.path.isdir(base):
            continue
        folder = name[:-len(CART_SUFFIX)]
        man = json.loads(_read(os.path.join(base, "manifest.json")))
        if man.get("system") is not True:
            raise ValueError(
                "%s/manifest.json must declare \"system\": true (it lives in "
                "system_carts/, so it IS a seed cart)" % name)
        order = man.get("order")
        if not isinstance(order, int):
            raise ValueError(
                "%s/manifest.json must declare an integer \"order\" (its place "
                "in the device seed list)" % name)
        if order in seen:
            raise ValueError("duplicate \"order\" %d: %s and %s"
                             % (order, seen[order], folder))
        seen[order] = folder
        out.append((folder, man))
    out.sort(key=lambda fm: (fm[1]["order"], fm[0]))
    return out


def cart_order(system_carts_dir=None):
    """The seed / embedded-fallback order -- what CART_ORDER used to hold."""
    return [folder for folder, _man in read_manifests(system_carts_dir)]


def title_to_folder(system_carts_dir=None):
    """`{manifest title: folder}`. The device names a seeded folder from the
    TITLE slug while the host copies the SOURCE folder, so the two names differ
    (theme_picker.moy vs appearance.moy) and something has to relate them --
    tests/test_device_seed_parity.py used to do it with a hand-written map."""
    return {man["title"]: folder
            for folder, man in read_manifests(system_carts_dir)}


def roster(target, system_carts_dir=None):
    """The carts that ship on `target` ("host" / "device" / "web"), in seed
    order. A manifest with no `"targets"` ships everywhere."""
    out = []
    for folder, man in read_manifests(system_carts_dir):
        targets = man.get("targets")
        if targets is None or "*" in targets or target in targets:
            out.append(folder)
    return out


def app_decls(system_carts_dir=None):
    """The SYSTEM APP declarations (docs/app_api_v1.md), in REGISTRATION order
    -- which is not seed order (`app.order` vs the cart's `order`), because
    registration order is the launcher's dispatch precedence and seed order is
    the shelf.

    Each entry is the manifest's `app` block plus the cart `folder`/`title` it
    rides on, so a consumer never has to re-open the manifest."""
    out = []
    for folder, man in read_manifests(system_carts_dir):
        app = man.get("app")
        if not app:
            continue
        for key in ("id", "entry"):
            if not app.get(key):
                raise ValueError("%s.moy: app declaration needs %r" % (folder, key))
        if ":" not in app["entry"]:
            raise ValueError(
                "%s.moy: app entry must be \"module:Class\", got %r"
                % (folder, app["entry"]))
        decl = {
            "id": app["id"],
            "entry": app["entry"],
            "text_mode": bool(app.get("text_mode", False)),
            "order": int(app.get("order", 0)),
            "folder": folder,
            "title": man["title"],
        }
        if app.get("min_size"):
            decl["min_size"] = tuple(int(v) for v in app["min_size"])
        out.append(decl)
    out.sort(key=lambda d: (d["order"], d["id"]))
    return out


def build_carts(system_carts_dir):
    """Read each system cart and build its embedded entry (title/type/src/
    sprites?/sounds?/canvas?/permissions?/cfg/edit) -- the shape moy_runtime
    expects (and seed_builtins writes back to SD)."""
    carts = []
    for folder, man in read_manifests(system_carts_dir):
        base = os.path.join(system_carts_dir, folder + CART_SUFFIX)
        cart = {
            "title": man["title"],
            "type": man.get("type", "app"),
            # cart content version (#47): seed_builtins overwrites a stale on-SD copy
            # when this is newer. Pre-versioning carts default to 0.
            "version": int(man.get("version", 0)),
            "src": _read(os.path.join(base, man.get("main", "main.py"))),
        }
        if man.get("format") and man["format"] != DEFAULT_FORMAT:
            # The device REGENERATES each seeded manifest from this blob (the host
            # copies the folder instead), so a non-default format has to ride along
            # or the two tiers disagree about what the cart is.
            cart["format"] = man["format"]
        if man.get("runtime", "python") != "python":
            # #67 dual-runtime seam: a lua built-in seeds/loads with its runtime +
            # main filename intact (defaults stay implicit to keep the blob lean).
            cart["runtime"] = man["runtime"]
            cart["main"] = man.get("main", "main.py")
        if man.get("fps"):                 # frame pacing (#63): "fps": 60 opt-out
            cart["fps"] = int(man["fps"])
        if man.get("icon"):                # launcher icon tiles (SPEC.md 3.4)
            cart["icon"] = man["icon"]
        sheet = os.path.join(base, "sprites.moygfx")
        if os.path.exists(sheet):
            cart["sprites"] = _read(sheet)
        sounds = os.path.join(base, "sounds.json")     # AudioBank, optional (#16)
        if os.path.exists(sounds):
            cart["sounds"] = json.loads(_read(sounds))
        tilemap = os.path.join(base, "map.moymap")        # TileMap blob, optional (#32)
        if os.path.exists(tilemap):
            cart["map"] = _read(tilemap)
        flags = os.path.join(base, "flags.moyflags")      # tile flags (SPEC.md 3.5)
        if os.path.exists(flags):
            cart["flags"] = _read(flags)
        images_dir = os.path.join(base, "images")          # paint-image assets, optional (#63)
        if os.path.isdir(images_dir):
            images = {}
            for iname in sorted(os.listdir(images_dir)):
                if iname.endswith(".moyimg"):
                    images[iname[:-len(".moyimg")]] = _read(os.path.join(images_dir, iname))
            if images:
                cart["images"] = images
        scenes_dir = os.path.join(base, "scenes")          # placed-actor scenes (#85)
        if os.path.isdir(scenes_dir):
            scenes = {}
            for sname in sorted(os.listdir(scenes_dir)):
                if sname.endswith(".moyscene"):
                    scenes[sname[:-len(".moyscene")]] = _read(os.path.join(scenes_dir, sname))
            if scenes:
                cart["scenes"] = scenes
                # seed_builtins writes manifest assets.scenes from scene_order
                # (element 0 = the default active scene); carry the manifest order.
                order = (man.get("assets") or {}).get("scenes") or []
                keep = [n for n in order if n in scenes]
                if keep:
                    cart["scene_order"] = keep
        blocks = os.path.join(base, "blocks.json")      # block source (#29), optional
        if os.path.exists(blocks):
            # carry the block program so a block-authored seed (tap_game) opens in
            # the on-device block editor, not just as compiled code.
            cart["blocks"] = json.loads(_read(blocks))
        if "canvas" in man:
            cart["canvas"] = man["canvas"]
        if "permissions" in man:
            cart["permissions"] = man["permissions"]
        if "input" in man:                # #42 Thread 3 input-kind hint, optional
            cart["input"] = man["input"]
        cart["cfg"] = man.get("config", {})
        cart["edit"] = man.get("edit", [])
        carts.append(cart)
    return carts


def render_module(carts):
    """A freezable carts_data.py: a header + `CARTS = <repr>`. repr() of plain
    str/int/list/dict is valid, deterministic Python that MicroPython parses."""
    header = (
        '"""AUTO-GENERATED by tools/gen_device_carts.py from system_carts/.\n'
        "Do NOT edit -- the build regenerates it. Edit system_carts/ instead.\n"
        '"""\n\n'
    )
    return header + "CARTS = " + repr(carts) + "\n"


# The deflate window the roster is compressed with, and the one number the two
# sides have to agree on. 15 is the maximum and it is what the size argument
# rests on: the whole roster is 732 KB, so a 32 KB window still finds matches
# ACROSS a cart (its sprite sheet against its own map, its source against its
# own boilerplate). Dropping to 12 costs 7 KB of flash to save 28 KB of a heap
# that is only alive during the seed -- measured, and the wrong trade on every
# board in the fleet, all four of which have PSRAM. `moy_carts._SEED_WBITS` is
# the reader's copy; tests/test_seed_pack.py pins them equal.
SEED_WBITS = 15
SEED_FORMAT = "deflate-raw-%d" % SEED_WBITS

PACKED_HEADER = (
    '"""AUTO-GENERATED by tools/gen_device_carts.py from system_carts/.\n'
    "Do NOT edit -- the build regenerates it. Edit system_carts/ instead.\n"
    "\n"
    "THE PACKED SEED ROSTER. Same carts as the plain `CARTS = [...]` form this\n"
    "generator also emits, spelled as one RAW DEFLATE stream per cart, each\n"
    "inflating to that cart's JSON. A board unpacks ONE AT A TIME\n"
    "(`moy_carts.seed_packed`), so the peak heap is one cart and never the\n"
    "roster.\n"
    "\n"
    "  CARTS_Z    [(title, version, blob)] in seed order. The title and the\n"
    "             version ride OUTSIDE the blob on purpose: they are the two\n"
    "             fields the #47 already-there check needs, so a board that\n"
    "             is already seeded skips a cart without inflating it.\n"
    '"""\n'
)


def pack_cart(cart):
    """One cart dict -> its JSON, raw-deflated.

    NO `sort_keys`, and that is not an oversight. `seed_builtins` writes a
    cart's `cfg`, `sounds`, `blocks` and per-card `edit` dicts back out with
    `json.dumps`, so their key order on the store is the key order in the dict
    it was handed -- and sorting here made a packed-seeded board's config.json
    differ, byte for byte, from a plain-seeded one's. Insertion order is what
    the plain form carries (repr of a dict built by `build_carts`), so it is
    what this one carries too: same roster in, same bytes on the store.

    Determinism rests on the same assumption `render_module`'s repr() already
    does -- a CPython 3.7+ build interpreter, where dict order is insertion
    order -- so a rebuild of an unchanged roster still produces byte-identical
    source and a warm build does not churn `frozen_content.c`.
    """
    import zlib

    body = json.dumps(cart, separators=(",", ":")).encode("utf-8")
    comp = zlib.compressobj(9, zlib.DEFLATED, -SEED_WBITS)
    return comp.compress(body) + comp.flush()


def build_packed(system_carts_dir):
    """The packed roster: [(title, version, blob)], in seed order."""
    return [(c["title"], int(c.get("version", 0)), pack_cart(c))
            for c in build_carts(system_carts_dir)]


def render_packed_module(packed):
    """A freezable PACKED carts_data.py.

    One entry per line, so a diff of this generated file names the cart that
    changed rather than reporting that the roster did. repr() of a bytes object
    is valid, deterministic Python that MicroPython parses -- the same contract
    `render_module` relies on for str/int/list/dict.
    """
    out = [PACKED_HEADER, "", "SEED_FORMAT = %r" % SEED_FORMAT, "",
           "CARTS_Z = ["]
    for title, version, blob in packed:
        out.append("    (%r, %d, %r)," % (title, version, blob))
    out.append("]")
    return "\n".join(out) + "\n"


APP_DECLS_HEADER = (
    '"""The SYSTEM APP registry -- AUTO-GENERATED by tools/gen_device_carts.py\n'
    "from the `app` blocks in system_carts/*/manifest.json.\n"
    "\n"
    "Do NOT edit: regenerate with\n"
    "\n"
    "    python tools/gen_device_carts.py --app-decls\n"
    "\n"
    "and tests/test_app_registry.py fails until you do.\n"
    "\n"
    "This is the frozen tiers' copy of the declaration. The manifests are the\n"
    "source of truth, but a board and the wasm head have no `system_carts/` to\n"
    "read -- exactly as `carts_data.py` is the frozen copy of the cart bodies.\n"
    "console.py loops over APPS to import, construct and register every system\n"
    "app; there is no per-app line anywhere in the shell.\n"
    "\n"
    "  id         the process kind (router / back-stack / window key)\n"
    "  entry      \"module:Class\" -- a runtime/ module staged to every target\n"
    "  text_mode  True = a TYPING app (clean ASCII keyboard, Writer precedent)\n"
    "  order      registration precedence (NOT the cart's shelf order)\n"
    "  folder     the identity cart it rides on, in system_carts/\n"
    "  title      that cart's title (what the device names its seeded folder from)\n"
    "  min_size   optional (w, h) windowed resize floor; omitted means the app's\n"
    "             layout MIN_W/MIN_H are adopted at registration\n"
    '"""\n'
)


def render_app_decls(decls):
    """A freezable `runtime/app_decls.py`: a header + `APPS = <repr>`. One dict
    per app, in registration order -- plain literals MicroPython parses."""
    body = ["APPS = ["]
    for d in decls:
        body.append("    " + repr(d) + ",")
    body.append("]")
    return APP_DECLS_HEADER + "\n" + "\n".join(body) + "\n"


def as_module(system_carts_dir):
    """An in-memory `carts_data` module (so host tests can exec moy_runtime,
    which does `from carts_data import CARTS_Z`, without writing a file).

    BOTH forms, because both are real: every console board freezes the packed
    roster and the plain one is still what the packer is checked against. A
    host test that wants the cart dicts reads `CARTS`; one that wants what a
    board actually holds reads `CARTS_Z` and inflates it, which is the round
    trip worth exercising.
    """
    import types
    mod = types.ModuleType("carts_data")
    mod.CARTS = build_carts(system_carts_dir)
    mod.CARTS_Z = [(c["title"], int(c.get("version", 0)), pack_cart(c))
                   for c in mod.CARTS]
    mod.SEED_FORMAT = SEED_FORMAT
    return mod


def _default_system_carts():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "system_carts")


def _default_app_decls():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "runtime", "app_decls.py")


def main(argv):
    """CLI.

      gen_device_carts.py [OUT [SYSTEM_CARTS]]   the frozen carts_data.py
      gen_device_carts.py --packed OUT [SRC]     the same, PACKED (the Zero)
      gen_device_carts.py --app-decls [OUT]      regenerate runtime/app_decls.py
      gen_device_carts.py --roster TARGET        the cart folders for a target,
                                                 one `<folder>.moy` per line
                                                 (the web runner's bundle list)
    """
    argv = list(argv[1:])
    src = _default_system_carts()
    packed = False
    if argv and argv[0] == "--packed":
        packed = True
        argv = argv[1:]
    if argv and argv[0] == "--roster":
        if len(argv) > 2:
            src = argv[2]
        for folder in roster(argv[1], src):
            sys.stdout.write(folder + CART_SUFFIX + "\n")
        return 0
    if argv and argv[0] == "--app-decls":
        out = argv[1] if len(argv) > 1 else _default_app_decls()
        if len(argv) > 2:
            src = argv[2]
        text = render_app_decls(app_decls(src))
        if out and out != "-":
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
        return 0
    out = argv[0] if argv else None
    if len(argv) > 1:
        src = argv[1]
    if packed:
        blobs = build_packed(src)
        text = render_packed_module(blobs)
        sys.stderr.write(
            "seed roster packed: %d carts, %d B compressed (%d KB), %d B plain\n"
            % (len(blobs), sum(len(b[2]) for b in blobs),
               sum(len(b[2]) for b in blobs) // 1024,
               len(render_module(build_carts(src)).encode("utf-8"))))
    else:
        text = render_module(build_carts(src))
    if out and out != "-":
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
