# Shell layers refactor — decompose `Workstation` into composited Layers

**Status:** DESIGN / READY-TO-EXECUTE INCREMENTALLY. This is a committed plan for
*how* to reshape `runtime/console.py`'s core loop, not an exploration. Each step
lands behind the existing nets (775+ host tests + the golden-frame pixel harness)
and is individually revertible.

**Companion docs:** `docs/shell_architecture_v1.md` — this refactor is the concrete
*enabling* step under both of its halves (§2 privileged system carts, §3 layered
compositor); see "Relationship to the shell architecture doc" below. The kid-facing
contract (`docs/moy_cart_api.md`) does **not** change.
**Successor:** `docs/shell_os_architecture_v1.md` — the adversarial review of this
refactor found its sharpest flaw: the Layer boundary made the coupling *legible* but
did not *reduce* it (every Layer still reaches through `self.ws` into a ~100-member
implicit API, including other surfaces' privates). That doc is the plan that fixes it —
per-surface capability APIs + an event bus, the same boundary the kid carts already
have via `make_api`.

**One-line thesis:** the remaining console cleanup stalled because we were moving
surfaces into *files* while they're still smeared across the god-loop. Give each
surface a **Layer boundary** (its own draw + input + state + constants, co-located)
**first**; the file/module/cart split then falls out mechanically.

---

## 1. The diagnosis — why "move it to a module" kept hitting a wall

Nine clean extractions (`perf_hud`/`update_ui`/`system_menu_ui`/`achievements_ui`
on the host, five device modules) worked because their state + constants were
already self-contained. The *next* six (top bar, cards, settings, theme, code,
paint) each stalled on the same thing, and it isn't about those clusters — it's the
shape of `Workstation`:

- `frame()` is one big `self.screen` / `self.menu_view` branch that draws **every**
  surface (see `runtime/console.py`, the `elif self.screen == …` / `elif
  self.menu_view == …` ladder).
- `handle_input()` and `handle_pointer()` are **parallel** branches that route input
  for every surface.
- State is flat attributes on `Workstation`.

So a single surface — say the top bar — is **smeared across all three**: its pixels
in `frame()`, its taps in `handle_pointer()`, its rects (`_HOME_BTN`/`_MENU_BTN`/…)
referenced in **20 places** spanning both. You cannot lift a smear into a file
without either duplicating the shared constants (drift) or leaving half the surface
behind (cross-file state). That *is* the spaghetti. Moving the smear to a module
relocates half of it; it doesn't remove the smear.

**The fix is a boundary, not a file.** Once a surface owns its draw + input + state +
constants together, the module split is a rename.

---

## 2. The `Layer` protocol

A uniform interface every surface implements. It's deliberately the shape the
extracted editors (`BlockEditorUI`/`MapEditorUI`/`MusicEditorUI`) *already nearly
have* — this refactor formalizes it, it doesn't invent it.

```python
class Layer:
    """One composited surface. Owns its own pixels, input, state, and constants."""

    # --- identity / router hints ------------------------------------------
    id       = "..."       # stable key ("launcher", "cards", "code", "bar", ...)
    domain   = "system"    # "system" (reflowed sys_canvas) or "game" (fixed 320x240)
    #                        -- preserves the #39 two-domain composite seam.

    def visible(self, ws) -> bool: ...   # is this layer painted this frame?

    # --- render -----------------------------------------------------------
    def draw(self, cv): ...              # paint self onto the canvas for its domain

    # --- input (return True == "I handled it; stop routing") --------------
    def handle_input(self, inp) -> bool: ...          # keyboard
    def handle_pointer(self, px, py, click) -> bool: ...  # touch / trackball

    # --- lifecycle --------------------------------------------------------
    def on_enter(self): ...   # became the active content layer / shown
    def on_leave(self): ...   # deactivated / hidden
```

Notes:
- `visible(ws)` encodes rules like "the bar auto-hides while a cart PLAYS (#71) —
  shown only when paused/error" and "the FPS chip only on the desktop".
- `domain` keeps the #39 seam: `game` layers draw on the fixed 320×240 canvas and
  are composited (upscaled) into the `system` canvas; `system` layers draw straight
  on the reflowed canvas. The router owns the one composite step, not the layers.
- `handle_pointer`/`handle_input` return a *handled* bool so the router can stop at
  the top-most layer that claims the event (overlay > content > chrome).
- A Layer holds a back-reference to the owning `Workstation` (`self.ws`) for the
  shared primitives (`canvas`, `pointer`, `cart`, store, `_glyph`/`_icon`/`_btn`,
  `go_home`, `open`, …) — the exact seam the extracted UIs already use. Shared draw
  helpers (`_glyph`/`_icon`/`_btn`/`_mini_btn`) stay on `ws` as the common toolkit;
  a Layer is a *consumer* of them, not an owner.

---

## 3. `Workstation` becomes a compositor / router

The god-branches collapse into three thin loops over a layer stack.

**Today** (`frame()` sketch, real):
```python
if   self._splash_until: self._draw_splash()
elif self.screen == "launcher":  self._draw_desktop_home(dt)
elif self.screen == "settings":  self._draw_settings(dt)
elif self.screen == "update":    self.update_ui._pump_update(dt); self.update_ui._draw_update(dt)
elif self.screen == "desktop":   ...cart _update/_draw + pause chrome + bar...
elif self.menu_view == "code":   self._draw_code()
elif self.menu_view == "blocks": self.block_ui._draw_blocks()
...                              # theme/paint/map/cards
# then: fps, sysmenu, about, toast, egg, cursor overlays
```

**Target** (`frame()`):
```python
def frame(self, dt):
    self._tick_active(dt)                     # cart _update / editor previews / update pump
    for layer in self._visible_stack():       # z-ordered, bottom -> top
        with self._perf(layer.id):            # keep the CHROMEBRK/DRAWBRK per-layer timing
            self._paint(layer)                # handles the game->system composite by domain
```

**Target** (`handle_pointer`):
```python
def handle_pointer(self, px, py, click):
    for layer in reversed(self._visible_stack()):   # top -> bottom
        if layer.handle_pointer(px, py, click):
            return
```

**Target** (`handle_input`):
```python
def handle_input(self, inp):
    if self._global_keys(inp):        # THE one console key (#71 BACKSPACE pause), etc.
        return
    self._active_content.handle_input(inp)
```

The `screen` / `menu_view` strings don't vanish — they become **"which content layer
is active,"** a registry lookup instead of a 12-arm branch. `_visible_stack()` is the
single place the z-order + visibility rules live:

```
wallpaper (system, launcher/settings only)
  → active content layer (cards | code | paint | map | blocks | music | settings | update | running-cart)
  → chrome bar (system/desktop, visible per #71)
  → dock (system, launcher/settings)
  → overlays (sysmenu | about | toast | egg)   # transient, topmost content
  → perf HUD (desktop, if shown)
  → cursor (always top)
```

---

## 4. Current-state inventory — how far along we already are

The codebase is **~40% layered already**. The `frame()` ladder is an implicit layer
selector; several surfaces are already objects with `draw`/`input`/`pointer` methods.

| Surface | Today | Conforms to `Layer` by… |
|---|---|---|
| Block editor | `BlockEditorUI` (own file) | adapter over `_draw_blocks`/`_blocks_input`/`_blocks_pointer` |
| Map editor | `MapEditorUI` (own file) | adapter over `_draw_map`/`_map_input`/`_map_click`/… |
| Music editor | `MusicEditorUI` (own file) | adapter over `_draw_music`/`_music_input` |
| OTA update | `UpdateUI` (own file) | adapter over `_draw_update`/`_update_input`/`_update_pointer` |
| System menu | `SystemMenuUI` (own file) | overlay layer over `_draw_sysmenu`/… |
| Achievements/eggs | `AchievementsUI` (own file) | overlay layer over `_draw_egg`/`_draw_achievements` |
| Perf HUD | `PerfHud` (own file) | overlay layer (read-only) |
| **Top bar / dock** | **smeared** in `frame`+`handle_pointer` | **new `BarLayer`** (draw + taps + rects + cache state together) |
| **Cards / config home** | **smeared** | **new `CardsLayer`** |
| **Settings** | **smeared** | **new `SettingsLayer`** |
| **Theme (wallpaper+icons)** | **smeared** | **new `ThemeLayer`** |
| **Code editor glue** | **smeared** (`self.editor` semi-public) | **new `CodeLayer`** |
| **Paint editor glue** | **smeared** | **new `PaintLayer`** |
| Launcher grid | part of `_draw_desktop_home` | **`LauncherLayer`** (or folded with Cards) |
| Running cart | inline in `frame` desktop branch | **`CartLayer`** (the game domain) |

So the work splits cleanly: **(A) make the already-object surfaces conform** to the
protocol + prove the router with them (near-zero pixel risk), then **(B) migrate the
smeared surfaces one at a time** — and *migrating a surface into a Layer is the same
motion as extracting it to a module, done once, correctly.*

---

## 5. Migration strategy — incremental, pixel-identical, reversible

The refactor reshapes the core loop, so it must never be a big-bang. The sequence
keeps the golden-frame harness **byte-identical at every commit** because the layer
stack preserves draw order.

**Phase 0 — introduce the seam (no behavior change).**
- Add the `Layer` base + `_visible_stack()`/`_active_content` machinery to
  `Workstation`, but have `_visible_stack()` return **wrapper layers whose `draw`
  simply calls the existing `_draw_*` methods** (a `_LegacyLayer(fn)` shim). `frame`
  becomes the loop; every existing draw runs in the same order. Same for input.
  Golden must be identical — this is the proof the router preserves order.

**Phase 1 — conform the already-object surfaces.**
- Wrap `BlockEditorUI`/`MapEditorUI`/`MusicEditorUI`/`UpdateUI`/`SystemMenuUI`/
  `AchievementsUI`/`PerfHud` in real `Layer` adapters (thin — they already have the
  methods). Delete their `_LegacyLayer` shims. Router now drives real layers for the
  half of the shell that's already objects. Still pixel-identical.

**Phase 2 — migrate the smeared surfaces, one per commit (this is the payoff).**
For each of BarLayer → CardsLayer → SettingsLayer → ThemeLayer → CodeLayer →
PaintLayer: move its `_draw_*` **and** its slice of `handle_pointer`/`handle_input`
**and** its constants **and** its state into the Layer class, and register it. Its
`handle_pointer` dispatches back to `ws` for core actions (`go_home`, `open`, …) —
the same way `SystemMenuUI`'s items already call `ws` verbs. Each such commit both
shrinks `Workstation` **and** produces a self-contained unit; a follow-up rename
moves the Layer to its own file (`bar_layer.py`, …) with zero further untangling.

**Migration order + rationale** (least-coupled first, by `ws.` pin count measured):
1. **Top bar / dock** (12 pins) — smallest; proves the "move draw+taps+rects+cache
   together" pattern; also the #55/#51 first cart-migration target.
2. **Cards / config home** (34) — the kid "make it mine" surface.
3. **Settings** (40) — the aggregator; its taps already delegate into update/theme/
   achievements layers, so it migrates cleanly *after* they exist.
4. **Theme** (wallpaper + icon editor) — the icon editor is a PaintEditor over
   `icon_sheet`; folds naturally once PaintLayer exists. Keep `load_icon_sheet` /
   `load_system` reachable on `ws` (device backend calls them) via the Layer or a
   one-line forward.
5. **Code** / **Paint** editor glue (42 / smaller) — mirror the block/map/music
   editor layers; `self.editor` stays the shared "current editor" handle on `ws`
   (semi-public; tooling + 42 refs) with the Layer as its driver.

**The floor (stays in `Workstation`, ~1,300 lines):** the router itself (`frame`
loop, `_visible_stack`, input routing), the game↔system composite (`_composite_game`,
#39), lifecycle (`open`/`_start`/`go_home`/`set_menu_view`), the shared draw toolkit
(`_glyph`/`_icon`/`_btn`/`_mini_btn`), and the `make_api` boundary. Splitting *those*
across files is the real spaghetti — a router is cohesive by definition.

---

## 6. Invariants (must hold at every commit)

- **Golden pixel-identity.** The layer stack preserves draw order, so the 9 golden
  chrome screens stay byte-identical to pre-refactor `master` through the whole
  migration. Any drift = a bug, caught before commit.
- **Host == device.** All Layer code is shared `runtime/` source staged into the
  firmware `modules/` tree the same way `console.py` already is. No device-only shell
  logic. `moy_runtime.run_desktop` keeps injecting `make_api` + the store.
- **Kid API frozen.** `make_api` / `docs/moy_cart_api.md` do not change. Layers are a
  *shell*-side structure; a cart authored today runs identically. The running cart is
  just the `CartLayer` in the game domain.
- **Perf neutral.** The per-layer `with self._perf(layer.id)` preserves the existing
  CHROMEBRK/DRAWBRK breakdown; the router is object dispatch (branch → method call),
  the same cost class as today's `elif` ladder. #66 numbers must not regress.
- **Portable/indexed canvas contract** (`CLAUDE.md`) is untouched — layers draw
  through the same `cls/rect/spr/print/…` verbs.

---

## 7. Relationship to the shell architecture doc

`docs/shell_architecture_v1.md` sketched two "linked but separable" directions. This
refactor is the **missing common foundation** under both, and it identifies the
concrete need that doc was waiting for:

- **§2 (privileged system carts).** A Layer that owns its draw + input + state is one
  step from a system *cart*: run its `draw`/`handle_*` from the sandbox + inject the
  `make_system_api` (§2.2, the evidence-derived verb list). The migration order here
  (bar first) is exactly §2.4's. You can't cart-ify a smear; you can cart-ify a Layer.
- **§3 (layered compositor / #73).** §3 is retained pixel *buffers* per layer (for
  the web-view-as-window-manager + the One/P4 desktop feel). This refactor is the
  **logical** layering that must exist first: once surfaces are Layers, giving a Layer
  a retained buffer is a local change, and the web view can ship N layers instead of
  one flattened frame. §3.3 said "watch for a concrete need on the T-Deck side" — this
  is it: the layer decomposition unblocks the module/cart cleanup *today*, no P4
  required.

So the corrected dependency is: **logical Layers (this doc) → { modules (rename),
privileged carts (§2), retained-buffer compositor (§3) }.** All three consume the
same boundary.

---

## 8. Explicitly NOT in scope

- **Retained per-layer pixel buffers** — that's §3/#73, a later optimization on top
  of this. Phase 0–2 composite by *redrawing* in z-order (exactly as today), just
  through objects.
- **Concurrent multi-cart execution** — still one active cart + resident system
  surfaces (§3.4). A Layer is a render/input unit, not a scheduled process.
- **Any kid-facing change** — `make_api` and the cart contract are frozen.
- **A window manager / multi-panel UI** — that's the One/P4 payoff §3 defers; this
  refactor only makes it *reachable*.

---

## 9. Risks

- **Core-loop reshape touches the hottest path.** Mitigation: Phase 0 is a pure
  no-op shim proven by golden pixel-identity; every later commit keeps that invariant.
- **Input routing order is subtle** (overlays must win over content over chrome).
  Mitigation: `_visible_stack()` is the single source of z-order; the existing
  `handle_pointer` branch order is the reference to reproduce, and the redraw-count
  tests (`test_top_bar.py`, #66) guard the bar-cache behavior.
- **The `game`/`system` two-domain composite (#39)** must stay exactly one step.
  Mitigation: `domain` on the Layer + the router owning `_composite_game`; no Layer
  touches the composite.
- **Scope creep into §2/§3.** Mitigation: this doc stops at logical layers + the
  module split; carts and retained buffers are explicitly out (§8).
