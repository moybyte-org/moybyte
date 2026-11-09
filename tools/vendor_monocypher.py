#!/usr/bin/env python3
"""Vendor Monocypher into native/moy_net/monocypher (its README.md).

    make vendor-monocypher                     # the pinned release, fetched to .build/
    python3 tools/vendor_monocypher.py --tarball PATH
    python3 tools/vendor_monocypher.py --check # what would change

The release tarball is fetched from monocypher.org, checked against the
pinned SHA-512 (the checksum monocypher.org publishes beside it, which the
GitHub release's asset also matches) and SHA-256, and FILES is copied flat:
the core, the optional Ed25519 (SHA-512) half and the licence. Nothing is
patched. The hash of every copied file goes to
native/moy_net/monocypher_vendor.json; tests/test_monocypher_vendor.py holds
the copy to it.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "native", "moy_net", "monocypher")
RECORD = os.path.join(ROOT, "native", "moy_net", "monocypher_vendor.json")

VERSION = "4.0.3"
URL = "https://monocypher.org/download/monocypher-%s.tar.gz" % VERSION
SHA512 = ("40904ada5c7ee4f7741733e38b69a30a4b0561cbffba5ffe7c2dce16136d5402"
          "51ec0d9056ff606510d3b5b708fb8a40db7e0870d4a0b2dc17ba2bfb880f8965")
SHA256 = "8cc9bc341a66249016db9bd70e9142d8d0aef9945973744b1ac05dbc55d8ee66"
TOP = "monocypher-%s" % VERSION

# The tarball's path -> the copy's, flat: monocypher-ed25519.h includes
# "monocypher.h" beside it.
FILES = {
    "LICENCE.md": "LICENCE.md",
    "src/monocypher.c": "monocypher.c",
    "src/monocypher.h": "monocypher.h",
    "src/optional/monocypher-ed25519.c": "monocypher-ed25519.c",
    "src/optional/monocypher-ed25519.h": "monocypher-ed25519.h",
}


def digest(path, algo="sha256"):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tarball", default=os.path.join(ROOT, ".build",
                                                      "monocypher-%s.tar.gz" % VERSION))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.tarball):
        os.makedirs(os.path.dirname(args.tarball), exist_ok=True)
        print("vendor-monocypher: fetching %s" % URL)
        part = args.tarball + ".part"
        urllib.request.urlretrieve(URL, part)
        os.replace(part, args.tarball)
    if (digest(args.tarball, "sha512"), digest(args.tarball)) != (SHA512, SHA256):
        raise SystemExit("vendor-monocypher: %s is not monocypher-%s (sha512)"
                         % (args.tarball, VERSION))
    work = tempfile.mkdtemp(prefix="vendor_monocypher_")
    try:
        with tarfile.open(args.tarball) as t:
            t.extractall(work, filter="data")
        top = os.path.join(work, TOP)
        files = {"native/moy_net/monocypher/" + out: digest(os.path.join(top, src))
                 for src, out in FILES.items()}
        record = {"version": VERSION, "url": URL, "sha512": SHA512, "sha256": SHA256,
                  "files": files}
        if args.check:
            with open(RECORD) as f:
                old = json.load(f)
            drift = [p for p in files if not os.path.exists(os.path.join(ROOT, p))
                     or digest(os.path.join(ROOT, p)) != files[p]]
            if old != record or drift:
                print("vendor-monocypher: the copy differs from the pin: %s" % (drift or "record"))
                return 1
            print("vendor-monocypher: the copy is the pin")
            return 0
        os.makedirs(DEST, exist_ok=True)
        for name in os.listdir(DEST):
            if name != "README.md":
                os.remove(os.path.join(DEST, name))
        for src, out in FILES.items():
            shutil.copy2(os.path.join(top, src), os.path.join(DEST, out))
        with open(RECORD, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
            f.write("\n")
        print("vendor-monocypher: monocypher-%s, %d files" % (VERSION, len(FILES)))
        return 0
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    sys.exit(main())
