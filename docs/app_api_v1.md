# The console APP API v1 — cartridge identity, system process

**Status:** SHIPPED (2026-07-12). This formalizes the pattern Paint, Appearance
and Storybook grew organically ("a cartridge identity backed by a
responsive system process") into one public seam, aligned with
`docs/shell_architecture_v1.md`'s privileged-system-carts direction. **Calc**
(`runtime/calc_app.py` + `system_carts/moybyte.calc.moy`) is the reference app — small
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

       def __init__(self, ctx, names): ...   # ctx = your AppContext

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
                   text_mode=False,        # True = typing app (Files precedent)
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

| role | what it is | verbs |
|---|---|---|
| `ctx.damage` | whole-surface invalidation: repaint next frame, or ask for one more frame from inside `draw()` | `all()`, `again()` |
| `ctx.surface` | the system canvas the app draws on, its scales, the desk's window flag, the live pointer, the chrome glyph painter | `canvas()`, `font_scale()`, `chrome_scale()`, `windowed()`, `pointer()`, `glyph()` |
| `ctx.theme` | the live panel-theme tokens and the verbs that change the look | `colors()`, `light()`, `name()`, `variant()`, `skin()`, `set()`, `set_variant()`, `set_skin()` |
| `ctx.files` | the USER-FILES store (#108): named documents (`docs` is plain Markdown — `files/docs/<name>.md`, the file's body IS the document), the trash, history sidecars, the image, cover and text codecs, provenance stamps | `readable()`, `ready()`, `batch()`, `list()`, `count()`, `load()`, `save()`, `delete()`, `duplicate()`, `rename()`, `new_name()`, `trash_list()`, `restore()`, `empty_trash()`, `history()`, `history_ops()`, `history_commit()`, `encode_image()`, `decode_image()`, `decode_cover()`, `encode_cover()`, `sig()`, `stamp()`, `encode_text()`, `decode_text()`, `provenance()` |
| `ctx.carts` | the CART store: the live cart list, projects' decks, code and images | `readable()`, `ready()`, `batch()`, `all()`, `can_journal()`, `slug()`, `hydrate()`, `apply()`, `load_deck()`, `save_deck()`, `save_code()`, `images()`, `save_image()`, `encode_image()` |
| `ctx.nav` | where the console goes next: another app, the Editor, a document, a run | `open_app()`, `is_system_app()`, `projects()`, `edit()`, `open_image()`, `open_text()`, `edit_file()`, `play()`, `run_script()`, `text_mode()` |
| `ctx.prefs` | per-app settings on `system.json`, namespaced per app | `get()`, `set()`, `clear()` |
| `ctx.notify` | achievement events | `achieve()` |
| `ctx.wallpaper` | the desktop-backdrop capability (Appearance and Paint only) | `current()`, `carts()`, `fills()`, `id_for()`, `cart_by_id()`, `select()`, `preview()`, `load_copy()`, `save_copy()` |
| `ctx.artwork` | the ArtworkService itself (Paint's document model) | `attach()`, `doc_name()`, `editable()`, `is_paint_app()`, `load()`, `new_doc()`, `open_named()`, `resend()`, `save()`, `set_wallpaper()`, `sync_wallpaper()`, `targets()`, `thumbnail()`, `usage()`, `why_read_only()` |
| `ctx.clipboard` | the system cut/copy/paste buffer (#132) | `put_text()`, `text()` |
| `ctx.install` | carts from outside (#124): the network Get Carts fetches through, its radio lease, the store session an install writes in, the engine's sizing, this console's chip and compiled-code format; in the browser, the keeper that makes an install durable in OPFS, the page's file picker, and where carts come from on a page a board serves | `hold()`, `release()`, `fit()`, `memory()`, `chip()`, `runtimes()`, `home()`, `can_pick()`, `pick()`, `root()`, `writable()`, `session()`, `rescan()`, `free()`, `find()`, `net()`, `keep()` |

Read that module for the signatures. The verbs column is the role table's
(`native/moy_app/roles.json`), and `tests/test_roles.py` holds the two equal.
Four things about the roles are load-bearing:

- **`NEEDS` is a filter, not documentation.** `AppContext` attaches only the
  declared roles, so reaching an undeclared one raises immediately.
  `tests/test_app_context.py` pins it in BOTH directions: a role your source
  names must be declared, and a role you declare must be named. An
  over-declaration is a capability granted for nothing, and user apps will be
  handed exactly these tuples.
- **Roles expose METHODS, and there is not one `property` in the module.**
  Measured (`docs/history/ui_refactor_2026-08.md` Section 2.4): a plain attribute hop
  costs +0.5us on the P4 and the same forward written as a descriptor costs
  +5.1us. So `cv = ctx.surface.canvas()`, and a test asserts the absence.
- **Hoist.** Bind the roles you use every frame once in `__init__`
  (`self._surf = ctx.surface`) and read the live values once at the top of
  `draw()`. Reading `ctx.surface.canvas()` per widget adds a call per widget;
  a counter budget in `tests/test_roles.py` caps it at one per drawn frame.
- **Storage returns `(value, err)` and never raises.** `err` is `None`, the
  `NO_STORE` singleton, or the failure's text -- which is exactly what
  `app_shell._persist` turns into CAN'T SAVE HERE versus CAN'T SAVE <why>.
  Several verbs in one storage session go through `batch(fn)`, whose `fn` gets a
  raw view of the same verbs.

**No role hands out the console.** An app reaches the shell only through the
roles above; the shared `file_widgets.FileGridView` lists and loads through the
`files` role its embedder passes it. `tests/test_app_context.py` fails an app
module that names the Workstation.

## Lifecycle: `close()` is the LEAVING hook

`close()` is optional and the host calls it when your app comes off the screen,
whatever route took it there. Implement it if you persist on an idle debounce --
it should be **change-gated and cheap**, because a pop home must not cost a
store write for an app nobody edited — a write is a floor plus the payload
(#154), and the floor alone is more than a frame.

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
  debounce (#111) implements it (`storybook_app` does); forgetting it costs an
  autosave, never the exit.

## Checklist for a new shipped app (2026-08-19: it is two files)

1. `runtime/<name>_app.py` — the Layer, with its `NEEDS` tuple.
2. `system_carts/moybyte.<slug>.moy` — the identity cart, whose manifest
   carries an `"app"` block in its `"moybyte"` object (Moybyte's own fields,
   SPEC.md §3.1):

   ```json
   "moybyte": { "app": { "id": "myapp", "entry": "myapp_app:MyAppLayer",
                         "text_mode": false, "order": 80 } }
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

The five lists this replaced were not merely tedious: **four of the five failed
silently, and on device only.** Forgetting `CART_ORDER` meant the identity cart
never seeded, so `is_app` never claimed it, so the app was unreachable on
hardware while working perfectly on the host.

## USER APPS: an app that is nothing but a cart (2026-08-19, #181)

Everything above describes a SHIPPED app -- a Layer class in `runtime/`, frozen
into the firmware, registered from a manifest. A USER APP is the other kind: an
ordinary `.moy` cart, editable in the project picker, that asks for shell
capabilities in its own `manifest.json` and gets them as cart globals.

    "moybyte": {"type": "app",
                "permissions": ["graphics", "input", "files:docs", "prefs"]}

The mechanism is `make_system_api(ctx_factory, cart, canvas, bar_h)`
(`runtime/system_api.py` -- READ IT, it is the authority and carries the policy
argument for each entry). It is a FILTER over the `AppContext` above and not a
second interface: it maps each declared permission to a role, calls the same
`ws.app_context` factory a shipped app goes through, and returns the globals
those roles publish. The Player merges that into the cart namespace.

**An ungranted capability has NO NAME.** Not a stub, not an object with a nicer
error -- absent, so touching it is a `NameError` in the cart. That is the same
gate `wifi` has ridden since #38 and `net` since #65, and `tests/test_user_apps.py`
proves it with one cart source opened twice, one manifest line apart.

| permission | cart globals |
|---|---|
| `files` / `files:<kind>` | `files.list/load/save/load_text/save_text/rename/delete/duplicate/new_name/badge`, scoped to ONE user-files kind (`docs` by default). `new_name(title)` takes a name a person typed and answers what it may be stored as; `badge(name)` is the mode table's short label for a row (`MD`/`TXT`/`JSON`/`PY`/`LUA`) |
| `prefs` | `prefs.get` / `prefs.set`, namespaced under the app's own title slug |
| `appearance` | `set_theme(name)` / `themes()` |
| `launch` | `open_app(id)` |

Never grantable, whatever a manifest says: `shell`, `carts` (a cart that can
author carts can escalate itself), `install` (the same, with somebody else's
cart), `wallpaper`, `artwork`, `damage`, `surface`, `notify`. Firmware update and reboot are not roles at all. The
enforcement is that the permission table is an ALLOWLIST, so a role nobody
mapped is ungrantable by construction; a test asserts every role in
`app_context.ROLES` is classified as one or the other, so ADDING a role forces
the decision.

Four globals every `type: "app"` cart gets with no permission, because they are
how an app draws rather than what it may reach: **`ui`** (the real
`runtime/ui.py`), **`screen()`** (the canvas -- `ui`'s `cv`), **`theme()`** (the
live tokens -- its `th`), and **`bar_h()`** (rows the host's exitable strip owns;
draw below them, never hardcode 18).

### The canvas: FIXED by default, responsive by opt-in

A shipped app is responsive because a Layer class can afford to be. A cart
hardcodes coordinates, so **fixed is the default and there is no way to get the
other by accident**: the manifest's `canvas` (320x240 unless it says otherwise)
is the raster, and the WM centres and integer-scales it exactly as it does for a
game. On the handheld nothing changed at all -- there is no new code on that path.

A cart that defines a top-level `_layout(w, h, fs)` opts INTO the whole system
surface: it draws on the system canvas, is called once before `_init` and again
whenever the size or font scale changes, and gets the responsive exitable strip
instead of the frozen 320-wide cluster. Two cases keep the fixed raster and are
told so through `_layout`'s own arguments, which is the whole reason it takes
them: a cart that ALSO declares a small `canvas` (that has a SPEC contract behind
it), and the windowed DESK world, where a cart lives in a window whose blit
source is `ws.canvas` -- binding the screen there makes the desktop blit itself.
From the fullscreen Library the same cart gets the full desktop surface.

Say the honest part out loud: **responsive is real authoring work most app carts
should not take.** It means no literal coordinates anywhere, a rect-algebra
layout pass, and testing at more than one size; `W`/`H` are bound once at start
and go stale, so a responsive cart must read its `_layout` arguments instead.
The payoff is one app that fills a 7" desktop and a 3.5" handheld. For a
calculator or a notepad, fixed is the right answer.

### What a user app costs to write

`system_carts/moybyte.notes.moy` is the worked example: a notepad that types, saves into
the kid's documents (the same `docs` kind Files browses -- open one
there and it is really the same file), lists what it saved and remembers which
note was open. **200 lines of cart, no shell code, no registration, no
`runtime/` module** -- and no C, no build, no reflash: it is a cart, so it edits
and re-runs on the device.

    system_carts/moybyte.notes.moy/manifest.json   "moybyte": "type": "app" + the permissions
    system_carts/moybyte.notes.moy/main.py         _init / _update / _draw
    system_carts/moybyte.notes.moy/sprites.moygfx  one 8x8 tile: its launcher icon

Compare the shipped path in the checklist below: a Layer class, a `NEEDS` tuple,
an `app` block, a regenerated `app_decls.py` and a firmware build.

### Crash isolation: three strikes (#160)

The Player has turned a cart exception into the friendly panel plus
crash-to-code since 2026-07-23. What it cannot catch is a cart that does not
raise -- a hang, an OOM, a native fault -- and if the thing that died runs
itself, the next boot runs it again. `runtime/crash_guard.py` marks the app id in
`system.json` BEFORE the cart's code is compiled and clears it after three
painted frames; three unhealed opens and the console stops running it, landing
the next tap on the ordinary error panel whose top bar carries EDIT/CODE. The
cart stays in the picker, because editing it is how it gets fixed.

The Player arms it for `type: "app"` carts only. Two small settings writes per
open is not free, and a game that always crashes shows the panel and is not a
brick. The wallpaper, which runs itself at every boot, has its own ledger; how
it boots without those writes is in `runtime/crash_guard.py`.

## Non-goals (v1)

- Kid-installed NATIVE apps -- a `runtime/` module in an image the kid did not
  build. `register_app` is a shell seam and stays one. What #181 asked for is
  covered by the USER APPS section above: the cart IS the app.
- Multiple instances of one app.

**App-to-app is no longer a non-goal (2026-08-19).** It was one, and it shipped
anyway: `files_app` reached straight into the notebook app's layer across five
sites, because "open this doc in the text app" is a real product need and there
was no seam for it. `ctx.nav.app(id)` / `ctx.nav.open_app(id)` is the seam --
resolution is by REGISTERED ID, so no app holds a reference to another app's
class and a build without the target degrades to a status line. IPC beyond
"open that, pointed here" is still out.

The Files ROUTER (docs/text_editing_2026-09.md) is the seam's second customer,
and it added verbs that are all "open that, pointed here":
`nav.projects()` lists the editable carts, `nav.edit(cart, tab)` opens one in
the project Editor, `nav.open_text(name, kind, mode)` opens a document on
the shell's text page in a `text_modes` mode, and `nav.open_image(name, kind,
cart)` opens a picture in Paint -- a gallery drawing, or a cart's OWN image
edited in place on that project's kind, which is the SAME console verb the
Editor's ADVANCED files row takes, so there is one image route and not two. `projects()` is on `nav` and not
on `ctx.carts` deliberately: a list of places to GO is navigation, and an app
that browses projects has not thereby earned the right to author executable
content.
