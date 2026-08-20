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

  read_manifests(dir) -> [(folder, manifest)]  every system cart, in seed order
  cart_order(dir) -> [folder]      the seed / embedded-fallback order
  title_to_folder(dir) -> {title: folder}
  roster(dir, target) -> [folder]  the carts that ship on `target`
  app_decls(dir) -> [dict]         the system-app declarations, in reg. order
  build_carts(dir) -> list[dict]   the CARTS structure (what gets frozen)
  render_module(carts) -> str      a freezable carts_data.py source
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
    which does `from carts_data import CARTS`, without writing a file)."""
    import types
    mod = types.ModuleType("carts_data")
    mod.CARTS = build_carts(system_carts_dir)
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
      gen_device_carts.py --app-decls [OUT]      regenerate runtime/app_decls.py
      gen_device_carts.py --roster TARGET        the cart folders for a target,
                                                 one `<folder>.moy` per line
                                                 (the web runner's bundle list)
    """
    argv = list(argv[1:])
    src = _default_system_carts()
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
    text = render_module(build_carts(src))
    if out and out != "-":
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
