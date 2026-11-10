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
True where the item went to an app, the menu is up or the request waits in a
notification (below), False where no app opens it.

**Taking the screen is one gate (owner, 2026-11-08).** Every way a process
brings an app to the front passes `runtime/system/front.py`'s `take`:
`open_with` and `share`, the picker (§2), and the nav role's verbs
(`open_app`, `edit`, `open_image`, `open_text`, `edit_file`, `play`,
`run_script`; `NavServer`, whose caller is the run holding the role's
grant), on every tier that holds them: Python, a Lua service's console
calls, a Lua app's `open_app` and a compiled app's through the ROLE door.
The request goes through where its process is the run on screen (the front
run, its window holding the input) or holds the **`front`** permission
(`processes.md` §2); the shell and the user's own input are never gated.
Any other process's request becomes a notification under its title ("Wants
to open todo.md in Notes", `processes.md` §7) whose act does what it asked,
the route read as it is opened; the verb answers as though it went. An alarm
lights the screen and takes nothing: what its callback asks for passes the
gate as any call of its process does.

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
kind (documents' notes, pictures' drawings). A compiled command (WASI or
native) is given the same folder by path, its only preopened root
(`sh_door.wasi_roots`, from the same policy). One folder a grant on every
tier: a manifest naming two is refused, at launch and at a command's run.
Downloads, `.Trash` and `.history` are no grant's: the user moves a download
into a folder with Files, or hands one file over through the picker.

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
import of it, and picking a folder is not offered. The picker takes the
screen, so it passes §1's gate: from the background without `front` it is a
notification, and opening that brings the asking run to the front with the
picker over it, where the pick reaches it.

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
(`system.md` §4). **USB mass storage** (owner, 2026-11-03) puts the card, its `moybyte/`
folder with it, on a computer while the store is held closed, never both at
once (`runtime/system/usb_drive.py`): the card volume closes and the C holds
it (`moy_store.card_hold`, every mount then answering EBUSY) before the
board's driver starts (`native/moy_usbdisk`, which refuses a card not held),
and the driver stops before the store mounts again and the shelf rescans. It
is Settings' USB drive page, refused with no card or while a game or an app
runs; while it is on the console opens nothing and the page holds the
screen until its Turn off gives the card back, or the computer lets it go
(an eject, a power-off, the cable out), or the driver gives the port back
by itself with no computer configuring it for 30 s.
On the Waveshare P4 it is the high-speed OTG port (the `usb` host's port:
each refuses while the other holds it) and the dev channel stays; on the
T-Deck it switches the S3's one USB port from Serial/JTAG to the OTG
controller, so the dev channel is lost while it is on, turning it off
switches back, a restart switches back first, and the BOOT button still
reaches the ROM loader. The Guition P4's high-speed connector is unknown and
its USB-C is the Serial/JTAG, whose other PHY's pad is its panel's reset, so
it denies the driver, as the Guition S3 does (each board.toml says why).
**No migration** (CLAUDE.md
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
seconds, refused with EPERM without `alarms` (a Lua app's and a Lua
service's alike, `tiers.md` §2; a Lua service's and a compiled program's is
the kernel's, the system's message `"moybyte.at"`, `docs/app_api_v1.md`
§9), runs `fn` no earlier than `t` and
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

As built: WiFi is up while the station has an address and BLE while its
host has synced with the controller. The drivers keep each state on the
input table and post a change, and only a change, as a NET or BLE record on
its ring source `radio` (`native/moy_input/moy_input.h`, the radios): the
station's event handler (`native/moy_net/moy_wifi.c`), the BLE central's
start and stop (`native/moy_input/moy_hid.c`) and NimBLE's reset
(`moy_ble_task.c`), and on the host and in the browser `FakeWifi`'s link
(`runtime/run/host_api.py`). A record's `a` is 1 up and 0 down, its `b` why it
went down (the station's disconnect reason, NimBLE's reset reason; 0 when it
was put down). `net` and `ble` are messages from the system, `fn("system",
up, why)` a change, the same names and arguments on every tier: one router
of the shell's reads the ring with the table's one reader, while a process
waits on either or a Lua service lives, and routes each change to every
process's event queue that listens on its name (`runtime/system/moybyte.py`,
THE RADIOS; `native/moy_kernel/moy_evq.h`'s `moy_evq_route`), so the table's
eight readers are not spent one a process. `tests/test_radio_events.py` holds it.

## 5. Crypto

Random bytes, SHA-256, HMAC-SHA256, AES-GCM, AES-CTR, AES-CCM, X25519 and
Ed25519 are rows of the one table (`native/os_calls.json`, `moybyte.random`,
`sha256`, `hmac_sha256`, `aes_gcm_seal`, `aes_gcm_open`, `aes_ctr`,
`aes_ccm_seal`, `aes_ccm_open`, `x25519`, `ed25519_public`, `ed25519_sign`,
`ed25519_verify`; `docs/app_api_v1.md` §10 names each tier's), so a mesh
stack (Reticulum, Meshtastic) can be an app on any tier. Decisions
(2026-11-09):

- **One C body, over the image's mbedTLS.** `native/moy_net/moy_crypto.c`,
  beside the sign-in's `moy_seal.c`, whose random source it shares
  (`moy_seal_random`: an HMAC-DRBG a board seeds at boot with the SAR ADC's
  noise mixed into its RNG, the host and the browser from `getentropy`;
  `moy_seal.h`, THE RANDOM SOURCE). A board compiles it against the ESP-IDF's mbedTLS 3.6.4 (AES and
  SHA in hardware), the host's library and the browser's wasm against the
  vendored copy of the same release (`native/moy_net/mbedtls/README.md`), so
  every tier runs the same code; Python reaches it through the module
  `moy_app`, every other tier through the app ABI's door. No tier
  reimplements a primitive.
- **No permission.** Each row is pure computation over the caller's buffers:
  nothing kept, nothing of the device reached, no key stored. The RNG is
  the hardware's, which no app can drain or read another's draw from.
- **A small surface that is hard to misuse.** An app's own AES is GCM: a
  seal draws its own 12-byte nonce and puts it first, the 16-byte tag last,
  and an open answers the plaintext or FORGED (EBADMSG on the POSIX tiers,
  None in Python, nil in Lua). No AAD: the native tier's U-mode check passes
  at most 7 argument registers, which key, AAD, data and answer would pass.
  There is no ECB.
- **CTR and CCM are for wire formats that fix the nonce (2026-11-10, #246's
  mesh item).** Meshtastic encrypts a channel's packets with AES-CTR under a
  counter block made of the packet id and the sender, and a direct message
  with AES-CCM (a 13-byte nonce, an 8-byte tag) under the SHA-256 of an
  X25519 secret; an app speaking it must take the nonce the protocol
  gives. So `aes_ctr(key, data)` takes the 16-byte initial counter block
  first in `data` (the block counts as one big-endian 128-bit number, NIST
  SP 800-38A) and is its own inverse, and `aes_ccm_seal(key, data, tag)` /
  `aes_ccm_open(key, sealed, tag)` take the 13-byte nonce first, no AAD, a
  tag of 4 to 16 bytes, even: the nonce rides in the data for the same 7
  registers. Keeping a nonce unique under a key is the caller's, as the
  protocol it speaks says. X25519 clamps the
  secret itself, takes an empty peer for the base point (the public key)
  and refuses a peer of small order. Every answer is written into the
  caller's buffer and answers its length; one too small is FULL (ERANGE), a
  length refused BAD (EINVAL), never a crash.
- **Curve25519 is a board's sdkconfig line** (`CONFIG_MBEDTLS_ECP_DP_CURVE25519_ENABLED`
  in each console board's `sdkconfig.board`; MicroPython's base config
  turns it off); an image without it answers `x25519` ABSENT.
- **Ed25519 is the vendored Monocypher's (owner, 2026-11-09, #246):**
  mbedTLS 3.x has no EdDSA. Monocypher 4.0.3 (Loup Vaillant; CC0 or
  BSD-2-Clause; audited by Cure53 in 2020), its `monocypher.c` and
  `monocypher-ed25519.c` (RFC 8032's Ed25519 over SHA-512), is pinned and
  hashed in `native/moy_net/monocypher/` (`make vendor-monocypher`) and
  compiled beside `moy_crypto.c` on every tier, a board's image included.
  The secret is RFC 8032's 32-byte seed: `ed25519_public(seed)` answers
  the 32-byte public key, `ed25519_sign(seed, message)` the deterministic
  64-byte signature, `ed25519_verify(public, message, signature)` 1 or 0
  (a key or a signature that does not decode is 0, a length refused BAD).
  Dropbear's TweetNaCl-derived `curve25519.c` (`native/moy_ssh`) is in
  every console image too, but it compiles only inside Dropbear's
  configuration, is absent from the host's library and the browser, and is
  TweetNaCl's slow reference arithmetic; a hand-written Ed25519 was not an
  option.
- **What a mesh stack adds later:** Reticulum's token is AES-CBC with HMAC,
  an interop row of its own added with the protocol that needs it, as
  Meshtastic's CTR and CCM were (above; the Mesh app,
  `tests/fixtures/apps/moybyte.mesh.moy`, is their first user). HKDF is two
  HMACs an app writes.
