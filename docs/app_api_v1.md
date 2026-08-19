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
       NEEDS = ("surface", "theme", "damage")   # the shell roles you use

       def __init__(self, ctx, names, in_rect): ...   # ctx = your AppContext

       def is_app(self, cart): ...   # claim the identity cart (title + marker
                                     # permission + slug -- never a renamed copy)
       def open(self): ...           # (re)enter on every launch
       def relayout(self, w, h, fs): ...  # adopt a new canvas size / font scale
       def close(self): ...          # OPTIONAL -- you are leaving the screen

       def draw(self, dt): ...
       def handle_input(self, i): ...
       def handle_pointer(self, px, py, click): ...
   ```

3. **One registration** (console does this for the shipped apps, from the
   manifest declaration; anything holding a `ws` can do it after construction):

   ```python
   ws.register_app(MyAppLayer(ws.app_context("myapp", MyAppLayer.NEEDS),
                              NAMES, _in),
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

## What an app is HANDED: the AppContext (2026-08-19)

An app used to hold `ws` -- the whole `Workstation` -- and reach through it for
whatever it needed, private members included. Across the seven shipped apps that
came to 41 distinct names and ~371 uses, 13 of the names private. Nothing could
say what an app was permitted to do, which is the question user apps have to
answer.

An app now takes an **`AppContext`** (`runtime/app_context.py`) carrying only the
roles it declared:

| role | what it is |
|---|---|
| `ctx.damage` | `all()` -- repaint the whole system surface next frame |
| `ctx.surface` | `canvas()`, `size()`, `font_scale()`, `windowed()`, `pointer()`, `glyph()` |
| `ctx.theme` | `colors()`, `light()`, `name()`, `variant()`, `set()`, `set_variant()` |
| `ctx.files` | the USER-FILES store (#108): named documents, the trash, history sidecars, the image codec |
| `ctx.carts` | the CART store: projects, decks, cart images, `create`/`scan`/`hydrate` |
| `ctx.nav` | `app()`, `open_app()`, `play()`, `open_workspace()`, `text_mode()`, `is_system_app()` |
| `ctx.prefs` | `get`/`set`/`clear` on `system.json`, namespaced per app |
| `ctx.notify` | `achieve()`, `notice()` |
| `ctx.wallpaper` | the desktop-backdrop capability (this app and Paint only) |
| `ctx.artwork` | the ArtworkService handle (Paint's document model) |
| `ctx.clipboard` | the system cut/copy/paste buffer (#132) |
| `ctx.shell` | the escape hatch -- see below |

Read that module for the signatures; it is the authority and this table is a
map. Four things about it are load-bearing:

- **`NEEDS` is a filter, not documentation.** `AppContext` attaches only the
  declared roles, so reaching an undeclared one raises immediately.
  `tests/test_app_context.py` pins it in BOTH directions: a role your source
  names must be declared, and a role you declare must be named. An
  over-declaration is a capability granted for nothing, and user apps will be
  handed exactly these tuples.
- **Roles expose METHODS, and there is not one `property` in the module.**
  Measured (`docs/ui_refactor_2026-08.md` Section 2.4): a plain attribute hop
  costs +0.5us on the P4 and the same forward written as a descriptor costs
  +5.1us. So `cv = ctx.surface.canvas()`, and a test asserts the absence.
- **Hoist.** Bind the roles you use every frame once in `__init__`
  (`self._surf = ctx.surface`) and read the live values once at the top of
  `draw()`. Reading `ctx.surface.canvas()` per widget adds a call per widget;
  a counter budget in that test file caps it at one per drawn frame.
- **Storage returns `(value, err)` and never raises.** `err` is `None`, the
  `NO_STORE` singleton, or the failure's text -- which is exactly what
  `app_shell._persist` turns into CAN'T SAVE HERE versus CAN'T SAVE <why>.
  Several verbs in one storage session go through `batch(fn)`, whose `fn` gets a
  raw view of the same verbs.

**`ctx.shell` is the un-narrowed Workstation, and it is open for one reason:**
the shared `file_widgets.FileGridView` still duck-types on `ws.carts_store` /
`ws.carts_root` / `ws._with_sd`. Four apps declare it to construct that widget,
its consumer list is pinned so it can only shrink, and it is the one role a user
app will never be granted. Giving the widget the files role closes it.

## Lifecycle: `close()` is the LEAVING hook

`close()` is optional and the host calls it when your app comes off the screen,
whatever route took it there. Implement it if you persist on an idle debounce --
it should be **change-gated and cheap**, because a pop home must not cost a
flash write for an app nobody edited (~800ms on the P4).

`commit()` (see "The bar contract" below) is its forced twin for an explicit
exit GESTURE: the bar's context-X, or the WM strip's X on the windowed tier.

This replaced a ladder in `go_home()` that named four apps and four different
verbs. An app persisting on a debounce that nobody added to that list lost the
kid's work -- the same shape as the bar bug, one level down. Neither list exists
now.

## What an app draws with

The ui toolkit (`runtime/ui.py`) is the intended surface: theme tokens
(`ctx.theme.colors()`, `ctx.theme.light()`), widgets (`button`, `chip`, `tab_row`,
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

## Checklist for a new shipped app (2026-08-19: it is two files)

1. `runtime/<name>_app.py` — the Layer, with its `NEEDS` tuple.
2. `system_carts/<slug>.moy` — the identity cart, whose manifest carries an
   `"app"` block:

   ```json
   "app": { "id": "myapp", "entry": "myapp_app:MyAppLayer",
            "text_mode": false, "order": 80 }
   ```

   then regenerate the frozen copy:
   `python tools/gen_device_carts.py --app-decls`.
3. Tests (see `tests/test_app_api.py` for the Calc set).

**That is the whole list.** Everything else is derived from the declaration:
console constructs and registers it in a loop (there is no per-app line in
`console.py`), the device seed order, the host↔device title map, and the web
bundle's roster. `tests/test_app_registry.py` fails if any of them grows a
hand-written app name back.

Staging needs no entry either — since #161 a board declares what it DENIES in
its `board.toml`, so a new `runtime/` module reaches every target by default and
keeping it off one is a written decision. (This section used to say "both
firmware `build.sh` module lists", which stopped being true when that landed.)

The four lists this replaced were not merely tedious: **four of the five failed
silently, and on device only.** Forgetting `CART_ORDER` meant the identity cart
never seeded, so `is_app` never claimed it, so the app was unreachable on
hardware while working perfectly on the host.

## Non-goals (v1)

- Third-party/kid-installed native apps: `register_app` is a SHELL seam; kid
  content stays `.moy` carts under the frozen cart API. The capability track in
  `docs/shell_architecture_v1.md` is where sandboxed app privileges would land,
  and `NEEDS` is the shape it will take -- `make_system_api(ctx, cart)` is the
  same filter keyed on a manifest's permissions instead of a class constant.
- Multiple instances of one app.

**App-to-app is no longer a non-goal (2026-08-19).** It was one, and it shipped
anyway: `files_app` reached `ws.writer_app.open_named(...)` across five sites,
because "open this table in Sheets" is a real product need and there was no seam
for it. `ctx.nav.app(id)` / `ctx.nav.open_app(id)` is the seam -- resolution is
by REGISTERED ID, so no app holds a reference to another app's class and a build
without the target degrades to a status line. IPC beyond "open that, pointed
here" is still out.
