# The console APP API v1 — cartridge identity, system process

**Status:** SHIPPED (2026-07-12). This formalizes the pattern Paint, Appearance,
Writer and Storybook grew organically ("a cartridge identity backed by a
responsive system process") into one public seam, aligned with
`docs/shell_architecture_v1.md`'s privileged-system-carts direction. **Calc**
(`runtime/calc_app.py` + `system_carts/calc.moy`) is the reference app — small
enough to read in one sitting, built ONLY on the seams below.

## The model

A system APP is two artifacts:

1. **An identity cartridge** (`system_carts/<slug>.moy`): a normal `.moy` folder
   (manifest + `main.py`). The manifest carries a marker permission (e.g.
   `"calc"`); `main.py` is a small *fallback* body an older shell runs as a
   plain cart ("UPDATE MOYBYTE TO OPEN"). The cart gives the app its launcher
   tile, title, versioned re-seed (#47), and — because it is just a cart — it
   stays editable through the project picker like everything else.

2. **A content Layer class** (one module in `runtime/`, staged to the firmware
   builds like every shared module) implementing the Layer facets plus the app
   protocol:

   ```python
   class MyAppLayer:
       id = "myapp"          # process kind: router / back-stack / window key
       domain = "system"     # draws on the responsive system canvas
       TITLE = "MY APP"      # windowed WM title strip (falls back to id.upper())

       def is_app(self, cart): ...   # claim the identity cart (title + marker
                                     # permission + slug -- never a renamed copy)
       def open(self): ...           # (re)enter on every launch
       def relayout(self, w, h, fs): ...  # adopt a new canvas size / font scale

       def draw(self, dt): ...
       def handle_input(self, i): ...
       def handle_pointer(self, px, py, click): ...
   ```

3. **One registration** (console does this for the shipped apps; anything with
   a `ws` can do it after construction):

   ```python
   ws.register_app(MyAppLayer(ws, NAMES, _in),
                   text_mode=False,        # True = typing app (Writer precedent)
                   min_size=(310, 230))    # windowed resize floor, fs-scaled
   ```

   `min_size` may be omitted when the app's live layout exposes `MIN_W` and
   `MIN_H`; registration adopts those constants. `TITLE` is likewise captured
   by the registry, so the WM never needs an app-id/title ladder.

Everything else follows from the registration — **apps never edit console.py**:

- a launcher tap on the claimed cart opens the app instead of the Player;
- the router (`_content_layers`), back-stack kind, and windowed-WM window /
  taskbar chip / title strip;
- the per-window layout context (the WM captures every registered app's
  `.layout` generically, so the app reflows per window);
- the resize minimum (the ui.py min-size convention);
- keyboard text mode after open (for typing apps);
- the exitable bar itself — the strip draw and its context-X tap are the
  router's, not the app's (see "The bar contract" below);
- exit via the tool bar's context-X / `ws.exit()` — return-to-caller is the
  WM's, not the app's.

## What an app draws with

The ui toolkit (`runtime/ui.py`) is the intended surface: theme tokens
(`ws.theme_colors`, `ws.light_chrome()`), widgets (`button`, `chip`, `tab_row`,
`status_row`, `panel`, `toolbar`, `dialog`, `text_field`, `focus_ring`,
`scroll_cues`), `ScrollRegion`, the rect algebra (`cut_*`/`inset`/`hsplit`/
`vsplit` — the recommended layout style for NEW apps; see `CalcLayout`), and
`Hits` for draw==tap dispatch (see `CalcAppLayer.draw`/`handle_pointer` — the
draw pass registers every key's rect, the pointer resolves against the same
registry).

## The bar contract is the HOST's, not yours (2026-08-19)

An app draws **no bar at all**. On the fullscreen tiers the router paints the
minimal exitable strip (title + status + the context-X, spec `shell_ux_v1.md`
§9) *after* your `draw()` — chrome over content — and routes a tap in that band
*before* your `handle_pointer()`; in the windowed desk world it suppresses the
strip, because the WM's title strip carries the close there. You get all of
that from `register_app` alone, including in an app that has never heard of the
bar.

This used to be a paragraph here telling you to write both halves yourself, in
the right order, and an app that forgot either became **unexitable** — silently,
on device only. Seven apps carried the same two lines. `runtime/console.py`'s
`_app_bar_route` owns them now, pinned behaviourally by
`tests/test_app_api.py` (a stub app that draws no strip and routes no bar tap
must still show the strip and still exit on its X, and so must all seven
shipped apps, through the same assertion).

Two things follow for an app author:

- **Leave the bar band alone.** It is the top `layout.bar_h` rows of your own
  layout (`0` when windowed — the `ListShellLayout._init_frame` convention every
  app already follows); the host paints over it and swallows taps inside it, so
  `handle_pointer` never sees one. An app with no `layout` gets the bar's own
  band height instead.
- **Optional `commit(self)`** — the host calls it just before routing a bar
  tap, because the X there is an exit path. An app that persists on an idle
  debounce (#111) implements it (`writer_app`, `sheets_app`, `storybook_app`
  do); forgetting it costs an autosave, never the exit.

## Checklist for a new shipped app

1. `runtime/<name>_app.py` (the Layer), `system_carts/<slug>.moy` (identity).
2. `ws.register_app(...)` in console's app block (one line).
3. Staging: both firmware `build.sh` module lists; `host_app` bare-name alias.
4. `tools/gen_device_carts.py` `CART_ORDER` (device launcher order) and the
   parity map in `tests/test_device_seed_parity.py`.
5. Tests (see `tests/test_app_api.py` for the Calc set).

## Non-goals (v1)

- Third-party/kid-installed native apps: `register_app` is a SHELL seam; kid
  content stays `.moy` carts under the frozen cart API. The capability track in
  `docs/shell_architecture_v1.md` is where sandboxed app privileges would land.
- Multiple instances of one app, or app-to-app IPC.
