# libmoy (audio) — vendored, verbatim

`moy_audio.c` / `moy_audio.h` are **copied unmodified** from
[moy-spec](https://github.com/moybyte-org/moy-spec)'s `libmoy/` — the moy console
as a C library. MIT, same as this directory's `LICENSE`.

**Which commit, and whether this copy still matches it, is recorded in
[`../../libmoy_vendor.json`](../../libmoy_vendor.json)** — written by
`make vendor-libmoy` and checked by `tests/test_libmoy_vendor.py`. That file is
the authority; this page is the explanation.

## Why vendored rather than re-implemented

SPEC.md §8.3 pins the synthesis to PICO-8's measured output (as reverse-engineered
by zepto8 and fake-08) — the unequal instrument loudness, the pitched noise walk,
the Hz-linear slide, the 109/110 phaser detune. Moybyte is the reference console,
so its audio has to BE that, and the cheapest way to guarantee it is to compile the
spec library's own source rather than keep a hand-maintained twin of it. The #167
3D verbs took the other route — `moy_gfx` re-implements `moy_canvas.c`'s geometry
line-for-line — and that only works because the conformance goldens pin every
pixel. §8.3 deliberately exempts audio from bit-identical conformance, so there is
no golden to catch a drifting twin. Vendoring is what replaces the missing net.

## Do not edit these files

Fix it upstream in moy-spec, then:

```sh
make vendor-libmoy                          # ../moy-spec beside this repo
make vendor-libmoy SPEC=/path/to/moy-spec
```

which copies the pinned file set and re-stamps the manifest. **An edit made here
instead is a red test** (`tests/test_libmoy_vendor.py`), which is the point: an
in-place fix works, and survives exactly until the next re-vendor silently
reverts it — weeks later, in a build nobody connects to the change.

`tests/test_audio_parity.py` compiles this exact source and diffs its PCM against
`runtime/audio.py`, so a re-vendor that changes the synthesis fails the suite
until the Python twin is brought along.

## What uses it

- **T-Deck (S3)** and the **web runner** — `../modmoy_audio.c` is a thin
  MicroPython binding over this library's public API. The bank, both sequencers
  and the mixer are all libmoy's; MicroPython only forwards the six §8.2 verbs.
- **P4** — inherits it when the ES8311 codec is brought up (#82).
- **Host sim (CPython)** — cannot link C without putting a compiler in `make
  setup`, so `runtime/audio.py` stays a Python twin of this file. It is pinned by
  the parity harness above rather than trusted.
