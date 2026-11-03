# The OS (2026-11): processes

Phase 1 of the order of work, and phase 3's asyncio, restart policies and
mini-VM spike. Read `docs/os_2026-11.md` first.

---

## 1. Processes

A **process** is a program the OS runs: a cart `id` (#162), a tier, a grant, a
row of a process table (a new handle kind, `MOY_KIND_PROC`, beside
`native/moy_spine/moy_htab.h`'s kinds), and a lifecycle the kernel drives.
The process row is **not** an OWNER row: a VM stop ends every OWNER row, and
the grant table was kept off OWNER for that reason (`native/moy_app/README.md`,
"The grant"). A process row holds an OWNER for its run's loans (canvases,
audio sessions, leases); the row itself, like a grant, outlives a stop.

| kind | canvas | runs when | ends when | exists today as |
|---|---|---|---|---|
| **app** (a game, a tool, a system app) | yes | while its window is open: focused, visible-unfocused (throttled) or minimised (`update` at a reduced rate, no `draw`, owner 2026-11-03) | the user closes it, it quits, it crashes | the Player's run, a registered Layer |
| **wallpaper** | yes, behind everything | self-started with the desk, never focused | the desk closes or a wallpaper is chosen | the wallpaper cart (its own ledger role, its own audio session) |
| **command** | no; it writes to its stdout, a VT100 stream the terminal renders (`system.md` §1) | while the terminal runs it; backgrounded with `&` it is a background process whose output goes to the log | it returns an exit code | nothing |
| **service** | no | from start until stopped, whatever is on screen | the user stops it, it exits, it is killed under memory pressure, it strikes out | nothing user-written |
| **OS service** | no | always | never | the input task, the radios, the audio feeder, the WiFi station |

**The shell is process 1**: the Python console (`runtime/shell/console.py`,
the window managers), where every shipped app runs. It is privileged by
holding the grants the policy marks `never` (`native/moy_app/roles.json`:
`carts`, `install`, `wallpaper`, `artwork`, `damage`, `surface`, `ui`, and `notify`
until it becomes the achievements process's API, §8).

**Several apps, games included, run at once (owner, 2026-11-03).** The user
chooses what runs and sees what it costs (§6: "it's the user's choice; we
tell the user the CPU and RAM usage so he can close apps if they get slow").
Focus and running are separate properties. **Focus** is the window manager's
(`docs/shell_ux_v1.md` §3): the focused window alone gets input and names
the audible audio session (`native/moy_audio/moy_aud.h`). **Running** is the
process table's, and a window is in one of three states:

- **focused**: frames at the cart's rate, as today;
- **visible, unfocused**: the game keeps running at a lower rate. The rule
  (sketch): its `update` is stepped every frame with a true `dt`, its `draw`
  every Nth frame, N from the shell's measured cost of the focused window
  (2 when the focused frame has slack, rising to the cart's declared floor
  otherwise), and the task manager calls it *throttled*;
- **minimised** (owner, 2026-11-03): `update` keeps being stepped at a
  reduced rate with a true `dt`, `draw` never runs, and the app receives
  minimise and restore events. The default game template pauses itself on
  minimise, so a game written from it is held as the owner's 2026-10-19
  call says; an app that wants to keep working (a player, a download) does.

On a handheld (one app fullscreen, a back-stack) an app not in front is
minimised, so the playing cart owns every draw there and a minimised app
costs only its reduced-rate `update`.

What this changes in `docs/surface_model_v1.md` §14.1, the focused-window
content freeze: nothing in the mechanism. The freeze is a *skip-draw* for a
window whose content provably did not change; a throttled game is a Class B
animating surface on the frames its `draw` runs and a frozen one on the
frames it does not, so it is marked dirty by the shell on its draw frames
and presents its retained `win.buf` on the others, through the one
invalidation path. No second mechanism.

A service runs regardless, has no window, and shows itself through kernel
chrome and the task manager (§6). Two owner calls stay intact: sound
stops when its process exits (a media stream lives exactly as long as its
process: closing the app ends it, minimising keeps it, owner 2026-11-03),
and no app gains a way to come back after a VM stop (owner,
2026-10-10).

**Every process has a pid, a state and an exit code.** The pid is its
process-table slot and generation; the states are `running`, `throttled`,
`minimised`, `paused` (held across a VM stop, §5), `background`,
`suspended` (an overrun's last rung, §3),
`stopping` and `exited` (with the code the task
manager and `ps` show); a stop is TERM (the `moy_stop`/`close` hook) and, after
`MOY_SERVICE_STOP_MS`, KILL (§5), the same for an app, a command and a
service.

**Lifecycle verbs:** `open`/`close` for an app (`docs/shell_ux_v1.md` §11),
`main(argv) → exit code` for a command, `moy_start`/`moy_stop` for a
service on every tier that may be one (`tiers.md`), with `moy_stop` the hook
under which a service persists what a kill would lose (a LoRaWAN frame
counter). A crash is an exit, recorded in the strike ledger (§10).

**The tick model.** The foreground app keeps frames: `update(dt)`/`draw()`
as today; a minimised app keeps a reduced-rate `update` (above). **A background process, on every tier, never gets a per-frame
tick.** It declares its wake sources and sleeps between them: timers
(`moybyte.every(ms, fn)`, `moybyte.after(ms, fn)`) and events
(`moybyte.on(source, fn)`: a UART, a socket readable, a pin edge, a LoRa
packet), each fed from the C rings of `hardware.md` §2, so nothing is lost
and a record keeps its real timestamp. An idle process costs nothing, the
idle ladder can still dim and sleep (§5), and it is one model for Python,
Lua, wasm's `moy_timer` and ELF. A timer means "no earlier than"; jitter of up
to a frame is normal, and precise timing belongs to C, the wasm lanes and the
hardware engines. Python's `asyncio` rides the same scheduler (§3);
`every`/`after`/`on` stay as the simple form.

**Where processes run.** A Lua, wasm or ELF process runs on a task the
kernel owns; its callbacks run on that task. A Python process runs inside
the shell's one VM (§3): its callbacks run in the shell loop's slack after
a frame is presented and before the next. Services run on core 0 on the P4s;
what the S3s get is the overview's §5.

## 2. The app manifest and the permission table

Every process comes from a `.moy`; `manifest.json`'s `"moybyte"` object
carries Moybyte's fields (`docs/moy_cart_api.md`, `docs/app_api_v1.md`). The
OS reads:

- **the tier**: moy-spec's `"runtime"` for `python`, `lua`, `wasm`; `elf` is
  a Moybyte-side field (`"moybyte": {"native": "esp32s3"}`), since the spec
  does not know the tier;
- **the kind**: `"type"` — `game` (default), `app`, `command`, `service`, or
  **several entry points** sharing one grant and one prefs namespace
  (`"entries": {"app": "...", "service": "..."}`): the LoRa node and
  its radio app are one cart;
- **permissions**: names from the one table (below): a role (`files:documents`,
  `prefs`, `appearance`, `launch`, `clipboard`), the ungated floor
  (`graphics`, `input`), a device API (`mic`, `battery`, `brightness`,
  `alarms` (`between_apps.md` §3), `gps`, `lora`, `ble`, `usb:hid`, `midi`,
  `camera`, `http`, `network`, `multiplayer`, `media`, `media:url`,
  `notifications` §7), `autostart` (§5), a lease (`lease:<resource>` naming a
  row of the board's resource table, `hardware.md` §1), or `native`. A
  permission may be marked
  `required`: revoked, it makes the launch refused with a message naming
  it, where any other revoked permission is absent (`None`);
- **background**: a service's one-line reason the OS shows ("LoRa node"),
  and `"lanes"` naming the timed lanes a wasm or ELF process exports
  (`hardware.md` §2).

**Enforcement is at bind time, not per call.** A role is checked when the
grant is made and when a wasm module's imports are walked at load
(`native/moy_app/moy_app_wasm.c`, `moy_app_wasm_admit`); a Python app's
namespace holds only the granted names (`runtime/shell/system_api.py`). No
draw verb or input read checks a grant. For **Lua and wasm** an ungranted
capability has no name in the process. For **Python** the manifest is
legible, not binding: a Python cart runs inside process 1's VM with the real
builtins and can reach `machine` and the `never` roles (`system_api.py`: "NOT
a sandbox"); the import scanner rejecting illegal imports at install and at
PLAY is a lint (`__import__`, `exec`, `getattr` walk past it), and there is
no boundary (§3). For **ELF** permissions
are informational (`tiers.md` §1). Where the manifest does not bind, the
**install prompt** does the telling (the overview's §1).

A permission naming a class or resource the board lacks is refused **at
install**, naming the board, and **re-checked at launch** (a card moves
between boards; an update may drop a class). **Install is all or nothing
(owner, 2026-11-03)**: accepting the prompt installs the app with every
permission it lists, denying installs nothing. Any single permission can be
revoked later in Settings, and from the next launch the app sees it absent
(`None`), or is refused with a message if it marked the permission
`required`. An update that adds a permission prompts again, all or nothing.

**The permission table (sketch).** One JSON source in `native/moy_app/`, each
row: the name, what it grants, the tiers that may hold it, what its line of
the install prompt says (its own name, or `"full"`: "full access to the
console", the overview's §4), the board class it needs, whether it is
**enforced** on that tier or informational (above).

```json
{"perm": "graphics",   "grants": "floor",      "tiers": ["python","lua","wasm","elf"]},
{"perm": "files:documents", "grants": "role:files", "tiers": ["python","lua","wasm","elf"]},
{"perm": "autostart",  "grants": "boot",       "tiers": ["python","lua","wasm","elf"]},
{"perm": "mic",        "grants": "api:mic",    "tiers": ["python","lua","wasm","elf"], "needs": "class:mic"},
{"perm": "pins",       "grants": "lease:gpio:digital", "tiers": ["python","lua","wasm","elf"]},
{"perm": "lease:uart", "grants": "lease:uart", "tiers": ["python","wasm","elf"], "prompt": "full"},
{"perm": "notifications", "grants": "api:notify", "tiers": ["python","lua","wasm","elf"]},
{"perm": "native",     "grants": "elf",        "tiers": ["elf"], "prompt": "full", "enforced": false}
```

## 3. Python: one VM, stepped cooperatively

**Every Python app runs inside the shell's one MicroPython VM, on every board
(owner, 2026-11-03).** Several Python apps, foreground and
background, are stepped cooperatively by the shell; there is no second
instance and no state swap, and "embed, not fork"
(`native/moy_kernel/moy_kernel.h`) holds; the spike's evidence against an
instance per process is #246's comment of 2026-11-03. What this costs,
written down: **no memory isolation**; the apps' live
objects lengthen the shell's GC; a stop relies on the shell dropping every
reference; a runaway callback holds the shell until the abort lands,
which can be one GC pause after its budget, and a callback blocked inside
C with no pending check (a socket read without a timeout, a lock wait) is
never aborted. Python stays trusted under per-app consent (the overview's
§1).

**The scheduler**: a background
Python app's callbacks (§1) run in the loop's slack after present, each
under a **per-callback slice** the shell measures, overruns going warning
→ strike → suspend; a callback that never returns gets a timer-driven
`mp_sched_vm_abort` while the shell survives (P1.1 proved it on the
T-Deck and a P4, 2026-11-03, #246). The mechanism is a guard around each
callback: its own abort point, a one-shot timer that requests the abort,
the outer point, the gc lock and the scheduler restored after; it runs on
the main thread only, and every console board builds with
`MICROPY_ENABLE_VM_ABORT`. An abort skips `finally` and `__exit__`, so a
lock the callback took stays held; an aborted callback is a strike, and
the ladder's slice is sized to land after a GC pause. On a
board with the VM stop, Python background apps **pause** while the VM is
stopped and resume with it (§5; the mini-VM spike, P3.8, may keep
them running). The hardening rung is the overview's §4.

**`asyncio`** is one event loop the shell owns on this scheduler: a
task's step is a callback under the same slice, `await asyncio.sleep(s)`
or `asyncio.sleep_ms(ms)` a timer wake, an asyncio stream over a UART or
a socket an event wake. **Standard names first**: `open()` on tree paths,
`os`, `time.ticks_us`/`time.time`, `socket`, `requests` and `logging`
from micropython-lib (`logging` routed to the OS log, §9); `moybyte.*`
only for what is genuinely ours (below, and `hardware.md` §4).

**Threads (owner, 2026-11-03)**: a Python app may use `_thread`. Its
threads share the one VM and its GIL, so one runs Python at a time and
every GC stops them all: a thread's timing depends on the rest of Python,
and a stall can last as long as a collection (#246 holds the pause
figures). The shell creates an app's threads through a registering entry,
so each counts toward its app's process in the task manager, and closing
the app ends them with a **per-thread kill**: an exception the thread
cannot catch, delivered at its next pending check. The VM abort never
reaches a thread that is not the main one (P1.1), and upstream MicroPython
has no thread kill; a thread blocked inside C ends only at the VM stop,
which deletes the VM's threads. For audio, a thread or the loop renders ahead into the ring at a
depth greater than a GC pause (`hardware.md` §2).

**Verbs (sketch).** The standard names, Python's (each tier has the same by
its own standard: POSIX, WASI, Lua `io`/`os`, `tiers.md`), and Moybyte's
own for what this area serves:

```
time.ticks_us() -> int                 wrapping microseconds, the kernel's clock
time.time() -> int                     wall time; moybyte.clock_synced() says whether NTP answered (between_apps.md §3)
open(path, mode)                       a tree path; refused outside the app's grants
os.listdir / stat / remove / mkdir     the same
socket.socket(...)                     lwIP's, under "network"
requests.get(url)                      micropython-lib over socket; non-blocking use rides asyncio
asyncio.sleep(s) / sleep_ms(ms) / StreamReader over a UART or a socket
machine.UART / I2C / SPI / Pin / PWM / ADC   handed back by a lease (hardware.md §1), invalid after the sweep
print(...) / input()                   the process's stdio; print also reaches the log (§9)
logging.getLogger(name).info(...)      micropython-lib logging, routed to the OS log (§9)
moybyte.every(ms, fn) / after(ms, fn) / on(source, fn) / at(t, fn)   wake sources (§1); at needs "alarms"
moybyte.notify(summary, body, icon=None, actions=(), urgency=1, replaces_id=0) -> id   §7
moybyte.log(severity, text)            the OS log, for Lua and wasm (§9)
```

## 4. The guaranteed background slice

A foreground game running unpaced (`"fps": "free"`, `docs/moy_cart_api.md`)
leaves no slack after present, so background callbacks would starve. The
scheduler gives background work a **small guaranteed slice** every frame, or
every N frames, taken before the next frame starts: configuration, sketched
as `MOY_BG_SLICE_US` and `MOY_BG_SLICE_EVERY` in the shell's loop. A paced
game pays nothing extra, since the slice comes out of its idle; an unpaced
game's lost time shows in the task manager as the slice's cost (§6).

## 5. Service lifecycle

A service sleeps on its declared wake sources (§1), and the idle ladder
(`native/moy_kernel/moy_idle.h`) treats a running service as activity for
those sources only. Across a VM stop (the S3s) an OS service (and, on a
board that runs them, a Lua, wasm or ELF service) keeps running; a Python
background app pauses with the VM and resumes with it, its timers and events
delivered then with their real timestamps (owner, 2026-11-03). The mini-VM
spike (P3.8) tries more on the S3s: when a big cart stops the VM, a
**small MicroPython VM** (a few hundred KB of heap) starts in its place, one
VM at a time and in sequence through the stop/start machinery
(`native/moy_kernel/moy_kernel.h`'s stop order), never concurrent, which
avoids #246's variant-B failure. Background Python apps restart in it from
the state their `moy_stop` persisted (`moy_start`), so they keep running
through the big cart; Python-served commands stay refused, their servers
being the whole shell. Its heap comes out of the big cart's share, so it
runs only if the cart's declared need leaves room; otherwise Python pauses.
If the spike finds it too complex, it is dropped and "Python pauses" stands. A service that is killed under
memory pressure (the threshold from the shares) gets its `moy_stop` hook
first, and what it persists there is its contract.

**Services are managed like an init system (owner, 2026-11-03).** The
process table holds, per service, its **autostart** state (a permission,
`autostart`, owner 2026-11-03: an app holding it starts at boot by default,
and the user can turn that off in Settings), its **restart
policy** (`never`, `on-crash`, `always`, with a back-off and the strike
ledger's cap; P3.6) and its log lines under its process id (§9). `service
start/stop/status/restart` and the task manager are the two faces of one
implementation. The `moy_stop` hook is the TERM; a service still running after
`MOY_SERVICE_STOP_MS` (configuration) gets the KILL: its task deleted, its
OWNER swept, the ledger told.

## 6. The task manager and admission

**The task manager** is a system app listing every process with its state
(running, throttled, minimised, paused, background, suspended), its CPU, its memory and a Close
button. CPU is **exact**: the shell times each app's `update`, `draw` and
callbacks. Memory is **exact for Lua and wasm**, read from their own heaps,
and **approximate for Python**, counted as the allocations made during the
app's own calls and labelled so, because one shared VM cannot attribute
ownership. Background apps and services are rows too, beside the bar's
indicator and its stop control (owner, 2026-11-03).

**Admission.** An app that does not fit THE SHARES (`native/moy_play/moy_play.h`)
beside what is already running is **refused with a message naming what to
close**; nothing degrades silently. On the S3s two big games do not fit, and
the message says which one to close. The fit check is the Player's today,
extended to count the running set.

**Sessions.** Lua and wasm run one session at a time today
(`native/moycore`, `native/moy_wasm`), so several Lua or wasm apps at once
arrive with the process tables (phase 5); Python, stepped by the shell in
its one VM, gets several first. Audio has `MOY_AUD_SESSIONS` (4) rows: a
fifth source is refused with the same message, and the focused app's session
has priority.

## 7. Notifications

**Decision (owner, 2026-11-03): app notifications, as on Android.** The verb
is `moybyte.notify(summary, body, icon, actions, urgency, replaces_id)`,
the freedesktop notification fields (owner, 2026-11-03), behind a **`notifications`**
permission granted at install (§2). The name is deliberately not
`notify`: today's `notify` ROLE (`native/moy_app/roles.json`) is the
achievements surface, stays ungrantable, and becomes the achievements
process's API (§8), so the two never collide.

- **The OS draws the banner**, over whatever is running, games included:
  kernel chrome where the kernel draws (the toast and banner state of
  `native/moy_play/moy_chrome.h`), shell chrome on the desk. The foreground
  app never receives the notification or its contents.
- **Acting on it** focuses the posting app; a game that was in front loses
  focus (throttled on the desk, minimised on a handheld, §1). **Dismissing** it keeps the game playing.
- **Missed notifications** are a list, in the bar or the task manager.
- **There is no do-not-disturb (owner):** a user who wants no interruptions
  closes the app that posts them.

The motivating case: a background mesh app wakes on `moybyte.on("lora", fn)`
while a game plays, and posts.

## 8. Achievements, the first background process

**Decision (owner, 2026-11-03): achievements become a small background
process**, not part of the shell. Today the core is
`runtime/toolkit/widgets.py`'s `Achievements` over its `ACHIEVEMENTS` table,
wired by `runtime/shell/console_notices.py`'s `load_achievements`, served to
apps by `NotifyServer` in `runtime/shell/shell_servers.py` (`ws.ach`), stored
as `achievements.json` by `runtime/carts/moy_docs.py`, drawn by
`runtime/system/achievements_ui.py` (Settings' page and the eggs) and
announced by the kernel's toast.

What moves: `Achievements`, `load_achievements` and `NotifyServer` leave the
shell for a system cart, `+system_carts/moybyte.achievements.moy`, a Python
background process (§3) that **wakes only on events**: a cart's
`achieve(id)` becomes an event to it (`moybyte.on("achieve", fn)`, an
OS-routed message, not a ring record), with no per-frame cost; it records into its own store (the same document, now the
process's) and posts through the notification service. The `notify` role's
verbs (`achieve`, `award`, `count`, `has`, `found_on`, `progress`, `today`)
become that process's API: in `roles.json` their `server` changes from
`shell` to the process (a server value naming a process, sketch
`"proc:achievements"`), the grant stays `never` for apps, and Settings'
Achievements page reads them through the role as it does now. The toast's
words stay the kernel's. It is the first user of background processes,
events, notifications and the task manager on every board, and it is
testable with no LoRa.

## 9. The system log and crash reports

Today's diagnostics are the kernel's crash record
(`native/moy_kernel/moy_crash.h`), the strike ledger
(`native/moy_spine/moy_ledger.h`), the Player's panel
(`runtime/run/player.py`), `print` to serial and the HITCH and LOOP lines
(`native/moy_kernel/moy_loop.h`). None of it is addressed to an app's author.

**The design: one OS log**, a ring in PSRAM (`native/moy_kernel/moy_log.h`)
of records `(t_us, severity, process id, text)`, the **syslog severities**
(`emerg` … `debug`, RFC 5424; owner, 2026-11-03), a crash written at
`crit`. Writers: the kernel's diag lines; a process's `print` and its log
calls (micropython-lib `logging` for Python, `moybyte.log` for Lua and
wasm), routed to the ring under its process id (and still mirrored to
serial); the crash intake, with the
record's text; the Player's panel text and a Python app's **traceback**,
under the app's process id. Readers: the terminal's `log` command (as
`dmesg`/`logcat`; `log -f`, `log <process>`), and before the terminal
exists a dev-channel word `log` and a desk viewer. **A crash shows itself
to its author**: the task manager's row offers "this app crashed: details"
(the traceback from the ring), and a background app's crash posts a
notification (§7) that opens it. Developers need this from day one, so
it is early in phase 1.

## 10. Crash attribution

The ledger arms one id per role in RTC memory and heals after painted frames
(`native/moy_kernel/moy_crash.h`, `native/moy_spine/moy_ledger.h`); a
service paints nothing, and with several processes alive a crash would strike
whoever was armed. The design: the crash record carries **the running task's
process id**, a service heals after N callbacks or T seconds (configuration,
in the binding), an ELF's strikes key by content hash (`tiers.md` §1). Memory
corruption by an ELF striking a bystander is accepted as the tier's trust
level. **A native crash writes an ESP-IDF core dump to flash** (owner,
2026-11-03), readable with gdb from the SDK (`tiers.md` §1); the images build
with `CONFIG_ESP_COREDUMP_ENABLE_TO_NONE` today, and the `coredump`
partition's cost is checked against `headroom_floor_kib` in phase 5. Python
keeps its tracebacks (§9).

## 11. How it maps onto what exists

| design piece | today | what changes |
|---|---|---|
| process identity | the cart `id`; OWNER rows (`native/moy_spine/moy_htab.h`); one run (`native/moy_play`) | a PROC kind that outlives a VM stop and holds an OWNER for its loans |
| the grant | `native/moy_app/moy_app.c`'s table, keyed by id, not an OWNER row | unchanged in shape; carries device-API and lease bits |
| the permission table | `roles.json` + `PERMS` in C; `network`/`multiplayer`/`pins` gated in `runtime/run/cart_api.py` | **one table** (§2), C-owned, read by the Player, the wasm adapter, the Lua binding, the ELF loader, the installer and the simulator |
| the update screen | the updater's; firmware update is never a role (`runtime/shell/system_api.py`) | **kernel chrome** (owner, 2026-11-01, option b): the precedent for a service's indicator |
