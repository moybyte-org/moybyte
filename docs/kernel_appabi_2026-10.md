# The app ABI — sprint 5's design (2026-10)

**What this is.** What sprint 5 of `docs/native_kernel_2026-09.md` (#224)
is to build, set down ahead of the code: the roles an app is handed, reshaped so a
role call carries numbers, handles and byte spans only; where each role's verbs
are served; how thin the Python binding gets; how a compiled module reaches a
role through an import; which launches may stop the VM; the order the steps
land in, and the gate. Status and the sprint's measurements are #224's.
Revised 2026-10-10 after an adversarial review, and again after the owner's
answers to the questions it raised (§12), which the sections below build.

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

**The role table is data**: `native/moy_app/roles.json`, one row per verb: the
role, the verb, its **server**, the permission that grants it (or `never`) and
its wasm type (or none); a C row's signature is its header's. Every row starts
`python`, today's server, and the step that crosses a verb flips its row. It is
to the app ABI what moy-spec's `wasm-imports.json` is to the cart verbs; the
bindings, the import adapter, `docs/app_api_v1.md`'s table, an author's header
and `ROLES` are each held to it by a test (`tests/test_roles.py`).

**Every verb has one of three servers.**

- **C**: the state is the kernel's and resident: the settings rows, the
  glass, a kernel row this sprint adds, the lease table, the engine's sizing,
  and the user-files layer (§12 answer 4). Each verb is its own C
  function in `native/moy_app/` taking the grant handle; there is no by-name
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

**The grant.** Every caller of a role holds a grant: a row of native/moy_app's
own grant table (`moy_htab.h`'s kind 12, GRANT), carrying the app's key, its
class, its role mask, its files kind, its prefs namespace and an OWNER handle it
names (sprint 3's lifetimes, `docs/kernel_survival_2026-10.md`), and from step
5 its surface row. It is not itself an OWNER row because `moy_glass_end_owners()`
ends every OWNER row at a stop, and a shipped app's grant outlives one (step 4,
2026-10-10). The table is the kernel's on a board (`moy_app_kernel`, made once
in PSRAM, so a stop leaves it); elsewhere each console owns one.
**A grant is keyed by the cart's `id`** (#162, moy-spec's SPEC.md §3.1): the
manifest's `<author>.<name>` when it has one, else the cart's folder less
`.moy`, which is what the spec keys a host's records by; a path-less built-in
(`runtime/boot_carts.py`) has neither and is keyed by its title's slug. A
renamed cart keeps its prefs namespace and its strikes. A shipped app's grant
(class SHIPPED) is keyed by its registered id and idempotent: a start
re-registering the app finds the row it had, so a stop leaves the count where
it was. A user app's grant (class RUN) is its run's: the Player ends it when
the run ends, and a fresh start ends any left. The grant policy is C's
whole: the permission-to-role map, the file kinds, `manifest_error`, and
`app_id_for`, the key above (the built-in's slug is ASCII letters and digits,
the C rule; CPython's Unicode `isalpha()` no longer decides it).
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
app its registered id. `persist` is not in the ABI: no app passes it, and
only the shell's own calls (boot restore, cycling) reach the look's
`persist=False` path, past the role.

### 2.4 The roles, reshaped

| role | import shape | server | notes |
|---|---|---|---|
| `damage` | `all()`, `again()` | C | two kernel flags, ALL and AGAIN, folded into `ws._dirty` where the frame gate folds the kernel epoch (`_needs_redraw` takes both); the gate drops an ALL raised by its own draw, as it cleared `ws._dirty`, so a draw's `all` is lost and its `again` is the next frame |
| `surface` | `canvas() -> handle`, `size(&w,&h)`, `font_scale()`, `chrome_scale()`, `windowed()`, `bar_h()`, `pointer(out[5])` | C | the grant's **surface row**, written by the shell (§2.5); the pointer is the kernel's input state in the app's coordinates; `bar_h` joins the role so the ungated cart global and the shipped apps read one value |
| `surface` | `glyph` | Python-only | the toolkit's icon callable, until sprint 6 |
| `theme` | `token(role_id)`, `gen()`, `light()`, `name(buf)`, `variant(buf)`, `skin(buf)` | C | the **live token table**: one theme's flattened tokens and a generation, written by the look at each switch. Role ids are theming's vocabulary (`docs/theming_2026-09.md` §4.1); the generation is theming §6's `look_gen` |
| `theme` | `set(name, variant)`, `set_variant(v)`, `set_skin(name)` | shell | the look coordinates caches, the skin and the wallpaper |
| `files` | list, count, load, save, delete, duplicate, rename, new_name, trash, restore, empty_trash, history, history_ops, history_commit and the codecs over `(kind, name)`; `begin()`, `end()`; `ready()`, `readable()` | C | the user-files layer in `native/moy_store` (§12 answer 4); history ops cross as bytes. A **session** is mount and readiness only (§2.6) |
| `carts` | `ids(out)`, `title(cart, buf)`, `slug`, `can_journal`, `load_deck`, `images`, `encode_image`, `save_deck`, `save_code`, `save_image`, `rescan` (the old `hydrate` and `apply`) | shell | the live list is the shell's; the commit verbs ask the block compiler and Storybook (`runtime/project_store.py`) |
| `nav` | `open_app(id)`, `edit(cart, tab)`, `edit_file(cart, name, mode)`, `open_text(kind, name, mode)`, `open_image(kind, name, cart)`, `play(cart)`, `run_script(kind, name, why_buf)`, `projects(out)`, `is_system_app(cart)`, `text_mode(on)` | shell | every verb lands on a Python surface; `play`'s caller is the grant; `nav.app` is deleted |
| `prefs` | `get(key, buf)`, `set(key, json)`, `clear(key)` | C | the settings rows under the grant's namespace, values as JSON text; `get` answers ABSENT and the Python binding returns the caller's default. On a board the rows are the kernel's (`moy_spine_kernel`'s, viewed by `moy_spine.kernel_settings`), and a write flushes through the rows' own saver (`moy_settings_flush`), which the console's store registers while its VM runs; a write made with no VM stays dirty in the rows, and the next start flushes them before it reads the file |
| `notify` | `achieve(kind, key)` | shell | the `Achievements` object is the console's |
| `wallpaper` | `current(buf)`, `fills(out)`, `carts(out)`, `id_for(cart, buf)`, `title(id, buf)` (in place of `cart_by_id`, whose one caller reads the title), `select(id)`, `preview(...)` | shell | `current` is the look's live `wallpaper_id`, which differs from the settings row after a boot fallback that does not persist (`runtime/appearance.py`); `preview` is a Python callable on the frame path |
| `wallpaper` | `load_copy`, `save_copy` | C | the backdrop's backing file and its preview sidecar (`runtime/moy_image.py`'s, the plan's §2.2.1 row) |
| `artwork` | `current(kind_buf, name_buf)`, `follow(kind, old, new)` | C | Paint's open drawing, its settings rows; `follow` repoints it when Files renames it (`runtime/files_app.py`'s rename). A consumer loads the drawing through `files`; Paint's `NEEDS` gains `files` |
| `clipboard` | `put_text(text)`, `text(buf)`, `kind()`, `seq()` | C | a kernel row in PSRAM holding at most 4 KiB of text (configuration); a longer `put_text` answers BAD (the Python binding's False) and keeps the old text, and the code editor keeps that copy as its own until another lands. It is kernel state, so it outlives a stopped VM; the code editor's lane keeps its calls |
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

**What it becomes.** `native/moy_app/modmoy_app.c` on the boards and in the
browser, and a ctypes binding on the host (`tools/moy_app_binding.py`, the
`tools/moy_spine_binding.py` pattern, over the spine's host library, which
carries native/moy_app so a Settings the spine made is a pointer moy_app
writes; the `runtime` package registers it as `moy_app`): role types whose methods are the table's
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

**Where the C runs.** The C rows read the spine's tables, so the spine is C on
every tier before the grant lands (§12 answer 5): the four consoles run it in
C already; the CPython host takes it through `tools/moy_spine_binding.py` and
the browser build links `native/moy_spine/`, and the twin
(`docs/kernel_spine_2026-10.md`) moves under `tests/` as the differential
oracle the binding is fuzzed against.

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

Step 9's gate prices the hop per call on each S3.

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

**Cart-shaped** (§12 answer 8): a compiled app runs as a compiled cart does,
fullscreen, its loop the cart's. A compiled app as a registered, windowed
Layer, with a window's surface, size and focus events, its lifecycle as
exports and its input as an event queue, is sprint 6's, beside the toolkit's C
primitives.

### 4.2 What moy-spec has to change

moy-spec's SPEC.md §16.2 allows a compiled cart functions from module `"moy"`
and nothing else. libmoy enforces it twice (`native/moycore/libmoy/moy_wasm.c`,
vendored, never edited here): `moy_wasm_check` refuses another module, and
`moy_wasm_check_bytes` types every import against the one table
(`row_named`, `row_type_is`). So no conforming host can link a role import,
ours included. The change, opened as a proposal (§12 answer 1):

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

## 5. Which launches stop the VM

After a stopped run, the start builds a fresh Workstation and WM, follows the
route held in the kernel, and lands the launcher on its resume record
(`native/moy_kernel/moy_kernel.h`; `docs/kernel_cartpath_2026-10.md` §5.4).
That is the one surface a return start rebuilds, and this sprint keeps it so
(§12 answer 2): **no app gains a way to come back after a stop.** There is no
`place()`, no `open(place)` and no place table; the launcher's resume record
stays the kernel's and the launcher's alone.

1. **A launch from the launcher** may stop the VM, by sprint 4's rules: under
   the `need` policy only a VM-free cart that does not fit beside the VM
   (`docs/kernel_cartpath_2026-10.md` §5), and Doom loads after the scripted
   session with the VM up (#224). A compiled app joins the
   VM-free carts when every import it makes is C-served (§4.1).
2. **A launch from any other surface keeps the VM**, and `info` names the
   reason **"place"**: the Editor's PLAY, a run an app started (Storybook's and
   Get Carts' PLAY, Files' RUN), a desk window. The rule is sprint 4's
   (`docs/kernel_cartpath_2026-10.md` §5.1).
3. **What a stopped return keeps** is kernel state (sprint 4's §5.4 list), and
   this sprint adds two rows to it: the clipboard, a C role (§2.4), and the
   grants of the shipped apps, which the return start re-registers onto the
   rows they had, so `kstop N stop` reads the grant count flat. A user app's
   grant is its run's and ends with it. Still lost at a stopped return, by
   sprint 4's §9 decision 2: the achievements counters and a toast
   mid-deadline.

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
| `runtime/app_context.py`: `Damage`, `Surface`, `Theme`, `_StoreRole`, `_RawFiles`, `Files`, `_RawCarts`, `Carts`, `Nav`, `Prefs`, `Notify`, `WallpaperRole`, `Installer` | the role table; C rows in `native/moy_app/moy_app.c`; shell and Python-only rows on the Workstation's registered servers | every class; `ROLES`, `NO_STORE`, `AppContext` stay |
| `runtime/system_api.py`: `_ROLE_FOR`, `granted_roles`, `NEVER_GRANTED`, `FILE_KINDS`, `DEFAULT_FILE_KIND`, `ScopedFiles`, `manifest_error`, `slug`, `app_id_for` | the grant in C | those; `make_system_api`, `wants_layout`, `is_text_app` stay |
| `runtime/file_widgets.py`: `FileGridView` | the `files` role | its Workstation reads |
| `runtime/moy_files.py`, `runtime/moy_file_ops.py` | C in `native/moy_store`, a module the Zero denies (§12 answer 4) | both |
| `runtime/moy_spine.py` | the C spine on the host (ctypes) and in the browser | moves under `tests/` as the oracle (§12 answer 5) |
| `runtime/widgets.py`: `Clipboard` | a kernel row | the class |
| `runtime/artwork.py`: `ArtworkService` | Paint's own model; the role is `current` and `follow` | the role's object form |
| `runtime/appearance.py`: `set_theme` | writes the live token table, then recaches | nothing |
| `runtime/wm_windowed.py`: `_LayoutCtx.install`; the fullscreen relayout | write the grants' surface rows | nothing |
| `runtime/wallpaper.py` | the copy and sidecar rows go to C | the sidecar's Python half (`runtime/moy_image.py`'s) |
| `runtime/console.py`, `runtime/console_spine.py`: `app_context`, the app registration | make the grants; register the servers | `AppContext`'s Workstation reach |
| the six apps, `runtime/editor_app.py`, `runtime/launcher_layer.py` | callers on the new shapes | `self._shell`, callback sessions, cart dicts from roles |
| `native/moy_play/moy_play_stop.c`, `runtime/moy_play.py`: the stop's "route" refusal | the reason "place" (§5) | nothing |
| `native/moy_play/moy_play_rule.c` | the compiled app's clauses (§4.1) | nothing |

## 8. The pass order

One step is one commit, and whole. Its iterations are host test runs; then one
gate — `make test`, `tools/preflight.sh --web`, `tools/board.py pass tdeck p4`
— then the commit. Each step corrects in place every claim it falsifies,
`docs/app_api_v1.md`'s first. Each trace is extended before the
code it pins lands (`tests/test_semantic_traces.py`, on both object models).

1. **The table and the nets.** `native/moy_app/roles.json` from today's
   roles; a `roles` trace family (each verb's effect over a real store); a
   per-verb call counter and a coverage ratchet (every row called by some
   test); per-frame budgets per verb, shell verbs included, for each app's
   drawn frame; the ratchet holding `docs/app_api_v1.md`'s table to the rows.
   No behaviour moves.
2. **`ctx.shell` closed** (§2.7); `nav.app` deleted; the doc's `size` and
   `notice` corrected; `persist` out of the roles' verbs (§2.3); the stop's
   "route" refusal named "place" (§5).
3. **The C spine on the host and the browser** (§3): the CPython host and the
   browser build run `native/moy_spine/`; `runtime/moy_spine.py` moves under
   `tests/` as the differential oracle.
4. **`+native/moy_app/`, the grant and the first C rows**: the module in its
   three bindings (denied on the Zero, which runs no apps); the grant as a
   GRANT row naming an OWNER, with its policy in C, keyed by the cart's `id`
   (§2.2); `prefs`,
   `damage`, `clipboard` (its 4 KiB row); their Python classes deleted; a
   parity test across the bindings (`tests/test_gfx_binding.py`'s pattern);
   the Bench role row.
5. **Surface and theme**: the per-grant surface row written by
   `_LayoutCtx.install` and the relayout, the canvas handle, the live token
   table and its generation; the five `pointer()` sites on the row. Goldens
   byte-identical, the desk's two-window golden included.
6. **The shell servers for Python callers**: `carts`, `nav`, `notify`,
   `wallpaper`, `theme.set*`, `install`, `artwork` as `current`/`follow`,
   `ArtworkService` under Paint with `files` in Paint's `NEEDS`; sessions as
   `begin`/`end` with the gate per op; the raw views deleted; `system_api.py`
   and `app_context.py` reduced to their shims.
7. **The user-files layer in C** (§12 answer 4): `runtime/moy_files.py` and
   `runtime/moy_file_ops.py` crossed into `native/moy_store` and deleted,
   history ops as bytes, the codecs; the Zero denies the module; `files`
   becomes C-served.
8. **The ROLE upcall**: the argument-carrying door, its class in the counted
   totals and `upcall_sites.txt`, `NEEDS_VM`.
9. **The wasm import adapter** (§4), once moy-spec carries the proposal's
   rule and libmoy is re-vendored (`make vendor-libmoy`): the natives on the
   host, the boards and the browser, the hop and the seqlock, the load-time
   grant check, the verdict's new clauses, the fixture modules, an author's
   header from the table.
10. **The sprint's end**: `docs/app_api_v1.md` read whole against the code, the
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
| only the launcher's launches stop the VM (§5) | on the S3s, `vmstop force` from the launcher stops and returns to the resume record; from Storybook's PLAY, Get Carts' PLAY, the Editor's PLAY and Files' RUN the run keeps the VM and `info` reads "place" | a stop from a surface no return start rebuilds |
| the clipboard survives a stop | text put before `kstop 1 stop` reads back after it; a put over 4 KiB answers BAD and keeps the old text | a clipboard in the GC heap; a truncated put |
| grants keyed by `id` | a cart renamed (its title, then its folder with an `id` in the manifest) keeps its prefs; two carts with one title and different `id`s keep apart | a grant keyed by the title's slug |
| the C spine everywhere | the host's ctypes spine and the browser's linked one pass the spine's suite and the differential fuzz against the twin under `tests/` | a host that still runs the twin; a twin that drifts unseen |
| grants do not leak | `kstop N stop` reads the grant count flat | a grant re-made at every start |
| user apps unchanged | `tests/test_user_apps.py`: one cart opened twice, a permission apart; Notes on every console | a grant widened or narrowed by the move to C |
| the standing meters | all five boards passing; the roster uncapped on each S3; Bench, µs per op; `KERNEL_SRAM` with the launcher up, beside internal SRAM free and its low-water (radios on), inside the plan's §6.1 share; each image's headroom over its floor (the Zero: 256 KiB); time from reboot to first light and to `state`; the return start's parts | what every sprint's gate guards against |

## 10. Risks

- **Two invalidation flags.** `damage.all` sets the kernel's epoch flag and the
  WMs keep `ws._dirty`; both fold at the one point the frame gate folds today,
  so there is one mechanism (`docs/surface_model_v1.md` §8). A test damages
  through the role and asserts the repaint.
- **The spec rule is moy-spec's to accept.** Steps 1 to 8 stand without it;
  step 9 waits for it.
- **Theming lands beside this.** Sprint 5 moves no pixels, so a restyling
  theming phase does not share a step with it; the token generation is
  `look_gen`, so whichever lands second adopts the other's.

## 11. Decisions this design takes

- Cart and wallpaper reads are shell-served, not C over a resident catalogue:
  none is per-frame, and building one is store work no role needs.
- Python callers reach shell verbs directly; only a compiled app uses ROLE.
- `glyph`, `install.net` and `install.keep` are Python-only rows.
- No surface but the launcher comes back after a stop (§5); the stop's
  refusal for any other route is "place".
- Step 4 (2026-10-10): the grant is a GRANT row (kind 12) of native/moy_app's
  own table, naming an OWNER, so `moy_glass_end_owners()` at a stop leaves it;
  the KSTOP down line reads the count (`grants=N`). The settings rows move into
  the kernel (`moy_spine_kernel`) and save through their own saver rather than
  a hop to the VM. The damage flags fold at the frame gate's existing fold, one
  mechanism. Each C row counts its calls in C, the counter the trace's coverage
  and the budgets read; a size query (no buffer) that succeeds is not a call.
- Step 5 (2026-10-10): the console writes every surface grant's row from what
  it shows at each change (`Workstation._surface_write`: a relayout, a layout
  context's install with the window's origin, the canvas promote and degrade,
  a responsive app cart's canvas, a new grant), and `tests/test_roles.py`
  fails a read whose row is not the shell's at that moment. The canvas object
  a row answers is the binding's, per grant slot, beside the row's CANVAS
  handle. The pointer row reads the console's `moy_input_ptr_t` live, bound by
  `set_pointer` and let go when the VM ends (`moy_app_vm_stop`). `theme.colors`
  is a C row answering the generation the binding's dict is keyed on. A user
  app's context holds `surface` for the ungated `bar_h()`. The rows served in
  Python call the server the console registers per role (`app.serve`) and are
  counted in the binding (`app.served()`).

## 12. The owner's answers (2026-10-10)

1. **The moy-spec change** (§4.2) is opened as a proposal: the
   vendor-extension import rule, `(module, name, type)` extension tables in
   libmoy's checks, and `moy check` reporting declared extension imports.
2. **No place contract.** Under `need` a stop is rare, so a launch from any
   surface but the launcher keeps the VM (the refusal reason is "place"). The
   reviewed design's place contract (`place()`, `open(place)`, a place table
   in the spine) and its two steps are not built; §5 states the rule.
3. Moot with answer 2.
4. **User files cross to C** (`moy_files.py`, `moy_file_ops.py` into
   `native/moy_store`) as their own step; the Zero denies the module.
5. **The C spine on the host and in the browser**, before the grant step, with
   `runtime/moy_spine.py` kept under `tests/` as the differential oracle.
6. Not a decision: the clipboard is a C role, so it survives a stop as kernel
   state does; its buffer is bounded at 4 KiB of text in PSRAM.
7. **`persist` leaves the app ABI**; only the shell's own calls use it.
8. **A compiled app is cart-shaped in sprint 5**; a windowed compiled app (a
   window's surface, size and focus events) is sprint 6's, alongside the
   toolkit's C primitives.
9. **Grants are keyed by the cart's `id`** (#162, SPEC.md §3.1), not the
   title slug, so a renamed cart keeps its prefs and strikes.

## 13. Claims this sprint falsifies

Each is corrected in its own file, in the step named:

| claim | lives in | step |
|---|---|---|
| the role table, `ctx.shell`, `batch(fn)`'s raw view, `surface.size`, `notify.notice` | `docs/app_api_v1.md`, `runtime/app_context.py`'s header | 1, 2, 6 |
| the stop's refusal for a route but HOME is "route" | `docs/kernel_cartpath_2026-10.md` §5.1, `runtime/moy_play.py`'s `STOP_WHYS` | 2 |
| the CPython host and the browser run the spine's twin | `docs/kernel_spine_2026-10.md` | 3 |
| a user app's prefs and strikes are keyed by its title's slug | `runtime/system_api.py`, `docs/app_api_v1.md` | 4 |
| the clipboard is lost at a stopped return | `docs/kernel_cartpath_2026-10.md` §5.4, §9 | 4 |
| `ArtworkService` is a role service; `runtime/wallpaper.py`, `runtime/appearance.py` and `runtime/chrome.py`'s token tables cross | `docs/native_kernel_2026-09.md` §2.2.1 | 5, 6 |
| the user-files layer stays Python | `docs/kernel_store_2026-10.md` | 7 |
| a VM-free cart is a game with the native permission set | `native/moy_play/moy_play.h`, `docs/kernel_cartpath_2026-10.md` §2 | 9 |
| a compiled cart imports only from `"moy"` | moy-spec's SPEC.md §16.2 and libmoy's checks | 9 (moy-spec first) |
