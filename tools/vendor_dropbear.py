#!/usr/bin/env python3
"""Vendor Dropbear into native/moy_ssh/dropbear (native/moy_ssh/UPSTREAM.md).

    make vendor-dropbear                       # the pinned release, fetched to .build/
    python3 tools/vendor_dropbear.py --tarball PATH
    python3 tools/vendor_dropbear.py --check   # what would change

The release is fetched as GitHub's mirror's tag archive (mkj/dropbear), the
same tree as Dropbear's own release tarball under another top folder:
Dropbear's release server answers this tool's fetch with HTTP 403. --tarball
takes either, each checked against its own pinned SHA-256. native/moy_ssh/
patches/moybyte.patch is applied, default_options_guard.h is generated as
Dropbear's Makefile does (ifndef_wrapper.sh), and FILES is copied. The
hash of every copied file goes to native/moy_ssh/dropbear_vendor.json.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "native", "moy_ssh")
DEST = os.path.join(MOD, "dropbear")
PATCH = os.path.join(MOD, "patches", "moybyte.patch")
RECORD = os.path.join(MOD, "dropbear_vendor.json")

VERSION = "2026.94"
URL = "https://github.com/mkj/dropbear/archive/refs/tags/DROPBEAR_%s.tar.gz" % VERSION
SHA256 = "827d3f6e510e7554ee18d5c6a00dfee1a6a555559495e65e2e8f8d41c79eed84"
RELEASE_URL = "https://matt.ucc.asn.au/dropbear/releases/dropbear-%s.tar.bz2" % VERSION
RELEASE_SHA256 = "e098034a843699200c8c977a991fff73159735bf795d5f72ef672c41a6b1ae81"
# Each archive's top folder, by its hash.
TOPS = {SHA256: "dropbear-DROPBEAR_%s" % VERSION, RELEASE_SHA256: "dropbear-%s" % VERSION}

# What the server build compiles and includes (native/moy_ssh/micropython.cmake):
# the server, curve25519 and ed25519, chacha20-poly1305, SHA-2 and the few
# libtommath calls the buffer code makes. A file the build stops needing
# leaves this list; one it starts needing joins it.
FILES = [
    "LICENSE",
    "libtomcrypt/LICENSE",
    "libtomcrypt/src/hashes/helper/hash_memory.c",
    "libtomcrypt/src/hashes/sha2/sha256.c",
    "libtomcrypt/src/hashes/sha2/sha512.c",
    "libtomcrypt/src/headers/tomcrypt.h",
    "libtomcrypt/src/headers/tomcrypt_argchk.h",
    "libtomcrypt/src/headers/tomcrypt_cfg.h",
    "libtomcrypt/src/headers/tomcrypt_cipher.h",
    "libtomcrypt/src/headers/tomcrypt_custom.h",
    "libtomcrypt/src/headers/tomcrypt_dropbear.h",
    "libtomcrypt/src/headers/tomcrypt_hash.h",
    "libtomcrypt/src/headers/tomcrypt_mac.h",
    "libtomcrypt/src/headers/tomcrypt_macros.h",
    "libtomcrypt/src/headers/tomcrypt_math.h",
    "libtomcrypt/src/headers/tomcrypt_misc.h",
    "libtomcrypt/src/headers/tomcrypt_pk.h",
    "libtomcrypt/src/headers/tomcrypt_pkcs.h",
    "libtomcrypt/src/headers/tomcrypt_prng.h",
    "libtomcrypt/src/mac/hmac/hmac_done.c",
    "libtomcrypt/src/mac/hmac/hmac_init.c",
    "libtomcrypt/src/mac/hmac/hmac_process.c",
    "libtomcrypt/src/mac/poly1305/poly1305.c",
    "libtomcrypt/src/misc/base64/base64_decode.c",
    "libtomcrypt/src/misc/base64/base64_encode.c",
    "libtomcrypt/src/misc/crypt/crypt_argchk.c",
    "libtomcrypt/src/misc/crypt/crypt_cipher_descriptor.c",
    "libtomcrypt/src/misc/crypt/crypt_find_cipher.c",
    "libtomcrypt/src/misc/crypt/crypt_find_hash.c",
    "libtomcrypt/src/misc/crypt/crypt_hash_descriptor.c",
    "libtomcrypt/src/misc/crypt/crypt_hash_is_valid.c",
    "libtomcrypt/src/misc/zeromem.c",
    "libtomcrypt/src/stream/chacha/chacha_crypt.c",
    "libtomcrypt/src/stream/chacha/chacha_ivctr64.c",
    "libtomcrypt/src/stream/chacha/chacha_keystream.c",
    "libtomcrypt/src/stream/chacha/chacha_setup.c",
    "libtommath/LICENSE",
    "libtommath/bn_mp_clamp.c",
    "libtommath/bn_mp_clear.c",
    "libtommath/bn_mp_copy.c",
    "libtommath/bn_mp_count_bits.c",
    "libtommath/bn_mp_div_2d.c",
    "libtommath/bn_mp_from_ubin.c",
    "libtommath/bn_mp_grow.c",
    "libtommath/bn_mp_init.c",
    "libtommath/bn_mp_init_copy.c",
    "libtommath/bn_mp_init_size.c",
    "libtommath/bn_mp_lshd.c",
    "libtommath/bn_mp_mod_2d.c",
    "libtommath/bn_mp_mul_2d.c",
    "libtommath/bn_mp_rshd.c",
    "libtommath/bn_mp_to_ubin.c",
    "libtommath/bn_mp_ubin_size.c",
    "libtommath/bn_mp_zero.c",
    "libtommath/tommath.h",
    "libtommath/tommath_class.h",
    "libtommath/tommath_private.h",
    "libtommath/tommath_superclass.h",
    "src/agentfwd.h",
    "src/algo.h",
    "src/atomicio.h",
    "src/auth.h",
    "src/bignum.c",
    "src/bignum.h",
    "src/buffer.c",
    "src/buffer.h",
    "src/chachapoly.c",
    "src/chachapoly.h",
    "src/channel.h",
    "src/chansession.h",
    "src/circbuffer.c",
    "src/circbuffer.h",
    "src/common-algo.c",
    "src/common-channel.c",
    "src/common-chansession.c",
    "src/common-kex.c",
    "src/common-runopts.c",
    "src/common-session.c",
    "src/compat.c",
    "src/compat.h",
    "src/crypto_desc.h",
    "src/curve25519.c",
    "src/curve25519.h",
    "src/dbctype.c",
    "src/dbctype.h",
    "src/dbhelpers.c",
    "src/dbhelpers.h",
    "src/dbmalloc.c",
    "src/dbmalloc.h",
    "src/dbrandom.c",
    "src/dbrandom.h",
    "src/dbutil.c",
    "src/dbutil.h",
    "src/debug.h",
    "src/default_options_guard.h",
    "src/dh_groups.h",
    "src/dss.h",
    "src/ecc.h",
    "src/ecdsa.h",
    "src/ed25519.c",
    "src/ed25519.h",
    "src/fake-rfc2553.c",
    "src/fake-rfc2553.h",
    "src/forward.h",
    "src/fuzz.h",
    "src/gcm.h",
    "src/gendss.h",
    "src/gened25519.c",
    "src/gened25519.h",
    "src/genrsa.h",
    "src/gensignkey.c",
    "src/gensignkey.h",
    "src/includes.h",
    "src/kex-x25519.c",
    "src/kex.h",
    "src/list.c",
    "src/list.h",
    "src/listener.c",
    "src/listener.h",
    "src/loginrec.h",
    "src/ltc_prng.h",
    "src/mlkem768.h",
    "src/netio.c",
    "src/netio.h",
    "src/options.h",
    "src/packet.c",
    "src/packet.h",
    "src/process-packet.c",
    "src/queue.c",
    "src/queue.h",
    "src/rsa.h",
    "src/runopts.h",
    "src/service.h",
    "src/session.h",
    "src/signkey.c",
    "src/signkey.h",
    "src/sk-ecdsa.h",
    "src/sk-ed25519.h",
    "src/sntrup761.h",
    "src/ssh.h",
    "src/sshpty.h",
    "src/svr-auth.c",
    "src/svr-authpasswd.c",
    "src/svr-authpubkey.c",
    "src/svr-chansession.c",
    "src/svr-forward.c",
    "src/svr-kex.c",
    "src/svr-runopts.c",
    "src/svr-service.c",
    "src/svr-session.c",
    "src/sysoptions.h",
    "src/termcodes.h",
    "src/x11fwd.h",
]


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def guard(text):
    """default_options.h with every `#define X ...` wrapped in #ifndef X."""
    out = ["/*\n > > > Do not edit this file (default_options_guard.h) < < <\n"
           "Generated from default_options.h by tools/vendor_dropbear.py\n*/\n"]
    for line in text.splitlines(True):
        m = re.match(r"^( *#define ([^ ]+) .*)", line)
        if m:
            out.append("#ifndef %s\n%s\n#endif\n" % (m.group(2), m.group(1).rstrip("\n")))
        else:
            out.append(line)
    return "".join(out)


def build_tree(tarball):
    folder = TOPS.get(sha(tarball))
    if folder is None:
        raise SystemExit("vendor-dropbear: %s is not dropbear-%s (sha256)" % (tarball, VERSION))
    work = tempfile.mkdtemp(prefix="vendor_dropbear_")
    with tarfile.open(tarball) as t:
        t.extractall(work, filter="data")
    top = os.path.join(work, folder)
    r = subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "-i", PATCH],
                       cwd=top, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("vendor-dropbear: the patch does not apply:\n%s" % r.stdout + r.stderr)
    src = os.path.join(top, "src")
    with open(os.path.join(src, "default_options.h")) as f:
        g = guard(f.read())
    with open(os.path.join(src, "default_options_guard.h"), "w") as f:
        f.write(g)
    return work, top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tarball", default=os.path.join(ROOT, ".build",
                                                      "dropbear-DROPBEAR_%s.tar.gz" % VERSION))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.tarball):
        os.makedirs(os.path.dirname(args.tarball), exist_ok=True)
        print("vendor-dropbear: fetching %s" % URL)
        part = args.tarball + ".part"
        urllib.request.urlretrieve(URL, part)
        os.replace(part, args.tarball)
    work, top = build_tree(args.tarball)
    try:
        files = {}
        for rel in FILES:
            files["native/moy_ssh/dropbear/" + rel] = sha(os.path.join(top, rel))
        record = {"version": VERSION, "url": URL, "sha256": SHA256,
                  "patch": sha(PATCH), "files": files}
        if args.check:
            with open(RECORD) as f:
                old = json.load(f)
            drift = [p for p in files if not os.path.exists(os.path.join(ROOT, p))
                     or sha(os.path.join(ROOT, p)) != files[p]]
            if old != record or drift:
                print("vendor-dropbear: the copy differs from the pin: %s" % (drift or "record"))
                return 1
            print("vendor-dropbear: the copy is the pin")
            return 0
        if os.path.isdir(DEST):
            shutil.rmtree(DEST)
        for rel in FILES:
            out = os.path.join(DEST, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(top, rel), out)
        with open(RECORD, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
            f.write("\n")
        print("vendor-dropbear: dropbear-%s, %d files" % (VERSION, len(FILES)))
        return 0
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    sys.exit(main())
