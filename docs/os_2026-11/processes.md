# The OS (2026-11): processes

Phase 1 of the order of work, and phase 3's asyncio, restart policies and
mini-VM spike. Read `docs/os_2026-11.md` first.

---

## 1. Processes

A **process** is a program the OS runs: a cart `id` (#162), a tier, a grant, a
row of a process table (handle kind `MOY_KIND_PROC`,
`native/moy_kernel/moy_proc.h`), and a lifecycle the kernel drives. The
table holds the processes outside the shell's VM: the kernel's Player adds a
row per Lua or compiled run at its launch (its pid, tier, kind, the run's
OWNER, its engine and its grant) and ends it after the runtime's close.
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
holding the grants the policy marks `never` (`native/os_calls.json`'s roles:
`carts`, `install`, `wallpaper`, `artwork`, `damage`, `surface` and `ui`; `notify`,
also `never`, is the achievements process's API, §8).

**Several apps, games included, run at once (owner, 2026-11-03).** The user
chooses what runs and sees what it costs (§6: "it's the user's choice; we
tell the user the CPU and RAM usage so he can close apps if they get slow").
Focus and running are separate properties. **Focus** is the window manager's
(`docs/shell_ux_v1.md` §3): the focused window alone gets input and names
the audible audio session (`native/moy_audio/moy_aud.h`). **Running** is the
running set's (`runtime/shell/procs.py`, built 2026-11-03 for Python games
on the desk): a run's state is ONE object, its `Run` -- its Player,
Project, audio session, owner tag, game canvas and the binding over it, its
view and its clock -- and none of it lives on the Workstation (owner,
2026-11-08, #246 decision 6). The FRONT run is the one `ws.run` names, in
the WM's `desktop` window, and every other running game is a Proc holding
its own Run, in a window keyed `run:<n>`. Stepping a Proc points `ws.run`
at its Run for that step, so the Player and the cart API act on that run
alone (`tests/test_run_object.py` fails code that reaches a run's field on
the Workstation). Focusing a Proc's window PROMOTES it: the two Runs trade
places and the two windows their keys, so the focused game is always the
front one and gets the keys,
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
joining the set. Every run there
draws on the one glass, so a handheld's runs share the system canvas
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
manager and `ps` show); a stop is TERM (the `moy_stop`/`close` hook, a
compiled service's event descriptor hung up) and, after
`MOY_SERVICE_STOP_MS`, KILL (§5), the same for an app, a command and a
service. The codes the OS gives are the shell's, defined once
(`native/moy_kernel/moy_proc_exit.h`, Python's through `moy_log`): 126 a
start refused, 130 Ctrl-C, 134 a trap, 137 a kill.

**Lifecycle verbs:** `open`/`close` for an app (`docs/shell_ux_v1.md` §11),
`main(argv) → exit code` for a command, `moy_start`/`moy_stop` for a
Python or Lua service, with `moy_stop` the hook under which a service
persists what a kill would lose (a LoRaWAN frame counter), and for a
compiled service `main`, whose loop persists the same when its event
descriptor reads end of file (`tiers.md`). A crash is an exit, recorded in the strike ledger (§10).

**The tick model.** The foreground app keeps frames: `update(dt)`/`draw()`
as today; a minimised app keeps its `update` and a minimised game is paused (above). **A background process, on every tier, never gets a per-frame
tick.** It declares its wake sources and sleeps between them: timers
(`moybyte.every(ms, fn)`, `moybyte.after(ms, fn)`) and events
(`moybyte.on(source, fn)`: a UART, a socket readable, a pin edge, a LoRa
packet, a message). Events are read from the C rings of `hardware.md` §2, so nothing is
lost and a record keeps its real timestamp; timers are deadlines on
`ticks_ms` in the wake registry (`runtime/system/moybyte.py`). An idle process costs nothing, the
idle ladder can still dim and sleep (§5), and it is one model on every tier:
the service model below. A timer means "no earlier than"; jitter of up
to a frame is normal, and precise timing belongs to C, the wasm lanes and the
hardware engines. Python's `asyncio` rides the same scheduler (§3);
`every`/`after`/`on` stay as the simple form.

**The service model (owner, 2026-11-08: "Real OSes don't have different ways
depending on the app language").** A background program is one that blocks
in ONE kernel wait on its own event queue, and each language's style is a
library over that wait. Three kernel pieces carry it, every tier's alike:

- **The event queue** (`native/moy_kernel/moy_evq.h`, the module `moy_evq`):
  one per process, by pid, opened by the OS at its start with its app id and
  its manifest's `receives`, closed at its end, `MOY_EVQ_DEPTH` (16) events
  deep with the oldest dropped. An event is a message: a name, its sender's
  pid and app id (stamped by the kernel from the sender's own queue) and its
  arguments, a JSON array. The OS's events are messages from the system
  (pid 0 and 1, app `"system"`): the radios' `net` and `ble`
  (`between_apps.md` §4) are routed to every process that listens on them.
- **The wait** (`moy_evq_wait`): a process on a task of its own blocks until
  an event is queued, it is kicked (a stop, an answer from the VM, a resume)
  or its own nearest deadline passes. Lua's `every`/`after`/`on` are its
  library on a Lua service's task (`tiers.md` §2). The Python VM's processes
  share one task, so the shell loop is their wait: a queue the VM serves is
  flagged so, and the scheduler takes its events at the frame's start
  (`moy_evq_ready`, `Scheduler.mail`), `asyncio` and `every`/`on` riding the
  same scheduler (§3). A compiled program's face is its **event
  descriptor**, `MOY_EVQ_FD` (owner, 2026-11-08; `moy_evq.h`, THE EVENT
  DESCRIPTOR): readable while an event is queued, each read one record (the
  sender's pid and app id, the name, then the arguments), never blocking. A
  WASI program blocks in `poll_oneoff` on it (`native/moy_wasm/moy_wasi.h`)
  and an ELF module in `poll` (`native/moy_elf/moy_os.h`), each wait the
  one kernel wait with the program's nearest deadline as its timeout, so a
  timer is a deadline and no hook is dispatched. Both listen and send with
  the same two rows, `moy_on` and `moy_send` (a WASI program's `moybyte.os`
  imports), the kernel's own on the program's thread. A compiled command's
  process gets a queue from the shell's door (`runtime/shell/sh_door.py`),
  and a compiled service's from the scheduler, its app id and `receives`;
  its stop hangs the descriptor up (`POLLHUP`, WASI's hangup flag, a read's
  end of file) for its `main` or `_start` to return. `sdk/include/moybyte/os.h`
  carries the record and both rows for either tier, and the example service
  of each, a loop that blocks in poll and answers a "ping" with a "pong",
  is `tests/fixtures/elf/elf_svc.moy` and `tests/fixtures/wasm/wasi_svc.moy`.
- **The supervisor** (`native/moy_kernel/moy_super.h`): one ladder of budgets
  and strikes (§3's rungs) for every tier's callbacks, charged by the tier
  that ran the callback, logged under its pid. A compiled program's callback
  is a wake: from a poll of its event descriptor's return to its next poll
  of it, less what its calls waited for the VM's task (a WASI command's
  session calls, an ELF service's hops); a suspended program's poll waits
  unserved until it is resumed. A compiled service's wake that never polls
  again is struck by a watchdog at the scheduler's poll (its runtime's
  `reap`: `moy_os_reap`, `moy_wasi_watch`), once for each
  `MOY_SUPER_ABORT_US` it runs on, and the strike that suspends it kills it
  (137), since a suspension cannot hold a wake that is still running; a
  start that reaches no poll within its stop time is killed too. Native
  code is not aborted mid-wake, so an ELF wake is killed where it stands; a
  WASI one's instance is terminated (`wasm_runtime_terminate`), which its
  `_start` reads at its next import call, so a loop that calls none runs on
  until it does.
- **The VM's queue** (`native/moy_kernel/moy_vmq.h`): the one way a process
  on its own task enters the console. A HOP runs C on the VM's task while
  the poster waits (a role row of its app's run); a CALL is a console call
  by name (`moybyte.notify`, `open_with`, `share`, `send`, a function the
  console handed a system process), answered in Python as the calling
  process at the scheduler's poll, the poster woken by the answer.

**Messages are one mechanism (owner, 2026-11-08).**
`moybyte.send(app_id, name, *args)` posts `name` to every process of app
`app_id` that listens on it, the sender apart, and answers how many took it;
`moybyte.on(name, fn)` listens, `fn(sender, *args)` with `sender` the app id
the kernel stamped. The rule is the kernel's (`moy_evq.h`, THE RULE):

| sender | reaches a listener |
|---|---|
| a process of the receiver's own app | always |
| a process of another app | only where the receiver's manifest lists the name under `receives` |
| the system (pid 0 and 1) | always |

A message to a process that does not listen on its name is not taken. Every
tier holds the same two names (`moybyte.send` is a row of
`native/os_calls.json`; Lua's runs as a console call), and a message crosses
tiers unchanged: a Python app's `send` reaches its Lua service's queue, and
the service's reply the app's.

**Where processes run.** A Lua, wasm or ELF process runs on a task the
kernel owns; its callbacks run on that task. A Python process runs inside
the shell's one VM (§3): its callbacks run in the shell loop's slack after
a frame is presented and before the next. Services run on core 0 on the P4s;
what the S3s get is the overview's §5. As built for Lua
(`native/moycore/moycore_svc.h`, P5.3): a Lua service is a state of its own
on a task of its own, pinned to core 0 on the P4s, which blocks in the one
wait until its next timer, a message or a stop and runs what is due; the S3s
refuse one, and the host and the desktop MicroPython, which have no task to
give, step their services at the scheduler's poll
(`runtime/system/scheduler.py`, LUA SERVICES). What a Lua service asks of
the console (a notification, a message, a function the console handed it)
goes through the VM's queue (`tiers.md` §2). A compiled service, ELF or
WASI, is the scheduler's as a Python or Lua one is, started, listed,
stopped, restarted and autostarted the same way
(`runtime/system/scheduler.py`, COMPILED SERVICES), and the scheduler's
poll reaps its end. An ELF service is its `main` on a task of its own, on
core 0 on the P4s and core 1 on the S3s, its app calls (`moy_notify` and
the rest) hops on the VM's queue under a run grant of its own, served at
the scheduler's poll. A WASI service (a wasm cart with no source,
`native/moy_wasm/moy_wasi.h`, A SERVICE) is its `_start` on a task of its
own with no shell session: a session of its own on core 0 on the P4s, a
thread of its own on the host (several at once, apart from the door's one
command job); the S3s refuse one, as they refuse a Lua service. Its stdout
and stderr are the OS log's lines under its pid, its stdin and env are
empty, and a path answers NOTCAPABLE; it imports no app call. A compiled
program's `moy_send` posts from its own thread; the door notices one that
reached the VM's processes after each step of a command
(`sh_door._noticed`), the scheduler's poll after a service's, and the
frame's start serves it.

## 2. The app manifest and the permission table

Every process comes from a `.moy`; `manifest.json`'s `"moybyte"` object
carries Moybyte's fields (`docs/moy_cart_api.md`, `docs/app_api_v1.md`). The
OS reads:

- **the tier**: moy-spec's `"runtime"` for `python`, `lua`, `wasm`; `elf` is
  a Moybyte-side field, the chips it carries a module for (`"moybyte":
  {"native": ["esp32s3", "esp32p4"]}`, `native/moy_elf/README.md`), since the
  spec does not know the tier;
- **the kind**: `"type"` — `game` (default), `app`, `command`, `service`, or
  **several entry points** sharing one grant and one prefs namespace
  (`"entries": {"app": "...", "service": "..."}`): the LoRa node and
  its radio app are one cart;
- **permissions**: names from the one table (below): a role (`files:docs`,
  `prefs`, `appearance`, `launch`, `clipboard`), the ungated floor
  (`graphics`, `input`, `audio`), a device API (`mic`, `battery`, `brightness`,
  `alarms` (`between_apps.md` §3), `gps`, `lora`, `ble`, `usb:hid`, `midi`,
  `camera`, `http`, `network`, `multiplayer`, `media`, `media:url`,
  `notifications` §7), `front` (below), `autostart` (§5), a lease
  (`lease:<resource>` naming a row of the board's resource table,
  `hardware.md` §1), or `full_access` (full access: what a native cart must
  ask for, and what admits a module with no signature on either compiled
  tier, `moy_ota_admit_module`). A permission the manifest also lists in `"moybyte": {"required": [...]}`
  is required: absent, it makes the launch refused with a message naming
  it, where any other absent permission reads `None`;
- **handles**: the MIME types its app opens (`between_apps.md` §1), what
  `open_with` and `share` route by;
- **background**: a service's one-line reason the OS shows ("LoRa node"),
  and `"lanes"` naming the timed lanes a wasm or ELF process exports
  (`hardware.md` §2).

**Enforcement is at bind time, not per call.** A role is checked when the
grant is made and when a wasm module's imports are walked at load
(`native/moy_app/moy_app_wasm.c`, `moy_app_wasm_admit`); a Python app's
namespace holds only the granted names (`runtime/shell/system_api.py`), and a
Lua app's state opens with them alone (`native/moy_app/moy_app_lua.h`).
Each Lua or wasm run holds its own grant, a value its owner keeps
(`native/moy_app/moy_app_run.h`), and a role row it calls answers from that
grant's row and no other run's. **A service holds its app's roles (owner,
2026-11-08)**: the grant its start makes from its granted permissions is the
one a foreground run of the same app is made from (the same policy, app id,
files kind and prefs namespace), a run grant whose owner is the service's
pid, so it is a row of its own that neither run's end takes from the other
(`runtime/shell/system_api.py`'s `service_api`, §5). No draw verb or input read checks a grant. For **Lua and wasm** an ungranted
capability has no name in the process. For **Python** the manifest is
legible, not binding: a Python cart runs inside process 1's VM with the real
builtins and can reach `machine` and the `never` roles (`system_api.py`: "NOT
a sandbox"); the import scanner rejecting illegal imports at install and at
PLAY is a lint (`__import__`, `exec`, `getattr` walk past it), and there is
no boundary (§3). What a Python app's grants gate is the names its
namespace is handed: `files` the role row, `wifi` and `moybyte.http` their
device APIs; `open`, `os`, `socket`, `requests` and `machine` are the real
modules, reached with or without a grant. For **ELF** permissions
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

**Consent is recorded with the grant's key**: the console's `system/settings`
holds one record per app id (`moy_app.id_for`, what the grant, the prefs
namespace and the crash strikes are keyed by), the permissions consented
at install and the ones turned off since (`runtime/shell/app_perms.py`,
`Consent`). A cart with no record (written in the Editor, a seed, a cart
copied onto the card by hand) never prompts and runs on its manifest, less
what Settings turned off. Get Carts asks once every file of the download is
checked and before anything moves onto the shelf, since a carts index
carries no manifest (`cart_index.Install`'s `ask`); a denied install
removes the build, and Remove forgets the record.

**The permission table.** One JSON source, `native/os_calls.json`'s
`"permissions"` beside the rows of every call (`tiers.md` §1), generated
into `app_perms.TABLE` and the C policy's allowlist (`MOY_APP_PERMS`) by
`tools/gen_os_table.py`, which the prompt, the install check, the launch
check and Settings all read. Each row: the name (`files` also matches
`files:<folder>`), what it grants (`floor`, `role:<role>`, `api:<service>`,
`boot`, or `marker`: a shipped app's claim on its cart, granting nothing),
the tiers that may hold it (derived: the tiers that serve the calls it hands
an app, so a prompt names nothing the cart's tier cannot do and a cart of
another tier is not asked for it, `tests/test_app_perms.py`), its line of
the install prompt (None for the
floor and the markers: nothing to consent to and nothing to turn off), the
console service it needs (`wifi`, `net`, `gpio`), and the names a Python
cart's namespace holds while it is granted, each `None` while it is turned
off.
The rows Python holds in phase 1 are the floor, the roles, `network`,
`multiplayer`, `pins`, `console`, `notifications`, `autostart` and the
markers (`notifications` with no names: its verb lives on the shared
`moybyte` module and checks the poster's grant, §7); a Python app's prompt also says it is a trusted app. Phase 2 adds
`lease` (P2.3): `lease:<row>`, granting `lease`, Python's for now, its
prompt `"full"` ("Full access to the console's uart1", `app_perms.FULL`; the
overview's §4), needing the board to offer the row (`app_perms.leasable`
names each as `lease:<row>`), with no names: `moybyte.lease` checks the
caller's grant (`Procs.granted`), the shell's always passing. P2.3 adds
the device API rows `http`, `mic`, `lora` and `ble`, `api:<name>`, Python's
for now, with no names: `moybyte.http` and the rest are the faces
`runtime/system/devices.py` hands out, each checking the caller's grant the
same way. `http` needs `wifi`; the others need the board's class, which
`devices.apis()` adds to the console's services. P2.4 adds `media` (it
needs the board's media service, `hardware.md` §3) and `media:url` (it needs
`wifi`), both granting `api:media`. The later rows (USB host,
`midi`, `camera`, `full_access`) arrive with their APIs, each saying whether it
is enforced on its tier or informational (above).

**`front`: taking the screen from the background (owner, 2026-11-08).**
`api:front`, every tier's, its line "Take over the screen from the
background", with no names: one gate, `runtime/system/front.py`'s `take`,
reads it for every way a process brings an app to the front
(`between_apps.md` §1 lists them). The run on screen needs no permission,
the shell is never gated, and a process in the background without `front`
gets its request as a notification it can be opened from (§7). A run is
named by its pid (`moybyte` verbs, the current process) or by its role
grant (`Procs.pid_of`, the nav role's verbs: a system app's grant is the
shell's, one no run holds is gated), never by the app id it claims.

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
until it is resumed); the shell is never struck. The ladder is the one
supervisor's (`native/moy_kernel/moy_super.h`, §1's service model), which a
Lua service's callbacks are charged to alike. A callback that never
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

**`asyncio`** is MicroPython's, on one event loop the shell owns on this
scheduler (`runtime/system/aio.py`, whose header is the authority): no
process runs a loop of its own, so `asyncio.run(coro)` and
`create_task(coro)` put the task on the shell's loop and answer its Task at
once, and a service's main ends with `asyncio.run(main())`. A task is the
process's that made it, and each step (one resume up to its next await) is
that process's callback under the guard and the same rungs, its cost in the
process's CPU time; an aborted step ends its task. A process holds its live
tasks as it holds its wakes, so a service runs while it has one, and its end
cancels them. A task's exception that nothing awaits is its process's crash.
`await asyncio.sleep(s)` or `asyncio.sleep_ms(ms)` is a timer, and a stream
over a socket (`open_connection`, `start_server`) waits on the loop's poller,
looked at every frame while one waits. The loop is one wake of the shell's,
registered while a task lives, so a console with none pays nothing and an
asleep task's frames allocate nothing. The host runs CPython's asyncio in
its place, one iteration a pass, with `sleep_ms` added; the browser's VM has
no asyncio. **Standard names first**: `open()` on tree paths,
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
open(path, mode)                       the builtin: any tree path (Python is trusted, §2)
os.listdir / stat / remove / mkdir     the same
socket.socket(...)                     lwIP's; it imports and connects with or without "network"
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
those sources only. Across a VM stop (the S3s) an OS service keeps
running. A service the scheduler runs is not held through it, whatever its
tier, since the stop ends the VM's heap, which holds its row (an ELF
service's app calls are the VM's to serve, too): the Player TERMs every
running service just before the stop (its `moy_stop`, or its loop at its
descriptor's end of file, persists what it needs), the resume record names
them, and the console that starts after the stop starts them again
(`moy_start`, `main`), so its timers start
afresh and the records its sources took during the stop are not delivered
(the owner's 2026-11-03 answer asked for them with their real timestamps,
which a restart cannot give; #246 holds the question). The mini-VM
spike (P3.8, branch `minivm`, f4496588f) built the alternative and it was
DROPPED (owner, 2026-11-06): a small MicroPython started in the stopped
VM's place, strictly after its teardown, ran the services through Doom on
both S3s, but it cost the cart 1-2 fps and 256 KB of its PSRAM, reached no
card and no glass, and helps only Python services while the ones that
matter during a game (media, the radio link) are C. "Python pauses"
stands. What it showed that outlives it: a VM with only the services
subset frozen in boots in about 100-350 ms and runs one service in a
12-20 KB heap, the starting point for a board with no PSRAM whose only
Python is service-sized (#246 has the numbers). A service that is killed under
memory pressure (the threshold from the shares) gets its `moy_stop` hook
first, and what it persists there is its contract. The threshold is
`moy_share_kill_below` (`native/moy_play/moy_play.h`): what every present row
still owes, its cap less what it holds, PROCS's held being the running set's
need. Nothing reads it yet: a Python service's memory is the VM heap's, which
never gives an area back (#248), so killing one returns no PSRAM; the first
reader is the first holder outside that heap (the media service, P2.4).

**Services are managed like an init system (owner, 2026-11-03).** The
process table holds, per service, its **autostart** state (a permission,
`autostart`, owner 2026-11-03: a service cart holding it starts at the
console's first drawn frame, and the user can turn that off in Settings), its **restart
policy** and its log lines under its process id (§9): start, stop, kill,
exit with its code, each rung, each restart and the cap. The policy says
what follows an end nobody asked for (an exit, a crash, a start killed at
its limit; a stop, the pause before a VM stop are asked): `never`, the
default, leaves the service down, `on-crash` starts it again after an end
with a code other than 0, `always` after any. The restart waits a back-off,
`MOY_RESTART_BACKOFF_MS` (1 s) doubled for each end in a row, and the
strike ledger's cap (`CrashGuard.STRIKES`, 3) of ends in a row leaves it
down; a run of `MOY_RESTART_HEAL_MS` (10 s) before its end, or a start by
hand, starts the count afresh. The restart is the cart's launch again under
its consent as it stands then, a new process. The policies are a
`system/settings` row, `service_restart` ({service id: policy}, configuration
in `runtime/system/scheduler.py`), and a manifest declares none. `service
start/stop/status/restart` and the task manager are the two faces of one
implementation, and `service policy [NAME [never|on-crash|always]]` shows
and sets the policy. **A service's storage is its app's (owner, 2026-11-08: "wouldn't a well
written app just call its background service? I don't know its on us to
sync them").** The OS gives the pieces, not a sync engine. A service gets the
same `files` and `prefs` roles as its app, and whatever else its manifest's
grants give, under the same consent (§2): a Python service's namespace holds
the names a Python app's does (`files`, `prefs`, `set_theme`, `open_app`;
`None` while Settings turns one off), a Lua service's state opens the names a
Lua app's does (`tiers.md` §2), and what it is not granted has no name. Each
write lands whole or not at all, by the paths a foreground run takes: a file
through the user-files layer's crash-safe publish (`moy_fs_publish`), a Lua
`io` file saved whole at its flush or close, and a pref as one settings row
of `system/settings`, whose file is written atomically
(`moy_docs.save_system`). An app talks to its own service through messages
(§1, the service model): `moybyte.send(its own app id, name, ...)`, which the
rule always lets through within one app, the app's run to its service and
back, each tier's alike.
Concurrent edits of one file or pref are the app's to order, as on any OS.
The install prompt says so: a service's lines end with "Runs in the
background, and can do all of this with no window open"
(`app_perms.BACKGROUND`).

The `moy_stop` hook is the TERM; a service still running after
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
lists Python runs and services, and phase 5 Lua runs and Lua services, whose
memory is their own state's heap, exact (`moycore.heap` for a run, Lua's own
count; `moycore.svc_info` for a service, its allocator's count); compiled rows
arrive with the wasm sessions. The
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
`Procs.launch` before a game opens on the desk), admission counts one row
of THE SHARES per board, PROCS, the PSRAM the running set's apps hold
together (`player_fit.board_share`, from `moy_play.shares()`): 4 MiB on the
S3s, the cart floor THE SHARES already give (one Doom), and 7 MiB on the
P4s, under what a P4 desk keeps free beside its Python heap's room to grow
with a game running; a build with no line (the host, the browser, the Zero)
has no share to count against. An app holds
its compiled module's load footprint where its runtime reports one, else
its canvas in RGB565, and a Python world (384 KiB) beside either. So on the
S3s two big games do not fit, and a big one beside a Python game names the
Python game. The refusal is the delete confirm's dialog
(`DeleteConfirm.ask_close`): "Close <name>?", the game that does not fit
beside it, Cancel and Close, whose Close closes what it names and opens the
game. The Player's own fit check still runs at the load, against what is
free.

**Sessions.** Python, stepped by the shell in its one VM, and Lua run
several at once: a Lua run is a row of the engine's (its own state,
console and media, `native/moycore/moycore_lua.h`) and of the kernel's
Player (`MOY_PLAY_RUNS`, `native/moy_play/moy_play.h`), so a Lua game joins
the running set as a Python one does, held aside reading no input
(`moy_play_mute`) (P5.3, 2026-11-07). A compiled run is a session of its
own on a board's engine and the host's (`native/moy_wasm/moy_wasm_session.h`),
so compiled games join the running set too, and the share decides whether
two fit; the browser's engine holds one session, so there a compiled game
runs alone and admission names everything running to close
(`player_fit.shares_runtime`). Each run takes one of four
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
`notify`: the `notify` ROLE (`native/os_calls.json`) is the
achievements process's API (§8) and stays ungrantable, so the two never
collide.

- **The OS draws the banner**, over whatever is running, games included:
  kernel chrome where the kernel draws (the toast and banner state of
  `native/moy_play/moy_chrome.h`), shell chrome on the desk. The foreground
  app never receives the notification or its contents.
- **Acting on it** focuses the posting app; a game that was in front loses
  focus (throttled on the desk, minimised on a handheld, §1). **Dismissing** it keeps the game playing.
- **Missed notifications** are a list, in the bar or the task manager.
- **A request to take the screen** a process in the background may not make
  (§2's `front`) is a notification too (owner, 2026-11-08): the shell posts
  it under the process's title, with no permission, and opening it does what
  was asked.
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
  give a service cart an app. A request (`NOTES.request`) is acted on by
  running what was asked, as the user's own input, in place of the hook;
  a process's newer request replaces one nobody opened, and its summary
  is the request ("Wants to open todo.md in Notes").
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
it could be written in any tier; it ships in Python, and its Lua port is the
tier-conformance test (`tiers.md` §2). It is the first user
of background processes, events, notifications and the task manager on every
board, and it is testable with no LoRa.

**Decision (owner, 2026-11-08, #246 decision 8): every game's badges.** A
game declares its badges in its manifest and unlocks them with messages to
this process; the console's own badges are declared the same way and are the
`"system"` sender's; the game does its own counting. `docs/moy_cart_api.md`
("Achievements") is the author's side.

As built, the process is the system cart
`system_carts/moybyte.achievements.moy`, a Python service (§3, §5) whose
manifest lists `award` and `progress` under `receives` and grants it
`notifications` and `prefs`. The console's end of it is
`runtime/system/achievements.py`: `ws.ach`, the link, `NotifyRole`, the
`notify` role's server, the console's own declaration (`SHELL`) and the
strict readers both ends share.

- **It wakes only on two messages**, `award(id)` and `progress(id,
  value)`, sent by a game (`moybyte.award`, `moybyte.progress`: rows of
  `native/os_calls.json`, each `moybyte.send` to the process) or by the
  console for its own badges (`Scheduler.send`, from the system). They are
  §1's messages: a message wake costs no frame while nothing is queued, and
  a queued one is served under the guard and the rungs at the start of the
  console's next frame, before it draws (`Scheduler.mail`), so the banner
  an unlock raises shows in the frame of the input that earned it, or by a
  tick that runs first.
- **The sender is the OS's stamp.** The process asks the console for the
  sender's declaration (`game(app)`, handed at its start with `clock_day()`
  by `Workstation.service_env`; for `"system"` the console's own), so a game
  reaches only the badges its own manifest declares, read strictly
  (`achievements.declared`). An id the sender does not declare, a `progress`
  on a badge with no goal and a value that is no whole number are refused,
  and the refusal goes to the OS log under the process's pid.
- **It counts nothing.** `award` finds a badge; `progress` stores the
  sender's last value and finds the badge once it reaches the manifest's
  `goal`. Sets, totals, bests and streaks are the sender's logic: the
  console keeps its own distinct carts and editors in its own row of
  `system/settings` (`milestones`) and reports the counts (`Link.played`,
  `Link.used`), as a game keeps its best in its pmem.
- **Its store is its prefs** (one row, `badges`; `read_badges` is the strict
  reader): a record a sender, its name, each found badge's day, title, icon
  and hint, and each goal's last value, read and written whole on each
  message. A deleted game's badges stay in the row and stay listed under
  its name. The row is a cart namespace's, so the system root carries it
  between a board and its page with the portable Settings
  (`moy_sync.SYSTEM_KEYS`). There is no migration: a console's old
  `achievements.json` is never read again (CLAUDE.md, no store migrations).
- **The `notify` role is the console's face of it.** In
  `native/os_calls.json` its rows' `server` is `"proc:achievements"` and the
  grant stays `never`: `award` (one of the console's own, the eggs' and the
  saves', sent as a message), `groups` (Settings' list: the console's own
  first, then each game on the shelf that declares a badge, then each game
  gone from the shelf whose badges were found, each badge with its day and
  its goal's last value) and `today`. The reads answer the process's row
  whatever its tier and whether or not it runs.
- **It posts.** A fresh find is a `moybyte.notify` whose summary is the
  banner's head, the console's own "You found a badge!" (a secret's "You
  found a secret!") or the game's name ("Harpoon Pop: badge found!", a
  secret's "...: secret found!"), its body the badge's title and its icon
  the badge's. The console shows the process's banner as the unlock banner:
  its card, a secret's burst, the beep and the kernel's toast copy; the
  notification stays in the task manager's list.
- **The task manager** lists it as a service, with its CPU and memory, and
  can stop it.
- **When it is not running** (stopped, crashed, a build without its cart),
  a message goes nowhere and the reads answer its row; the next store load
  starts it again.

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
| the permission table | `native/os_calls.json`'s permissions, generated into `app_perms.TABLE` and `MOY_APP_PERMS` in C; `network`/`multiplayer`/`pins` gated in `runtime/run/cart_api.py` | **one table** (§2), C-owned, read by the Player, the wasm adapter, the Lua binding, the ELF loader, the installer and the simulator |
| the update screen | the updater's; firmware update is never a role (`runtime/shell/system_api.py`) | **kernel chrome** (owner, 2026-11-01, option b): the precedent for a service's indicator |
