# The OS (2026-11): the OS between apps

Phase 6 of the order of work. Read `docs/os_2026-11.md` first.

---

## 1. Intents

Today roles go only from an app to the shell; `nav.open_app`
and the Files router are the one "open that, pointed here" seam
(`docs/app_api_v1.md`). The design: an app's manifest declares what it
**handles** (`"handles": ["text/markdown", "image/x-moyimg", "image/*",
"x-scheme-handler/https"]`); an app calls `moybyte.open_with(file_or_url)`
or `moybyte.share(data, mime)`; the OS
routes to the one app that matches, or, when several do, to **the last app
chosen** (owner, 2026-11-03), changeable from an "open with" menu drawn as
OS chrome; there is no per-type "always". The receiving app gets the item as a
picker grant (§2), never a broad permission.

```
moybyte.open_with(item) / share(data, mime) / pick(mime, mode) -> h   intents and the picker
```

## 2. The files model

Today (the owner: "detached from the real picture"):
carts under `/sd/moybyte/carts` (`runtime/carts/moy_store_base.py`'s
`CARTS_DIR`); a person's creations beside them as `files/<kind>/<name>`
(`native/moy_store/moy_ufiles.h`); a compiled cart's writable files in
`written/<cart>/` (`runtime/carts/cart_files.py`); the console's documents
(`system/settings`, `wifi.json`, `achievements.json`, `runtime/carts/moy_docs.py`)
beside the carts; every volume one library behind `native/moy_store/moy_vol.h`.
#231 decided (2026-10-03) that a user's own files reach a compiled cart
through a console-drawn picker.

The design, one visible hierarchy under one root (`moybyte/` on the card,
the same shape on flash without one):

| folder | holds | who reaches it |
|---|---|---|
| `carts/` | installed carts and the kid's projects | the shell (`carts`, `install`), the Editor; an app only through `nav` |
| `apps/<id>/` | **app-private storage**: what `written/<cart>/` and an app's prefs hold today | its own app, no permission |
| `files/` | the **shared user folders**, named on disk as the OS shows them: `Documents/` (today's docs; stories in `Documents/Stories/`), `Pictures/` (drawings; sprites in `Pictures/Sprites/`), `Music/`, `Recordings/`, `Downloads/` and `.Trash/` | an app with `files:<folder>` (`files:documents`, `files:pictures`, …; per-app consent, one folder a grant), or one file at a time through the picker |
| `system/` | the console's documents as plain-text files (`system.md` §5) | the shell |

**The shared folders carry their real names on disk (owner, 2026-11-03)**,
so USB, SFTP and WebDAV show what the OS shows. The rename ships as a
strict reader plus a seed bump: no migration, a legacy `files/<kind>/`
folder reading as absent until wiped by hand (dev boards hold nothing of
value). **Types are MIME types** (owner, 2026-11-03) wherever an
app sees them: `handles`, the picker, intents, share and the clipboard
(`text/markdown`, `image/x-moyimg`, `audio/x-moysong` for our formats,
registered in one table). **File types**
map to default apps through the `handles`
table of §1. **The picker** is OS chrome: an app with no `files:*`
permission calls `moybyte.pick(mime, mode)` and receives a handle to
the one file or folder the user chose, read or read-write, for its run (the
#231 shape, generalised from compiled carts to every tier). **The Files
app** (`runtime/apps`' files app today, the router's seam) becomes the
tree's browser and the first test of the picker and of "open with".
**Sync, the web and USB see the same tree**: the sync RPC's three roots are
three of the top folders and `apps/` becomes a fourth, with the same strict
reader rule; a board-served page edits the board's tree, a site page its
OPFS copy; SFTP and WebDAV mount it from a computer (`system.md` §4).
**USB mass storage** (owner,
2026-11-03) exposes the card's `moybyte/` folder while the store is held
closed, never both at once: on the P4s, and on the T-Deck as a mode in
Settings that switches the S3's one USB port from Serial/JTAG to mass
storage, so the dev channel is lost while it is on; turning it off switches
back and remounts the card, and the BOOT button still reaches the ROM
loader. **No migration** (CLAUDE.md "Settled"): `written/<cart>/` →
`apps/<id>/` ships as a strict reader plus a seed bump, a legacy `written/`
folder reading as absent until wiped by hand (owner, 2026-11-03); a
compiled cart's saves on a dev board are disposable.

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

## 4. Connectivity events

WiFi and BLE up and down as records in the
rings (`hardware.md` §2), so an app wakes on `moybyte.on("net", fn)` or
`moybyte.on("ble", fn)` instead of polling `wifi`.
