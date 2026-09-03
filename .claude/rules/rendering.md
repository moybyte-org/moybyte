---
paths:
  - "runtime/*canvas*.py"
  - "runtime/ui.py"
  - "runtime/skin.py"
  - "runtime/chrome.py"
  - "runtime/*_layer.py"
  - "runtime/*_ui.py"
  - "runtime/wm*.py"
  - "device/device_canvas.py"
  - "native/moy_gfx/**"
  - "docs/surface_model_v1.md"
---

<!-- The raster, the widget toolkit, and the goldens that gate them. -->

### The indexed-canvas portability contract (why the canvas is "indexed")

The `.moy` canvas works in **palette indices** (the `MOY64` palette) with a plain-function drawing API (`cls/pset/line/rect/rectfill/circ/circfill/spr/print`) — no dependency on `framebuf`, LVGL, or even Python. This is deliberate: the *same* `.moy` runs on the host, on all three boards and in the browser. **There is now ONE canvas class on every tier** — `device_canvas.DeviceCanvas`, RGB565 with the palette resolved at draw time; the host builds it on CPython through `runtime/host_canvas.py` (the host's own indexed raster, runtime/canvas.py, was deleted 2026-08-15 — git history has it). So a drawing feature is added ONCE, in that class + the `moy_gfx`/libmoy kernel under it. The SYSTEM-surface contract (#39: font_scale text, font-scale layers, `blit_cover`) is one body too since 2026-08-18 — `device_canvas.SystemCanvas`, which the host/web/P4 classes subclass for only their per-tier pieces; two of the three hand-copies it replaced had silently drifted (the P4's print stride, its lost cart-palette layer rider).


### Graphics is conformance-checked, and the indexed canvas was MEASURED AND DECLINED

Graphics has not followed audio into vendoring, and the reason is measured
rather than assumed: **the indexed canvas was A/B'd on P4 glass (2026-08-05) and
565-at-draw won**, losing no colour — every scene hashed identical on silicon.
That question is CLOSED on performance. Why, by how much, and the two readings
not to misquote: **`docs/perf_native_gap_v1.md` §8** (#77). Do not re-derive it
here.

**Nine verbs are libmoy's now (2026-08-07), and the hand-porting argument lost.**
`tri`/`sspr`/`tline`/`circ`/`circb`/`line` went first, then `print`/`blit_map`/
`blit_batch` later the same day; in `moy_gfx` all nine are CALLS into vendored
libmoy (`native/moy_gfx/libmoy/`, built `MOY_PIXEL_RGB565` at `-O3` via
`libmoy_kernels.c`), not transcriptions of it. The reason is one measurement: on 2026-08-06 the board failed
`provisional_tline` against the golden (2773 px, 3.61%) while the host passed it,
because the only lane that exercises the REAL C kernel is on-glass conformance
and it had never been run on that verb — `test_device_canvas_parity.py` compares
the host to a *Python transcription* of the kernel, which cannot catch the
transcription being right and the C being wrong. Routing the verb through the
spec's own raster took it to **0 differing pixels**, all ten scenes pass on the
P4, and the cart-level cost is nil (per-verb A/B within the bench's ±5% noise;
per-cart fps unchanged across the roster). **`print`, `blit_map` and the sprite
path were declined first and then CROSSED** — the numbers that kept them out were
stale (they predated `moy_print`'s off-clip early-out and `moy_spr`'s scale-1 fast
path), and re-measuring on 2026-08-07 reversed two of the three; adopting `spr`
also deleted the RGB565 tile atlas, which handed back 64 KB of S3 internal SRAM
and removed a ~100ms first-use bake nobody had attributed. What `moy_gfx` still
owns is its COMPOSITOR — viewport-aware `fill`/`fill_rect`, `blit565_scale`,
`copy_async`, `scroll_rect`, `blit_window`, `blit_indices`, `fill_spans`,
`draw_ctx` — which the spec's raster has no counterpart for.
**`native/moy_gfx/libmoy/UPSTREAM.md` is the authority on which verbs cross and
why** (it carries the before/after table, the dates, and the warning about which
of the bench's verbs are too cheap to quote); fixes belong upstream (`moy_circ`
already went that way — moy-spec `ef01426`). Tooling: `tools/p4_perf.py` (per-cart fps),
`tools/p4_cart_bench.py` (the Bench cart's per-verb µs — since 2026-08-17 the
Lua twin reports over serial too: both carts write a fixed PMEM layout the tool
reads live via `moycore.pmem_image`, so `--json`/`--diff` speak one format for
both twins; `tests/test_bench_pmem_report.py` locks the three layout copies
together), `tools/p4_bench.py` (the console's own UI-panel bench),
`tools/p4_conformance.py --serve` (holds the board — opening the port
REBOOTS it, which cost a full boot per scene; the suite went 12min → 4m45).

**`tests/test_spec_conformance.py` is that gate** (suite vendored under
`tests/spec_conformance/`, see its UPSTREAM.md). It replays the spec's recorded
verb traces through the host's canvas (`runtime/host_canvas.py` → `DeviceCanvas`) and hashes each frame against the
golden — every scene in the suite, the 3D verbs included, in ~0.1s, on every
`make test`. It exists because the suite previously only checked this repo from
*outside* it (moy-spec's `conformance/parity.py --ref`, and `tools/p4_conformance.py`
on a board), so `make test` could go green on a raster that no longer drew what
the spec said. The device inherits it through `test_device_canvas_parity.py`
(host↔device), and `tools/p4_conformance.py` is still the only check that
reaches the real C kernel on real glass — run it when the raster changes.

**The compiled-vs-compiled check is a `make` target now (2026-08-15).**
`tests/test_gfx_binding.py::test_matches_the_native_moy_gfx` drives **131 ops
across 16 verbs** through the host's ctypes binding AND through the real native
`moy_gfx` under a desktop MicroPython — the only lane in `make test` where two
independently COMPILED rasters meet. It used to point at a hand-built binary
nothing produced, so it passed on one machine and silently SKIPPED in CI.
**`make unix-micropython`** builds it (~15s cold, <1s warm, **no cache** — a
cache miss that skips the check is the bug), CI runs it every push, and a
missing binary WARNS locally and FAILS under `CI`/`MOYBYTE_REQUIRE_UNIX_MP`.
The same binary carries `moycore` and `moy_audio`. On the strength of it,
`tests/test_device_canvas_parity.py` shed its ~400-line Python transcription of
libmoy's nine verbs — `_FakeGfx` forwards them to the binding and transcribes
only `moy_gfx`'s OWN compositor; its `gfx=False` arm STAYS, because it is the
only thing anywhere that runs `device_canvas`'s no-kernel Python lanes. Two
things not to undo in that op script: its framebuffer is a `memoryview` into a
larger **patterned** arena (without it a capacity guard that fails to clamp
writes past the end on BOTH sides and reads as agreement), and its clamp ops aim
ONE pixel past each edge, because a mutant that clamps at `max_rows + 1`
survives any large overhang. Every suite that drives the real native modules
resolves the binary through ONE shared lookup, `tests/unix_mp.py`
(`require_unix_mp`, probing the binary for the modules the suite needs;
`MOYBYTE_MICROPYTHON` overrides) — `test_gate_pal_sync`, `test_semantic_traces`,
`test_moycore_loop`, `test_audio_parity`'s native case and `test_gfx_binding`
all ride it, none carries its own path, and absence WARNS locally / FAILS under
`CI`/`MOYBYTE_REQUIRE_UNIX_MP` instead of silently skipping. (This line used to
say five suites "still point at their own hand-built binaries" for days after
the unification landed — the 2026-08-17 sweep verified all five run, 19 passed
0 skipped.) Do NOT symlink into
`firmware/lilygo_t_deck_plus_mainline/.build/micropython/`, which `build.sh` git-clones into.


### The UI refactor landed (2026-08-19) — one widget vocabulary, apps as data, user apps

Record: `docs/history/ui_refactor_2026-08.md` (it folds `ui_widgets_2026-08.md`
and `shell_decoupling_2026-08.md`, and CUT about half the combined program on
evidence — read its Section 1 before proposing any cut part again). The rules it
left:

- **The 320×240/1× baseline does NOT exercise the toolkit.** `editor_app._draw_zone`
  and siblings are guarded `if not ws.layout._base`, so perturbing a widget turns
  the Guition/fs3/windowed configs red and leaves BOTH T-Deck rows green.
  **Verify a widget change on the non-`_base` configs; a green T-Deck row proves
  nothing.** The net is `tests/test_shell_goldens.py` + `test_settings_layer_pixels.py`.
- **Adding a system app is TWO files** (`docs/app_api_v1.md` has the checklist):
  an `"app"` block in the identity cart's manifest, and `runtime/app_decls.py`, its
  generated frozen copy. The five hand-lists it replaced failed silently and on
  device only. Ratchet: `tests/test_app_registry.py`.
- **The bar contract is a HOST guarantee**, scoped to `"tool"` — there are SEVEN
  strip kinds and collapsing them breaks the others.
- **Apps declare what they need** (`runtime/app_context.py`, a pure leaf) and there
  is **zero `ws.` in the app tier**. `ctx.files` and `ctx.carts` are split
  deliberately: carts authors executable content, so granting it to a cart is
  self-escalation. **No `property` forwards** (a plain hop is +0.5µs, a property
  +5.1µs) — live state reads through a method. `prefs_ns` exists because
  `paint_doc` is the real key in kids' `system.json` since #108.
- **Style is data** — `runtime/skin.py`, NOT `chrome.py`, which would close
  `ui → chrome → settings_layer → ui`. `tests/test_skin.py` pins skin knowledge to
  its two owner modules in both directions.
- **A user can add an app.** `runtime/system_api.py` maps manifest permissions to
  roles as an ALLOWLIST; never grantable: `shell`, `carts`, `wallpaper`, `artwork`,
  `damage`, `surface`, `clipboard`, `notify`. **An ungranted verb is ABSENT, not
  stubbed** (`system_carts/notes.moy` is the proof). Storybook/Sheets/Files/Paint
  STAY shell code; Calc is portable today.
- **The windowed DESK world must NOT bind the system canvas** — a cart there lives
  in a window whose blit source IS `ws.canvas`, so binding makes the desktop blit
  itself. Found only by rendering it.
- **`crash_guard` disables a type:"app" cart after three crashed opens**, and
  committing fixed CODE forgives the strikes (`Project.commit_code` →
  `forgive_app` is the only thing that clears the count).

