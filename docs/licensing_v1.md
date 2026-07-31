# Moybyte licensing — what's free, and what isn't

**Status:** decided.
**Scope:** how the software is licensed and what each licence lets you do.
This is the answer to "why isn't this MIT?".

---

## 1. The decision (two lines)

1. **Carts, the cart format, and the spec's player are unencumbered.** Projects
   you write, the carts you make, and community tools around them carry no
   obligation to this repository; the cart format/API is an open specification
   anyone may implement; and the compiled **web player** the spec repo ships
   (`build.sh --spec`) is granted under MIT so a `.moy` player can be embedded
   anywhere.
2. **The console and firmware are FSL-1.1-MIT** (Functional Source License):
   free for everyone to read, modify, and flash on their own hardware — but
   **selling hardware or a competing product built on it requires a commercial
   license from us**. Each release automatically becomes plain **MIT two years
   after publication**.

The licence exists to protect the **Moybyte** name and the hardware that carries
it, and nothing else.

There is **no license requirement for individuals — ever**. A kid, parent,
teacher, or hacker flashing a T-Deck, a Guition board, or anything else is the
community, and the community is the point.

## 2. What this means in practice

| You want to… | Answer |
|---|---|
| Run the simulator / console on your PC | Free, forever |
| Flash the firmware on your own board | Free, forever |
| Modify the console, share your fork | Free (keep the license notice) |
| Use it in a classroom / library | Free |
| Write, share, or **sell** carts you made | Free — carts are yours; this repo claims nothing over them |
| Implement your own runtime for `.moy` carts | Free — the cart format/API is an open spec |
| **Sell hardware preloaded with the console** | Commercial license required |
| Ship a competing commercial console product from this code | Commercial license required |
| Do any of the above with a release ≥ 2 years old | Free — it's MIT by then |

## 3. Where the exact terms live

- [`LICENSE.md`](../LICENSE.md) — the split, and which directories are MIT.
- [`LICENSES/`](../LICENSES) — the full FSL-1.1-MIT and MIT texts.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — the DCO sign-off contributions need,
  and why a source-available project asks for it.

The FSL is **source-available, not OSI-approved open source**, and we say so
plainly rather than describing it as open source. The two-year MIT conversion is
the honest form of "we will open it": it is automatic, per release, and needs no
decision from us to happen.
