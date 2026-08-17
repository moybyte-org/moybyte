#!/usr/bin/env python3
"""Sign an OTA manifest, so a device can tell our firmware from someone else's.

The threat this closes is a network attacker, which is the realistic one for a
console on a home or school WiFi. TLS alone does not close it: MicroPython's
`ssl.wrap_socket` performs no certificate verification, and the manifest's
sha256 is no help against an attacker who supplies the manifest too -- it would
bind a forgery to itself perfectly.

So the manifest carries a signature over the fields that decide what gets
flashed, and the device checks it against a public key baked into the firmware
it was flashed with (see moy_ota.OTA_PUBLIC_KEYS). The image itself is covered
transitively: the signature commits to its sha256, and the download is rejected
unless the bytes hash to it. One signature, 4 MB of image.

RSA-2048/SHA-256, PKCS#1 v1.5. The choice is driven entirely by the VERIFIER:
`pow(sig, 65537, n)` is ~17 modular squarings of a 2048-bit int, single-digit
milliseconds on the device with no native code. Ed25519 is the better primitive
in every other way and is the wrong one here -- pure-Python scalar
multiplication on a 240 MHz MCU runs into seconds.

WHAT IS SIGNED (`canonical`): channel, version, size, sha256. Deliberately NOT
the url or the label:

  * the url is only where to fetch bytes the sha256 already pins, and leaving
    it out means a school can mirror the official manifest to a LAN host,
    rewrite the url, and have it still verify.
  * the label is cosmetic. Rewriting it buys an attacker nothing they cannot
    already do by replaying an older signed manifest, which the version check
    is what bounds.

A hand-built string rather than canonical JSON, because the verifier is
MicroPython: its `json.dumps` has no `sort_keys` and no `separators`, so
"re-serialize and compare" has no stable meaning over there.

    make ota-keygen                      # once: your key + what to paste where
    tools/ota_sign.py sign latest.json   # needs MOYBYTE_OTA_SIGNING_KEY
    tools/ota_sign.py verify latest.json  --public-key <n-hex>

Signing needs `cryptography` (the `release` extra). Verifying does not: it is
the same arithmetic the device does, in the same shape, so the tests can prove
both halves agree.
"""

import argparse
import hashlib
import json
import os
import sys

SCHEME = "moybyte-ota-v2"        # v2 added `board` -- see canonical()
EXPONENT = 65537
KEY_BITS = 2048
KEY_BYTES = KEY_BITS // 8

# The DER prefix of a PKCS#1 v1.5 DigestInfo for SHA-256 -- the ASN.1 header in
# front of the 32 digest bytes. Fixed for the algorithm, so it is a constant
# here and in the device verifier rather than an ASN.1 parser on either side.
SHA256_DER = bytes((
    0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65,
    0x03, 0x04, 0x02, 0x01, 0x05, 0x00, 0x04, 0x20,
))

ENV_KEY = "MOYBYTE_OTA_SIGNING_KEY"      # PEM, or a path to one


def canonical(manifest):
    """The exact bytes the signature covers. Mirrored by moy_ota._canonical --
    change one and you MUST change the other, which is what
    test_ota_signing.py::test_the_two_canonical_forms_agree is for.

    `board` is in here because an OTA payload is an app-partition image: without
    it, a genuinely-signed T-Deck manifest replayed at the P4's url installs an
    Xtensa image on a RISC-V chip. Rollback would recover the board, so this is
    a denial of service rather than a takeover -- which is still worth one field
    to close."""
    return ("%s\n%s\n%s\n%d\n%d\n%s" % (
        SCHEME,
        manifest.get("board") or "",
        manifest.get("channel") or "",
        int(manifest.get("version") or 0),
        int(manifest.get("size") or 0),
        (manifest.get("sha256") or "").lower(),
    )).encode()


def pkcs1_v15_block(digest, k=KEY_BYTES):
    """EMSA-PKCS1-v1_5: 00 01 FF..FF 00 <DigestInfo>. Built whole and compared
    whole on the way back -- parsing the padding is where the classic
    signature-forgery bugs live, and there is nothing here worth parsing."""
    tail = SHA256_DER + digest
    pad = k - len(tail) - 3
    if pad < 8:
        raise ValueError("key too small for a SHA-256 PKCS#1 block")
    return b"\x00\x01" + b"\xff" * pad + b"\x00" + tail


def verify(manifest, signature_hex, n, e=EXPONENT):
    """True when `signature_hex` is a valid signature over `manifest` for the
    public key (n, e). Pure arithmetic -- no crypto library, byte-identical in
    intent to the device's moy_ota._verify_sig."""
    try:
        s = int(signature_hex, 16)
    except (TypeError, ValueError):
        return False
    if not 0 < s < n:
        return False
    m = pow(s, e, n)
    k = (n.bit_length() + 7) // 8
    try:
        got = m.to_bytes(k, "big")
    except OverflowError:
        return False
    want = pkcs1_v15_block(hashlib.sha256(canonical(manifest)).digest(), k)
    # Not constant-time, and it does not need to be: this compares a public
    # value against a public value, with no secret to leak through timing.
    return got == want


# -- signing (host only; needs `cryptography`) -------------------------------

def _load_backend():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError:
        raise SystemExit(
            "signing needs the `cryptography` package:\n"
            "    .venv/bin/python -m pip install -e '.[release]'\n"
            "(verifying does not -- that is plain arithmetic)")
    return hashes, serialization, padding, rsa


def read_key(source=None):
    """The private key as PEM bytes, from `source` or $MOYBYTE_OTA_SIGNING_KEY.
    Either may be the PEM itself (how CI passes a secret) or a path to it."""
    raw = source or os.environ.get(ENV_KEY)
    if not raw:
        return None
    if "-----BEGIN" in raw:
        return raw.encode()
    try:
        with open(os.path.expanduser(raw), "rb") as f:
            return f.read()
    except OSError as exc:
        raise SystemExit("cannot read the signing key: %s" % exc)


def sign(manifest, key_pem):
    """Sign `manifest` and return the signature as lowercase hex."""
    hashes, serialization, padding, _rsa = _load_backend()
    key = serialization.load_pem_private_key(key_pem, password=None)
    sig = key.sign(canonical(manifest), padding.PKCS1v15(), hashes.SHA256())
    return sig.hex()


def public_numbers(key_pem):
    """(n, e) of the key's public half, for baking into the firmware."""
    _h, serialization, _p, _r = _load_backend()
    pub = serialization.load_pem_private_key(key_pem, password=None).public_key()
    nums = pub.public_numbers()
    return nums.n, nums.e


def keygen(path):
    """Generate a signing key at `path` (0600) and return (n, e)."""
    _h, serialization, _p, rsa = _load_backend()
    key = rsa.generate_private_key(public_exponent=EXPONENT, key_size=KEY_BITS)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    nums = key.public_key().public_numbers()
    return nums.n, nums.e


def key_constant(n, e=EXPONENT):
    """The line to paste into moy_ota.OTA_PUBLIC_KEYS."""
    h = "%x" % n
    body = "\n".join("     '%s'" % h[i:i + 64] for i in range(0, len(h), 64))
    return "OTA_PUBLIC_KEYS = (\n    (\n%s,\n     %d),\n)" % (body, e)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("keygen", help="generate a signing key (run this once)")
    g.add_argument("--out", default=os.path.expanduser("~/.moybyte-ota-signing-key.pem"))

    s = sub.add_parser("sign", help="add a `sig` to a manifest, in place")
    s.add_argument("manifest")
    s.add_argument("--key", help="PEM or a path to one (default: $%s)" % ENV_KEY)

    v = sub.add_parser("verify", help="check a manifest's signature")
    v.add_argument("manifest")
    v.add_argument("--public-key", required=True, help="modulus n, in hex")

    args = ap.parse_args(argv)

    if args.cmd == "keygen":
        if os.path.exists(args.out):
            raise SystemExit("refusing to overwrite an existing key: %s\n"
                             "(a lost signing key means every board needs a "
                             "USB reflash to trust a new one)" % args.out)
        n, e = keygen(args.out)
        print("wrote %s (mode 0600)\n" % args.out)
        print("1. Back it up somewhere you will still have in a year. If it is\n"
              "   lost, updates can no longer be signed and every deployed board\n"
              "   needs a cable to trust a replacement.\n")
        print("2. Give it to CI:\n")
        print("     gh secret set %s < %s\n" % (ENV_KEY, args.out))
        print("3. Bake the public half into the firmware -- replace\n"
              "   OTA_PUBLIC_KEYS in firmware/lilygo_t_deck_plus_mainline/"
              "device/moy_ota.py with:\n")
        print(key_constant(n, e))
        print("\n   Boards already in the field trust the key in the image they\n"
              "   are RUNNING, so this takes effect for them one update later.")
        return 0

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    if args.cmd == "sign":
        key_pem = read_key(args.key)
        if not key_pem:
            raise SystemExit("no signing key: set $%s or pass --key" % ENV_KEY)
        manifest.pop("sig", None)
        manifest["sig"] = sign(manifest, key_pem)
        with open(args.manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
            f.write("\n")
        print("signed %s (%s v%s)" % (args.manifest, manifest.get("channel"),
                                      manifest.get("version")))
        return 0

    sig = manifest.pop("sig", None)
    if not sig:
        print("NOT SIGNED: %s" % args.manifest)
        return 1
    ok = verify(manifest, sig, int(args.public_key, 16))
    print("%s: %s" % (args.manifest, "valid" if ok else "INVALID"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
