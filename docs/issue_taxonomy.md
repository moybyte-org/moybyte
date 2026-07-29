# Issue taxonomy — labels & the readiness ladder

This is the label scheme for `moybyte-org/moybyte` issues. The goal is to tell **at a
glance what can be worked on**: every open issue carries one or more **area** labels
(what part of the system it touches) and one **maturity** label (how far along it is).
GitHub's label filters are the source of truth; `make sync-issues` renders a convenience
dashboard at `docs/issues/STATUS.md` (gitignored, generated — never hand-edit it).

Labels are applied on four orthogonal axes plus a `tracker` marker.

## 1. `area:*` — what part of the system (one or more)

| Label | Covers |
|---|---|
| `area:launcher` | The launcher / home grid / shelf |
| `area:editor` | The authoring app: Config/Blocks/Code/Sprites/Map/Music tabs, asset editors |
| `area:wm` | Window manager / layered compositor (fullscreen-stack + windowed tiers) |
| `area:player` | The `run(cart)` player black box + exit model |
| `area:blocks` | Block language → Python compiler and block UX |
| `area:carts` | Cart runtime, the `.moy` format, the cart API, seed games, runtimes (Lua) |
| `area:apps` | Non-game apps: Desk Lab (Writer/Storybook/Sheets), quest system |
| `area:audio` | AudioEngine, sfx/music, sound packs, codec bring-up |
| `area:input` | Keyboard/trackball/touch/BLE/USB-HID, on-screen + unified input model |
| `area:multiplayer` | ESP-NOW, local co-op, sharing, `net.*` |
| `area:webview` | Device/host web view + draw-command transport |
| `area:ota` | OTA firmware update pipeline & channels |
| `area:perf` | Performance work (fps, frame budget, levers) |
| `area:p4` | ESP32-P4 (Waveshare 7B) device target |
| `area:tdeck` | LilyGO T-Deck Plus device target |
| `area:hardware` | Form factors, device tiers, external hardware (Lego) |
| `area:ai` | AI helper / hint system |
| `area:tooling` | Dev pipeline, build/test automation, repo tooling |
| `area:brand` | Naming, trademark, brand identity |
| `area:docs` | Documentation itself |

Add a new `area:*` only when a real subsystem has no home — and add it to `AREA_ORDER`
in `tools/sync_issues.py` so it sorts where you expect in the dashboard.

## 2. `maturity:*` — the readiness ladder (exactly one)

A project-native ladder in place of a generic TRL scale. The axis that matters here is
**host → device → hardened**, so it's baked in. "Shipped" is the implicit 7th rung: a
closed issue.

| Rung | Label | Meaning |
|---|---|---|
| ① | `maturity:idea` | Unspecced concept — direction only, no design yet |
| ② | `maturity:spec` | Design settled, not built (the body reads like a plan) |
| ③ | `maturity:prototype` | Partial / rough / spike — some of it exists |
| ④ | `maturity:host` | Works in the host simulator, not yet device-verified |
| ⑤ | `maturity:device` | Verified on real hardware |
| ⑥ | `maturity:hardened` | On-glass verified and in the shipped seed |

Bump the rung as the work advances (that's the whole point — it should move). Most-mature
issues sort to the top of each area and of the "Ready to work" list, i.e. closest to done.

## 3. `status:*` — a temporary gate (zero or more)

| Label | Meaning |
|---|---|
| `status:blocked` | Waiting on an external dependency / another issue |
| `status:pending-decision` | Needs a product/design call before work starts |
| `status:pending-testing` | Built; awaiting verification |

The dashboard's **"Ready to work"** list excludes `blocked` and `pending-decision`
issues, so it shows only what's actually actionable.

## 4. `build:*` — what it takes to build (exactly one, actionable issues only)

Answers a different question from `maturity:*`: not *how far along* the work is, but
*what a machine needs in order to advance it*. This is the "can I pick this up on a
host-only dev box (no firmware build, no C toolchain, no device to flash)?" filter.

| Label | Meaning |
|---|---|
| `build:host` | Buildable + testable entirely on the host (Python/tests/docs) — no device |
| `build:host-partial` | Host reference workable now; a device/radio/audio leg remains blocked (build the host leg, hand off the rest) |
| `build:device` | Requires firmware build, native C, on-glass verification, or radio/audio hardware |

Applied to **actionable** issues only — `tracker`/meta/`pending-decision` issues carry
their own markers and aren't build tasks, so they stay unlabelled on this axis.
`build:host` vs `maturity:host` are independent: `maturity` says the host
implementation *exists and runs*; `build:host` says it *can be built here* (a
`maturity:idea` feature can still be `build:host` if the reference lives in `runtime/`).
Filter what you can pick up today with `gh issue list --label build:host`. Colors:
`build:host` `0e8a16` (green), `build:host-partial` `dbab09` (amber), `build:device`
`b60205` (red).

## 5. `tracker` — living issues (marker)

Some issues are **persistent, continuously-updated ledgers**, not one-shot tasks to
close: the performance ledger (#66), the P4 port status (#58), the native-gap lever
roadmap (#77), the compositor→WM path (#73), the launcher/UI-perf sibling (#86), the
product-lineup vision (#59). Tag these `tracker`. The dashboard rolls them into a
**"Living trackers"** section and marks them ⏳ elsewhere so they're never mistaken for
closeable work. Read a tracker for current state; don't try to finish it.

## Umbrella trackers + native sub-issues

Each subsystem has a `tracker` **umbrella issue** whose body carries the current
status, a maturity rung, and a **"known gaps / wishlist"** checklist — so a gap can
be recorded even when no standalone issue exists yet (e.g. "the code editor has no
jump-to-definition"). Concrete work items are **GitHub native sub-issues** of the
umbrella (parent shows a live progress checklist; each child links back to its
parent). A child has exactly **one** parent — pick the primary area; any secondary
area stays a plain `area:*` label, so the dashboard still lists it under both.

Current umbrellas: Authoring & editors (#88) → Code (#89) / Sprite-Paint (#90) /
Map (#91) / Music (#92) / Blocks (#93) / Config (#94); Carts (#95); Apps (#96);
Audio (#97); Input (#98); Multiplayer (#99); Web view (#100); OTA (#101); AI (#102);
Tooling (#103); Brand (#104); Launcher (#105); plus the pre-existing P4 (#58),
Product lineup (#59), and Compositor/WM (#73). Perf (#66/#77/#86) stays a
cross-cutting axis, not a task-parent.

To add a new subsystem tracker: open an issue titled `[tracker] <name> — …`, label it
`tracker` + its `area:*` + a `maturity:*`, give it a status + gaps body, and nest the
related issues under it as sub-issues.

## How the dashboard is generated

`tools/sync_issues.py` (run via `make sync-issues`, needs the `gh` CLI, authed) mirrors
every issue into `docs/issues/` and derives `STATUS.md` purely from these labels —
grouped by area, sorted by maturity, with "Ready to work", **"Buildable on host"**, and
"Living trackers" roll-ups. Every row carries an inline `build:*` marker (🟢/🟡/🔴), and
the "Buildable on host" section lists just the 🟢/🟡 issues (host-first) — the
pick-up-today view for a host-only box. Because it's generated from GitHub, it can't
drift. **Re-run `make sync-issues` after any label change** (the mirror is gitignored, so
a fresh checkout rebuilds it).

## Housekeeping

- Colors are APPLIED (2026-07-14, via `gh label edit --color`): `area:*` all
  `1d76db` (blue); `maturity:*` a green gradient idea→hardened (`c2e0c6` →
  `9fd6a5` → `6cc370` → `3fb950` → `1a7f37` → `0d5424`); `status:blocked`
  `d93f0b`, `status:pending-decision` `f0871f`, `status:pending-testing`
  `fbca04`; `tracker` `6f42c1` (purple). A new `area:*`/rung should follow the
  same scheme (`gh label create --color ...`).
- Superseded loose labels (`enhancement`, `input`, `runtime`, `sharing`, `ai`,
  `blocked`, `pending decision`, `pending testing`) were replaced by the axes above and
  can be deleted in the GitHub label UI once you're happy with the new scheme.
