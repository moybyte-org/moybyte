# Moybyte shell architecture — privileged system carts + a layered compositor

**Issues:** #55 (make the system itself a cart — the privilege half), a new companion
issue (the compositor/window-manager half, see "Tracking" below), #58 (ESP32-P4 "One"
port, whose "Desktop look" section defers exactly this), #54 (the layer/scroll engine,
the seed primitive this builds on).
**Status:** EXPLORATION / OPEN — this is a target to design toward, not a committed plan.
Nothing here should be implemented speculatively; each piece lands when a concrete need
(a system surface migration, or actual One/P4 hardware in hand) makes it worth doing.
**One exception, added 2026-07-05:** §2.2 is now *evidence, not exploration* — the draft
`make_system_api` there was derived from a real extraction pass (four chrome surfaces
pulled into their own UI classes, each recording its `ws.*` dependency profile). The
*implementation* still waits for its trigger; only the API's shape is now grounded.
**Enabling foundation, added 2026-07-06:** `docs/shell_layers_refactor_v1.md` is the
*concrete prerequisite* under both halves below. The further module/cart extraction
stalled because the remaining surfaces are smeared across `frame`/`handle_input`/
`handle_pointer` — they need a **Layer boundary** (own draw + input + state) before a
clean file/cart split is even possible. That refactor is the common foundation: logical
Layers → { modules, §2 privileged carts, §3 retained-buffer compositor }.
**Boundary mechanism, added 2026-07-06:** `docs/shell_os_architecture_v1.md` is the
concrete "how" under both halves — it grounds §2.2's `make_system_api` in how real OSes
draw this line (syscall table / capabilities / message passing; Picotron as the
fantasy-console realization) and defines the migration path (per-surface capability API
replacing the layers' `ws` reach-through, an event bus, privilege tiers). Read it before
implementing anything from §2.
**Scope of this doc:** two related long-term directions for `runtime/console.py`'s
shell (`Workstation`) that this session's editor-extraction refactor (block/map/music
editors → their own classes) turned out to be laying groundwork for, whether or not
that was the original intent: (1) system screens becoming actual privileged carts, and
(2) the compositor growing from "one mutated framebuffer" into a layered model that
can eventually support more than one visible surface — which is also the natural shape
for the web view to become a real remote window manager instead of a single mirror.

This follows the same portability discipline as the rest of v0.4 (`CLAUDE.md`): nothing
proposed here should change the **kid-facing cart contract** (`docs/moy_cart_api.md`) —
it stays exactly as-is, indexed canvas, plain functions, portable across every tier.
Everything below is *shell*-side, layered on top of that contract, never replacing it.

---

## 1. Why now, and why these two things are linked

Issue #55 ("make the system itself a cart(s)") has been open since 2026-06-29 with a
`DEFER` note: *"a sizable architecture change... best tackled as one deliberate pass
once there's a concrete need — not piecemeal."* This session's refactor pass (extracting
the block editor, then the map editor, then the music editor out of `Workstation` into
`BlockEditorUI`/`MapEditorUI`/`MusicEditorUI`, each holding its own state and its own
input/draw methods) wasn't done *for* #55 — it was a plain complexity-reduction pass,
reviewed and staged independently. But the resulting shape is, incidentally, exactly
the shape #55 needs: a self-contained class with an `_init`-like construction, its own
`handle_input`/`handle_pointer`/`frame`-equivalent methods, and (crucially) no state
that leaks outside itself except through a small, explicit set of shared primitives
(`self.ws.canvas`, `self.ws.go_home`, `self.ws._leave_menu`, …). That's a UI class one
step away from being a cart with elevated permissions instead of a cart with none.

Separately, issue #58 (the ESP32-P4 "One" port) already names the second direction in
its "Desktop look" section: *"carts stay 320×240 (portable, upscaled), while the
shell/chrome can optionally render at a higher native resolution for a real desktop
feel (bigger/multi-panel editors). That's a follow-up, not part of the base port —
track separately once the panel is lit."* This doc is that follow-up, tracked.

The two are linked because a shell made of independent, privilege-scoped units (§2) is
also the natural set of things a multi-surface compositor (§3) would eventually need to
composite. Neither requires the other to ship — they can and should land independently
— but designing them with the same seam in mind avoids painting into a corner.

---

## 2. Privileged system carts (the #55 half)

### 2.1 The privilege boundary

The kid-facing cart API (`make_api`, `docs/moy_cart_api.md`) is deliberately a small,
sandboxed surface: drawing, input, audio, `pmem`, and `wifi` only when a manifest opts
into the `"network"` permission. A system screen (Settings, the launcher, the top bar,
the dropdown menu) needs verbs that must **never** be reachable from an ordinary kid
cart: `go_home()`, opening/closing other screens, creating/duplicating/deleting carts
in the store, reading wifi credentials, triggering OTA, editing the system icon theme.

The proposal is a **second, additive API surface** — call it the *system* namespace —
injected only when both are true:
- the cart's `manifest.json` declares `"system": true` (a flag that already exists
  today, currently used only for "don't let the kid rename/delete this built-in," not
  for capability), **and**
- it was loaded from the trusted system location (the seed/builtin path), never from
  an SD card or a shared/downloaded cart, so a kid cart can never simply set the flag
  and inherit privilege.

This keeps the existing `make_api` surface **completely untouched** — no new kid-facing
verbs, no portability regression, no change to `docs/moy_cart_api.md`. The system
namespace is a parallel injection, host and device both (same contract discipline as
everything else here), likely built as a second `make_system_api(ws)` alongside the
existing `make_api`, returning the small number of privileged callables a system cart's
`_update`/`_draw` would use.

### 2.2 The draft `make_system_api` — derived from the 2026-07-05 extraction pass

This section is **evidence, not proposal.** §2.1 says the privileged API "can't be
designed in the abstract — the extraction reveals it." The 2026-07-05 refactor did that
extraction: it pulled four chrome surfaces out of `Workstation` into self-contained UI
classes (`perf_hud.py`, `update_ui.py`, `system_menu_ui.py`, `achievements_ui.py`), and
each class's docstring records a **"Dependency profile (the facade lens)"** — the exact
`ws.*` it touches, split into *shared / non-privileged* vs *privileged*. The list below
is those profiles consolidated (plus a pre-extraction profiling of the surfaces still in
`Workstation`), so the draft `make_system_api` is now grounded in what the code provably
needs rather than guessed.

**Per-surface privileged surface** (what each system surface touches that a kid cart must
*never* reach — everything else it uses is the shared, sandbox-equivalent drawing/input/
persistence layer, which is NOT part of `make_system_api`):

| Surface | Module (this session) | Privileged verbs it forces |
|---|---|---|
| Perf HUD | `perf_hud.py` (stage 5) | **none** — read-only frame-timing (`perf_snapshot`) |
| OTA update | `update_ui.py` (stage 6) | **`ota`** — the updater handle (check/download/install/reset) |
| System menu | `system_menu_ui.py` (stage 7) | **`reboot`**, **`del_cart`**, **`restart_cart`**, **`open_settings`**, open-about |
| Achievements/eggs | `achievements_ui.py` (stage 8) | **none** — shared `ws.ach` + persistence via the shared `_with_sd` |
| Settings | *(still in Workstation)* | **`web_view.toggle`**, **`set_diag_live`**, **`set_font_scale`**, **`persist_system`** + delegates into ota/theme/achievements (**the aggregator — its API is the union of the others**) |
| Theme | *(still in Workstation)* | **`wallpaper.select`/`cycle`**, **`icons.edit`/`save`** (repaints system chrome) |
| Launcher | *(still in Workstation)* | store-read + **`open(cart)`**, **`new_cart`**, **`dup_cart`** |
| Top bar | *(still in Workstation)* | mostly reads state; dispatches taps to the verbs above |

**Consolidated draft `make_system_api(ws)`** (the deduped union, grouped by concern):

- **Navigation / lifecycle:** `go_home()`, `open_settings()`, `open(cart)`, screen transitions
- **Cart store:** `new_cart()`, `dup_cart()`, `del_cart()`, `restart_cart()`/`apply()`
- **System:** `reboot()`, `ota` (the OTA updater handle), `set_diag_live()`, `set_font_scale()`, `persist_system()`
- **Theme:** `wallpaper.select()`/`cycle()`, `icons.edit()`/`save()`
- **Web view:** `web_view.toggle()`
- **Read-only but system-only:** `perf_snapshot()`, wifi status / credential read

**Findings that fall out of the evidence** (each one shapes §2.3–§2.4):

1. **The privileged surface is small, and it concentrates.** The *dangerous* verbs
   (`reboot`, `del_cart`) live almost entirely in the **system menu**; the *breadth*
   lives almost entirely in **Settings** (the aggregator). Everything between is thin.
2. **Two surfaces need ZERO privileged verbs** (Perf HUD, Achievements) — they're pure
   read-only / shared-state consumers. So "system surface" ≠ "needs the system
   namespace": if those ever became carts, they'd run under the *kid* `make_api` plus a
   read-only snapshot, no privilege at all. A useful narrowing of §2.1's scope.
3. **Settings' API = the union of the others**, confirmed from its call list — which is
   independent evidence for §2.4's "migrate Settings last" ordering: its facade isn't
   fully knowable until the surfaces it aggregates expose theirs.
4. **The shared primitives are the line.** `canvas`/`pointer`/draw-helpers/`mark_dirty`/
   `_with_sd` persistence are NOT in `make_system_api` — they're the sandbox-equivalent a
   kid cart already gets. `make_system_api` is *only* the ~15 privileged callables above,
   which keeps the trust boundary (§2.1) auditable and small.

### 2.3 Crash isolation and lifecycle

#55 already names this correctly: *the bar/launcher must never go down because a system
cart threw*. The existing cart-crash path (`Workstation`'s `cart_error` capture, the
error panel) is scoped to **one** running kid cart at a time and assumes falling back to
the launcher is always safe — that assumption breaks if the launcher *itself* is a cart
that can throw. A system cart needs its own guard: caught exceptions fall back to a
**hardcoded minimal recovery UI** (not another cart), so there is always a floor that
cannot itself fail. This is a small, well-scoped addition, not a new subsystem — but it
has to exist before any system surface migrates, or a bug in a "themeable Settings cart"
could brick the whole console with no way back in except a firmware reflash.

### 2.4 Migration order (only when there's concrete need — not now)

If/when this gets picked up, the natural order (least to most privileged, matching the
existing extraction pattern):
1. **Top bar** (#51, folded into #55) — mostly reads state (clock/wifi/battery icons)
   and dispatches taps to already-privileged `Workstation` methods; smallest privileged
   surface.
2. **Dropdown/system menu** (#52) — similar shape, a natural sibling of the bar.
3. **Launcher** — reads the cart store, opens a selected cart; needs store-read + `open()`.
4. **Settings** — the highest-privilege one (wifi credentials, OTA trigger, icon theme
   edit, PERF DIAG toggle); do this last, once the privilege boundary + recovery UI are
   proven on lower-stakes surfaces.

The already-extracted `BlockEditorUI`/`MapEditorUI`/`MusicEditorUI`/(a future
`CodeEditorUI`) are a different case: they're invoked *from inside* a system-privileged
context already (opened via the top bar / pause menu, never directly by a kid cart), so
they may not need the full system-cart treatment at all — worth revisiting once the
first real system-cart migration (the bar) is done and the pattern is proven.

---

## 3. A layered compositor (the webview / One-tier half)

### 3.1 Where this starts: the scroll/layer engine (#54)

`make_layer(w, h)` / `draw_layer(layer, cam_x, cam_y)` (#54, shipped) already gives a
cart an off-screen indexed buffer it draws into once and window-copies from every frame
— the same *shape* a window has (a private pixel buffer, composited into a visible
viewport at some offset). Today exactly one thing ever composites onto the real
framebuffer at a time: whatever `Workstation.frame()` is currently drawing (chrome, the
launcher, or the one running cart). The natural generalization is a compositor that
holds **N** named layers and decides which are visible and where, rather than one
mutated `DeviceCanvas`.

### 3.2 Why this matters for the web view

`moy_webserver.py`/`runtime/web_view.py` today mirrors the **single physical screen** —
whatever `DeviceCanvas` currently shows, recorded as a draw-command stream and replayed
in the browser. That's a remote *mirror*, not a window manager: there is exactly one
thing to look at, matching exactly what the device's own panel shows. A layered
compositor changes what there is to mirror: if the shell composites N layers instead of
one merged bitmap, the web view has the option of shipping **multiple layers/windows**
to the browser instead of one flattened frame — which is what "the web view as a window
manager" concretely means. This is a transport-side change built on a shell-side
primitive; it doesn't require rewriting `web_view.py`'s existing recorder, just giving
it more than one recordable surface to draw from.

### 3.3 Why the One/P4 tier is where this actually lands first

#58 already flags the shape: the One's 7″ 1024×600 MIPI-DSI panel, no SPI flush
ceiling, and 32MB PSRAM make a genuine multi-panel "desktop feel" (bigger editors,
maybe more than one visible at once) plausible in a way the T-Deck's 320×240 handheld
screen and tighter memory budget don't invite. Per #58's own "Desktop look" note: carts
stay 320×240 (portable, upscaled) — only the **shell** would ever render at native
resolution or host more than one panel. This doc doesn't propose *how much* multi-panel
UI the One should have (that's a product/UX call for when the hardware is in hand) —
only that the compositor should be shaped as N-layer-capable now, cheaply, since
introducing named layers costs nothing on the S3 (module/object boundaries, not a
per-frame cost) even while only one is ever visible there.

### 3.4 What this is explicitly NOT proposing (yet)

**Not** true concurrent multi-cart execution — several carts each ticking their own
`_update`/`_draw` simultaneously. That's a real multitasking model (per-cart Python VM
state, scheduling, memory budgeting across N resident carts) and a much bigger lift
than a compositor that can *display* more than one layer. Cooperative "the shell hosts
one active cart plus a small, fixed set of resident system carts" (launcher, bar) is
the honest ceiling to design toward until real P4 numbers (RAM, CPU headroom) make a
bigger claim defensible. Sizing that is future work, explicitly out of scope here.

---

## 4. Non-goals and constraints (repeated because they're easy to violate by accident)

- The kid-facing cart API and `docs/moy_cart_api.md` do not change. A cart authored
  today runs identically after any of this ships.
- Host == device stays true: any system-cart or compositor work is shared-source
  (`runtime/`), staged into firmware the same way `editors.py`/`block_editor_ui.py`/etc.
  already are — no device-only shell logic.
- Nothing here is a prerequisite for the P4 port (#58) itself; #58's base port
  (display/input/SD/Wi-Fi backend) ships independently of any shell/compositor rework.

## 5. Tracking

- **#55** — system-as-carts (the privilege half, §2). This doc expands it; comment
  added there pointing here.
- **New issue** — the layered-compositor / window-manager half (§3), split out because
  #55 doesn't cover it and #58 only footnotes it. Filed alongside this doc.
- **#58** — the P4 "One" port; its "Desktop look" section is where §3 concretely lands.
- **#54** — the layer/scroll engine this builds on.
- **#51 / #52** — bar-as-a-cart / dropdown menu, the first concrete #55 migration targets.
