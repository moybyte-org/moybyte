#!/usr/bin/env python3
"""Vendor mbedTLS's crypto subset into native/moy_net/mbedtls (its README.md).

    make vendor-mbedtls                        # from the boards' ESP-IDF tree
    python3 tools/vendor_mbedtls.py --src PATH # an mbedTLS checkout
    python3 tools/vendor_mbedtls.py --check    # what would change

A board links the ESP-IDF's own mbedTLS (its AES, SHA and, on a P4, ECC in
hardware); the host's library and the browser's wasm have none, so they
compile this copy of the same release: what native/moy_net/moy_seal.c calls
(AES, GCM, SHA-256, HMAC, HMAC_DRBG, the bignum and P-256), under the config
native/moy_net/moy_seal_mbedtls.h. The hash of every copied file goes to
native/moy_net/mbedtls_vendor.json, which tests/test_mbedtls_vendor.py holds
the copy to.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "native", "moy_net", "mbedtls")
RECORD = os.path.join(ROOT, "native", "moy_net", "mbedtls_vendor.json")
IDF_SRC = os.path.join(ROOT, "firmware", "esp32_p4_wifi6_touch_lcd_7b", ".build",
                       "esp-idf", "components", "mbedtls", "mbedtls")
VERSION = "3.6.4"

LIBRARY = ["aes", "block_cipher", "bignum", "bignum_core", "constant_time", "ecp",
           "ecp_curves", "gcm", "hmac_drbg", "md", "platform_util", "sha256"]
HEADERS = ["aes", "bignum", "block_cipher", "build_info", "check_config", "cipher",
           "config_adjust_legacy_crypto", "config_adjust_ssl", "config_adjust_x509",
           "constant_time", "ecp", "error", "gcm", "hmac_drbg", "md", "md5", "platform",
           "platform_util", "private_access", "ripemd160", "sha1", "sha256", "sha3",
           "sha512", "threading"]
INTERNAL = ["alignment", "bignum_core", "bignum_internal", "bignum_mod",
            "block_cipher_internal", "bn_mul", "common", "constant_time_impl",
            "constant_time_internal", "ctr", "ecp_internal_alt", "ecp_invasive", "md_wrap"]
FILES = (["LICENSE"]
         + ["include/mbedtls/%s.h" % h for h in HEADERS]
         + ["library/%s.h" % h for h in INTERNAL]
         + ["library/%s.c" % c for c in LIBRARY])


def version(src):
    with open(os.path.join(src, "include", "mbedtls", "build_info.h")) as f:
        m = re.search(r'MBEDTLS_VERSION_STRING\s+"([^"]+)"', f.read())
    return m.group(1) if m else None


def commit(src):
    try:
        return subprocess.check_output(["git", "-C", src, "rev-parse", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default=IDF_SRC)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    got = version(a.src)
    if got != VERSION:
        sys.exit("vendor_mbedtls: %s is mbedTLS %s, the pin is %s" % (a.src, got, VERSION))
    record = {"upstream": {"version": VERSION, "commit": commit(a.src)}, "files": {}}
    changed = []
    for rel in FILES:
        src = os.path.join(a.src, rel)
        dst = os.path.join(DEST, rel)
        h = digest(src)
        record["files"]["native/moy_net/mbedtls/" + rel] = h
        if not os.path.exists(dst) or digest(dst) != h:
            changed.append(rel)
            if not a.check:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
    if a.check:
        print("\n".join(changed) or "up to date")
        return 1 if changed else 0
    with open(RECORD, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")
    print("vendored mbedTLS %s: %d files, %d changed" % (VERSION, len(FILES), len(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
