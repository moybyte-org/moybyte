# The kernel's spine — sprint 2's native design (2026-10)

**What this is.** The design sprint 2 of `docs/native_kernel_2026-09.md` (#224)
builds, written before any of it is: the C modules and their interfaces, where
the crash record lives, what the recovery screen draws with no VM on each
console, the settings store's format, the WiFi leases, the embed entry, the
gate's tests and the internal-SRAM cost. The kernel's language is C (owner,
2026-10-06). Where the sprint stands is #224's; measurements go there too.

**The carve it builds on** (dev, 2026-10-06): the spine's half of
`runtime/console.py` is `runtime/console_spine.py`; the components it drives
are `runtime/moy_spine.py`, the Python twin of the native module, call for
call; `tests/test_moy_spine.py` pins the rules and the spine trace in
`tests/test_semantic_traces.py` pins the values on every VM. The crossing
swaps each twin for its binding under those tests unchanged.

## 1. What crosses, and where it lands

| component | Python twin (the interface) | C module |
|---|---|---|
| handle tables | `moy_spine.Table` | `native/moy_spine/moy_htab.h` |
| app registry, back-stack, return records | `moy_spine.AppRegistry`, `BackStack`, `Returns` | `native/moy_spine/moy_route.h` |
| WiFi leases | `moy_spine.Leases` | `moy_route.h` (one mask) |
| settings store | `moy_spine.Settings` under `runtime/system_store.py` | `native/moy_spine/moy_settings.h` |
| strike ledger | `runtime/crash_guard.py`'s `CrashGuard` | `native/moy_spine/moy_ledger.h`: the same class over the settings rows, `moy_spine.CrashGuard`, which `crash_guard` takes when the spine is native (§6) |
| crash record, boot-loop guard | — | `native/moy_kernel/moy_crash.h` |
| recovery screen | — | `native/moy_kernel/moy_recovery.h` |
| the entry and the VM service | the port's `mp_task` | `native/moy_kernel/moy_kernel.c` |
| `runtime/moybuf.py`'s registry | `moy_alloc`'s `moy_buf_live` | a `moy_htab` of kind BUF |

`native/moy_spine/` builds three ways, as `native/moy_index/` does: a
MicroPython usermod registered extensible (a `moy_spine.py` on the path wins,
so a build that leaves the native module out runs the twin), a host library
for ctypes, and a fuzz driver. Each crossing deletes the twin it replaces in the
same change (`docs/native_kernel_2026-09.md` §4.2).

**Where the native spine is the default** (2026-10-06): the four consoles. Each
board.toml declares `[native.impl] spine = "c"`, `tools/esp32_build_lib.sh`
exports it as `MOY_SPINE_IMPL`, and the image freezes no `moy_spine.py`. The
desktop MicroPython the traces run on builds it too (`UNIX_MP_SPINE`, default
`c`). The CPython host runs the Python twin, which is also the interface the
tests pin; the browser build keeps it until sprint 3's frame-tail pass takes the
C spine into the browser's wasm, the kernel's modules having entered that build
with the glass (2026-10-07: `native/moy_glass` compiles into it, and the bundle
is rebuilt at the pinned emscripten and re-baked into every image); the Zero has
no console to route.

**What stays Python after sprint 2**, by decision: the app OBJECTS and the
surfaces a route lands on (`console_spine.py`'s side of each verb); the radio
service the lease powers (`device/device_wifi.py`, sprint 3); the settings
rows' save hook, which writes the file through the SD gate
(`SystemStore._write`, §5); the Editor's project return
(`_project_return` holds a cart and reloads it through the loader: an app's
return, not the kernel's); `load_system`'s apply cascade (each subject crosses
in its own sprint); the launcher's broken-cart badge (§10).

## 2. Handle tables

`moy_htab` is the discipline `runtime/moy_index.py` set, generalised: a row
holds a C struct of the client's size instead of a path, and the handle carries
a KIND, so a handle handed to the wrong table is refused rather than read as a
row there.

    handle = gen << 12 | kind << 8 | slot      never 0, below 2**30
    kind   1..15, assigned in moy_htab.h: APP = 1, BUF = 2; later sprints add theirs
    gen    1..262143, wraps to 1; a freed slot is reused lowest-first

    moy_htab_t *moy_htab_new(const moy_htab_mem_t *, uint8_t kind, uint32_t slots, size_t row_size);
    int  moy_htab_add(moy_htab_t *, uint32_t *h, void **row);  // FULL, NOMEM
    int  moy_htab_get(const moy_htab_t *, uint32_t h, void **row);  // STALE
    int  moy_htab_release(moy_htab_t *, uint32_t h);            // STALE
    uint32_t moy_htab_count(...), moy_htab_slots(...), moy_htab_at(..., slot);

Every byte comes from the `moy_htab_mem_t` the client passes in, a pair of
allocate and release functions, because the store index (the gc heap, so the
collector keeps its table with the object) and the spine (PSRAM) share one
`moy_htab.c` in one image and cannot share one host symbol. The spine's is
PSRAM (`heap_caps_calloc(MALLOC_CAP_SPIRAM)`) on a board, calloc on the host and
a failure-injecting counter under the fuzzer. The binding maps STALE to
`StaleHandle` (a ValueError naming the table), FULL to `OSError(ENOSPC)`, NOMEM
to `MemoryError` and a non-int argument to TypeError, which is what the twin
raises. A kinded table has up to 256 slots; the binding's `Table` keeps its
rows' Python objects in a gc array beside the C table, which holds no row bytes.

**The store index is this table.** `moy_index`'s handles are the same layout
with the kind field folded into a 12-bit slot: `moy_htab` with kind 0 is that
table, and `native/moy_index/moy_index.c` keeps its ABI and its pinned values
and takes its slot bookkeeping from it; its path intern is a key map on top.

## 3. Routing: the registry, the back-stack and the return records

`moy_route` holds kinds, never objects: a kind (an app id or a back-stack kind)
is 1..15 bytes of any value, stored as a length and 15 bytes (16 in all), so a
kind with a NUL in it is its own kind. The binding hands a kind to Python as
an interned string (a qstr, made once per kind), so `top()` and `has()` on the
frame path allocate nothing (`tests/test_frame_alloc.py`).

- **Registry**: a `moy_htab` of kind APP, 64 rows of
  `{id[16], title (PSRAM string), text_mode, min_w, min_h}`, in registration
  order; `register` refuses a duplicate. The binding's Python side keeps
  `_apps_by_id` (id to Layer), because an object cannot cross.
- **Back-stack**: 32 kinds, the launcher at the root. `goto(kind)` answers
  STAYED / PUSHED / RETURNED and each WM rebuilds on the answer, so navigation
  stays one call through the WM (`FullscreenStackWM.goto`); `remove` never takes
  the root.
- **Returns**: the run's caller kind (the Editor's is `menu`), the app-return
  kind, and `route(windowed)`, the exit's decision: WINDOW, EDITOR, APP or
  HOME. `_exit_to_caller` executes the route; the decision is C, so a run the
  kernel ends with the VM stopped (sprint 4) needs no Python to know where it
  lands.

## 4. WiFi leases

A mask over a closed tag table (web, update, settings, cart, link, carts, dev):
`hold(tag)` and `release(tag)` return the mask after; an unknown tag is
refused. In sprint 2 the glue in `console_spine.py` still powers the radio
through the WiFi service on the mask's edges; in sprint 3 the kernel takes the
radio and the glue goes. A non-zero mask refuses a VM stop (the stop inventory, `docs/native_kernel_2026-09.md` §4.4).

## 5. The settings store

**Rows of JSON text.** `system.json` is the object `json.dumps` writes. The
kernel holds one row per top-level key and keeps each value as its JSON text,
never re-serialising a value it does not own, so a value Python wrote reads back
byte for byte. Loading is a scanner, not a tree: it walks the top-level object
(strings with escapes, nesting to depth 32, numbers, the three literals),
splits key and value spans, and refuses anything else. Writing is `{` + rows in
order, joined with `", "` and `": "`, + `}`, through the store's atomic write;
the reader recovers from the `.bak` as `runtime/moy_carts.py`'s
`load_system` does.

**The format stays identical, and there is no migration.** Same file, same
keys, same value shapes; a file the scanner refuses reads as absent (`{}`),
the existing "a bad store must not crash boot" rule (CLAUDE.md: strict readers,
no migrations).

**The rows are the store, and a write is one verb.** There is no dict beside
them. Reads are `get(key, default)`, a value decoded afresh from its row (so
nothing a reader does to it changes the store; `text(key)` is the row's JSON for
a reader that only compares), and the one write is `set(key, value)`: it
encodes the value as `json.dumps` writes it, marks the store dirty and calls the
save hook with the file's text, which `SystemStore._write` writes through the
Python SD gate until sprint 3 makes the gate native. A hook that answers False
(a card pulled) leaves the store dirty and the next write carries the change;
`persist=False` defers a write the same way. A write cannot be left out of the
file by a caller that forgot to ask, and a key has one writer: the strike
ledger's rows (`app_guard`, `wallpaper_guard`) are read and written by
`CrashGuard` through the same `get` and `set` and, once the ledger is native, by
the kernel's own, with no stale copy to write back from. `set_text` is the same write for
text already JSON, kept as written. The format is pinned as literal bytes
(`tests/test_moy_spine.py`): `json.dumps`'s of the same object, rows in file
order.

    int moy_settings_load(moy_settings_t *, const char *text, size_t len, uint32_t *rows);  // BADJSON, NOMEM
    int moy_settings_get(const moy_settings_t *, const char *key, size_t key_len, const char **json, size_t *len);
    int moy_settings_set(moy_settings_t *, const char *key, size_t key_len, const char *json, size_t len);
    int moy_settings_delete(moy_settings_t *, const char *key, size_t key_len);
    uint32_t moy_settings_dirty(const moy_settings_t *);   // changes since clean
    void moy_settings_clean(moy_settings_t *);
    size_t moy_settings_dump(const moy_settings_t *, char *out, size_t cap);

A key is held decoded, with its length (it may hold a NUL), and written as a
JSON string escaping only what JSON requires; a value is held as written. The
scanner is RFC 8259 plus the `NaN`, `Infinity` and `-Infinity` tokens CPython's
`json` reads and writes, and refuses three more things: an empty key, a lone
surrogate escape in a key (it holds no UTF-8), and nesting past 31 containers
inside a value (the file's object is the 32nd). A refused load changes nothing.
The Python twin refuses the same three, and re-encodes each value `load` reads,
so for text `json.dumps` wrote its rows and the scanner's are the same bytes.

The scanner reads untrusted bytes, so it is fuzzed under ASan and UBSan
(`native/moy_spine/fuzz_spine.c`) and held to CPython's `json` as the model, on
generated and corrupted text, by `tests/test_moy_spine_twins.py` (the
containment of `docs/native_kernel_2026-09.md` §5).

## 6. The crash record and the strike ledger

**The record** is one 128-byte struct: magic, version, size, crc32, boot
sequence, uptime, the firmware build, the fault's PC, cause and address
(Xtensa's EXCCAUSE/EXCVADDR on the S3s, RISC-V's MCAUSE/MTVAL on the P4s), four
backtrace PCs (RISC-V: RA), the kind (fault, abort, either watchdog, an unclean
reset with no record, a VM that died), the core, `esp_reset_reason()`, the
faulting task's name, the id the ledger held OPEN and its role, and a short
text: the abort's PC, the exception's name or the VM's error. 64 bytes held
neither the backtrace nor the text.

**Where it survives, per board.**

| board | survives the panic in | after the reboot |
|---|---|---|
| T-Deck, Guition S3 (ESP32-S3) | `RTC_NOINIT_ATTR` (RTC memory): panics, both watchdogs and software resets keep it; power-on and brownout do not | copied to NVS (namespace `moy_kernel`, key `crash`) before the VM starts, then the RTC copy cleared |
| Waveshare P4, Guition P4 (ESP32-P4) | `RTC_NOINIT_ATTR` in LP SRAM; the build static-asserts `SOC_RTC_MEM_SUPPORTED` | the same |

Every board's partition table has an `nvs` partition, so no table changes and
no core-dump partition (the headroom floors of `docs/native_kernel_2026-09.md`
§6.1 assume none on the S3s). A brownout or a
power-on leaves only `esp_reset_reason()`, which the intake records as such.

**Capture.** `-Wl,--wrap=esp_panic_handler`: the wrapper, in IRAM and writing
only RTC memory, fills the record and calls the real handler. That covers
panics, aborts and both watchdogs once they panic. It reads the task name, the
uptime and the text only while the flash cache is on, so a panic inside a flash
operation still leaves PC, cause, address and backtrace. A VM that dies with
the board up (a console boot that ends before `boot_ok`, a `nlr_jump_fail`) is
recorded by the VM service, which then restarts the board.

**A hang becomes a record** (#160): the VM service task subscribes to the task
watchdog with `CONFIG_ESP_TASK_WDT_PANIC=y`, and the console's frame feeds it
through the binding once per frame; the timeout is the board's `CONFIG_ESP_TASK_WDT_TIMEOUT_S` in its
`sdkconfig.board` (15 s), above the worst legitimate frame the gate reads with
`moy_kernel.watchdog()` (#66). The first `feed()` subscribes the task, the
loop's end unsubscribes it, so the REPL is never watched; the record's kind is
`task_wdt` and its reason `console hung: no frame in Ns`. `khang` is the dev
word and `tools/kernel_gate.py BOARD hang` the gate.

**Shown after the reboot.** On the next healthy boot the console's first
painted frame (`device_boot.boot_ok`) reads `moy_crash.take()` once and the
notice banner names what was running and why it stopped; the dev channel's `state` carries the last record as `crash`, and the
Settings diagnostics row shows it. The recovery screen (§7) shows it when the
console cannot come up.

**The ledger** is `CrashGuard`, in Python (`runtime/crash_guard.py`, the reference) and
in C (`native/moy_spine/moy_ledger.c` under `moy_spine.CrashGuard`, over
`moy_settings_get` and the persisting write; the native spine's consoles run it,
`tests/test_ledger_twin.py` holds its answers and the bytes it writes to the
Python one's). The Python class stays in `crash_guard.py` because the file also
holds the record's readers. Its armed id is mirrored into RTC memory at `arm` (`moy_crash.arm(role, id)`, one
slot per role; the record names the app's over the wallpaper's) and cleared at
the heal and the release, which is how the record names what was running; a
boot clears what the last one left, which the record already holds.

**The boot-loop guard** is the same bracket around the console itself: the
starts count in RTC memory, the console calls `moy_kernel.boot_ok()` after its
first painted frame, and four starts in a row without it send the next boot to
the recovery screen instead of the VM. Four, not three: a wallpaper that kills
the board strikes out in the ledger after three boots and the fourth boots
without it (#160), so the guard trips only once the ledger has had its turn
(`tests/test_moy_kernel.py`). The boot shell's ways to the REPL, a developer's
Ctrl-C and every self-terminating mode, call `boot_ok` too; a console boot that
ends any other way before it is a failed start (§7).

## 7. The recovery screen

**When**: the VM fails to start (the first heap area cannot be allocated, or the
port's boot script or the console's `main.py` ends before `boot_ok`), or the
boot-loop guard trips.

**Only on a boot no VM ran in.** A start that fails inside the VM is recorded
and the board restarts with the floor armed in RTC memory; each choice on the
floor is a restart with that choice armed. So the floor never finds a panel, a
feeder or a bus a VM left mid-frame (`native/moy_flush/moy_flush.c`'s header:
every clause was a race once), and a missing heap area, which fails before the
VM touches anything, is drawn in the same boot.

**What**: the record (cause, the app, uptime), the firmware label, and three
choices: RETRY (start the VM as usual), SAFE (start it with `system.json`
neither read nor written: the defaults, a fill instead of a wallpaper cart) and
REPL (start it with `main.py` skipped, for a developer on serial).

**How it draws.** Its own raster, a few dozen lines: a filled rect and an 8×8
glyph from libmoy's compiled-in `moy_font_data`, at an integer scale, with a
rotation of 0/90/180/270 applied per pixel, into the panel module's own
framebuffer 0. No libmoy canvas state, no compositor, no PPA, no allocation:
the floor must not depend on what sprint 3 replaces. Each panel module gains
four C entry points beside its MicroPython binding, which keeps calling the same
bodies; the two that can fail answer 0 or an `esp_err_t`, because a floor whose
panel will not come up still has serial. A board names its four, its geometry
and its input in `mpconfigboard.h` (`MOY_KERNEL_PANEL(fn)`, `MOY_KERNEL_*`):

    int      moy_<panel>_kinit(void);        power, bus, panel init, as the binding's init()
    uint16_t *moy_<panel>_kfb(void);         framebuffer 0, panel-native size
    int      moy_<panel>_kpresent(void);     blocking: banded flush, or cache writeback on DPI
    void     moy_<panel>_kbacklight(int on);

| console | panel path before sprint 2 | what sprint 2 adds | rotation | input on the floor |
|---|---|---|---|---|
| T-Deck | `moy_lcd` (`firmware/lilygo_t_deck_plus_mainline/native/moy_lcd/`): the power rail, backlight, SPI2 and the ST7789 sequence are already C | the four entry points, factored out of `init`/`fb`/`show`/`backlight`; present is the blocking banded flush | none (320×240) | trackball click (GPIO0, polled); serial |
| Guition S3 | `moy_axs` (`firmware/guition_jc3248w535/native/moy_axs/`): QSPI AXS15231B, C | the four entry points factored out as on the T-Deck (the stop spike's, branch kernel-spike-v129 `486f843`, were a copy of `init`'s body) | the module's own rotated bands | the spike's C AXS15231 touch poll, on the port's legacy I2C driver: at v1.28 the port links it, and the IDF aborts a boot that links the new `i2c_master` beside it; serial |
| Waveshare P4 | `moy_dsi` (`native/p4/moy_dsi/`): EK79007, DPI from a PSRAM framebuffer, C; the backlight (GPIO32, active-low) is Python (`firmware/esp32_p4_wifi6_touch_lcd_7b/modules/p4_display.py`) | the entry points; present is a cache writeback; the backlight pin and polarity become board defines | none (1024×600) | BOOT button (GPIO35); serial |
| Guition P4 | `moy_dsi`: JD9365, 800×1280 portrait; the desk's landscape is the PPA's rotation in `device/dsi_panel.py`; backlight GPIO23, active-high, Python (`firmware/guition_jc8012p4a1c/modules/guition_p4_display.py`) | as the Waveshare | `guition_p4_display.ROTATION`'s 270°, counter-clockwise, in the raster | serial only: its GSL3680 needs a firmware upload and stays off the floor. With no input for 30 s the floor picks SAFE, never RETRY |

**The minimum, on every console**: the panel brought up, presented and lit
from C, one font, and serial. Serial is what every board shares: while the VM
is down the kernel drains the console's RX ring (the stop inventory's rule) and reads
`retry`, `safe` and `repl`, answers `state` with `"screen": "recovery"`, and
prints `KERNEL recovery reason=<r> sel=<choice> crc=<framebuffer crc32>` with
the lines it drew, every five seconds. That is also how the gate drives the
floor on a board with no input on it. A one-button board moves the highlight on
a press and picks on a one-second hold.

## 8. The embed entry

**Sprint 2 is where the kernel first owns `app_main`**, because a floor needs
code that runs when the VM cannot. `MICROPY_ESP_IDF_ENTRY` renames the port's
`app_main` (the embed decision of `docs/native_kernel_2026-09.md` §4.4, owner
2026-10-05), and the kernel's runs
instead:

1. the port's one-time startup (`nvs_flash_init`, the board's startup hook);
2. the crash intake (RTC record and reset reason into NVS);
3. the VM service task, created with `mp_task`'s stack size, priority and core;
   `app_main` returns, so the IDF main task's stack is freed as it is today.

The VM service is `mp_task` copied: one start per boot, the soft reset (Ctrl-D)
kept, no stop between apps (sprint 4 adds it). After the port's prelude, which
the floor's serial needs, it makes the boot decision (`moy_boot_decide`: START,
SAFE, REPL or the floor). Where the port would fall to the REPL after a failed
console boot, the service records the failure and restarts into the floor; a
missing first heap area lands there in the same boot instead of the port's
restart. The spike's call-list guard comes with it (`tools/mp_task_calls.py`):
the build fails when the pinned `MPY_TAG`'s `mp_task` call list differs from
`native/moy_kernel/mp_task_calls.txt`, the one the copy was reviewed against,
and `tests/test_mp_task_calls.py` holds the copy to that record.

The four consoles take the entry in `board.toml` as they take `moy_wasm`. The
Zero keeps the port's entry until sprint 3 makes the kernel the entry on every
target. The browser and the host have no entry: the web runner's worker and
CPython call the bindings, and `moy_crash.c` and `moy_recovery.c` build on the
host for the tests only. The three, the entry and the binding are one directory,
`native/moy_kernel/`, so a board takes or denies one module.

## 9. Internal SRAM against the kernel's share

The share (`docs/native_kernel_2026-09.md` §6.1) is at most 4 KiB net while the VM runs. ESTIMATED, by placement:

| what | where | internal SRAM |
|---|---|---|
| handle tables, registry rows, the back-stack, settings text, the NVS copy's buffer | PSRAM by rule (`moy_htab_host_alloc`) | 0 |
| the modules' statics (table pointers, the lease mask, boot state) | `.bss` | under 256 B |
| the panic wrapper | IRAM, carved from the same SRAM on the S3 | under 512 B |
| the crash record | RTC / LP memory | 0 (outside the internal heap) |
| the VM service task | replaces `mp_task`, same stack | 0 net |
| the recovery screen | runs only with the VM down, on the VM task's stack | 0 while the VM runs |

So about 1 KiB at most. The gate measures it with the `heapcaps` word on both
S3 boards with WiFi and BLE up, and the numbers are #224's.

## 10. The gate, as tests

| gate | host | on glass, every console board |
|---|---|---|
| a stale handle is refused loudly | `tests/test_moy_spine.py` gains the native binding through ctypes and the desktop MicroPython; the spine trace runs over the native module on both object models; the API-sequence and settings-scanner fuzzers under ASan and UBSan | `kstale` hands the spine a released, a forged, a wrong-kind and a zero handle and each answers `stale app handle`; the shared body `stale_handle_is_refused_loudly` (`tests/on_glass.py`) also asks the live app registry through `py`, and reads `impl=native` |
| a native crash is recorded and shown after reboot | `tests/test_moy_kernel.py`: the record's seal and crc through ctypes, a torn or foreign record read as none, the OPEN ids a boot forgets | `tools/kernel_gate.py BOARD crash`: `kcrash` arms a test id in the ledger and faults; after the reboot `state`'s `crash` names the id and the cause, and the notice is up |
| a VM that fails to start lands on the recovery screen | the same file: `moy_boot_decide` as a pure function, and the recovery raster rendered at each console's size and rotation and hashed against goldens | `tools/kernel_gate.py BOARD floor safe`: `kfail` arms a one-shot start failure in RTC memory; the floor says `KERNEL recovery reason=vm_start`, its framebuffer's crc is the host's render of the lines it printed, and `retry` and `safe` start the console as they say |

The two floor gates are a tool and not a suite body: each step reboots the
board, which an attach-only board's held-open session does not survive.

Plus every sprint's standing items (`docs/native_kernel_2026-09.md` §6): internal free and low-water on both S3s with WiFi and
BLE up, every image above its headroom floor, and the traces extended before
the crossing (the spine trace is).

## 11. What #160's open tail the spine takes

- **Correlating an unclean reset with the OPEN mark**: taken, as the crash
  record's id (§6).
- **The task-WDT subscribe with PANIC**: taken by the VM service (§6); its
  timeout is set from a measurement at the gate.
- **The launcher's broken-cart badge**: not the spine's. It is launcher chrome
  over `cart_broken`, and the launcher's place is sprint 4's decision.
- The per-role question was settled by `18215217` (two ledgers).
