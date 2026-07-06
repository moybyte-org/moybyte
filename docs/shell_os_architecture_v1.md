# Moybyte shell OS architecture — a syscall boundary for the shell's own surfaces

**Issues:** #55 (system-as-carts, Picotron-style "everything is a process"), #73 (layered
compositor → window manager), and the adversarial review of the layers refactor whose #1
finding this doc answers.
**Status:** DESIGN / TARGET — a north star plus one concrete, immediately-achievable
first step. This is NOT a committed implementation plan; nothing here is scheduled. The
first step (the per-surface capability API, §5.1) is achievable *today* because the
layers refactor already produced its inputs: each surface's `ws.*` footprint is now a
one-line grep, and those measured footprints ARE the per-surface syscall lists. The
later steps escalate from "cheap and worth doing soon" to "aspirational endgame" and are
labeled as such.
**Companion docs:** `docs/shell_architecture_v1.md` (§2 privileged system carts — this
doc supplies the *boundary mechanism* §2 needs; §3 layered compositor — this doc's
endgame), `docs/shell_layers_refactor_v1.md` (the just-completed refactor this doc is
the successor to), `docs/moy_cart_api.md` (the kid-facing cart contract, which is
**frozen** and unaffected by everything below).
**One-line thesis:** the layers refactor made the shell's coupling *legible* but did not
*reduce* it — every Layer still reaches through a `self.ws` back-reference into a
~100-member undocumented god-API. Real OSes solved exactly this problem, all in the same
way: a narrow, explicit, capability-scoped boundary. Moybyte already runs its kid carts
behind precisely such a boundary (`make_api`). The fix is to give the shell's own
surfaces the same treatment.

---

## 1. The problem — legible coupling is still coupling

The layers refactor (`docs/shell_layers_refactor_v1.md`) did what it promised: every
surface now owns its draw + input + state + constants in one class in one file
(`runtime/bar_layer.py`, `settings_layer.py`, `cards_layer.py`, `code_layer.py`,
`paint_layer.py`, `launcher_layer.py`, plus the earlier-extracted `*_ui.py` surfaces),
and `Workstation` is a router over a z-ordered `Layer` stack (`runtime/layers.py`). The
adversarial review of that refactor found its sharpest flaw, and the review is right:

**The refactor moved the smear into files; it did not shrink the interface.** Every
`Layer` holds a `self.ws` back-reference (`runtime/layers.py`, `Layer.__init__`) and
reaches through it at will. Measured on this branch (methodology:
`grep -ohE '\bws\.[A-Za-z_]\w*'` over the surface sources, distinct names):

| Scope | Distinct `ws.*` members reached |
|---|---|
| The six Phase-2 layer files (`runtime/*_layer.py` + `layers.py`) | **93** |
| All surface sources (adding `runtime/*_ui.py`, `perf_hud.py`) | **108** |
| …of which private `Workstation` internals (`ws._*`) | **36** |

Per-surface footprints: **bar 28, settings 28, paint 27, cards 16, code 16, launcher 8**
distinct `ws.*` members each. So the de-facto interface between the shell core and its
surfaces is a ~100-member, undocumented, implicit API — a third of it private — that no
one designed and no one can enumerate without grepping.

Three concrete failure shapes, all real code on this branch:

1. **Reach-through into core internals.** Layers call private `Workstation` machinery
   directly: `ws._draw_menu_backdrop()`, `ws._game_xy(px, py)`, `ws._leave_or_home()`,
   `ws._toggle_web_view()`, `ws._exit_settings()` — 36 distinct `ws._*` names in total.
   Nothing marks which of these are load-bearing contract and which are internals that
   should be free to churn.
2. **Layers poking each other's guts *through* `ws`.** `SettingsLayer.handle_pointer`
   resets another surface's private counter (`ws.ach_ui._secret_taps = 0`,
   `runtime/settings_layer.py:216,226`), hit-tests another layer's private geometry
   (`ws.bar_layer._dock_slot_at(px, py)`, `:227`), and drives a third surface's flow
   (`ws.update_ui.open_update()`, `:101`). `LauncherLayer` iterates
   `ws.ach_ui._KONAMI` and calls `ws.ach_ui._konami_step(_b)`
   (`runtime/launcher_layer.py:226-228`); `BarLayer` calls `ws.ach_ui._tap_clock()` and
   zeroes `ws.ach_ui._clock_taps` (`runtime/bar_layer.py:345-347`).
3. **Direct mutation of core state.** Layers write `ws` attributes rather than calling
   verbs: `ws.config[...] = ...` (`cards_layer.py:297`), `ws.cart_error = ...`
   (`cards_layer.py:78`), `ws._dirty = True` (`paint_layer.py:316,336`),
   `ws.system["name"] = ...` (`settings_layer.py:134`).

In operating-systems terms this is **ambient authority with no syscall boundary**: every
"process" (surface) can name and touch any kernel (shell-core) data structure, and
processes reach into each other's address spaces. The refactor was still the right move
— you cannot scope an interface you cannot see, and the footprints above are only
measurable *because* each surface now lives in one file. But legibility was the
reconnaissance, not the fix. This doc is the fix.

---

## 2. How real OSes prevent this — three shared principles

Every serious OS architecture, whatever its other disagreements, converges on the same
three moves. They are worth stating precisely because each maps onto a concrete Moybyte
mechanism (§4–§5).

### 2.1 A narrow, explicit boundary — the syscall table

Linux userland crosses into the kernel through exactly one gate: the syscall table,
~350 enumerated entry points. A process never touches a kernel data structure — not
because it politely declines to, but because the boundary makes it impossible to name
one. Three properties matter:

- **Explicit.** The interface is an enumerated list, not "reach any symbol you can
  find." You can print it, audit it, count it.
- **Stable.** The syscall ABI is a contract ("we do not break userspace"); kernel
  internals churn freely *behind* it. The boundary is what makes internal refactoring
  safe.
- **Privilege-gated.** Ring 3 code cannot execute ring 0 operations except through the
  gate, so the gate is also where policy lives.

Note what this does *not* require: the Linux kernel is a monolith, heavily coupled
*inside*. The lesson is not "decouple everything" — it's that a system can be as tangled
as it likes internally so long as the interface it exposes *outward* is tiny and
enumerated. `Workstation` can stay a cohesive router; it's the 100-member reach-through
surface that has to go.

### 2.2 Capabilities, not ambient authority

seL4, Fuchsia/Zircon, and Capsicum go one step further: a process doesn't get "the
syscall table plus the ambient right to open anything it can name." It holds **handles**
to the specific objects it was granted, and it cannot operate on — cannot even *name* —
anything else. Fuchsia hands a process exactly the handle set it needs at creation; seL4
proves the property. The slogan form: **you cannot couple to what you cannot name.**

This is the direct antidote to a god-API. The move is not to shrink `ws` member by
member (a losing game of whack-a-mole); it is to make `ws` *unreachable* and hand each
surface an object that contains only its granted verbs. Coupling then can't regrow
silently — adding a capability is a visible diff in the grant, not a new `ws.` in a
method body.

### 2.3 Message-passing between decoupled peers

The microkernel family (seL4, QNX, Minix, Mach) shrinks the kernel to scheduling + IPC +
memory; filesystems, drivers, and services are userland *servers* you talk to by sending
messages, not by sharing memory and calling into each other's structs. Whatever one
thinks of the performance history of that argument, the decoupling result is not in
dispute: a process that wants something from another process *asks*, through a message
whose schema is the whole contract.

This is the fix for failure shape #2 above. `SettingsLayer` zeroing
`ach_ui._secret_taps` is one process writing another process's private memory. The
message-passing form: Settings emits "a tap landed that wasn't the secret door," the
achievements service — the *owner* of the streak counter — decides what that means for
its own state. The emitter doesn't know the achievements surface exists.

**Plan 9** deserves a mention as the fourth data point that triangulates the same spot:
every resource is a file server, and each process gets a *per-process namespace* — the
set of files it can see is assembled for it at startup. Narrow interface (nine file
operations) + capability-by-construction (the namespace is the grant). Different
mechanism, same two principles.

---

## 3. Picotron — the fantasy-console realization

Picotron demonstrates that this architecture fits a fantasy console, at a scale Moybyte
can actually see itself in. It is, in essence, a fantasy *microkernel*:

- **Everything is a process.** The window manager, taskbar, editors, file browser are
  Lua programs — carts — not hardcoded shell. The desktop is just the set of processes
  currently running.
- **Message/event passing.** Processes coordinate by sending events, not by reaching
  into each other's state.
- **Compositor.** Each process draws into its own window; a compositor combines them
  into the screen.
- **A virtual-filesystem namespace** (distinctly Plan 9-flavored) as the resource
  interface.
- **A small host API as its syscall table** — the enumerated set of functions a process
  may call is the entire boundary between a cart and the system.

This matters here because Moybyte has already adopted Picotron as the north-star
*grammar* (`moybyte_Console_Plan_v0_5.md`: "everything is a cartridge," and its Cycle-2
section names "the Picotron conclusion: the system UI itself runs as carts"). Issue #55
is literally titled after this model. `docs/shell_architecture_v1.md` §2 (privileged
system carts) + §3 (layered compositor) are the two halves of it. What has been missing
is the *boundary mechanism* that makes a system-UI-as-cart safe and honest — which is
the subject of this doc.

---

## 4. The pivot — Moybyte already has a syscall boundary; the shell just isn't behind it

Here is the observation that makes this whole doc practical rather than aspirational:

**Kid carts already run behind a real syscall boundary.** `make_api()`
(`runtime/host_app.py`; the identical device build in
`firmware/lilygo_t_deck_plus_micropython/modules/moy_runtime.py`) constructs a cart's
*entire* global namespace: **42 enumerated names** (draw verbs, input, audio, `pmem`,
`cfg`, `Image`/`image`, helpers) plus a **43rd, `wifi`, injected only when the manifest
grants the `"network"` permission**. A cart cannot name `ws`, cannot import, cannot
reach one byte of shell state. Check the properties from §2.1: explicit (it's a dict
literal you can read), stable (it's the frozen contract of `docs/moy_cart_api.md`),
privilege-gated (`wifi` is a capability grant, §2.2-style). That boundary is *why* kid
carts are portable across host/device/web and sandboxable — the payoff is already
banked, in production, on hardware.

The shell's own surfaces never got that treatment. A `SettingsLayer` is trusted code, so
it was handed the whole `Workstation` — ambient authority — and §1 is the entirely
predictable result. The asymmetry is the bug:

| | Kid cart | Shell surface (today) |
|---|---|---|
| Interface | 42 enumerated names (`make_api`) | ~100 implicit `ws.*` members |
| Can name shell internals? | no — cannot even name `ws` | yes — including 36 `ws._*` privates |
| Can touch other surfaces? | no | yes (`ws.ach_ui._secret_taps = 0`) |
| Privilege gating | manifest capability (`wifi`) | none — every surface gets everything |
| Documented | `docs/moy_cart_api.md` | nowhere (grep is the documentation) |

So the fix is not exotic and requires inventing nothing: **give the system surfaces the
same treatment the kid carts already have.** That is exactly what
`shell_architecture_v1.md` §2.2 drafted as `make_system_api` — a per-surface table of
privileged verbs, derived from a real extraction pass. And the layers refactor, whatever
else it did or didn't achieve, completed the reconnaissance for it: each surface's
`ws.*` footprint is now one grep, and **those measured footprints are the per-surface
syscall lists.** The bar's API is its 28 names, minus the ones §5.2's events replace,
minus the privates that get promoted to real verbs. Nobody has to design the interface
from a whiteboard; the code already voted.

### The mapping table

| OS concept | Moybyte realization |
|---|---|
| Syscall table | `make_api` (kid, shipped) / `make_system_api` (shell, §2.2 draft) |
| Process | Cart (kid, shipped) / surface-Layer → eventually system cart (#55) |
| Capability / handle | The injected `api` object; `wifi` today is the existing precedent |
| Ring 3 vs ring 0 | Kid `make_api` vs system `make_system_api` vs privileged verbs (ota/reboot/del_cart) |
| IPC / message passing | An event bus (pub/sub callbacks in-process, §5.2) |
| Compositor / windows | The Layer stack today; retained per-layer buffers = #73 / §3 |
| Per-process namespace (Plan 9) | Each surface's `api` is exactly its granted verb set |

---

## 5. The migration path — pragmatic, not seL4 cosplay

Everything runs in one MicroPython process and always will on this hardware. So a
"syscall" here is a method call through an injected API object, and a "message" is a
pub/sub callback dispatched synchronously in the same interpreter. That is not a
watered-down version of the idea — it is the *correct* port of it: the decoupling value
of these boundaries comes from what code can *name*, not from hardware privilege levels
or context switches. We get the microkernel's architecture without paying the
microkernel's IPC tax.

Steps, in dependency order. Step 1 is achievable now; each later step builds on the
previous.

### 5.1 Per-surface capability API — kill the `ws` back-reference

Each surface's constructor takes `api`, not `ws`. The `api` object exposes **only that
surface's verbs**, assembled by the shell core from the surface's measured footprint:

```python
# console.py (the shell core / "kernel") — the grant is explicit and per-surface
self.settings_layer = SettingsLayer(make_settings_api(self), NAMES, _in, _clamp_scroll)
```

- The 93/108-member god-API becomes **N small, explicit, per-surface contracts** — the
  §2.1 property. Each contract is auditable ("why does the *bar* need `del_cart`?" is
  now a diff-review question, not archaeology).
- The 36 `ws._*` reaches get resolved one by one at migration time: each either becomes
  a real named verb on the surface's `api` (it was load-bearing contract all along —
  e.g. `_game_xy`, `_draw_menu_backdrop` for game-domain layers, the shared
  `_glyph`/`_btn` draw toolkit) or turns out to be an internal the surface shouldn't
  have touched (it gets a proper verb or an event instead). No private names cross the
  boundary when it's done.
- Direct state writes (`ws.config[...] = v`, `ws.cart_error = ...`, `ws._dirty = True`)
  become verbs (`api.set_config(k, v)`, `api.report_cart_error(e)`,
  `api.mark_dirty()`) — the boundary is also where invariants get to live.
- **Migrate one surface at a time**, exactly like the layers refactor's Phase 2, under
  the same nets (the host test suite + the golden-frame pixel harness). Start with the
  launcher (8 members — the trivial proof), then the bar (28, but the designated #55
  first target). During migration a surface's `api` can be a thin facade *over* `ws`
  internally; the point is what the surface can name, not what the core does behind the
  gate.

### 5.2 An event bus — kill the cross-surface pokes

A minimal pub/sub (`bus.on(name, fn)` / `bus.emit(name, *args)`, synchronous, in-order)
replaces every case of one surface reaching into another:

| Today (surface pokes surface) | Target (surface emits, owner reacts) |
|---|---|
| `ws.ach_ui._secret_taps = 0` from Settings | Settings emits `tap_elsewhere`; achievements service owns its own streak reset |
| `ws.ach_ui._konami_step(b)` from Launcher | Launcher emits `button(b)` (or the bus taps input); achievements tracks the code |
| `ws.bar_layer._dock_slot_at()` from Settings/Launcher | The dock is the bar's geometry: route the tap to the bar layer, or expose one granted verb on those surfaces' `api` — either way, not a private reach |
| `ws.update_ui.open_update()` from Settings | `api.open_screen("update")` — a navigation verb on the core, which owns screen lifecycle |
| `ws.set_icon_sheet` → `bar_layer.invalidate()` (core pokes surface) | Core emits `theme_changed`; the bar invalidates its own strip cache |

The achievements case is the purest win: it is *by nature* a cross-cutting observer
("the kid did X anywhere in the shell"), which is exactly why it currently has tendrils
from three different surfaces. As a bus subscriber it has zero.

### 5.3 Capability tiers fall out for free

Once §5.1 exists, privilege is just *which verbs a grant contains* — the §2.2 trust
boundary, realized:

- **Kid cart:** `make_api` — 42 names, `wifi` by manifest permission. Frozen, shipped.
- **System surface:** `make_system_api` base — the shared shell toolkit (draw helpers,
  layout, navigation, store-read) that §2.2 measured every chrome surface needing.
- **Privileged system surface:** base **plus** the dangerous verbs, granted
  individually per §2.2's evidence table: `ota` only to the update surface, `reboot`/
  `del_cart` only to the system menu, `persist_system`/`set_diag_live`/theme verbs to
  Settings. §2.2's finding 1 ("the dangerous verbs concentrate; two surfaces need zero
  privileged verbs") means most grants are small.

This is precisely §2's system-cart privilege model, arrived at bottom-up — and it makes
§2.1's manifest flag (`"system": true` + trusted-path check) the *loader-side* switch
for which API constructor a cart gets.

### 5.4 The endgame — everything is a process (aspirational)

With surfaces behind capability APIs (5.1), coordinating by events (5.2), and
privilege-tiered (5.3), the remaining distance to Picotron is: run each surface from the
cart runtime instead of linking it into the shell, and give each a retained pixel buffer
the compositor combines (#73 / `shell_architecture_v1.md` §3). That is real work — the
system-cart lifecycle + crash-floor of §2.3, the retained-buffer compositor of §3 — and
it stays gated on the concrete needs already named there (One/P4 hardware, the web view
as window manager). Steps 5.1–5.3 are the on-ramp that makes 5.4 *reachable*; 5.4
justifies nothing before it. The honest ceiling stays §3.4's: one active kid cart plus a
small fixed set of resident system surfaces — this doc does not propose true concurrent
multi-cart execution, ever, on the S3.

---

## 6. Honest caveats — where the analogy breaks, and what this costs

Locked-in means honest about the bill:

- **A boundary has a per-call cost, and this device fights per-frame overhead.** The
  #66 perf ledger's central lesson is that dispatch/call count — not pixel fill — is
  the S3's bottleneck. So: the `api` object must be a *thin* injected object (bound
  methods / direct attribute references, the same cost class as today's
  `self.ws.method` — one attribute load + call either way), never a
  validating/marshalling proxy. The hot draw-toolkit verbs (`_glyph`, the canvas
  itself) are the ones to watch; if a surface draws through the same canvas reference
  it holds today, the frame path gains zero new calls. The golden harness + #66 numbers
  are the regression gate, same as the layers refactor.
- **No rings, no isolation, no enforcement.** CPython/MicroPython can't stop trusted
  code from importing the core and calling anything. The boundary is *conventional* —
  its teeth are that reaching around it is grep-visible (`ws.` in a surface file goes
  from "the norm" to "a lint hit") and diff-reviewable, not that it's impossible. That
  is still most of the value: §1's coupling didn't happen because someone maliciously
  bypassed a boundary; it happened because there wasn't one to bypass. (The *kid* tier
  keeps its real enforcement: `make_api` builds the whole namespace, so a kid cart
  truly cannot name what it wasn't given.)
- **Events are synchronous calls in disguise.** The bus is decoupling of *names*, not
  of *time* — a slow subscriber still blocks the frame, and event storms are possible.
  Keep the event vocabulary small and coarse (screen lifecycle, theme, taps-for-eggs),
  not a per-pixel firehose.
- **The event bus can hide control flow.** `emit("screen_changed")` is harder to trace
  than a direct call. Use events only where the *coupling is the bug* (cross-surface
  observation, one-to-many notification); keep direct verbs for one-to-one commands
  (Settings opening the update screen is a navigation verb, not an event).
- **Don't over-adopt.** No microkernel IPC, no message serialization, no scheduler, no
  per-surface heaps. One process, one interpreter, one active cart (§3.4). The move
  that matters — the ONE thing every system in §2 shares — is a narrow, explicit,
  capability-scoped interface between core and surface, replacing ambient reach-through.
  Everything beyond that is imported ceremony.
- **Frozen things stay frozen.** The kid API (`make_api` / `docs/moy_cart_api.md`) does
  not change — not one name. Host == device stays: every mechanism here is shared
  `runtime/` source staged into firmware `modules/` exactly like `console.py` today.
- **Interface design is now real work.** The god-API had one advantage: zero design
  meetings. Each per-surface grant is a small act of interface design, and getting one
  wrong means churn in two places. Mitigation: the measured footprints mean v1 of every
  grant is *transcription*, not invention; refinement happens on real evidence.

---

## 7. Relationship to the other docs, and tracking

- **`docs/shell_layers_refactor_v1.md` (predecessor).** That refactor built the Layer
  boundary and (its own words) left the `ws` back-ref as "the exact seam the extracted
  UIs already use." The adversarial review of it found the seam is a ~100-member god-API
  — this doc is the successor that turns that #1 finding into a plan. The refactor's
  role in hindsight: it made every surface's true interface *measurable*, which is the
  prerequisite for scoping it.
- **`docs/shell_architecture_v1.md` §2 (privileged system carts).** This doc is the
  concrete "how" underneath it: §2.2's evidence-derived `make_system_api` is exactly
  §5.1+§5.3 here, and §2's trust boundary is §5.3's grant tiers. §2.3's crash floor and
  §2.4's migration order are unchanged and still gate §5.4.
- **`docs/shell_architecture_v1.md` §3 / #73 (layered compositor).** The §5.4 endgame's
  other half: capability-scoped, event-coordinated surfaces are the natural things a
  retained-buffer compositor composites and a web-view window manager ships.
- **#55** — this doc supplies the boundary mechanism ("everything is a process" needs a
  syscall table before it needs a process model).
- **#66** — the perf ledger holds the regression bar for §6's first caveat.

The dependency chain, updated from the layers-refactor doc's version:

**logical Layers (shipped) → per-surface capability API + event bus (§5.1–5.2, the
first step) → privilege tiers (§5.3 / §2) → system carts + retained-buffer compositor
(§5.4 / §2+§3, the Picotron endgame).**
