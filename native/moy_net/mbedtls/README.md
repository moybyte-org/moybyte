# mbedTLS, the sign-in's crypto subset (VENDORED)

A copy of mbedTLS 3.6.4 as ESP-IDF v5.5.1 carries it: the AES, GCM, SHA-256,
HMAC, bignum and P-256 sources the browser sign-in
(`native/moy_net/moy_seal.h`) calls, under upstream's paths. The host's
library (`runtime/host/host_native.py`) and the browser's wasm
(`firmware/web_runner/build.sh`) compile it under the config
`native/moy_net/moy_seal_mbedtls.h`; a board links ESP-IDF's own build of the
same release, with its hardware AES and SHA, and never compiles this copy.

Do not edit it. `make vendor-mbedtls` (`tools/vendor_mbedtls.py`) copies it
from the boards' ESP-IDF tree and stamps `native/moy_net/mbedtls_vendor.json`;
`tests/test_mbedtls_vendor.py` fails on any edit. An ESP-IDF that moves to
another release moves this copy with it, so the host and the wasm run the
crypto the boards run. Licence: `LICENSE` (Apache-2.0, `THIRD_PARTY.md` §2.8).
