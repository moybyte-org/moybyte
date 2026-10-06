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
| handle tables | `moy_spine.Table` | `+native/moy_spine/moy_htab.h` |
| app registry, back-stack, return records | `moy_spine.AppRegistry`, `BackStack`, `Returns` | `+native/moy_spine/moy_route.h` |
| WiFi leases | `moy_spine.Leases` | `moy_route.h` (one mask) |
| settings store | `moy_spine.Settings` under `runtime/system_store.py` | `+native/moy_spine/moy_settings.h` |
| strike ledger | `runtime/crash_guard.py` | `+native/moy_crash/moy_ledger.c` |
| crash record, boot-loop guard | — | `+native/moy_crash/moy_crash.h` |
| recovery screen | — | `+native/moy_recovery/moy_recovery.h` |
| the entry and the VM service | the port's `mp_task` | `+native/moy_kernel/moy_kernel.c` |
| `runtime/moybuf.py`'s registry | `moy_alloc`'s `moy_buf_live` | a `moy_htab` of kind BUF |

`native/moy_spine/` builds three ways, as `native/moy_index/` does: a
MicroPython usermod registered extensible (a `moy_spine.py` on the path wins,
so a build that leaves the native module out runs the twin), a host library
for ctypes, and a fuzz driver. Each crossing deletes the twin it replaces in the
same change (`docs/native_kernel_2026-09.md` §4.2).

**What stays Python after sprint 2**, by decision: the app OBJECTS and the
surfaces a route lands on (`console_spine.py`'s side of each verb); the radio
service the lease powers (`device/device_wifi.py`, sprint 3); the settings
dict callers alias (`ws.system`, now a mirror, §5); the Editor's project return
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

    moy_htab_t *moy_htab_new(uint8_t kind, uint16_t slots, size_t row_size);
    int  moy_htab_add(moy_htab_t *, uint32_t *h, void **row);  // FULL
    int  moy_htab_get(const moy_htab_t *, uint32_t h, void **row);  // STALE
    int  moy_htab_release(moy_htab_t *, uint32_t h);            // STALE
    uint32_t moy_htab_count(...), moy_htab_slots(...), moy_htab_at(..., slot);

Every byte comes from `moy_htab_host_alloc`, which the host defines: PSRAM
(`heap_caps_malloc(MALLOC_CAP_SPIRAM)`) on a board, malloc on the host, a
failure-injecting counter under the fuzzer. The binding maps STALE to
`StaleHandle` (a ValueError naming the table), FULL to `OSError(ENOSPC)` and a
non-int argument to TypeError, which is what the twin raises.

**The store index is this table.** `moy_index`'s handles are the same layout
with the kind field folded into a 12-bit slot, so when 1b crosses,
`native/moy_index/moy_index.c` keeps its ABI and its pinned values and takes
its slot bookkeeping from `moy_htab` with `slot_bits = 12` and no kind; its path
intern is a key map on top.

## 3. Routing: the registry, the back-stack and the return records

`moy_route` holds kinds, never objects: a kind (an app id or a back-stack kind)
is 1..15 bytes, stored in a `char[16]`. The binding hands a kind to Python as
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

**A key has one writer.** The ledger's rows (`app_guard`, `wallpaper_guard`)
are the kernel's. `SystemStore`'s dict is a mirror the console's sites keep
aliasing; `persist` pushes only the keys whose JSON differs from what the mirror
last read or wrote, so a kernel-owned row is never written back from a stale
copy (pinned: `tests/test_moy_spine.py`). The file write goes through the
Python SD gate until sprint 3 makes the gate native.

    int moy_settings_load(moy_settings_t *, const char *text, size_t len);  // BADJSON
    int moy_settings_get(const moy_settings_t *, const char *key, const char **json, size_t *len);
    int moy_settings_set(moy_settings_t *, const char *key, const char *json, size_t len);
    int moy_settings_delete(moy_settings_t *, const char *key);
    size_t moy_settings_dump(const moy_settings_t *, char *out, size_t cap);

The scanner reads untrusted bytes, so it is fuzzed under ASan and UBSan
against CPython's `json` as the model (the containment of
`docs/native_kernel_2026-09.md` §5).

## 6. The crash record and the strike ledger

**The record** is one 64-byte struct: magic, version, crc32, boot sequence,
`esp_reset_reason()`, uptime, the fault's PC, cause and address (Xtensa's
EXCCAUSE/EXCVADDR on the S3s, RISC-V's MCAUSE/MTVAL on the P4s), the faulting
task's name, the id the ledger held OPEN and its role, and the firmware build's
crc.

**Where it survives, per board.**

| board | survives the panic in | after the reboot |
|---|---|---|
| T-Deck, Guition S3 (ESP32-S3) | `RTC_NOINIT_ATTR` (RTC memory): panics, both watchdogs and software resets keep it; power-on and brownout do not | copied to NVS (namespace `moy_kernel`, key `crash`) before the VM starts, then the RTC copy cleared |
| Waveshare P4, Guition P4 (ESP32-P4) | `RTC_NOINIT_ATTR` in LP SRAM; the build static-asserts `SOC_RTC_MEM_SUPPORTED` | the same |

Every board's partition table has an `nvs` partition, so no table changes and
no core-dump partition (the headroom floors of `docs/native_kernel_2026-09.md`
§6.1 assume none on the S3s). A brownout or a
power-on leaves only `esp_reset_reason()`, which the intake records as such.

**Capture.** `-Wl,--wrap=esp_panic_handler`: the wrapper, in IRAM and touching
only RTC memory, fills the record and calls the real handler. That covers
panics, aborts and both watchdogs once they panic. A VM that dies without a
reset (an exception out of the console's boot, a `nlr_jump_fail`) is recorded
by the VM service with no reboot.

**A hang becomes a record** (#160): the VM service task subscribes to the task
watchdog with `CONFIG_ESP_TASK_WDT_PANIC=y`, and the console's frame feeds it
through the binding once per frame; the timeout is a `board.toml` value, set at
the gate from each board's worst legitimate frame (#66).

**Shown after the reboot.** On the next healthy boot the console's notice
banner reads `moy_crash.take()` once and names what was running and why it
stopped; the dev channel's `state` carries the last record as `crash`, and the
Settings diagnostics row shows it. The recovery screen (§7) shows it when the
console cannot come up.

**The ledger** is `CrashGuard`'s C twin over its two rows, the same keys and
shapes (`strikes`, `open`, `proven`), the same arm / frame / heal / forgive /
proof bracket the spine trace pins. Its armed id is mirrored into RTC memory at
`arm`, which is how the record names what was running.

**The boot-loop guard** is the same bracket around the console itself: the
boot sequence counts in RTC memory, the console calls `moy_kernel.boot_ok()`
after its first painted launcher frame, and three boots in a row without it
send the next boot to the recovery screen instead of the VM.

## 7. The recovery screen

**When**: the VM fails to start (the first heap area cannot be allocated, or an
exception leaves the port's boot script or the console's `main.py` before
`boot_ok`), or the boot-loop guard trips.

**What**: the record (cause, the app, uptime), the firmware label, and three
choices: RETRY (start the VM as usual), SAFE (start it with the settings rows
ignored: defaults, the wallpaper off, nothing auto-run) and REPL (start it with
`main.py` skipped, for a developer on serial).

**How it draws.** Its own raster, a few dozen lines: a filled rect and an 8×8
glyph from libmoy's compiled-in `moy_font_data`, at an integer scale, with a
rotation of 0/90/180/270 applied per pixel, into the panel module's own
framebuffer 0. No libmoy canvas state, no compositor, no PPA, no allocation:
the floor must not depend on what sprint 3 replaces. Each panel module gains
four C entry points beside its MicroPython binding, which keeps calling the same
bodies:

    void     moy_<panel>_kinit(void);        power, bus, panel init, as the binding's init()
    uint16_t *moy_<panel>_kfb(void);         framebuffer 0, panel-native size
    void     moy_<panel>_kpresent(void);     blocking: banded flush, or cache writeback on DPI
    void     moy_<panel>_kbacklight(int on);

| console | panel path today | what sprint 2 adds | rotation | input on the floor |
|---|---|---|---|---|
| T-Deck | `moy_lcd` (`firmware/lilygo_t_deck_plus_mainline/native/moy_lcd/`): the power rail, backlight, SPI2 and the ST7789 sequence are already C | the four entry points, factored out of `init`/`fb`/`show`/`backlight`; present is the blocking banded flush | none (320×240) | trackball click (GPIO0, polled); serial |
| Guition S3 | `moy_axs` (`firmware/guition_jc3248w535/native/moy_axs/`): QSPI AXS15231B, C | the stop spike's entry points (branch kernel-spike-v129, `486f843`), which drew the spike's frames with no VM, ungated from its build flag | the module's own rotated bands | the spike's C AXS15231 touch poll; serial |
| Waveshare P4 | `moy_dsi` (`native/p4/moy_dsi/`): EK79007, DPI from a PSRAM framebuffer, C; the backlight (GPIO32, active-low) is Python (`firmware/esp32_p4_wifi6_touch_lcd_7b/modules/p4_display.py`) | the entry points; present is a cache writeback; the backlight pin and polarity become board defines | none (1024×600) | BOOT button (GPIO35); serial |
| Guition P4 | `moy_dsi`: JD9365, 800×1280 portrait; the desk's landscape is the PPA's rotation in `device/dsi_panel.py`; backlight GPIO23, active-high, Python (`firmware/guition_jc8012p4a1c/modules/guition_p4_display.py`) | as the Waveshare | 90°, in the raster | serial only: its GSL3680 needs a firmware upload and stays off the floor. With no input for 30 s the floor picks SAFE, never RETRY |

**The minimum, on every console**: the panel brought up, presented and lit
from C, one font, and serial. Serial is what every board shares: while the VM
is down the kernel drains the console's RX ring (the stop inventory's rule) and reads
`retry`, `safe` and `repl`, and it prints `KERNEL recovery reason=<r>`. That
is also how the gate drives the floor on a board with no input on it.

## 8. The embed entry

**Sprint 2 is where the kernel first owns `app_main`**, because a floor needs
code that runs when the VM cannot. `MICROPY_ESP_IDF_ENTRY` renames the port's
`app_main` (the embed decision of `docs/native_kernel_2026-09.md` §4.4, owner
2026-10-05), and the kernel's runs
instead:

1. the port's one-time startup (`nvs_flash_init`, the board's startup hook);
2. the crash intake (RTC record and reset reason into NVS) and the boot-loop
   check;
3. the VM service task, created with `mp_task`'s stack size, priority and core;
   `app_main` returns, so the IDF main task's stack is freed as it is today.

The VM service runs `mp_task`'s body as the spike's embed lifecycle did: one
start per boot, the soft reset (Ctrl-D) kept, no stop between apps (sprint 4
adds it). Where the port would fall to the REPL after a failed console boot,
the service runs the recovery loop instead. The spike's call-list guard comes
with it (`+tools/mp_task_calls.py`): the build fails when the pinned `MPY_TAG`'s
`mp_task` call list differs from the one the service was reviewed against.

The four consoles take the entry in `board.toml` as they take `moy_wasm`. The
Zero keeps the port's entry until sprint 3 makes the kernel the entry on every
target. The browser and the host have no entry: the web runner's worker and
CPython call the bindings, and `moy_crash` and `moy_recovery` build on the host
for the tests only.

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

| gate | host | on glass (a shared body in `tests/on_glass.py`, every console board) |
|---|---|---|
| a stale handle is refused loudly | `tests/test_moy_spine.py` gains the native binding through ctypes and the desktop MicroPython; the spine trace runs over the native module on both object models; the API-sequence and settings-scanner fuzzers under ASan and UBSan | a stale app handle through `py` answers `stale app handle` |
| a native crash is recorded and shown after reboot | the record's encode, crc and NVS copy through ctypes over a fake RTC and NVS | a dev word arms a test id in the ledger and aborts; after the reboot `state`'s `crash` names the id and the cause, and the notice is up |
| a VM that fails to start lands on the recovery screen | the VM service's decision (boot sequence, `boot_ok`, start result to START, SAFE or RECOVERY) as a pure function through ctypes; the recovery raster rendered at each console's size and rotation and hashed against goldens | a dev word arms a one-shot start failure in RTC memory; after the reboot serial reads `KERNEL recovery reason=vm_start`, the framebuffer's hash matches the golden, and `retry` brings the console back |

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
