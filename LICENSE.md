# Moybyte licensing

Moybyte uses a split license. The short version:

**Everything you'd do as a person is free.** Run the simulator, flash the
firmware on your own board, modify it, share your fork, teach a class with it,
make and even sell your own carts. No license key, no registration, no fee.

**Selling hardware or a commercial product built on the console requires a
commercial license from us.** Contact the maintainers.

**Every release becomes plain MIT two years after its publication** — the
restriction above is temporary by design.

## What applies where

| Part | License |
|---|---|
| Repo default — the console, firmware, tools, seed carts (everything not listed below) | [FSL-1.1-MIT](LICENSES/FSL-1.1-MIT.md) (source-available; MIT after 2 years) |
| `moybyte/`, `moybyte_cli/`, `moybyte_sim/`, `moybyte_blocks/`, `examples/` — the SDK and examples | [MIT](LICENSES/MIT.md) |

The `.moy` cart format and cart API are an **open specification**: anyone may
implement a compatible runtime or tools for them, without restriction. Carts
you author are **yours** — this repository's licenses claim nothing over them.

The Functional Source License is *source-available*, not OSI-certified open
source, and we don't claim otherwise. Its practical effect: you can do anything
except commercially compete with us (e.g. sell hardware preloaded with this
console) — and even that becomes permitted, per release, two years after that
release ships.

"Moybyte" and the Moybyte logo are trademarks and are **not** licensed by the
above; see the Trademarks clause in the FSL text.

Copyright © 2026 The Moybyte Authors
