# KidCode SD cartridge store.
#
# Cartridges live as .kcart folders under /sd/kidcode/carts/<name>.kcart/:
#   manifest.json   title, type, runtime, main, edit-schema
#   main.py         the cartridge source (_init/_update/_draw + kid API)
#   config.json     user-edited values (the Make-it-mine surface)
#   (sprites later)
#
# Mirrors the host runtime/cartridge.py model, but MicroPython-friendly (no
# shutil; os-only). Functions take a `root` so the format/seed/scan logic is
# host-testable against a temp dir. SD shares the SPI bus with the display, so
# the caller mounts SD (kidcode_sd) with the LoRa/TFT CS deselected first.

import json

try:
    import os
except ImportError:  # pragma: no cover
    os = None

CARTS_DIR = "/sd/kidcode/carts"
CART_FORMAT = "kidcode-cart-v1"


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _read(path):
    with open(path, "r") as f:
        return f.read()


def _write(path, data):
    with open(path, "w") as f:
        f.write(data)


def slug(title):
    out = ""
    for ch in str(title).lower():
        if ch.isalpha() or ch.isdigit():
            out += ch
        elif ch in " -_":
            out += "_"
    return out or "cart"


def ensure_dirs(root=CARTS_DIR):
    parent = root.rsplit("/", 1)[0]
    if parent:
        _mkdir(parent)
    _mkdir(root)


def seed_builtins(seed_list, root=CARTS_DIR):
    """Write any missing built-in carts to SD as editable .kcart folders."""
    for cart in seed_list:
        d = root + "/" + slug(cart["title"]) + ".kcart"
        if _exists(d):
            continue
        _mkdir(d)
        manifest = {
            "format": CART_FORMAT, "title": cart["title"], "type": cart["type"],
            "runtime": "python", "main": "main.py", "edit": cart.get("edit", []),
        }
        _write(d + "/manifest.json", json.dumps(manifest))
        _write(d + "/main.py", cart["src"])
        _write(d + "/config.json", json.dumps(cart["cfg"]))


def load(path):
    """Load one .kcart folder into a cart dict, or None on error."""
    try:
        man = json.loads(_read(path + "/manifest.json"))
    except (OSError, ValueError) as exc:
        print("KidCode cart manifest bad:", path, exc)
        return None
    try:
        src = _read(path + "/" + man.get("main", "main.py"))
    except OSError as exc:
        print("KidCode cart main missing:", path, exc)
        return None
    cfg = dict(man.get("config", {}))
    try:
        cfg.update(json.loads(_read(path + "/config.json")))
    except (OSError, ValueError):
        pass
    try:
        sprites = _read(path + "/sprites.kgfx")   # PICO-8 __gfx__-style hex, optional
    except OSError:
        sprites = None
    return {
        "path": path,
        "title": man.get("title", "cart"),
        "type": man.get("type", "app"),
        "src": src,
        "cfg": cfg,
        "edit": man.get("edit", []),
        "sprites": sprites,
    }


def scan(root=CARTS_DIR):
    """All carts found under root, sorted by folder name."""
    carts = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return carts
    for name in names:
        if name.endswith(".kcart"):
            c = load(root + "/" + name)
            if c:
                carts.append(c)
    return carts


def save_config(cart):
    """Persist a cart's edited config back to its config.json (needs cart['path'])."""
    _write(cart["path"] + "/config.json", json.dumps(cart["cfg"]))


def save_code(cart, src):
    """Persist edited source back to the cart's main file (code editor)."""
    _write(cart["path"] + "/main.py", src)
    cart["src"] = src


def save_sprites(cart, hex_text):
    """Persist the sprite sheet (PICO-8 __gfx__-style hex) to sprites.kgfx."""
    _write(cart["path"] + "/sprites.kgfx", hex_text)
    cart["sprites"] = hex_text


# --- cart management (create / duplicate / delete) --------------------------

# A friendly starter cartridge: an editable colored dot on a wallpaper.
NEW_TEMPLATE = {
    "type": "wallpaper",
    "src": (
        "def _draw():\n"
        "    cls(col(cfg('bg', 'dark_blue')))\n"
        "    circ(W // 2, H // 2, cfg('size', 24), col(cfg('color', 'yellow')))\n"
        "    print('MY NEW CART', 20, 20, col('white'), 2)\n"
    ),
    "cfg": {"bg": "dark_blue", "color": "yellow", "size": 24},
    "edit": [
        {"key": "bg", "type": "choice", "choices": ["dark_blue", "black", "dark_purple", "indigo"], "card": "SKY IS {value}"},
        {"key": "color", "type": "choice", "choices": ["yellow", "red", "green", "blue", "pink"], "card": "DOT IS {value}"},
        {"key": "size", "type": "int", "min": 4, "max": 80, "step": 4, "card": "DOT SIZE {value}"},
    ],
}


def _is_dir(path):
    try:
        return (os.stat(path)[0] & 0x4000) != 0
    except OSError:
        return False


def _unique_dir(root, base):
    d = root + "/" + base + ".kcart"
    if not _exists(d):
        return d
    i = 2
    while _exists(root + "/" + base + "_" + str(i) + ".kcart"):
        i += 1
    return root + "/" + base + "_" + str(i) + ".kcart"


def create(title, root=CARTS_DIR, src=None, cfg=None, edit=None, type="app"):
    """Create a new .kcart folder and return its loaded cart dict."""
    d = _unique_dir(root, slug(title))
    _mkdir(d)
    manifest = {
        "format": CART_FORMAT, "title": title, "type": type,
        "runtime": "python", "main": "main.py", "edit": edit or [],
    }
    _write(d + "/manifest.json", json.dumps(manifest))
    _write(d + "/main.py", src if src is not None else NEW_TEMPLATE["src"])
    _write(d + "/config.json", json.dumps(cfg or {}))
    return load(d)


def new_from_template(root=CARTS_DIR, title="New Cart"):
    return create(title, root, src=NEW_TEMPLATE["src"], cfg=dict(NEW_TEMPLATE["cfg"]),
                  edit=NEW_TEMPLATE["edit"], type=NEW_TEMPLATE["type"])


def duplicate(cart, root=CARTS_DIR, new_title=None):
    return create(new_title or (cart["title"] + " copy"), root,
                  src=cart["src"], cfg=dict(cart["cfg"]), edit=cart["edit"], type=cart["type"])


def delete(cart):
    _rmtree(cart["path"])


def _rmtree(path):
    try:
        names = os.listdir(path)
    except OSError:
        return
    for n in names:
        p = path + "/" + n
        if _is_dir(p):
            _rmtree(p)
        else:
            try:
                os.remove(p)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass
