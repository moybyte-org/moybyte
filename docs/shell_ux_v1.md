# Moybyte shell UX v1 — what the user experiences

**Status:** SHIPPED (2026-07) / CURRENT UX REFERENCE — the shell described here is
implemented (branch `refactor/shell-ux`), and this doc has been corrected where the
build deliberately diverged from the original lock: §4 (the maker/player tap-mode was
RETIRED in favour of the Editor-as-an-app model), §7 (the bar's compact SAVE icon was
ITSELF retired, #111 -- autosave is the only model now, so there is no persist-now
affordance left to keep), and §9 (tools/apps run with a minimal bar; text-mode games
exit via the new `quit()` cart verb). It remains the "what the user experiences"; the
"how" docs beneath it are
implemented and archived under `docs/history/` (`shell_ux_technical_plan_v1.md`,
`shell_layers_refactor_v1.md`, `shell_os_architecture_v1.md`), while
`docs/shell_architecture_v1.md` stays the standing direction doc. The shipped module
map (`runtime/project.py` / `player.py` / `editor_app.py` / `wm.py` + the shrunk
`Workstation` kernel) lives in `runtime/README.md` and `.claude/rules/shell.md`. This doc deliberately does NOT contain
module design, migration phases, or code; mechanisms are named only where the UX
guarantee is meaningless without one.
**Issues:** #29 (blocks — becomes a graduating Editor tab), #46 (the unified bar —
becomes the zoned OS shell), #71 (exit/pause — resolved by the taskbar-vs-fullscreen
split), #55 (Editor/Settings as privileged system carts), #22/#41 (the web view — one
more window-manager surface), #58 (the P4 "One" — the big-screen presentation), #66
(the perf guardrails §11 bakes in).
**Companion docs (the "how", beneath this one — implemented, archived):**
`docs/history/shell_ux_technical_plan_v1.md` (the staged migration that shipped this
spec), `docs/history/shell_layers_refactor_v1.md` (the Layer decomposition),
`docs/history/shell_os_architecture_v1.md` (the capability/syscall boundary; its
per-surface API track remains open). `docs/shell_architecture_v1.md` (privileged
system carts + layered compositor) is current. The kid-facing cart contract
(`docs/moy_cart_api.md`) stayed frozen; the one addition is the `quit()` verb (§9).
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

*(2026-07-23: a CRASH is an exit too — the **crash-to-code throw**. A cart that
fails to start or raises mid-frame no longer parks on an OOPS panel waiting for
a manual TAP-CODE: the run exits straight into the Editor's Code tab with the
caret on the crashing line, the inline marker set, and a tap/type-dismissible
error popup over it (`ws._crash_to_code`). On a cart of several scripts
(SPEC.md 4) it opens the FILE that raised, not whichever the tab was left on --
on a PICO-8 port that is as often the generated half as the game, and a marker
on somebody else's line N is worse than none (#89). The marker then RE-CHECKS on every
edit/undo — it retires only when the source actually parses again, and follows
the live syntax error while it doesn't. The old panel survives as the
no-open-cart fallback, and for a compiled (`"runtime": "wasm"`) cart, whose
trap has no source line behind it: the panel reports it, offers no EDIT, and
points at HOME. This is still the caller model: the crash path is just one more
pop, into the one place the fix can happen.)*

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

*(Status 2026-07-08: the windowed tier is IMPLEMENTED host-side —
`runtime/wm_windowed.py`, also served over the web transport — with two
owner-directed refinements to this section's sketch: INPUT FOCUS is decoupled
from the back-stack (clicking a
window never pops anything — a playtest keeps running beside a focused editor,
and only an explicit exit ends a run); and closes are surgical (closing one
window never closes another). One cart still runs at a time (#73's multi-cart
boundary). The P4 device port of this tier rides #58.)*

*(Status 2026-07-20 — TWO WORLDS, #105; two MODES since 2026-10-13,
`docs/shell_layout_2026-10.md` §3: the windowed tier BOOTS onto the
**desktop** — wallpaper + a static icon column (PLAY, PROJECTS — the home in
a window, on Make — and the system apps) + the one OS bar with taskbar chips — desktop mode's floor:
everything opened there is a window, and the desk bar draws no X (nothing is
beneath it). The **PLAY icon** enters console mode: the desk's windows wait
parked, off the back-stack, and the fullscreen **home** shows — the same
home every board has (`docs/shell_layout_2026-10.md` §12: its Games / Mine /
Apps / Make tabs, the Games tab holding no app): a tap runs a game
FULLSCREEN exactly like the small tiers (the pause menu's Quit exits back to the
home), and a tap on the Make tab and a card's CHANGE open the Editor
fullscreen, as there. The home's X — B from a keyboard — is the way back to
the desk, its windows where they were. The Player tier only ever gets the
home; its desktop story is the web view.)*

*(Status 2026-07-31 — PLAY INTENT, #178: `run(cart)` stays one verb, but the
windowed tier reads WHY it was called to pick the playtest window's default
size. A **PLAY from the Editor is a dev action** — the window opens at **2× of
the cart canvas, centred over the Editor**, where the desk holds it, else at
1× at the Editor's top right, clear of its symbol keys (owner, 2026-10-19:
the game is the point of the window); a **run from the desk is a play
action** and opens as big as the desktop fits. The size is read ONCE, at window creation, so a window the kid
resizes is never re-sized under them. No new UI and no setting — the two entry
points already carry the meaning. An explicit scale chooser and per-project
memory remain open on #178.)*

---

## 4. Two personas — making is an app, a tap on a played tab plays

*(As originally locked, this section was a per-device "tap-mode" setting — maker vs
player. That was built, then RETIRED during implementation: a modal tap is invisible
state, and dispatching on cart type made a tap unpredictable. What shipped is
simpler and mode-free.)*

The console serves two kids (often the same kid at different hours): the one who wants
to **play** and the one who wants to **make**. Both get a first-class, always-visible
door — no setting, no mode:

- **A tap on the home's Games, Mine and Apps tabs RUNS the cart** (an app's
  cart opens the app). Every cart type, no per-device default, no type
  dispatch. The fourth tab, **Make**, is the one list of projects (owner,
  2026-10-14) and a tap there EDITS (owner, 2026-10-13) — the one tab whose
  tap makes rather than plays (`docs/shell_layout_2026-10.md` §12).
- **The home is a carousel** (2026-10, `docs/shell_layout_2026-10.md` §12):
  one row of cover cards slides sideways under the tab row, the centre card
  larger; a card tap activates on RELEASE (tap-vs-drag slop, the shared
  `ui.DragTap` machine the Settings rows ride too), so a scroll can never
  launch a cart. The SAME home renders on every tier, the 320×240 T-Deck
  included. Its other arrangement, and the one the desk's PROJECTS window
  always shows, is the **shelf**: a grid of two rows that scrolls
  continuously LEFT-RIGHT (2026-07-13 — paging retired), Make's ＋New card
  heading its list.
- **Scrolling is KINETIC** (#113, 2026-07-22): a fast release FLINGS the row,
  which coasts with friction and hard-stops at the ends; a slow or held release
  is a plain stop, and a touch mid-coast CATCHES it (that catching tap never
  activates a card). The physics lives in the shared `ui.ScrollRegion`, so the
  shelf flings identically; scroll/fling frames repaint via
  the scroll-as-blit band path (shift the retained pixels, redraw only the
  exposed strip), which is what makes a 60Hz coast affordable on every backend.
- **The selected card also exposes the two verbs PLAY / CHANGE** (visual identity
  v1 §1.2): PLAY runs it (same as the tap), CHANGE opens the SAME project in the
  Editor landing on Config — the bridge from playing to making, in place, never a
  surprise duplicate. The verbs ride the lent bar zone as chips on every tier,
  the active tab's set. The `C` (code) key is the keyboard route.
- **Making is an app you launch:** the home's **Make** tab — fullscreen, or on
  the desk in the PROJECTS window — lists EVERY editable cart (wallpapers and
  built-ins included) after a **＋New** card. Picking a project opens it in the
  Editor, **landing on Config first** — the
  "Make it mine" cards, intrigue-first: a kid customizes a game before ever seeing a
  line of code, and the path from "I changed the color" to "what else can I change?"
  runs straight through the Editor's tab ladder (§6).
- **Make manages projects:** ＋New (create a game and open it), COPY, and DEL
  (which asks first: a deleted cart is gone for good) are Make's verbs; the tabs
  that play carry none of them but an empty Mine's ＋New.
- **Wallpapers are backdrop-only:** they are on none of the tabs that run. The backdrop
  (and the panel theme) is chosen in the **Appearance app** — the ONE appearance
  surface, reachable from Settings' APPEARANCE row; wallpaper carts stay editable
  on Make.
- **The PROJECTS window sits on a static backdrop** (the panel field): the
  desk is the kid's decorated wallpaper, a window is software. With no
  animated backdrop forcing repaints, an idle window costs zero under the
  redraw-on-change gate.

Nothing is ever locked away: Play is a tap, Make is a tab, and both are visible from
every screen a kid starts on.

---

## 5. The zoned top bar — the macOS menu-bar model

The default layout is a single OS-owned 18px top bar with two zones; a theme's
layout places the same widgets in bars on any edge (`docs/shell_layout_2026-10.md`
§4). This is the #46 bar, re-framed: it stopped being a hardcoded strip of mode
buttons and became an OS shell with an app-populated region.

- **LEFT zone = the active app's toolbar.** The OS *lends* the app this region. The
  Editor loads its tab ladder + PLAY here (§6, no SAVE -- #111); the home puts
  the active tab's chips there for its selected card (§4); Settings
  fills it with its sections. The app owns the pixels and the taps inside the zone;
  the OS owns the zone's existence and bounds.
- **RIGHT zone = OS-owned system status**: wifi / clock / battery — each tappable,
  opening its component (the WIFI panel, the calendar, the power rows) as a popover
  in desktop mode and fullscreen in console mode (`docs/shell_layout_2026-10.md`
  §8). Apps cannot draw here; the right zone is how the OS stays present
  and trustworthy no matter which app is active.
- **The bar HIDES entirely while a fullscreen game plays.** The game owns all of
  320×240 and every input but Super, which pauses it (§9). The bar is authoring/system chrome; play is sacred.
  (A running *tool/app* cart is the exception: it keeps a minimal bar — the
  back chevron, its title and the status — so it is always exitable by tap, §9.)

This is exactly the macOS menu-bar deal: one bar, always in the same place, whose left
half belongs to whoever is frontmost and whose right half is the system's — except
that on a games console the "frontmost app" state includes *no bar at all*.

---

## 6. The Editor — one app, a view ladder ordered gentlest-first

The Editor is ONE app, launched on a project. Its left-zone toolbar is a **tab
ladder**, ordered easy → deep, so the leftmost thing a kid sees is always the
gentlest:

```
Config → Blocks → Code → Sprites → Map → Scene → Music   [ PLAY ]
```

- **Config** — the default landing tab (§4) and the intrigue rung: the "Make it mine"
  cards. Zero code, zero blocks; sliders, pickers, and choices the cart's
  `config.json` exposes. A kid's first edit is a success in under ten seconds.
- **Blocks** — the beginner's programming view (§8). Snap-together logic that
  *generates* the code.
- **Code** — the real Python source. The Blocks tab's output, and eventually the kid's
  own hands. Carries a full small-editor toolset behind an on-screen tool palette
  (#89): text selection with copy/cut/paste (an internal clipboard, identical on
  device), incremental find, a line-number gutter (on by default), and block
  indent/outdent — every verb touch-reachable, with host Ctrl+C/X/V/F/Z/Y as
  conveniences on top.
- **Sprites / Map / Scene / Music** — the asset editors, each with an icon-buttoned
  tool row (#90/#91/#92): the sprite painter has PICO-8's row (pen, fill, shape,
  select, stamp) with stroke-level undo/redo, and its operations
  (copy/flip/rotate/shift/mirror/clear, on the selection or the whole sprite)
  under a more menu and PICO-8's keys (`docs/look_2026-10.md` step 24); the map editor has
  per-gesture undo/redo, rectangle + flood fills, map resize, and a zoom ladder
  whose last rung is an OVERVIEW -- each cell a solid block of its tile's dominant
  colour, because no tile can be drawn below 8px, at 4-7px per cell: the whole map
  where it fits, panning where it does not (#215);
  the scene placement editor (#85 Stage 2) places tagged actors WYSIWYG on the world (tap =
  place/select, drag = move or pan, snap toggle, tag/flip props, front/back
  z-order, per-gesture undo/redo — every committed gesture live-syncs so PLAY
  runs the freshest placement); the music editor has copy/paste/duplicate,
  step/slot reorder, and undo/redo. They are tabs of the one Editor rather than
  global console modes. (In the codebase these are the extracted layer modules —
  `code_layer` / `paint_layer` / `map_editor_ui` / `scene_editor_ui` /
  `music_editor_ui` — owned as tabs by `runtime/editor_app.py`; see §13.) The Blocks tab likewise carries subtree copy/paste/duplicate, an
  explicit cross-parent MOVE flow, and outline undo/redo (#93). Tool buttons
  draw from the shared 12×12 glyph vocabulary (`native/moy_ui/style.json`, drawn by `ui.glyph`), so every tool
  reads pre-literate.
- **PLAY** — literally `commit(); run(current)` (§2). The cart plays fullscreen (on a
  desk, in the workspace's Run slot or Game tab); exit returns to the Editor **on
  the tab you were on**. Test-play is a round trip, not a
  context switch.
- **B and Esc** back out one level: to wherever the Editor was opened
  from, the home's tab and card included. The Editor's one bar draws no
  chevron and no Wi-Fi, so the open tab is named
  (`docs/media/look_2026-10/EDITOR_LAYOUT.md`'s handheld); Enter plays.
  Another project is the home's Make tab (§4); the bar has no way there of
  its own.
- **On a desk** the Editor shows several of these at once, as five
  workspaces (Settings, Code, Art, World, Sound) whose dividers move, one
  pane at a time where the glass holds none of them
  (`runtime/editor_desk.py`, `docs/look_2026-10.md` step 30).

There is no SAVE (#111): the bar used to carry one compact persist-now icon, but
autosave (§7) is the only model now, so it was removed along with the concept it
stood for. Every hard exit (PLAY, a window close or the bar's back, a workspace
swap, going home) hard-commits whichever tab was showing, on top of the idle
debounce -- exactly what SAVE used to do, just automatic. Switching tabs OWES the
commit rather than taking it inside the tap (#154): the frame that paints the
destination pays it, and the next hard exit pays anything still owed. The debounce
runs on EVERY tab (#154), so by the time one of those events fires it usually finds
nothing left to write.

The ladder is the icons → blocks → code progression (#29) made spatial: growth is
"one tab to the right," and every rung is visible from every other rung.

---

## 7. Save is invisible; Undo is durable

Two guarantees, stated as UX law:

- **Save is never required — there is no SAVE.** No "unsaved changes" state, no save
  prompt on exit, no SAVE button anywhere (#111): edits persist continuously — a
  an idle autosave debounce on every tab -- armed by typing on the code tab and by
  touch on the drawn ones -- plus hard commits on every hard exit (PLAY,
  a window/context-X close, a workspace swap, going home, and the reboot into a new
  firmware image), and a tab switch's commit paid by the frame that paints the
  destination. A
  kid can pull the battery mid-edit and lose (at most) the last idle-debounce window.
  (`commit` in the §10 contract is the app telling the OS "persist this" — the exit
  paths above are simply every place that telling now happens automatically.)
- **Undo is real, step-by-step, and survives reboots.** Every project carries an
  undo/redo journal persisted on SD **per project**: step-by-step undo AND redo, so a
  kid walks back a mistake one change at a time — including after power-off, including
  days later. "I broke my game yesterday" is recoverable by a nine-year-old, alone.

As shipped: an **append-only edit journal beside the project** — the Google-Docs
model, where the document is always saved *and* always steppable
(`<cart>.moy/journal/`: an append-only `journal.jsonl` + full-file snapshots + an
atomic cursor, torn-snapshot-safe; Ctrl+Z / Ctrl+Y walk it in the code editor).
Durable steps are commit-granular — one journal entry per idle-debounce/tab-leave/
PLAY commit, not per keystroke; finer undo stays in-RAM per editor. The format,
cadence, and compaction details live in the archived technical plan
(`docs/history/shell_ux_technical_plan_v1.md`, Stage 7).

---

## 8. Blocks ↔ Code — the MakeCode model, with an honest graduation door

Blocks and Code are **two views of ONE program** — the MakeCode model, deliberately
NOT the Scratch model. Scratch is blocks-only; it cannot even ask the question "can
you still edit the code?" because there is no code to edit. Moybyte's whole point is
the ladder, so the two views must coexist — and coexisting honestly means admitting
when they can't:

- **Blocks are the beginner's source of truth and GENERATE the code.** (The compiler
  exists: `runtime/blocks.py` does blocks → Python.) Edit blocks, flip to the
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

The rule in one line: **back goes one level, a window's X closes it
(owner, 2026-10-18); taskbar shown → the back on its bar exits (a tap);
fullscreen game → the console's key opens the pause menu, whose Quit exits.** On a console
bar (one screen stack, no windows) the way back is the **back chevron** at
the head of the bar's zone, after the Moy button: the bar draws it wherever
there is somewhere to go back to and its layout places no `close` widget
(`BarLayer.draws_back`; the shipped themes place none, a theme without a
look keeps the X, `docs/shell_layout_2026-10.md` §9). The chevron, Esc and B
do the same thing, and on an app's first page they leave it
(`Workstation.app_back`; docs/look_2026-10.md step 27a). On the desk the X
lives only in a window's title strip and closes the window.

| App | Presentation | Exit affordance | BACKSPACE is… |
|---|---|---|---|
| Launcher (back-stack root) | taskbar | **no back** where it is its mode's floor (nothing beneath it); on the windowed tier's console mode, back → the desk beneath | a plain key |
| Desk (the windowed tier's desktop-mode floor) | taskbar + windows | **none** — the PLAY icon enters console mode; a window's X closes it | a plain key |
| Editor | taskbar | back → pop back, to where it was opened from | a plain key |
| Settings (incl. wifi setup) | taskbar | back, Esc or B → a page of it, else out of it | a plain key — **delete in the password field just works** |
| Running game | fullscreen | the console's key (§9a) → the pause menu's Quit → return to caller (§2) | a plain key the game reads |
| Registered app (Calc, Files, …) | the bar: Moy, back, title, status | back, Esc or B → a page of it, else return to caller | a plain key — delete in text fields |
| Running tool/app cart | minimal bar (back + title + status) | back → return to caller | a plain key — delete in text fields |

Why this kills the keyboard special cases: because taskbar apps exit via a **tap**,
BACKSPACE is never reserved in them — it stays an ordinary key everywhere a taskbar
exists. The wifi password field gets BACKSPACE=delete **for free**, with zero
special-casing. This retires the #71 "text-mode TOOL keeps backspace as delete" hack,
and the whole BACKSPACE pause-screen / double-tap-to-exit lineage that #71 iterated through —
the interlock that made BACKSPACE precious was the bar being one global mode-machine;
zone the bar (§5) and the preciousness dissolves.

For a fullscreen game BACKSPACE reaches the game like any other key, in every input
mode (a text game's delete included, autorepeat or none). The way out is the
console's key (§9a): its pause menu's Quit returns control to whoever called `run` —
launcher or Editor.

### 9a. The console key (owner call, 2026-10-18)

Every device has one key the console keeps for itself, in every input mode, and no
cart ever reads it. It is the `start` button (`Workstation.system_key`,
`docs/look_2026-10.md` step 25a), and each input spells it once:

| input | the console key |
|---|---|
| a BLE or USB keyboard | its GUI key tapped (the simulator's Super, Cmd or Meta): it acts on its release, and only when no other key went down while it was held, so Super leads chords (`native/moy_input/moy_input.h`'s key events, 2026-10-21) |
| the T-Deck's keyboard | **Mic**: d0 bit 6 of the raw matrix, and in the C3's ASCII mode (which sends no byte for a bare Mic) Shift+Mic (`firmware/lilygo_t_deck_plus_mainline/README.md`); inside a text field being typed into, Mic is the field's Cancel instead (owner call 2026-10-18: K types a letter there, and Mic never types) |
| a gamepad | Start |
| the browser page | a Meta tap (on a Mac Cmd is Ctrl, so there it is the page's ☰ and Esc), the page's ☰, and Esc during play (a page rarely sees Super) |
| touch | the bar's Moy button, where a bar shows |

A tap over a fullscreen game opens the **pause menu**; anywhere else it opens the
**system menu** (the Moy button's, which takes no focus). Pressed again it closes
the one it opened, and **Esc** closes either and steps back (Options to the pause
menu, the pause menu to the game). The pause menu holds the run — no tick, no draw,
its `time()` and its music stopped where they are — and offers Resume, Restart (the
same cart from the top, the same caller), Options (the console's Volume) and Quit
(return to caller); Resume gives the cart its next frame as if no time passed.

Nothing reads a hold of BACKSPACE, an ordinary key on every device and in every
mode. A **`textmode(True)` game** reaches its pause menu through the console key
like any other, so it builds no way out for itself; `quit()` stays a cart verb
(`docs/moy_cart_api.md`), for a cart that ends itself.

A fullscreen game hides the bar and with it the Moy button, so a board whose only
input is touch has no console key during play. A touch-only exit gesture was
DECLINED by the owner (`firmware/guition_jc3248w535/README.md`): the way out there is
a paired keyboard's GUI key; tools and apps keep the bar's back.

---

## 10. Settings — a privileged system cart

Settings is an app like the Editor — a process the OS runs — but a *privileged* one
(`shell_architecture_v1.md` §2, issue #55). It is launched from the OS right zone (the
gear, or any tappable status icon deep-linking to its page), and it holds the
privileged verbs: wifi, brightness, time, OTA — the `make_system_api` surface of
`shell_architecture_v1.md` §2.2.

Wifi setup lives inside it, and here the exit model (§9) pays off directly: Settings
is a taskbar app, so it exits via the bar's back, so its password field's BACKSPACE=delete
works with no carve-out. The wifi keyboard problem is not solved by Settings — it is
*dissolved* by Settings being an ordinary taskbar app under §9's rule.

*(Status 2026-07-08: implemented — Settings carries a WIFI row + panel
(scan/pick/password/connect/forget over the injected wifi service), and because
Settings is an app, wifi setup works while a game keeps running. The panel is
the one wifi implementation: the bar's wifi widget opens it as a popover in
desktop mode and as the Settings page opened at it in console mode
(`docs/shell_layout_2026-10.md` §8; the wifi tool cart was deleted for it on
2026-10-14). Settings also
gained a THEME row — selectable panel colorways, `chrome.THEMES`. 2026-07-11:
the theme tokens grew the visual-identity-v1 §4.3 SEMANTIC roles — desktop /
surface / ink / focus / play / author / danger etc., resolved for every theme
with the frozen literals as fallbacks — and an opt-in **"machine"** theme ships
the Open Machine colorway: dark-blue construction field, navy dot grid/chrome,
grape identity, green PLAY / orange authoring / yellow focus. The default is
Moy's Day, the 2026-10 look, since `docs/look_2026-10.md` step 27 (2026-10-18),
which deleted `night` and `machine` with the other old seeds. 2026-07-23: every theme family ships a **dark AND a
light variant** (Appearance → THEMES → DARK/LIGHT chips, persisted
`theme_variant`), and the tokens now reach the whole system: the OS bar,
Settings rows + WiFi panel, the ≡ menu/About, achievements, the OTA screens,
the windowed-WM strips/chips, and the editor tabs on every tier, the 320×240
one included (its frozen dark literals retired with theming's phase 2b,
2026-10-11). Bar icons derive plate-free
light-mode sprites at draw time (`Workstation._bar_image`), so wifi/battery
read on a light band. See docs/visual_identity_v1.md §4.3 for the role
contract.)*

Two owner-facing diagnostics rows live here too (#68, both persisted, both default
OFF — together they are "kid mode"): **PERF DIAG** turns on the measurement
machinery (the capture meters and every periodic serial line — PERF, the diag
tick, the audio rate — each of which costs felt hitches, so it is never on for
play), and **DIAG SD LOG** separately gates the periodic
diag→SD write (~115ms every 20s) so a serial-attached measurement session runs
stutter-free; crash/cart-exit diag flushes happen regardless.

**UNKNOWN SOURCES** (owner, 2026-09-29; persisted, default OFF) is the owner's
switch for compiled carts whose module carries no signature — a cart somebody
rebuilt from its source. Turning it ON replaces the rows with a warning
("Unsigned carts can do anything on this console. Only run ones you
trust.") whose focus starts on KEEP OFF, so it takes the
TURN ON button, or a move and a press; turning it OFF is one tap. With it on,
an unsigned module runs at full native speed; with it off the module is
ignored and the cart plays on the interpreter instead, with a short system
notice ("isn't signed") saying so (2026-09-30, "A cart survives its
firmware") — never the blocking crash panel, either way. A module whose
signature is present and fails is refused either way
(`native/moy_wasm/README.md`, "Unknown sources").

---

## 11. The OS ↔ app contract — ~5 verbs each way

The whole conversation between the OS and an app is about ten verbs — versus the
93-distinct-member implicit `ws.*` surface it replaces (measured in
`docs/history/shell_os_architecture_v1.md` §1):

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

**Privilege tiers stay as designed** (`docs/history/shell_os_architecture_v1.md`
§5.3): kid game carts talk only to the Player through the frozen `make_api` — the one
addition is `quit()` (§9). System carts (the Editor, Settings) additionally receive
`make_system_api` — the per-surface capability track is the part of the OS-arch doc
that remains open (the shipped shell moved the tabs' *data* access onto `Project`;
the draw toolkit still rides the `ws` seam). The contract above is the *shape* of the
boundary; the per-surface grant lists and their evidence live in the archived "how"
docs.

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

**The stack, top to bottom:** this doc (the "what") → the implemented "how" docs, now
archived under `docs/history/` — `shell_ux_technical_plan_v1.md` (the staged migration
that shipped the 2026-07 shell), `shell_layers_refactor_v1.md` (the Layer decomposition
that made surfaces movable), `shell_os_architecture_v1.md` (the capability boundary;
its per-surface `make_system_api` track remains open) — plus
`docs/shell_architecture_v1.md` (privileged system carts §2 + the layered compositor
§3), which stays current.

Concretely, as shipped: the extracted layer modules (`runtime/code_layer.py`,
`runtime/paint_layer.py`, `runtime/map_editor_ui.py`, `runtime/music_editor_ui.py`,
`runtime/cards_layer.py`, `runtime/block_editor_ui.py`) ARE the Editor's tabs, owned
by `runtime/editor_app.py` (§6); `runtime/player.py` is the Player (§2);
`runtime/project.py` is the project workspace + commit verbs (§7, §11);
`runtime/wm.py` (`FullscreenStackWM`) is the small-screen window manager (§3); the
launcher/settings/bar layers are the launcher app, the Settings app, and the zoned OS
bar. `Workstation` (`runtime/console.py`) is the kernel — see `runtime/README.md` for the
module map.

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
