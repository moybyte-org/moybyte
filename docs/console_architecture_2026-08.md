# Console architecture 2026-08 — the shape that sticks

**Status: EXECUTED (2026-08-27)** — all five landings shipped on the
`shell-carve` branch, one object per commit, every gate run the same night
(the record is #209's landing comment; CLAUDE.md carries the dated entry).
This document is now the record of the shape, kept at revision 3 as landed;
the per-landing deviations it did not predict are recorded in the six
collaborator commits' own messages. Originally:
**PROPOSAL, REVISION 3 (2026-08-26)** — rev 2 folded the three
parallel adversarial reviews (architecture, performance, evidence); rev 3
folds the owner's first-pass comments (achievements event-push; gates batched
9 → 5). The forward-looking half of the debt program: `docs/refactor_debt_2026-08.md`
(tracked **#209**) is the survey and stays as evidence; this doc is the target
architecture and the migration program. Related standing issues: #208 (shared
bodies with no executable coverage), #207 (the `colors=` skin hatch), #206
(board-port copies), #203 (the PPI chrome floor).

**What the review pass changed (rev 1 → rev 2).** The destination survived all
three reviews; what changed is what a stage has to contain:

1. **The carve is a shared-STATE problem, not a method-forwarding problem** —
   the clusters' load-bearing surface is mutable attributes read (and written)
   from other modules. Every stage now ships a state table first, and the
   dict-identity rule (§3a) is the mechanism that keeps `ws.system` aliases
   honest without the banned `property` forwards.
2. **The pilot flipped to WebConsole** — its state is two attributes;
   `system.json` is the most-aliased dict in the shell.
3. **A sixth cluster: Appearance** (~18 methods) — rev 1's map left
   `select_wallpaper` (which crosses four clusters in one verb) and the whole
   theme/skin/font-scale/wallpaper/icon-sheet family unassigned, which is also
   where #207's triage and §7's toggle registry land.
4. **A `StoreHandle` seam** — the (store, root, can_manage, with_sd) guard
   4-tuple repeated in four bodies today; the storage roles and both
   store-touching clusters take it, which scopes rev 1's over-broad "roles
   narrowing makes private reach structurally impossible" claim.
5. **CoverCache is not self-contained** — its lifecycle is driven by
   `_apply_items` and the frame loop, and the #186 free-order invariant lives
   in two bodies. The object's surface is now designed up front and the
   invariant gets its own mutation-checked test.
6. **The ratchet grew a `getattr`-companion and on-glass gates** — 87
   `getattr(ws, …, default)` sites fail *silent* when an attribute moves, and
   the dev channel + three on-glass suites drive `ws.*` in ways host CI never
   executes.
7. **The A/B protocol grew the S3 arm, GC cadence, idle-paints-zero and
   p90/max** — P4 medians cannot see the S3s' allocation-stutter class, the
   one regression this codebase has actually produced.
8. **LayoutBase re-homed to a new leaf module** — `chrome.py` imports its
   geometry constants FROM `bar_layer`/`settings_layer`/`code_layer`
   (chrome.py:45–66), so a base living beside `chrome.Layout` is unimportable
   by exactly the modules #203 names as consumers.
9. **Numbers corrected** (evidence pass): the layout heads are **eight**, not
   nine (#209's list of seven already contained CodeLayout — and it lives in
   chrome.py, not its own file); the cover cluster is **13** methods; the class
   span is **5,435** lines (the survey's 5,472 included `wire_workstation_core`);
   the goldens sit at **97×5 + 298** today (87/300 was the 2026-08-19
   snapshot); two-player added **two** ws methods, not one.
10. **A live bug fell out of the review**: `_icon_cache` (console.py:1130) is
    written at one site and cleared at none — `_apply_items` clears every
    `_cover_*` structure but not it, so a re-seed/sync keeps stale desk icons
    and a deleted cart's Image leaks. Fix independent of this program; assign
    the cache to the CoverCache stage.

**Rev 2 → rev 3 (owner review, same evening):**

11. **Achievements flip from frame-poll to event-push** (owner comment on the
    artifact): they stay a separate class, but the class GENERATES the effect
    and the kernel EXECUTES it — an unlock writes its overlay deadlines
    (toast/confetti/egg) into flat kernel fields, the shape `notice()` already
    uses, so `_animating`/`_overlay_sig` read plain attributes and nothing
    calls into the achievement objects per frame. The objects are called only
    while an overlay actually draws (cold). SystemStore still owns only their
    persistence.
12. **Gates batched 9 → 5.** One object per COMMIT stays (bisectability on the
    host nets); the expensive unit is the GATE (three boards flashed, suites,
    benches), and gates are sized by risk, not by object — see §8.

## 1. The thesis: this repo already discovered its architecture

Every 2026-08 refactor that STUCK follows the same four rules, and every debt
that keeps costing violates at least one:

1. **One body per mechanism.** `moy_flush`, `BandedCompositor`,
   `device_boot.FrameLoop`, `DevChannel`, `moy_sync`, `SystemCanvas`,
   `lua_ext.py` (twinned copies crashed layer carts on the host until it was
   one file).
2. **Wiring is data in a registry something CONSUMES.** `board.toml` denylists,
   `app_decls.py` (five hand lists gone, four failing silently), `skin.py`, the
   sync-roots registry (`a960563`), `FILE_KINDS`.
3. **Boundaries are declared capabilities.** `app_context` roles + `NEEDS`,
   `system_api`'s permission allowlist, `fold_supported`-absence.
4. **Every boundary gets an executable ratchet.** `test_app_registry`,
   `test_skin` (which pins skin *knowledge* to exactly two owner modules —
   console + the Appearance app — plus one declared forwarder),
   `test_staging_closure`. Anti-evidence: #208's `fold=0`, green for weeks
   behind a test that pinned a format string.

`Workstation` (runtime/console.py:806 — **5,435 lines, 275 methods**; the
survey's 5,472 included the module-level wiring function) predates all four
rules and is where un-promoted mechanisms live as attribute scatter — the
web-console switch added ~10 methods, two-player added two, crisp pixels one.
**The program is "finish applying the four rules to the shell kernel", not
"split the big class."**

## 2. Corrections to #209's carve (as amended by the reviews)

**2a. The extracted objects back the roles — with the claim properly scoped.**
Today every role wraps `ws` and reaches its privates (`Prefs.set` →
`ws._persist_system()`, app_context.py:666–669; nine `ws._*` sites across seven
private names in that file). The carve makes each single-backed role (prefs,
notify) narrow one object. But the **storage roles straddle by construction**:
`_StoreRole.readable/ready/_session` read `ws.carts_store`/`carts_root`/
`can_manage`/`_with_sd` — the exact attributes 2b keeps flat. The fix is the
**`StoreHandle`**: a small object bundling (store, root, can_manage, with_sd),
the same guard 4-tuple `_persist_system`, `_save_achievements`, `rescan_carts`
and every `_StoreRole` verb re-derive today. SystemStore, CartManager and the
storage roles all take it. And the honest framing (system_api.py records
MicroPython does no name mangling, so `__ws` privacy was only ever a host-side
speed bump): the win is **one owner per piece of state**, not access control.

**2b. `ServiceRegistry` stays declined** — no runtime enumeration consumer
exists; `wire_workstation_core` (which replaced three hand-copies, per its own
docstring) remains the one body. Two footnotes from review: (a)
`test_board_service_parity` already keeps a `SERVICES` list and *scrapes the
wiring from source* — the #208 grep-test shape; a small declarative table read
by BOTH the function and the test is rule-2-shaped without a runtime registry.
(b) #206 schedules a second shared wiring body (`wire_optional_services` on the
boot spine); the ordering contract between the two functions must be stated
when it lands, or they become the next twin copies.

**2c. Six clusters, not four.** Rev 1 added HistoryRouter (~28 methods — the
bar undo/redo + journal walk + save funnels; after the cover split it is the
largest coherent chunk) and split CoverCache (13 methods + `_CoverImage`/
`_CoverJob`) out of CartManager. Rev 2 adds **Appearance** (~18: theme/skin/
variant, font scale, wallpaper select/cycle + its persist, icon sheet,
`light_chrome`) — the home of `select_wallpaper`, which crosses CartManager
(rehydrate), SystemStore (persist), achievements (note) and the kernel in one
verb and fit nowhere in rev 1's map.

**2d. Extraction needs coverage, not just goldens** — unchanged, sharpened:
each object lands with direct mutation-checked tests, and CoverCache's #186
free-order invariant (today duplicated between `_apply_items` and
`_release_cover_caches`) becomes ONE body with its own perturbation test.

## 3. The mechanics the reviews forced

**3a. State tables and dict identity.** Before any stage's code moves, write
its state table: attribute → external consumers → migration verb. The known
hard ones: `ws.system` is written raw by settings_layer (:844, :847),
dev_channel (:524) and read per-paint by the launcher (favorites, :840–841);
`ws._cover_gen` has 7 launcher sites + wm_windowed's drag keys; `ws._all_carts`
is read by dev_channel, wm_windowed, app_context. The mechanism that keeps
aliases honest without property forwards: **SystemStore owns the dict and its
`load()` mutates it in place (clear+update)** — `ws.system` stays one plain
alias forever, and the CrashGuard-takes-a-callable wart (console.py:1120–1124,
which exists because `load_system` rebinds the dict today) retires. For
`_cover_gen`/`_all_carts` there is no alias trick: the object exposes a plain
attribute (`covers.gen`) and the consumer sites migrate **in the same stage**
— stages 3 and 4 are cross-module stages and say so.

**3b. Forward rules.** Migration forwards are plain methods with **fixed
signatures** — a `*a, **kw` shim allocates a tuple per call, the churn class
#63/#66 were about. The frame loop, `handle_input`/`handle_pointer` and
`device_boot` never call through a forward: they call the collaborator (or the
method stays kernel) from the first commit; forwards exist only for the long
tail the ratchet burns down.

**3c. Hoisting has a wiring-order trap.** Collaborator *objects* are
constructed in `Workstation.__init__` and may be bound-hoisted anywhere. Late-
injected *services* (store, webhost — `None` at construction, injected by
`wire_workstation_core`; system.json loads later still) are read through `ws`
per call or handed over at wire time — never captured in a collaborator's
`__init__` (the `make_webhost` pin-at-start precedent). So SystemStore is
"rooted at wire time through one setter", not "constructor-rooted";
`load_system`'s apply *cascade* (it calls eight `set_*` verbs and
`select_wallpaper`) stays kernel.

**3d. The ratchet's blind spots, closed.** Three consumer classes a
forward-count test over console.py cannot see: 87 `getattr(ws, "…", default)`
sites (a moved attribute silently returns the default — dev_channel's `state`
snapshot is built this way), the on-glass suites driving `ws.*` over serial
`py` (green in every host CI run, red only with a board on the desk), and
`moy_webhost`'s construction-time lambdas capturing `ws.web_pin`/
`ws.rescan_carts`/`ws.launch_named`. Fixes: a **name-string ratchet** over
`getattr(ws, "X")` per delegate beside the forward ratchet; the dev-channel /
on-glass vocabulary moves in the SAME commit as any forward it names; the
per-stage gate runs the **three on-glass suites**, not only the benches.

**3e. Temperature: the audited hot list.** The per-frame surface each stage
must keep flat or hoist (grounded at file:line by the perf review):

| Cluster | Frame-hot members | Rule |
|---|---|---|
| SystemStore | `ach.toast_active`/`ach_ui` confetti+egg/`notice_active` — read ×1 by `_animating` + ×3 by `wm._overlay_sig` per loop; flat mirrors `frameskip`/`show_fps`/`show_achievements` (`frame_cap_fps` runs in `pace()` EVERY iteration); `system["favorites"]` per home paint | EVENT-PUSH (rev 3): an unlock writes its overlay deadlines into flat kernel fields (the `notice()` pattern) — the per-frame gates read plain attributes and never call into `ach`/`ach_ui`; the objects draw only while an overlay is up; store owns persistence. Toggle mirrors stay flat `ws` attributes, store owns persisted copies |
| CoverCache | `frame()` writes `_cover_built`/`_cover_ms` at its top and drains `_covers_deferred` at its tail; `cover_for` runs per card per painted shelf frame **through injected bound methods**; `_cover_gen` in 7 launcher keys; both ticks per idle frame | grid wiring re-points at the collaborator's bound methods (`launcher.cover_for = covers.cover_for`); launcher reads `covers.gen` — no `ws` mirror (one-author rule); budget fields written through the object once per frame |
| CartManager | `_all_carts` (idle prefetch scan + desk statics keys), `is_favorite` | plain attribute on the object; consumers migrate in-stage |
| WebConsole | cold — provided `ws.webhost` stays a flat service attribute (`poll_webhost` runs at every frame tail on all three boards) | none |
| HistoryRouter | `_journal_idle_tick` every frame (cheap early-out), `_edit_ms` per keypress | tick stays kernel-called through a hoisted binding |
| Appearance | `light_chrome`/theme tokens are read per draw via chrome | token reads stay flat; the cluster owns the *verbs* |

**3f. RAM and image: immaterial, with numbers.** Five-six instance dicts ≈
1–2KB on a PSRAM-resident heap; fixed-signature forwards ~40–60B each; frozen
bytecode ~conserved by moving between modules. Against the T-Deck's recorded
186KB slack (2026-08-15 figure — re-check at stage 1, the C6/sync work since
may have moved it) and the #168 guard, this is noise. Record the delta per
stage and stop worrying.

## 4. Target object map

    Workstation — the kernel:
      frame/input/pointer loop · layer stack · spawn/exit + go_home ·
      viewport/composite · draw toolkit · the run/exit Project forwards
      │  plus, KERNEL BY DECISION (2026-08-26, revisit only with a consumer):
      │  app registry/context filter · perf meters · notices/toast/splash ·
      │  search · share-tile · the flat overlay-deadline fields the
      │  achievement objects PUSH at event time (rev 3 — never polled)
      │
      ├─ web:     WebConsole    park/stop/unpark · pin mint/read · url · QR
      ├─ prefs:   SystemStore   the system.json dict (in-place load) + 4 persist
      │                         funnels + achievements load/save · StoreHandle
      ├─ covers:  CoverCache    13 methods + _CoverImage/_CoverJob + _icon_cache;
      │                         surface: begin_frame/take_deferred/invalidate_all/
      │                         diet_release/gen/cover_for; #186 invariant = one body
      ├─ carts:   CartManager   new/dup/del · _apply_items · rescan_carts (webhost's
      │                         on_sync) · slim/rehydrate · favorites/recents verbs
      ├─ look:    Appearance    theme/skin/variant · font scale · wallpaper (incl.
      │                         select_wallpaper as coordinator) · icon sheet
      └─ history: HistoryRouter the #111 bar undo/redo + journal + save funnels
                                (LAST; own design pass)

    app_context roles narrow these objects:
      ctx.prefs → SystemStore · ctx.notify → the kernel's ach objects (store
      persists) · ctx.carts → CartManager + StoreHandle · storage roles take
      StoreHandle · ctx.surface/damage/nav/theme → kernel as today

Construction rules: §3b–§3e. The façade shrinks by the two ratchets (§3d), and
the end state's surviving forwards are the deliberate public surface, each with
a reason.

## 5. The forward test: what the roadmap asks of each seam

- **#131 multi-kid profiles** → SystemStore rooted (at wire time) by path —
  one root instead of four funnels and scattered reads.
- **#123/#124/#125 gallery browse/install/publish** → a second cart SOURCE
  registers on CartManager (the sync-roots-registry shape, `a960563`).
- **#195 browser gallery leg** → WebConsole.
- **#136 version time machine** → reads through HistoryRouter.
- **#207 skin tail + §7's toggle registry** → land in Appearance.
- **#158 third cart runtime (wasm)** → already data-shaped
  (`player.py:863` routes on the manifest's `runtime` field); do not regress.
- **#203 PPI floor** → §6, scoped honestly: its first-pass surfaces (bar, ≡
  menu, Settings rows) live in chrome geometry — #203's own seam map names
  `chrome.Layout` their single home — so the base's contribution is the seam
  (`fs` vs `chrome_scale` as distinct inputs, defined once) and the named
  second pass (the editor symbol palette) riding the shared head.
- **#135 localization** → out of scope; same R2 move when its day comes.

## 6. LayoutBase: the geometry contract gets one body

**Eight** classes hand-copy the `_BASE_W/_BASE_H` + `_base`-predicate header
(the `self._base = (…)` grep): `chrome.Layout`, `chrome.CodeLayout`, and the
six editor layouts in their own files (cards/paint/map/music/scene/block; the
survey's "seven, each in its own file" double-counted — CodeLayout lives in
chrome.py). Two (scene, block) extend the predicate with `bounds is None`;
the base owns only the header + predicate and must accommodate that.

**Home: a new leaf module** (`runtime/layout_base.py`-shaped), NOT chrome.py —
chrome imports its geometry constants FROM `bar_layer`/`settings_layer`/
`code_layer` (chrome.py:45–66; code_layer already defers a chrome import into a
function body to dodge the cycle), so a base beside `chrome.Layout` is
unimportable by exactly #203's consumers. `chrome.Layout` subclasses the leaf
like everyone else.

**The app tier is a declared second vocabulary, for now.** `app_shell.
ListShellLayout` (base of Files/Storybook/Writer/Sheets) and the calc/
appearance/artwork heads speak `(w, h, fs, windowed=)` with no `_base` — a
different contract (no frozen-baseline branch). Decision: they stay separate,
written down here, revisited when #203's second pass touches app surfaces.

Verification: the goldens net the move — on the NON-`_base` configs (the
baseline rows don't exercise responsive code; a green T-Deck row proves
nothing). Perf: all eight are constructed only at init/relayout, never per
frame (verified at every construction site) — the base costs nothing hot.
Today's stored matrix is 97 whole-screen hashes ×5 configs + 298 sub-surface
(the 87/300 in older docs is the 2026-08-19 snapshot).

## 7. Candidate, smaller: the settings-toggle registry

A new toggle walks five hand-kept sites (verified end-to-end for
crisp_pixels): the `ws.set_*` method, the system.json key, the
`_SETTINGS_ROWS` splice with its capability gate, the tap-dispatch branch, the
dev-channel handler. A registry (name → default, persist key, apply hook,
capability gate) is R2-shaped and lands in **Appearance/Settings territory
after stage 5**. Two constraints from review: the capability gates are
per-board/per-service and must be expressed, not flattened; and the registry
must NOT move the flat mirror reads into dict lookups — `frame_cap_fps` reads
`ws.frameskip` inside `pace()` every loop iteration on all three boards.
Mirrors stay flat attributes; the registry owns declaration, persistence and
the Settings row.

## 8. Sequencing — five gated landings, one object per commit

The unit that must stay small is the COMMIT (blame isolation on the cheap host
nets: goldens, unit tests, ratchets). The unit that is expensive is the GATE
(three boards flashed, three on-glass suites, the bench set) — and rev 2
over-provisioned it at nine. Gates are sized by risk: the on-glass gate exists
for what the host cannot see (GC cadence, timing tails), so changes that
cannot plausibly move those share a gate. Everything on its own refactor
branch off `dev`, each LANDING merged back to dev so the branch stays short
against a moving dev.

- **Landing A — warm-ups + pilot (4 commits, 1 gate):** W1 `vendor_common.py`
  (host tools, pytest-only risk) · W2 LayoutBase (init-time only, goldens on
  the non-`_base` configs) · W3 the launcher invalidation helper (the
  dependency reducer for CoverCache) · **WebConsole** (two state attributes;
  the pilot exists to exercise the new machinery — state table, both
  ratchets, the full gate — once on something trivial; park-order invariant
  pinned; rescan left to CartManager). None of the four can plausibly move GC
  or timing except the pilot, which is the point of piloting.
- **Landing B — SystemStore (alone):** the aliasing-risk stage (§3a's
  in-place dict + funnels + achievements persistence + StoreHandle + the rev-3
  event-push flip). Gated alone because a late stutter here must not bisect
  against anything else.
- **Landing C — CoverCache then CartManager (2 commits, 1 gate):** they share
  the `_apply_items` seam — gating together avoids measuring the same code
  twice and skips the intermediate state where the list rebuild clears
  attributes it no longer owns. This is the frame-hot landing; its gate is
  the one where the S3 arm and idle-paints-zero earn their keep.
  `_icon_cache` joins covers here and its missing invalidation is fixed.
- **Landing D — Appearance:** needs B and C (wallpaper persist + rehydrate);
  absorbs `select_wallpaper` as coordinator.
- **Landing E — HistoryRouter:** own design pass first; not started until
  A–D are landed and quiet.

**Why not fewer than five:** a single end-gate (the UI refactor's shape)
worked there because its changes were draw-surface-local and the goldens saw
nearly everything on host; this program moves frame-loop state ownership,
whose failure class is visible only on glass and only in tails — one end-gate
means a late GC stutter bisects across six objects on hardware, which costs
more than the four intermediate gates it saves.

**Exit criteria, every LANDING:** state table written before code moves ·
goldens green on the configs that exercise the change · direct
mutation-checked tests on each new body · both ratchets (forward count +
getattr name-strings) updated downward · forwards fixed-signature, frame loop
never through one · `p4_bench`/`p4_clicks` p50 AND p90/max inside noise ·
`tools/p4_perf.py` roster unchanged · one S3 arm (T-Deck PERF capture over a
cart run + launcher fling: worst frame + collect count) · idle-paints-zero
(`_frames_drawn` static over quiet seconds) · all three on-glass suites green ·
image delta recorded. CLAUDE.md gets its entry when a landing LANDS, dated —
never an "in progress" status line.

## 9. What not to do — pre-answered

- **No big-bang**, no two objects in one stage.
- **No ABCs / interface classes / registries without enumerators.** Duck
  typing plus a ratchet test is the contract mechanism here.
- **No splitting cohesive big files** — `DeviceCanvas`, `WindowedWM`,
  `BlockEditorUI`, `SettingsLayer` keep their "leave it" verdicts.
- **No new layer between roles and collaborators**, and no `ws` mirrors of
  collaborator state (one author per derived value).
- **No re-litigating the dismissed duplications** (import shims, the canvas
  compositor contract, argparse heads).
