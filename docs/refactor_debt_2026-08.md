# Refactor debt — god classes, large files, duplication (2026-08-25)

**Tracked as GitHub #209.** That issue is the self-contained summary; this file
is the long form. If they disagree, the issue wins (it is what has state).

**Status: UNCOMMITTED working note, not a plan of record.** A survey taken right
after the sync-stack work landed, while the tree was fresh. Nothing here is
scheduled; it is a ranked backlog with verdicts, so the next person does not
re-measure. Numbers are line counts / method counts at the time of writing
(HEAD around the wasm-sync merge). None of this is on the sync branch — these
are shell-wide cleanups that want their own focused branch, not a feature one.

Measured over TRACKED source only, excluding the intentional staged copies
(`firmware/*/modules/` mirror `runtime/`), the vendored trees
(`native/*/libmoy`, `spec_conformance`) and `.build/` — those inflate every
scan and are duplication by design.

---

## 1. God classes

| Class | Lines | Methods | Verdict |
|---|---:|---:|---|
| **`Workstation`** (`runtime/console.py`) | 5,472 | **275** | **Real god class.** The one that matters. |
| `DeviceCanvas` (`device/device_canvas.py`) | 2,119 | 61 | Cohesive — a canvas has many draw verbs. Leave it. |
| `BlockEditorUI` (`runtime/block_editor_ui.py`) | 1,879 | 83 | Borderline; one editor. Candidate later. |
| `WindowedWM` (`runtime/wm_windowed.py`) | 1,834 | 71 | Borderline; window management is one job but grown. Watch. |
| `SettingsLayer` | 1,191 | 40 | Big but single-responsibility. Fine. |
| `Player` / `BleHidKeyboard` / `OtaUpdater` | ~1,000 | 20–43 | Single-responsibility. Fine. |

**Only `Workstation` is a true god class.** CLAUDE.md says the 2026-07 refactor
"shrunk it to a compositor/router"; it still owns roughly a dozen concerns:

- the Layer-stack frame / input / pointer loop (the actual compositor/router)
- the shared draw toolkit (`_glyph`/`_icon`/`_btn`)
- store + service attach points (`carts_store`/`wifi`/`updater`/`webhost`)
- the spawn/exit verbs and `go_home` funnel
- **cart management** (new/dup/delete, `_apply_items`, the cover caches)
- **persistence** (`system.json`, achievements, `_persist_system`)
- **the web-console switch** (`park`/`stop`/`unpark`, `web_pin`, `rescan_carts`)
- crash-to-code, notices/banners, theming, the appearance/skin plumbing

### Proposed decomposition (needs owner alignment before any code moves)

The safe carve is to pull collaborators OUT of `Workstation` while it stays the
façade, so callers do not change and the shell goldens stay the net:

1. **`CartManager`** — new/dup/delete + `_apply_items` + the cover caches
   (`_cover_runs`/`_cover_cache`/`rescan_carts`). ~40 methods, self-contained.
2. **`ServiceRegistry`** — the injected seams (`carts_store`/`wifi`/`updater`/
   `webhost`/`lua_runtime`) + `wire_workstation_core`. Turns "attach point"
   into a thing, not a scatter of attributes.
3. **`WebConsoleController`** — the #197 switch (`park`/`stop`/`unpark`/
   `web_pin` + the connection-screen wiring). Already almost separable.
4. **`SystemPrefs`** — `system.json` + achievements + skin/theme persistence.

Each is a delegate `Workstation` holds and forwards to; extract one at a time,
re-run `tests/test_shell_goldens.py` + `tests/test_settings_layer_pixels.py`
(300 sub-surface hashes) after each. Do NOT big-bang it. This is a multi-session
project, not a pass.

---

## 2. Largest files
`runtime/console.py` 6,276 · `device/device_canvas.py` 2,665 ·
`runtime/wm_windowed.py` 2,320 · `runtime/blocks.py` 2,287 ·
`runtime/moy_carts.py` 2,273 · `runtime/block_editor_ui.py` 2,224 ·
`runtime/ui.py` 1,798. Only `console.py` is oversized *because of* the god
class; the rest are large-but-cohesive and splitting them buys little.

Longest single functions worth a glance (not urgent): `cart_api.make_api` 528
(a closure factory — inherently long), `player.start` 350, `console.frame` 345,
`dev_channel.run` 261, `blocks.compile_blocks` 238.

---

## 3. Duplication (genuine, after filtering idioms)

Ranked by value = (copies × subtlety of the logic). A shared body is worth most
where a silent drift between copies would be a *bug*, not just noise.

### 3a. The 7 editor `*Layout` classes — HIGHEST VALUE
`CardsLayout`, `PaintLayout`, `MapLayout`, `MusicLayout`, `SceneLayout`,
`CodeLayout`, `BlockLayout` — each in its own file — re-declare
`_BASE_W = 320` / `_BASE_H = 240` and an identical `__init__` head plus
`self._base = (self.w == _BASE_W and self.h == _BASE_H and fs == 1)`. Three of
them (`map`/`paint`/`scene`) also repeat the panel-rect block
(`px,py = 8*fs …; self.body_fill = …; self.panel = …`).

CLAUDE.md's own #39 note says these are *meant* to be "`_base`-verbatim
byte-identical at 320×240/1×" — which is precisely the case for a shared base
making that a STRUCTURAL guarantee instead of a by-discipline one. A `LayoutBase`
with the common `__init__`/`_base` (and optionally the panel geometry) that all
seven inherit removes 7 copies of the responsive scaffolding.

- **Risk:** low — fully covered by the shell goldens (87 hashes × 5 configs) and
  the sub-surface pixel hashes; any drift turns a config red.
- **Effort:** moderate — 7 files, each diverges *after* the common head, so the
  base must be minimal (init + `_base`, maybe panel geometry) and the subclasses
  keep their own bodies.

### 3b. `vendor_libmoy.py` ↔ `vendor_p8_import.py` — CLEAN, SELF-CONTAINED
~20 lines shared: the manifest-write (`json.dump` + `newline="\n"` + stamp) and
the change-report (`--check` vs updated, "already up to date", the dirty-checkout
warning). CLAUDE.md forbids *merging the two scripts* (they vendor different
things — C vs Python); it does not forbid a shared `tools/vendor_common.py`
helper both import. Low-traffic tooling, covered by the two `*_vendor` tests.

### 3c. `launcher_layer.py` retained-frame check — SUBTLE, WORTH IT
The `_statics_key` / `_full_streak` / `_retained_n(cv)` invalidation logic
appears **3× within the one file** (≈1042, 1088, 1564). `ui_damage_model_v1.md`
explicitly names repeated invalidation logic as the class that "produced the
same silent cache bug." A private helper method that the three sites call would
retire exactly the pattern that doc warns about. Care: confirm the three sites
are truly identical before folding.

### Evaluated and DISMISSED (not duplication to remove)
- The `try: from X import Y / except ImportError: from runtime.X import Y`
  host/device import shim (files_app/sheets_app/writer_app, …) — an idiom, not
  logic; you cannot factor out an import statement.
- `size()`/`framebuffer()`/`gfx()` across `device_canvas`/`host_canvas`/
  `web_canvas`/bench — a polymorphic COMPOSITOR CONTRACT (`surface_model_v1.md`
  §4). Each backend implementing the same interface is the point, not a copy.
- The argparse header (`from __future__ … import argparse, sys`) in the `p4_*`
  perf tools — import boilerplate.

---

## Recommended sequence (when someone picks this up)
1. **3b** (`vendor_common.py`) — quickest, zero product risk, warms up.
2. **3a** (`LayoutBase`) — highest value, golden-protected. The main event of a
   dedup pass.
3. **3c** (launcher helper) — small, but read the three sites carefully first.
4. **`Workstation`** — a separate, multi-session project with owner sign-off on
   the carve; extract one collaborator at a time behind the façade, goldens
   green after each.

All of the above is independent of the sync stack and should land on its own
branch off `dev`, never bundled into a feature branch.
