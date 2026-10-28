---
paths:
  - "system_carts/**"
  - "runtime/cart_api.py"
  - "runtime/moy_carts.py"
  - "runtime/lua_ext.py"
  - "runtime/player.py"
  - "native/moycore/**"
  - "native/moy_audio/**"
  - "docs/moy_cart_api.md"
  - "tools/*p8*.py"
---

<!-- The .moy cart contract, its runtimes, and its vendored audio. -->

### Audio is VENDORED from moy-spec, not implemented here (#97)

The one subsystem where that rule is inverted. SPEC.md §8.3 pins synthesis to
PICO-8's measured output (as reverse-engineered by zepto8/fake-08) — the
unequal instrument loudness, the pitched noise walk, the Hz-linear slide, the
109/110 phaser detune — and moy-spec ships its own C implementation of it,
**libmoy**. That source is vendored verbatim into
`native/moy_audio/libmoy/` and
**compiled into** every board that plays sound, the web runner and the host;
the kernel's sessions (`moy_aud.c`) drive it and `modmoy_audio.c` is a thin
binding. libmoy owns each session's bank, both sequencers and the mixer, so the
boards are conformant by construction and nothing marshals across the boundary
per frame — a bank crosses ONCE per cart (and per editor change) as
`sounds.json` text.

**Heard on a T-Deck (owner-verified, 2026-08-09, firmware 0.9 over OTA).** Until
then the swap had only host evidence — `tests/test_audio_parity.py` diffs every
sample against the vendored C, which proves the twin faithful and says nothing
about whether I2S comes up on glass. It does. **The game sounds CHANGED audibly,
and that is the expected result, not a regression**: §8.3 pins the synth to
PICO-8's measured output, whose instrument loudness is deliberately unequal — the
triangle family peaks at about twice the square family. The engine it replaced
had them EQUAL: `_sample_wave` before `c5d594e` returned ±1.0 for both square and
triangle (organ reached 1.5, phaser was halved back to 1.0), so the audible change
is that triangle-family parts are now roughly twice as loud against a square lead
as they used to be. The seed carts were authored against the old balance, so a
cart whose lead now sits under its accompaniment is a **cart mix** to fix in its
`sounds.json` vol column, never a synth to "correct" here — and nothing will catch
it for you, because §8.3 exempts audio from pixel conformance on purpose.

So: **do not "improve" the synthesis locally, and do not add a waveform or an
effect here.** Fix it in moy-spec, re-vendor with **`make vendor-libmoy`**
(`tools/vendor_libmoy.py`, pointed at a sibling moy-spec checkout or `SPEC=`;
it copies the pinned file set and re-stamps `native/libmoy_vendor.json`), bring
the Python twin along. **Editing a vendored file in place is a red test** —
`tests/test_libmoy_vendor.py` hashes every one against the manifest, and also
diffs against a sibling checkout when it sits at the pinned commit, so both
"someone patched the copy" and "someone patched upstream without re-vendoring"
fail on the same day rather than at the next re-vendor. The #167 3D verbs took the other route
— `moy_gfx` re-implements `moy_canvas.c`'s geometry line-for-line — and that is
only safe because the conformance goldens pin every pixel; §8.3 deliberately
exempts audio from pixel conformance, so there is no golden to catch a drifting
twin.

**The Python twin synth is DEAD (moycore stage 0, 2026-08-11), and the
playing is the kernel's (sprint 3, 2026-10-07).** `native/moy_audio/moy_aud.h`
holds the sessions (one per owner, only the FOCUSED one heard), the mix and
the sample voice over libmoy; `moy_aud_out.c` is the board's speaker, started
at the first focus. Every tier reaches it as the module `moy_audio`: the
usermod on the boards, the browser and the desktop MicroPython, and on the
host `runtime/audio_binding.py`, which compiles it with the DOUBLE-WIDENED
libmoy (the parity harness's own recipe) into a hash-cached `.so` under
`.build/host_audio/`. `runtime/audio_session.py` is the Python face
(`AudioSession`, the per-frame `PcmPump` of the host and the browser). **No
compiler / no native module means SILENCE, not a fallback synth** (owner call,
KISS): a session then holds no handle and its verbs do nothing.
`tests/test_audio_parity.py` gates: the strict pass pins the host's module
bit-exactly against an independently-driven reference render (any difference
is marshalling, never the synth), the device-precision pass measures the
double-vs-float gap, and it drives the NATIVE module under a desktop
MicroPython build when one exists. Run
`.venv/bin/python experiments/audio_parity/audio_parity.py -v` for the report.
The data model (`SFX`/`MusicTrack`/`AudioBank`, `sounds.json`, the Music
editor) is ordinary shared Python.


- **Cart versioning (#47):** every `system_carts/*/manifest.json` carries an integer `"version"`. `seed_builtins` re-seeds an on-SD built-in only when the baked version is **newer**, and preserves the kid's data (`pmem.json` saves + `config.json` tuning) across the re-seed. **Bump a built-in's manifest `version` whenever you change its content**, or an already-seeded device keeps the stale copy.
