# Refactorability audit: the Python console (shell, apps, system services, Player, device tier)

Tree: origin/dev at 5465b571c. Read-only; no builds, no boards.
Paths below are repo-relative; line numbers are from that commit.

## 1. Health verdict

The console's *drawing* is in good shape: the shipped apps sit on the public
`ui.*` verbs through `ctx.ui` (an `AppHost`), the app tier holds no `ws.`
reference (only `text_console.py` has 12), and the 2026-08 façade ratchets
still hold (`ws.system` is never rebound; `DOOR_ARGS` and `app_decls.py` are
generated). Its *input policy* has no owner, and that is where the cost and
the bugs are. There are three policies (text mode, cancel, back), and each is
decided in several places, in two styles:
- Cart apps get all three from the toolkit's C. These are `api_typing`,
  `api_cancel` and `api_back`, which landed today in 4e090bf88 and b251f6b1d.
- The shell's own apps and pages flip text mode by hand at 31 `_set_text_mode`
  sites plus 6 `nav.text_mode` sites. They step back through ~15 hand-written
  `back()` page ladders, and they cancel through a hand-built chain in
  `console_text.py:80`.

This chain does not know Files' rename, Appearance's theme name or
Storybook's fields. So on a T-Deck, Mic in those fields opens the system menu
instead of cancelling, today: none of those apps defines `cancel_field`.

Back is decided four times: the router (B/stop), the bar chevron, the host's
Esc cascade and the window chevron. The host's Esc cascade runs only in the
simulator and the browser, never on a board.

`Workstation` is ~9.5k lines over ten mixins with ~138 attributes. 101
distinct `ws._private` names are read from 61 files outside `console*.py`.
Surfaces are duck-typed through `getattr(app, "...")`, and Settings is special
cased at 33 sites.

A cross-cutting tax: 806 `except ImportError` dual-import fallbacks across 222
files. The refactor sprint should start with the input owner. It is the
highest-payoff change, and it removes a live bug class.

## 2. Findings, ranked by payoff

### S1. One owner for input policy: text mode, cancel and back

The first finding. It lands in three steps (S1a–S1c) over a step 0, the
surface protocol.

**Evidence: text mode**

Text mode is imperative and set by every surface:
- 31 `ws._set_text_mode` call sites:
  - `editor_app.py:378,407,459,470,577`;
  - `settings_layer.py:732,867,875,928,1512,2026`;
  - `console_home.py:211,259,310,594,603`;
  - `console_spine.py:302,320,483`;
  - `block_editor_ui.py:883,920,933,1218`;
  - `cards_layer.py:220,748`;
  - `update_ui.py:90,116,137`;
  - `editor_scene.py:235,252`;
  - `editor_desk.py:444`;
  - `widgets.py:81`;
  - `player.py:1383,1385`;
  - `console_input.py:681,685`;
  - `wm_windowed.py:1667`.
- 6 `ctx.nav.text_mode` sites:
  - `files_app.py:681`;
  - `music_app.py:1042,1056,1196`;
  - `appearance_app.py:493,498`;
  - `story_edit.py:180,187`.

Three places write `input.text_mode` directly, around the setter:
- `cart_api.py:450` (a cart's `textmode()`);
- `moycore_glue.py:597`;
- `console_spine.py:479`.

Because the mode is set rather than derived, a pile of state exists only to
restore it:
- `Workstation._text_by`, `_text_back`, `_text_sets`, `_menu_over` and
  `_menu_keys` (`console_input.py:665`);
- `_sync_cart_text_mode` (`console_input.py:688`);
- `WindowedWM._text_wish`, `note_text_mode`, `_text_left` and `_apply_text`
  (`wm_windowed.py:602,756,1507,1661-1679`);
- `Player._text_was` and `_typing` (`player.py:1364`);
- `SettingsLayer._typing`;
- the `text_mode` column of `app_decls.py`, threaded through
  `register_app`/`open_app`/`_return_to_app`.

Three surfaces already derive the mode instead of setting it: the Player
(`_sync_typing`: `api_typing` on edges), Settings (`settings_layer.py:2024`:
its focus kind) and the WM (per window).

**Evidence: cancel**

`console_text.py:80` `cancel_field` is a fixed if-ladder over these, in order:
- `crash_popup`;
- `code_layer.close_overlay`;
- `cards_layer.prompt`;
- `block_ui.cancel_prompt`;
- `search_typing`;
- `script_console.reading`;
- `settings_layer.cancel_field`;
- `Player.cancel_field`;
- `getattr(app, "cancel_field")`.

Only Music and Terminal define `cancel_field` among the apps. So Mic in Files'
rename (`files_app.py:920`), Appearance's name (`appearance_app.py:1041`) and
Storybook's fields falls through to `system_key()`. Settings' Name re-does by
hand what `api_cancel` now does (`settings_layer.py:1812-1829`: drop the
draft, step focus up).

**Evidence: back**

Four dispatchers with their own cascades:
- `console_input.py:156-177`, the router's B/stop, which has `leave_asked`;
- `bar_layer.py:1228` `go_back(where)`, a string ladder;
- `host_api.py:457-505`, `takes_escape`/`escape`. These are host and browser
  only: a board's Esc arrives as `stop` (`moy_kbd.c:63`) and takes the router
  path, so the host-only path can disagree with glass unseen.
- `wm_chrome.py:523`.

There are ~15 per-surface `back()` methods. Each re-implements "close the
dialog or popover I drew, else step my page":
- `files_app.py:710`, `music_app.py:1050`, `appearance_app.py:555`,
  `myart_app.py:246`, `paint_app.py:477`, `storybook_app.py:431`,
  `story_edit.py:121`, `getcarts_app.py:1199`, `terminal_app.py:152`;
- `settings_layer.py:1917`, which branches on six view flags;
- `about_ui.py:150`, `achievements_ui.py:215`, `log_ui.py:69`,
  `perms_ui.py:47`, `tasks_ui.py:175`;
- `PauseMenu.back`, `Player.back`.

`api_back` already does the first half for any `AppHost` context.

**What to do**

Create `+runtime/shell/input_policy.py` (the owner) and make the surface
protocol explicit.

*Step 0, the surface protocol.* In `layers.Layer`, add defaulted verbs:
- `ui_host()`: the `AppHost` it draws through, or None;
- `typing()`;
- `cancel()`;
- `back()`, `has_page()`;
- `close()`, the leaving hook (see S5);
- `page_title()`, `ball_steps()`.

Registered apps, Settings, Tasks, the Editor's tab layers and the Player's
layer answer it. Remove the `getattr(app|layer, "...")` duck typing (~42
sites). Remove `page_app`'s Settings/Player special cases (`console.py:2780`).

*S1a, typing derived on edges.* `policy.sync()` runs once a frame at one
point in `handle_input`, after the `start` handling. It computes:

```
want = False                     if a modal holds the keys (pause/confirm/sysmenu over a handheld)
     = holder.typing()           otherwise
```

where the holder is `wm.input_holder()`'s surface. `typing()` answers:
- by default, `api_typing(ui_host().ctx)` where the surface has a host;
- for typing surfaces, a declared constant or callable: the Code tab, the
  Terminal, the cards and blocks prompts, the home search and the update
  screen's off;
- for the Player, the cart's own `textmode()` flag OR its field focus. Store
  the flag on the Player, not on `input`.

Only on a change of `want` does it apply today's `_set_text_mode` body:
- the seed rule (`code_layer.seed_key`, `seed_cart_key`);
- `input.text_mode`;
- `kb.set_game_mode`.

This keeps the comment at `console_text.py:163`: no per-frame *apply*.

Then delete:
- the 37 call sites;
- `nav.text_mode` (a shell row in `native/os_calls.json`, regenerate the
  tables);
- the restore state listed above;
- the `text_mode` manifest key.

The Terminal answers `typing()` True itself. Desk windows get "each window
keeps its mode" for free, because the mode is a function of the focused
window's surface.

*S1b, cancel.* `policy.cancel()` asks, in order:
1. the modal overlay;
2. `holder.cancel()`. Its default is `app_cancel(ui_host().ctx)`, then the
   surface's own overlays. The Editor's layer owns its crash popup, code
   overlay, cards prompt and blocks prompt, in that order.

Then:
- Mic (`console_input.py:98`) calls `policy.cancel()`;
- `console_text.cancel_field` is deleted;
- Settings' `cancel_field`/`_leave_field` go;
- the Name field and the Wi-Fi password field take `api_cancel` from the
  context they already draw in.

*S1c, back.* `policy.back()` is the one cascade:
1. modal: `pause.back` / `confirm.cancel` / `sysmenu.close`;
2. a fullscreen game: `system_key`;
3. `holder.back()`. Its default is `app_back(ui_host().ctx)`, then the
   surface's own page step; on False it calls `holder.close()` and leaves:
   `BACK_MINIMISES` or `_exit_to_caller`.

All four entry points call `policy.back()`:
- the router;
- `bar_layer.go_back`;
- `ConsoleDriver.escape`: delete `takes_escape`'s duplicate predicate, and
  map host Esc to `stop` as the boards do;
- `wm_chrome`.

The apps' `back()` shrink to their page step, because the dialog and popover
half is `api_back`'s.

**Lines:** about −600 net:
- ~250 of text-mode plumbing;
- ~80 of the cancel chain and Settings' copy;
- ~150 of back ladders;
- ~120 of host Esc and `go_back`.

**Risk: high.**
- This is the T-Deck typing path. Seed/swallow, Mic and W A S D as move keys
  must hold.
- On the desk, focus moves between windows.
- `api_typing` per frame per app is one C call. Keep it allocation-free
  (`tests/test_frame_alloc.py`).

**Guards:**
- A ratchet test: `_set_text_mode`, `input.text_mode =` and
  `set_game_mode(` appear only in `+input_policy.py`, `console_text.sync_keyboard_mode`
  and the C binding.
- A table test over every surface in `APPS` plus Settings, the Editor tabs and
  a cart app:
  - focus a field: typing;
  - Mic: the draft is reverted and the menu is not opened;
  - Esc, B, the chevron and the window chevron: the same result from all four
    entry points;
  - a menu over the field: game mode, and back again after.
- Extend `tests/test_app_field_typing.py` to the shell apps.

**Dependencies:** step 0 first. S3 and S5 build on it.

**Board:** yes. On the T-Deck: a field in Files, Settings, Music, Appearance
and a cart app; Mic; W A S D; the trackball. On a P4: two desk windows, one
typing and one game.

### S2. Settings as a page table, then as a registered app

**Evidence:**
- `settings_layer.py` is 2079 lines and 181 `ws.` references.
- Its pages are six booleans plus a string: `_page_of`
  (`settings_layer.py:1874`) checks `wifi_view`, `bt_view`, `crash_view`,
  `tz_view`, `paired_view`, `usb_view` and `page`.
- Each page has its own `open_X`/`close_X` pair: lines 555–1220.
- `back()` (`settings_layer.py:1917`) is a ladder over them.
- Settings is not a registered app. It is special-cased at 33 `"settings"`
  string sites in 12 files:
  - `console_text.py`, `console_spine.py`, `console.py`, `bar_layer.py`;
  - `host_api.py`, `shell_servers.py`;
  - `wm_windowed.py`, `wm_chrome.py`, `wm_paint.py`;
  - `editor_desk.py`, `editor_panes.py`, `settings_layer.py`.
- Tasks has the same shape: 10 `"tasks"` sites.

**What to do:**
- (S2a) One page stack: `self.page` plus a table `{name: (open, close, draw,
  keys)}`. `back()` becomes "pop, else leave". The vestigial `wifi_pw =
  TextEntry(...)` is used only as a `.text` holder for `ui.field`
  (`settings_layer.py:1063`); make it a str. Drop the `_wifi_seed` hand
  swallow (`:865,938`); it is S1/S3's job.
- (S2b, later) Register Settings like the other apps, with an `AppContext`
  carrying wifi/bt/usb roles. That removes the 33 special cases.

**Lines:** S2a about −150; S2b about −120 of special cases, plus new roles.

**Risk:**
- S2a: low. Goldens: `test_settings_layer_pixels.py`.
- S2b: high. The Wi-Fi lease and the USB drive hold, and Settings' "resume the
  cart it was opened from".

**Guards:**
- The existing Settings goldens.
- A test that no module outside `settings_layer.py` compares a kind to
  `"settings"` (a ratchet count, shrink-only).

**Dependencies:** S1 step 0.

**Board:** S2a no. S2b yes (Wi-Fi panel on a P4).

### S3. One typed-key edge instead of nine

**Evidence:** "a fresh typed byte" is recomputed per surface, because
`last_key` is level state (BLE):
- `KeyEdge()` instances: `code_layer`, `map_editor_ui`, `music_editor_ui`,
  `paint_layer`, `scene_editor_ui`, `block_editor_ui`, `text_console` and
  `TextEntry`, defined at `editors_base.py:12`;
- `AppHost.key` (`ui_host.py:91`);
- `about_ui._key` (`about_ui.py:135,171`);
- `ws._ekey_prev`;
- `Player.seed_cart_key`;
- `code_layer.seed_key`;
- `settings._wifi_seed`;
- `widgets.arm_prompt`;
- `ConsoleDriver`'s "never the same byte in two adjacent frames" dedup
  (`host_api.py:520`).

The "a switch swallows the byte" rule exists *because* each copy keeps its
own edge (`console_text.py:153-163`).

**What to do:**
- The input table publishes the typed press edge per frame. It already
  tracks the key's owner and repeats; give `i.typed` its edge, from
  `moy_input` C.
- `policy.sync()` spends it once on a mode switch: `input.spend_key()`.
- Surfaces read `i.typed`.
- `KeyEdge` shrinks to `undo_redo` or dies with S4.
- `arm_prompt`'s edge clearing stays (pointer click), and its keyboard half
  goes.

**Lines:** about −150.

**Risk: medium.**
- The T-Deck's press edges against BLE level state.
- The browser's soft keyboard repeats ("ll").

**Guard:** a test that `KeyEdge(` and `last_key !=`-style edge code appear
only in the input layer. Keep the existing editor typing tests.

**Dependencies:** S1a. It touches `native/moy_input`: coordinate with the C
auditor.

**Board:** yes (T-Deck, a BLE keyboard on a P4).

### S4. Editor Ctrl+Z/Y take two different undos

**Evidence:**
- The Code tab sends 0x1A/0x19 to `ws.history.undo/redo`
  (`code_layer.py:668-671`). That walks local ops, then the journal: the
  bar's UNDO semantics.
- Map, Music, Scene, Paint and Blocks catch them themselves and walk only the
  local op stack:
  - `map_editor_ui.py:334` `_mkey.undo_redo(k, self._map_undo, ...)`;
  - `music_editor_ui.py:201`, `scene_editor_ui.py:340`;
  - `paint_layer.py:491-494`, `block_editor_ui.py:1119`.
- So past the local stack, Ctrl+Z on those tabs stops where the bar's UNDO
  carries on into commits.

**What to do:** route Ctrl+Z/Y once, in the Editor app's key path, to
`ws.history.undo/redo` for every `COMMIT_TABS`/`THEME_TABS` tab. Delete the
five per-tab handlers and their `KeyEdge`s.

**Lines:** about −60.

**Risk:** low. The `history_router` already handles every tab.

**Guard:** a parametrized test over the Editor tabs: Ctrl+Z after the local
stack empties equals the bar's UNDO (journal step).

**Dependencies:** none. It lands alone.

**Board:** no.

### S5. One leaving hook on every exit path

**Evidence:**
- `go_home` loops `close()` over the apps (`console_home.py:303-345`). Its
  comment records that a hand ladder "loses the kid's work, silently".
- The window X still has that hand ladder (`wm_windowed.py:2108-2125`):
  - `storybook_app.commit()`, `artwork_app._save()`;
  - `look.commit()`, `settings_layer.commit_name()`, the `menu` case.
- `app_back` calls `commit` (`console.py:2814`).
- `open_app` calls `look.commit()` and `settings_layer.commit_name()`
  (`console_spine.py:473`).
- The Layer protocol's `on_enter`/`on_leave` exist and nothing calls them
  (`layers.py:60-66`; `layers.py:183` says so).

**What to do:** one `ws.leave_surface(kind)` that calls the surface's
`close()`, as `docs/app_api_v1.md` defines it ("leaving the screen, by any
route"). Every exit path calls it:
- `go_home`, `app_back`;
- the window X;
- `open_app`'s jump;
- `_exit_to_caller`.

Either make `on_leave` this hook or delete `on_enter`/`on_leave`. Fold
`commit()` into `close()` where they coincide.

**Lines:** about −40, plus a closed data-loss bug class.

**Risk:** low to medium. A double commit must stay change-gated (#154: a
write costs more than a frame).

**Guard:** parametrize over `APPS`, Settings' Name and the Editor. Leave a
pending edit, take each exit (home, back, window X, app jump, OTA hand-off),
then assert it is persisted.

**Dependencies:** S1 step 0.

**Board:** no.

### S6. The bar is drawn by each surface, by a private name

**Evidence:**
- `bar_layer._draw_status_strip(where)`: its docstring says "Entry point every
  content Layer calls" (`bar_layer.py:376`).
- It is called at 30 sites in 15 files:
  - `layers.py` ×6, `cards_layer` ×4, `block_editor_ui` ×3,
    `launcher_layer` ×3, `console.py` ×3, `paint_layer` ×2;
  - one each in `settings_layer`, `tasks_ui`, `theme_editor`, `code_layer`,
    `editor_app`, `editor_desk`, `home_band`, `wm_desk`,
    `console_notices`.
- Each site passes its own `where` string. The bar contract is "a HOST
  guarantee", yet a surface that forgets the call has no bar.

**What to do:** the frame loop draws the strip after the content layer, from
`wm.top_kind()`, through a `where` map kept in `bar_layer`. Surfaces stop
calling it. Keep `held`.

**Lines:** about −50; one private poke gone.

**Risk: medium.**
- Draw order inside a layer, where an overlay draws after the bar: cards'
  prompt, popovers.
- The bar's cache and the desk windows' per-window strips.

**Guard:**
- `tests/test_shell_goldens.py`, `test_content_rect.py`.
- A grep test that `_draw_status_strip(` appears only in the frame loop.

**Dependencies:** none.

**Board:** a P4 frame-cost spot check, because the desk repaint path moves.

### S7. Workstation's privates are its real API

**Evidence:**
- 101 distinct `ws._name` attributes are read from 61 files outside
  `console*.py`. Top ones outside `_dirty`:
  - `_set_text_mode` 31, `_with_sd` 22, `_reset_canvas_state` 20;
  - `_glyph` 18, `_animating` 17, `_relayout` 15, `_sys_canvas` 13;
  - `_icon` 12, `_exit_to_caller` 11, `_apps_by_id` 11.
- `ws._dirty = True` appears at ~230 sites outside the class. `mark_dirty()`,
  which does the same thing, appears at 65.
- Layers also poke each other's privates:
  - `player._is_tool` ×6;
  - `wm._wins` ×7, `wm._move_focus` ×3, `wm._bring_forward`;
  - `bar_layer._today`, `bar_layer._show_x`;
  - `code_layer._recheck_err`;
  - `bar_layer._lent_ctx` reads an app's `_ui` (`bar_layer.py:1287`).
- `test_console_facade.py` pins forwards and `getattr` names but not this.

**What to do:**
- (a) Add a ratchet test: the set of `ws._x` names read outside
  `console*.py`, pinned, shrink-only. Do the same for `player._`, `wm._` and
  `bar_layer._`.
- (b) Rename the dozen most-read names public.
- (c) `ws._dirty = True` → `ws.mark_dirty()`. Same cost: the store walks the
  MRO either way (`console_input.py:51` notes ~60 µs a Workstation store on a
  T-Deck), so per-frame sites gain nothing and lose nothing.
- (d) Add `ws.app(id)`, replacing `_apps_by_id` and the `ws.<id>_app`
  attributes. Its docstring claim of "100+ call sites"
  (`console_spine.py:372-374`) is false: there are ~10 sites. Also replace
  the `_is_tool` reads with `player.is_app()`.

**Lines:** ~0 net (renames), but it gives the façade a boundary.

**Risk:** low, mechanical. Watch `inspect.getsource` users in the web build
and the serial vocabulary (`tools/p4_*` speak some names).

**Guard:** (a) itself.

**Dependencies:** land the pin first. S1 and S5 then shrink it.

**Board:** no. Run preflight.

### S8. Dual-import boilerplate and the host's alias list

**Evidence:**
- 806 `except ImportError` blocks in 222 files and 889 `from runtime import`
  lines. The common shape is `try: import x` / `except ImportError: from
  runtime import x`, "host fallback when not yet aliased".
- `host_app.py:23-74` keeps a hand list of `sys.modules.setdefault`
  aliases.
- `runtime/__init__.py:17-33` already makes the package span every subfolder
  as one flat namespace.

**What to do:**
- Add a meta-path finder in `runtime/__init__.py` that resolves a flat name
  naming a runtime module to the *same* module object as `runtime.<name>`.
- Codemod the two-arm imports to the device form (`import x`).
- Delete `host_app`'s alias list.

Keep the genuine optional-module `ImportError`s (moycore absent, a board
without a driver); the codemod matches only the exact
`import X` / `from runtime import X` pair.

**Lines:** about −2,000.

**Risk: medium.**
- Module identity: a test may import `runtime.x` before `x`.
- The browser runner is MicroPython-WASM and already flat.
- `inspect.getsource` callers in the web build.

**Guards:**
- After importing every runtime module, assert `sys.modules[n] is
  sys.modules["runtime." + n]`.
- A grep test that no runtime file has the two-arm fallback.

**Dependencies:** none. Land it alone, in a quiet window: it touches every
file.

**Board:** no. Preflight (web blob) and one board build to confirm the
frozen set is unchanged.

### S9. Retire the cheap legacy property forwards

**Evidence:** `LEGACY_PROPERTY_FORWARDS` (`test_console_facade.py:301`).
Call-site counts (src / tests):

| forward | src | tests |
|---|---|---|
| `images` | 2 | 0 |
| `_cart_key_prev` | 1 | 0 |
| `scenes` | 2 | 7 |
| `pmem` | 2 | 10 |
| `crash_line` | 9 | 2 |
| `_draw` | 3 | 8 |
| `_update` | 6 | 10 |
| `config` | 7 | 35 |
| `tilemap` | 7 | 67 |

The test's "~920-site rename" applies to `cart`, `screen`, `ns`, `cart_error`,
`menu_view` and `sheet`, not to these.

**What to do:** replace these with `ws.run.project.x` / `ws.run.player.x`.
Drop the properties. Shrink the pinned set from 15 to 6.

**Lines:** about −70 of properties.

**Risk:** low.

**Guard:** the existing pinned set, now smaller.

**Dependencies:** none.

**Board:** no.

### S10. `console_notices.py` does four jobs

**Evidence:** `console_notices.py` is 2098 lines.
- The `Notices` mixin (154–707) holds:
  - notices and toasts;
  - intents glue: `_intent_*`, 298–458, ~160 lines;
  - status-item glue;
  - `ask_first`, `ask_stop_music`.
- `PauseMenu` (708–1191).
- `DeleteConfirm` (1192–1446).
- `SystemMenuUI` and `AboutCard` (1447–2098).

`console.py` is 4132 lines. It also carries the splash (622–715), the
pre-toolkit draw trio (3894–4080) and per-tab lazy builders. `_icon_btn`
(`console.py:3907`) has no caller; only a comment in `ui_draw.py:1250`
mentions it.

**What to do:** move:
- the intents glue to a `console_intents` mixin;
- the pause, confirm and system-menu cards to `+shell_cards.py`;
- the icon helpers next to `bar_layer`.

Then update `test_console_facade.MIXINS`, and the board denylists, especially
the Zero's allowlist. Run `git add` before the first build, because the
stager prunes untracked files.

**Lines:** ~0 net; three readable files instead of one.

**Risk:** low.

**Guards:** the existing façade ratchets. A file-size budget test
(`console*.py` ≤ N lines, shrink-only) would keep it from regrowing.

**Dependencies:** best after S1/S5, which delete code from both files.

**Board:** a build only (staging), not a pass.

### S11. Files opens a file two ways

**Evidence:**
- OPEN is Files' own per-kind ladder: `files_app.py:497-545` calls
  `nav.open_image`, `nav.edit_file`, `nav.open_text`, `nav.edit`,
  `nav.run_script` and `nav.play`.
- "Open with…" is `intents.open_with` by mime (`files_app.py:571-575`;
  `intents.py:279-345`).
- So a type's default app is decided in two places.

**What to do:** OPEN becomes `INTENTS.open_with(SHELL, item, False)`. Paint and
the Notes cart declare their types in their manifests (Paint `image/*`, text
`text/*`). Projects (edit) and scripts (run) stay explicit verbs. Then drop
`nav.open_image` and `nav.open_text` from the app tier if nothing else uses
them (`myart_app.py:306,316` does: route those too).

**Lines:** about −80.

**Risk:** medium. The return-to-caller contract (#108) must hold.

**Guard:** existing Files tests, plus one asserting OPEN and WITH (single
candidate) land in the same app.

**Dependencies:** none.

**Board:** no.

### S12. Shell pages still keeping their own scroll

**Evidence:** the licence text in `about_ui.py:120-260` is a hand scroller:
- `_top`/`_rows`;
- its own key edge `_key`;
- its own `wrap_lines`;
- per-line `ui.at` placement and a fade.

`ui.text_view` already does this (`text_console.py:508`). CLAUDE.md's shell
row names this duplication. The home band's offsets in `home_grid` and
`home_carousel` are the engine's table pass and deliberate (§14); do not
include them.

**What to do:** `ui.text_view` over the licence lines.

**Lines:** about −90.

**Risk:** low.

**Guards:** the goldens. A ratchet grep for `self._top`/`self.scroll` offsets
in `runtime/system`, `runtime/apps` and `runtime/editors`, excluding
CodeEditor.

**Dependencies:** none.

**Board:** no.

### S13. The no-keyboard rule is per field, so most fields lack it

**Evidence:**
- `can_type()` and `no_keyboard_text` are checked by Settings (6 sites) and
  the home search (3).
- They are not checked by Files' rename, Appearance's name, Music's station
  fields, Storybook or cart apps' `ui.field`.
- On a touch-only or gamepad board those fields promise a keystroke (#239,
  #242).

**What to do:** the host passes `can_type` in the frame's input words.
`ui.field` draws `NO_KEYBOARD_TEXT` itself where nothing can type. Delete the
per-field checks.

**Lines:** about −30, and every field becomes consistent.

**Risk:** low to medium. This is `native/moy_ui`, the C auditor's area.

**Guard:** a golden of one field per app rendered with `input_kinds={"touch":
"touch"}`.

**Dependencies:** none.

**Board:** no.

### S14. Undo stacks outside `op_history`

**Evidence:**
- `story_doc.py:315-383`: snapshot undo/redo lists with `UNDO_MAX`.
- `theme_editor.py:114-160` `_SlotOps`: its own list and `DEPTH`.
- `op_history.OpHistory` is the codec-based general one.

**What to do:** fold both onto `OpHistory` with a snapshot codec. Keep
`artwork.py`'s tile-delta undo (different representation, see §4).

**Lines:** about −60.

**Risk:** low to medium.

**Guard:** the existing story and theme undo tests.

**Dependencies:** none.

**Board:** no.

### S15. Smaller items, each landing alone

- **The trackball spend has two faces.** `host_api.py:520-530`
  `ConsoleDriver.frame` and `desktop_spine.py:107-120` `handle_input` both do
  `ws.ball(...)`, then `pointer.move(cursor(dx))`. Make it one
  `ws.ball_frame(dx, dy, click, pointer)`. About −15 lines, low risk;
  board: T-Deck smoke.
- **The device-API name list is copied.** `moybyte.py:1170` has a literal
  tuple equal to `devices.NAMES`. `devices.PERM` is an identity map
  (`devices.py:36`). Use `NAMES`; drop `PERM` unless a name will differ.
  Low risk.
- **`moybyte.ENDS` order is set by import order.**
  `dev_handles.py:377` `insert(0)`, `devices.py:96` `insert(0)`,
  `leases.py:485` `append`, `aio.py:474`. The contract "handles, then
  devices, then leases" is implicit. Make it an explicit ordered registry,
  plus a test of the order.
- **Probably dead code (confirm first).** Check moy-spec's runner and the web
  JS before deleting:
  - `AudioSession.take_pcm` (`audio_session.py:250`, no reference in this
    repo);
  - `PerfMeters.perf_breakdown_raw` (`console_perf.py:335`, a docstring
    mention only);
  - `chrome.Layout.grid_content_w` (tests only).
- **The Python `Scheduler` class is ~1220 lines** (`scheduler.py:410-1628`).
  It mixes Python wakes, Lua services and compiled services. Its split should
  follow the plan's scheduler unification (brief: three schedulers), not
  precede it.

### S16. `Player` is one 2813-line class

**Evidence:** `player.py:273`, 82 methods covering:
- start and gates;
- the app-cart face (`ui_room`…`_input_active`, 1315–1636);
- pacing and the front hand-off (1802–2116);
- lockstep;
- crash capture.

`_is_tool` is read from 6 other modules.

**What to do:**
- Extract the app-cart face (`PlayerUi`, ~320 lines) and the front/pacing
  half by composition.
- Keep `tick`'s hot path on the Player: attribute hops cost on a board.
- Add `is_app()`.

**Lines:** ~0 net; three objects of ~900 lines.

**Risk:** medium (the frame path; `tests/test_frame_alloc.py`).

**Guard:** the frame-alloc and semantic-trace suites.

**Dependencies:** after S1, which removes `_sync_typing` and the `_text_*`
state.

**Board:** yes (per-cart fps spot check on the T-Deck; numbers to #66).

## 3. Suggested order (batches that land independently)

1. **Pins and quick wins.** Host only, low risk, one worker:
   - the S7(a) ratchet test, pinned at today's set;
   - S4 (Ctrl+Z);
   - S9 (cheap forwards);
   - S12 (licence text);
   - S15's name list, ENDS order and dead-code checks.
2. **S1 step 0 and S1a.** The surface protocol and derived typing. Board:
   T-Deck plus a P4 desk.
3. **S1b and S1c, plus S15's trackball/Esc fold.** Cancel and back, one
   cascade. Board: T-Deck (Mic, B, K), P4 (window chevron), simulator Esc.
   This fixes Mic in Files, Appearance and Storybook fields.
4. **S5.** The leaving hook, with `on_leave` decided. Host.
5. **S3.** The typed-key edge in the input table. Coordinate with the C
   auditor. Board: T-Deck plus BLE.
6. **S2a, S6, S10.** The Settings page table, the bar drawn by the frame
   loop, and the `console_notices`/`console.py` split. Host plus a P4
   frame-cost check for S6.
7. **S8.** The import finder and codemod. Alone, in a quiet tree: it touches
   222 files and conflicts with everything. Preflight with the web build.
8. **S11, S13, S14.** Files OPEN via intents, the no-keyboard field in the
   toolkit, undo onto `OpHistory`.
9. **Later.** S2b (Settings as a registered app) and S16 (the Player split),
   each after its prerequisites.

## 4. What NOT to touch

- **Two WMs.** The console WM and `WindowedWM` are deliberate: `WindowedWM`
  is not staged to the S3, and branches read `wm.mode`, never the stack
  shape.
- **The home band's own offsets.** `home_grid`/`home_carousel` scroll state is
  the engine table pass with snap/spill and shift frames
  (`docs/surface_model_v1.md` §14). It is not a hand-rolled scroller.
- **CodeEditor's buffer, caret and scroll.** A code editor is not a
  `ui.field`; its typing-burst undo is the history's.
- **Paint's tile-delta undo** (`artwork.py:237-330`). It is a different
  representation (tiles a stroke touched), not a copy of `OpHistory`.
- **`_set_text_mode`'s "no per-frame caller" rule.** S1a keeps it: it
  derives every frame but *applies* only on a change, with the seed rule
  intact.
- **The big legacy forwards** (`cart`, `screen`, `ns`, `cart_error`,
  `menu_view`, `sheet`). The architecture doc's §9 rules out the big-bang
  rename; S9 takes only the cheap ones.
- **The 2026-08 façade rules.** The `ws.system` alias, event-push
  achievements, `look_gen` and "the frame loop reaches collaborators
  directly".
- **The input policy stays in the Python shell.** The mode bit and the key
  edge live in the C input table, but the policy is shell code: the kernel is
  C and apps stay Python (`native/moy_kernel/README.md`).
- **Generated tables.** `DOOR_ARGS` (`shell_servers.py:819`) and
  `app_decls.py` are already generated: edit the generator inputs, not them.
- **`row_menu` vs `row_list`, and `ui.toast` vs `ws.notice`.** `row_menu`
  and `row_list` are deliberately two style entries. `ui.toast` is an app's
  in-frame pill and `ws.notice` the console's timed banner, two different
  owners.
- **`editor_app` vs `editor_desk`.** Not audited for duplication. The desk
  Editor's workspaces are an owner decision
  (`docs/theme_and_shell_2026-11.md` §8.1); read that before merging them.
