# moy-spec conformance suite — vendored

`traces/`, `carts/` and `hashes.json` are copied unmodified from
[moy-spec](https://github.com/moybyte-org/moy-spec)'s `conformance/`.

| | |
|---|---|
| vendored at | `0d8fd92` (2026-09-03, branch p8-verbs) |

SPEC.md §11: *an implementation conforms when it runs the conformance suite and
produces pixel-identical output.* Moybyte is the reference console, so that is a
claim it should be able to make about itself, on demand, in its own test run.

## Why this is here rather than reached for

The suite already checked this repo — from the **other side**. moy-spec's
`conformance/parity.py --ref <moybyte>` replays every scene through the host's
canvas, and `tools/p4_conformance.py` runs the carts on a board over
serial. Both work, and neither is a gate: the first only runs if someone has both
checkouts and remembers, the second needs hardware and takes ten minutes. So
`make test` could go green on a raster that no longer draws what the spec says,
and the news would arrive from a different repository.

Vendoring is 256 KB and makes it a gate. Same reasoning as
`native/moy_audio/libmoy/` — where the alternative net was missing entirely, this
one existed but was never in the path anything runs.

## What checks what

- `tests/test_spec_conformance.py` (here) — replays `traces/` through the host's
  canvas (`runtime/host_canvas.py` → `device_canvas.DeviceCanvas` over the
  compiled `moy_gfx`/libmoy kernel) and hashes the framebuffer against
  `hashes.json`, on every `make test`, with no spec checkout.
- `tests/test_gfx_binding.py` — that host kernel against the REAL native module
  under a unix MicroPython build, so the board's compiled kernel is reached.
- `tools/p4_conformance.py` — the real board against the real goldens. The one
  that reaches the C kernel and the RGB565 framebuffer; still manual, still the
  most convincing.

## Re-vendoring

```sh
SPEC=../../moy-spec
cp -r $SPEC/conformance/traces $SPEC/conformance/carts .
cp $SPEC/conformance/golden/hashes.json .
```

and update the commit above. A hash that moves is either a spec change to follow
or a regression to fix — `hashes.json` carries a `provenance` field saying which
implementations agreed on the frames, which is what makes the difference
arguable rather than a coin toss.

The golden PNGs are deliberately NOT vendored: the hashes are the contract, and
for a failing scene moy-spec's own `run.py --diff out/` produces proper diff
images from the same traces.
