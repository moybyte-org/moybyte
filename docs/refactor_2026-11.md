# The refactor sprint (2026-11)

The owner asked for this plan on 2026-11-11, after the 2026-11 OS sprint
(`docs/os_2026-11.md`). Three audits of origin/dev 5465b571c and the main
session's own list produced the findings; each was then checked against the
code at that commit, which is where every count below is from. This document
is the plan.

The audits and the check are the appendix:

| file | holds |
|---|---|
| `docs/refactor_2026-11/BRIEF.md` | what the auditors were told, and what was already measured |
| `docs/refactor_2026-11/native.md` | the native C core: N1–N15 |
| `docs/refactor_2026-11/shell.md` | the Python console: S1–S16 |
| `docs/refactor_2026-11/tiers.md` | the toolkit, the tiers, the bindings, the generators, the build: T1–T16 |
| `docs/refactor_2026-11/extra.md` | god objects G1–G7, maintenance X1–X8, the live bugs |
| `docs/refactor_2026-11/verification.md` | every finding's tag and what the check found |

This plan uses three ids the audits do not: **L1–L3** for the live bugs in
`extra.md`'s last section, in its order, and **B1–B3** for three items the
brief lists as measured that no finding carries (three timer schedulers, two
ways to post a notification, two faces of a device). A finding's evidence is
in its audit; this document does not repeat it.

On 2026-11-11 the owner folded five open issues into the sprint. They were
checked the same way and carry ids of their own:

| id | issue | what |
|---|---|---|
| K1, K2 | #245 | the two canvas paths left from the kernel work: the web compositor, the P4's game composite |
| I1–I4 | #242 | the four input-reliability items of its 2026-11-11 comment: the conformance matrix, one owner per policy, explicit widget state, no frame at a guessed size. Its convention rows are features and are not in the sprint |
| E1 | #127 | a one-step backup archive and restore |
| C1 | #160 | the launcher's broken-cart badge |
| F1, F2 | #249 | its two flaky on-glass tests. Its perf items wait (§7) |

Status is not kept here. Each phase's state is its issue's, under the sprint's
tracker.

## 1. The approach (owner, 2026-11-11)

- **A short sprint of big mechanical passes.** One system is refactored whole,
  by one worker or a few, and then the tests, the goldens and the board suites
  are made to pass. Nothing is ratcheted down gradually.
- **Every finding is in this plan.** One that is not done in the sprint is in
  §7 with its reason.
- **Every finding was checked against the code first.** §2 gives the tags.

Four conditions, agreed in principle:

1. **A mechanical refactor moves no pixel golden.** A golden that moves means
   the change was not mechanical, and it does not land as one. Only the
   behaviour-changing items carry a re-bake, each reviewed from before and
   after pictures and each named in §4: phase 0's fixes, the input owner
   (phase 5), the broken-cart badge (C1, phase 7; owner, 2026-11-11) and
   the timers (phase 11). Anything else that would change behaviour is put
   in or out of the sprint by the owner's rule in §6.
2. **A system lands before the next one in its area starts.** Phases run side
   by side only where §5 names the pair and says why they do not overlap.
3. **One final guard per system**, written when the system is done, so it does
   not grow back. Each phase names its guard. No count is pinned on the way.
4. **Feature freeze for the sprint.** The trial's software (first boot, the
   tutorials) follows the sprint. The store's migration door (X1) is settled
   before a trial unit goes out. Two additions are in the sprint because
   the owner put them there on 2026-11-11: the broken-cart badge (C1), which
   a kid sees, and the backup archive (E1), without its on-device action.

## 2. What the check found

`docs/refactor_2026-11/verification.md` has one row per finding. Of 80 rows:
56 verified, 21 corrected, 1 dropped, 2 not checked (X8: headroom is what a
build prints, and the check built nothing; F2: a flake shows only on a
board).

**Dropped**

| id | why |
|---|---|
| L3 | Listed as a live bug: a second task drawing through `moy_gfx`'s file-scope caches while the VM's frame does. No such task exists. An ELF app links its own copy of the raster, a compiled cart's session draws while the VM's task waits on it, a WASI program has no screen, and a `par` lane's imports are refused. N3 stays, as a guard that stops at the wrong directory and three setters for one fact. |

**Corrected** (the finding stands; the plan uses the corrected form)

| id | what changed |
|---|---|
| N1 | 62 probes, not 67; at least 30 private clocks, not 21; lazily-made mutexes in 13 files. More to fold, same fix. |
| N3 | No race (see L3). The work is one wire-order setter and the instance scan extended to `native/moy_gfx`, not per-instance caches. |
| N4 | 292 weak declarations, not 238. No `#pragma weak` exists yet, so the first one proves the form on every toolchain. |
| N5 | The timer row type is in `moycore_svc.c`, not its header. |
| N8 | The two string builders are near-identical, not byte-identical; twelve UTF-8 helpers, not eleven. |
| N15 | Four twins, 1,544 lines. The Settings pair is gone (its Python half is a test oracle, which stays). The host already builds two of the four C bodies. |
| S1 | 45 setter calls in 17 files, 8 `nav.text_mode` calls, 4 direct writes. |
| S2 | `"settings"` is compared at 26 sites and written at 62, in 23 files. |
| S14 | The shared class is `History` over an `OpCodec`, not `OpHistory`. |
| T6 | The step protocol is already one body (`moy_wasi_step` over `moy_wasi_runner_t`). What is twice is the hop under it and the job's lifecycle. Smaller than estimated, and moved to §7. |
| T8, L2 | The theme reader is fuzzed by a seeded Python corpus. It is outside the native fuzz targets. The fix is unchanged. |
| T9 | 104 mirrored structures, not about 40. |
| T11 | 96 functions, not 120. |
| T15 | `tools/bench_unix_mp.py` is named by one C comment, which goes with it. |
| X4 | The "after" picture exists (`tools/golden_png.py`). The "before", the diff and a failure that offers them are missing. |
| X6 | About 61k lines of tracked Markdown; `docs/` outside the archive holds 18.6k. |
| K1 | Worth doing, and for more than the issue says. The two compositors share four trivial methods, but the host's has since gained the three last-frame verbs the WM probes for and the web's has not, so the browser's desk takes a different path from the host's and the boards'. |
| K2 | Worth doing, and smaller than the issue says. The kernel's front already composites a P4 game frame in C over C entry points into the PPA, so the choice of crisp, bilinear or CPU is written twice. The work is one C body for both, not a port of every PPA hook. |
| I4 | "Measure first, then draw" meets a settled decision (below). It is done as "no frame is shown at a guessed size". |
| F1 | The failed run no longer wedges the next test: the test gives the board its VM back in a `finally` (fea23f699, 2026-10-31). Why it failed once is unknown. |

**Where a fix meets a settled decision**

| finding | the decision | how the plan goes round it |
|---|---|---|
| T7, G2, I4 | `docs/surface_model_v1.md` §8 is closed and §14's shift frames, floor and kept tables draw from the previous frame's record | The context is regrouped and its dropped state is counted. The toolkit stays single-pass; no measure pass is added. A widget that needs a size it has not kept draws nothing and owes the next frame, which the engine already does for a stale layout. |
| G7 | One canvas class runs on every tier (CLAUDE.md) | `DeviceCanvas` stays one class and is not split in the sprint (§7): a helper per verb group adds a hop on the path #66 measures. |
| K1, K2 | The same rule | Neither adds a canvas class. K1 merges two compositors under the one class; K2 moves a choice from `P4SystemCanvas`, which stays the P4's subclass, into the C it already calls. |
| S1, S3 | The kernel is C and our apps stay Python (`native/moy_kernel/README.md`) | The policy is Python, in the shell. The C input table keeps only the mode bit and gains the typed-key edge. |
| S12, S6 | A surface scrolls and draws text through the public verbs (CLAUDE.md's shell row; owner, 2026-10-29) | S12 is that fold for the licence page; the rule in §6 puts it after the sprint, and phase 7's guard names the page as the one hand scroller left. The home band's offsets are §14's table pass and are left alone. |
| X1, N15 | No store migrations until there are users (2026-09-07); the door is `sweep_store`'s `prune_retired` and its bar | A trial family is a user. Decision 4 (§6) sets the mechanism; it uses that door and keeps that bar. |
| N7 | `docs/board_ports_2026-08.md` declines a driver registry and codegen | N7 adds rows to the generated `MOY_HW_*` defines that exist. No registry, no generated sdkconfig. A pin a board lacks has no define. |
| N2 | The Lua allocator's SRAM-first pool is settled | The allocator guard allowlists it with its reason. |
| T1, T2 | Generated files are committed and held by a drift test; no build step generates | The face generator follows it. |
| S9, G1 | The archived architecture doc ruled out the 920-site rename of the big forwards | The carve (decided, §6) does not need it. The services' own code reaches the owning object directly; the six big forwards stay on the façade for outside callers and tests (§6's rule, §7). |
| T6, T1 | How a host executes a module never enters the spec (`native/moy_wasm/README.md`) | Nothing in the plan changes execution: the board runs AOT and the host interprets. |

## 3. The phases at a glance

| # | phase | closes | kind | boards |
|---|---|---|---|---|
| 0 | Bugs and prerequisites | X4, L1, L2, T8, N3, L3, F1, F2 | behaviour (fixes) | T-Deck, Guition S3 |
| 1 | The native platform layer | N1, N2, N4, N10, X5 | mechanical | T-Deck, P4; all targets build |
| 2 | One module namespace | S8 | mechanical | one build |
| 3 | Native codecs, file splits, board pins | N8, N11, G5, N7, N13 | mechanical | all five |
| 4 | The call table as the source | T11, T12, T15, T5, T3, I1 (the matrix) | mechanical, one stated meaning | none |
| 5 | The input owner | S1, S3, S4, S5, S15 (trackball), L1, I2, I1 (green) | behaviour | T-Deck, P4 with BLE |
| 6 | Generated binding faces | T1, T9, T10, T13, N9, T16, X8 | mechanical | T-Deck, P4; Zero builds |
| 7 | Shell surfaces | S2a, S6, S11, S14, S15 (names, `ENDS`, dead code), S10 (dead helper), C1 | mechanical, one named change | P4 spot check |
| 8 | The toolkit | T7, G2, T2, I3, I4 | mechanical, one named change | T-Deck, P4 |
| 9 | Workstation | G1, S7, S9, S10, S2b, G6 | mechanical | P4 (Wi-Fi panel), T-Deck |
| 10 | App rows and codes from the table | T14, G4, N6, N12 | mechanical | P4 ELF suite, one S3 |
| 11 | One timer scheduler, one Lua `moybyte` table | B1, N5, T4, N14, B2, S15 (`Scheduler`) | behaviour | one S3, one P4 |
| 12 | The Player | S16, G3 | mechanical | T-Deck |
| 13 | The store: one body, the trial's door, backup | N15, X1, E1 (the archive, restore, the host tool) | mechanical, plus the door and the archive | T-Deck, P4 |
| 14 | The canvas leftovers | K1, K2 | mechanical | P4, Guition P4; a web build |

X6 is not a phase: every phase's last commit corrects the documents it
falsified (`.claude/rules/docs.md`: grep the vocabulary it moved).

## 4. The phases

Every phase runs in its own worktree, lands as one outcome, and is checked
the same way unless it says otherwise:

- **host:** the focused suites while working, then `tools/preflight.sh` on
  the stacked wave (§5);
- **goldens:** `tests/shell_goldens/hashes.json`, the cart raster's
  `tests/spec_conformance/hashes.json` and every pixel test unchanged, or the
  re-bake the phase names;
- **boards:** `tools/board.py pass` for the boards named, once, at the end of
  the wave. Numbers go to their issues (#66, #58), not here.

### Phase 0. Bugs and prerequisites

Closes X4, L1, L2/T8, N3, L3, F1, F2.

**Changes**

- **X4, first.** A moved golden shows what moved.
  - `tests/test_shell_goldens.py`'s failure writes each moved surface's
    rendered frame as a PNG under the build directory and prints one command.
  - `+tools/golden_diff.py REF` renders the same surfaces at `REF` in a
    temporary worktree (through `tools/golden_png.py`'s wrap) and writes
    before, after and a marked diff per surface, with an index page.
  - CI uploads that folder when the golden job fails.
  - The other hash-pinned pixel tests take the same failure helper from one
    place under `tests/`.
  - A re-bake's commit names its surfaces; its review reads the diff.
- **L1.** Mic cancels the field of a shell app. `cancel_field`'s last arm
  (`runtime/shell/console_text.py`) asks the top app's own context
  (`app_cancel` on its `AppHost`) where the app defines no `cancel_field`.
  Files' rename, Appearance's name and Storybook's fields then give their
  draft back. Phase 5 removes the ladder this arm sits in.
- **L2, T8.** `native/moy_ui/moy_theme.c` reads a package through
  `native/moy_spine/moy_json.c`'s scanner and a hundredths helper; its own
  reader goes. The loader keeps its refusals: depth 8, and no NaN or
  Infinity. A theme target, `native/moy_ui/fuzz_theme.c`, joins the native
  fuzz lane.
- **N3.** One wire-order call (`moy_gfx_wire`) sets text, shape and path
  together; the three setters and their partial callers go. The copied
  `can()` and `isqrt64` fold. `tests/test_ui_instance.py`'s scan covers
  `native/moy_gfx`, with each file-scope name's reason, which records why
  L3 is not a bug.

- **F1, F2.** The gate runs every board suite, so its tests have to be
  deterministic first. On the Guition S3, `test_a_stuck_run_with_the_vm_down_is_ended`
  and `test_twenty_soft_resets_leave_psram_flat` (both over `tests/on_glass.py`)
  each run twenty times in a row from a cold boot. A failure is read to its
  cause: a deadline in the test, or a race in the stop path, and fixed where
  it is. Twenty clean runs of each close the item.

**Mechanical or not.** L1 changes behaviour on the T-Deck. T8 must not: a
theme that loaded loads the same, and one that was refused is refused. N3 and
X4 are mechanical.

**Check.** Host suite. The theme corpus (`tests/test_theme_loader.py`,
`tests/test_theme_store.py`) gives the same verdict for every source; a
changed verdict is reviewed by name. Goldens unchanged: none of these draws a
different static frame. T-Deck: Mic in a Files rename, an Appearance name
and a Storybook field. Guition S3: the two tests' twenty runs.

**Guards.** The fuzz target; the extended instance scan; a row per shell app
in `tests/test_app_field_typing.py` (Mic reverts the draft and opens no
menu).

**Size.** About +350 (tool, target, tests), −240.

**Depends on.** Nothing. Everything else depends on X4.

**Closes on the issues.** #249's flaky-test item.

### Phase 1. The native platform layer

Closes N1, N2, N4, N10, X5.

**Changes**

- `+native/moy_kernel/moy_platform.h`: on-a-board, threads and browser
  decided once; `moy_now_us` and `moy_now_ms`; a portable mutex, plain and
  recursive. `moy_sync.h`'s platform half folds into it; its critical
  section stays what it is. Every per-module probe macro, private clock and
  lazily-made mutex copy becomes a use of it.
- `+native/moy_kernel/moy_kmem.h`: one allocator whose flags say small
  (kpool), PSRAM, internal allowed, DMA. Each of the private helpers becomes
  one call **with the policy it has today** written in its flags. No block
  changes where it lands.
- Each consumer of an optional symbol includes the owning header and marks
  the name `#pragma weak`; the hand-typed weak prototypes go. An image that
  denies the module keeps a declaration, generated.
- `+native/moy_kernel/moy_tasks.h`: one table of every task (name, stack,
  priority, core per chip, PSRAM stack) and one spawn call. Every value is
  the one in the tree today.
- X5: `native/moy_kernel/README.md` gains the threading model, one page: the
  tasks from that table, and for each what it may touch and through which
  lock.
- `tests/test_moy_spine_twins.py:572` asserts the platform header, not the
  probe's text. `moy_kernel.h`'s sentence about the placement test is
  corrected.

**Mechanical or not.** All mechanical. The audit's second step under N2
(the kernel's own small tables moved into kpool) changes placement and is in
§7.

**Check.** All five board images and the web runner build. Host suite and
the sanitizer lane (`tools/asan_pytest.sh`). T-Deck and P4 pass: the heap
lines and the PERF line read as before (#66, #58). Goldens unchanged.

**Guard.** `+tests/test_native_platform.py`, a scan of our C: the probe, the
raw clocks, semaphore creation, `heap_caps_*alloc`/`malloc`/`calloc`,
`xTaskCreate*` and weak prototypes appear only in their owner and in an
allowlist whose every entry has a reason (ISR rings, DMA drivers,
`native/p4`, the Lua allocator's pool, the pinned `mp_task` copy, vendored
trees).

**Size.** About −1,200, across on the order of a hundred C files.

**Depends on.** Phase 0.

### Phase 2. One module namespace

Closes S8.

**Changes**

- `runtime/__init__.py` gains a finder that resolves a flat runtime module
  name to the same module object as its `runtime.` name.
- A codemod rewrites the two-arm import (`import x`, else
  `from runtime import x`) to the device form in every file. Only that exact
  pair matches: an `ImportError` for a module a board may really lack stays.
- `runtime/host/host_app.py`'s alias list goes.

**Mechanical or not.** Mechanical. It touches 222 files, so it runs when no
other Python phase is open.

**Check.** Host suite; preflight with the web blob (the browser build is
already flat, and `inspect.getsource` feeds it); one board build whose frozen
module set is byte-for-byte the list it was. Goldens unchanged.

**Guard.** `tests/test_one_namespace.py`: after importing every runtime
module, each flat name and its `runtime.` name are one object; no runtime
file holds the two-arm form.

**Size.** About −2,000.

**Depends on.** Phase 0.

### Phase 3. Native codecs, file splits, board pins

Closes N8, N11, G5, N7, N13.

**Changes**

- `+native/moy_spine/moy_sb.h` (one growable builder with a failed flag and
  an allocator parameter) and `+native/moy_spine/moy_codec.h` (base64,
  base64url, hex, UTF-8: valid, next, put, whole prefix). The copies become
  uses. `moy_http_resp_parse` moves beside the request parser.
- One UTF-8 decoder, the strict one. `moy_path.c` calls it and keeps, on
  its own side, what it does today with a byte the decoder refuses (an
  overlong lead is taken, a control draws as `?`). The two files' rules stay
  two; merging them is in §7.
- `moy_kernel.c`: the recovery floor moves to
  `+native/moy_kernel/moy_recovery_floor.c` and the watchdog to
  `+native/moy_kernel/moy_wd.c`. The pinned `mp_task` copy does not move
  (`native/moy_kernel/mp_task_calls.txt`).
- `moy_os.c` splits by concern into files, sockets and processes over a
  private header (`+native/moy_elf/moy_os_int.h`).
- N7: each board's `board.toml` gains the `os` rows its hand defines stand
  for, the drivers read `MOY_HW_*`, and the Zero's `/gpio` allowlist comes
  from the lease rows `tools/board_config.py` already emits. The hand pin
  defines and the test that held two copies equal go.
- N13: `moy_htab.h` says what it is (the table most handles use; `moy_dev`
  keeps its own) and no longer names a deleted Python file; `moy_gpio.c`
  names the lease row as the allowlist's source.

**Mechanical or not.** Mechanical. Every string draws as it did.

**Check.** Host suite; the fuzz lane with a codec target. All five boards
boot, with touch, keys and audio smoke (a wrong pin is a dead peripheral on
one board). The recovery floor on glass and the P4's ELF suite.

**Guards.** `+tests/test_native_codecs.py`: no private builder, base64, hex
or UTF-8 helper outside `native/moy_spine`. `tests/test_board_toml.py`: no
`mpconfigboard.h` defines a pin-shaped `MOY_*` name. `tests/test_mp_task_calls.py`
as it is.

**Size.** About −350 and 100 defines; 6,200 lines regrouped at no net
change.

**Depends on.** Phase 1 (the split must not carry `moy_os.c`'s private
platform section with it).

### Phase 4. The call table as the source

Closes T11, T12, T15, T5, T3, and writes I1's matrix.

**Changes**

- T11: `tools/gen_os_table.py` becomes the entry point over a package,
  `tools/gen_os/` (the table and its rules, the C prototype parser, one
  emitter per tier). Its outputs do not change by a byte.
- T12: the host library's parts and the index's signatures move from
  `tools/moy_index_spike.py` into `+runtime/host/native_parts.py`. The fuzz,
  sanitize and host commands stay as `+tools/native_check.py`; the one-off
  bench, sizes and ci-time commands go (#224 holds their numbers).
- T15: `tools/bench_unix_mp.py` and the comment that names it are deleted;
  `tools/music_mockups.py` moves under `experiments/`; the two docstrings
  that cite a test that does not exist point at T3's; `api_floor` moves to a
  test helper.
- T5: one `errors` section in `native/os_calls.json`: code, Python
  exception, errno, message, Lua message. It generates the C and Lua header
  and the host's table. `MOY_APP_FULL` gets one meaning.
- T3: `+tests/test_every_row.py`. Each row gains an `example`. Generated
  probes (a Python app, a Lua app, a wasm module and a WASI command assembled
  by `tools/wat.py`) call every row a tier projects and print one normalised
  line each; every tier's trace is compared with Python's, on CPython and on
  the board model.

- I1: `+tests/test_input_matrix.py`, the input conformance matrix.
  - Rows: every widget kind (field, list, button, popover, dialog, text
    view).
  - Columns: every input as its driver delivers it, through the drivers'
    own entry points in the host's input part (`moy_kbd.c`'s matrix in text
    and move modes, `moy_hid.c`'s USB and BLE keyboards, `moy_touch.c`, the
    trackball, a gamepad, Esc, Mic), never by setting the input table or
    calling the toolkit.
  - Layers: a Python shell page, a Python cart app, a Lua app and a wasm app
    from T3's generated probes, and an ELF app the SDK builds for the host
    (`tools/elf_cart.py`'s host target, as `tests/test_elf_commands.py`
    builds its modules), run through the host's loader. Every column runs on
    the host.
  - A cell that fails today is listed in the test by name, with the phase 5
    or phase 8 change that clears it. The list is a hand-off inside the
    sprint, emptied by those two phases; it is not a pinned count.

**Mechanical or not.** Mechanical except FULL: today it reads "the grant
table is full" on Python and "the answer is too long" in Lua. The phase's
commit states the one meaning, which is the C's own: no room, whether a
table is full or an answer does not fit its buffer (`moy_app.c` answers
FULL for both). Lua's text changes to it; Python keeps `OSError(ENOSPC)`.
Where T3 goes red because tiers disagree, the disagreement is fixed in this
phase and listed in the commit.

**Check.** Host only. `tests/test_os_table.py`'s drift test unchanged.

**Guards.** T3 is the guard, and the net for phases 6, 8, 10 and 11; the
matrix is the net for phases 5 and 8. With
it: nothing under `runtime/` imports from `tools/`; `tests/test_tools_smoke.py`
fails a tool that nothing names.

**Size.** About +1,000 of test and generator (T3 600, the matrix 400), −550
of code, 1,850 lines out of `tools/`.

**Depends on.** Phase 2 (both rewrite imports in `runtime/host`).

### Phase 5. The input owner

Closes S1 (step 0, a, b, c), S3, S4, S5, S15's trackball item, L1 for good,
I2, and turns I1's matrix green for everything but phase 8's cells.

**Changes**

- **The surface protocol.** `layers.Layer` gains defaulted verbs: the
  `AppHost` a surface draws through, `typing()`, `cancel()`, `back()`,
  `has_page()`, `close()`, the page title, the ball's steps. Every surface answers
  it: each registered app, Settings, Tasks, each tab layer of the Editor, and
  the layer the Player draws in.
  The `getattr` duck typing and `page_app`'s special cases go.
- **`+runtime/shell/input_policy.py`, the owner.**
  - *Typing* is derived once a frame from the surface that holds the keys and
    applied only when it changes, with the seed rule intact. The 45 setter
    calls, the 8 `nav.text_mode` calls (and that row of the call table), the
    direct writes and the state kept only to restore the mode all go. A
    cart's `textmode()` flag lives on the Player.
  - *Cancel* asks the modal overlay, then the holder. The ladder in
    `console_text.py` and Settings' own copy go.
  - *Back* is one cascade. The router's B, the bar's chevron, the window's
    chevron and the host's Esc all call it; the host maps Esc to `stop` as a
    board does, and `takes_escape` goes. Each app's `back()` shrinks to its
    page step.
- **I2.** Each of the four policies has one owner and one source. The
  toolkit decides from what is focused (`api_typing`, `api_cancel`,
  `api_back`, in C, for every tier); the policy asks it and applies the
  answer; no shell page and no cart sets text mode, cancel, back or the
  trackball's meaning by hand. The trackball is the fourth: what it does on
  a surface is that surface's `ball_steps`, asked in one place.
- **S3.** The C input table publishes the typed-key press edge each frame.
  The policy spends it once on a mode switch. Surfaces read it; the nine
  `KeyEdge` instances and the per-surface seed fields go. About's licence
  page reads it too, and keeps its own scroll (S12 is in §7).
- **S4.** Ctrl+Z and Ctrl+Y are routed once, in the Editor's key path, to
  the history for every tab. Five of the nine `KeyEdge` instances exist only
  to catch those two keys in Map, Music, Scene, Blocks and Paint; with the
  route in one place they are deleted, not ported. Past a tab's own stack
  Ctrl+Z then carries on into the journal, as the bar's UNDO does.
- **S5.** One `leave_surface(kind)` calls the surface's `close()` on every
  exit: home, back, the window X, an app jump, the hand-off to an update. The
  window X's per-kind ladder and `open_app`'s two named commits go. The
  `Layer` protocol's `on_enter`/`on_leave`, which the router never calls,
  go too: a surface with a leaving step does it in `close()`.
- **The trackball.** One `ball_frame` serves the host driver and the device
  spine.

**Mechanical or not.** Behaviour-changing, by design: it is the T-Deck's
typing path, Mic, back from four entry points, and the simulator's Esc.

**Check.** Host suite, `tests/test_frame_alloc.py` (the per-frame derive
allocates nothing), `tests/test_app_field_typing.py` extended to the shell
apps. **Re-bake "input owner":** static frames should not move; any that
does is reviewed from phase 0's pictures under this name. T-Deck: typing in a
field of Files, of Settings, of Music, of Appearance and of a cart app; Mic
in each; W A S D moving; the trackball. P4: two desk windows, one typing and one a game; a BLE
keyboard's held key.

**Guards.** Phase 4's matrix, with no failing cell left that names this
phase. And `+tests/test_input_policy.py`:
- a table over every surface in `APPS`, Settings, the Editor's tabs and a
  cart app: focusing a field types; Mic reverts and opens no menu; Esc, B and
  both chevrons end in one state; a menu over a field is game mode and typing
  again after; a pending edit survives every exit; on every Editor tab,
  Ctrl+Z after the tab's stack empties equals the bar's UNDO;
- a scan: the mode setter, a write of `input.text_mode`, `set_game_mode(`
  and `KeyEdge(` appear only in the owner and the input layer.

**Size.** About −860.

**Depends on.** Phases 0 and 2. Phase 1 has landed before S3 edits
`native/moy_input`. It lands on top of phase 4 (§5), whose matrix it turns
green.

**Closes on the issues.** #242's "one owner per input policy", and its
matrix item once phase 8 clears the last cells.

### Phase 6. Generated binding faces

Closes T1, T9, T10, T13, N9, T16; measures X8. Decided: §6, decision 2.

**Changes**

- A per-module table, `face.json`, beside each module's C: each row names
  its C function, and the generator reads the prototype from the header, so
  the row adds only what C types cannot say (spans, ownership, out buffers,
  the error table to raise from, constants, defaults, no-allocation rows,
  the targets a row is absent on).
- The generator emits, committed and drift-tested: the MicroPython face
  (`modmoy_<m>_face.c`, included by the hand file's globals table), the
  ctypes face, the mirrored structures (T9, N9: from the headers), the
  constants (T13, N9) and the one table `runtime/__init__.py` registers host
  faces from (T10).
- Per-tier converters are written once by hand: canvas, context, span, error
  raise, and the one library loader that replaces the copies.
- Modules switch in this order, each after its four columns agree (hand and
  generated, on CPython and on unix MicroPython's board model): `moy_log`,
  `moy_lease`, `moy_alloc`, `moy_prof`, `moy_mic`, `moy_midi`, `moy_http`,
  `moy_store`, `moy_ufiles`, the app ABI's crypto, clipboard and theme rows,
  `moy_evq` without its callbacks, `moy_input`, `moy_net`, `moy_gfx`.
- Callbacks, iterators, object types and the hot-path specials stay by hand,
  as rows marked `hand` so their names and arity are still checked. T16: the
  generator emits the context form and the run form of a verb from one row;
  the two forms are not merged.
- X8: the build's printed headroom for the Zero and the T-Deck is read
  before and after, and the difference goes to the phase's issue.

**Mechanical or not.** Mechanical. What can go subtly wrong is integer
truncation, None, and allocation on a frame path; the four columns and the
no-allocation rows are there for that.

**Check.** Host suite; T3 green in every column. T-Deck and P4 pass; the
Zero's image builds with headroom above its floor. Goldens unchanged.

**Guard.** `+tests/test_face_parity.py`: for every `face.json`, the
MicroPython globals, the ctypes names and the table's rows are one set less
the declared absences, and one generated script gives equal answers on both
runtimes. The generator's drift test.

**Size.** About −6,000 to −8,000 net.

**Depends on.** Phases 1 (it edits the same MicroPython files lightly) and
4 (the split generator, the error table, T3).

**Not in this phase.** `moy_glass`, `moy_play`, `moy_wasm` and `moycore`
(§7).

### Phase 7. Shell surfaces

Closes S2a, S6, S11, S14, S15's name list, `ENDS` order and dead code, S10's
dead helper, and C1.

**Changes**

- S2a: Settings' six view flags and their open and close pairs become one
  page stack over a table; `back()` is "pop, else leave". `wifi_pw` is a
  string.
- S6: the frame loop draws the status strip after the content layer, from
  the top surface's kind through one `where` map in `bar_layer`. The 30
  calls from 15 files go.
- S11: Files' OPEN goes through the intents, as "Open with" does. Paint and
  Notes declare their types. Projects and scripts keep their verbs. My Art's
  two opens take the same route.
- S14: the story's and the theme editor's undo lists sit on `History` with a
  snapshot codec.
- S15: `moybyte.py` uses `devices.NAMES`, the identity map goes, and `ENDS`
  is an explicit ordered registry.
- S15, S10: `AudioSession.take_pcm`, `PerfMeters.perf_breakdown_raw`,
  `Layout.grid_content_w` and `Workstation._icon_btn` are deleted **after** a
  search of moy-spec's runner, the carts repo and the web runner's script.
- C1: the launcher's Make tab marks a cart the crash guard has turned off.
  The card takes a corner mark in the idiom the favourite's star already
  uses, drawn from `ws.cart_broken(cart)`. The cart stays on the tab and
  stays editable. `app_guard.broken_ids()` either gains this caller's use or
  is deleted with its test rows. The check's verdict is to wire it: the
  query is tested, the corner idiom exists, and the alternative is deleting
  a path the guard's design counts on. Whether the Editor shows the mark
  too stays #160's open question and is not in the sprint.

**Mechanical or not.** Mechanical, with one named change:
- **Re-bake "broken-cart badge":** one new golden surface, a struck-out
  cart on the Make tab. No stored golden holds such a cart, so none of the
  existing surfaces may move.

S6 must move nothing: the strip is the same strip, drawn from one place. If
a surface's overlay order makes that impossible, that surface keeps its call
and the guard names it with the reason.

**Check.** Host suite; `tests/test_settings_layer_pixels.py`,
`tests/test_content_rect.py`. P4: a frame-cost spot check, because the desk's
repaint path moves (#58).

**Guard.** `+tests/test_shell_surfaces.py`: the strip is drawn from the
frame loop only; Settings keeps no per-page flag; OPEN and a single-candidate
"Open with" land in one app; no surface under `runtime/system`,
`runtime/apps` or `runtime/editors` keeps a scroll offset of its own, the
code editor, the home band and About's licence page excepted by name;
`ENDS` is in its stated
order; a cart the guard turned off is marked on the Make tab and one it
forgave is not.

**Size.** About −340, +15 for the badge.

**Depends on.** Phase 5 (the surface protocol; Settings' seed swallow is the
policy's by then).

**Closes on the issues.** #160: the badge is its last open item.

### Phase 8. The toolkit

Closes T7, G2, T2, I3, I4.

**Changes**

- T7: `moy_ui_ctx_t`'s flat fields become one named sub-struct per family
  (tables, dialogs, popovers, shift frames, floor, kept floor, snap, bar,
  field edit, spread, text), each declared by the file that owns it. The
  context is opaque bytes to every face, so no ABI moves.
- `moy_ui_keep_t` is a union tagged by kind, with named fields. A widget
  that asks for kept state when the table is full is counted (`keep_lost`)
  and told, as a lost stop is.
- I3: that table is the one per-widget store, keyed by id, and a widget's
  state lives while the widget is drawn and is freed the frame it is not.
  A field's edit moves into it: today the context holds one edit for all
  its fields (`fe_id`, `fe_live`, `fe_cancel`, and one start text in the
  instance), which is why a field stacked over another cancels wrongly. A
  field's record holds its edit's start and its caret; the start text is
  the instance's, under the same id.
- I4: no widget is shown at a guessed size. A popover or an anchored menu
  places itself from the size its record kept; with no record yet it draws
  nothing that frame and owes the next, which is what the engine does for a
  layout from a stale measure. Owed frames are counted with the engine's
  other counts.
- The run face moves from `moy_ui_wasm.c` to
  `+native/moy_ui/moy_ui_run.c`.
- T2: the `ui.*` rows gain what the faces need (the Python signature, enum
  arguments, index base per tier, list arguments, the shape of an answer,
  the end form), and `+native/moy_ui/over.json` lists every override key.
  The generator emits the MicroPython verbs, the ctypes verbs, the run face,
  the wasm imports, the Lua verbs and `runtime/toolkit/ui.py`'s bodies
  between markers, and all five override codecs.
- By hand, as now: `label_text`, the single walk of the override dict, the
  verbs marked `hand`, and every docstring in `ui.py`.

**Mechanical or not.** Mechanical, with one named change. `keep_lost` and
the owed count are counts that were not there; no pixel moves for them.
**"Widget state"** (I3, I4) changes two behaviours: cancel in a field
stacked over another gives back that field's own text, and a popover's
first shown frame is at its settled size. A stored golden is a settled
frame, so none should move; one that does is reviewed under this name.

**Check.** Host suite; `tests/test_app_ui.py`; `tests/test_frame_alloc.py`
and `tests/test_ui_opts_seq.py` (the boards' dict path still allocates
nothing). T-Deck and P4 pass. Goldens unchanged, which here is the proof.

**Guards.** `+tests/test_ui_ctx_groups.py`: each engine file touches only
the sub-struct it owns, and 17 kept ids in one context raise `keep_lost`.
`tests/test_ui_opts_seq.py` drives every key of `over.json`. T3 and the
parity test hold the names. Phase 4's matrix has no failing cell left, and
gains two assertions: a stacked field's cancel, and a popover's box equal on
its first shown frame and the one after.

**Size.** About −3,100.

**Depends on.** Phases 4 and 6 (the generator and its emitters), 0 (the
theme reader is already off the context's files), 5 (the matrix's other
cells).

**Closes on the issues.** #242's "explicit widget state" and "measure
first, then draw" items, and with them its matrix item. The four close with
the sprint; #242 stays open for its convention rows.

### Phase 9. Workstation

Closes G1, S7, S9, S10, S2b, and G6's console part. Decided: §6,
decision 3.

**Changes**

- **Services.** `Workstation`'s nine mixins become objects with stated
  interfaces: saves, notices, intents, home, canvas, settings toggles, perf
  meters, and the frame's dirty state, beside phase 5's input policy. An app
  receives the ones its declaration names through `app_context`. The frame
  loop still reaches its collaborators directly.
- **S7.** No module outside the console reads a private name of it. The
  dozen most-read become public verbs on their service; `ws._dirty = True`
  becomes `mark_dirty()` at all 214 sites; `ws.app(id)` replaces
  `_apps_by_id` and the per-app attributes; `player.is_app()` replaces the
  reads of `_is_tool`. The same rule is applied to `wm`, `bar_layer` and
  the Player.
- **S9.** The nine cheap legacy forwards go. The six big ones (`cart`,
  `screen`, `ns`, `cart_error`, `menu_view`, `sheet`) stay as properties on
  the façade: code that moves into a service is rewritten to reach the
  owning object in any case, and the rest of their sites are outside callers
  and tests the carve does not need to touch (§7).
- **S10.** `console_notices.py` splits: the intents glue to its service, the
  pause, confirm and system-menu cards to `+runtime/shell/shell_cards.py`,
  the pre-toolkit draw trio beside `bar_layer`. New modules are added to git
  before the first board build (the stager prunes untracked files) and to
  the Zero's allowlist.
- **S2b.** Settings is a registered app with an `AppContext` carrying the
  Wi-Fi, Bluetooth and USB roles. The comparisons to `"settings"` go; Tasks
  takes the same route.

**Mechanical or not.** Mechanical, and the largest pass in the sprint.
S2b is where behaviour could slip: the Wi-Fi lease, the USB drive's hold,
and Settings resuming the cart it was opened from.

**Check.** Host suite and the web build (`inspect.getsource` users; the
serial vocabulary `tools/p4_*` speak). `tests/test_frame_alloc.py`. Goldens
unchanged. P4: the Wi-Fi panel, the USB drive. T-Deck: a frame-cost check,
since a store on a small service is cheaper than one on `Workstation` and
must not be dearer (#66).

**Guard.** `tests/test_console_facade.py`, as final rules and not counts:
no `ws._name`, `wm._name`, `bar_layer._name` or `player._name` read from
outside its owner; the pinned forward set is the six big names and nothing
else, and no service's module reads one of them; no kind compared to
`"settings"` or `"tasks"` outside the registry; each service's module holds
only its own verbs.

**Size.** Near zero net; about 60 files and their tests touched.

**Depends on.** Phases 5 and 7.

### Phase 10. App rows and codes from the table

Closes T14, G4, N6, N12.

**Changes**

- T14: one generic Lua dispatcher packs a row's arguments from its `in`
  types and shapes the answer from `lua_ret`. The per-row functions in
  `moy_app_lua.c` go; the file object type and the JSON pair stay by hand.
- G4: the app door's `switch` in `moy_app.c`, and its no-argument list, are
  generated from the rows, as the WASI tier's rows already are.
- N6: each `MOY_APP_*` and `MOY_EVQ_*` code's errno is a column of the
  table, emitted in both numberings; the four hand mappings go. The rule for
  a send's arguments moves into the kernel (`moy_evq_send_text`), and the
  ELF and WASI tiers only translate its code.
- N12: `moy_kernel.h` states the convention (0 is OK, a negative value is a
  code, the code space is the module's). `MOY_HTTP_OK` becomes 0 and
  `moy_evq`'s one negated return joins the rest.

**Mechanical or not.** Mechanical. N12's sign changes are internal; every
caller is in the tree.

**Check.** Host suite; T3's Lua, wasm and WASI columns green before and
after; `tests/test_os_table.py`. The P4's ELF suite and one S3 with a Lua
app.

**Guard.** In `tests/test_os_table.py`: no `case MOY_ROW_` and no
`case MOY_APP_` outside a generated file; every `MOY_*_OK` is 0; no per-row
Lua function outside the hand list.

**Size.** About −1,100.

**Depends on.** Phases 3 (`moy_os.c` is split), 4 (T5's table, T3) and 6.

### Phase 11. One timer scheduler, one Lua `moybyte` table

Closes B1, N5, T4, N14, B2, S15's `Scheduler` item. Decided: §6,
decision 1.

**Changes**

- **Timers.** `moy_evq`'s per-process timers are the scheduler for every
  tier. Python's `every`, `after` and `alarm` post through it, as its
  messages already do, and `Wakes` keeps only the id-to-callback map. A Lua
  service's `every` and `after` use it (its `at` already does); its timer
  table, its poll and the `ALARMS` prelude go. A waiting service task runs
  the kernel's timers itself and sleeps to the deadline they return, so a
  service's timer is not quantised to the console's frame.
- **T4.** A Lua app's run takes the `moybyte` table the Lua service has, in
  C. Its wakes are the kernel's, owned by the run's pid; the Player fires them
  as a frame starts. The Lua-source JSON encoder, `_RunWakes` and the
  source-text fire path go.
- **N14.** One `moycore` sandbox function serves the cart run and the
  service; libmoy's cart layer stays above it.
- **B2.** A Python process's notification posts through the kernel's queue,
  as every other tier's does.
- **`Scheduler`** splits along what is left: Python processes, Lua services,
  compiled services.

**Mechanical or not.** Behaviour-changing: when a wake fires within a frame,
and its order against other wakes. **Re-bake "timers":** the semantic
traces that record wake order; no pixels.

**Check.** Host suite: `tests/test_semantic_traces.py`,
`tests/test_lua_services.py`, `tests/test_app_entries.py`,
`tests/test_frame_alloc.py` (a wake no longer allocates a source string).
One S3: a Lua service's timers through a VM stop. One P4: a Lua app's
`every` beside a Python app's.

**Guard.** `+tests/test_one_scheduler.py`: a timer made on any tier is a
row of the kernel's table; a Lua app's and a Lua service's `moybyte` names
are one set; a service and a cart see one set of banned globals; no timer
table in `moycore_svc.c` and no due-list in `Wakes`.

**Size.** About −600; 1,220 lines of `Scheduler` regrouped.

**Depends on.** Phases 1, 4, 6 (`moy_evq`'s face), 10 and 12 (the Player's
frame start).

### Phase 12. The Player

Closes S16, G3.

**Changes.** The app-cart face (the room, the field focus, back and cancel
into the app) and the front hand-off and pacing become two objects the
Player holds. `tick`'s hot path stays on the Player: an attribute hop costs
on a board. `is_app()` is the public question.

**Mechanical or not.** Mechanical.

**Check.** `tests/test_frame_alloc.py`, the semantic traces, the cart
raster's goldens unchanged. T-Deck: per-cart fps as before (#66).

**Guard.** `+tests/test_player_parts.py`: the Player defines no method of
the app face or the pacing half, and the frame path makes no call through
either.

**Size.** Near zero net; 2,813 lines in three objects.

**Depends on.** Phases 5 (which removes the Player's typing state) and 9.

### Phase 13. The store: one body, the trial's door, backup

Closes N15, X1, E1. Decided: §6, decision 4.

**Changes**

- N15: the C store (pack, files, img, fs) is a host part, as `evq` is.
  `runtime/carts`' four twins become thin faces over it, and the tests that
  held each pair to one answer become plain tests of the one body. The test
  oracles (`tests/spine_twin.py`, the Python index, `tests/app_twin.py`)
  stay.
- X1: the store carries a format stamp, written when a
  store is made and read by its strict readers. A fixture store in the
  trial's format is committed. `sweep_store`'s generation-gated pass is the
  one door: a later format change ships as a step there, with a test that
  takes the fixture through it and keeps the bar (one small read when
  warm). An export of a kid's work runs before a step: E1's archive.
- E1: one call writes one archive, and one call restores it.
  - The archive is a plain zip of the store's layout plus a small manifest
    that carries X1's format stamp and the firmware's version, written by
    the store's own packer (the one body N15 leaves), which streams a
    member of any size.
  - In it: every cart that is not a built-in, built-ins a kid has edited,
    the user files, each cart's saves, and the journal. Thumbnails are left
    out; they are made again.
  - Restore onto a fresh card or console keeps the seed and provenance
    rules, so a re-seed does not overwrite a restored built-in that carries
    a kid's edits. A restore of an archive with an older stamp goes through
    the door above.
  - The archive lands in the user files, where the USB drive and SFTP
    already reach it; no new transport.
  - `+tools/console_backup.py` pulls and pushes an archive from a host, and
    the dev channel has the two calls as commands.
  - The on-device action, a row in Settings with its confirm and its restore
    picker, is not in the sprint (§6's rule, §7).


**Mechanical or not.** N15 is mechanical. X1 adds a stamp to a new store;
no existing reader changes its answer. E1 adds two calls and draws nothing,
so no golden moves.

**Check.** Host suite; `tests/test_store_native.py`, `tests/test_moy_files.py`,
`tests/test_moy_img.py`, `tests/test_moy_store.py`. T-Deck: a cold boot's
store sweep costs what it did; an archive of a full card is written without
running the heap out. P4: the same archive restored onto its internal
flash.

**Guard.** `+tests/test_store_one_body.py`: no module under `runtime/carts`
defines a body for a function the C store exports; the fixture store opens,
unchanged, on the current tree; a store archived and restored into an empty
root reads back equal, file for file, thumbnails aside, and a re-seed after
it leaves an edited built-in as restored.

**Size.** About −1,300, +550 (the stamp and fixture 200, the archive and
the host tool 350).

**Depends on.** Phase 6 (`moy_store`'s face is generated by then).

**Closes on the issues.** X1. #127's archive and restore exist and are
reachable from a host; the issue stays open for the on-device action and
for LAN auto-backup (§7).

### Phase 14. The canvas leftovers

Closes K1, K2.

**Changes**

- K1: one one-buffer compositor. `WebCompositor`
  (`firmware/web_runner/web_canvas.py`) and `HostCompositor`
  (`runtime/host/host_canvas.py`) become one class in a module the web
  runner stages and the host imports, with the raster module handed in
  (`moy_gfx` in the browser, the ctypes binding on the host). The boards
  deny the module; it is not theirs. The class carries the three last-frame
  verbs (`copy_last`, `shift_last`, `copy_last_into`), so the browser's desk
  takes the kept-frame paths the host and the boards take. The host keeps a
  thin subclass for a test's ring of buffers and for the desk's variant.
- K2: one C body composites a game canvas onto a P4's glass. It holds the
  choice (the PPA's banded nearest for crisp pixels, its bilinear scale,
  else the CPU kernel), the fit test and the demotion on a PPA error. The
  kernel's front (`native/moy_glass/modmoy_glass.c`) and
  `P4SystemCanvas._composite_paint` (`device/p4_canvas.py`) both call it;
  the PPA gains the one kernel-side entry it lacks, for the crisp path. The
  rotated compositor's paint route calls the same body, so it takes no
  Python callback for it.

**Mechanical or not.** Mechanical in pixels. Two paths change which code
runs: the browser's desk starts using the last-frame verbs, and the front's
crisp frames go through the PPA where they went through the CPU kernel
(byte-identical by the PPA path's own contract).

**Check.** Host suite and goldens unchanged. A web build and the browser
suites (`make preflight-web`). P4 and Guition P4 passes, with #58's composite
numbers read before and after; crisp and smooth each looked at on glass, and
a forced PPA error demoting to the CPU kernel.

**Guard.** `+tests/test_one_compositor.py`: one class defines the
one-buffer compositor's verbs, and the web's and the host's compositors are
it; `device/p4_canvas.py` holds no branch on crisp or smooth around a blit.

**Size.** About −20 (K1) and −60 of Python for +40 of C (K2).

**Depends on.** Phases 1 and 3 (they edit `native/moy_glass` and `native/p4`
first) and 2.

**Not in this phase.** The rest of #245's second item: `present_frame`, its
patch pictures and `blit_strip_async` stay Python (§7).

**Closes on the issues.** #245, with its second item closed at the narrower
scope the check found.


## 5. The order

Seven waves. A wave's phases run side by side; a wave starts when the one
before it has landed on `dev` with a green preflight and its board passes.
At most three workers, and the board passes of a wave run one after
another.

| wave | phases | why they do not overlap |
|---|---|---|
| 0 | 0 | alone: everything leans on X4 |
| 1 | 1 ∥ 2 | 1 is C under `native/`; 2 is Python imports under `runtime/` and `device/` |
| 2 | 3 ∥ 4 ∥ 5 | 3 is native C outside the faces; 4 is `tools/`, the table and tests; 5 is the shell. Three shared files, each given one owner: `native/moy_input/moy_input.c` is phase 5's; `tools/board_config.py` is phase 3's, and phase 4's comment fix there rides on top; `native/os_calls.json` is phase 4's, so phase 5's removal of one row is its last commit, on top of phase 4, where it also turns phase 4's input matrix green |
| 3 | 6 ∥ 7 ∥ 14 | 6 is the faces and `runtime/host`'s bindings; 7 is shell surfaces; 14 is the compositors (`runtime/host/host_canvas.py`, which is not a binding, the web runner's canvas, `native/moy_glass`, `native/p4`, `device/p4_canvas.py`), none of which phase 6 generates |
| 4 | 8 ∥ 9 | 8 is `native/moy_ui` and its faces; 9 is the console |
| 5 | 10 ∥ 12 ∥ 13 | 10 is the app ABI's C and the generator; 12 is `runtime/run/player.py`; 13 is `runtime/carts` and the store's host part |
| 6 | 11 | alone: it changes behaviour across three tiers and wants a quiet tree |

Fifteen worker-phases in the same seven waves. Then the gate: one
`tools/preflight.sh`, one pass over every board with every on-glass suite
(which is why F1 and F2 are in phase 0), and the CI run read, not only
preflight.

The sprint removes about 16,500 to 18,500 lines net, most of them faces
(phases 6 and 8) and import boilerplate (phase 2). The folded issues add
about 900 of that back: the input matrix, the archive and its tool.

## 6. Owner decisions

### Decided (owner, 2026-11-11)

**1. One timer scheduler.** The C kernel's `moy_evq` serves Python, Lua and
compiled programs (phase 11). Python's messages already ride it and a Lua
service's alarms already use it, so only the timers were apart: now one id
space, one missed-tick rule, one place to look.
- *What it costs:* wake order within a frame can change (the named
  re-bake), and a waiting service task runs the kernel's timers itself so
  its timers are not quantised to the frame.
- *Declined:* keeping three schedulers.

**2. The binding faces are generated, and the host stays on CPython**
(phases 6, 8, 10). Unix MicroPython is the parity oracle, as it already is
for 67 test files.
- *Declined:* running the host itself on unix MicroPython over the modules
  a board has. It would delete the ctypes faces outright, and it would cost
  the test suite (427 of 540 test files import the runtime in-process on
  CPython, 172 use `monkeypatch`), the pygame simulator, the CPython-only
  hardware stand-ins, the in-process render tools and pdb.
  `docs/refactor_2026-11/tiers.md` §2.4 has the comparison.

**3. `Workstation` is carved into services handed out through
`app_context`** (phase 9), the input policy first (phase 5), which proves
the shape before the rest follow.
- *What it costs:* the largest pass in the sprint, across about 60 files
  and their tests, and S2b's risk on the Wi-Fi and USB paths.
- *Declined:* keeping the mixins and only renaming the most-read privates.

**4. The trial's store.** A format stamp; a committed fixture of a
trial-format store; `sweep_store`'s generation-gated pass as the one door;
an export before any step, which is #127's archive (E1); and no migration
written until a format changes (phase 13). What the sprint buys is that the
first migration has a door, a fixture, a test and a backup.
- *Declined:* no mechanism, with the format frozen for the trial and a
  change handled by export, wipe and re-import by hand.

### The rule for everything else (owner, 2026-11-11)

"If they slow down the sprint they go after; if NOT implementing them slows
down the sprint, they go into it." Applied to each place where the plan
would change behaviour or reach further than the audits' mechanical core:

| item | verdict | reason |
|---|---|---|
| S12, the licence page onto `ui.text_view` | after | Doing it adds a re-bake of the licence surface in 22 golden configurations with a look to review. Leaving it costs one key-edge conversion in phase 5 and one named exception in phase 7's guard. |
| S4, Ctrl+Z into the journal on every tab | in, phase 5 | Five of the nine `KeyEdge` instances phase 5 removes exist only for the per-tab Ctrl+Z handlers. Without S4 each handler is ported to the new edge by hand and kept as an exception in the guard; with it they are deleted. |
| T5, one meaning for `MOY_APP_FULL` | in, phase 4 | One error table generates one text per code. Keeping two would need a per-tier override column and an exception in the every-row test. The meaning is the C's own. |
| N8, `moy_path.c` on `moy_text.c`'s strict UTF-8 rule | after | Sharing the decoder needs no rule change. Changing the rule alters what a story draws for a stray byte, which wants a look on glass and may move a golden. |
| S9, the six big forwards and their sites | after | About 210 source sites and 1,160 test sites to rewrite, and the carve is no faster for it: the services' own code is rewritten to the owning object anyway, and six properties on the façade cost phase 9 nothing. |
| E1's on-device action (a Settings row) | after | The archive, restore and host tool need no screen. The row needs a confirm, a restore picker and a re-bake of Settings' surfaces, while phases 7 and 9 are rebuilding Settings. |
| I3 and I4, "widget state" | in, phase 8 | Phase 8 regroups the same fields. Leaving them means grouping the field-edit state once and dissolving it later, and the input matrix keeping two failing cells with no final guard. |
| The matrix's ELF column | in, phase 4, on the host | The SDK already builds a module for the host and the host's loader runs it; no cell needs glass. |
| #160's mark in the Editor | after | A second mark and a second golden. The launcher's badge is the issue's stated item, and nothing in the sprint waits on the Editor's. |

### For the owner

- **#127's on-device action waits for the trial's software (owner,
  2026-11-12).** Under the rule, the sprint ends with the archive, restore
  and host tool (phase 13), and a family's one-tap backup in Settings comes
  with the trial's first items. #127 stays open for that row.

## 7. After the sprint

**Before a trial unit goes out**

| id | what | why not in the sprint |
|---|---|---|
| X3 | A standing check of each `*_vendor.json` pin against upstream security fixes; mbedTLS and Dropbear first | A process and a CI job, not a refactor. It gates the trial, not the sprint. |
| X7 | A minimum-firmware field in the manifest | A cart feature in moy-spec's format, under the freeze. The trial's carts need it. |

**The first trial items**

| id | what | why not in the sprint |
|---|---|---|
| E1 (action) | The backup's on-device action: a Settings row, its confirm, the restore picker | §6's rule. #127 closes with it. It belongs with the trial's first-boot software. |
| #83 | USB keyboard and mouse into the console | A feature. It is built on the input owner and proved by the matrix's USB column, so it starts where phase 5 ends. |
| #26 | BLE input | The same as #83, on the matrix's BLE column. |

**Later**

| id | what | why not in the sprint |
|---|---|---|
| N2 (second step) | The kernel's own small tables moved into kpool | It changes placement and kpool's high water: a measured change (#66), not a mechanical one. |
| N13 (widening) | `moy_dev`'s handles on `moy_htab` | It needs the 4-bit kind field widened first, a decision of its own. |
| T6 | One hop under the WASI runner | The step protocol is already shared. What is left is a platform seam inside the session every compiled cart uses, where a mistake hangs a board. It wants phase 1's mutex first, and its saving is small. |
| T1 (rest) | `moy_glass`, `moy_play`, `moy_wasm`, `moycore` faces | Mostly object types and callbacks, which the generator leaves by hand. Revisit when the parity test shows what is left. |
| T7 (meter) | Frames owed per page in the PERF line | It changes a line the perf tools parse. A meter, under the freeze. |
| S12 | The licence page drawn by `ui.text_view` | §6's rule: a 22-configuration re-bake with a look to review. It is the one hand scroller left, and phase 7's guard names it. |
| S9 (big six) | `cart`, `screen`, `ns`, `cart_error`, `menu_view`, `sheet` retired | §6's rule: about 1,370 sites, most of them tests, for no gain to the carve. A codemod of its own, in a quiet tree. |
| N8 (rule) | `moy_path.c` and `moy_text.c` on one UTF-8 validity rule | §6's rule: it changes what a story draws for a stray byte. |
| S13 | `ui.field` draws the no-keyboard text itself | It changes what a field draws on a touch-only or gamepad board: a feature, with #239 and #242. |
| G6 (rest) | `WindowedWM`, `BarLayer`, `Appearance`, `GetCartsAppLayer`, `EditorDesk` | Large, but no audit found a policy written twice in them. The desk Editor's workspaces are an owner decision (`docs/theme_and_shell_2026-11.md` §8.1). |
| G7 | `DeviceCanvas`'s verb groups in helpers | One canvas class is settled, and a helper adds a call on the path where call count is the cost. Measure first (#66). |
| B3 | A Python program's device objects over the handle rows | `docs/os_2026-11/hardware.md` §4 describes the two faces as the design. Reopen it only with that document. |
| X2 | An upgrade rehearsal of MicroPython and ESP-IDF on a branch | Sizing work for a bump the P4's later silicon may force. Independent of the refactor. |
| #249 (perf) | Its perf items: Notes' app pass on the P4s, the home's vertical scroll and carousel, the Guition S3 pickers' first frame, the T-Deck's carousel fling | The refactor changes what they measure: phases 5, 7, 8 and 9 rewrite the home's and the apps' Python frame. Measured again after the gate, numbers to #66. |
| #242 (rows) | Its input-convention rows | Features. They are checked against the matrix once it exists. |
| #127 (LAN) | LAN auto-backup | A feature on top of E1's archive. |
| K2 (rest) | `present_frame`, its patch pictures and `blit_strip_async` in C | Console-only Python with no second copy, whose patch arithmetic has no host test. A perf port; decide it from #58's numbers. |
| #160 (Editor) | The broken-cart mark in the Editor | §6's rule. #160 closes on the launcher's badge. |

## 8. What not to touch

**Vendored.** Lua, both libmoy copies, WAMR, dash, mbedTLS, monocypher,
Dropbear, the DSI vendor drivers, `tools/p8_lua_port.py`, the SDK's libc.
Fixed upstream or in the fork and re-vendored. Every scan in this plan
excludes them.

**Two by design**

| what | why it is not a duplicate |
|---|---|
| `moy_png`'s inflater | It resumes row by row in a caller's buffer and builds without MicroPython's. |
| the two `kern()` functions | Different table formats. Only their UTF-8 readers merge (phase 3). |
| `moy_sync.h`'s lock and `moy_lock.h`'s mutex | A critical section for a few loads, a mutex for long copies. |
| `moy_wasm_web` beside `moy_wasm` | The browser's engine is a sibling, and its absences are declared, not filled. |
| the WASI preview1 table | A standard ABI, written by hand; it is not a call-table row. |
| `moy_prof`, `moycore_prof`, `moy_perf` | Three meters (the `perf` skill). |
| the console WM and `WindowedWM` | Two on purpose; code reads `wm.mode`. |
| the home band's scroll offsets | The engine's table pass (`docs/surface_model_v1.md` §14). |
| the code editor's buffer, caret and scroll | A code editor is not a `ui.field`. |
| Paint's tile-delta undo | A different representation from `History`'s. |
| `row_menu` and `row_list`; `ui.toast` and `ws.notice` | Two style entries; two owners. |
| the context form and the run form of a UI verb | One context per Python app, one per run (T16). |
| Lua's 1-based answers | A tier's convention, written into the table as `base`. |
| the test oracles | `tests/spine_twin.py`, the Python index, `tests/app_twin.py`, `FakeMoycore`. |
| AOT on a board, the interpreter on the host | How a module executes is the host's business. |

**Pinned or settled**

- `moy_kernel.c`'s `mp_task` copy stays verbatim and in place (owner,
  2026-10-05).
- `native/moy_flush` and the compositors under `native/moy_glass`: nothing
  folds without `tests/moy_flush_harness/` and glass.
- The Lua allocator's SRAM-first pool.
- The single-pass toolkit: no measure pass, no new invalidation mechanism
  (`docs/surface_model_v1.md` §8).
- `modmoy_ui.c`'s zero-allocation hand paths: their schema is generated,
  their shape is not.
- The object-valued Lua preludes in `runtime/run/lua_ext.py` (handles,
  layers, the editor, grants) stay in that one module.
- The setter's "applied on a change, never every frame" rule survives
  phase 5.
- The 2026-08 façade rules: the `ws.system` alias, event-push achievements,
  `look_gen`, the frame loop reaching its collaborators directly.
- The generated tables (`DOOR_ARGS`, `app_decls.py`): change the
  generator's input.
- Committed generated output with a drift test.
- The per-board `build.sh` files and the two S3 sdkconfig fragments.
- The host stays on CPython (owner, 2026-11-11; §6, decision 2).
- `tools/look_shots.py`, `tools/focus_shots.py`, `tools/shell_bench.py`,
  `tools/input_gate.py`: large and live.
- `editor_app` beside `editor_desk`: not audited, and an owner decision.
- `P4SystemCanvas` stays the P4 tier's subclass of the one canvas class;
  phase 14 moves a choice under it, not the class.
