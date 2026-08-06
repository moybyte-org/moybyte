# Moybyte licensing

Moybyte uses a split license. The short version:

**Everything you'd do as a person is free.** Run the simulator, flash the
firmware on your own board, modify it, share your fork, teach a class with it,
make and even sell your own carts. No license key, no registration, no fee.

**Selling hardware or a commercial product built on Moybyte requires a
commercial license from us.** Contact the maintainers.

**Every release becomes plain MIT two years after its publication** — the
restriction above is temporary by design.

## What applies where

| Part | License |
|---|---|
| Repo default — the system, firmware, tools, seed carts (everything here) | [FSL-1.1-MIT](LICENSES/FSL-1.1-MIT.md) (source-available; MIT after 2 years) |

**The spec's player is not built here any more, and needs no grant from this
repository.** It used to be: `build.sh --spec` produced a de-branded player that
was vendored into the public cart spec
([moybyte-org/moy-spec](https://github.com/moybyte-org/moy-spec), MIT), and the
copyright holder granted those compiled artifacts under MIT so that anyone could
embed a working `.moy` player. The spec now builds its own, from its own
MIT-licensed C library (`libmoy/port/wasm`), so the intent is served without a
carve-out: **a format spec is only useful if its player is freely
redistributable**, and that player is MIT all the way down.

Some files here come from other projects and keep their own licenses — see [THIRD_PARTY.md](THIRD_PARTY.md).

The `.moy` cart format and cart API are an **open specification**: anyone may
implement a compatible runtime or tools for them, without restriction. Carts
you author are **yours** — this repository's licenses claim nothing over them.

The Functional Source License is *source-available*, not OSI-certified open
source, and we don't claim otherwise. Its practical effect: you can do anything
except commercially compete with us (e.g. sell hardware preloaded with this
system) — and even that becomes permitted, per release, two years after that
release ships.

"Moybyte" and the Moybyte logo are trademarks and are **not** licensed by the
above; see the Trademarks clause in the FSL text.

Copyright © 2026 Nikola Jovicic
