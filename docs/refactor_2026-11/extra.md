# Extra findings (main session, 2026-11-11): beyond the three audits

## God objects and oversized files (measured on dev 5465b571c)

- **G1 `Workstation`** (`runtime/shell/console*.py`)
  - 338 methods across 9 mixins (`InputVerbs`, `PerfMeters`, `SettingsToggles`, `SaveVerbs`, `Notices`, `SpineVerbs`, `HomeVerbs`, `TextVerbs`, `CanvasBinding`), in 10 files and ~9,500 lines.
  - 61 files read 101 of its private names, and `ws._dirty = True` appears at ~230 sites.
  - The mixins split the file, not the responsibility.
  - Proposed: carve it into services with explicit interfaces (input policy, saves, notices, home, canvas, settings, frame/dirty), handed to apps through `app_context`.
- **G2 `moy_ui_ctx_t`**: 177 fields (tiers T7). `moy_ui_api.c` is 4.5k lines, `moy_ui_parts.c` 3.5k, and `moy_theme.c` 4.7k with its own JSON parser (tiers T8).
  - Proposed: per-concern sub-structs (layout, focus, text edit, paste, popover) plus an explicit per-widget state store that never drops state silently.
- **G3 `Player`** (`runtime/run/player.py`): 2,813 lines, 82 methods (shell S16). One class runs every tier's carts plus app input, pause, windows, typing, cancel and back.
- **G4 the app-call door** (`native/moy_app/moy_app.c`): 2,680 lines and one hand-edited dispatch switch. Generate it from `os_calls.json`.
- **G5 `native/moy_elf/moy_os.c`**: 4,108 lines; memory, files, sockets, threads and rows in one file. Split it by concern.
- **G6 the big shell layers:**
  - `WindowedWM`: 106 methods, 2.0k lines.
  - `BarLayer`: 92 methods, 2.0k lines. 30 surfaces call its `_draw_status_strip`.
  - `SettingsLayer`: 88 methods, 1.75k lines. Special-cased at 33 sites (shell S2).
  - `Appearance`: 92 methods, 1.7k lines.
  - `GetCartsAppLayer`: 103 methods, 1.8k lines.
  - `EditorDesk`: 100 methods, 1.9k lines.
  - `console_notices.py`: 2,098 lines, four jobs.
- **G7 `DeviceCanvas`** (`device/device_canvas.py`): 96 methods, 3k lines. One canvas class on every tier is SETTLED (CLAUDE.md), so it stays one class but may delegate its verb groups to helpers.

Big but fine: `tools/p8_lua_port.py` (vendored), `tools/look_shots.py` (test tooling), monocypher and `moy_p8.c` (vendored).

## Hard-to-maintain systems that are not duplication

- **X1 the store format, before the trial.** The rule is "no migrations before users". Trial families' units ARE users. Decide the migration mechanism (a versioned store, a tested migration path, an export or backup) before units go out.
- **X2 the upstream patch ladder.** 22 `moybyte_patch_*` steps over MicroPython v1.28 and ESP-IDF 5.5.1 make an upstream bump expensive. P4 v3.x silicon will likely force an IDF bump. Do an upgrade rehearsal on a branch to size it.
- **X3 vendored code and security fixes.** WAMR (our fork), mbedTLS, Dropbear, Lua, dash and the moy-spec parts each have a re-vendor step. Nothing flags an upstream security fix, and mbedTLS and Dropbear face the network. Needs a standing check.
- **X4 the pixel goldens are hashes only** (44 configurations in `tests/shell_goldens/hashes.json`). A failure cannot show what moved, so a re-bake is unreviewable. Save before/after PNGs and a diff on failure. Needed BEFORE a refactor sprint that leans on the goldens.
- **X5 concurrency.** The kernel tasks, the VM task, the shell runner tasks, the SSH task and the door hop have no one-page threading model of who may touch what. Extend the shared-state scan to all of `native/` (native N3).
- **X6 docs.** 48.5k lines of docs; `check_docs` resolves paths but not truth. Each refactored system cuts its doc to what is true.
- **X7 three repos versioned apart** (moybyte, moy-spec, carts). A cart cannot say which firmware it needs; Moy Mesh v2 needs v0.18.1 to type on the T-Deck. Add a minimum-firmware manifest field (moy-spec).
- **X8 image headroom.** The Zero has ~91 KB and the T-Deck ~137 KB. Measure what the generated bindings do to image size.

## Live bugs found by the audits (fix first)

- Mic doesn't cancel the Files rename, Appearance's theme name or Storybook's fields on the T-Deck (shell audit).
- The theme loader's own JSON reader parses downloaded theme packages unfuzzed (tiers T8).
- `moy_gfx`'s unlocked file-scope caches, shared by every UI instance (native N3; first check whether a compiled program draws concurrently with the VM).
