# The app ABI — sprint 5's design (2026-10)

**What this is.** What sprint 5 of `docs/native_kernel_2026-09.md` (#224)
is to build, set down ahead of the code: the roles an app is handed, reshaped so a
role call carries numbers, handles and byte spans only; where each role's verbs
are served; how thin the Python binding gets; how a compiled module reaches a
role through an import; what an app does so it comes back after the kernel
stopped the VM; the order the steps land in, and the gate. Status and the
sprint's measurements are #224's. Revised 2026-10-10 after an adversarial
review; §12 holds the owner's questions it raised.

**The scope (owner, 2026-10-10; the plan's §10 question 8).** Sprint 5 runs in
full. Sprint 6 runs data-driven, in a doc of its own. Sprint 7 does not run:
the window managers and the rest of `runtime/console.py` stay Python. Nothing
here assumes they cross. The roles are the line between an app and the
console; the console's Python half sits on the far side of it, and a verb whose
state lives there is served from there.

**What it stands on.** Sprint 2's spine: handle tables, the app registry, the
back-stack, the return records, the leases and the settings rows
(`native/moy_spine/moy_route.h`). Sprint 1b's store. Sprint 3's glass, input,
loop, owner lifetimes and the counted upcall door
(`native/moy_kernel/moy_loop.h`). Sprint 4's run: the Player in C, the VM
stopped when a cart needs the memory, the return start that rebuilds the shell
and lands the launcher on its resume record (`docs/kernel_cartpath_2026-10.md`
§5).

## 1. The roles today

`runtime/app_context.py`'s `ROLES` names thirteen. An app declares the ones it
uses in `NEEDS`, `AppContext` attaches only those, and `runtime/system_api.py`
grants a subset to a `type: "app"` cart by its manifest permissions.

| role | verbs (live in the code) | shipped consumers | user-app grant |
|---|---|---|---|
| `damage` | `all`, `again` | Calc, Files, Paint, Appearance, Storybook, Get Carts | never |
| `surface` | `canvas`, `font_scale`, `chrome_scale`, `windowed`, `pointer`, `glyph` | the same six | never; a cart gets `screen()` and `bar_h()` ungated |
| `theme` | `colors`, `light`, `name`, `variant`, `skin`, `set`, `set_variant`, `set_skin` | the same six | reads ungated (`theme()`); `appearance` grants `set_theme`, `themes` |
| `files` | the user-files store: list, count, load, save, delete, duplicate, rename, new_name, the trash, history and its ops, the image, cover and text codecs, `sig`, `stamp`, `provenance`; `readable`, `ready`, `batch` | Files, `ArtworkService` | `files` / `files:<kind>`, one kind (`ScopedFiles`), with `open_editor` |
| `carts` | `all`, `can_journal`, `slug`, `hydrate`, `apply`, `load_deck`, `save_deck`, `save_code`, `images`, `save_image`, `encode_image`; `batch` | Storybook, `ArtworkService` | never |
| `nav` | `app`, `open_app`, `is_system_app`, `projects`, `edit`, `edit_file`, `open_image`, `open_text`, `play`, `run_script`, `text_mode` | Files, Storybook, Get Carts, `ArtworkService` | `launch` grants `open_app` |
| `prefs` | `get`, `set`, `clear`, namespaced | `ArtworkService` (namespace `paint`) | `prefs` |
| `notify` | `achieve` | `ArtworkService` | never |
| `wallpaper` | `current`, `carts`, `fills`, `id_for`, `cart_by_id`, `select`, `preview`, `load_copy`, `save_copy` | Appearance, `ArtworkService` | never |
| `artwork` | the `ArtworkService` object itself | Files, Paint, Appearance, Storybook | never |
| `clipboard` | the `Clipboard` object itself (`runtime/widgets.py`: `put_text`, `text`, `kind`, `seq`) | Storybook; the code editor's lane (`runtime/editors_code.py`) | `clipboard` |
| `install` | `net`, `home`, `keep`, `can_pick`, `pick`, `hold`, `release`, `root`, `writable`, `session`, `rescan`, `free`, `find`, `runtimes`, `fit`, `memory`, `chip` | Get Carts | never |
| `shell` | the Workstation | Files and Paint, to build `FileGridView` | never |

The Workstation reaches `ArtworkService` past any context: `runtime/console.py`
calls `open_named` (the image door), `console_spine.load_system` calls
`sync_wallpaper`, `runtime/player.py` asks `is_paint_app`. Three verbs have no
caller: `nav.app`, and the `surface.size` and `notify.notice` that
`docs/app_api_v1.md` lists and the code never had. No app passes
`persist=False`; the fourteen calls that do are the shell's own (boot restore,
cycling).

**What is object-shaped** (the plan's §2.1): `surface.canvas` and `pointer`
(five call sites: `runtime/app_shell.py`, `runtime/artwork.py`,
`runtime/files_app.py`, two in `runtime/getcarts_app.py`) return objects;
`surface.glyph` is passed as a callable (`glyph_draw=`) into every toolkit chip
in `runtime/app_shell.py`, `runtime/artwork.py` and `runtime/appearance_app.py`;
`theme.colors` returns a dict; `files.batch`, `carts.batch` and
`install.session` take a callback; `nav.play` takes the calling Layer;
`carts.all`, `wallpaper.carts`, `cart_by_id`, `install.find` and
`nav.projects` return cart dicts; `wallpaper.preview` draws into a canvas
object, every Appearance frame; `install.net` and `keep` return service
objects; `artwork` and `clipboard` are objects; `shell` is the whole console.

## 2. The import-shaped design

### 2.1 One table, a server per verb

**The role table is data**: `+native/moy_app/roles.json`, one row per verb: the
role, the verb, its C signature, its wasm type (or none), its **server**, and
the permission that grants it (or `never`). It is to the app ABI what moy-spec's
`wasm-imports.json` is to the cart verbs; the bindings, the import adapter,
`docs/app_api_v1.md`'s table and an author's header are each held to it by a
test, and `ROLES` is read from it.

**Every verb has one of three servers.**

- **C**: the state is the kernel's and resident: the settings rows, the
  glass, a kernel row this sprint adds, the lease table, the engine's sizing,
  and the user-files layer if question 4 says so. Each verb is its own C
  function in `+native/moy_app/` taking the grant handle; there is no by-name
  dispatcher.
- **shell**: the state is the Python console's, which stays Python: the
  routes' surfaces, the look and its caches, the wallpaper cart's namespace,
  the shelf's live cart list (`ws.carts.all`, which holds path-less built-ins
  on a board with no store, `runtime/boot_carts.py`), the achievements, Get
  Carts' transport. No resident C catalogue exists (`native/moy_store/moy_cat.h`
  scans a root per call; `moy_index` interns paths), so every cart and
  wallpaper read is shell-served, and none of them is per-frame.
- **Python-only**: a row with no wasm type, for a verb whose only consumer is
  Python code on paths and objects: `install.net` and `install.keep`, which
  Get Carts' engine (`runtime/cart_index.py`, an app's) drives, and
  `surface.glyph`, the toolkit's icon callable until sprint 6. `install` is
  never granted to a cart, so the role is Python-only in all but its lease and
  engine rows.

**How a Python app reaches a shell verb.** The Python binding binds the verb
straight to the Python server the Workstation registers at its build, and
counts the call in the binding. There is no trip through C and back.

**How a compiled app reaches a shell verb.** Through a new argument-carrying
upcall, ROLE: the verb's row id, an argument buffer and an answer buffer, an
`int32` result whose negatives are the errors of §2.3. It is a door of its own
because the SERVICE door carries one small int, clamps a negative answer to 0
and reports a raise with a print and a collect (`native/moy_kernel/modmoy_loop.c`).
ROLE is counted as its own class in `native/moy_play/upcall_sites.txt`, enters
MicroPython only on the VM's task (§4.1), and answers `MOY_APP_NEEDS_VM` with
no VM.

**The frame path.** A role read on the frame path costs a C call with no Python
shim, or a cached object checked by one integer: `theme.colors()` returns the
dict built at the last token generation and compares only that generation;
`surface.canvas()` returns the canvas object registered for the grant's glass
row. The shell-served verbs a frame calls — `glyph` and `wallpaper.preview` —
are Python callables bound in the binding, as today.

### 2.2 How an app declares what it imports

| runtime | the declaration | where it is checked |
|---|---|---|
| a shipped app (Python Layer) | `NEEDS`, unchanged | `AppContext` makes the grant; `tests/test_app_context.py` holds both directions |
| a user app (`type: "app"` cart, Python) | the manifest's permissions | the grant is made in C at the run's launch; the Python cart binding publishes globals only for granted verbs |
| a compiled app (wasm) | the manifest's permissions and extension, and the module's imports | each import must be a row of a granted role; one from an ungranted role is refused at load, named (§4) |

**The grant.** Every caller of a role holds a grant: a loan on an OWNER row
(sprint 3's lifetimes, `docs/kernel_survival_2026-10.md`), carrying the app's
id, its role mask, its files kind, its prefs namespace and its surface row. A
shipped app's grant is keyed by its id and idempotent: a start re-registering
the app finds the row it had, so a stop leaves the count where it was. A user
app's grant is its run's, and goes with the run. The grant policy is C's
whole: the permission-to-role map, the file kinds, `manifest_error`, the slug
an app's id and prefs namespace are made from (ASCII letters and digits, the
C rule; CPython's Unicode `isalpha()` no longer decides it), and `app_id_for`.
Every role call takes the grant first, so no verb takes the calling app as an
argument.

### 2.3 Handles, errors, call shapes

Errors extend `MOY_HTAB_*` as `moy_route.h`'s and `moy_play.h`'s do:

    enum {
        MOY_APP_OK = 0, MOY_APP_STALE = 1, MOY_APP_FULL = 2, MOY_APP_NOMEM = 3,
        MOY_APP_DENIED = 4,   // the grant does not hold this role
        MOY_APP_NOSTORE = 5,  // no writable store: NO_STORE
        MOY_APP_IO = 6,       // the store failed; moy_app_why() has its text
        MOY_APP_BAD = 7,      // a refused argument: an unknown kind or token, a name too long
        MOY_APP_ABSENT = 8,   // no such cart, app, key or document, or a build without it
        MOY_APP_NEEDS_VM = 9, // a shell-served verb while no VM runs
    };

`DENIED` guards a binding that exposed what it should not: in every runtime an
ungranted verb has no name. Text crosses as a byte span; an answer that is text
or a blob is written into a caller's buffer and returns its length, with a
`*_size` verb beside it for blobs; a list is a count plus a packed buffer of
ids; numbers are `int32`, as moy-spec's binding marshals them. A cart is its
folder (or a built-in's title where it has none), a document `(kind, name)`, an
app its registered id. The `persist` arguments are not in the ABI (question 7).

### 2.4 The roles, reshaped

| role | import shape | server | notes |
|---|---|---|---|
| `damage` | `all()`, `again()` | C | the kernel's frame epoch flag, folded where the frame gate folds `ws._dirty`; `again` is the loop's next-frame request |
| `surface` | `canvas() -> handle`, `size(&w,&h)`, `font_scale()`, `chrome_scale()`, `windowed()`, `bar_h()`, `pointer(out[5])` | C | the grant's **surface row**, written by the shell (§2.5); the pointer is the kernel's input state in the app's coordinates; `bar_h` joins the role so the ungated cart global and the shipped apps read one value |
| `surface` | `glyph` | Python-only | the toolkit's icon callable, until sprint 6 |
| `theme` | `token(role_id)`, `gen()`, `light()`, `name(buf)`, `variant(buf)`, `skin(buf)` | C | the **live token table**: one theme's flattened tokens and a generation, written by the look at each switch. Role ids are theming's vocabulary (`docs/theming_2026-09.md` §4.1); the generation is theming §6's `look_gen` |
| `theme` | `set(name, variant)`, `set_variant(v)`, `set_skin(name)` | shell | the look coordinates caches, the skin and the wallpaper |
| `files` | list, count, load, save, delete, duplicate, rename, new_name, trash, restore, empty_trash, history, history_ops, history_commit and the codecs over `(kind, name)`; `begin()`, `end()`; `ready()`, `readable()` | C, or shell (question 4) | history ops cross as bytes. A **session** is mount and readiness only (§2.6) |
| `carts` | `ids(out)`, `title(cart, buf)`, `slug`, `can_journal`, `load_deck`, `images`, `encode_image`, `save_deck`, `save_code`, `save_image`, `rescan` (the old `hydrate` and `apply`) | shell | the live list is the shell's; the commit verbs ask the block compiler and Storybook (`runtime/project_store.py`) |
| `nav` | `open_app(id)`, `edit(cart, tab)`, `edit_file(cart, name, mode)`, `open_text(kind, name, mode)`, `open_image(kind, name, cart)`, `play(cart)`, `run_script(kind, name, why_buf)`, `projects(out)`, `is_system_app(cart)`, `text_mode(on)` | shell | every verb lands on a Python surface; `play`'s caller is the grant; `nav.app` is deleted |
| `prefs` | `get(key, buf)`, `set(key, json)`, `clear(key)` | C | the settings rows under the grant's namespace, values as JSON text; `get` answers ABSENT and the Python binding returns the caller's default |
| `notify` | `achieve(kind, key)` | shell | the `Achievements` object is the console's |
| `wallpaper` | `current(buf)`, `fills(out)`, `carts(out)`, `id_for(cart, buf)`, `title(id, buf)` (in place of `cart_by_id`, whose one caller reads the title), `select(id)`, `preview(...)` | shell | `current` is the look's live `wallpaper_id`, which differs from the settings row after a boot fallback that does not persist (`runtime/appearance.py`); `preview` is a Python callable on the frame path |
| `wallpaper` | `load_copy`, `save_copy` | C | the backdrop's backing file and its preview sidecar (`runtime/moy_image.py`'s, the plan's §2.2.1 row) |
| `artwork` | `current(kind_buf, name_buf)`, `follow(kind, old, new)` | C | Paint's open drawing, its settings rows; `follow` repoints it when Files renames it (`runtime/files_app.py`'s rename). A consumer loads the drawing through `files`; Paint's `NEEDS` gains `files` |
| `clipboard` | `put_text(text)`, `text(buf)`, `kind()`, `seq()` | C | a kernel row in PSRAM with a fixed cap (question 6); the code editor's lane keeps its calls |
| `install` | `hold()`, `release()` | C | the lease table |
| `install` | `fit(...)`, `memory(&free,&block)`, `chip(buf)`, `runtimes(out)` | C | the engine's sizing |
| `install` | `home(buf)`, `can_pick()`, `pick(name, size, host) -> request`, `poll`, `close`, `root(buf)`, `writable()`, `begin()`, `end()`, `rescan()`, `free(&bytes,&block)`, `find(folder) -> cart` | shell | |
| `install` | `net()`, `keep()` | Python-only | the transport and the keeper are objects `runtime/cart_index.py` drives (`net_text` among them) |
| `shell` | — | — | closed (§2.7) |

### 2.5 The surface row and the desk

On the desk, `runtime/wm_windowed.py` swaps `ws._sys_canvas` and every app's
layout per window, per frame (`_LayoutCtx.install`). So the surface row is per
grant, not per console: `_LayoutCtx.install` writes the installing window's
canvas row, size, scales and windowed flag into the grant of each app it hands
a layout, and the fullscreen relayout writes the same fields once. An app reads
its own row, never the last window's.

### 2.6 Sessions and the bus

On the T-Deck the card shares the panel's SPI host, and the bus gate drains the
flush and holds the next one off while it is held
(`native/moy_store/moy_vol.h`). A session therefore holds no gate: `begin`
mounts and checks readiness, every verb takes and leaves the gate around its
own op, and `end` releases the mount. A session left open ends at the frame's
end and at the run's end. `batch(fn)` and `install.session(fn)` become `begin`
and `end`; the raw views (`_RawFiles`, `_RawCarts`) go, so inside a session a
verb answers as it does outside one.

### 2.7 Closing `ctx.shell`

Two consumers declare it (`SHELL_CONSUMERS` in `tests/test_app_context.py`):
`FilesAppLayer` and `PaintAppLayer`, and the only use its ratchet allows is
handing the Workstation to `FileGridView` (`runtime/file_widgets.py`), which
reads `ws.carts_store`, `ws.carts_root` and `ws._with_sd` to list and load one
kind. `FileGridView` takes the files role. `shell` then leaves `ROLES`,
`NEVER_GRANTED` and the table, and `SHELL_CONSUMERS` and its two tests become
one assertion that no role hands out the console and no app module names it.

## 3. The Python binding

**What it becomes.** `+native/moy_app/modmoy_app.c` on the boards and in the
browser, and a ctypes binding on the host (`+tools/moy_app_binding.py`, the
`tools/moy_spine_binding.py` pattern): role types whose methods are the table's
C rows over a grant handle, with the shell and Python-only rows bound to the
Workstation's registered servers. `runtime/app_context.py` keeps `ROLES`,
`NO_STORE` and `AppContext`; every role class is deleted.
`runtime/system_api.py` keeps `make_system_api` and `wants_layout`, the Python
cart's binding of a grant; the grant policy goes to C (§2.2).

**What the binding keeps, because the Python side is Python:** `theme.colors()`
as a dict (the shell's draw sites read `ws.theme_colors`, and the toolkit takes
a dict until sprint 6); `surface.canvas()` as the canvas object; storage verbs
answering `(value, err)` with `err` `None`, `NO_STORE` or the text, which
`runtime/app_shell.py`'s CAN'T SAVE HERE versus CAN'T SAVE <why> reads;
`batch(fn)` as sugar over `begin`/`end`; no `property`, live values through
methods; a call counter per verb.

**What stays Python behind the line, because the WMs and `console.py` stay
Python:** the shell servers; the app objects and their hooks, which the Python
WMs call; `register_app` and the bar contract; the look; the wallpaper renderer;
`ui` and the ungated cart globals built on it.

**Where the C runs.** The C rows read the spine's tables. The four consoles run
the spine in C; the CPython host and the browser build run
`runtime/moy_spine.py`, the twin (`docs/kernel_spine_2026-10.md`). A C-served
verb on the host needs the C spine there: question 5.

## 4. The wasm import adapter

### 4.1 What it is

`+native/moy_app/moy_app_wasm.c`: one native per row with a wasm type,
registered with the engine under the role module's name beside libmoy's `moy`
table, the same table on the host's WAMR (ctypes), the boards and the browser
(`native/moy_wasm_web`, whose JavaScript adapters wrap the same C thunks). A
thunk reads its arguments out of linear memory, runs the verb with the run's
grant and writes the answer back.

**Which thread runs it.** A compiled cart runs on its session thread, whose
stack is PSRAM (`native/moy_wasm/moy_wasm_footprint.h`), and its file requests
already hop to the VM service task through `moy_wasm_on_vm` inside the bus gate,
because an internal-flash write disables the cache
(`docs/kernel_cartpath_2026-10.md` §2). The role thunks follow the same rule:

- a row that writes (`prefs.set`, `prefs.clear`, the `files` writes,
  `clipboard.put_text`, `wallpaper.save_copy`, `artwork.follow`) and every
  shell row hop through `moy_wasm_on_vm`; a shell row enters MicroPython only
  there, on the VM's task, never from the session thread;
- a read of a PSRAM row (`theme.token`, `gen`, `light`, the surface row,
  `clipboard.text`) runs on the session thread under the row's seqlock, which
  its writer bumps around each write.

Step 11's gate prices the hop per call on each S3.

**The grant and "no name".** A compiled app is a `type: "app"` cart with
`"runtime": "wasm"` that declares the role extension, launched by the Player
like any compiled cart; its grant is made from its permissions at the launch. A
module importing a row of an ungranted role is refused at load with the import
named, as a cart built for a newer console is (`moycore_glue.missing_imports`).

**The VM-free verdict.** `moy_play_vm_free` decides from the catalogue entry
alone (`native/moy_play/moy_play.h`), and two of its rules refuse every app:
rule 2 answers WHY_TYPE for anything but a game, and rule 3 refuses a
permission outside graphics, input, audio and multiplayer
(`native/moy_play/moy_play_rule.c`). The verdict gains an input and two
changes, for a compiled app only:

- the module's import head, read at launch and at census time
  (`moycore_glue.wasm_head`'s parse, in C);
- rule 2 admits `type: "app"` when the runtime is wasm;
- rule 3 maps each permission through the role table: a permission passes when
  every import the module makes from that permission's role is C-served.

A compiled app that imports a shell row keeps the VM, and `why` names the
import. A Lua or Python app is unaffected: neither runs with no VM.

**Out of scope**: a compiled app as a registered, windowed Layer, with its
lifecycle as exports and its input as an event queue (question 8).

### 4.2 What moy-spec has to change

moy-spec's SPEC.md §16.2 allows a compiled cart functions from module `"moy"`
and nothing else. libmoy enforces it twice (`native/moycore/libmoy/moy_wasm.c`,
vendored, never edited here): `moy_wasm_check` refuses another module, and
`moy_wasm_check_bytes` types every import against the one table
(`row_named`, `row_type_is`). So no conforming host can link a role import,
ours included. The change (question 1):

- **The rule.** A compiled cart may also import from a module named by a
  vendor extension its manifest declares (§10's `vendor.feature` names: the
  extension `moybyte.app`, the module `"moybyte.app"`). A host without the
  extension refuses the cart before it runs, as §3.1 refuses an unknown
  extension.
- **libmoy.** Both checks take the host's extension tables, each a list of
  `(module, name, type)` rows, and type an extension import against its table
  as they type a `moy` one; an import from a module no table names is refused
  as now.
- **`moy check`.** It types `moy` imports as now. An import from a module the
  manifest declares as an extension is reported as that extension's, untyped,
  with the warning that the cart runs only on hosts carrying it; an import from
  any other module is refused.

The role table stays vendor space, moybyte's (`docs/app_api_v1.md`). How a host
binds an import — natives, adapters, the grant, the hop — is PORTING.md
territory and never enters the spec.

## 5. The `open()`-after-stop contract

### 5.1 What sprint 4 left

After a stopped run, the start builds a fresh Workstation and WM, follows the
route held in the kernel, and lands the launcher on its resume record
(`native/moy_kernel/moy_kernel.h`). It covers one route: the stop is refused
whenever the run's route is not HOME (`docs/kernel_cartpath_2026-10.md` §5.1),
because a run started from anywhere else returns to a live Python object, and
after a stop there is none. Under the `need` policy a stop happens only for a
VM-free cart whose fit fails with the VM up, and sprint 4's gate has Doom
loading after the scripted session with the VM up (#224), so the case is rare
today. What the contract buys is that a stop is never refused for where the
run came from, which matters as soon as carts or sessions grow past what fits
with the VM up. Whether to build it now is question 2.

### 5.2 The contract

1. **The kinds.** The set of surfaces is the back-stack's kinds, derived from
   them and held by a test: the launcher, the Editor's picker (`picker`) and
   the Editor (`menu`, the common chain is launcher, picker, menu, run), the
   six registered apps, `settings`, `update` and `webconsole`. A kind that does
   not implement `place()` refuses the stop, with reason "place".
2. **A place.** `place()` answers text naming where the person is, in ids: the
   mode, the selection, the scroll, the open document as `(kind, name)`, the
   cart as its folder. Never an object or a document's body.
3. **Before every launch, on every tier**, the console calls `commit()` and then
   `place()` on each kind on the back-stack and the return chain, and stores
   each in the **place table**: a spine table, in `runtime/moy_spine.py` and
   `native/moy_spine/` alike, one row per back-stack kind, 256 bytes each (the
   resume record's size; the launcher's record becomes its row), in PSRAM,
   never written to flash, cleared by `go_home`. A place over 256 bytes is
   refused, not truncated, and counted; a `commit()` or `place()` that fails or
   raises keeps the VM up for this run, with reason "place".
4. **`open(place=None)`.** With no place an app opens at its root, as every
   launch does today. With one, it lands where the place says, re-reading what
   it names. A place naming something gone (a cart deleted through the webhost
   while the VM was down) lands at that part's root (question 3).
5. **The return start** re-registers the apps, rebuilds the back-stack's
   surfaces bottom to top with `open(place)`, and executes the recorded route.
   A return with the VM up is unchanged: the live object is there and `open` is
   not called.
6. **The refusal narrows** to a desk-window route and to item 1's reason, "place". The
   S3 boards deny `runtime/wm_windowed.py`, and no board with the desk has the
   stop lever.

What each place holds is each surface's own; question 3 asks what a restored
app keeps. Notes and the other user apps are runs themselves, and one run never
sits under another. Lost at a stopped return, by sprint 4's §9 decision 2: the
achievements counters and a toast mid-deadline; the clipboard too, unless
question 6 keeps it.

## 6. `wallpaper.py` and `appearance.py`

Both stay Python, by the owner's decision on sprint 7: their callers are the
launcher, Settings and the WMs, and their state is caches those draw.

**`runtime/wallpaper.py`** runs a wallpaper cart (every seed wallpaper is a
Python cart) compiled into its own namespace through `make_api` and drawn
behind the launcher and the desk. That is a Python run beside Python chrome and
stays. Its backing copy and preview sidecar become the C `wallpaper` rows; the
choice is a settings row already; the crash guard arms through the kernel's
record already. Everything else in the role is shell-served.

**`runtime/appearance.py`** (the look) stays the coordinator: `set_theme`
writes the live token table and then rebuilds `ws.theme_colors` and the caches
keyed on it; `set_skin` installs the skin in the Python toolkit;
`select_wallpaper` drives the Python component; the scales go into the surface
rows through the relayout. The theme catalogue (`runtime/chrome.py`'s `THEMES`
and `theme_colors`) stays data the look reads; only the live table crosses.

**`ArtworkService`** (`runtime/artwork.py`) is Paint's document model with the
wallpaper and project copy verbs, and stops being a role: consumers take
`artwork.current()` and `files`; Appearance's thumbnail comes from `files` and
the image codec; Files' rename repoint is `artwork.follow`; Paint's publish
into the backdrop is `wallpaper.save_copy` and `select`. The service stays
with Paint, built with Paint's grant, and the console's three reaches into it
go through Paint's registered app.

## 7. The crossing table

| file and function | goes to | Python deleted |
|---|---|---|
| `runtime/app_context.py`: `Damage`, `Surface`, `Theme`, `_StoreRole`, `_RawFiles`, `Files`, `_RawCarts`, `Carts`, `Nav`, `Prefs`, `Notify`, `WallpaperRole`, `Installer` | the role table; C rows in `+native/moy_app/moy_app.c`; shell and Python-only rows on the Workstation's registered servers | every class; `ROLES`, `NO_STORE`, `AppContext` stay |
| `runtime/system_api.py`: `_ROLE_FOR`, `granted_roles`, `NEVER_GRANTED`, `FILE_KINDS`, `DEFAULT_FILE_KIND`, `ScopedFiles`, `manifest_error`, `slug`, `app_id_for` | the grant in C | those; `make_system_api`, `wants_layout`, `is_text_app` stay |
| `runtime/file_widgets.py`: `FileGridView` | the `files` role | its Workstation reads |
| `runtime/moy_files.py`, `runtime/moy_file_ops.py` | C in `native/moy_store` (question 4) | both, if so |
| `runtime/widgets.py`: `Clipboard` | a kernel row | the class |
| `runtime/artwork.py`: `ArtworkService` | Paint's own model; the role is `current` and `follow` | the role's object form |
| `runtime/appearance.py`: `set_theme` | writes the live token table, then recaches | nothing |
| `runtime/wm_windowed.py`: `_LayoutCtx.install`; the fullscreen relayout | write the grants' surface rows | nothing |
| `runtime/wallpaper.py` | the copy and sidecar rows go to C | the sidecar's Python half (`runtime/moy_image.py`'s) |
| `runtime/console.py`, `runtime/console_spine.py`: `app_context`, the app registration | make the grants; register the servers | `AppContext`'s Workstation reach |
| `runtime/console.py`: `resume_record`, `resume_launcher` | the launcher's `place` and `open(place)` | both |
| `device/desktop_spine.py`: the return start | restore the back-stack from the place table | the launcher-only branch |
| the six apps, `runtime/editor_app.py`, `runtime/launcher_layer.py`, the picker, Settings, the update and web-console screens | `place()` and `open(place)`; callers on the new shapes | `self._shell`, callback sessions, cart dicts from roles |
| `native/moy_kernel/moy_kernel.h`: the resume record | the spine's place table | the record |
| `native/moy_play/moy_play_rule.c` | the compiled app's clauses (§4.1) | nothing |

## 8. The pass order

One step is one commit, and whole. Its iterations are host test runs; then one
gate — `make test`, `tools/preflight.sh --web`, `tools/board.py pass tdeck p4`
— then the commit. Each step corrects in place every claim it falsifies,
`docs/app_api_v1.md`'s first. Each trace is extended before the
code it pins lands (`tests/test_semantic_traces.py`, on both object models).

1. **The table and the nets.** `+native/moy_app/roles.json` from today's
   roles; a `roles` trace family (each verb's effect over a real store); a
   per-verb call counter and a coverage ratchet (every row called by some
   test); per-frame budgets per verb, shell verbs included, for each app's
   drawn frame; the ratchet holding `docs/app_api_v1.md`'s table to the rows.
   No behaviour moves.
2. **`ctx.shell` closed** (§2.7); `nav.app` deleted; the doc's `size` and
   `notice` corrected.
3. **The place table in the spine**, twin and C, with its read-back in
   `kstop N stop`. Only if question 2 says build it, as are 4 and its gate row.
4. **The place contract** (§5): `place()` and `open(place)` on every kind,
   places written before every launch, the return start restoring the chain,
   the refusal narrowed, the failures refusing the stop. Written in ids against
   today's roles, so the ABI steps do not reopen it.
5. **The C spine on the host and the browser**, if question 5 says so; the twin
   kept under `tests/` as the differential oracle.
6. **`+native/moy_app/`, the grant and the first C rows**: the module in its
   three bindings (denied on the Zero, which runs no apps); the grant as an
   OWNER loan with its policy in C; `prefs`, `damage`, `clipboard`; their
   Python classes deleted; a parity test across the bindings
   (`tests/test_gfx_binding.py`'s pattern); the Bench role row.
7. **Surface and theme**: the per-grant surface row written by
   `_LayoutCtx.install` and the relayout, the canvas handle, the live token
   table and its generation; the five `pointer()` sites on the row. Goldens
   byte-identical, the desk's two-window golden included.
8. **The shell servers for Python callers**: `carts`, `nav`, `notify`,
   `wallpaper`, `theme.set*`, `install`, `artwork` as `current`/`follow`,
   `ArtworkService` under Paint with `files` in Paint's `NEEDS`; sessions as
   `begin`/`end` with the gate per op; the raw views deleted; `system_api.py`
   and `app_context.py` reduced to their shims.
9. **The user-files layer in C** (question 4): `runtime/moy_files.py` and
   `runtime/moy_file_ops.py` crossed and deleted, history ops as bytes, the
   codecs. If the answer is no, `files` stays shell-served over them and this
   step does not run.
10. **The ROLE upcall**: the argument-carrying door, its class in the counted
    totals and `upcall_sites.txt`, `NEEDS_VM`.
11. **The wasm import adapter** (§4), once moy-spec has the rule and libmoy is
    re-vendored (`make vendor-libmoy`): the natives on the host, the boards and
    the browser, the hop and the seqlock, the load-time grant check, the
    verdict's new clauses, the fixture modules, an author's header from the
    table.
12. **The sprint's end**: `docs/app_api_v1.md` read whole against the code, the
    gate on all five boards, the figures into #224 and #66.

## 9. The gate

Each item fails with its bug present, and the step that lands it shows it red
against that bug before it goes green.

| item | how | the bug it catches |
|---|---|---|
| `docs/app_api_v1.md` rewritten in place | the doc-table ratchet against the role table; `tools/check_docs.py` | a verb added, renamed or dropped without the doc; a doc row with no verb |
| the traces and `tests/test_app_context.py` cover every role | the `roles` trace on both object models; the per-verb coverage ratchet; the `NEEDS` both-directions test; per-verb budgets per drawn frame from the binding's counters | a verb only its `def` line ran; a role reached undeclared; a per-widget role read, shell verbs included |
| `ctx.shell` is closed | the role-table and `ROLES` assertion; the no-console-in-an-app-module ratchet | the hatch reopened under any name |
| the frame path | Bench's role row on the T-Deck: a role read from MicroPython within today's attribute hop plus 1 µs (configuration); the uncapped roster | a by-name dispatcher, a Python shim on a C row, a cache keyed on more than the generation |
| the desk | a golden with two app windows of different sizes | a console-wide surface row read from the wrong window |
| the bus | a test leaks a session and asserts the panel still flushes, on the host's gate model and in the T-Deck's suite | a session holding the bus gate |
| a wasm module reaches a role through an import | a fixture importing `prefs` and `theme` rows sets, reads and draws a token on the host's WAMR, in the browser suite and in the T-Deck's and the P4's suites, with zero upcalls of every class; the same module one permission short is refused at load naming the import; one importing a shell row keeps the VM with the import as `why`; the hop's µs per call on each S3 recorded in #224 | no adapter; a grant not enforced; a C row routed through Python; a write or a shell row run on the session thread; a verdict that ignores an import |
| every shipped app restores after a stop (question 2) | each kind that can sit under a run (the launcher, the picker, the Editor, Storybook, Get Carts with a canned catalogue and no network, Files): driven to a place, a launch, then on the host the Workstation rebuilt over the same store and spine tables, and on the S3s a real stop (`vmstop force`) from Storybook's PLAY, Get Carts' PLAY, the Editor's PLAY and Files' RUN, the restored frame equal to the frame before the launch and the chain intact. Every other kind: `place` → `open(place)` → `place` round-trips. A place naming a deleted cart; a place at the maximum length of every name; one over it refused and counted; a raising `place()` keeping the VM up | a kind without a place; `open(place)` at the root; a chain lost below the top; an edit not committed; a truncated place; a stale place that raises |
| grants do not leak | `kstop N stop` reads the grant count flat | a grant re-made at every start |
| user apps unchanged | `tests/test_user_apps.py`: one cart opened twice, a permission apart; Notes on every console | a grant widened or narrowed by the move to C |
| the standing meters | all five boards passing; the roster uncapped on each S3; Bench, µs per op; `KERNEL_SRAM` with the launcher up, beside internal SRAM free and its low-water (radios on), inside the plan's §6.1 share; each image's headroom over its floor (the Zero: 256 KiB); time from reboot to first light and to `state`; the return start's parts | what every sprint's gate guards against |

## 10. Risks

- **Two invalidation flags.** `damage.all` sets the kernel's epoch flag and the
  WMs keep `ws._dirty`; both fold at the one point the frame gate folds today,
  so there is one mechanism (`docs/surface_model_v1.md` §8). A test damages
  through the role and asserts the repaint.
- **The spec rule is moy-spec's to accept.** Steps 1 to 10 stand without it;
  step 11 waits for it.
- **Theming lands beside this.** Sprint 5 moves no pixels, so a restyling
  theming phase does not share a step with it; the token generation is
  `look_gen`, so whichever lands second adopts the other's.

## 11. Decisions this design takes

- Cart and wallpaper reads are shell-served, not C over a resident catalogue:
  none is per-frame, and building one is store work no role needs.
- Python callers reach shell verbs directly; only a compiled app uses ROLE.
- `glyph`, `install.net` and `install.keep` are Python-only rows.
- The place table is the spine's, so the host has it without the C spine.

## 12. Owner questions

1. **The moy-spec change** (§4.2): the vendor-extension import rule, libmoy's
   checks taking `(module, name, type)` extension tables, and `moy check`
   reporting declared extension imports untyped with a portability warning.
   Without it no host, ours included, links a role import. **Recommendation:
   open the proposal.**
2. **Whether to build the place contract now, and how much.** Facts: under
   `need` the VM stops only for a VM-free cart that does not fit with the VM
   up; sprint 4's gate has Doom loading after the session with the VM up
   (#224), and §10 question 8 records the memory motive as largely met. What
   it buys is a stop never refused for where the run came from. Cost: a
   spine table, a `place`/`open(place)` pair on eleven kinds, and the
   restore gate. Options: all kinds (steps 3–4); only the run callers
   (launcher, picker, Editor, Storybook, Get Carts, Files), the rest refusing
   the stop with reason "place"; or none, keeping §5.1's refusal.
   **Recommendation: the run callers only.** They are the routes a big cart is
   launched from, and the refusal keeps the rest correct.
3. **What a restored app keeps.** Calc's place holds its entry but loses
   `acc` and `op` unless the place carries them; a place whose target is gone
   lands at that part's root with no word to the person. **Recommendation:**
   places carry all of an app's in-memory state that fits 256 bytes (Calc's
   included), and a gone target shows the app's ordinary status line naming
   what is missing.
4. **The user-files layer in C** (step 9). Facts: `runtime/moy_files.py` and
   `runtime/moy_file_ops.py` are about 880 lines, stayed Python after 1b by
   decision, carry the history ops (which become bytes) and the codecs; the
   Zero takes them as store code. In C they let a compiled app save a document
   with no VM; kept Python, `files` is shell-served and such an app keeps the
   VM. **Recommendation: cross them**, in `native/moy_store` (which every image
   links), with step 9 skipped if not.
5. **The C spine on the host and the browser** (step 5). Facts: it retires
   the twin, which is the differential oracle of `tests/test_moy_spine_twins.py`
   and `tests/test_ledger_twin.py`, and touches 18 modules that import it; it
   flips the web build's `MOY_SPINE_IMPL` default (`firmware/web_runner/build.sh`),
   a rebuild at the pinned emscripten and a re-bake into every image. Without
   it a C row has a C server on a board and none on the host.
   **Recommendation: yes, before step 6**, keeping the twin under `tests/` as
   the oracle.
6. **The clipboard across a stop.** As a kernel row it survives a stop, which
   sprint 4's §9 decision 2 accepted losing; its cap is configuration (text
   only, as `Clipboard` holds today). **Recommendation: yes, with a 4 KiB cap**
   in PSRAM; longer text is refused with the editor's existing status.
7. **Dropping `persist` from the ABI.** No app passes `persist=False`; the
   shell's own calls do and keep it. **Recommendation: drop it**; an app's
   change always persists.
8. **A compiled app is cart-shaped.** Fixed canvas, `_init`/`_update`/`_draw`,
   the C strip; a windowed, Layer-shaped compiled app waits for the wasm
   tier's first app. **Recommendation: agree.**
9. **Grants keyed by the title slug.** A user app's id and prefs namespace are
   its title's slug (`app_id_for`), so renaming the cart loses its prefs and
   crash strikes. Options: keep, or key by a manifest id (#162's namespaced
   ids). **Recommendation: keep the slug for sprint 5** and move to #162's id
   when it lands, as one store change.

## 13. Claims this sprint falsifies

Each is corrected in its own file, in the step named:

| claim | lives in | step |
|---|---|---|
| the role table, `ctx.shell`, `batch(fn)`'s raw view, `surface.size`, `notify.notice` | `docs/app_api_v1.md`, `runtime/app_context.py`'s header | 1, 2, 8 |
| the spine's tables have no place table; the resume record is the launcher's alone | `docs/kernel_spine_2026-10.md`, `native/moy_kernel/moy_kernel.h` | 3 |
| a stop is refused for any route but HOME | `docs/kernel_cartpath_2026-10.md` §5.1 | 4 |
| the CPython host and the browser run the spine's twin | `docs/kernel_spine_2026-10.md` | 5 |
| the clipboard is lost at a stopped return | `docs/kernel_cartpath_2026-10.md` §5.4, §9 | 6 |
| `ArtworkService` is a role service; `runtime/wallpaper.py`, `runtime/appearance.py` and `runtime/chrome.py`'s token tables cross | `docs/native_kernel_2026-09.md` §2.2.1 | 7, 8 |
| the user-files layer stays Python | `docs/kernel_store_2026-10.md` | 9 |
| a VM-free cart is a game with the native permission set | `native/moy_play/moy_play.h`, `docs/kernel_cartpath_2026-10.md` §2 | 11 |
| a compiled cart imports only from `"moy"` | moy-spec's SPEC.md §16.2 and libmoy's checks | 11 (moy-spec first) |
