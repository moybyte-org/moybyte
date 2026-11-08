# The OS (2026-11): the OS between apps

Phase 6 of the order of work. Read `docs/os_2026-11.md` first.

---

## 1. Intents

`nav.open_app` and the Files router are the shell's own "open that, pointed
here" seam (`docs/app_api_v1.md`); intents are the one an app reaches
(`runtime/system/intents.py`). An app's manifest declares what it
**handles** (`"moybyte": {"handles": ["text/markdown", "image/*",
"x-scheme-handler/https"]}`; `type/*` takes every subtype), which the shelf's
catalogue entry carries. An app calls `moybyte.open_with(item)` (a URL, a user
file named by its path in the shared folders, `"Documents/todo.md"` (§2), or
a grant it was handed) or
`moybyte.share(data, mime)`; the OS routes to the one app that matches, or,
when several do, to **the last app chosen** for that type (owner,
2026-11-03), kept in `system/settings`' `open_with` row. With none chosen yet,
the **"open with" menu** asks: OS chrome, the console's one question
(`DeleteConfirm.choose`) holding a `ui.menu` of the apps that open it, the
last chosen first and focused, then Cancel, under keys, a pad or touch alone.
There is no per-type "always": `open_with(item, choose=True)` opens the menu
whatever was chosen, and the row picked becomes the choice. The verbs answer
True where the item went to an app or the menu is up, False where no app
opens it.

**The receiving app gets the item as a grant**, never a broad permission: a
handle with its `name`, `mime`, `url` and `mode`, `read()` and, where it is
`"rw"` (a file opened with `open_with`), `write(data)`, ending with its run.
A user app is launched and handed it by its `moy_open(h)`, as a notification
reaches its `moy_action`. Paint and Notes are shell code and open the one
file through their own doors (`open_image`, `open_text_cart`); data shared
to them is kept as a new item in their kind first (`Shared`, named for its
type). A Lua service holds both verbs as console calls (strings only).

```
moybyte.open_with(item, choose=False) / share(data, mime) / pick(mime, mode) -> h   intents and the picker
```

## 2. The files model

One visible hierarchy under one root (`moybyte/` on the card, the same shape
on flash without one), replacing a layout the owner called "detached from
the real picture" (2026-11-03):

| folder | holds | who reaches it |
|---|---|---|
| `carts/` | installed carts and the kid's projects | the shell (`carts`, `install`), the Editor; an app only through `nav` |
| `apps/<id>/` | **app-private storage**: a compiled cart's written files (`runtime/carts/cart_files.py`, `native/moy_store/moy_files.h`; `<id>` its folder less `.moy`), and an app's prefs to come (they are `system/settings` rows today) | its own app, no permission |
| `files/` | the **shared user folders**, named on disk as the OS shows them: `Documents/` (notes; stories in `Documents/Stories/`), `Pictures/` (drawings; sprite sheets in `Pictures/Sprites/`), `Music/`, `Recordings/`, `Downloads/`, and the layer's own `.Trash/` and `.history/` | an app with `files:<folder>` (`files:documents`, `files:pictures`, `files:music`, `files:recordings`; one folder a grant), or one file at a time through the picker |
| `system/` | the console's documents as plain-text files (`system.md` §5) | the shell |

**The shared folders carry their real names on disk (owner, 2026-11-03)**,
so USB and SFTP show what the OS shows. The user-files layer
(`native/moy_store/moy_ufiles.h`) keeps its kinds as the names every verb
takes and files each in its folder (`moy_store_base.FILE_KIND_DIRS`, held to
the C by `tests/test_userfiles_parity.py`); an app, an intent and the Files
app name a file by its path there (`intents.item_of`, `split`). The rename
shipped as a strict reader (no migration): a legacy `files/<kind>/` folder
reads as absent until wiped by hand (dev boards hold nothing of value).
**Types are MIME types** (owner, 2026-11-03) wherever an app sees them:
`handles`, the picker, intents, share and the clipboard (`text/markdown`,
`image/x-moyimg`, `audio/x-moysong` for our formats, registered in one
table, `intents.TYPES`), routed to default apps by the `handles` table of §1.

**`files:<folder>`** names a shared folder in lower case
(`native/moy_app/moy_app.c`'s policy, `app_perms`' prompt line "Read and save
your pictures"; bare `files` is `files:documents`). The grant reaches every
kind in its folder (`files:pictures`: drawings and sprite sheets) and no
other; a Python app's `files` and a Lua run's io work in the folder's own
kind (documents' notes, pictures' drawings). Downloads has no kind of the
layer's: a compiled command reaches it, as it reaches any folder its
`files` permissions name, by path (`sh_door.wasi_roots`).

**The picker** is OS chrome: an app with no `files:*` permission calls
`moybyte.pick(mime, mode)` and the console's one question
(`DeleteConfirm.choose`) lists the user's files of that type, a row a file
(its name as Files shows it, its kind's icon), under "Choose a picture"
(or a document, a song, a file) and the asking app's title; its body
scrolls the focused row into view, under keys, a pad or touch alone. The
file picked reaches the asking process's `moy_open(h)` as the §1 grant in
`mode` (`"r"`, or `"rw"` to write it back), held for its run; a cancel
hands it None (#231's shape). `pick` answers False where no file has the
type. It is a Python app's verb: a Lua service and a compiled cart hold no
import of it, and picking a folder is not offered.

**The Files app** (`runtime/apps/files_app.py`) is the tree's browser: its
places are Projects, the shared folders by their names, Themes and the
Trash, and a file's verbs include **Open with…**, which asks the "open
with" menu of §1 always (`open_with(item, True)`), the app picked becoming
the choice for the type.

**Sync, the web and USB see the same tree**: the sync RPC's files root
takes and walks the shared folders alone (`moy_sync.file_folders`, the one
allowlist that keeps `.Trash/` and `.history/` home), and `apps/` is the
fourth root (`moy_sync.APPS_ROOT`): every file of it crosses as bytes, a
name holding `~` (a write staged on its way to its key) stays home, and a
whole delete is one app's folder. **One writer per mode**: a board-served
page pulls `GET /apps.json` and pushes what a cart wrote in the tab as `apps`
batches, the board being of record; a site page leaves its OPFS `apps/` to
the keeper alone (`moy_store.commitWritten`, a write whole or not at all,
moy-spec SPEC.md §16.12), and no sweep seeds or watches that root there. A
cart's session reads its folder once, when it opens (`cart_files.py`), so a
batch that lands while the same cart plays on the board's glass is what
that session finds the next time it opens. A board-served page edits the
board's tree, a site page its OPFS copy; SFTP mounts it from a computer
(`system.md` §4). **USB mass storage** (owner, 2026-11-03) exposes the card's
`moybyte/` folder while the store is held closed, never both at once: on the
P4s, and on the T-Deck as a mode in Settings that switches the S3's one USB
port from Serial/JTAG to mass storage, so the dev channel is lost while it
is on; turning it off switches back and remounts the card, and the BOOT
button still reaches the ROM loader. **No migration** (CLAUDE.md
"Settled"): `written/<cart>/` → `apps/<id>/` shipped as a strict reader, a
legacy `written/` folder reading as absent until wiped by hand (owner,
2026-11-03); a compiled cart's saves on a dev board are disposable.

## 3. Clock and alarms

Wall time from NTP over the kernel's WiFi station,
a time zone as a **POSIX TZ string** in `system/settings`, `time.time()`
the standard wall clock (an int on MicroPython) and `moybyte.clock_synced()`
saying whether NTP has answered; alarms `moybyte.at(t, fn)` as a wake source
(`processes.md` §1) that wakes a background app, and the board from the idle
ladder's sleep (`native/moy_kernel/moy_idle.h`, the RTC timer; #130),
`processes.md` §5's contract. The standard faces (owner, 2026-11-03): a
**crontab** file in `system/` and `at <time> <cmd>` in the shell, both
scheduling commands over the same alarms, a line's command spawned as a
process whose output goes to the log. The `alarms` permission gates
`moybyte.at` waking an app or the board, and nothing else.

As built (phase 6 step 3): NTP, the TZ string (Settings' Time zone) and
`moybyte.clock_synced()` are `runtime/system/wallclock.py` over
`native/moy_net/moy_wifi.c`'s clock. An alarm is a wake of the registry's
on the wall clock (`runtime/system/moybyte.py`): `moybyte.at(t, fn)`, Unix
seconds, refused with EPERM without `alarms` (a Python permission: a Lua
service's verbs are `tiers.md` §2's), runs `fn` no earlier than `t` and
lights the screen from the idle ladder as it goes off. The board's sleep
today is the ladder's BLANK rung, the system menu's Sleep, through which the
board keeps running; the SLEEP rung's RTC timer is #130's. The crontab is
`system/crontab`, cron's five fields and a command, local in the console's
zone and read strictly at the first frame and by `cron reload`; `at TIME
COMMAND` keeps its jobs in RAM. Both are `runtime/system/cron.py`, whose
alarms are the shell's and light nothing, and a job runs its line in a
shell session of its own with no face (`native/moy_sh/moy_sh_job.h`) as a
process named `cron` or `at`, its output in the OS log under its pid, one
job at a time. The shell's `cron` and `at` are role commands
(`runtime/shell/sh_roles.py`), so with the VM stopped they need the
console, and so do the alarms, which a stopped VM does not serve.

## 4. Connectivity events

WiFi and BLE up and down as records in the
rings (`hardware.md` §2), so an app wakes on `moybyte.on("net", fn)` or
`moybyte.on("ble", fn)` instead of polling `wifi`.
