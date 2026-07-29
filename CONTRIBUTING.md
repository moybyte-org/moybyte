# Contributing to Moybyte

Thanks for your interest! First, the promise that governs everything here:

> **Everything you'd do as a person is free, forever.** Run the simulator,
> flash the firmware on your own board, modify it, teach with it, make and
> sell your own carts. The only thing that needs a commercial license is
> selling hardware or a competing commercial product built on the console —
> and even that restriction expires per release after two years. See
> [`LICENSE.md`](LICENSE.md).

## Before you start

- **Bugs / small fixes:** open an issue or a PR directly.
- **Features:** open an issue first. The console is deliberately small and
  opinionated (see `moybyte_Console_Plan_v0_5.md` §2 — "learn to make"); a
  quick design conversation saves you from building something we can't merge.
- **Carts:** carts you author are **yours** — license them however you like.
  A community gallery is planned (#122); until then, share them in an issue
  or your own repo.

## Developer Certificate of Origin (DCO)

Because the console is under a source-available license (FSL) that the project
also licenses commercially, we need to know every contribution is yours to
give. We use the [Developer Certificate of Origin](https://developercertificate.org/)
— no CLA paperwork, just sign off each commit:

```bash
git commit -s
```

This appends `Signed-off-by: Your Name <you@example.com>`, certifying you
wrote the change (or have the right to submit it) and that the project may
distribute it under its licenses, including the FSL's future-MIT grant and
commercial licensing. PRs without sign-off can't be merged.

## Development

Python 3.10+ is all you need for the host side — no toolchain, no device.

```bash
make setup          # .venv + editable install (the dev + sim extras)
make test           # pytest — must pass
make check-portable # if you touched examples/ or the .moyproj SDK

.venv/bin/python tools/simulate_desktop.py   # the console itself, on your PC
make doctor         # environment sanity check, if something looks off
```

`make setup` installs everything the tests and the simulator need. Flashing a
board needs more: `.venv/bin/python -m pip install -e '.[device]'` (esptool,
pyserial, mpremote) and, for the T-Deck/P4 images, the ESP-IDF 5.5 toolchain —
see the per-board READMEs under `firmware/`.

- Working orientation for the codebase lives in `CLAUDE.md` (humans: it's the
  best map of the repo, not just for AI tools).
- **Host == device:** the console UI in `runtime/` runs on both the PC
  simulator and the device (the firmware build stages copies). Changes to
  drawing/canvas APIs must land in **both** backends and keep the API
  identical (`runtime/canvas.py` + the device modules).
- Firmware-touching changes: note in the PR whether you tested on hardware —
  untested-on-glass is fine, just say so (the maintainers flash and verify).
- Match the surrounding code's style; tests go in `tests/`.

## What we're unlikely to merge

Features that fight the pillars (§2.4–2.5 of the plan): open-internet access
in child mode, accounts/telemetry, media-consumption features, or anything
that breaks the 320×240 MOY64 cart contract that lets one cart run on every
tier.
