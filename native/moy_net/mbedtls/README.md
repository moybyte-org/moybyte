# mbedTLS, the sign-in's and the apps' crypto subset (VENDORED)

A copy of mbedTLS 3.6.7 as ESP-IDF v5.5.6 carries it: the AES (with its CTR
mode), GCM, CCM, SHA-256, HMAC, HMAC_DRBG, bignum, P-256 and Curve25519
sources the browser sign-in
(`native/moy_net/moy_seal.h`) and the apps' crypto rows
(`native/moy_net/moy_crypto.h`) call, under upstream's paths. The host's
library (`runtime/host/host_native.py`) and the browser's wasm
(`firmware/web_runner/build.sh`) compile it under the config
`native/moy_net/moy_seal_mbedtls.h`; a board links ESP-IDF's own build of the
same release, with its hardware AES and SHA, and never compiles this copy.

Do not edit it. `make vendor-mbedtls` (`tools/vendor_mbedtls.py`) copies it
from the boards' ESP-IDF tree and stamps `native/moy_net/mbedtls_vendor.json`;
`tests/test_mbedtls_vendor.py` fails on any edit. An ESP-IDF that moves to
another release moves this copy with it, so the host and the wasm run the
crypto the boards run. Licence: `LICENSE` (Apache-2.0, `THIRD_PARTY.md` §2.8).

`make vendor-advisories` (`tools/vendor_advisories.py`, weekly in CI) asks
OSV.dev, NVD and upstream's GitHub advisories which known vulnerabilities reach
the pinned release; an advisory it reports is bumped past or acknowledged, with
a reason, in `tools/vendor_advisories_ack.json`.
