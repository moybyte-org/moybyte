"""Cartridge model for the v0.4 fantasy workstation.

A cartridge is a `.kcart` folder:

    my_cart.kcart/
      manifest.json     # title, type, runtime, main, config defaults, ...
      main.py           # _init() / _update(dt) / _draw()
      config.json       # (optional) user edits overriding manifest "config"

System cartridges are protected (read-only originals); the child duplicates one
into their projects and edits the copy. `config` is the editable surface that
powers the "change a number, press Run, watch it change" loop.
"""

import json
import os
import shutil

from .editors import SpriteSheet

REQUIRED_FIELDS = ("format", "title", "type", "runtime", "main")
CART_FORMAT = "kidcode-cart-v1"
SPRITES_FILE = "sprites.kgfx"


class CartridgeError(Exception):
    pass


class Cartridge:
    def __init__(self, path, manifest, main_source, config, system=False, sheet=None):
        self.path = path
        self.manifest = manifest
        self.main_source = main_source
        self.config = config
        self.system = system
        # The cartridge's sprite sheet (8x8 indexed tiles). Always present so
        # spr(n, ...) is safe even before any sprites are painted.
        self.sheet = sheet if sheet is not None else SpriteSheet()

    @property
    def title(self):
        return self.manifest.get("title", "untitled")

    @property
    def type(self):
        return self.manifest.get("type", "app")

    @property
    def runtime(self):
        return self.manifest.get("runtime", "python")

    @classmethod
    def load(cls, path):
        if not os.path.isdir(path):
            raise CartridgeError("not a cartridge folder: %s" % path)
        manifest_path = os.path.join(path, "manifest.json")
        if not os.path.isfile(manifest_path):
            raise CartridgeError("missing manifest.json in %s" % path)
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except ValueError as exc:
            raise CartridgeError("invalid manifest.json: %s" % exc)
        _validate(manifest)

        main_name = manifest.get("main", "main.py")
        main_path = os.path.join(path, main_name)
        if not os.path.isfile(main_path):
            raise CartridgeError("missing main file %s" % main_name)
        with open(main_path, "r", encoding="utf-8") as fh:
            main_source = fh.read()

        # config = manifest defaults overlaid by config.json (user edits).
        config = dict(manifest.get("config", {}))
        config_path = os.path.join(path, "config.json")
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    config.update(json.load(fh))
            except ValueError as exc:
                raise CartridgeError("invalid config.json: %s" % exc)

        # Optional sprite sheet (PICO-8 __gfx__-style hex); absent for carts that
        # have no painted sprites yet.
        sheet = None
        sprites_path = os.path.join(path, SPRITES_FILE)
        if os.path.isfile(sprites_path):
            with open(sprites_path, "r", encoding="utf-8") as fh:
                sheet = SpriteSheet.from_hex(fh.read())

        system = bool(manifest.get("system", False))
        return cls(path, manifest, main_source, config, system=system, sheet=sheet)

    def duplicate(self, dest_dir, new_title=None):
        """Copy this cartridge into dest_dir as an editable (non-system) copy."""
        if os.path.exists(dest_dir):
            raise CartridgeError("destination already exists: %s" % dest_dir)
        shutil.copytree(self.path, dest_dir)
        manifest = dict(self.manifest)
        manifest["title"] = new_title or (self.title + " copy")
        manifest["system"] = False
        with open(os.path.join(dest_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        return Cartridge.load(dest_dir)

    def save_config(self, updates=None):
        """Persist config edits to the cartridge's config.json. Refuses system carts."""
        if self.system:
            raise CartridgeError("cannot edit a system cartridge; duplicate it first")
        if updates:
            self.config.update(updates)
        with open(os.path.join(self.path, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(self.config, fh, indent=2)

    def save_sprites(self):
        """Persist the sprite sheet to sprites.kgfx. Refuses system carts."""
        if self.system:
            raise CartridgeError("cannot edit a system cartridge; duplicate it first")
        with open(os.path.join(self.path, SPRITES_FILE), "w", encoding="utf-8") as fh:
            fh.write(self.sheet.to_hex())
        self.sheet.dirty = False

    def save_main(self, source):
        """Persist edited cartridge source to its main file. Refuses system carts."""
        if self.system:
            raise CartridgeError("cannot edit a system cartridge; duplicate it first")
        self.main_source = source
        with open(os.path.join(self.path, self.manifest.get("main", "main.py")),
                  "w", encoding="utf-8") as fh:
            fh.write(source)


def _validate(manifest):
    if not isinstance(manifest, dict):
        raise CartridgeError("manifest must be an object")
    missing = [f for f in REQUIRED_FIELDS if f not in manifest]
    if missing:
        raise CartridgeError("manifest missing fields: %s" % ", ".join(missing))
    if manifest["format"] != CART_FORMAT:
        raise CartridgeError("unsupported cart format: %r" % manifest["format"])
