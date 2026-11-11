#!/usr/bin/env python3
"""Vendor Espressif's elf_loader into native/moy_elf/elf_loader
(native/moy_elf/UPSTREAM.md).

    make vendor-elf-loader                     # the pinned release, fetched to .build/
    python3 tools/vendor_elf_loader.py --zip PATH
    python3 tools/vendor_elf_loader.py --check # what would change

The release is the ESP component registry's archive of espressif/elf_loader
(the component's folder of esp-iot-solution at the commit its manifest
names), checked against its pinned SHA-256. native/moy_elf/patches/
moybyte.patch is applied and FILES copied: the loader for the S3s (Xtensa)
and the P4s (RISC-V), no shared objects, no symbol tables of its own. The
hash of every copied file goes to native/moy_elf/elf_loader_vendor.json.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "native", "moy_elf")
DEST = os.path.join(MOD, "elf_loader")
PATCH = os.path.join(MOD, "patches", "moybyte.patch")
RECORD = os.path.join(MOD, "elf_loader_vendor.json")

VERSION = "2.1.0"
URL = ("https://components-file.espressif.com/components/espressif/elf_loader/%s/"
       "espressif__elf_loader-v%s.zip" % (VERSION, VERSION))
SHA256 = "525a778528a2c962807b38e9c6ca5631de5e717a07f786a2040971b1e8dfff06"
# esp-iot-solution's commit the archive was made from (its idf_component.yml).
COMMIT = "cc06c1c383ba78f913596a176a4513c07657ede5"

# What native/moy_elf/micropython.cmake compiles and includes. A file the
# build stops needing leaves this list; one it starts needing joins it.
FILES = [
    "license.txt",
    "include/esp_dlfcn.h",
    "include/esp_elf.h",
    "include/private/elf_lifecycle.h",
    "include/private/elf_platform.h",
    "include/private/elf_symbol.h",
    "include/private/elf_types.h",
    "include/private/elf_validate.h",
    "include/private/esp_dlmod.h",
    "src/arch/esp_elf_riscv.c",
    "src/arch/esp_elf_xtensa.c",
    "src/esp_elf.c",
    "src/esp_elf_adapter.c",
    "src/esp_elf_symbol.c",
    "src/esp_elf_validate.c",
]


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build_tree(archive):
    if sha(archive) != SHA256:
        raise SystemExit("vendor-elf-loader: %s is not elf_loader %s (sha256)" % (archive, VERSION))
    work = tempfile.mkdtemp(prefix="vendor_elf_loader_")
    with zipfile.ZipFile(archive) as z:
        z.extractall(work)
    r = subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "-i", PATCH],
                       cwd=work, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("vendor-elf-loader: the patch does not apply:\n%s" % r.stdout + r.stderr)
    return work


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=os.path.join(ROOT, ".build",
                                                  "espressif__elf_loader-v%s.zip" % VERSION))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.zip):
        os.makedirs(os.path.dirname(args.zip), exist_ok=True)
        print("vendor-elf-loader: fetching %s" % URL)
        part = args.zip + ".part"
        urllib.request.urlretrieve(URL, part)
        os.replace(part, args.zip)
    work = build_tree(args.zip)
    try:
        files = {}
        for rel in FILES:
            files["native/moy_elf/elf_loader/" + rel] = sha(os.path.join(work, rel))
        record = {"version": VERSION, "url": URL, "sha256": SHA256, "commit": COMMIT,
                  "patch": sha(PATCH), "files": files}
        if args.check:
            with open(RECORD) as f:
                old = json.load(f)
            drift = [p for p in files if not os.path.exists(os.path.join(ROOT, p))
                     or sha(os.path.join(ROOT, p)) != files[p]]
            if old != record or drift:
                print("vendor-elf-loader: the copy differs from the pin: %s" % (drift or "record"))
                return 1
            print("vendor-elf-loader: the copy is the pin")
            return 0
        if os.path.isdir(DEST):
            shutil.rmtree(DEST)
        for rel in FILES:
            out = os.path.join(DEST, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(work, rel), out)
        with open(RECORD, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
            f.write("\n")
        print("vendor-elf-loader: elf_loader %s, %d files" % (VERSION, len(FILES)))
        return 0
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    sys.exit(main())
