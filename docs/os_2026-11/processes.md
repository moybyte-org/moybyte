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
| **app** (a game, a tool, a system app) | yes | while its window is open: focused, visible-unfocused (throttled) or minimised (a game paused, an app's `update` at full rate with no `draw`, owner 2026-11-03) | the user closes it, it quits, it crashes | the Player's run, a registered Layer |
| **wallpaper** | yes, behind everything | self-started with the desk, never focused | the desk closes or a wallpaper is chosen | the wallpaper cart (its own ledger role, its own audio session) |
| **command** | no; it writes to its stdout, a VT100 stream the terminal renders (`system.md` §1) | while the terminal runs it; backgrounded with `&` it is a background process whose output goes to the log | it returns an exit code | nothing |
| **service** | no | from start until stopped, whatever is on screen | the user stops it, it exits, it is killed under memory pressure, it strikes out | nothing user-written |
| **OS service** | no | always | never | the input task, the radios, the audio feeder, the WiFi station |

**The shell is process 1**: the Python console (`runtime/shell/console.py`,
the window managers), where every shipped app runs. It is privileged by
holding the grants the policy marks `never` (`native/moy_app/roles.json`:
`carts`, `install`, `wallpaper`, `artwork`, `damage`, `surface` and `ui`; `notify`,
also `never`, is the achievements process's API, §8).

**Several apps, games included, run at once (owner, 2026-11-03).** The user
chooses what runs and sees what it costs (§6: "it's the user's choice; we
tell the user the CPU and RAM usage so he can close apps if they get slow").
Focus and running are separate properties. **Focus** is the window manager's
(`docs/shell_ux_v1.md` §3): the focused window alone gets input and names
the audible audio session (`native/moy_audio/moy_aud.h`). **Running** is the
running set's (`runtime/shell/procs.py`, built 2026-11-03 for Python games
on the desk): the FRONT run is the game whose world the Workstation holds,
in the WM's `desktop` window, and every other running game is a Proc with
its world held aside -- its Player, Project, audio session, owner tag and
game canvas -- in a window keyed `run:<n>`. Running one is installing its
world (an exchange of those fields, done twice to put the front back), so
the Player and the cart API run whichever world is installed. Focusing a
Proc's window PROMOTES it: the two worlds trade places and the two windows
their keys, so the focused game is always the front one and gets the keys,
the pointer, the audible session and the pause menu. A Proc reads no input
(its input verbs answer nothing), keeps no edges and pulls no mix. A game
window is in one of three states:

- **running**: the front game, frames at the cart's rate as before -- also
  beside a focused app window, where its canvas has no last frame to show
  between draws (the desk's live wallpaper painted on it until the first
  Proc moved the wallpaper onto a canvas of its own, `Wallpaper.rehome`);
- **throttled**: a Proc whose window shows. Its `update` runs at its own
  rate with a true `dt` (a paced game's scheduler keeps its tick rate), its
  `draw` on every Nth of its draw frames: N is 2 while the loop keeps the
  front's tick period, and rises to `THROTTLE_MAX` (4) while the loop runs
  more than 15% over it, moving at most every 500 ms. Between draws its
  window presents its canvas's last frame;
- **minimised** (owner, 2026-11-03): a window hidden to its chip, under a
  full-screen window, or parked by console mode, and every run but the
  front one on a handheld. A **game** is paused: no `update`, no `draw`, its
  clock held as the pause menu holds it (`Player.pause`; a Proc's clock
  alone, its session not being the audible one). An **app** (`"type":
  "app"`) keeps its `update` every loop frame with the true `dt` and never
  draws, so a player or a download keeps working. Either hears
  `_minimise()` going in and `_restore()` coming out, functions of its
  namespace beside `_update` (Python, this phase; `Player.window_event`).
  A restore gives the run back as it was, with no pause card.

On a handheld (one app fullscreen, a back-stack) the running set keeps no
windows, and every run but the front one is minimised, so the playing cart
owns every draw. The pause card's Minimise (`Procs.minimise`) holds the
front game in the running set and goes home (a launch while a run plays
replaces it, as before); launching a running cart again, or the task
manager's Switch to, brings it back (`Procs.switch`), the run that played
joining the set. Every world there
draws on the one glass, so a handheld's worlds share the system canvas
rather than each making one. Admission is the desk's, over the S3s' 4 MiB
share.

What this changes in `docs/surface_model_v1.md` §14.1, the focused-window
content freeze: nothing in the mechanism. The freeze is a *skip-draw* for a
window whose content provably did not change; a game window has no
`win.buf`, its content is its canvas composited, as the front's always was.
A throttled game draws into its own canvas when its `draw` runs, and each
painted frame that canvas is owed to (one per retained buffer, `Proc.owed`)
composites it; the quiet frame's partial stack (`_PlayerWindowLayer`)
carries the running set's windows beside the front's while no window
overlaps a game's. `tests/test_wm_content_freeze.py` pins the focused
window's freeze with a throttled game beside it. No second mechanism.

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
as today; a minimised app keeps its `update` and a minimised game is paused (above). **A background process, on every tier, never gets a per-frame
tick.** It declares its wake sources and sleeps between them: timers
(`moybyte.every(ms, fn)`, `moybyte.after(ms, fn)`) and events
(`moybyte.on(source, fn)`: a UART, a socket readable, a pin edge, a LoRa
packet). Events are read from the C rings of `hardware.md` §2, so nothing is
lost and a record keeps its real timestamp; timers are deadlines on
`ticks_ms` in the wake registry (`runtime/system/moybyte.py`). An idle process costs nothing, the
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
- **permissions**: names from the one table (below): a role (`files:docs`,
  `prefs`, `appearance`, `launch`, `clipboard`), the ungated floor
  (`graphics`, `input`, `audio`), a device API (`mic`, `battery`, `brightness`,
  `alarms` (`between_apps.md` §3), `gps`, `lora`, `ble`, `usb:hid`, `midi`,
  `camera`, `http`, `network`, `multiplayer`, `media`, `media:url`,
  `notifications` §7), `autostart` (§5), a lease (`lease:<resource>` naming a
  row of the board's resource table, `hardware.md` §1), or `native`. A
  permission the manifest also lists in `"moybyte": {"required": [...]}`
  is required: absent, it makes the launch refused with a message naming
  it, where any other absent permission reads `None`;
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

A permission is **absent** for a run when Settings turned it off, when an
update added it and nobody consented, when the console lacks the service it
needs, or when the cart's tier may not hold it. Turned off or unconsented,
its names read `None`; a permission naming what this console has not (`pins`
on a board without them) installs and leaves no name, as the board has no
verb, and only a `required` one is refused, **at install** naming the board
and **again at launch** (a card moves between boards; an update may drop a
class). **Install is all or
nothing (owner, 2026-11-03)**: accepting the prompt installs the app with
every permission it lists, denying installs nothing. Any single permission
can be turned off later in Settings (About → App permissions,
`runtime/system/perms_ui.py`), and from the next launch the app sees it
absent, or is refused with a message if it requires it. An update that adds
a permission prompts again, all or nothing; one that adds none, and a cart
that asks for nothing a game does not, install unasked.

**Consent is recorded with the grant's key**: the console's `system.json`
holds one record per app id (`moy_app.id_for`, what the grant, the prefs
namespace and the crash strikes are keyed by), the permissions consented
at install and the ones turned off since (`runtime/shell/app_perms.py`,
`Consent`). A cart with no record (written in the Editor, a seed, a cart
copied onto the card by hand) never prompts and runs on its manifest, less
what Settings turned off. Get Carts asks once every file of the download is
checked and before anything moves onto the shelf, since a carts index
carries no manifest (`cart_index.Install`'s `ask`); a denied install
removes the build, and Remove forgets the record.

**The permission table.** One JSON source, `native/moy_app/roles.json`'s
`"permissions"` beside the role rows, restated as `app_perms.TABLE` (held
equal to it, and its `role:` rows to the C policy's allowlist, by
`tests/test_app_perms.py`), which the prompt, the install check, the launch
check and Settings all read. Each row: the name (`files` also matches
`files:<kind>`), what it grants (`floor`, `role:<role>`, `api:<service>`,
`boot`, or `marker`: a shipped app's claim on its cart, granting nothing),
the tiers that may hold it, its line of the install prompt (None for the
floor and the markers: nothing to consent to and nothing to turn off), the
console service it needs (`wifi`, `net`, `gpio`), and the names a Python
cart's namespace holds while it is granted, each `None` while it is turned
off.
The rows Python holds in phase 1 are the floor, the roles, `network`,
`multiplayer`, `pins`, `console`, `notifications`, `autostart` and the
markers (`notifications` with no names: its verb lives on the shared
`moybyte` module and checks the poster's grant, §7); a Python app's prompt also says it is a trusted app. The later
phases' rows (`mic` and the other device APIs, the leases, `native`) arrive
with their APIs, and with them a prompt line of `"full"` ("full access to
the console", the overview's §4) and whether the row is enforced on its
tier or informational (above).

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

**The scheduler** (`runtime/system/scheduler.py`, whose header is the
authority): every process's wake callbacks (§1) run once a frame after
the console's frame, in the kernel loop's tail (the service upcall, its
bit set while any wake is registered) on a board and after the frame on the
host and in the browser, within the frame's slack and the guaranteed slice
(§4). Each runs as its process (the OS log's current pid) under a
**per-callback slice**: a callback over `MOY_BG_CALLBACK_US` (20 ms,
measured less the collections inside it) is an overrun, and overruns go
warning → strike → suspend (the first a warning, every later one a strike,
`MOY_BG_STRIKES`, 3, suspend the process, whose wakes then stay unserved
until it is resumed); the shell is never struck. A callback that never
returns gets a timer-driven `mp_sched_vm_abort` while the shell survives
(P1.1 proved it on the T-Deck and a P4, 2026-11-03, #246): the guard
(`native/moy_kernel/modmoy_guard.c`, `moy_guard.call`) is its own abort
point and a one-shot timer of `MOY_BG_ABORT_US` (300 ms, past the longest
collection #246 measured) that requests the abort, and it puts back the
outer point, the globals, the gc lock depth and the scheduler's lock. The
abort is armed on the main thread only; every console board's
`mpconfigboard.h` and both unix builds turn on `MICROPY_ENABLE_VM_ABORT`,
while the browser's VM has none and the Zero runs no scheduler. An abort
skips `finally` and `__exit__`, so a lock the callback took stays held; an
aborted callback is a strike. A callback's exception is a crash (§10). On
a board with the VM stop, Python services are stopped before the stop and
started again after it (§5; the mini-VM spike, P3.8, may keep them
running). The hardening rung is the overview's §4.

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
print(...) / input()                   a script's stdio; its print also reaches the log (§9); a cart's print draws
logging.getLogger(name).info(...)      micropython-lib logging, routed to the OS log (§9)
moybyte.every(ms, fn) / after(ms, fn) / on(source, fn) / at(t, fn)   wake sources (§1); at needs "alarms"
moybyte.notify(summary, body, icon=None, actions=(), urgency=1, replaces_id=0) -> id   §7
moybyte.log(severity, text)            the OS log, for Lua and wasm (§9)
```

## 4. The guaranteed background slice

A foreground game running unpaced (`"fps": "free"`, `docs/moy_cart_api.md`)
leaves no slack after present, so background callbacks would starve. The
scheduler's budget each frame is the frame's slack (`moy_loop.slack()`, what
the pace would still sleep; none while the Player paces a game) and at least
a **guaranteed slice**, `MOY_BG_SLICE_US` (1000 µs) every
`MOY_BG_SLICE_EVERY` (1) frames: configuration in
`runtime/system/scheduler.py`. A callback is never split, so the first one
due runs whatever the budget, and what the budget left runs first on the
next frame. The slice is a ceiling, not a spend: with no wake due it costs
the registry's scan, and with none registered nothing at all. A paced
game's loop has time between its ticks, which the slice takes; an unpaced
game's lost time shows in the task manager as the slice's cost (§6).

## 5. Service lifecycle

A service sleeps on its declared wake sources (§1), and the idle ladder
(`native/moy_kernel/moy_idle.h`) treats a running service as activity for
those sources only. Across a VM stop (the S3s) an OS service (and, on a
board that runs them, a Lua, wasm or ELF service) keeps running. A Python
service cannot be held through it, since the stop ends the VM's heap: the
Player TERMs every running service just before the stop (its `moy_stop`
persists what it needs), the resume record names them, and the console that
starts after the stop starts them again (`moy_start`), so its timers start
afresh and the records its sources took during the stop are not delivered
(the owner's 2026-11-03 answer asked for them with their real timestamps,
which a restart cannot give; #246 holds the question). The mini-VM
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
`autostart`, owner 2026-11-03: a service cart holding it starts at the
console's first drawn frame, and the user can turn that off in Settings), its **restart
policy** (`never`, `on-crash`, `always`, with a back-off and the strike
ledger's cap; P3.6) and its log lines under its process id (§9): start,
stop, kill, exit with its code, and each rung. `service
start/stop/status/restart` and the task manager are the two faces of one
implementation. The `moy_stop` hook is the TERM; a service still running after
`MOY_SERVICE_STOP_MS` (configuration, 2000) gets the KILL: its task deleted, its
OWNER swept, the ledger told. A Python service's row is the scheduler's
(`runtime/system/scheduler.py`): its `moy_stop` runs under a guard of
`MOY_SERVICE_STOP_MS`, and the KILL is that guard's abort with its wakes and
readers ended (exit 137); a start that runs past the same limit is killed
too, and a service with no wakes left has exited (0).

## 6. The task manager and admission

**The task manager** is a system app listing every process with its state
(running, throttled, minimised, paused, background, suspended, crashed),
its CPU, its memory and a Close button. CPU is **exact**: the shell times
each app's `update`, `draw` and callbacks. Memory is **exact for Lua and
wasm**, read from their own heaps, and **approximate for Python**, counted
as the allocations made during the app's own calls and labelled so, because
one shared VM cannot attribute ownership. Background apps and services are
rows too, beside the bar's indicator and its stop control (owner,
2026-11-03).

As built (runtime/system/tasks_ui.py, the kernel kind `tasks`), phase 1
lists Python runs and services; Lua and wasm rows arrive with phase 5. The
Player times its run's `update`, `draw` and window events in microseconds,
and the scheduler a process's callbacks (`Proc.cost_us`); every
`METER_MS` (2 s) `Procs.account` turns each into busy microseconds a second,
per mille of a core, the row's CPU. A Python run's memory is what its start
allocated, its body and `_init`: the world it built, its frees not
subtracted. The VM counts it in `moy_guard.allocated()` (from MicroPython's
`gc_alloc_amount`, low across a collection, never high) on every console
board and both unix builds; CPython reads tracemalloc while it traces, and
otherwise the figure is absent (`-`). A service's is its start's the same
way. Rows are the front run, the running set oldest first, then the
services; the current row's verbs sit under the table: Switch to, Close,
Stop for a service, Details for a crashed or suspended row (its log,
`Workstation.open_log`), and Home on a handheld. It opens from the system
menu's Tasks and by holding the console's key (`start`, the T-Deck's Mic)
for `START_HOLD_MS` (500 ms) on every board: the key acts when it comes
up, so a tap is the system menu or the pause menu as before (a T-Deck text
field's Cancel), and a hold never cancels the field. On a handheld it goes
on top, holding the front game while it shows, and its back returns to it;
on the desk it is a window.

**Admission.** An app that does not fit THE SHARES (`native/moy_play/moy_play.h`)
beside what is already running is **refused with a message naming what to
close**; nothing degrades silently. As built (`player_fit.admit`, asked by
`Procs.launch` before a game opens on the desk), phase 1 counts one figure
per board, the PSRAM share the running set's apps hold together
(`player_fit.APP_SHARE`): 4 MiB on the S3s, the cart floor THE SHARES
already give (one Doom), and 7 MiB on the P4s, under what a P4 desk keeps
free beside its Python heap with a game running; a board with no row (the
host, the browser, the Zero) has no share to count against. An app holds
its compiled module's load footprint where its runtime reports one, else
its canvas in RGB565, and a Python world (384 KiB) beside either. So on the
S3s two big games do not fit, and a big one beside a Python game names the
Python game. The refusal is the delete confirm's dialog
(`DeleteConfirm.ask_close`): "Close <name>?", the game that does not fit
beside it, Cancel and Close, whose Close closes what it names and opens the
game. P2.1's shares rows replace the one figure. The Player's own fit check
still runs at the load, as before.

**Sessions.** Lua and wasm run one session at a time today
(`native/moycore`, `native/moy_wasm`), so several Lua or wasm apps at once
arrive with the process tables (phase 5); Python, stepped by the shell in
its one VM, gets several first: admission lets a Lua or compiled game run
alone and names everything running to close. Each run takes one of four
owner tags (`cart`, `cart2`..`cart4`) for its layer loans and its audio
session. Audio has `MOY_AUD_SESSIONS` (4) rows, less the Music editor's while
it holds one: a fifth source is refused with the same dialog, naming the
oldest game, and the focused app's session has priority (it is the one
focused, so the one heard).

**A later phase, maybe: paused runs give back their session (owner,
2026-11-04).** The cap of four is the audio table's, not memory's, and a
minimised game is paused, so it is silent. A paused run could close its
session and reopen it on resume. Only playing runs would then count against
the table, leaving the share as the bound on a board, plus a fixed cap where
there is no share (the browser). This is left out for now to keep admission
simple. What it would need first is the cost of reopening a session (does
it reload the cart's bank?) on a T-Deck resume.

## 7. Notifications

**Decision (owner, 2026-11-03): app notifications, as on Android.** The verb
is `moybyte.notify(summary, body, icon, actions, urgency, replaces_id)`,
the freedesktop notification fields (owner, 2026-11-03), behind a **`notifications`**
permission granted at install (§2). The name is deliberately not
`notify`: the `notify` ROLE (`native/moy_app/roles.json`) is the
achievements process's API (§8) and stays ungrantable, so the two never
collide.

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

As built (`runtime/system/notifications.py`, the service `NOTES`), phase 1
serves Python processes; Lua and wasm get the verb with their process tables
(phase 5). `moybyte.notify` answers the notification's id, or None where the
process does not hold the permission: `moybyte` is one module every process
shares, so the permission's row names no Python names (§2) and the grant is
read at the post, from the one its run or its service was bound with
(`Procs.who`); the shell itself always may post. `summary` is one line (47
characters, the kernel copy's), `body` up to 200, `icon` a name of the
console's icon set ("bubble" where it names none), `actions` the freedesktop
flat list (key, label, ...; `"default"` the action the notification itself
stands for), `urgency` 0 to 2, and `replaces_id` one of the same process's
notifications, updated in place under its id.

- **The banner** is the unlock banner's card (`moy_ui_unlock`): the posting
  app's title over the summary, on the icon's tile, at the top centre, under
  the bar where there is one and at the glass's top over a fullscreen game.
  It stays up `BANNER_MS` (5 s) at urgency 1 and `CRITICAL_MS` (10 s) at 2,
  so no banner stays over a game on its own; urgency 0 goes to the list
  without one, and a new banner replaces the one up. The shell draws it as a
  system layer on every tier, and arms the kernel's copy (`moy_chrome_note`,
  a slot beside the notice and the toast) for a run the kernel drives, so a
  banner over a compiled cart costs its frame no Python and no allocation
  (`tests/test_frame_alloc.py`).
- **Acting** is a tap or a click on the banner, or Open (and each action the
  app named) on the task manager's Notifications tab. The posting process's
  run comes to the front as the task manager's Switch to brings it, a cart no
  longer running opens again, and the process's `moy_action(id, key)` hook
  runs (a run's through `Player.window_event`, a service's through
  `Scheduler.hook`, under the guard and the rungs). A service has no window,
  so acting on its notification calls the hook alone until §2's `entries`
  give a service cart an app.
- **Dismissing** is the banner's deadline passing, a press on it dragged up,
  or opening the task manager; the game in front plays on, and the
  notification stays in the list until opened or cleared.
- **The list** is the task manager's second tab, Notifications: the
  notifications nobody opened, newest first (16 at most), the app and the
  summary a row, the current one's body under the table, and Open, its named
  actions and Clear. Holding the console's key while a banner is up opens the
  task manager there with that notification current, so the keys and the pad
  reach it as a finger reaches the banner.

## 8. Achievements, the first background process

**Decision (owner, 2026-11-03): achievements become a small background
process**, not part of the shell. Because the portable draw and UI API exist
it could be written in any tier; it is Python for now. It is the first user
of background processes, events, notifications and the task manager on every
board, and it is testable with no LoRa.

As built, the process is the system cart
`system_carts/moybyte.achievements.moy`, a Python service (§3, §5) holding
the tracker (`Achievements` in `runtime/toolkit/widgets.py`, over its
`ACHIEVEMENTS` catalogue, which the console keeps for drawing). The console's
end of it is `runtime/system/achievements.py`: `ws.ach`, the link, and
`NotifyRole`, the `notify` role's server.

- **It wakes only on events.** A cart's `achieve(kind, key)` and `award(id)`
  are OS-routed MESSAGES, not ring records: the process holds
  `moybyte.on("achieve", fn)` and `moybyte.on("award", fn)`, and the link
  queues each call on them (`moybyte`'s `send`). A message wake waits apart
  from the timer and input wakes and costs no frame while nothing is queued:
  a tick returns at once and a board makes no service upcall, and a send
  asks for none. A queued message is served under the guard and the rungs
  at the start of the console's next frame, before it draws
  (`Scheduler.mail`), so the banner an unlock raises shows in the frame of
  the input that earned it, or by a tick that runs first. A run the kernel
  drives (a compiled cart) makes no crossing for the `open` its launch
  sent: the process takes it when the console frames again.
- **The `notify` role is its API.** In `roles.json` its rows' `server` is
  `"proc:achievements"` and the grant stays `never`. The console registers
  the link for the role (to `native/moy_app` the row is served as a shell
  row): `achieve` and `award` are the messages, and `count`, `has`,
  `found_on`, `progress` and `today` call the process's functions of those
  names. Settings' Achievements page reads them through the role as before.
  `award` answers True where the badge is unfound and the process took the
  message.
- **Its store.** The console starts the process when it loads its store
  (`load_achievements`, again on a reload; one process a host, so a second
  console on a host stops the first's), handing it, in its namespace, the
  document `achievements.json` holds, the write and the day off the bar's
  clock (`Workstation.service_env`). The process records into its document
  and hands it back; the console writes it at its next frame, outside any
  slice, because a store write on a card can outlast a callback's 20 ms and
  its abort. Before a VM stop the console writes what is waiting.
- **It posts.** A fresh find is a `moybyte.notify` of the badge's title, how
  it was found and its glyph. The console shows the process's banner as the
  unlock banner: its card with the kernel's words ("You found a badge!", a
  secret's "You found a secret!" and its burst), the beep and the kernel's toast copy; the
  notification stays in the task manager's list. A badge kept from another
  console (the web console's sync, `quiet`) is recorded and not posted.
- **The task manager** lists it as a service, with its CPU and memory, and
  can stop it.
- **When it is not running** (stopped, crashed, a build without its cart),
  an event goes nowhere and the reads answer the last document the console
  holds; the next store load starts it again.

## 9. The system log and crash reports

**One OS log** (P1.3): a ring in PSRAM (`native/moy_kernel/moy_log.h`, its
size the board define `MOY_LOG_BYTES`) of records `(t_us, seq, process id,
severity, text)`, the **syslog severities** (`emerg` … `debug`, RFC 5424;
owner, 2026-11-03). A write takes a lock and allocates nothing; a full ring
drops its oldest records, and a reader that falls behind restarts at the
oldest. Process ids: 0 the kernel, 1 the shell, one per run until the
process table (P1.6) takes over spawn and `current`. Writers: the kernel's
HITCH (warning), LOOP and PERF (debug) lines and the crash intake (crit);
the shell's diag lines; each run's start, exit and crash (a Python
traceback, or the Lua and wasm panel text, at crit); a console script's
`print` (still mirrored to serial); `logging` (`moy_logging`, installed as
`logging` on a VM that has none; on the host a cart's `import logging` is
CPython's own and misses the ring); `moybyte.log` for Lua and wasm arrives
in phase 5. A **cart's** `print` is the draw verb, never routed. A run the
VM stops for writes its exit record once the kernel's run end has the
process table. Readers: the kernel word `log [level] [+seq] [name]`, which
answers with no VM running; Settings → About → System log
(`runtime/system/log_ui.py`, `open log` on the dev channel); and the
terminal's `log` (phase 3). **A crash shows itself
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
