# Moybyte shell UX v1 — what the user experiences

**Status:** LOCKED / TOP OF STACK — this is the UX north star the shell converges on.
It is the "what the user experiences"; the three existing shell docs
(`shell_layers_refactor_v1.md`, `shell_os_architecture_v1.md`,
`shell_architecture_v1.md`) are the "how" and sit **beneath** it. Where a "how" doc
and this doc disagree about the experience, this doc wins. This doc deliberately does
NOT contain module design, migration phases, or code — the implementation plan is a
separate successor doc. What is locked here is the experience and the contracts;
mechanisms are named only where the UX guarantee is meaningless without one.
**Issues:** #29 (blocks — becomes a graduating Editor tab), #46 (the unified bar —
becomes the zoned OS shell), #71 (exit/pause — resolved by the taskbar-vs-fullscreen
split), #55 (Editor/Settings as privileged system carts), #22/#41 (the web view — one
more window-manager surface), #58 (the P4 "One" — the big-screen presentation), #66
(the perf guardrails §11 bakes in).
**Companion docs (the "how", beneath this one):** `docs/shell_layers_refactor_v1.md`
(the Layer decomposition), `docs/shell_os_architecture_v1.md` (the capability/syscall
boundary), `docs/shell_architecture_v1.md` (privileged system carts + layered
compositor). The kid-facing cart contract (`docs/moy_cart_api.md`) is **frozen** and
unaffected.
**One-line thesis:** there are exactly two concerns — *authoring* a cart and *playing*
a cart — joined by exactly one primitive: `run(cart)` plays until exit and returns
control to whoever called it. Everything else in the shell is an app the OS runs, and
the only thing that differs between a T-Deck, a browser tab, and the P4 is the window
manager.

---

## 1. One console, three presentations

Three tiers run the **same functionality**:

| Tier | Screen | Presentation |
|---|---|---|
| LilyGO T-Deck (ESP32-S3) | 320×240 handheld | one app fullscreen at a time |
| WebView (#22/#41) | whatever the browser gives | whichever model fits the viewport |
| ONE (ESP32-P4, #58) | 1024×600 desktop-ish | windows, several apps at once |

The ONLY difference between tiers is UI presentation — screen size and window model.
No tier has a feature the others lack; no tier has its own shell logic. This is the
existing host == device discipline plus the #39 responsive-layout discipline, extended
one level up: not just "the same pixels reflow," but "the same *apps* present
differently." A kid who learns the console on the T-Deck already knows the P4.

---

## 2. The core model — everything is a process, joined by launch-and-return

This is the Picotron model (`shell_os_architecture_v1.md` §3), stated as UX:

- **Everything the user sees is an app/process the OS runs.** The launcher is an app.
  The Editor is an app. Settings is an app. A running kid cart is an app. There is no
  privileged "the shell UI" that lives outside the process model — the desktop is just
  the set of processes currently running.
- **Two concerns, one primitive.** Authoring (edit) and playing (run) are joined by
  `run(cart) → plays until exit → returns control to the caller`. That launch-and-
  return primitive is the entire relationship between the two halves of the console.
- **The Player is a black box.** It knows nothing about launchers, editors, pause
  menus, or toolbars. It runs a cart under the frozen kid API and guarantees exactly
  one thing: the cart will eventually exit and control will come back. It has no
  opinion about what happens next.
- **The caller decides what's next.** Everything that plays a cart — the launcher, the
  Editor's PLAY button — is just a *caller* of `run`. Launcher → play → exit returns
  to the **launcher**. Editor → play → exit returns to the **Editor**, on the tab you
  left. Same primitive, different caller, and that is the whole difference between
  "playing a game" and "testing my game."

The payoff of this shape is that nothing needs to know about anything else: the Player
never special-cases "was I launched from the editor?", the Editor never special-cases
"how do I get back after a test run?", and adding a new caller (a debugger, a
slideshow, the web view) costs nothing.

---

## 3. The kernel, and the window manager as the only tier-specific layer

The OS kernel is small and identical everywhere:

- the **compositor** (puts app pixels on the screen),
- **spawn/kill** for processes,
- the **filesystem / project store** (carts + their save data + the undo journal, §7),
- **input routing** (which app gets this key/tap),
- the **run/exit contract** of §2 (the Player invocation and its return guarantee).

The **window manager is the only layer that differs by tier**:

| Tier | Window model |
|---|---|
| T-Deck (small screen) | **One app fullscreen at a time + a back-stack.** Launching pushes; exiting pops. There is deliberately NO app-switcher — the stack is the navigation model, and it is always shallow (launcher → editor → playtest is the deep end). |
| ONE / P4 (big screen) | **Windows.** Several apps visible at once — editor beside a running playtest is the canonical picture. |
| WebView | Whichever of the two fits the viewport; a phone gets the stack, a laptop can get windows. |

The small-screen WM is also the **perf gate**: the T-Deck never composites multiple
apps, so a playing cart owns the entire frame budget — full 320×240, no chrome, no
sibling app stealing milliseconds. "One fullscreen app + a back-stack" is not a
compromise UI; it is the mechanism by which games hit their #66 numbers.

---

## 4. Two personas — the launcher's tap default is a mode setting

The console serves two kids (often the same kid at different hours): the one who wants
to **play** and the one who wants to **make**. The launcher honors both with one
setting — what a tap on a cart does by default:

- **Maker mode** (the dev T-Deck, the P4): tap a cart → opens the **Editor, landing on
  the Config page first**. Config is the "Make it mine" cards — pick your character,
  the stars-falling rate, the colors. Intrigue-first: a kid customizes a game before
  ever seeing a line of code, and the path from "I changed the color" to "what else
  can I change?" runs straight through the rest of the Editor's tab ladder (§6).
- **Player mode** (a kid's player device): tap → **plays**.

Both actions (Play / Edit) always exist on every cart in both modes; the setting only
flips which one a bare tap means. Nothing is ever locked away — Player mode is a
default, not a parental wall.

---

## 5. The zoned top bar — the macOS menu-bar model

There is a single OS-owned 18px bar with two zones. This is the current #46 unified
bar, re-framed: it stops being a hardcoded strip of mode buttons and becomes an OS
shell with an app-populated region.

- **LEFT zone = the active app's toolbar.** The OS *lends* the app this region. The
  Editor loads its tab ladder + PLAY here (§6); the launcher fills it with its own
  controls (new/dup/del today); Settings fills it with its sections. The app owns the
  pixels and the taps inside the zone; the OS owns the zone's existence and bounds.
- **RIGHT zone = OS-owned system status**: wifi / clock / battery — each tappable as a
  shortcut into the matching Settings page — **plus a context X** that exits the
  current app (§9). Apps cannot draw here; the right zone is how the OS stays present
  and trustworthy no matter which app is active.
- **The bar HIDES entirely while a fullscreen game plays.** The game owns all of
  320×240 and every input. The bar is authoring/system chrome; play is sacred.

This is exactly the macOS menu-bar deal: one bar, always in the same place, whose left
half belongs to whoever is frontmost and whose right half is the system's — except
that on a games console the "frontmost app" state includes *no bar at all*.

---

## 6. The Editor — one app, a view ladder ordered gentlest-first

The Editor is ONE app, launched on a project. Its left-zone toolbar is a **tab
ladder**, ordered easy → deep, so the leftmost thing a kid sees is always the
gentlest:

```
Config → Blocks → Code → Sprites → Map → Music        [ PLAY ]
```

- **Config** — the default landing tab (§4) and the intrigue rung: the "Make it mine"
  cards. Zero code, zero blocks; sliders, pickers, and choices the cart's
  `config.json` exposes. A kid's first edit is a success in under ten seconds.
- **Blocks** — the beginner's programming view (§8). Snap-together logic that
  *generates* the code.
- **Code** — the real Python source. The Blocks tab's output, and eventually the kid's
  own hands.
- **Sprites / Map / Music** — the existing asset editors, unchanged in what they do;
  they become tabs of the one Editor rather than global console modes. (In the
  codebase these are the already-extracted layer modules — `code_layer` /
  `paint_layer` / `map_editor_ui` / `music_editor_ui` — which become the Editor's
  tabs; see §13.)
- **PLAY** — literally `save(); run(current)` (§2). The cart plays fullscreen; exit
  returns to the Editor **on the tab you were on**. Test-play is a round trip, not a
  context switch.

The ladder is the icons → blocks → code progression (#29) made spatial: growth is
"one tab to the right," and every rung is visible from every other rung.

---

## 7. Save is invisible; Undo is durable

Two guarantees, stated as UX law:

- **There is no Save.** No Save button, no "unsaved changes" state, no save prompt on
  exit — edits persist continuously, always. A kid can pull the battery mid-edit and
  lose nothing. (`commit` in the §10 contract is the app telling the OS "persist
  this"; the kid never sees it.)
- **Undo is real, step-by-step, and survives reboots.** Every project carries an
  undo/redo journal persisted on SD **per project**: step-by-step undo AND redo, so a
  kid walks back a mistake one change at a time — including after power-off, including
  days later. "I broke my game yesterday" is recoverable by a nine-year-old, alone.

The model to note (so the technical plan builds the right thing): an **append-only
edit journal beside the project** — the Google-Docs model, where the document is
always saved *and* always steppable. This paragraph is a UX guarantee, not a design;
journal format, granularity, and compaction are the technical plan's job. The honest
tradeoff is also its job: a durable journal costs SD writes and space, and the
guarantee above is the bar those costs must clear, not an invitation to weaken it.

---

## 8. Blocks ↔ Code — the MakeCode model, with an honest graduation door

Blocks and Code are **two views of ONE program** — the MakeCode model, deliberately
NOT the Scratch model. Scratch is blocks-only; it cannot even ask the question "can
you still edit the code?" because there is no code to edit. Moybyte's whole point is
the ladder, so the two views must coexist — and coexisting honestly means admitting
when they can't:

- **Blocks are the beginner's source of truth and GENERATE the code.** (The compiler
  exists: `moybyte_blocks` already does blocks → Python.) Edit blocks, flip to the
  Code tab, and see what your program *is* — that flip is the single most valuable
  teaching gesture in the console.
- **Blocks → code is always smooth.** Regenerate any time; the code view is never
  stale.
- **Code → blocks only holds while hand-edits stay within the block vocabulary.** Edit
  the code in ways blocks can still express, and the round trip survives.
- **The moment you write code beyond the vocabulary, you've GRADUATED.** The Blocks
  tab goes **read-only** — regenerating from blocks would discard your code, so the
  Editor refuses to, and says so plainly: *"you've leveled up to code."* No silent
  loss, no lying "sync" that mangles one side. Graduation is one-way by design and the
  Editor celebrates it rather than apologizing for it.

This is the icons → blocks → code ladder with a **celebrated one-way graduation
door**. The blunt tradeoff, accepted: a graduated project cannot go back to blocks
(short of undoing past the graduating edit, §7). Any design that pretends otherwise
either restricts the code editor or corrupts kid code — both worse.

---

## 9. The exit model — taskbar-context-dependent

The rule in one line: **taskbar shown → an X in the OS right zone exits (a tap);
fullscreen game → hold-BACKSPACE exits.**

| App | Presentation | Exit affordance | BACKSPACE is… |
|---|---|---|---|
| Launcher (back-stack root) | taskbar | **no X** — it's home; nothing beneath it | a plain key |
| Editor | taskbar | X → back to launcher | a plain key |
| Settings (incl. wifi setup) | taskbar | X → back | a plain key — **delete in the password field just works** |
| Running game | fullscreen | **hold**-BACKSPACE → return to caller (§2) | a game key on a quick tap |

Why this kills the keyboard special cases: because taskbar apps exit via a **tap**,
BACKSPACE is never reserved in them — it stays an ordinary key everywhere a taskbar
exists. The wifi password field gets BACKSPACE=delete **for free**, with zero
special-casing. This retires the #71 "text-mode TOOL keeps backspace as delete" hack,
and the whole pause-screen / double-tap-to-exit lineage that #71 iterated through —
the interlock that made BACKSPACE precious was the bar being one global mode-machine;
zone the bar (§5) and the preciousness dissolves.

For a fullscreen game, a quick BACKSPACE tap still reaches the game (carts may use it
as a key); only a sustained HOLD exits, returning control to whoever called `run` —
launcher or Editor. One niche edge, on the record: a game that wants *autorepeat*
backspace-delete (hold to erase) collides with hold-to-exit. Accepted; it is rarer
and cheaper than every alternative tried (see #71's history of what "clever" cost).

---

## 10. Settings — a privileged system cart

Settings is an app like the Editor — a process the OS runs — but a *privileged* one
(`shell_architecture_v1.md` §2, issue #55). It is launched from the OS right zone (the
gear, or any tappable status icon deep-linking to its page), and it holds the
privileged verbs: wifi, brightness, time, OTA — the `make_system_api` surface of
`shell_architecture_v1.md` §2.2.

Wifi setup lives inside it, and here the exit model (§9) pays off directly: Settings
is a taskbar app, so it exits via the X, so its password field's BACKSPACE=delete
works with no carve-out. The wifi keyboard problem is not solved by Settings — it is
*dissolved* by Settings being an ordinary taskbar app under §9's rule.

---

## 11. The OS ↔ app contract — ~5 verbs each way

The whole conversation between the OS and an app is about ten verbs — versus the
93-distinct-member implicit `ws.*` surface it replaces (measured in
`shell_os_architecture_v1.md` §1):

| Direction | Verb | Meaning |
|---|---|---|
| OS → app | `open(project)` | here is your project; you are now the active app |
| OS → app | `present(left_zone, canvas)` | here is your drawing surface + the lent bar region (§5) |
| OS → app | `route(input)` | this key/tap is yours |
| OS → app | `teardown()` | you are exiting; release everything |
| app → OS | `run(project)` | invoke the Player; I get control back on exit (§2) |
| app → OS | `commit(project)` | persist this state (the invisible auto-save, §7) |
| app → OS | `exit` | pop me off the stack / close my window |

**The OS owns:** the bar shell + right-zone status (§5), the back-stack / window
manager (§3), the Player invocation and its exit guarantee (§2), and the project
store + per-project undo journal on SD (§7).

**Privilege tiers stay as designed** (`shell_os_architecture_v1.md` §5.3): kid game
carts talk only to the Player through the frozen `make_api` — not one name changes.
System carts (the Editor, Settings) additionally receive `make_system_api`. The
contract above is the *shape* of the boundary; the per-surface grant lists and their
evidence live in the "how" docs.

---

## 12. Perf guardrails baked into the UX

These are stated here — as UX-level invariants — precisely so the technical plan
cannot trade them away for architectural tidiness. All three come from the plan
reviews and the #66 ledger's central lesson (dispatch/call count beats pixel fill on
this hardware):

1. **The hot per-frame draw path is NOT routed through an indirection object.** A
   playing cart draws through the same direct path it does today; wrapping the canvas
   in a per-call boundary object would re-add the #43/#63 dispatch tax the native
   batching work spent months removing.
2. **The bar hides during play (§5), so the game owns the frame budget.** No chrome
   compositing, no bar redraw, no status polling on the game's clock.
3. **No per-frame events.** Whatever eventing the OS grows (see
   `shell_os_architecture_v1.md` §5.2's own caveats), its vocabulary is coarse —
   lifecycle, theme, navigation — never a per-frame or per-draw firehose.

A shell redesign that regresses #66's numbers has failed this spec regardless of how
clean its boundaries are.

---

## 13. Relationship to the other docs, and tracking

**The stack, top to bottom:** this doc (the locked "what") → the three "how" docs —
`shell_layers_refactor_v1.md` (the Layer decomposition that made surfaces movable),
`shell_os_architecture_v1.md` (the capability boundary + event bus that make them
honest processes), `shell_architecture_v1.md` (privileged system carts §2 + the
layered compositor §3) → the implementation plan (a separate successor doc, written
next; it sequences the work, this doc does not).

Concretely: the already-extracted layer modules (`runtime/code_layer.py`,
`runtime/paint_layer.py`, `runtime/map_editor_ui.py`, `runtime/music_editor_ui.py`)
become the Editor's tabs (§6). The launcher/settings/bar layers become the launcher
app, the Settings cart, and the zoned OS bar respectively.

**Issue map:**

| Issue | What this spec makes of it |
|---|---|
| #29 (blocks-on-device) | the Blocks tab — a graduating view of the one program (§8) |
| #46 (unified top bar) | re-framed as the zoned OS shell + app-populated left zone (§5) |
| #71 (BACKSPACE / pause / exit) | resolved by the taskbar-vs-fullscreen split (§9); the pause-screen machinery and the text-mode backspace carve-out both retire |
| #55 (system-as-carts) | the Editor and Settings are exactly its privileged system carts (§10, §11) |
| #22 / #41 (web view) | one more window-manager surface (§3) — same apps, browser presentation |
| #58 (ESP32-P4 "One") | the big-screen WM presentation (§3) — windows, not a different console |
| #66 (perf ledger) | holds the regression bar for §12's guardrails |
