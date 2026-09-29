# Moybyte licensing

**Everything you'd do as a person is free.** Run the simulator, flash the
firmware on your own board, modify it, share your fork, teach with it, and make
and sell your own carts. No license key, no registration, no fee.

**Selling hardware or a commercial product built on Moybyte needs a commercial
license.** Contact the maintainers.

**Every release becomes MIT two years after it is published.**

| Part | License |
|---|---|
| This repository: the system, firmware, tools and built-in carts | [FSL-1.1-MIT](LICENSES/FSL-1.1-MIT.md) |
| The `.moy` cart format and cart API | an open specification anyone may implement; [moy-spec](https://github.com/moybyte-org/moy-spec), including its player, is MIT |
| Carts you make | yours — nothing here claims them |
| The tools that build, key and sign a compiled cart's modules: `tools/wasm_module.py`, `tools/wasm_cart.py`, `tools/ota_sign.py` and `native/moy_wasm/moy_wasm_key.h` (`tools/wat.py` is moy-spec's, already MIT) | MIT, so a GPL cart can publish its complete build scripts |
| The Jet carts' sources (`ports/jet/teapot.moy/src/`, `ports/jet/esp88.moy/src/`) and their build tools (`tools/jet_cart.py`, `tools/vendor_jet.py`) | MIT, so anyone can start a Jet game from them |
| Files from other projects | their own licenses, listed in [THIRD_PARTY.md](THIRD_PARTY.md) |

The Functional Source License is source-available, not OSI-approved open source.
You can do anything with it except compete commercially with Moybyte (for
example, sell hardware preloaded with it), and that restriction ends for each
release two years after it ships.

"Moybyte" and the Moybyte logo are trademarks and are **not** licensed by the
above; see the Trademarks clause in the FSL text.

Copyright © 2026 Nikola Jovicic
