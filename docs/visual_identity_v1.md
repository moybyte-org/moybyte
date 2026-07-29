# Moybyte visual identity v1 — Open Machine

**Status:** DRAFT / CHOSEN DESIGN DIRECTION — this document turns the July 2026
concept exploration into a product and implementation contract. It is not yet the
shipped shell reference. Where it changes behavior described by
`docs/shell_ux_v1.md`, the change must land in code and tests before that reference is
updated.

**Implementation status (2026-07-12):** the §10 Phase 1 + Phase 2 vertical slice is
CODED and host-tested (`tests/test_visual_identity.py`): the §4.3 semantic theme
roles (frozen-literal fallbacks; `night` byte-identical), the opt-in **"machine"**
Open Machine theme (`chrome.THEMES`, selectable in Settings/Appearance), the
Library card's PLAY/CHANGE verbs on both tiers (on-card at desktop density, bar-zone
chips at 320×240, `C` key), `Workstation.change_selected` → Editor-on-Config, and the
§6.2 MAKE tile authoring accent. The launcher renders the library concept
mockup's **Library shelf** on EVERY tier — the 320×240 T-Deck included
(2026-07-13; this retired the baseline's frozen icon-tile grid): a framed
tool-surface panel (Moy + "LIBRARY" display-type header; footer with the
cartridge count) over the construction field, whose card grid **scrolls
continuously left-right** (2026-07-13 — paging is gone; a shelf slides
sideways): the ONE tall featured slot — the pinned yellow MAKE STUDIO card
(pencil, pin, caption) — heads the list and scrolls away with it, cover-art
cartridge cards with title bands stack the columns marching right (cards clip
to the grid viewport), and the scroll is driven by touch drag (tap-vs-drag
disambiguated on release via the shared `ui.DragTap`, so a scroll never
launches a cart), the footer's left/right column-nudge arrows, the slim
scrollbar along the grid's bottom edge, and keyboard nav (the view follows
the selection). The selected card
carries the focus ring and — where the card is tall enough — its in-card
PLAY/CHANGE row; small-card tiers (320×240) keep the verbs as bar-zone chips
and the home bar's lent zone shows the selected cart's name (wide-card tiers
show the moybyte wordmark). Phase 3's shared `ui` toolkit, warm-light Studio
surfaces, responsive app seam, and reference Calc app are also implemented.
Phases 4-5 and on-glass validation on the P4 panel remain open.

**Scope:** the Moybyte OS shell, Library, Studio/system apps, Player boundary, and
website expression. Kid cartridges keep their own visual identities and the frozen
`.moy` drawing contract.

**Direction:** **Open Machine** — a friendly computer whose structure is visible and
whose contents invite inspection and change.

**Product thesis:**

> See it. Play it. Open it. Change it.

The first encounter should resemble technology a child already understands: large
visual objects, immediate play, and a small number of obvious actions. Authoring then
reveals a real productive workspace: projects, tools, tabs, files, playtesting, undo,
and code. The transition is deliberate. Moybyte does not disguise a workstation as a
game forever; play is the doorway into making.

---

## Reference mockups

These three concept images establish the intended visual relationship between the
brand, Library, and Studio. They are design references, not screenshots of implemented
behavior. The written contracts in this document take precedence wherever a generated
image invents a control, process, color, or interaction.

### Open Machine identity direction

> **Concept render — not in this repo.** `open_machine_direction.png` lives in the private
> brand repo (`moybyte-org/biz`, under `brand/`). The renders were moved out
> before this repo went public; the direction they encode is described in the
> text below, which is what the code actually implements.

Use this board for the overall relationship: dark construction field, warm-light tool
surfaces, grape Moy, thin geometry, visible layers, and restrained functional accents.

### Library concept

> **Concept render — not in this repo.** `library_concept.png` lives in the private
> brand repo (`moybyte-org/biz`, under `brand/`). The renders were moved out
> before this repo went public; the direction they encode is described in the
> text below, which is what the code actually implements.

Use this image for scale, hierarchy, large cartridge art, the pinned MAKE entry, and
the shared Open Machine chrome. It predates several decisions in this document:

- `EDIT` becomes **CHANGE**;
- CHANGE edits the existing project in place;
- Library is Home, so the bottom Home/Library navigation is removed;
- Settings exists once in the upper-right OS system area;
- the generated `OPTIONS`, `FAVORITES`, and `NEW` toolbar actions are not approved;
  MAKE and the project picker own creation and management.

### Studio concept

> **Concept render — not in this repo.** `studio_concept.png` lives in the private
> brand repo (`moybyte-org/biz`, under `brand/`). The renders were moved out
> before this repo went public; the direction they encode is described in the
> text below, which is what the code actually implements.

Use this image for the precision-pixel desktop, warm-light authoring surfaces, dark
taskbar, compact tabs, and side-by-side editing/playtesting. It is not a process-model
diagram: v1 keeps one tabbed Make/Editor window plus a separate playtest window, not
independent Code, Map, and Sprites editor processes. Decorative Trash, folders,
resource meters, and fake menu commands are not requirements.

---

## 1. The three surfaces

Moybyte has three behavioral surfaces, not three unrelated products.

| Surface | Job | Visual character |
|---|---|---|
| **Library** | Pick something and play; enter making | Large visual cartridges, low density, obvious focus and actions |
| **Studio** | Author a project | Precise desktop/workbench, warm-light tools over a dark construction field |
| **Player** | Run one cartridge | The cartridge owns the screen; no Moybyte decoration over the game |

### 1.1 Library is Home

`Library` is the child-facing name of the root play shelf. There is no separate Home
destination that leads to another nearly identical root screen. This avoids redundant
navigation and keeps the model shallow:

```text
Library
  |-- cartridge / PLAY --> Player --> exit --> Library
  |-- cartridge / CHANGE --> Studio, project open on Config
  `-- MAKE --> Studio, project picker

Studio
  |-- PLAY --> playtest --> exit --> same Studio project and tab
  `-- LIBRARY --> Library
```

`Player` retains its current architectural meaning: the black box that runs a cart and
returns to its caller. Library is not renamed to Player.

### 1.2 Library behavior

- A cartridge card's **primary activation always plays**. A tap/click/confirm on the
  card has one predictable meaning.
- The selected cartridge exposes two verbs:
  - **PLAY** — dominant action; runs the cart.
  - **CHANGE** — secondary action; opens the project in Studio on Config.
- Use **CHANGE**, not Customize or Edit. It is short, kid-readable, broad enough for
  logic as well as cosmetics, and matches the product promise.
- The pinned **MAKE** tile opens the Studio project picker. New, Copy, and Delete stay
  with project management there; they do not become competing Library actions.
- **Settings has one global home:** the OS-owned upper-right system area, in the same
  position in Library and Studio. Do not duplicate it as a bottom navigation item or
  add a page-local `OPTIONS` button that leads to the same place.
- Wallpapers remain backdrop-only and do not appear in the playable cartridge grid.
- Do not add Favorites, Gallery, Learn, feeds, accounts, badges, or store-like
  categories until each has a real product behavior. The Library is a shelf, not a
  content service.
- Cartridge cover art may be expressive and use all of MOY64. Shell chrome around it
  remains quiet.

### 1.3 CHANGE is progressive disclosure

CHANGE is the bridge between familiar play and productive computing. It does not open
a separate lightweight customizer. It opens the same `Project` in the same
`EditorApp`, landing on **Config**:

```text
Config -> Blocks -> Code -> Sprites -> Map -> Music
```

The rest of the tab ladder stays visible. A child can change a friendly option, press
PLAY, and later notice that the same project has blocks, code, art, maps, and music.
There is one workspace and one persistence path, not a beginner editor that later
becomes throwaway state.

If a project has no useful Config schema, CHANGE opens the gentlest meaningful editable
tab. That fallback must be deterministic per project rather than inferred from age.

### 1.4 Studio behavior

- Studio is the authoring presentation of the existing Editor-as-an-app model, not a
  hidden maker/player mode.
- On the P4/host desktop tier, Studio uses the windowed workbench. The canonical
  picture is **one Make/Editor window plus one playtest window**.
- The Editor remains one tabbed app. Concept art that shows separate Code, Map, and
  Sprites windows is visual exploration, not the v1 process model.
- The project picker and Editor continue to share one Make window/group.
- A playtest launched from Studio returns to the same project and tab.
- Library remains the explicit route back to the play shelf.

### 1.5 Player behavior

- A running game owns its viewport and input.
- Fullscreen Player draws no shell bar or brand overlay.
- Exit returns to the caller: Library returns to Library; Studio playtest returns to
  Studio.
- Moybyte never recolors or visually normalizes a cartridge while it runs.

---

## 2. One identity, different density

Library and Studio should not be visually averaged into one compromised interface.
They share tokens and construction rules but use different density.

### Library

- large cartridge artwork;
- generous gaps and large targets;
- few simultaneous actions;
- selection readable at a glance;
- little exposed system structure.

### Studio

- smaller controls and tighter information rhythm;
- visible tabs, project structure, inspectors, tools, and status;
- windows and taskbar on the desktop tier;
- side-by-side authoring and playtesting where space permits.

The movement from Library to Studio should feel like opening the cover of the same
machine, not launching a second branded product.

---

## 3. Pixel-native, not retro

Moybyte keeps pixels because pixels are the real medium of the machine. It drops
retro-computer decoration used only for nostalgia.

Keep:

- crisp integer-aligned drawing;
- indexed colors;
- bitmap type;
- 16x16 system icons;
- visible sprite/tile grids where they express real editable data;
- flat fills, thin outlines, and deterministic geometry.

Avoid:

- bevels and fake plastic controls;
- CRT scanlines over ordinary system UI;
- neon glow used as focus;
- glass, blur, translucency, and glossy gradients;
- giant rounded cards and pill badges;
- noisy circuit-board wallpaper;
- deliberately awkward “old computer” interaction.

The OS chrome should feel precise and architectural. Pixel art belongs most strongly
to Moy, cartridge covers, game content, editable sprites, and child-created work.

---

## 4. The palette contract

### 4.1 Keep MOY64

**MOY64 remains the only runtime palette.** This is both an identity decision and a
portability constraint: the host canvas, device compositor, web transport, future
language VMs, and every `.moy` cartridge agree on the same 0-63 indices.

The redesign does not add arbitrary RGB colors to the OS. AI concept art may show
near-white or near-black colors that do not exist in MOY64; coded UI must resolve them
to real palette indices.

### 4.2 System subset

The shell should use a disciplined subset so cartridge art remains the most expressive
color on screen.

| Role | MOY64 index | RGB | Use |
|---|---:|---|---|
| black | 0 | `#000000` | strongest outline, game bezel, maximum-contrast ink |
| dark blue | 1 | `#1D2B53` | desktop field, dark title/task bars, primary dark border |
| grape shadow | 2 | `#7E2553` | Moy shadow/feet; rare berry detail |
| light grey | 6 | `#C2C3C7` | secondary lines and disabled ink |
| cream | 7 | `#FFF1E8` | primary light tool surface and light-on-dark text |
| orange | 9 | `#FFA300` | authoring highlight or attention, not generic decoration |
| signal yellow | 10 | `#FFEC27` | keyboard/pointer focus and the current primary selection |
| signal green | 11 | `#00E436` | PLAY, success, connected/healthy state only |
| grape | 13 | `#83769C` | Moy body, selected tabs, authoring identity |
| peach | 15 | `#FFCCAA` | warm secondary surface/accent |
| cool paper | 48 | `#DAE1F2` | optional cool raised surface or field separation |
| warm paper | 52 | `#E6DCD3` | inactive/secondary light surface |
| dim warm ink | 53 | `#8C857E` | secondary text on light surfaces |
| navy ramp | 60 | `#2E3E66` | raised dark panel, dot grid, inactive dark chrome |

Red (8) is reserved for danger, destructive confirmation, and errors. Green must not
become a general decorative brand color; preserving it for PLAY and healthy state
makes the system easier to scan.

Cartridges may use **all 64 colors**. The restricted subset applies only to Moybyte
system chrome and default system-app surfaces.

### 4.3 Semantic tokens

The existing panel theme tokens (`panel`, `edge`, `title`, `title_ink`, `accent`,
`hilite`, `dim`) are a useful seed but do not fully express a light tool surface over
a dark desktop. Before implementation, define or derive these semantic roles:

```text
desktop
desktop_pattern
surface
surface_alt
border
ink
ink_dim
surface_light  (boolean presentation class, not a palette index)
title_active
title_inactive
selection
focus
play
author
danger
```

The exact storage shape is an implementation decision. The behavioral rule is not:
system surfaces consume semantic roles rather than scattering literal indices.

The shipped `night` theme must remain byte-identical until golden/parity tests are
deliberately migrated. Open Machine should begin as an additional theme or an opt-in
prototype, not an in-place mutation of the default.

*Status (2026-07-23): SHIPPED, extended.* Every theme family now carries a **dark
and a light variant** (`chrome.THEME_VARIANTS`, picked in Appearance → THEMES →
DARK/LIGHT, persisted as `system.theme_variant`); the dark sets are the frozen
legacy tokens, the light sets are full token dicts (papers/pastels + dark ink, the
family tint kept for titles/selection). Section 4.3 grew four resolved roles beyond
the list above, all defaulting to today's frozen literals so dark themes stay
pixel-stable:

```text
chrome_ink / chrome_ink_dim  (ink on the OS chrome itself -- bar, strips, panels;
                              distinct from surface ink, which diverges in machine)
selection_ink                (ink on a hilite/selection fill)
bar / bar_edge / bar_light   (the OS bar band -- frozen black band on dark themes,
                              the panel tone + a boolean flip on light variants)
title_active / title_inactive (the WM strip pair, aliased to title/panel)
```

The Phase 5 system-app pass landed with it: the zoned bar + dock, Settings (rows +
Wi-Fi panel), the ≡ system menu, About, achievements, the OTA update screens, and
the editor tab surfaces (block/map/paint/scene/music/code/cards) read tokens on
every responsive tier — the token gate is no longer machine-only. Each surface's
frozen 320×240 `_base` branch keeps its literal indices **only in dark chrome**
(the device parity contract holds at the default theme): under a light variant
the base tier takes the token path too, so the small tier gets exactly a dark and
a light presentation of every editor (code included — its `_HL_LIGHT` syntax set
is the light half of the pair). Bar **icons get derived light variants** at draw
time (`Workstation._bar_image`): the IconSheet's untouched index-0 plate — invisible
on the frozen black bar — is keyed transparent and white strokes remap to ink-black
on a `bar_light` theme, so wifi/battery/close never draw a black plate on a light
band (the mascot keeps its authored colors).

### 4.4 Website color

The website may use a deeper CSS surround such as its existing `#0B1024`, because it
is not an indexed runtime surface. Actual OS screenshots, UI illustrations, Moy, and
palette demonstrations must use MOY64 faithfully. The website translates the
relationships of the palette; it does not pretend every CSS background is a new cart
color.

---

## 5. Core visual rules

### 5.1 Geometry

- Base spacing rhythm: **4px**, with **8px** as the primary layout unit.
- Default system border: **1px** at native system resolution.
- Corners: square by default; at most 2-4px on large Library cards where a softer
  hit target materially helps. Do not use capsule controls.
- Shadows: optional hard 1-2px offset in a palette color. No blur.
- Align important dividers, baselines, and icon boxes to the grid.

### 5.2 Focus and selection

- Keyboard, trackball, and pointer focus must be visible without hover.
- Focus uses signal yellow plus a shape/border change; color alone is insufficient.
- Selected Studio tabs use grape and maintain readable ink contrast.
- A selected Library card may expose PLAY and CHANGE, but unselected cards remain
  directly playable through primary activation.
- No focus glow, animated halo, or continuous pulse is required.

### 5.3 Icons

- System controls use the editable 16x16 `IconSheet` vocabulary.
- Prefer one- or two-color icons with a strong silhouette.
- Cartridge covers and project artwork are not system icons and may be richer.
- Do not introduce emoji or unrelated platform icon families.
- A control's icon and label must name the same verb.

### 5.4 Typography

- Petme128 remains the canonical runtime glyph source for v1 until a second bitmap UI
  face is explicitly approved and implemented identically on host and device.
- Do not base layouts on antialiased mockup type or fractional metrics.
- Prefer short verbs and concrete nouns: PLAY, CHANGE, MAKE, SAVE, PROJECTS,
  LIBRARY.
- Avoid small all-caps prose. Longer explanations use sentence case and the largest
  practical system font scale.
- A future UI-font evaluation is allowed, but it must be tested at 320x240 and
  1024x600 and must not fork host/device rendering.

---

## 6. Component contracts

### 6.1 Library cartridge card

Required anatomy:

1. cover art;
2. title;
3. visible focus/selection state;
4. PLAY and CHANGE actions when selected or contextually expanded.

Rules:

- no permanent `Code` badge on every card;
- no rating, currency, engagement count, or store metadata;
- no animated cover by default;
- prefer a manifest/seeded static cover over executing every cart for a live preview;
- title remains legible when cover art is visually busy.

### 6.2 MAKE tile

- Pinned before ordinary cartridges.
- Visually larger or otherwise unmistakably primary on the large-screen tier.
- Uses the authoring accent and a pencil/tool symbol.
- Opens the project picker; it does not silently create a blank project.
- Recommended label: **MAKE** or **MAKE STUDIO**. Final wording is an open test item
  (§11); the action must remain the same either way.

### 6.3 Studio window

- Warm cream primary surface over the dark desktop field.
- Thin dark border and compact title strip.
- Window title names project and app; it does not repeat every active tab.
- Existing Config -> Blocks -> Code -> Sprites -> Map -> Music ladder remains the
  main authoring navigation.
- PLAY and SAVE remain stable, visible verbs.
- App-window content may be dense; window-management chrome remains quiet.

### 6.4 Desktop field and taskbar

- Dark blue/navy field with an optional sparse dot/construction grid.
- The selectable `system_carts/open_machine.moy` cart is the coded reference for this
  backdrop. It remains static and MOY64-only; Moy Night remains available.
- Static by default so idle frames remain free.
- Taskbar chips represent real open processes only.
- Global Settings remains in the upper-right OS system area; it is not a taskbar chip
  or a second app-local Options command.
- No decorative Trash, filesystem folders, CPU/RAM meters, or fake menu commands.
- A system monitor or file browser may introduce those objects later only when they
  become real tools.

### 6.5 Moy mascot

Canonical colors:

| Part | Color |
|---|---|
| body | grape, index 13 |
| shadow/feet | dark purple, index 2 |
| highlight | light grey, index 6 |
| eyes | cream, index 7 |
| outline | black, index 0 |

The square bite in the upper-right silhouette is identity-critical. Do not replace Moy
with a generic rounded-square face.

Use Moy for:

- boot and identity moments;
- empty states and gentle onboarding;
- success, recoverable errors, and small celebrations;
- the wordmark/favicon/OS identity.

Do not place Moy on every button or let mascot art compete with a child's cartridge.
Future personal Moy recoloring may be a kid feature, but the canonical brand mascot
remains grape.

---

## 7. Responsive presentation

The functionality and names remain constant across tiers; presentation changes.

| Tier | Library | Studio | Player |
|---|---|---|---|
| T-Deck, 320x240 | the same scrolling shelf at small-card density; selected actions use the zoned bar | one responsive Editor tab fullscreen | fullscreen 320x240 |
| P4/host, 1024x600 | large visual shelf with the pinned MAKE card heading the sideways-scrolling list, cartridge covers, in-card PLAY/CHANGE | WindowedWM workbench; Editor beside playtest | fullscreen from Library; windowed playtest from Studio |
| Web | the shelf at the viewport's density | stack or windows according to viewport | viewport owned by game |

The large-screen mockups are not downscaled screenshots for the T-Deck. Each layout
must be authored responsively while preserving the same flow and vocabulary.

Minimum hit targets and font sizes must be verified on both physical devices. The
concept images are not evidence that tiny text will survive the 7-inch panel.

---

## 8. Motion and sound

Motion explains state; it is not ambient decoration.

Good uses:

- MAKE/CHANGE transition revealing the Studio workspace;
- a selected cartridge exposing its actions;
- window open/close/minimize feedback;
- save success and recoverable error acknowledgment.

Avoid:

- continuously glowing selection rings;
- animated backgrounds that force idle redraw;
- bouncing every card or icon;
- motion that delays PLAY or input response.

Sound should follow the same rule: small, optional confirmations with no reward-casino
cadence. A child's game owns its own audio while running.

---

## 9. Voice and naming

> The working contract that grows this section — the full rule set, the before/after
> rewrite table, and the shipped-string audit — lives in
> [`docs/os_voice_v1.md`](os_voice_v1.md) (#147). This section is the seed; that doc
> governs new text.

Voice is direct, warm, and concrete. It assumes the child is capable.

Prefer:

- PLAY
- CHANGE
- MAKE
- SAVE
- TRY AGAIN
- YOUR PROJECTS
- “That didn't run yet.”

Avoid:

- baby talk;
- school-assessment language;
- engagement language (“daily reward”, “streak”, “trending”);
- abstract software jargon where a concrete verb exists;
- surprise destructive actions.

Product naming:

- `moybyte` is the lowercase wordmark.
- `Moybyte` is the brand in prose.
- `Library`, `Studio`, and `Player` are conceptual names; Player is usually invisible
  to the child because the running game owns the screen.

---

## 10. Implementation sequence

Do not restyle every system app at once. Build one vertical slice:

### Phase 1 — tokens and static Library

1. Add Open Machine as an opt-in semantic theme without changing `night` golden
   pixels.
2. Restyle the existing launcher/`LauncherLayer` as Library at 1024x600.
3. Implement the real MAKE tile, cartridge card, selection, PLAY, and CHANGE states.
4. Use static cartridge covers and a static desktop field.

### Phase 2 — behavior slice

```text
Library -> PLAY Brick Siege -> Player -> exit -> Library
Library -> CHANGE Brick Siege -> Studio/Config
Library -> MAKE -> Studio/project picker
Studio -> PLAY -> playtest -> exit -> same Studio tab
```

This is the acceptance journey. It must work before broader surface restyling.

### Phase 3 — Studio chrome

1. Apply warm-light surfaces and thin Open Machine chrome to the existing single Make
   window.
2. Keep the real tabbed Editor architecture.
3. Verify one Editor plus one playtest window at 1024x600.
4. Preserve focus decoupling, return-to-caller behavior, and current retained-backdrop
   performance paths.

### Phase 4 — small-screen parity

1. Reflow Library to 320x240 without removing actions.
2. Route CHANGE to fullscreen Config.
3. Verify PLAY/exit/return and hold-to-exit behavior.
4. Keep Player performance unchanged: no shell compositing during fullscreen play.

### Phase 5 — system apps and website

1. Move Settings, Appearance, Writer, Storybook, and other system tools onto the same
   semantic surface contract.
2. Capture real runtime screenshots.
3. Update the website from those real screens rather than AI approximations.
4. Tell the same Pick -> Play -> Open -> Change story.

---

## 11. Confirmed decisions and questions to validate

These are deliberately not hidden inside visual implementation. Items marked
**Decision** are part of this direction; items marked **Recommendation** still need a
prototype or comprehension test.

### 11.1 Bottom navigation in Library?

**Decision: no persistent Library bottom navigation.** Library is Home, so separate
Home + Library entries are redundant. MAKE is already the pinned primary tile, and
Settings lives once in the upper-right OS system area, matching Studio. A desktop
taskbar appears only in Studio and represents real open processes; it is not a second
global navigation bar. Revisit this only if child testing shows that the upper-right
Settings entry or pinned MAKE tile cannot be found.

### 11.2 MAKE or MAKE STUDIO?

**Recommendation: test both.** `MAKE` is shorter and matches the current shell;
`MAKE STUDIO` better previews the transition into a workspace. The tile's action is
unchanged.

### 11.3 What happens when CHANGE targets a built-in?

**Decision: edit in place.** This preserves the current action and mental model:
CHANGE opens the existing cart's `Project`, and normal commit/undo persistence applies
there. It never creates a surprise duplicate. Copy remains an explicit project-picker
verb. The vertical slice must regression-test how built-in re-seeding interacts with
authored code, but changing the copy model is not part of this visual redesign.

### 11.4 How are cartridge covers produced?

**Recommendation: static authored cover art with a deterministic fallback.** Do not run
six games in the Library to synthesize live covers. Define the manifest/store contract
only after the visual slice proves the required dimensions.

*Status (2026-07-12): SHIPPED.* The contract is `images/cover.moyimg` in the cart
folder (any MOY64 `.moyimg`); the Library shelf cover-crops it full-bleed onto the
card, and carts without one keep the sprite/glyph fallback deterministically.
`tools/gen_covers.py` captures a clean gameplay frame for each seed game (committed
artifacts, hand-replaceable; manifest versions bumped for the #47 re-seed).

### 11.5 A second system UI font?

**Recommendation: defer.** First render Open Machine with Petme128 on real panels. If
legibility or density fails, evaluate one additional bitmap face as a shared host/device
asset. Do not substitute a web font in mockups and call the problem solved.

### 11.6 Library as a root surface or a fullscreen process above the desktop?

**Recommendation: choose from the vertical slice.** Preserve the process model and
return-to-caller contract. The implementation should make entering Studio explicit and
should not require two unrelated shell codebases.

---

## 12. Validation

### Visual and technical

- Capture golden screenshots at 320x240 and 1024x600.
- Confirm every system pixel is a valid MOY64 index.
- Inspect light surfaces and 1px borders on the physical P4 panel.
- Verify focus without hover and without relying only on color.
- Confirm static Library idle frames do not repaint continuously.
- Confirm fullscreen Player performance and pixels are unchanged.

### Child comprehension

Test behavior, not aesthetic preference, with a small mixed-confidence group around
ages 8-12. Give concrete tasks:

1. Play Brick Siege.
2. Change the tank.
3. Start something new.
4. Return to the Library.
5. Find the project again.

Observe whether the child understands that a cartridge is both playable and editable,
whether CHANGE predicts what Config does, and whether Studio feels discoverable rather
than like a school assignment. Test parent trust and purchase comprehension separately;
parents should not select the child's interaction model by proxy.

---

## 13. Non-goals for v1

- a cartridge marketplace or social feed;
- accounts, streaks, engagement rewards, or popularity metrics;
- multiple simultaneously running kid games;
- a separate beginner project format;
- abandoning MOY64 or the indexed cart canvas;
- making the T-Deck imitate a tiny desktop window manager;
- reproducing every decorative object shown by an AI concept image;
- a website redesign before the coded Library/Studio screens exist.

The v1 goal is narrower: one coherent identity and one comprehensible path from playing
something to changing it.
