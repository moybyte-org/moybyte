#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Build a per-chip WebAssembly module a board will load: .wasm -> .aot + key.

    python3 tools/wasm_module.py build cart.wasm --chip esp32s3 -o cart_s3.aot
    python3 tools/wasm_module.py hello --chip esp32p4 -o hello_p4.aot
    python3 tools/wasm_module.py key --chip esp32s3          # print the key tail
    python3 tools/wasm_module.py compilers                   # where wamrc is, by hash
    python3 tools/wasm_module.py verify cart_s3.aot --chip esp32s3

A per-architecture module is native code, so a board runs one only if its
provenance key says which wasm it came from, which COMPILED-CODE FORMAT
VERSION it was built against and with which compiler flags
(docs/wasm_tier_plan_2026-09.md, native/moy_wasm/README.md). This tool is the
one place a key is written:

  1. hash the canonical .wasm (the key's `wasm` line);
  2. append the key to a copy as the custom section `moybyte.key`;
  3. run wamrc with exactly the flags the key states, telling it to carry that
     section into the .aot (--emit-custom-sections).

The flags are not written here. They are the chip's block in
native/moy_wasm/moy_wasm_key.h -- the same text the board's check compares
against -- and the format version is that header's `MOY_WASM_FORMAT_VERSION`.
A module this tool builds therefore carries the key the board wants by
construction, and a module anything else builds is refused.

A module's key no longer names the fork commit (owner, 2026-09-30, ESP 88):
`native/moy_wasm/wasm_format_version.json` names the vendored files and the
compiler pins that make up the format, and `format_stamp()` below hashes them
the same way `tests/test_wasm_format_version.py` does, so a change to any of
them fails that test unless MOY_WASM_FORMAT_VERSION moves with it. A board a
cart's module does not match by chip+format is simply absent to it -- it
plays on the interpreter instead of refusing (native/moy_wasm/README.md).

EVERY MODULE IS SIGNED, the way an OTA manifest is: RSA, PKCS#1 v1.5, SHA-256,
with the OTA signing key (`tools/ota_sign.py`: $MOYBYTE_OTA_SIGNING_KEY, a PEM
or a path to one, else the key `make ota-keygen` writes), over a text naming
the chip and the module's length and sha256 -- so the signature covers every
byte of the module, its key section included. The signature rides after the
module in the same file; the layout and the text are native/moy_wasm/
moy_wasm_key.h's, which this tool reads, and the board checks it with
moy_ota.verify_sig against the keys its image trusts before the runtime sees
a byte. `--unsigned` builds a module with no signature, which a board runs
only while its owner has Settings -> UNKNOWN SOURCES on -- the way to run a
cart you rebuilt from its source on your own console; a module signed with a
key the board does not trust is refused whatever that switch says.

THE COMPILERS are fixed binaries, pinned by sha256 below: one wamrc for both
targets, built by experiments/wasm_aot/toolchain/build_wamrc_xtensa.sh
(Espressif's LLVM with its Xtensa and RISC-V backends, the fork's
wamr-compiler at the commit the runtime is vendored from), carried under
each target's name. They are looked up in
experiments/wasm_aot/toolchain/dist/ (gitignored), fetched from the fork's
release assets into it when absent, and refused when their hash is not the
pin. $MOYBYTE_WAMRC_<TARGET> points at another binary for an
experiment, and says so. No module is ever committed; tests build theirs.

THE HELLO MODULE is experiments/wasm_aot/core6502.c, the spike's 6502 core,
compiled with clang's wasm32 backend and linked by a wasm-ld (wasi-sdk's, or
rust-lld's -flavor wasm, which is what the spike used).
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import ota_sign  # noqa: E402

KEY_HEADER = os.path.join(ROOT, "native", "moy_wasm", "moy_wasm_key.h")
MOY_OTA = os.path.join(ROOT, "device", "moy_ota.py")
VENDOR_STAMP = os.path.join(ROOT, "native", "moy_wasm", "wamr_vendor.json")
FORMAT_STAMP = os.path.join(ROOT, "native", "moy_wasm", "wasm_format_version.json")
SPIKE = os.path.join(ROOT, "experiments", "wasm_aot")
DIST = os.path.join(SPIKE, "toolchain", "dist")
HELLO_SRC = os.path.join(SPIKE, "core6502.c")
MISALIGNED_SRC = os.path.join(ROOT, "tests", "fixtures", "wasm", "misaligned.c")

KEY_SECTION = "moybyte.key"
KEY_MAGIC = "moybyte-aot 1\n"

# The compilers, by the key's `target` field. `sha256` is the pin; `url` is
# where a missing binary is fetched from (the fork's release assets).
RELEASE = "https://github.com/moybyte-org/wasm-micro-runtime/releases/download/wamrc-2.4.5-moybyte-3"
COMPILERS = {
    "xtensa": {
        "file": "wamrc-xtensa",
        "sha256": "56622550bcf42dde5f9f52305e4fe396ca0be357b698203fad55b369e985e85d",
        "url": RELEASE + "/wamrc-xtensa",
    },
    "riscv32": {
        "file": "wamrc-riscv32",
        "sha256": "56622550bcf42dde5f9f52305e4fe396ca0be357b698203fad55b369e985e85d",
        "url": RELEASE + "/wamrc-riscv32",
    },
}


class ToolError(RuntimeError):
    pass


# -- the key ------------------------------------------------------------------


def targets(header=KEY_HEADER):
    """{chip: [(field, value), ...]} from the key header's blocks, in order."""
    text = open(header, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'#define MOY_WASM_KEY_(\w+) \\\n((?:[ \t]+"[^"\n]*"[ \t]*\\?\n)+)', text):
        chip = m.group(1).lower()
        fields = []
        for lit in re.findall(r'"([^"\n]*)"', m.group(2)):
            assert lit.endswith("\\n"), lit
            name, value = lit[:-2].split(" ", 1)
            fields.append((name, value))
        out[chip] = fields
    return out


def fork_commit(stamp=VENDOR_STAMP):
    """The fork commit the vendored runtime was copied from -- diagnostic only
    (`moy_wasm.FORK`) since 2026-09-30: it plays no part in the key (ESP 88,
    "A cart survives its firmware")."""
    with open(stamp, encoding="utf-8") as f:
        return json.load(f)["upstream"]["commit"]


def format_version(header=KEY_HEADER):
    """This tree's compiled-code format version, MOY_WASM_FORMAT_VERSION in
    moy_wasm_key.h -- what the key's `format` line names."""
    return _define("MOY_WASM_FORMAT_VERSION", header)


def format_stamp(stamp=FORMAT_STAMP):
    """The sha256 that `format_version()` is supposed to cover: every file
    wasm_format_version.json's `covers` names (path then bytes, in order) plus
    each `compilers` target's pinned sha256 from COMPILERS above, in that
    order. `tests/test_wasm_format_version.py` fails when this differs from
    the stamp recorded in the file, which is the guard the build step asks
    for: a change to the AOT loader/runtime ABI or the pinned compiler that
    does not also move MOY_WASM_FORMAT_VERSION (and re-record this stamp)."""
    with open(stamp, encoding="utf-8") as f:
        spec = json.load(f)
    h = hashlib.sha256()
    for rel in spec["covers"]:
        h.update(rel.encode())
        with open(os.path.join(ROOT, rel), "rb") as f:
            h.update(f.read())
    for target in spec["compilers"]:
        h.update(target.encode())
        h.update(COMPILERS[target]["sha256"].encode())
    return h.hexdigest()


def fields(chip, override=None):
    """The chip's key fields in order, with `override` ({name: value}) applied
    -- how a test builds a module with flags this board's build does not want."""
    blocks = targets()
    if chip not in blocks:
        raise ToolError("no key block for %r in moy_wasm_key.h (have %s)"
                        % (chip, ", ".join(sorted(blocks))))
    out = list(blocks[chip])
    for name, value in (override or {}).items():
        idx = [i for i, (n, _v) in enumerate(out) if n == name]
        if not idx:
            raise ToolError("no key field %r" % name)
        out[idx[0]] = (name, value)
    return out


def key_tail(chip, format=None, override=None):
    """Everything after the key's wasm line, exactly as the board compares it.
    `format` is a compiled-code format version to write instead of this
    tree's own (a module the board must refuse, like `override`)."""
    lines = ["format %s" % (format or format_version())]
    lines += ["%s %s" % kv for kv in fields(chip, override)]
    return "\n".join(lines) + "\n"


def key_text(wasm, chip, format=None, override=None):
    return (KEY_MAGIC + "wasm %s\n" % hashlib.sha256(wasm).hexdigest()
            + key_tail(chip, format, override))


def key_matches(data, wasm, chip):
    """True when the module file `data` carries exactly the key a board of
    `chip` built from this tree wants for `wasm`: that .wasm's hash, this
    tree's compiled-code format version and this chip's compiler flags."""
    return key_text(wasm, chip).encode() in data


# -- the signature ----------------------------------------------------------------


def _define(name, header=KEY_HEADER):
    text = open(header, encoding="utf-8").read()
    m = re.search(r'#define %s "([^"\n]*)"' % name, text)
    if not m:
        raise ToolError("no %s in %s" % (name, header))
    return m.group(1)


def sig_magic():
    return _define("MOY_WASM_SIG_MAGIC").encode()


def sig_scheme():
    return _define("MOY_WASM_SIG_SCHEME")


def signed_text(module, chip):
    """What a module's signature covers (moy_wasm_key.h)."""
    return ("%s\n%s\n%d\n%s" % (sig_scheme(), chip, len(module),
                                 hashlib.sha256(module).hexdigest())).encode()


def attach(module, signature):
    """The module file: `module`, then `signature` (bytes), its length and the
    magic."""
    return (module + signature + len(signature).to_bytes(4, "little")
            + sig_magic())


def split(data):
    """(module, signature bytes) of a signed module file, or (data, None)
    when it carries no signature trailer."""
    magic = sig_magic()
    if len(data) < len(magic) + 4 or not data.endswith(magic):
        return data, None
    k = int.from_bytes(data[-len(magic) - 4:-len(magic)], "little")
    end = len(data) - len(magic) - 4
    if not 64 <= k <= 1024 or k > end:
        return data, b""
    return data[:end - k], data[end - k:end]


def signing_key(source=None):
    """The OTA signing key as PEM bytes: `source`, $MOYBYTE_OTA_SIGNING_KEY,
    or the file `make ota-keygen` writes; None when there is none."""
    key = ota_sign.read_key(source)
    if key is None and os.path.isfile(ota_sign.DEFAULT_KEY):
        key = ota_sign.read_key(ota_sign.DEFAULT_KEY)
    return key


def sign(module, chip, key):
    """The signed module file for `module` (bytes) built for `chip`. `key` is
    the signing key as PEM bytes, or a callable taking the signed text and
    returning the signature's bytes (how a test signs with a throwaway key)."""
    text = signed_text(module, chip)
    sig = key(text) if callable(key) else bytes.fromhex(ota_sign._sign_bytes(text, key))
    return attach(module, sig)


def _verifier():
    """device/moy_ota.py's verify_sig: the body and the keys a board checks a
    module with."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("moy_ota_wasm_module", MOY_OTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.verify_sig


def verify(data, chip, keys=None, unknown_sources=False):
    """None when `data` is a module file a board of `chip` trusting `keys`
    (default: the image's OTA_PUBLIC_KEYS) lets through to its provenance
    check, otherwise the refusal the board gives -- `verify_module` in
    native/moy_wasm/modmoy_wasm.c. `unknown_sources` is the owner's setting:
    on, a module with no signature passes; a signature that is present must
    verify either way."""
    module, sig = split(data)
    if sig is None:
        return None if unknown_sources else "unsigned module"
    if not sig:
        return "malformed signature"
    if not _verifier()(signed_text(module, chip), sig.hex(), keys):
        return "bad signature"
    return None


def wamrc_flags(chip, override=None):
    """The wamrc command line the key's fields state."""
    f = dict(fields(chip, override))
    args = ["--target=%s" % f["target"]]
    if f["cpu"] != "-":
        args.append("--cpu=%s" % f["cpu"])
    if f["abi"] != "-":
        args.append("--target-abi=%s" % f["abi"])
    if f["features"] != "-":
        args.append("--cpu-features=%s" % f["features"])
    args += ["--opt-level=%s" % f["opt"], "--size-level=%s" % f["size"],
             "--bounds-checks=%s" % f["bounds"],
             "--stack-bounds-checks=%s" % f["stack-bounds"]]
    if f["xip"] == "1":
        args.append("--xip")
    return args


# -- the wasm container --------------------------------------------------------


def _leb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_leb(data, i):
    n = shift = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def sections(wasm):
    """[(id, name or None, payload)] of a wasm binary."""
    if wasm[:8] != b"\0asm\x01\0\0\0":
        raise ToolError("not a wasm binary (version 1)")
    out, i = [], 8
    while i < len(wasm):
        sid = wasm[i]
        size, j = _read_leb(wasm, i + 1)
        body = wasm[j:j + size]
        name = None
        if sid == 0:
            nlen, k = _read_leb(body, 0)
            name = body[k:k + nlen].decode()
            body = body[k + nlen:]
        out.append((sid, name, body))
        i = j + size
    return out


def with_custom_section(wasm, name, payload):
    """`wasm` with one more custom section appended."""
    if any(sid == 0 and n == name for sid, n, _b in sections(wasm)):
        raise ToolError("the wasm already carries a %r section" % name)
    nb = name.encode()
    body = _leb(len(nb)) + nb + payload
    return wasm + b"\x00" + _leb(len(body)) + body


# -- the compilers --------------------------------------------------------------


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compiler(target, fetch=True):
    """Path to the pinned wamrc for `target`, fetched into DIST if absent."""
    override = os.environ.get("MOYBYTE_WAMRC_%s" % target.upper())
    if override:
        print("wasm_module: using $MOYBYTE_WAMRC_%s=%s -- not the pinned "
              "compiler" % (target.upper(), override), file=sys.stderr)
        return override
    pin = COMPILERS[target]
    path = os.path.join(DIST, pin["file"])
    if not os.path.isfile(path):
        if not fetch:
            raise ToolError("no %s in %s" % (pin["file"], DIST))
        os.makedirs(DIST, exist_ok=True)
        tmp = path + ".part"
        try:
            urllib.request.urlretrieve(pin["url"], tmp)
        except Exception as exc:  # noqa: BLE001
            raise ToolError("no %s in %s and fetching %s failed: %s"
                            % (pin["file"], DIST, pin["url"], exc))
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    got = sha256_file(path)
    if got != pin["sha256"]:
        raise ToolError("%s hashes to %s, the pin is %s -- not the compiler "
                        "this tree names" % (path, got, pin["sha256"]))
    return path


# -- building -------------------------------------------------------------------


def build(wasm, chip, out, key=True, format=None, override=None, sign_with=None,
          signed=True):
    """Compile `wasm` (bytes) for `chip` into `out`, signed with `sign_with`
    (sign()'s `key`; default signing_key()). Returns the key text.

    `format` and `override` build a module this tree's boards must REFUSE: a
    key naming another compiled-code format version, or flags (and a key
    saying so) the board's build does not want. `signed=False` leaves the
    signature off: a module a board runs only while its owner has Unknown
    sources on."""
    pem = None
    if signed:
        pem = sign_with or signing_key()
        if pem is None:
            raise ToolError("no signing key: set $%s or pass --key (a board "
                            "trusts only the keys its image carries), or build "
                            "--unsigned, a module a board runs only with "
                            "Unknown sources on" % ota_sign.ENV_KEY)
    target = dict(fields(chip, override))["target"]
    text = key_text(wasm, chip, format, override)
    src = with_custom_section(wasm, KEY_SECTION, text.encode()) if key else wasm
    with tempfile.TemporaryDirectory() as tmp:
        wpath = os.path.join(tmp, "in.wasm")
        with open(wpath, "wb") as f:
            f.write(src)
        cmd = [compiler(target)] + wamrc_flags(chip, override)
        if key:
            cmd.append("--emit-custom-sections=%s" % KEY_SECTION)
        cmd += ["-o", out, wpath]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(out):
            raise ToolError("wamrc failed (%s):\n%s%s"
                            % (" ".join(cmd), r.stdout, r.stderr))
    if pem is not None:
        with open(out, "rb") as f:
            module = f.read()
        with open(out, "wb") as f:
            f.write(sign(module, chip, pem))
    return text if key else None


def _linker():
    """A wasm linker as an argv prefix, and the env it needs."""
    wasi = os.path.join(SPIKE, "toolchain", "wasi-sdk", "bin", "wasm-ld")
    if os.path.isfile(wasi):
        return [wasi], None
    found = shutil.which("wasm-ld")
    if found:
        return [found], None
    for lld in sorted(glob.glob(os.path.expanduser(
            "~/.rustup/toolchains/*/lib/rustlib/*/bin/rust-lld"))):
        libdir = os.path.join(lld.split("/lib/rustlib/")[0], "lib")
        env = dict(os.environ, LD_LIBRARY_PATH=libdir)
        return [lld, "-flavor", "wasm"], env
    raise ToolError("no wasm linker: wasi-sdk (experiments/wasm_aot/toolchain), "
                    "wasm-ld on PATH, or a rustup toolchain's rust-lld")


def hello_wasm():
    """The spike's 6502 core as a wasm module (bytes)."""
    return _c_wasm(HELLO_SRC)


def misaligned_wasm():
    """The misaligned-access guard (tests/fixtures/wasm/misaligned.c) as a
    wasm module (bytes): its `check` export counts the misaligned loads and
    stores that read or wrote the wrong bytes."""
    return _c_wasm(MISALIGNED_SRC)


def _c_wasm(src):
    """One freestanding C file as a wasm module (bytes)."""
    clang = os.path.join(SPIKE, "toolchain", "wasi-sdk", "bin", "clang")
    if not os.path.isfile(clang):
        clang = shutil.which("clang")
    if not clang:
        raise ToolError("no clang with a wasm32 backend")
    link, env = _linker()
    with tempfile.TemporaryDirectory() as tmp:
        stem = os.path.splitext(os.path.basename(src))[0]
        obj, out = os.path.join(tmp, stem + ".o"), os.path.join(tmp, stem + ".wasm")
        for cmd, e in (([clang, "--target=wasm32", "-O2", "-ffreestanding",
                         "-fno-builtin", "-c", "-o", obj, src], None),
                       (link + ["--no-entry", "--export-dynamic", "-o", out, obj], env)):
            r = subprocess.run(cmd, capture_output=True, text=True, env=e)
            if r.returncode != 0:
                raise ToolError("%s failed:\n%s" % (cmd[0], r.stderr))
        with open(out, "rb") as f:
            return f.read()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="compile a .wasm for a chip")
    b.add_argument("wasm")
    h = sub.add_parser("hello", help="build the hello module (core6502) for a chip")
    for p in (b, h):
        p.add_argument("--chip", required=True, choices=sorted(targets()))
        p.add_argument("-o", "--out", required=True)
        p.add_argument("--no-key", action="store_true",
                       help="leave the key out (a module the board must refuse)")
        p.add_argument("--format", help="write another compiled-code format "
                       "version into the key (a module the board must refuse)")
        p.add_argument("--field", action="append", default=[], metavar="NAME=VALUE",
                       help="build with another value for a key field, e.g. "
                       "size=3 (a module the board must refuse); repeatable")
        p.add_argument("--key", dest="signing_key",
                       help="the signing key, PEM or a path (default: $%s, then "
                       "%s)" % (ota_sign.ENV_KEY, ota_sign.DEFAULT_KEY))
        p.add_argument("--unsigned", action="store_true",
                       help="leave the signature off (a module a board runs "
                       "only with Unknown sources on)")
    k = sub.add_parser("key", help="print the key tail a chip's build wants")
    k.add_argument("--chip", required=True, choices=sorted(targets()))
    v = sub.add_parser("verify", help="check a module's signature as a board would")
    v.add_argument("aot")
    v.add_argument("--chip", required=True, choices=sorted(targets()))
    v.add_argument("--unknown-sources", action="store_true",
                   help="answer as a board with Unknown sources on")
    sub.add_parser("compilers", help="locate and verify the pinned compilers")
    fs = sub.add_parser("format-stamp", help="print or refresh the compiled-code "
                        "format version's stamp (wasm_format_version.json)")
    fs.add_argument("--write", action="store_true",
                    help="rewrite wasm_format_version.json's stamp in place")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "key":
            sys.stdout.write(key_tail(args.chip))
            return 0
        if args.cmd == "format-stamp":
            got = format_stamp()
            if args.write:
                with open(FORMAT_STAMP, encoding="utf-8") as f:
                    spec = json.load(f)
                spec["stamp"] = got
                with open(FORMAT_STAMP, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(spec, f, indent=2)
                    f.write("\n")
                print("%s: wrote stamp %s (MOY_WASM_FORMAT_VERSION %s -- bump "
                      "it too if this is an ABI change)"
                      % (FORMAT_STAMP, got[:16], format_version()))
            else:
                print(got)
            return 0
        if args.cmd == "verify":
            with open(args.aot, "rb") as f:
                data = f.read()
            why = verify(data, args.chip, unknown_sources=args.unknown_sources)
            ok = ("signed, trusted" if split(data)[1] else
                  "unsigned, loads with Unknown sources on")
            print("%s: %s" % (args.aot, ok if why is None
                              else "REFUSED: %s" % why))
            return 0 if why is None else 1
        if args.cmd == "compilers":
            for target in sorted(COMPILERS):
                print("%-8s %s" % (target, compiler(target)))
            return 0
        wasm = hello_wasm() if args.cmd == "hello" else open(args.wasm, "rb").read()
        override = dict(f.split("=", 1) for f in args.field)
        pem = signing_key(args.signing_key) if args.signing_key else None
        text = build(wasm, args.chip, args.out, key=not args.no_key,
                     format=args.format, override=override, sign_with=pem,
                     signed=not args.unsigned)
        print("%s: %d bytes%s" % (args.out, os.path.getsize(args.out),
                                  "" if text is None else ", wasm %s"
                                  % hashlib.sha256(wasm).hexdigest()[:16]))
        return 0
    except ToolError as exc:
        print("wasm_module: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
