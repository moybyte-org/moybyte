# The app ABI — sprint 5's design (2026-10)

**What this is.** What sprint 5 of `docs/native_kernel_2026-09.md` (#224)
builds, written before any of it: the roles an app is handed, reshaped so a
role call carries numbers, handles and byte spans only; where each role's verbs
are served; how thin the Python binding gets; how a compiled module reaches a
role through an import; what every shipped app does so it comes back after the
kernel stopped the VM; the order the steps land in, and the gate. Status and
the sprint's measurements are #224's.

**The scope (owner, 2026-10-10; the plan's §10 question 8).** Sprint 5 runs in
full. Sprint 6 runs data-driven, in a doc of its own. Sprint 7 does not run:
the window managers and the rest of `runtime/console.py` stay Python. Nothing
here assumes they cross. The roles are the line between an app and the
console; the console's Python half sits on the far side of that line, and a
role verb whose state lives there is served from there.

**What it stands on.** Sprint 2's spine: handle tables, the app registry, the
back-stack, the return records, the leases and the settings rows
(`native/moy_spine/moy_route.h`). Sprint 1b's store. Sprint 3's glass, input
and loop, with the counted upcall door (`native/moy_kernel/moy_loop.h`).
Sprint 4's run: the Player in C, the VM stopped when a cart needs the memory,
the return start that rebuilds the shell and lands the launcher on its resume
record (`docs/kernel_cartpath_2026-10.md` §5).

## 1. The roles today

`runtime/app_context.py`'s `ROLES` names thirteen. An app declares the ones it
uses in `NEEDS`, `AppContext` attaches only those, and `runtime/system_api.py`
grants a subset to a `type: "app"` cart by its manifest permissions. The
consumers, from the `NEEDS` tuples and the grant table:

| role | verbs (live in the code) | shipped consumers | user-app grant |
|---|---|---|---|
| `damage` | `all`, `again` | Calc, Files, Paint, Appearance, Storybook, Get Carts | never |
| `surface` | `canvas`, `font_scale`, `chrome_scale`, `windowed`, `pointer`, `glyph` | the same six | never; a cart gets `screen()` and `bar_h()` ungated |
| `theme` | `colors`, `light`, `name`, `variant`, `skin`, `set`, `set_variant`, `set_skin` | the same six | reads ungated (`theme()`); `appearance` grants `set_theme`, `themes` |
| `files` | the user-files store: list, count, load, save, delete, duplicate, rename, new_name, the trash, history and its ops, the image, cover and text codecs, `sig`, `stamp`, `provenance`; `readable`, `ready`, `batch` | Files, `ArtworkService` | `files` / `files:<kind>`, scoped to one kind (`ScopedFiles`), with `open_editor` |
| `carts` | `all`, `can_journal`, `slug`, `hydrate`, `apply`, `load_deck`, `save_deck`, `save_code`, `images`, `save_image`, `encode_image`; `batch` | Storybook, `ArtworkService` | never |
| `nav` | `app`, `open_app`, `is_system_app`, `projects`, `edit`, `edit_file`, `open_image`, `open_text`, `play`, `run_script`, `text_mode` | Files, Storybook, Get Carts, `ArtworkService` | `launch` grants `open_app` |
| `prefs` | `get`, `set`, `clear`, namespaced | `ArtworkService` (namespace `paint`) | `prefs` |
| `notify` | `achieve` | `ArtworkService` | never |
| `wallpaper` | `current`, `carts`, `fills`, `id_for`, `cart_by_id`, `select`, `preview`, `load_copy`, `save_copy` | Appearance, `ArtworkService` | never |
| `artwork` | the `ArtworkService` object itself | Files, Paint, Appearance, Storybook | never |
| `clipboard` | the `Clipboard` object itself (`runtime/widgets.py`) | Storybook | `clipboard` |
| `install` | `net`, `home`, `keep`, `can_pick`, `pick`, `hold`, `release`, `root`, `writable`, `session`, `rescan`, `free`, `find`, `runtimes`, `fit`, `memory`, `chip` | Get Carts | never |
| `shell` | the Workstation | Files and Paint, to build `FileGridView` | never |

The Workstation reaches two of them past any context: `runtime/console.py`
calls `artwork.open_named` (the image door) and `console_spine.load_system`
calls `artwork.sync_wallpaper`; `runtime/player.py` asks
`artwork.is_paint_app`. Three verbs have no caller: `nav.app`, and the
`surface.size` and `notify.notice` that `docs/app_api_v1.md` lists and the code
never had.

**What is object-shaped** (the plan's §2.1): `surface.canvas` and `pointer`
return objects; `theme.colors` returns a dict; `files.batch`, `carts.batch` and
`install.session` take a Python callback; `nav.play` takes the calling Layer
and `open_app` accepts one; `carts.all`, `wallpaper.carts`, `cart_by_id`,
`install.find` and `nav.projects` return cart dicts; `wallpaper.preview` draws
into a canvas object; `install.net` and `keep` return service objects;
`artwork` and `clipboard` are objects; `shell` is the whole console.

## 2. The import-shaped design

### 2.1 The rule: one table, a server per verb

**The role table is data**: `+native/moy_app/roles.json`, one row per verb —
the role, the verb, its C signature, its wasm type, its **server**, and the
permission that grants it (or `never`). It is to the app ABI what moy-spec's
`wasm-imports.json` is to the cart verbs: the bindings, the import adapter,
`docs/app_api_v1.md`'s table and the author's header are each held to it by a
test. `ROLES` is read from it.

**Every verb has one of two servers.**

- **C**: the state is the kernel's — the settings rows, the glass, the store's
  volumes, a kernel table this sprint adds. The verb is a C function in
  `+native/moy_app/`, and a call from any runtime is a plain call, no VM
  touched.
- **shell**: the state is the Python console's, which stays Python (sprint 7
  does not run) — the routes' surfaces, the look's caches, the wallpaper
  cart's namespace, the achievements, Get Carts' transport. The kernel calls
  the verb's Python server, which the Workstation registers when it is built,
  through the counted door as a SERVICE upcall. A Python app's call takes the
  same path, so there is one implementation and the trace sees it. With no VM,
  a shell verb answers `MOY_APP_NEEDS_VM`, and a module that imports one keeps
  the VM (§4.3).

The verbs called every frame are all C-served or cached by generation in the
Python binding, so the frame path makes no upcall (§3).

### 2.2 How an app declares what it imports

| runtime | the declaration | where it is checked |
|---|---|---|
| a shipped app (Python Layer) | `NEEDS`, unchanged | `AppContext` builds the grant; `tests/test_app_context.py` holds both directions |
| a user app (`type: "app"` cart, Python) | the manifest's permissions, unchanged | the grant is made in C from the catalogue row at the run's launch; the Python cart binding publishes globals only for granted verbs (§3) |
| a compiled app (wasm) | the manifest's permissions, and the module's imports | each import must be a row of a granted role; an import from an ungranted role is refused at load, naming it (§4) |

**The grant row.** Every caller of a role is a row of a new kind, GRANT, in
`native/moy_spine/moy_htab.h` (after sprint 4's IMAGE and ACTOR): the app's id,
its role mask, its files kind (a `files:<kind>` grant), its prefs namespace and
its open store session. A shipped app's grant is made at registration and
re-made at every start; a user app's at its run's launch, released with the
run. Every role call takes the grant handle first. The caller's identity is the
grant's, so no verb takes "the calling app" as an argument.

### 2.3 Handles, errors, call shapes

Errors extend `MOY_HTAB_*` as `moy_route.h`'s and `moy_play.h`'s do:

    enum {
        MOY_APP_OK = 0, MOY_APP_STALE = 1, MOY_APP_FULL = 2, MOY_APP_NOMEM = 3,
        MOY_APP_DENIED = 4,   // the grant does not hold this role
        MOY_APP_NOSTORE = 5,  // no writable store: NO_STORE
        MOY_APP_IO = 6,       // the store failed; moy_app_why() has its text
        MOY_APP_BAD = 7,      // a refused argument: an unknown kind or token, a name too long
        MOY_APP_ABSENT = 8,   // no such cart, app or document, or a build without it
        MOY_APP_NEEDS_VM = 9, // a shell-served verb while no VM runs
    };

`DENIED` is the C guard for a binding that exposed what it should not: in
every runtime an ungranted verb has no name. Text crosses as a byte span
(pointer and length); an answer that is text or a blob is written into a
caller's buffer and returns its length, with a `*_size` verb beside it for
blobs; a list of carts or documents is a count plus a packed row buffer of ids;
the numbers are `int32`, as moy-spec's binding marshals them. Ids are the ones
the store already keys on: a cart is its catalogue handle or its folder, a
document is `(kind, name)`, an app is its registered id.

### 2.4 The roles, reshaped

| role | import shape | server | what moves |
|---|---|---|---|
| `damage` | `all()`, `again()` | C | the kernel's frame epoch flag, folded where the frame gate folds `ws._dirty` today; `again` is the loop's next-frame request |
| `surface` | `canvas() -> handle`, `size(&w,&h)`, `font_scale()`, `chrome_scale()`, `windowed()`, `bar_h()`, `pointer(out[5])` | C | the canvas is the glass row the system canvas holds; the rest is a **surface row** the shell writes at each relayout, look change and window focus, and the pointer is read from the kernel's input state in the app's coordinates. `bar_h` joins the role, so the ungated cart global and the shipped apps read one value |
| `surface` | `glyph(id, x, y, w, h, colour)` | shell | the glyph vocabulary is the toolkit's (sprint 6) |
| `theme` | `token(role_id)`, `gen()`, `light()`, `name(buf)`, `variant(buf)`, `skin(buf)` | C | the **live token table**: one theme's flattened tokens and a generation, written by the look at every switch. Role ids are theming's vocabulary (`docs/theming_2026-09.md` §4.1), listed in the role table; the generation is the integer theming §6 calls `look_gen` |
| `theme` | `set(name, variant)`, `set_variant(v)`, `set_skin(name)` | shell | the look (`runtime/appearance.py`) coordinates caches, the skin and the wallpaper, all Python |
| `files` | the list, count, load, save, delete, duplicate, rename, new_name, trash, history and codec verbs over `(kind, name)`; `begin()` / `end()`; `ready()`, `readable()` | C (question 2) | the user-files layer, `runtime/moy_files.py` and `runtime/moy_file_ops.py`, over `moy_vol` and `moy_fs`; a session is the grant's, so the SD gate is held across the verbs between `begin` and `end` |
| `carts` | `ids(out)`, `row(id, out)`, `slug`, `can_journal`, `load_deck`, `images`, `encode_image` | C | the catalogue's rows and the store's reads |
| `carts` | `save_deck`, `save_code`, `save_image`, `rescan` (the old `hydrate` and `apply`) | shell | `runtime/project_store.py`'s commit verbs ask the block compiler and Storybook, which are apps, and the roster (`runtime/cart_manager.py`) is the shell's |
| `nav` | `open_app(id)`, `edit(cart, tab)`, `edit_file(cart, name, mode)`, `open_text(kind, name, mode)`, `open_image(kind, name, cart)`, `play(cart)`, `run_script(kind, name, why_buf)`, `projects(out)`, `is_system_app(cart)`, `text_mode(on)` | shell | the routes are C tables, but every verb lands on a Python surface. `play` takes no caller: the grant is the caller. `nav.app` is deleted |
| `prefs` | `get(key, buf)`, `set(key, json)`, `clear(key)` | C | the settings rows, under the grant's namespace; values cross as JSON text, as the rows hold them |
| `notify` | `achieve(kind, key)` | shell | the `Achievements` object is the console's |
| `wallpaper` | `current(buf)`, `fills(out)`, `carts(out)`, `id_for(cart, buf)`, `load_copy`, `save_copy` | C | the settings row, a constant table, a catalogue query by type, the backdrop's backing file and the preview sidecar (`runtime/moy_image.py`'s, the plan's §2.2.1 row) |
| `wallpaper` | `select(id)`, `preview(canvas, x, y, w, h, dt)` | shell | §6 |
| `artwork` | `current(kind_buf, name_buf)` | C | Paint's open drawing, read from its settings rows; a consumer loads it through `files`. `ArtworkService` becomes Paint's own model (§6) |
| `clipboard` | `put(text)`, `get(buf)` | C | a kernel text row in PSRAM with a fixed cap (configuration, set in the step); it survives a stop |
| `install` | `fetch(url) -> request`, `poll(request, buf)`, `close(request)`, `home(buf)`, `pick(name, size, host) -> request`, `hold()`, `release()`, `begin()`, `end()`, `writable()`, `rescan()`, `free(&bytes,&block)`, `find(folder) -> cart`, `runtimes(out)`, `fit(...)`, `memory(...)`, `chip(buf)` | shell, except `hold`/`release` (the lease table) and `fit`/`memory`/`chip` (the engine's) | Get Carts' transport and keeper stay the console's; `runtime/cart_index.py` is an app's engine and stays Python |
| `shell` | — | — | closed (§2.5) |

**Callbacks become sessions.** `batch(fn)` and `install.session(fn)` are
`begin` / `end` on the grant's session. The raw views (`_RawFiles`,
`_RawCarts`) go: inside a session every verb answers as it does outside one,
so no verb has two return shapes by construction, not by convention.

### 2.5 Closing `ctx.shell`

Two consumers declare it (`SHELL_CONSUMERS` in `tests/test_app_context.py`):
`FilesAppLayer` and `PaintAppLayer`, and the only use the ratchet allows is
handing the Workstation to `FileGridView` (`runtime/file_widgets.py`), which
reads `ws.carts_store`, `ws.carts_root` and `ws._with_sd` to list and load one
kind's files. `FileGridView` takes the files role instead and lists and loads
through it, in one session. Then `shell` leaves `ROLES`, `NEVER_GRANTED` and
the role table; `SHELL_CONSUMERS` and its two tests become one assertion that
no role hands out the console and no app module names it. Nothing else calls
the hatch.

## 3. The Python binding

**What it becomes.** `+native/moy_app/modmoy_app.c` on the boards and in the
browser, and the ctypes binding on the host (`+tools/moy_app_binding.py`, the
`tools/moy_spine_binding.py` pattern), each exposing the role objects as types
whose methods are the role table's verbs over a grant handle.
`runtime/app_context.py` keeps `ROLES` (read from the table), `NO_STORE` and
`AppContext`, which makes the grant and attaches the native role objects; every
role class in it is deleted. `runtime/system_api.py` keeps `make_system_api`
and `wants_layout`, the Python cart's binding of a grant, and loses the grant
policy (`_ROLE_FOR`, `granted_roles`, `NEVER_GRANTED`, `FILE_KINDS`,
`ScopedFiles`), which is the role table's and C's.

**What the binding keeps, because the Python side is Python:**

- `theme.colors()` answers a dict, built once per generation from the live
  token table: the shell's draw sites read
  `ws.theme_colors`, and the toolkit takes a dict until sprint 6.
- `surface.canvas()` answers the canvas object the shell registered for the
  glass row, since the Python toolkit draws through its methods.
- storage verbs answer `(value, err)`, `err` being `None`, `NO_STORE` or the
  text: `runtime/app_shell.py`'s CAN'T SAVE HERE versus CAN'T SAVE <why>
  reads it.
- `batch(fn)` stays as Python sugar over `begin` / `end`; `fn` gets the role
  itself.
- the method rule: no `property` anywhere, live values through methods, the
  hoist budget (`tests/test_app_context.py`).

**What stays Python behind the line, because the WMs and `console.py` stay
Python:** the shell-served verbs' servers (§2.4), registered by the Workstation
the way it registers the loop's upcalls; the app objects and their hooks
(`open`, `relayout`, `draw`, `handle_input`, `handle_pointer`, `close`,
`commit`), which the Python WMs call; `register_app` and the bar contract; the
look; the wallpaper renderer; `ui` and the ungated cart globals built on it.

**Where the C runs.** C-served verbs read the spine's tables, so every tier
that hosts apps runs the spine in C. The four consoles do; the CPython host and
the browser build run `runtime/moy_spine.py`, the twin
(`docs/kernel_spine_2026-10.md`). Question 3 asks to move them to the C spine
and delete the twin in step 4.

## 4. The wasm import adapter

### 4.1 What it is

`+native/moy_app/moy_app_wasm.c`: one native per role-table row whose wasm type
it carries, registered with the engine under the role module's name beside
libmoy's `moy` table, each a thunk that reads its arguments out of linear
memory, calls the C verb with the run's grant, and writes the answer back. The
host's WAMR (ctypes), the boards' and the browser's JavaScript engine
(`native/moy_wasm_web`, whose adapters wrap the same C thunks) register the
same table. A shell-served row's thunk is the counted upcall.

The grant is the run's: a compiled app is a `type: "app"` cart with
`"runtime": "wasm"`, launched by the Player like any compiled cart, its grant
made from its permissions at the launch. A module importing a row of an
ungranted role is refused at load, before anything runs, with the import named,
as `moycore_glue.missing_imports` refuses a cart built for a newer console. So
"no name" holds for wasm too: an ungranted role cannot be linked.

`moy_play_vm_free` (`native/moy_play/moy_play.h`) gains one clause: a module
that imports only C-served rows runs with no VM; one that imports a shell row
keeps it, the way a Lua cart naming a VM-bound superset verb does.

**Out of scope:** a compiled app as a registered, windowed Layer, with its
lifecycle as exports and its input as an event queue. A compiled app here is a
cart-shaped app: fixed canvas, `_init`/`_update`/`_draw`, the C chrome's strip.
The Layer-shaped one is the wasm tier's (`docs/wasm_tier_plan_2026-09.md`),
designed with its first app.

### 4.2 Whether moy-spec has to change

Yes, by one rule. moy-spec's SPEC.md §16.2 says a compiled cart imports
functions from module `"moy"` and nothing else, and libmoy enforces it:
`moy_wasm_check` (`native/moycore/libmoy/moy_wasm.c`, vendored, never edited
here) refuses any other module. A role import is therefore refused by every
conforming host, ours included, until the spec admits it.

What the spec needs is the cart-visible rule only: a compiled cart may also
import from a module named by a vendor extension its manifest declares (§10's
`vendor.feature` naming, e.g. `"extensions": ["moybyte.app"]` and imports from
`"moybyte.app"`), and a host that lacks the extension refuses the cart before
it runs, as §3.1 refuses an unknown extension. libmoy's check then takes the
host's list of extension modules. The role table itself is vendor space and
stays moybyte's (`docs/app_api_v1.md`), and how a host binds an import —
WAMR natives, JavaScript adapters, the grant — is PORTING.md territory and
never enters the spec. Question 1.

## 5. The `open()`-after-stop contract

### 5.1 What sprint 4 left

A return start rebuilds the Workstation and the WM, executes the route the
kernel recorded, and the launcher reads its resume record
(`moy_kernel_resume`, `native/moy_kernel/moy_kernel.h`). It covers one route:
the stop is refused whenever the run's route is not HOME
(`docs/kernel_cartpath_2026-10.md` §5.1), because a run started from an app
returns to that app's live object, and after a stop there is none. Today that
refusal bites where it matters: Get Carts' PLAY on a just-installed Doom and
Storybook's PLAY both route back to an app (`nav.play`), so the VM stays up and
a cart that needs the stop meets the fit notice. The Editor's PLAY, Files' RUN
and every app-to-app chain (`returns.back`: Files → the Editor → PLAY) are the
same case.

### 5.2 The contract

1. **A place.** Every surface that can sit on the back-stack under a run — the
   launcher, the six registered apps and the Editor — answers `place()` with
   text naming where the person is, in ids: the mode, the selection, the
   scroll, the open document as `(kind, name)`, the cart as its folder. Never
   an object, never a document's body: what the person made is in the store.
2. **Before every launch, on every tier**, the console calls `commit()` and
   then `place()` on each app on the back-stack and the return chain, and the
   kernel keeps each place in a **place table**: one row per back-stack kind,
   256 bytes each (the resume record's size, which this table replaces; the
   launcher's record is its row), in PSRAM, never written to flash, cleared by
   `go_home`. Writing them whether or not the run stops keeps one path on
   every tier.
3. **`open(place=None)`.** With no place an app opens at its root, as every
   launch does today. With one, it lands where the place says, re-reading what
   it names from the store. A place naming something gone — a cart deleted
   through the webhost while the run held the VM down — lands at the root of
   that part, never raises.
4. **The return start** re-registers the apps, rebuilds the back-stack's
   surfaces from the kernel's back-stack bottom to top, calling each one's
   `open(place)`, and then executes the recorded route. A plain return, with
   the VM up, is unchanged: the live object is still there and `open` is not
   called.
5. **The stop's refusal narrows** to a desk-window route: restoring a desk
   means the windowed WM's geometry, which is Python and not the kernel's, and
   no board with the desk has the stop lever.

What each place holds is the app's own (its `place` and `open` are a few lines
each): Calc its display; Files its mode, kind and selection; Appearance its tab
and selection; Storybook its shelf position, the story's folder and the page;
Get Carts its screen and the cart it showed (the list is fetched again, and an
install is never in flight across a launch); Paint its tool and view, its
document being its prefs already; the Editor the project's folder, the tab and
the code view's line. Notes and the other user apps are runs themselves, and
one run never sits under another.

Lost at a stopped return, as sprint 4 decided (its §9 decision 2): the
achievements counters and a toast mid-deadline. The clipboard is no longer on
that list, because it is a kernel row (§2.4).

## 6. `wallpaper.py` and `appearance.py`

Both stay Python, by the owner's decision on sprint 7: their callers are the
launcher, Settings and the WMs, and their state is caches those draw.

**`runtime/wallpaper.py`** runs a wallpaper cart — today every seed wallpaper is
a Python cart — compiled into its own namespace through `make_api` and drawn
behind the launcher and the desk. That is a Python run beside Python chrome,
and it stays. What crosses: the backdrop's backing copy and its preview sidecar
become C-served `wallpaper` verbs; the choice is a settings row already; the
crash guard already arms through the kernel's record. `select` and `preview`
are shell-served.

**`runtime/appearance.py`** (the look) stays the coordinator: `set_theme`
writes the live token table (C) and then rebuilds `ws.theme_colors` and the
caches keyed on it; `set_skin` installs the skin in the Python toolkit;
`select_wallpaper` drives the Python component; the effective font and chrome
scales are written into the surface row. The theme catalogue
(`runtime/chrome.py`'s `THEMES` and `theme_colors`) stays data the look reads
until theming's loader replaces it; only the live table crosses.

**`ArtworkService`** (`runtime/artwork.py`) is Paint's document model with the
wallpaper and project copy verbs. It stops being a role: its consumers take
`artwork.current()` and the `files` role, Appearance's preview thumbnail comes
from `files` and the image codec, Paint's publish into the backdrop goes
through `wallpaper.save_copy` and `select`, and the service stays in
`runtime/artwork.py` with Paint, constructed by Paint with Paint's grant. The
console's three reaches into it (§1) go through Paint's registered app.

## 7. The crossing table

| file and function | goes to | Python deleted |
|---|---|---|
| `runtime/app_context.py`: `Damage`, `Surface`, `Theme`, `_StoreRole`, `_RawFiles`, `Files`, `_RawCarts`, `Carts`, `Nav`, `Prefs`, `Notify`, `WallpaperRole`, `Installer` | the role table; C verbs in `+native/moy_app/moy_app.c`; shell servers registered by the Workstation (below) | every class; `ROLES`, `NO_STORE` and `AppContext` stay as the binding's shim |
| `runtime/system_api.py`: `_ROLE_FOR`, `granted_roles`, `NEVER_GRANTED`, `FILE_KINDS`, `DEFAULT_FILE_KIND`, `ScopedFiles` | the role table's grant column; the grant row (C) | those; `make_system_api`, `wants_layout`, `slug`, `app_id_for`, `manifest_error`, `is_text_app` stay |
| `runtime/file_widgets.py`: `FileGridView` | the `files` role | its three Workstation reads |
| `runtime/moy_files.py`, `runtime/moy_file_ops.py` | C, the `files` role's server (question 2) | both |
| `runtime/widgets.py`: `Clipboard` | a kernel text row | the class |
| `runtime/artwork.py`: `ArtworkService` | Paint's own model; the `artwork` role is `current()` | the role's object form |
| `runtime/appearance.py`: `set_theme`, the scale setters | write the live token table and the surface row, then recache | nothing |
| `runtime/chrome.py`: `THEMES`, `theme_colors` | stay data the look reads | nothing |
| `runtime/wallpaper.py`: the copy and preview-sidecar verbs | C-served `wallpaper` verbs | the sidecar's Python half (`runtime/moy_image.py`'s) |
| `runtime/console.py`, `runtime/console_spine.py`: `app_context`, the app registration | make the grant; register the shell servers | `AppContext`'s Workstation reach |
| `runtime/console.py`: `resume_record`, `resume_launcher` | the launcher's `place` and `open(place)` | both |
| `device/desktop_spine.py`: the return start | restore the back-stack's surfaces from the place table | the launcher-only branch |
| the six apps, `runtime/editor_app.py`, `runtime/launcher_layer.py` | `place()` and `open(place)`; callers on the new shapes | `self._shell`, the callback sessions, cart dicts from roles |
| `native/moy_kernel/moy_kernel.h`: the resume record | the place table | the single record |
| `runtime/moy_spine.py` | the C spine on the host and the browser (question 3) | the twin |

## 8. The pass order

One step is one commit, and whole. Its iterations are host test runs; then one
gate — `make test`, `tools/preflight.sh --web`, `tools/board.py pass tdeck p4`
— then the commit. Each step corrects in place every claim it makes false,
`docs/app_api_v1.md`'s first. The traces lead the code they pin
(`tests/test_semantic_traces.py`, both object models).

1. **The table and the nets.** `+native/moy_app/roles.json` written from today's
   roles. A `roles` trace family: each verb's observable effect over a real
   store. A coverage ratchet: every row is called through the binding by some
   test, counted per verb by a session fixture. A ratchet holding
   `docs/app_api_v1.md`'s role table to the table's rows. No behaviour moves.
2. **`ctx.shell` closed** (§2.5); `nav.app` deleted; the doc's `size` and
   `notice` corrected.
3. **The place contract** (§5): the place table, `place()` and `open(place)`
   on the launcher, the six apps and the Editor, places written before every
   launch, the return start restoring the chain, the refusal narrowed. It is
   written in ids against today's roles, so the ABI steps do not touch it.
4. **The C spine on every tier that hosts apps** (question 3), the twin deleted.
5. **`+native/moy_app/`, the grant row and the first C-served roles**: the
   module in its three bindings, the errors, `prefs`, `damage`, `clipboard`;
   their Python classes deleted; a parity test across the bindings
   (`tests/test_gfx_binding.py`'s pattern); a Bench row for a role read from
   MicroPython on the T-Deck and the P4 against today's attribute hop.
6. **Surface and theme**, with the shell-served mechanism: the surface row, the
   canvas handle, the live token table and its generation, the servers
   registered by the Workstation, SERVICE counting, `NEEDS_VM`; the four
   `pointer()` call sites on the row. Goldens byte-identical.
7. **The storage roles**: `files` (question 2), sessions in place of callbacks,
   the raw views deleted, `carts` as ids and rows, the `wallpaper` copy verbs
   and sidecar, `artwork` as `current()`, `ArtworkService` moved under Paint.
8. **The shell-served rest and the grant in C**: `nav`, `notify`, `install`,
   `wallpaper.select` and `preview`, `theme.set*`, `glyph`; the user app's
   grant made at the Player's launch from the catalogue row; `system_api.py`
   and `app_context.py` reduced to the shim.
9. **The wasm import adapter** (§4), once the moy-spec rule has landed there and
   libmoy is re-vendored (`make vendor-libmoy`): the
   natives on the host, the boards and the browser, the load-time grant check,
   the VM-free clause, the fixture modules, and an author's header generated
   from the table.
10. **The sprint's end**: `docs/app_api_v1.md` read whole against the code, the
    gate on all five boards, the figures into #224 and #66.

## 9. The gate

Each item fails with its bug present, and the step that lands it shows it red
against that bug before it goes green.

| item | how | the bug it catches |
|---|---|---|
| `docs/app_api_v1.md` rewritten in place | the doc-table ratchet against the role table; `tools/check_docs.py` | a verb added, renamed or reserved without the doc; a doc row with no verb (today's `size` and `notice`) |
| the traces and `tests/test_app_context.py` cover every role | the `roles` trace on both object models; the per-verb coverage fixture; the `NEEDS` both-directions test and the hoist budget, kept | a verb that only its `def` line ever ran; a role reached undeclared; a per-widget role read on the frame path |
| `ctx.shell` is closed | the role-table and `ROLES` assertion; the no-console-in-an-app-module ratchet | the hatch reopened under any name |
| a wasm module reaches a role through an import | a fixture module importing `prefs` and `theme` rows sets, reads and draws a token, on the host's WAMR, in the browser suite and in the T-Deck's and the P4's on-glass suites, reading zero upcalls of every class; the same module one permission short is refused at load naming the import; one importing a shell row keeps the VM | no adapter; a grant not enforced; a C-served verb routed through Python; a VM-free verdict that ignores a shell import |
| every shipped app restores after a stop | per surface (the launcher, the six apps, the Editor): driven off its root, a launch, then on the host the Workstation rebuilt over the same store and kernel tables, and on the S3s a real stop (`vmstop force`) from Storybook's PLAY, Get Carts' PLAY, the Editor's PLAY and Files' RUN; the restored frame equal to the one before the launch, and the back chain intact. Plus a place naming a deleted cart | an app with no place; `open(place)` landing at the root; a chain lost below the top; an edit not committed before the launch; a stale place that raises |
| user apps unchanged | `tests/test_user_apps.py`: one cart opened twice, a permission apart; Notes on every console | a grant widened or narrowed by the move to C |
| the standing meters | the five-board pass; both S3s' uncapped roster; Bench's µs per op, the new role row included; `KERNEL_SRAM` at the launcher with the VM up, internal free and low-water with WiFi and BLE up, within the plan's §6.1 share; every image above its floor, the Zero's above 256 KiB; reboot to first light and to `state`, and the return start's parts | the regressions every sprint guards |

## 10. Risks

- **The frame path.** A role read that was an attribute hop becomes a C call or
  a cached lookup. The hoist budget caps the count per frame, step 5's Bench
  row prices one, and the roster is the control.
- **Two invalidation flags.** `damage.all` sets the kernel's epoch flag and the
  WMs keep `ws._dirty`; both are folded at the one point the frame gate folds
  today, so there is still one mechanism (`docs/surface_model_v1.md` §8). A
  test damages through the role and asserts the repaint.
- **The spec rule is moy-spec's to accept.** Steps 1 to 8 stand without it;
  step 9 waits for it.
- **The place table and the store disagree** after a stopped run that the
  webhost changed underneath. §5.2's rule 3 and its gate row hold it.
- **Theming lands beside this.** Sprint 5 moves no pixels, so a theming phase
  that restyles does not share a step with it; the live token table's
  generation is `look_gen`, so whichever lands second adopts the other's.

## 11. Owner questions

1. **The moy-spec rule.** Open a moy-spec proposal so a compiled cart may also
   import from a module named by a vendor extension its manifest declares
   (§4.2)? **Recommendation: yes.** It is the only cart-visible change the
   sprint needs; the role table stays moybyte's; without it a wasm app cannot
   reach a role on any conforming host, ours included.
2. **The user-files layer crosses in sprint 5.** `runtime/moy_files.py` and
   `runtime/moy_file_ops.py` stayed Python after 1b by decision. Cross them as
   the `files` role's C server, or keep `files` shell-served, so a compiled app
   that saves a document keeps the VM? **Recommendation: cross them.** They are
   store code with no console state, `files` is the grant user apps ask for
   most, and the Zero takes the C with its store.
3. **The C spine on the host and the browser.** C-served roles read the
   spine's tables, which the CPython host and the browser hold in the Python
   twin. Move both to the C spine and delete `runtime/moy_spine.py` in step 4?
   **Recommendation: yes.** Otherwise a role verb has a C server on a board
   and a Python one on the host, which is the host-versus-device split the
   kernel exists to avoid; the host already runs sprint 4's Player in C.

## 12. Claims this sprint falsifies

Rewritten where they stand by the step that falsifies them:

| claim | lives in | step |
|---|---|---|
| the role table, `ctx.shell`, `batch(fn)`'s raw view, `surface.size`, `notify.notice` | `docs/app_api_v1.md`, `runtime/app_context.py`'s header | 1, 2, 7 |
| a stop is refused for any route but HOME; the clipboard is lost at a stopped return | `docs/kernel_cartpath_2026-10.md` §5.1, §5.4, §9 | 3, 5 |
| the resume record is the launcher's alone | `native/moy_kernel/moy_kernel.h` | 3 |
| the CPython host and the browser run the spine's twin | `docs/kernel_spine_2026-10.md` | 4 |
| `ArtworkService` is a role service; `runtime/wallpaper.py` and `runtime/appearance.py` cross; `runtime/chrome.py`'s token tables cross; `runtime/moy_files.py` and `runtime/moy_file_ops.py` stay Python | `docs/native_kernel_2026-09.md` §2.2.1; `docs/kernel_store_2026-10.md` | 6, 7 |
| a compiled cart imports only from `"moy"` | moy-spec's SPEC.md §16.2 and libmoy's check | 9 (moy-spec first) |
