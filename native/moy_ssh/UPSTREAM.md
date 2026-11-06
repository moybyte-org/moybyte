# Dropbear — vendored, patched

`dropbear/` is [Dropbear](https://matt.ucc.asn.au/dropbear/dropbear.html) at
a pinned release, with Moybyte's changes applied from `patches/`. Dropbear is
MIT-style (`dropbear/LICENSE`); its bundled libtomcrypt and libtommath are
public domain (`dropbear/libtomcrypt/LICENSE`, `dropbear/libtommath/LICENSE`).
The owner chose Dropbear on 2026-11-03 (`docs/os_2026-11/system.md` §3).

**The release, the SHA-256 of the archive it is fetched as, the patch's hash
and the hash of every copied file are in
[`dropbear_vendor.json`](dropbear_vendor.json)**, written by
`make vendor-dropbear` and checked by `tests/test_dropbear_vendor.py`. The
archive is GitHub's mirror's tag archive (`mkj/dropbear`): Dropbear's
release server answers the tool's fetch with HTTP 403. The release tarball
holds the same tree, and `--tarball` takes it too, checked against its own
pinned SHA-256 (`tools/vendor_dropbear.py`).

## What is copied

`tools/vendor_dropbear.py`'s `FILES`: the server's sources the build links
(curve25519 key exchange, the ed25519 host key, chacha20-poly1305, SHA-2),
the libtomcrypt and libtommath files they call, and the headers they
include. `default_options_guard.h` is generated as Dropbear's Makefile does.

## Changing Dropbear

Never edit `dropbear/`: the next re-vendor reverts it, and the test fails
first. A change is a change to `patches/moybyte.patch` (a `git diff` over the
release's tree, its header saying what it changes and why), then
`make vendor-dropbear`.
