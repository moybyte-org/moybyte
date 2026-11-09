# The OS (2026-11): the threat model

What the console defends, against whom, and what it leaves to the person
holding it: the section #246 asks for, beside the app guide
(`docs/app_api_v1.md` §6). Read `docs/os_2026-11.md` §4 first: the trust
ladder is the design this checks the code against. Each boundary names what
holds it, the file that enforces it, and what it does not stop; §7 gathers
the limits a reader would not guess.

---

## 1. Assets and adversaries

**What is defended.** The user's files and each app's prefs; the console's
secrets in the store's `system/` folder (the SSH password's record, `webkeys`,
`authorized_keys`, the SSH host key); the firmware and its update path; the
devices and radios, which one process holds at a time; and the screen, which
an app takes from the background only under `front`.

**Against whom.** An app the user installed, of any tier, reaching past what
it was granted; a stranger on the same network; a radio within range; a
server or a path between the console and one. **Not** the person holding the
board (§8).

---

## 2. The tiers, board by board

The ladder's rule holds in code: the more a tier can do, the less it is
sealed and the more the prompt says.

| tier | boards | what seals it | enforced in | what it does not stop |
|---|---|---|---|---|
| Lua app or game | every board | libmoy's sandbox: base without `load`, `loadstring`, `dofile`, `loadfile`, `require` and `collectgarbage`; `io`, `os`, `debug` and `package` not compiled in; a chunk is loaded as text only, a binary one refused; the role names its permissions bind, and `io`/`os` mapped onto the folders they grant | `native/moycore/libmoy/moy_lua.c` (`BANNED`, `SANDBOX_LIBS`), `native/moycore/modmoycore.c` (`run_chunk`), `native/moy_app/moy_app_lua.c` | a bug in the Lua VM, which runs in the kernel's own memory |
| Lua service | every board | a state of its own: base without `load`, `dofile` and `loadfile`, with `coroutine`, `table`, `string` and `math`; an `os` of `time` and `clock` alone; no `io`, `debug` or `package`; its source loaded as text | `native/moycore/moycore_svc.c` (`open_names`) | a bug in the Lua VM, which runs in the kernel's own memory |
| wasm, interpreted | every board | WAMR's interpreter checks every access against the bounded linear memory; the imports are the only surface, an import of a role not granted refused at load; a WASI command's paths answer NOTCAPABLE outside the folders its `files` grants, and WAMR's libc-wasi is not built | `native/moy_wasm/README.md`, `native/moy_wasm/moy_wasi.c`, `native/moy_sh/moy_sh_prog.h` (THE ROOTS) | a loop that calls no import runs until it returns (README, "Stopping a run") |
| wasm, AOT | every board | the bounds checks are in the compiled code, so a module loads only by provenance: its key matches this chip and format, and a signature present must verify (`moy_ota_admit_module`) | `native/moy_net/moy_ota.h`, `native/moy_wasm/moy_wasm_key.h` | an **unsigned** module, admitted under `full_access`, is native code at the kernel's trust on every board, the P4s included: AOT never runs in U-mode (§7) |
| Python | every board | nothing: the shell's one VM, its real builtins, `open`, `os`, `socket` and every shell module importable; the manifest is legible, not binding | `runtime/shell/app_perms.py` (`TRUSTED`), `processes.md` §2 and §3 | anything: the prompt says "A trusted app" and that is the whole of it. A permission turned off binds its names to `None`, a courtesy the app can step around |
| C (ELF) | the P4s | U-mode under PMP, the OS table the only way into the kernel (below) | `native/moy_elf/README.md` ("Isolation"), `native/moy_elf/moy_umode.h` | a bug in a row's own body; a canary mismatch ends the board, by design |
| C (ELF) | the S3s | nothing (owner, 2026-11-07, `tiers.md` §1): the module is loaded into the kernel's memory and can write any of it, whatever its rows check | `runtime/shell/app_perms.py`: `check` refuses a native cart without `full_access`, and the S3s have no entry in `FULL_ACCESS_LINES`, so the prompt says "Full access to the console" | everything: files, secrets, radios, flash. The floor is the crash record and the strike ledger, keyed by content hash for an unsigned module (`processes.md` §10) |

**The P4s' isolation, piece by piece** (`native/moy_elf/README.md` is the
authority; this is where each defence lives):

- **Memory.** One block per module (the loader's segment, by the
  `esp_elf_malloc` wrap in `native/moy_elf/modmoy_elf.c`) and extents for each
  thread's U stack and each mapping, every one a 128-aligned PMP window. The
  PMP is the kernel's from reset: the P4 images build with the bootloader's
  region protection off, and `moy_umode.c` refuses to build otherwise.
- **Arguments.** Each row's pointer rule is `native/os_calls.json`'s `u`
  field; `tools/gen_os_table.py` refuses a pointer parameter without one. A
  pointer outside the calling process ends it, and what another thread could
  change after the check is copied or read once.
- **Handles.** A `pthread_t`, a `DIR` and a mutex id carry their slot's
  generation, so a stale one names nothing; a descriptor the process does
  not hold is `EBADF`; an object a row is using outlives another thread's
  close of it.
- **The scrub.** A module's block, its stacks and its mappings are zeroed
  when taken and when let go (`u_scrub_free` in `native/moy_elf/moy_os.c`),
  so no process reads what the last holder left.
- **The trap context.** A U call's saved kernel context carries a canary
  checked at its end (THE SEAL, `native/moy_elf/moy_umode.c`); a trap is
  closed with `mret` before kernel code runs in it (`moy_u_close`).
- **The kernel's own records** are taken under its lock, so no race between
  a row, a fault and a process's end reaches freed memory (90da6e6d9).
- **Permissions bind rows**: a socket row asks the process's grant
  (`net_need` in `moy_os.c`) before it opens one.
- **The net**: `tests/on_glass.py`'s `elf_umode_isolates` runs
  `tests/fixtures/elf/elf_wild.moy` against every row with a checked pointer
  or handle (the list generated from the table), hostile stacks, and ends
  mid-row, checking the kernel's heap and stacks after (384ba4f87).

---

## 3. Permissions and consent

| boundary | held by | does not stop |
|---|---|---|
| **One table** | `native/os_calls.json`'s `permissions`, which `tools/gen_os_table.py` writes into `runtime/shell/app_perms.py`'s `TABLE` (the prompt, the install and launch checks, Settings) and into `moy_app`'s role allowlist. A permission lists only the tiers that serve it | — |
| **The install prompt** | Get Carts and `pkg install` name each permission and install on a yes, all or nothing; an update adding one asks again; Settings turns one off (`app_perms.prompt`, `Consent`). The shell's own roles are never grantable (`tests/test_user_apps.py`) | an app using what it was granted as it likes; the prompt is the user's judgement, not the console's |
| **A cart that arrived as files is asked** | a cart in a folder with no consent record (copied onto the card, over SFTP or USB, pushed by a paired browser) is asked on its first run, with the install prompt's lines; until a yes every way of launching it is refused, its unsigned modules' admission included (`app_perms.asks_first`, `Consent.for_run`). A built-in (by its seed) and a cart made on the console are never asked: the making writes a "made" record (`Consent.made`) and the cart runs on its manifest as written, less what Settings turned off (`app_perms.py`'s docstring) | a record outlives a cart folder deleted by hand (§7); the prompt is the user's judgement, as at install |
| **A built-in is known by its seed** (eb57f46a8) | the seed records its roster per store root (`runtime/carts/moy_catalogue.py`'s `seed`); a `moybyte.*` folder is a built-in only if that roster names it (`builtin_name`, `runtime/carts/moy_store_base.py`); the store refuses the namespace (`cart_index.check_cart`); the folder `<id>.moy` owns the id `<id>`: in any other folder a manifest's id names the cart only where no folder of the store is named for it, and else the folder's name is the id (`id_in_folder` in `moy_store_base.py`, its twin `id_may_name` in `native/moy_store/moy_cat.c`; `moy_carts._cart_id`) | two copies in folders not named for the id they claim share it (§7) |
| **Signed modules** | an AOT or ELF module's trailer signs scheme, chip, length and sha256 under a baked key; a present signature must verify whatever the consent; an absent one loads only for a cart granted `full_access` (`app_perms.admits_unsigned`, `moy_ota_admit_module`). There is no console-wide "unknown sources" switch: that consent is per cart | the signature vouches for the module's bytes, not the cart: it covers neither the manifest nor the id |
| **Tamper evidence** | a bad or malformed signature refuses the cart outright; only a refusal of any other kind falls back to the interpreter (`device/moycore_glue.py`, `_AOT_TAMPER_EVIDENCE`) | — |
| **The store's index** | a signed index pins each file's size and sha256 and an install lands by one rename once every byte checks (`runtime/carts/cart_index.py`, `system.md` §6) | an unsigned index rests on TLS alone |
| **Messages** | the kernel stamps the sender; another app is heard only on a name the receiver lists under `receives`; a sender's unread messages are capped per queue (`native/moy_kernel/moy_evq.h`); a background callback over its slice is struck, and strikes suspend (`native/moy_kernel/moy_super.h`) | a Python app, which runs in the shell's VM and reaches what the shell reaches |
| **Leases** | one holder (`EBUSY` for another), swept at its process's end by exit, crash or kill (`native/moy_kernel/moy_lease.h`, `runtime/system/leases.py`) | a raw lease is the resource itself: its prompt says "full access" to it |

---

## 4. Network surfaces

Nothing listens until the user turns it on.

**SSH** (`runtime/system/ssh_service.py`, `native/moy_ssh/moy_ssh.h`). It serves
only while `system/password` holds a record, which no console ships with
and `passwd` refuses under eight characters. The record is `$moy2$`
(`native/moy_net/moy_seal.h`, THE PASSWORD): PBKDF2-HMAC-SHA256 at 32,768
rounds, from which come the SPAKE2+ verifier's w0 and L (the web pairing,
below); SSH checks a typed password by deriving w0 and comparing it in
constant time, and a record of any other form, a `$moy1$` one included, reads
as no password. Keys in `authorized_keys` sign in too, and
`password_auth = no` in `system/ssh` turns passwords off. Any user name is
the console's one account, a connection gets five tries, and forwarding of
every kind is compiled out (`native/moy_ssh/port/localoptions.h`). Signing in
is being the owner: the shell and every command, the whole files tree over
SFTP, and `/etc`, which is `system/`. What it does not stop: one connection
is served at a time, so a connection that never signs in holds SSH for
`AUTH_TIMEOUT`, 30 s (`native/moy_ssh/port/localoptions.h`; Dropbear's own is
300); and a weak password falls to guessing against a copied record, 32,768
rounds a guess, which a GPU does fast.

**The webhost and the sync RPC.** The web console is a Settings switch, off
at boot (`runtime/system/web_console.py`). A browser pairs once, by the
console's QR or by the password, and signs in with its device key from then
on; the page may reach unsealed only the bundle and a marker, and a
per-source backoff counts every credential (`native/moy_net/moy_webauth.c`,
`native/moy_net/moy_seal.h`; `system.md` §3 has the protocol and its own
"What it does not stop"). The password pairs by SPAKE2+ (RFC 9383): the board
holds the record's w0 and L, never w1, so a copy of the record can run the
exchange but never finish it, and the password never crosses the network.
Its limits in short: the page itself crosses plain HTTP, so an active attacker on the LAN can serve a page that captures what is
typed into it; a paired browser can write and run any cart, one that arrives as files
being asked on the glass at its first run (§3); the password pairs against the record's PBKDF2 cost
(above) and the backoff. The Zero's setup network is open
while it is unconfigured (`firmware/seeed_xiao_esp32s3_zero/modules/zero_setup.py`).

**Fetches.** The kernel's client (`native/moy_net/moy_net_port.c`) verifies an
https server against the image's CA bundle, with the host's name, and a build
without the bundle refuses https (`moy_conn_verifies`; `moybyte.http.tls()`,
`runtime/system/dev_http.py`). The one exception is the updater's own
connection, which verifies nothing: the manifest's signature decides
(`native/moy_net/moy_ota.h`). A certificate's dates read a floor
that only moves forward, and NTP's answer counts only within 400 days past it
(`moy_clock_cert_time`; `hardware.md` §1 has the rule). A certificate that
expired before the floor is refused whatever NTP says; one that expired
since passes, and NTP is still unauthenticated inside that window, so an
attacker on the LAN can move the dates anywhere within it.
`http` reaches any host, the LAN's included, with no address filter; its
prompt line says "the internet and your network". A Python app's own `socket` use is its own business (§2).

**OTA** (`device/moy_ota.py`, `native/moy_net/moy_ota.c`). A manifest from the
baked URLs must carry an RSA-2048 signature, under a key baked into the image,
over its board, channel, version, size and the image's sha256; the C6's block
carries its own. The bootloader rolls back an image that never confirmed. The
private key never enters the repository: `tools/ota_sign.py` reads it from
`MOYBYTE_OTA_SIGNING_KEY`. What it does not stop: an `ota.json` on the card
may point at an unsigned image (§8), and a switch of channel may install an
older signed image, by design (a deliberate downgrade).

---

## 5. Radio input is data, never a sender

No radio input arrives with a sender the OS vouches for. An app that acts on
one parses it as hostile.

| radio | what the OS does | what it does not stop |
|---|---|---|
| **ESP-NOW** (`multiplayer`) | up only while a cart holding `multiplayer` runs (`runtime/run/player.py`); the payload is inputs, never state (`docs/netplay_v1.md`), and a desync is said; a `net` message is JSON, a bad one logged and dropped (`runtime/run/players.py`, `EspNowNet`) | frames are neither signed nor encrypted, so the sender's address is the one check, and it narrows who is heard without authenticating (`native/moy_play/moy_match.c`, `moy_match_dispatch`; `netplay_v1.md` §2, "A match hears only its peer"). A console that spoofs the peer's address is heard as the peer; a START can only re-run the cart this console announced (`launch_cart`) |
| **BLE GATT** (`ble`) | one process holds the radio; the app chooses the device and reads what it says (`runtime/system/dev_ble.py`) | whatever the device sends |
| **BLE HID keyboards** | only while Settings' keyboard input is on; a keyboard is picked in Settings and only that address is ever connected, with none picked nothing is: the machine waits in `choose` and scans for nothing; Just Works pairing with bonding, a report is input only once the link is encrypted, and a link whose encryption fails is dropped (`native/moy_input/moy_hid.h`, `moy_hid.c`'s `moy_hid_on_notify`) | Just Works has no protection against a device in the middle at the first pairing, so a keyboard the user picks is trusted from then on. A keyboard is the user's hands: it answers prompts and opens the terminal |
| **LoRa** (`lora`) | raw packets as received, with RSSI and SNR (`runtime/system/dev_lora.py`); LoRaWAN downlinks are checked by MIC under the session keys and their counter only rises (`native/moy_net/moy_lorawan.c`) | any raw packet; the app holds its own LoRaWAN keys |
| **WiFi** | the station joins the networks the user saved | NTP and mDNS answers, which nothing authenticates (§4) |

---

## 6. Crypto for apps

Random bytes, SHA-256, HMAC-SHA256, AES-GCM, X25519 and Ed25519 are rows of the one table, served on every tier by `native/moy_net/moy_crypto.c` over the image's mbedTLS and, for Ed25519, the vendored Monocypher 4.0.3 (`native/moy_net/monocypher/`), with no permission (`between_apps.md` §5, `tests/test_crypto_rows.py`; 65076d1f9, 879de54d4). GCM draws its own nonce and an open that does not authenticate answers FORGED; X25519 clamps the secret and refuses a small-order peer; Ed25519 signs deterministically from a 32-byte seed (RFC 8032), and a key or signature that does not decode verifies as 0. There is no key store: an app keeps its keys in its prefs or files, in the clear on the card.

Every random byte the console makes (the sign-in's, the SSH server's, the TLS
client's, a WASI program's `random_get`, LoRaWAN's and these rows') comes
from `moy_seal_random` (`native/moy_net/moy_seal.c`), an HMAC-DRBG over
SHA-256 seeded at its first draw from 256 bytes a board reads before
`app_main` with the SAR ADC's noise on, and mixing 16 fresh RNG bytes into
every draw (§7).

---

## 7. Known limits and side channels

What holds less than a reader would assume. Each is how the code stands.

- **A path's existence leaks inside a program's folders.** A compiled
  program's media handle plays only files under its own cart folder and the
  folder its `files` permission names, and anything else is DENIED whether
  it exists or not (`runtime/system/dev_handles.py`, `_media_roots`); inside
  those folders ABSENT still tells a missing file from a present one.

- **"Run its own machine code" on a P4 covers a wasm cart's unsigned AOT
  too.** `FULL_ACCESS_LINES` gives `full_access` the P4 line whatever the
  tier, but an unsigned AOT module is not confined the way an ELF is.
- **A consent record outlives a cart folder deleted by hand.** The record
  is the app id's, in `system/settings` (`app_perms.py`, `Consent`), and
  only a delete through the shell forgets it (`cart_manager.py`, `pkg`): a
  folder removed over SFTP or USB leaves it, and a cart copied into the same
  folder later inherits the consent and is never asked.
- **Two copies share an id.** The folder named for an id owns it (§3), but
  two carts in folders not named for the id they claim, and no folder that
  is, still share its prefs and its consent record; and prefs written under an
  id before a folder came to own it carry over to the owner.
- **The random source seeds once.** The DRBG is seeded at its first draw
  from the boot's 256 bytes (the SAR ADC's noise, read in a constructor
  before `app_main`) and mixes 16 bytes of the chip's RNG into every draw:
  ESP-IDF counts that RNG as true random only while the radio or the SAR ADC
  runs, and a P4's radio is the C6. Only `moy_seal.c` and the U-mode trap
  canary (`native/moy_elf/moy_umode.c`) read the chip's RNG directly
  (`tests/test_crypto_rows.py`).
- **A password record is guessable offline.** `$moy2$` costs a guess 32,768
  PBKDF2 rounds, which holds a long password and not a weak one against a
  GPU, and it pairs no browser by itself (§4).

- **Multiplayer and keyboards trust the air** (§5): the sender's address is
  all a match checks, and a keyboard's first pairing is Just Works.
- **SSH is one connection at a time** (§4).
- **A runaway wasm loop** that calls no import cannot be stopped short of its
  return or a restart (`native/moy_wasm/README.md`).

---

## 8. Out of scope

- **Physical access.** No board enables secure boot or flash encryption (each
  board's `sdkconfig.board`): whoever holds the board reads its flash and
  card and flashes any image. The USB serial line answers whoever has a
  cable: the dev channel takes shell lines and `py` Python expressions with no
  sign-in (`native/moy_kernel/moy_devch.h`, `runtime/boot/dev_channel.py`),
  which is the whole console, secrets included; the Zero's USB is
  MicroPython's REPL. The console's own keys (the terminal, the recovery
  console, `system.md` §2) need no sign-in either. An `ota.json` written to
  the card may name an unsigned update.
- **Timing, power and fault side channels**: the rows' durations, caches
  shared between processes, the radio's emissions, glitching.
- **An app misusing its grant**: `files:documents` may delete every document,
  `http` may send what it read anywhere, `front` may open apps over the
  user's.
- **The browser's profile**: a device key is that profile's, and on the
  project site the console's store is the browser's own storage.
- **The vendored code's own bugs** (Dropbear, WAMR, mbedTLS, Lua): they are
  the console's, fixed in our forks and re-vendored.
