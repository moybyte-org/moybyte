# Monocypher, the apps' Ed25519 (VENDORED)

A copy of Monocypher 4.0.3: the core (`monocypher.c`/`.h`) and its optional
Ed25519 half (`monocypher-ed25519.c`/`.h`, EdDSA over SHA-512 as RFC 8032
defines it), flattened out of the release's `src/` and `src/optional/`. The
apps' crypto rows `moybyte.ed25519_public`, `ed25519_sign` and
`ed25519_verify` (`native/moy_net/moy_crypto.h`) call it; mbedTLS 3.x has no
EdDSA. Every tier compiles this one copy: a board's image
(`native/moy_net/micropython.cmake`), the desktop and the browser MicroPython
(`micropython.mk`) and the host's library (`runtime/host/seal_binding.py`).

Do not edit it. `make vendor-monocypher` (`tools/vendor_monocypher.py`)
fetches the release tarball from monocypher.org, checks it against the
SHA-512 monocypher.org publishes beside it, copies these files and stamps
`native/moy_net/monocypher_vendor.json`; `tests/test_monocypher_vendor.py`
fails on any edit. Licence: `LICENCE.md` (CC0-1.0 OR BSD-2-Clause,
`THIRD_PARTY.md` §2.9).
