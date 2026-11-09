# Writing an app for Moybyte

The one guide to Moybyte's app and OS surface: read it top to bottom and you
can build an app, a background service or a command on any tier. It is the
authority for what makes a cart an APP: the manifest's `moybyte` fields,
permissions, the `ui` verbs' place, files, prefs, intents, services,
messages, alarms, notifications, achievements and what each tier can call.

What it is not: the GAME verbs (drawing, sprites, maps, sound, `btn`) are
moy-spec's (SPEC.md, the console spec any moy host runs) and Moybyte's page
of them is `docs/moy_cart_api.md`; an app calls them too. Where a truth is
generated or held by code, this guide points at it rather than copying it:

| what | its authority |
|---|---|
| every call an app reaches, on every tier | the call table (§10), written from `native/os_calls.json` |
| the `ui` verbs and their semantics | `docs/ui_api_2026-10.md` |
| Python's `moybyte` verbs, each with its signature | `runtime/system/moybyte.py`'s header |
| a Python app's globals per permission | `runtime/shell/system_api.py` |
| how the OS runs processes, and why | `docs/os_2026-11/processes.md`, `tiers.md`, `between_apps.md` |
| which board runs which tier | `docs/os_2026-11.md` §5 |

Two worked examples run in the host suite, `tests/test_app_examples.py`: a
Python app with a scrolling list and prefs (`tests/fixtures/apps/shopping.moy`)
and a Lua service answering a message (`tests/fixtures/apps/echo_lua.moy`).
`system_carts/moybyte.notes.moy` is the full-size app.

---

## 1. Your first app

An app is a `.moy` folder: `manifest.json` and a main script. No build, no
registration, no firmware.

**Python** (`hello.moy/`):

```json
{
  "format": "moy-1",
  "title": "Hello",
  "id": "local.hello",
  "runtime": "python",
  "main": "main.py",
  "moybyte": {"type": "app", "permissions": ["prefs"]}
}
```

```python
FRUIT = ["Apple", "Banana", "Cherry", "Date", "Elderberry", "Fig", "Grape"]
cur = 0


def _init():
    global cur
    cur = prefs.get("cur") or 0         # saved across runs and reboots


def _draw():
    global cur
    ui.page("Hello")
    new = ui.list("Fruit", FRUIT, cur)  # arrows, Enter, a tap or a drag
    if new != cur:
        cur = new
        prefs.set("cur", cur)
```

**Lua** is the same app with `"runtime": "lua"` and `"main": "main.lua"`;
a list's rows count from 1 and `nil` stands for Python's `None`:

```lua
FRUIT = {"Apple", "Banana", "Cherry", "Date", "Elderberry", "Fig", "Grape"}
cur = 1

function _init()
  cur = prefs.get("cur") or 1
end

function _draw()
  ui.page("Hello")
  local new = ui.list("Fruit", FRUIT, cur)
  if new ~= cur then
    cur = new
    prefs.set("cur", cur)
  end
end
```

**Run it in the simulator:** `python tools/simulate_desktop.py --cart hello.moy`
(`--windowed --size 1024x600 --font-scale 2` for a P4's desk). The simulator
copies the folder into its store, `~/.moybyte/projects`, the first time only:
edit that copy, or delete it, to run a change.

**Run it on a board:** `tools/board.py tdeck push hello.moy` copies the folder
into the board's store, then `tools/board.py tdeck run Hello` opens it (any
board id from `tools/board.py ports`; the `on-glass` skill has the rest).
On the console itself, the home's Make tab makes and edits carts, apps
included.

The rest of this guide is what you add to that.

## 2. The manifest

moy-spec's fields sit at the top level (SPEC.md §3.1 is their authority):
`format`, `title`, `id` (`<author>.<name>`, #162: what your grant, prefs,
consent and crash strikes are keyed by; without it, the folder's name less
`.moy`), `version` (an integer; bump it when a seed cart changes, #47),
`runtime` (`python`, `lua`, `wasm`), `main`, `canvas` (`320x240`, the default,
`160x120` or `128x128`), `fps`, `extensions`, `input` and `palette`
(`docs/moy_cart_api.md` has how a Moybyte cart reads each).

Moybyte's fields go in ONE object, `"moybyte"`; at the top level they read as
absent (`runtime/carts/moy_store_base.py`, `VENDOR_KEY`):

| field | is | more in |
|---|---|---|
| `type` | `game` (the default), `app`, `service` or `command` | §3, §7, §10 |
| `permissions` | what the app may reach: §6's names | §6 |
| `required` | permissions it cannot run without: absent, its launch is refused by name | §6 |
| `animates` | `true`: draw every frame (a clock, a meter); an app draws on change otherwise | §3 |
| `handles` | the MIME types it opens (`"text/markdown"`, `"image/*"`) | §5 |
| `receives` | the message names another app may send it | §8 |
| `achievements` | the badges a game declares | §9 |
| `native` | the chips a C cart carries a module for (`["esp32s3", "esp32p4"]`) | §10 |
| `audio_ring` | a compiled cart's audio ring depth in frames | `docs/os_2026-11/hardware.md` §2 |
| `config`, `edit` | the "Make it mine" values `cfg(key)` reads and the Editor's rows that change them | `runtime/carts/moy_carts.py`, `NEW_TEMPLATE` |
| `assets` | a cart's scenes, in order | `docs/moy_cart_api.md`, Scenes |
| `glass`, `pictures` | a wallpaper drawn per glass, a picture wallpaper | `docs/theme_and_shell_2026-11.md` §3.9 |
| `graduated` | written by the Editor when blocks become code; never by hand | `runtime/editors/blocks.py` |
| `system`, `order`, `targets`, `app` | the seed carts' (`system_carts/`): a system cart, its place, the targets it ships to, a shipped app's declaration | §13 |

## 3. Drawing an app

**The verbs.** An app draws its interface with `ui`, the public UI API
(`docs/ui_api_2026-10.md` §1 lists every verb, §2 their semantics): pages,
labels, buttons, toggles, sliders, lists, tables, tabs, menus, dialogs,
fields, cards, scroll regions and text views, themed by the console. The
same verbs exist in Python, Lua and a compiled app (§10), drawn by one C
engine (`native/moy_ui`), so a page looks the same from every tier. Call
them in `_draw()`; the engine keeps focus, each list's row and each page's
scroll by id, so your script keeps only its values. The game verbs
(`rect`, `spr`, `print`) draw inside a `ui.part` or a `ui.at` rect.

**One scroller, one frame path.** A list, a table, `ui.scroll` and
`ui.text_view` scroll on the engine's one scroll model and get their fast
frames from the engine (shift frames, `docs/surface_model_v1.md` §14.7). An
app never keeps a scroll offset or a frame path of its own (owner,
2026-10-29: the surfaces that did were folded back). A long document is a
`ui.text_view`; a long list is one `ui.list` or `ui.table`.

**The bar is the host's.** The console draws the bar (its back chevron,
your title, the status) over your app and routes its taps; your app draws
none. Lay out inside `content_rect()` (`x, y, w, h`), wherever the bar sits;
`ui.page` already does. `bar_h()` is that rect's `y`.

**Drawn on change.** An app paints a frame only when something changed: its
input, a message, a timer. One that moves on its own says
`"animates": true` beside its `type` and is drawn every frame like a game;
an app that lists `audio` is too, since its sound is pulled in the frame.

**Five names with no permission**, because they are how an app draws: `ui`,
`screen()` (the canvas), `theme()` (the live colour tokens),
`content_rect()` and `bar_h()`. Lua holds `ui` and `content_rect()`, which
answers four values.

**The canvas: fixed by default, responsive by opt-in.** The manifest's
`canvas` is the raster, and the console centres and integer-scales it, as it
does a game's. A cart that defines a top-level `_layout(w, h, fs)` opts into
the whole surface: it draws on the system canvas, is called before `_init`
and again whenever the size or font scale changes, and gets the responsive
bar. A cart that also declares a small `canvas` keeps the fixed raster and
is told so through `_layout`'s arguments. On the desk the cart gets a window
surface of its own, `_layout` and `content_rect()` being the window's
content (`Workstation.app_window_canvas`); from the fullscreen Library, the
whole desktop surface. Responsive is real work: no literal coordinates, a
rect-algebra layout pass, testing at more than one size, and reading
`_layout`'s arguments rather than `W`/`H`, which are bound once at start.
For a calculator or a notepad, fixed is the right answer.

**Text editing: the editor handle (#112).** `open_editor` hands the cart the
console's own text editor over one document. You draw it into a rect and
forward taps; it does the text, the caret, the selection, wrap, undo, the
clipboard, the Markdown rendering and the saving. There is no Save button to
build: it writes on an idle pause and again when the cart exits.

| call | does |
|---|---|
| `open_editor(name)` | an editor over the document `name` in the kind your `files` permission granted. Never a path and never another kind |
| `open_editor()` | the document the console was asked to open (a file tapped in Files, a project's own file on the Editor's Config tab), or `None`. Call it once in `_init` |

The kind that arrives with `open_editor()` may be one you were never granted:
a person chose that file, not your cart. One of them is a whole project,
`project:<folder>.moy`, a `.moy` folder's own files, reached only through the
Config tab's Files so the cart loader re-reads the folder afterwards. You
still just draw it. Everything else is on the handle:

| call | does |
|---|---|
| `ed.draw(x, y, w, h)` | render the document into that rect, this frame; a `scale` for a big screen. On a `ui.page` given a `gen` it draws only the rows that changed |
| `ed.tap(x, y, click)` | forward a pointer press. Answers what it meant: `("link", name)` a tapped `[[note]]`, `("check", row)` a checkbox it ticked, `("caret", None)` a plain place, `None` outside |
| `ed.drag(x, y, down)` | forward what follows the press: it pans, or in SELECT mode grows the selection |
| `ed.nav(dx, dy)` | move the caret by cells (the console feeds it from the T-Deck's trackball and the arrows while the handle has focus) |
| `ed.focus(on)` / `ed.focused()` | take or release the keyboard; while a handle has it, `key()`/`keyp()` read nothing |
| `ed.key(code)` | feed one byte (an on-screen key) |
| `ed.undo()` / `ed.redo()` / `ed.can_undo()` / `ed.can_redo()` | one step, and whether there is one |
| `ed.select_all()` / `ed.copy()` / `ed.cut()` / `ed.paste()` | the console's clipboard with the `clipboard` permission, the document's own otherwise |
| `ed.select_mode(on)` / `ed.selecting()` | SELECT mode: a drag and the trackball extend the selection, the way to mark a range with no shift-arrow |
| `ed.has_selection()` / `ed.can_paste()` | for dimming those buttons |
| `ed.wraps()` | whether the mode soft-wraps (`md`, `text`) or pans sideways (`code`, `json`) |
| `ed.save()` / `ed.save(soft=True)` | write now; the soft one refuses a document its mode cannot parse and badges it |
| `ed.badge()` / `ed.dirty()` | why the last save was refused (`""`: fine); edits no save has taken |
| `ed.can_run()` / `ed.run()` | run a `.py`/`.lua` document on the console's script screen, saved first; your cart opens again on it after, `open_editor()` handing it back |
| `ed.text()` / `ed.set_text(s)` / `ed.caret()` / `ed.scroll(rows, cols)` | the whole text, the caret's `(row, col)`, a pan without moving the caret |
| `ed.name()` / `ed.mode()` / `ed.close()` | the document's name, its mode (`md`, `code`, `json`, `text`), save and let it go |

A note is Markdown, rendered by the handle: `#` headings, `- [ ]` checkboxes
ticked by a tap, `[[note]]` links and `![[a drawing]]`, one of Paint's
pictures inline by name. Prose wraps; code and JSON pan. In Lua the handle
is a table with the same verbs as methods (`ed:draw(...)`), `ed:tap`
answering two values rather than a pair. `system_carts/moybyte.notes.moy`
is built on it.

## 4. Input

**Keyboard first; never keyboard only.** The keyboard is the primary input on
every board, and every surface must also work with a gamepad alone and with
touch alone (#242, #239). The `ui` engine gives you all three: arrows (the
T-Deck's WASD and trackball, a pad's d-pad) move focus between stops,
Enter, A or a tap activates, a drag scrolls. So: every action is a stop
(a button, a list row, a menu item), and a key shortcut only ever doubles
one.

**Back is the host's.** Esc, B, the T-Deck's K and the bar's chevron leave
your app (or step back a page, §11); the ui never reads them. The console's
own key (the T-Deck's Mic) opens the system menu and is never yours.

**Hints name actions, not keys.** `ui.hints({"confirm", "Open", "menu",
"Menu"})` names actions (`confirm`, `back`, `change`, `tabs`, `menu`,
`cancel`, `flip`, `rotate`, `shift`, `clear`, `copy`, `more`, `search`,
`move`, `scroll`) and the engine shows each input's own keys
(`native/moy_ui/moy_ui_parts.c`, `ACT_KEYS`). The T-Deck has Enter, K, WASD
and the trackball, Q E, Mic, F V, R, Del, M and Sym G, and no arrows, Tab,
Esc, Ctrl or brackets: a hint or a shortcut naming those does not exist
there.

**Reading input yourself.** Beside the ui, an app reads the cart API's
`btn`, `btnp`, `key`, `keyp` and `touch` (`docs/moy_cart_api.md`, Input),
`textmode(True)` for typing, and `ui.keys_taken()` to skip a frame's keys
the ui used. A Python process reads raw input records, timestamped, with
`moybyte.on("keys" | "touch" | "pad", fn)` and `moybyte.events(into)`.

## 5. Storage, files and other apps

**Prefs** (`prefs`): `prefs.get(key)`, `prefs.set(key, value)`,
`prefs.clear(key)`, any value JSON carries, each a row of the console's
settings namespaced under your app's `id`, saved as it is written; `None`
(Lua `nil`) for a key never set. Your app and its service share them.

**Files** (`files` / `files:<folder>`): one of the user's shared folders,
the ones the Files app shows: `files:documents` (the default),
`files:pictures`, `files:music`, `files:recordings`; asking for two is a
manifest error, and one the console has none of refuses the launch. The
verbs are `files.list`, `load_text`/`save_text`, `load`/`save`, `rename`,
`delete` (to the trash), `duplicate`, `new_name(title)` (what a typed name
may be stored as) and `badge(name)` (the row's short label, `MD`, `TXT`,
`JSON`, `PY`, `LUA`). Storage answers `(value, err)` and never raises (Lua:
two results), `err` being `None` or why. In Lua the standard `io` and `os`
libraries work in that one folder: `"notes.txt"` is a document there, a path
that climbs, starts at `/` or names a folder is refused with
`Permission denied`, a file is saved whole at its `flush` or `close`, and
`os.remove` sends it to the trash. A game's own state is `pmem`
(`docs/moy_cart_api.md`). `docs/os_2026-11/between_apps.md` §2 is the files
model.

**Python is trusted, not sandboxed.** A Python app runs in the shell's one VM
with the real builtins, so `open()`, `os` and `socket` reach the whole tree
with or without a grant; the manifest is what the install prompt tells the
user (`docs/os_2026-11/processes.md` §2). Lua and wasm have no names past
their grants.

**Opening with another app.** Your manifest says what your app opens
(`"handles": ["text/markdown", "image/*"]`), and a file is named by its path
in the shared folders, as Files shows it:

```python
import moybyte

moybyte.open_with("Documents/todo.md")            # in the app that opens Markdown
moybyte.open_with("https://moybyte.org", True)    # always ask which app
moybyte.share("a line of text", "text/plain")

def moy_open(h):            # an item routed to this app
    text = h.read()         # h.name, h.mime, h.url, h.mode ("r" or "rw")
    h.write(text + "!")     # a file opened with open_with is "rw"
```

Where several apps open the type, the console asks once with its "open with"
menu and keeps the answer; `True` as the second argument asks again. The
handle reaches one item and ends with your run. `moybyte.pick("image/*")`
asks the user for one of their files of that type in the console's picker,
with no `files` permission; the one picked reaches `moy_open(h)` the same way
(`moybyte.pick(mime, "rw")` to write it back), `None` reaching it on a
cancel, and it answers `False` where no file has the type. A Lua service
holds `open_with` and `share`. `docs/os_2026-11/between_apps.md` §1 has the
routing.

**From the background** (a service, or an app the player left) these verbs
and `open_app` ask first: the player gets a notification ("Wants to open
todo.md in Notes") that does it when opened, and the call answers `True` as
though it went. The `front` permission lets them straight through
(`runtime/system/front.py`).

**The clipboard** is the `clipboard` permission: the console's own, at most
4 KiB of text, the global `clipboard` (`put_text(text)`, False for a text
too long; `text()`; `kind()`, "text" or none; `seq()`, which moves at every
put), and what the editor handle's `cut`/`copy`/`paste` use (a compiled app
imports its rows, §10).

## 6. Permissions and consent

| permission | what the app holds |
|---|---|
| `graphics`, `input`, `audio` | nothing extra: the floor every cart has |
| `files`, `files:<folder>` | `files` (§5), `open_editor` (§3), Lua's `io` and `os` in the folder |
| `prefs` | `prefs` (§5) |
| `clipboard` | `clipboard`, the console's clipboard, which the editor handle uses too (§5) |
| `appearance` | `set_theme(name)`, `themes()` |
| `launch` | `open_app(id)`: another app, by its registered id |
| `front` | nothing by name: from the background, `open_app`, `open_with`, `share` and `pick` go straight through (§5) |
| `notifications` | `moybyte.notify` posts (below) |
| `alarms` | `moybyte.at` (§9) |
| `autostart` | a service starts with the console (§7) |
| `network` | `wifi` |
| `multiplayer` | `net`, `on_net` (`docs/moy_cart_api.md`, Multiplayer) |
| `pins` | `pin_write`, `pin_read` (`docs/moy_cart_api.md`, Pins) |
| `http`, `mic`, `lora`, `ble`, `usb`, `midi`, `camera`, `media`, `media:url` | `moybyte.http` and the other device APIs (Python, `runtime/system/devices.py`) |
| `lease:<resource>` | `moybyte.lease(name)`: a board resource (a UART, a pin) as MicroPython's own object (`docs/os_2026-11/hardware.md` §1) |
| `full_access` | what a native (ELF) cart must ask for, and what admits an unsigned compiled module (§10) |
| `editor` | a marker: the cart where the console opens a text file from Files (Notes) |

The full list is `native/os_calls.json`'s `"permissions"`, generated
into `runtime/shell/app_perms.py`'s `TABLE`, which the install prompt, the
launch check and Settings read; a permission's tiers are the tiers that
serve what it hands an app.

**Not asked for, not there.** An ungranted capability has no name: touching
it is a `NameError` (Lua: `nil`), like a typo. A permission the user turned
off in Settings (About → App permissions) has its names bound to `None`
(`nil`), so `if prefs is None:` is how an app copes. A permission naming what
this console lacks (`pins` on a board without them) leaves no name. Only a
`required` one refuses the launch, naming what is missing, at install and
again at launch. Never grantable, whatever a manifest says: the shell's own
roles (`carts`, `install`, `wallpaper`, `artwork`, `damage`, `surface`,
`notify`, `shell`); `tests/test_user_apps.py` pins it.

**Consent.** A cart installed from Get Carts (or `pkg install`) shows what it
asks for first and installs only on the user's yes, all or nothing; an update
that adds a permission asks again. A cart written on the console, a seed and a
cart copied onto the card are never asked about. The record is kept per app
id (`runtime/shell/app_perms.py`, `Consent`); `docs/os_2026-11/processes.md`
§2 has the rules.

**Notifications** (`notifications`): `moybyte.notify(summary, body="",
icon=None, actions=(), urgency=1, replaces_id=0)` answers the notification's
id, or `None` without the permission.

```python
import moybyte

nid = moybyte.notify("Ana: are you coming?", "We start at eight.", "heart",
                     ("default", "Open", "reply", "Reply"), 1)

def moy_action(nid, key):       # the player opened it: "default", or "reply"
    ...
```

The summary is a single line of at most 47 characters and the body at most
200; the icon names one of the console's icons (`None` draws a bubble); the
actions alternate keys and labels, `"default"` meaning a tap on the
notification itself; urgency 0 lists it quietly, 1 shows a banner for 5 s
and 2 for 10 s; `replaces_id` updates a notification you posted before. The
banner draws over whatever is on screen; opening it (the banner, or the task
manager's Notifications tab) brings your app to the front and calls
`moy_action(id, key)`. `docs/os_2026-11/processes.md` §7 has the rules.

**A status item.** `moybyte.status(icon, section, order=0)` puts an item in
the bar for your process, its section drawn in the item's menu on the desk
and in the control centre on a handheld; your process's end takes it out
(`runtime/system/status_items.py`).

## 7. Services: background work and the one wait

A **service** (`"type": "service"`) has no window: it starts, sleeps on its
wake sources and runs a callback when one fires, whatever is on screen. On
every tier a background program waits in the same single kernel wait, on
its own event queue; Python's wakes, Lua's callbacks and a compiled loop's
poll are three faces of it
(`docs/os_2026-11/processes.md` §1, the service model):

| tier | its shape | its example |
|---|---|---|
| Python | the script runs, then `moy_start()`; wakes are `moybyte.every(ms, fn)`, `after(ms, fn)`, `on(name, fn)`, `at(t, fn)`, or asyncio (`asyncio.run(main())`, `runtime/system/aio.py`) on the shell's one loop; `moy_stop()` on a stop | `system_carts/moybyte.achievements.moy` |
| Lua | `moy_start()` and `moy_stop()`; the `moybyte` table a Lua app holds (§10), and `exit` | `tests/fixtures/apps/echo_lua.moy` |
| wasm (WASI) | `_start` loops in `poll_oneoff` on the event descriptor, `MOY_EVQ_FD` (31), its nearest deadline the timeout; `moybyte.os` imports `send`, `on`, `notify`, `award`, `progress` and `log` | `tests/fixtures/wasm/wasi_svc.moy` |
| C (ELF) | `main` loops in `poll` on `MOY_EVQ_FD`; `moy_send`, `moy_on` | `tests/fixtures/elf/elf_svc.moy` |

A compiled service reads one record per event (its sender's pid and app id,
the name, the arguments as JSON, `moy_evq_ev_t` in `sdk/include/moybyte/os.h`)
and returns when the descriptor reads end of file, its stop. A timer is a
deadline, never a per-frame tick: **no background process gets a frame**.
A Python service with no wakes left has exited (0).

**Its storage is its app's.** A service holds the roles its permissions
grant, as a foreground run of the same app does: the same files folder, the
same prefs namespace. Each write lands whole or not at all; ordering two
writers is the app's own business.

**Budgets and strikes.** Every tier's callbacks are charged to one
supervisor (`native/moy_kernel/moy_super.h`): a callback over its slice
(`MOY_BG_CALLBACK_US`, 20 ms) is an overrun, the first a warning and every
later one a strike, and `MOY_BG_STRIKES` (3) strikes suspend the process
until it is resumed. Python aborts a callback still running after
`MOY_BG_ABORT_US` (300 ms) and counts a strike; Lua yields a runaway
callback with a count hook; a watchdog strikes and kills a compiled wake
that stops polling. `runtime/system/scheduler.py`'s header is the ladder.

**Start, stop, restart.** A service runs from its start until it is stopped,
exits, crashes, or strikes out. It starts by `autostart` (the permission: at
the console's first drawn frame, which the user can turn off), from the
terminal (`service start NAME`, `stop`, `restart`, `status`) or the task
manager. A stop runs `moy_stop()` (a compiled service: end of file) and
kills what is still running after `MOY_SERVICE_STOP_MS` (2 s). The restart
policy is the user's, never the manifest's: `never` (the default),
`on-crash`, `always`, set with `service policy NAME POLICY`, with a back-off
that doubles from 1 s and a cap of 3 ends in a row
(`docs/os_2026-11/processes.md` §5).

**Which boards.** Python services run on every console board, in the
shell's VM; on the S3s they are stopped before a VM stop (a game that needs
the whole board) and started again after it. Lua and WASI services run on
the P4s and the host; the S3s refuse them. ELF services run on both chips
(core 1 on the S3s). The browser's VM has no asyncio. The per-board table is
`docs/os_2026-11.md` §5.

## 8. Messages

Every process has an event queue (`native/moy_kernel/moy_evq.h`), and a
message is a name and arguments, any values JSON carries. The same two names
on every tier:

```python
import moybyte

def pong(sender, score):        # sender: the app id the OS stamped
    print(sender, "says", score)

moybyte.on("pong", pong)                    # listen
moybyte.send("local.pinger", "ping", 42)    # every process of that app that listens
```

`send` answers how many processes took it, the sender apart. Who hears whom
is the kernel's rule (`moy_evq.h`, THE RULE; the table is
`docs/os_2026-11/processes.md` §1): a process of the same app always; another
app only on a name the receiver's manifest lists under `receives`
(`"receives": ["ping"]`), so an app chooses what the world may tell it; the
console, as `"system"`, always. The sender is stamped by the kernel, never
taken from the sender's word. The rule holds both ways: an app that waits
for another app's answer lists the answer's name under its own `receives`
too (`"receives": ["pong"]` beside a `send` of `"ping"`). The tier does not matter: a Lua service
hears a Python app's `send`, and the app hears the service's answer. A process that
does not listen on a name does not take it.

A queue pushes back (`moy_evq.h`, BACKPRESSURE): it holds `MOY_EVQ_SENDER`
(16) unread messages of one sender at most, `MOY_EVQ_DEPTH` (64) in all, and
past that `send` answers FULL (`moybyte.FULL`, `-2`) and queues nothing, so
a flood stops at its sender, which sends again later; the system is never
refused. The receiver takes its senders' messages in turn, each sender's in
its order, `MOY_EVQ_BATCH` (4) a step: a frame's start for a Python process,
a run for a Lua service, a `poll` for a WASI or C service. A second app's
message is served behind a flood's oldest, never behind the whole flood.

A compiled program reads its messages: a WASI or C service from its event
descriptor (§7), a compiled app with `moybyte_recv(buf, cap)`, which it calls
in its `_update` as it reads its buttons. Each read takes the next message
(its senders in turn) as one record: `moy_evq_ev_t` (`sdk/include/moybyte/os.h`: the sender's pid,
the arguments' length, its app id, the name), then the arguments as a JSON
array, then a NUL. A compiled app's read answers the record's length, the NUL
counted, 0 when nothing waits, and FULL (`-2`) where the buffer is short,
the message kept for the next read. `tests/fixtures/wasm/app_talk.moy` is a
C app that pings a service and reads the answer.

## 9. Alarms, connectivity and achievements

**Alarms** (`alarms`): `moybyte.at(t, fn)` calls `fn()` once, no
earlier than `t` in Unix seconds on the wall clock (`time.time()`, Lua's
`os.time()`; `moybyte.clock_synced()` says whether NTP has set it), answers
an id `moybyte.cancel` takes, refuses without the permission (Python's
`OSError`, a Lua error), and lights the screen if the console had gone dark,
so a background app can wake its player
(`docs/os_2026-11/between_apps.md` §3). A Lua service's alarm is the
console's, heard as the system's message `"moybyte.at"`
(`native/moycore/moycore_svc.c`, ALARMS), and so is a compiled program's
on every tier (a wasm app's `moybyte_at(t)`, a WASI program's and a C
program's `moy_at`): the call answers the alarm's id, and the program reads
`"moybyte.at"` from `"system"` with that id as its one argument.

**Timers** need no permission. A Python and a Lua program pass a function
(`moybyte.every(ms, fn)`, `moybyte.after(ms, fn)`, §7); a compiled program
asks for the timer (`moybyte_every(ms)` and `moybyte_after(ms)` in a wasm
app, `moy_every` and `moy_after` in a WASI or C program), which answers its
id, and hears each time it goes off as the system's message
`"moybyte.timer"` carrying that id. `cancel` (`moybyte_cancel(id)`,
`moy_cancel(id)`) ends a timer or an alarm of the program's own, answering 1,
or 0 where it has none of that id. The program listens on those two names
for itself at its first wake (`runtime/system/moybyte.py`, `_wake_message`),
two of the eight names a process may listen on (`moy_evq.h`,
`MOY_EVQ_LISTEN`); a tick its queue cannot take is dropped.

```python
import moybyte, time

def ring():
    moybyte.notify("Tea is ready", urgency=2)

moybyte.at(time.time() + 4 * 60, ring)
```

**Connectivity.** The radios are messages from `"system"`:
`moybyte.on("net", fn)` hears WiFi come and go as `fn("system", up, why)`,
and `"ble"` the BLE host, `why` what took it down (0 when it was put down
or came up). A Python service and a Lua service hear them alike
(`docs/os_2026-11/between_apps.md` §4).

**Achievements.** A game declares its badges and tells the console when it
earns one; the console keeps them, shows the unlock banner ("Harpoon Pop:
badge found!") and lists them in Settings → About → Achievements under the
game's name, where they stay after the game is deleted.

```json
"moybyte": {"achievements": [
  {"id": "first_clear", "title": "Bubble Buster", "icon": "star", "hint": "Clear level 1"},
  {"id": "high_tide", "title": "High Tide", "icon": "run", "hint": "Reach level 5", "goal": 5},
  {"id": "untouchable", "title": "Untouchable", "icon": "heart",
   "hint": "Clear a level without losing a life", "secret": true}]}
```

`id`, `title`, `icon` (a name of the console's icon set) and `hint` are
text, the id at most 32 bytes and unique; `goal` is a whole number of 1 or
more; `secret` keeps the badge "Secret" until it is found. The list is read
strictly: one entry that breaks a rule, or a key it does not know, and the
game declares no badges.

```python
import moybyte

moybyte.award("first_clear")          # found
moybyte.progress("high_tide", level)  # the goal's current value: found once it reaches 5
```

`progress` reports the game's own count, which the console stores as told
(the last value, not the best); a best or a total is the game's to keep, in
`pmem`. Both may be called freely, every frame or on every kill: the game's
own process coalesces them (`runtime/system/moybyte.py`, COALESCING) and
allocates nothing doing so. An `award` of a badge it found already sends
nothing; `progress` keeps each badge's newest value and sends it once a
quarter second (`ACH_EVERY_MS`) at most, at once when it reaches the goal the
manifest declares, and at the game's end; an unchanged value sends nothing.
Each answers what its badge's last send answered. Each is a message to the achievements process
(`moybyte.send("moybyte.achievements", "award", id)`), which takes those two
names from every app. Because the kernel stamps the sender, a game unlocks
only its own manifest's badges; the process refuses any other id and logs
the refusal. A Lua
game, app and service hold `moybyte.award` and `moybyte.progress` too, a
compiled app imports `moybyte_award` and `moybyte_progress`, a WASI program
`moybyte.os`'s `award` and `progress`, and a C program calls `moy_award`
and `moy_progress`.
`system_carts/moybyte.harpoon_pop.moy` uses all three kinds;
`docs/os_2026-11/processes.md` §8 is the process.

## 10. Tiers: what each can call

| tier | an app | a service | a command | sealed by |
|---|---|---|---|---|
| Python | yes | yes | yes | nothing: trusted (§5) |
| Lua | yes | yes (not on the S3s) | — | its state's names |
| wasm | yes (`moybyte.app`) | yes, WASI (not on the S3s) | yes, WASI | its imports |
| C (ELF) | — | yes | yes | U-mode and PMP on the P4s; nothing on the S3s |

The calls a program reaches on more than one tier, each by its name there
(`docs/os_2026-11/tiers.md` §1; the `ui` rows are
`docs/ui_api_2026-10.md`'s, every app's with no permission):

<!-- GENERATED by tools/gen_os_table.py from native/os_calls.json. Do not edit. -->
| call | permission | Python | Lua | wasm (`moybyte.app`; a WASI program's `moybyte.os`) | C (ELF) |
|---|---|---|---|---|---|
| `moy_term_size` | none |  |  |  | `moy_term_size` |
| `theme.token` | `appearance` | `ctx.theme.token` | `theme.token` | `theme_token` |  |
| `theme.gen` | `appearance` | `ctx.theme.gen` | `theme.gen` | `theme_gen` |  |
| `theme.light` | `appearance` | `ctx.theme.light` | `theme.light` | `theme_light` |  |
| `theme.name` | `appearance` | `ctx.theme.name` | `theme.name` | `theme_name` |  |
| `theme.variant` | `appearance` | `ctx.theme.variant` | `theme.variant` | `theme_variant` |  |
| `theme.set` | `appearance` | `ctx.theme.set`, `set_theme` | `set_theme` | `theme_set` |  |
| `theme.set_variant` | `appearance` | `ctx.theme.set_variant` |  | `theme_set_variant` |  |
| `theme.edit_file` | `appearance` | `ctx.theme.edit_file` |  | `theme_edit_file` |  |
| `files.readable` | `files` | `ctx.files.readable` |  | `files_readable` | `moy_files_readable` |
| `files.ready` | `files` | `ctx.files.ready`, `files.ready` | `files.ready` | `files_ready` | `moy_files_ready` |
| `files.list` | `files` | `ctx.files.list`, `files.list` | `files.list` | `files_list` | `moy_files_list` |
| `files.count` | `files` | `ctx.files.count` |  | `files_count` | `moy_files_count` |
| `files.load` | `files` | `ctx.files.load`, `files.load`, `files.load_text` | `files.load`, `files.load_text` | `files_load` | `moy_files_load` |
| `files.save` | `files` | `ctx.files.save`, `files.save`, `files.save_text` | `files.save`, `files.save_text` | `files_save` | `moy_files_save` |
| `files.delete` | `files` | `ctx.files.delete`, `files.delete` | `files.delete` | `files_delete` | `moy_files_delete` |
| `files.duplicate` | `files` | `ctx.files.duplicate`, `files.duplicate` | `files.duplicate` |  |  |
| `files.rename` | `files` | `ctx.files.rename`, `files.rename` | `files.rename` |  |  |
| `files.new_name` | `files` | `ctx.files.new_name`, `files.new_name` | `files.new_name` |  |  |
| `nav.open_app` | `launch`, `front` | `ctx.nav.open_app`, `open_app` | `open_app` | `nav_open_app` | `moy_nav_open_app` |
| `prefs.get` | `prefs` | `ctx.prefs.get`, `prefs.get` | `prefs.get` | `prefs_get`, `moybyte.os.prefs_get` | `moy_prefs_get` |
| `prefs.set` | `prefs` | `ctx.prefs.set`, `prefs.set` | `prefs.set` | `prefs_set`, `moybyte.os.prefs_set` | `moy_prefs_set` |
| `prefs.clear` | `prefs` | `ctx.prefs.clear`, `prefs.clear` | `prefs.clear` | `prefs_clear`, `moybyte.os.prefs_clear` | `moy_prefs_clear` |
| `clipboard.put_text` | `clipboard` | `ctx.clipboard.put_text`, `clipboard.put_text` | `clipboard.put_text` | `clipboard_put_text`, `moybyte.os.clipboard_put_text` | `moy_clipboard_put_text` |
| `clipboard.text` | `clipboard` | `ctx.clipboard.text`, `clipboard.text` | `clipboard.text` | `clipboard_text`, `moybyte.os.clipboard_text` | `moy_clipboard_text` |
| `clipboard.kind` | `clipboard` | `ctx.clipboard.kind`, `clipboard.kind` | `clipboard.kind` | `clipboard_kind`, `moybyte.os.clipboard_kind` | `moy_clipboard_kind` |
| `clipboard.seq` | `clipboard` | `ctx.clipboard.seq`, `clipboard.seq` | `clipboard.seq` | `clipboard_seq`, `moybyte.os.clipboard_seq` | `moy_clipboard_seq` |
| `moybyte.notify` | `notifications` | `moybyte.notify` | `moybyte.notify` | `moybyte_notify`, `moybyte.os.notify` | `moy_notify` |
| `moybyte.open_with` | `front` | `moybyte.open_with` | `moybyte.open_with` | `moybyte_open_with`, `moybyte.os.open_with` | `moy_open_with` |
| `moybyte.share` | `front` | `moybyte.share` | `moybyte.share` | `moybyte_share` | `moy_share` |
| `moybyte.send` | none | `moybyte.send` | `moybyte.send` | `moybyte_send`, `moybyte.os.send` | `moy_send` |
| `moybyte.award` | none | `moybyte.award` | `moybyte.award` | `moybyte_award`, `moybyte.os.award` | `moy_award` |
| `moybyte.progress` | none | `moybyte.progress` | `moybyte.progress` | `moybyte_progress`, `moybyte.os.progress` | `moy_progress` |
| `moybyte.on` | none | `moybyte.on` | `moybyte.on` | `moybyte_on`, `moybyte.os.on` | `moy_on` |
| `moybyte.every` | none | `moybyte.every` | `moybyte.every` | `moybyte_every`, `moybyte.os.every` | `moy_every` |
| `moybyte.after` | none | `moybyte.after` | `moybyte.after` | `moybyte_after`, `moybyte.os.after` | `moy_after` |
| `moybyte.cancel` | none | `moybyte.cancel` | `moybyte.cancel` | `moybyte_cancel`, `moybyte.os.cancel` | `moy_cancel` |
| `moybyte.pick` | `front` | `moybyte.pick` | `moybyte.pick` | `moybyte_pick` |  |
| `moybyte.log` | none | `moybyte.log` | `moybyte.log` | `moybyte_log`, `moybyte.os.log` | `moy_log` |
| `moybyte.recv` | none |  |  | `moybyte_recv` |  |
| `moybyte.item_read` | none |  |  | `moybyte_item_read` |  |
| `moybyte.item_write` | none |  |  | `moybyte_item_write` |  |
| `net` | `multiplayer` | `net`, `on_net` | `on_net` |  |  |
| `pins` | `pins` | `pin_write`, `pin_read` | `pin_write`, `pin_read` |  |  |
| `console` | `console` | `print` | `print` |  |  |
| `moybyte.at` | `alarms` | `moybyte.at` | `moybyte.at` | `moybyte_at`, `moybyte.os.at` | `moy_at` |
<!-- END GENERATED -->

Reading the table: the Python column names a shipped app's `ctx` role first
and then a user app's global. The Lua column is a Lua app's and a Lua
service's alike, each name under the permission a Python app's is under
(`tests/test_app_examples.py` drives `tests/fixtures/apps/pinger_lua.moy`).
In a Lua app the console calls (`notify`, `open_with`, `share`, `send`,
`award`, `progress`, `pick`) are the moybyte module's own functions run as
the app's process, their arguments any values JSON carries; a wake
(`on`, `every`, `after`, `at`) calls its function in the app's state at the
start of a frame, as a Python app's, and `moybyte.log(level, text)` writes
the OS log with `moybyte.ERR`, `WARNING` and `INFO` (`runtime/run/lua_ext.py`,
PRELUDE_MOYBYTE). The theme's reads (`theme.token(role)`, `gen`, `light`,
`name`, `variant`) are every Lua app's, as Python's `theme()` is; a Lua app
hears `_minimise`, `_restore`, `moy_action(id, key)` and `moy_open(item)`,
the item a table whose `read()` answers its text and whose `write(text)`
writes it back under an `"rw"` grant. Python-only rows (`status`, `lease`,
the device APIs) are `runtime/system/moybyte.py`'s. The rows with only a
wasm name (`moybyte.recv`, `item_read`, `item_write`) are what a compiled
app reads where a Python or a Lua one is called back: its messages (§8) and
an item handed to it. Standard names come first on every tier: Python's
`open`, `os`, `time`, `socket`, `requests`, `asyncio` and `logging`; POSIX
in C; WASI preview1 in wasm; Lua's `io` and `os`.

**A compiled app (#224)** is `"runtime": "wasm"` with `"extensions":
["moybyte.app"]`. It reaches its roles through imports from module
`"moybyte.app"` (SPEC.md §16.2's vendor-extension rule), each named
`<role>_<verb>` at the types its row's parameters give
(`native/moy_app/moy_app_wasm.h` is the adapter's contract): a number answers
as an `i32`, text or a blob is written into the caller's buffer and answers
its length, and a negative answer is `moy_app.h`'s code negated. The theme's
reads are every app's; an import from a role its permissions do not name is
refused at load, the import named. The `ui` rows are every compiled app's
(`native/moy_ui/moy_ui_wasm.h`), the same C a Python and a Lua app draw
with; `tests/fixtures/wasm/app_ui.moy` imports every row. The console calls
(`moybyte_notify`, `open_with`, `share`, `pick`, `send`, `award`,
`progress`, `on`, `at`, `every`, `after`, `cancel`, `log`) are every
compiled app's to import, each
answered as the app's process under the permission the call names, as a
Python app's is (`notifications`, `alarms`, the front's gate): a refused
`notify` or `at` answers DENIED (`-4`), and `open_with`, `share` and
`pick` answer 0 where Python's answer False. It hears what a Python app is called back for as
messages from `"system"`: `_minimise`, `_restore`, `moy_action` (id, key)
and `moy_open`, which it listens on with `moybyte_on`, and its wakes,
`moybyte.at` and `moybyte.timer` (the wake's id, §9), which it listens on
by asking for one. `moy_open` carries the item's handle, its type, its
mode and its name (a cancelled pick's handle 0), the item read and written by its handle with
`moybyte_item_read(h, buf, cap)` and `moybyte_item_write(h, data)`. Each
console call runs on the VM's task, a hop from a board's session thread. The app is
cart-shaped: fullscreen, its loop the cart's, and on the desk a fixed canvas
in its player window, which the WM scales. `tools/wasm_cart.py` builds the
folder (`src/main.wat`, or C or Rust through wasi-sdk or `wasm32-wasip1`; a
C app as a reactor, its memory the manifest's `memory` pages,
`tests/fixtures/wasm/app_talk.moy`), `--chip` adding a board's signed AOT
module; `native/moy_wasm/README.md` is the tier.

**A command** (`"type": "command"`) is a program the terminal runs by
its id's name (`local.hello` is `hello`): in Python its `main(argv)`, with
`print` and `input` on the session's streams, answering the exit code; in
wasm a WASI program's `_start`; in C an ELF module's `main`
(`runtime/shell/sh_door.py`, `docs/os_2026-11/system.md` §1).

**A WASI program** (a command or a service) is any program wasi-sdk, Rust's
`wasm32-wasip1` or Zig builds: files under its permissions, clocks, args, env,
stdio; it imports `moybyte.os` for its messages (`send`, `on`), its
notifications, badges and log lines (`notify`, `award`, `progress`, `log`),
its wakes (`every`, `after`, `at`, `cancel`, §9), `open_with`, and its
prefs and the clipboard (`prefs_get`, `prefs_set`, `prefs_clear`,
`clipboard_put_text`, `clipboard_text`, `clipboard_kind`,
`clipboard_seq`), and `sdk/include/moybyte/os.h` declares each under its C
name, `moy_notify`, `moy_prefs_get` and the rest. A console call waits for
the console's answer: a service's at the scheduler's next poll, a
foreground command's at its job's next step; a background command's answers
AGAIN after 5 s, its door serving nothing while it runs. The prefs and
clipboard rows are the role table's for a grant its start makes, over the
roles its granted permissions name (`runtime/system/scheduler.py`,
`compiled_run`), as a C program's are: a permission it lacks answers ACCES
and a buffer too small RANGE. A WASI program has no window: `pick`, whose
item is handed to the asking app's window, is not imported, nor is
`share`, whose bytes are not a console call's JSON text; the files rows are
WASI's own files (§5), under its `files` permission.

**A C program** is an ELF module built against the OS function table by
`tools/elf_cart.py` with the SDK (`sdk/README.md`): a command's
`main(argc, argv)`, or a service's loop. Its manifest names its chips
(`"native": ["esp32s3", "esp32p4"]`) and asks for `full_access`;
`native/moy_elf/README.md` is the tier and `tests/fixtures/elf/elf_app.moy`
calls the app rows.

## 11. Lifecycle

`_init()` once, then `_update(dt)` and `_draw()` (a game's three, in
`docs/moy_cart_api.md`), `_layout(w, h, fs)` for a responsive app (§3), and
`quit()` to leave. Several apps run at once: an app the user minimises keeps
its `_update` every frame with no `_draw` (a player, a download), hearing
`_minimise()` going in and `_restore()` coming out; a minimised game is
paused (`docs/os_2026-11/processes.md` §1). Closing the app ends its
process, its sound and its wakes.

**Three strikes (#160).** An exception opens the stop card, whose See why
opens your code at the line. What the Player cannot catch (a hang, an
out-of-memory, a native fault) is caught by the crash guard
(`runtime/shell/crash_guard.py`): it marks the app before its code runs and
clears the mark after three painted frames; three opens that never healed
and the console stops running it, the next tap landing on the stop card. The
cart stays on Make, where editing it is how it gets fixed. It arms for
`type: "app"` carts only.

## 12. Debugging

- **The OS log** (`native/moy_kernel/moy_log.h`): every start, exit, crash
  (a Python traceback, a Lua or wasm panel), strike, refusal and service
  line, under its process id. Read it in Settings → About → System log, with
  the terminal's `log`, or over the dev channel's `log [level] [+seq] [name]`
  word (`tools/board.py tdeck tail 3 --send "log err"`). A Python app writes
  to it with `logging`; a cart's `print` draws on the screen, and a service's
  goes to the log.
- **A crash** shows its stop card's See why; a background app's crash posts
  a notification, and the task manager's row offers its details.
- **Services**: `service status` in the terminal, or on a board
  `tools/board.py p4 sh "service status"`.
- **On a board**: `tools/board.py <board> state`, `shot`, `py "..."` (an
  expression against the live console) and `tail`; the `on-glass` skill.
- **On the host**: the console is the same code; `tests/test_app_examples.py`
  drives an app with keys and a service with messages through
  `tests/ws_helpers.py`'s `build_ws`, the way to pin an app's behaviour.

## 13. Shipped apps: the console's own

Everything above is a USER app: a cart, editable on Make. A SHIPPED app
(Settings, Files, Paint, Calc) is a Layer class
in `runtime/`, frozen into the firmware and registered from a manifest; only
the console's own apps take this path. **Calc** (`runtime/apps/calc_app.py`,
`system_carts/moybyte.calc.moy`) is the reference.

**The two files.** An identity cart, `system_carts/moybyte.<slug>.moy`,
gives the app its tile, title and re-seed, and its manifest declares the
app:

```json
"moybyte": { "app": { "id": "myapp", "entry": "myapp_app:MyAppLayer",
                      "text_mode": false, "order": 80 } }
```

then `python tools/gen_device_carts.py --app-decls` regenerates the frozen
copy. The Layer:

```python
class MyAppLayer:
    id = "myapp"          # process kind: router, back-stack, window key
    domain = "system"     # draws on the responsive system canvas
    TITLE = "MY APP"      # the windowed WM's title strip
    NEEDS = ("surface", "theme", "damage")   # the roles it uses

    def __init__(self, ctx, names): ...   # ctx: its AppContext
    def is_app(self, cart): ...   # claim the identity cart (title, marker permission, slug)
    def open(self): ...           # (re)enter on every launch
    def relayout(self, w, h, fs): ...
    def close(self): ...          # optional: it is leaving the screen
    def draw(self, dt): ...
    def handle_input(self, i): ...
    def handle_pointer(self, px, py, click): ...
```

That is the whole list: the console registers it from the declaration
(`ws.register_app`), and the launcher's claim, the router, the back-stack,
the window and its chip, the resize floor (`min_size`, or the layout's
`MIN_W`/`MIN_H`), text mode and the exitable bar all follow; a new `runtime/`
module reaches every board unless a `board.toml` denies it (#161).
`tests/test_app_registry.py` fails a hand-written app name anywhere else, and
`tests/test_app_api.py` holds the Calc set.

**The AppContext** (`runtime/shell/app_context.py`) carries only the roles
`NEEDS` declares:

<!-- GENERATED by tools/gen_os_table.py from native/os_calls.json: the roles. Do not edit. -->
| role | what it is | granted by | verbs |
|---|---|---|---|
| `ctx.damage` | whole-surface invalidation: repaint next frame, or ask for one more frame from inside `draw()`; the kernel's flags, folded by the frame gate where it folds the console's dirty flag | the shell | `all()`, `again()` |
| `ctx.surface` | the grant's surface row, which the shell writes wherever it changes (on the desk, the window the app is in): the canvas the app draws on, its size, its scales, the window flag, the content rect the host's exitable strip leaves a running app (`content_rect()`, `(x, y, w, h)`, and `bar_h()`, its `y` -- what a user app's ungated `content_rect()` and `bar_h()` read), the pointer in the surface's coordinates (`x`, `y`, `down`, `click`, `visible`). A glyph is `ui`'s (`ui.glyph`), not this role's | the shell | `canvas()`, `size()`, `font_scale()`, `chrome_scale()`, `windowed()`, `bar_h()`, `content_rect()`, `pointer()` |
| `ctx.theme` | the live token table the look writes at every switch, under a generation: the token dict (rebuilt only when the generation moves), one token by its role id, the generation, the light flag, the theme's and variant's names; the verbs that change the look; and the Appearance page's (`docs/history/theming_2026-09.md` §9 phase 6 step 7): a change to the theme (landing in `custom`), what its rows show, CHROME SIZE, EDIT FILE, and NAME… (step 8: `custom` renamed into a theme of its own, and why a name will not do) | `appearance` | `colors()`, `token()`, `gen()`, `light()`, `name()`, `variant()`, `set()`, `set_variant()`, `change()`, `choices()`, `set_chrome_scale()`, `edit_file()`, `name_custom()`, `name_check()` |
| `ctx.files` | the USER-FILES store (#108), C rows over the layer in `native/moy_store/moy_ufiles.h`: named documents (`docs` is plain Markdown — `files/Documents/<name>.md`, the file's body IS the document), the trash, history sidecars, the image, cover and text codecs, provenance stamps; a grant made for a shared folder (a user app's) reaches the kinds in that folder alone | `files` | `readable()`, `ready()`, `begin()`, `end()`, `list()`, `count()`, `load()`, `save()`, `delete()`, `duplicate()`, `rename()`, `new_name()`, `trash_list()`, `restore()`, `empty_trash()`, `history()`, `history_ops()`, `history_commit()`, `encode_image()`, `decode_image()`, `decode_cover()`, `encode_cover()`, `sig()`, `stamp()`, `encode_text()`, `decode_text()`, `provenance()` |
| `ctx.carts` | the CART store: the live cart list, a new cart, a project's undo journal and its images | the shell | `readable()`, `ready()`, `begin()`, `end()`, `all()`, `can_journal()`, `slug()`, `create()`, `journal()`, `rescan()`, `hydrate()`, `images()`, `save_image()`, `encode_image()` |
| `ctx.nav` | where the console goes next: another app, the Editor, a document, a run | `launch` | `open_app()`, `is_system_app()`, `projects()`, `edit()`, `open_image()`, `open_text()`, `edit_file()`, `play()`, `run_script()`, `text_mode()`, `cover()`, `cover_gen()` |
| `ctx.prefs` | per-app settings on `system/settings`' rows, namespaced per app (the grant's namespace), a write saved as it is made | `prefs` | `get()`, `set()`, `clear()` |
| `ctx.notify` | the console's own badges and every game's, as Settings lists them: one of the console's awarded by its id, the badges by game (each found one's day, each goal's last value), and today's date on the bar's clock as the badges record it | the shell | `award()`, `groups()`, `today()` |
| `ctx.wallpaper` | the desktop backdrop, the theme's (`docs/theme_and_shell_2026-11.md` D6): what is shown, the wallpaper carts, a wallpaper's title, a pick (a change to the theme, landing in `custom`; None is NONE, the theme's `desktop` fill), the live preview, My Art's thumbnail, and Paint's picture as Paint and Files copy a drawing into it (its two rows C, over the user-files layer), which a My Art pick copies into the theme | the shell | `current()`, `carts()`, `id_for()`, `title()`, `select()`, `preview()`, `thumbnail()`, `load_copy()`, `save_copy()` |
| `ctx.artwork` | Paint's open picture, Paint's settings rows: which it is (`(kind, name)`, or None), and following it when its file is renamed under it | the shell | `current()`, `follow()` |
| `ctx.clipboard` | the system cut/copy/paste buffer (#132): at most 4 KiB of text, kernel state, so it outlives a VM stop; a longer `put_text` answers False and keeps the old text | `clipboard` | `put_text()`, `text()`, `kind()`, `seq()` |
| `ctx.install` | carts from outside (#124): the network Get Carts fetches through, its radio lease, one store op of an install (`op(fn)`, inside the bus gate), the engine's sizing, this console's chip and compiled-code format; in the browser, the keeper that makes an install durable in OPFS, the page's file picker, and where carts come from on a page a board serves; the Wi-Fi page Get Carts opens when the radio has no network, and whether it is still open; the console's consent records, which the install prompt reads and writes (`runtime/shell/app_perms.py`) | the shell | `hold()`, `release()`, `fit()`, `memory()`, `chip()`, `runtimes()`, `home()`, `can_pick()`, `pick()`, `root()`, `writable()`, `op()`, `rescan()`, `free()`, `find()`, `net()`, `keep()`, `wifi()`, `wifi_open()`, `consent()` |
<!-- END GENERATED -->

Every context holds a grant, a row of `native/moy_app`'s grant table keyed by
the app's id, and every role is that module's role object over it: a C row
reads the kernel's state, a shell row checks the grant and calls the server
the console registers for the role (`runtime/shell/shell_servers.py`).
`notify`'s rows are the achievements process's (`runtime/system/achievements.py`).
A user app's globals (§6) are a filter over the same contexts
(`make_system_api`, `runtime/shell/system_api.py`). Four rules hold:

- **`NEEDS` is a filter.** Reaching an undeclared role raises;
  `tests/test_app_context.py` fails a role named and not declared, and one
  declared and not named.
- **Roles expose methods, never properties**: a descriptor was measured far
  dearer than a plain call on the P4 (`docs/history/ui_refactor_2026-08.md`
  §2.4), and a test asserts there is none.
- **Hoist.** Bind the roles used every frame once in `__init__` and read the
  live values once at the top of `draw()`; `tests/test_roles.py` budgets it.
- **Storage answers `(value, err)` and never raises**; `begin()`/`end()`
  bracket a session for readiness and hold no gate.

No role hands out the console: `tests/test_app_context.py` fails an app
module that names the Workstation.

**What it draws with.** The same `ui` verbs (§3) into the frame its host,
`ctx.ui` (`ui_host.AppHost`), begins, with `UI = True`; its handlers hand
the host the input (`keys`, `point`). The engine under them,
`runtime/toolkit/ui_draw.py`, is the chrome's; a shipped app not yet moved
onto the verbs draws there, pinned by `tests/test_ui_public.py`.

**The bar contract is the router's** (`runtime/shell/console.py`, `_app_bar_route`):
it paints the strip after `draw()` and routes a tap in its band before
`handle_pointer()`, so an app lays out in its content rect
(`layout_base.content_of`) and never draws a bar. The optional hooks:

- `close()`: leaving the screen, by any route. Change-gated and cheap: a
  write costs more than a frame (#154).
- `commit()`: just before the bar's back or the window's X, for an app that
  persists on an idle debounce (#111).
- `back()`: one page back, answering True, or False on the first page, when
  the host leaves the app.
- `idle_step()` while `idle_owed`: work that must not land in a draw (a
  file read, a decode), run once on a frame with nothing to paint.
- `page_title()`: the open page's name, after the back chevron or as the
  window's title.
- `glass` (true while it holds the whole glass: no strip, its own back),
  `leave_asked` (its own way out, taken as the bar's back), `BACK_MINIMISES`
  (a back on the first page minimises on the desk), `cancel_field()` (the
  T-Deck's Mic inside an open field), `DESK_TILE` (False: off the desk's
  column).

**Between apps.** `ctx.nav.open_app(id)` opens another app by its registered
id (a build without it degrades to a status line); the Files router's verbs
are `nav.projects()`, `nav.edit(cart, tab)`, `nav.open_text(name, kind,
mode)` and `nav.open_image(name, kind, cart)`, each "open that, pointed
here". `projects()` is on `nav`, not `ctx.carts`: a list of places to go is
navigation, not the right to author carts.

## Non-goals

- Several instances of one app.
- IPC beyond messages (§8) and "open that, pointed here" (§5).
