# Moybyte OS voice v1 — how the machine talks

**Status:** CHOSEN VOICE DIRECTION. Grows `docs/visual_identity_v1.md` §9
("Voice and naming") from a seed into a working contract.

**Implementation status (2026-07-23):** the §9 rewrite table is SHIPPED in the
runtime (host-tested, full suite green): crash panel + crash-to-code popup
reworded, the `* FAILED` status family retired for the `CAN'T <verb>` idiom
(save/get/put/send/connect), WiFi failure is password-aware Spoken register, the
OTA error screen states `Nothing changed.`, the block editor's lowercase status
register is fully unified into ENGRAVED, and the About panel uses the lowercase
wordmark. As-built deviations from the table are noted inline below. Still open:
Moy's puzzled crash expression (§11.2), the first-boot hello (§8), and on-panel
legibility checks for the Spoken strings (§11.3).

**Scope:** every string the OS shows a kid — labels, toasts, errors, empty states,
update screens, celebrations — plus the naming system and where personality is
allowed to live. Kid cartridges keep their own voices. The developer docs
(`docs/moy_cart_api.md`) already have a good voice; this document does not restyle
them.

---

## 1. The pool — where the beloved platforms put their personality

Every loved consumer machine solves the same problem — *a computer that feels good
to be near* — but each hides the personality in a different place:

| Platform | Where the voice lives | The machine itself sounds like |
|---|---|---|
| **PICO-8 / Picotron** | The *fiction* ("fantasy workstation", "cosy filesystem") and the warm second-person manual | Terse, factual, all-caps terminal. `SAVED FOO.P8`. `SYNTAX ERROR LINE 9 (TAB 0)` — and it drops you at the fix |
| **Playdate** | *Moments* and animation-as-copy: the eye that wakes, "Crank to buy", confetti | Full friendly sentences, sparingly. `Sorry, Your Playdate has crashed. Press A to restart` — detail hidden behind B |
| **Nintendo (Wii/Switch)** | *Characters* (Miis, Resetti) and politeness-as-brand | An agentless polite appliance. `The software was closed because an error occurred.` `Why not take a break?` |
| **Apple (modern)** | Nowhere — restraint *is* the voice | A discreet concierge: name the problem in two words, make the fix the primary button, no "oops", no "sorry" |
| **Apple (classic Mac)** | The *face*: happy Mac, sad Mac, handwritten "hello" | Emotion via icon, one-line apology, zero detail |
| **Scratch** | In the *absence of errors*: failsoft runtime, no error dialogs exist at all | — |
| **Toca Boca / LEGO** | In the absence of *text*: everything discoverable by poking; wordless sequenced instructions | — |

The crash strings, side by side, are the clearest fingerprint of each voice:

```text
PICO-8      SYNTAX ERROR LINE 9 (TAB 0)            fact + location, repair site
Playdate    Sorry, Your Playdate has crashed.       warmth + one action, geek
            Press A to restart                      detail behind B
Switch      The software was closed because         agentless, blame-free,
            an error occurred.                      nothing to fix
Classic Mac Sorry, a system error occurred. + bomb  emotion, no information
Modern Mac  Billing Problem → [Add Payment Method]  no emotion, all fix
Scratch     (no message exists)                     failure designed away
```

Rules that hold across *every* loved example:

1. **Never blame the user.** Errors happen *to* the code, not because of the kid.
2. **One primary recovery action**, named as a verb.
3. **"Sorry" only when the *system* failed** — Playdate and classic Mac apologize
   for their own crashes; PICO-8 never apologizes for *your* syntax error, it just
   tells you where it is.
4. **Exclamation points are budgeted.** Earned by milestones, never spent on errors.
5. Platforms that reach the youngest kids either speak in full gentle sentences
   (Nintendo) or in no words at all (Toca, LEGO). Snark appears nowhere.

## 2. Our position — the vibe

Moybyte's product thesis is *See it. Play it. Open it. Change it.* The unique fact
about our machine — the thing none of the comparators can say — is that **the crash
screen is a door, not a wall**. On a Playdate a crash ends play; on Moybyte
crash-to-code drops the kid *inside* the game with the caret on the line that broke.
Our voice has to make that feel like the machine opening up, not like punishment.

So the vibe, in one line:

> **A real workshop that likes having you in it.** Short words engraved on the
> tools, a friendly face at the bench, and nothing in your project is ever
> "failed" — only *not yet*.

Mapped onto the pool: the **machine** talks like PICO-8 (terse, factual, locates
the fix), the **moments** are Playdate's (personality spent on wake, celebration,
and small animations, not sprinkled on every string), the **character** budget is
Nintendo's but carried by **Moy, wordlessly** (classic-Mac face doctrine: Moy
*expresses*, the text stays plain), and the **errors** follow modern Apple's
mechanics (say the fix, not the fault) with a kid-shaped warmth Apple deliberately
strips out. Like Scratch, the best error copy is the error we design away.

It assumes the child is capable — the §9 seed. Concretely: the console never
performs enthusiasm at the kid ("Great job!!!", streaks, daily rewards), never
baby-talks, and never scolds. It treats an eight-year-old like a maker who owns
real tools, because on this machine they do.

### 2.1 Kid-first, enthusiast-proof

Moybyte is a kid-first product that also needs the fantasy-console/handheld
enthusiast crowd to love it — the people who buy PICO-8, Playdate, and Analogue
hardware, review it, stream it, and give it to their kids. The pool shows this is
not a tension to balance but one property to protect: **PICO-8's community is
mostly adults, Playdate is an adult product wearing a toy's face, and Nintendo
spans ages 4 to 60 — all with a single voice.** What enthusiasts love about a
friendly machine ("terse honest machine-talk", "real tool, no hand-holding",
"the fiction is committed") is the *same* property that respects a kid. What
repels enthusiasts (baby-talk, performed enthusiasm, a mascot that narrates) is
exactly what this document already bans for the kid's sake.

The voice therefore never forks by audience. It holds because each register
serves both readers at once:

- **ENGRAVED** reads to a kid as "big clear buttons" and to an enthusiast as
  "disciplined terminal minimalism" — the same pixels.
- **Spoken** is plain enough that an adult parses it as clean design, not as a
  children's menu. `Your game stopped.` embarrasses nobody; `Oopsie! Your game
  went sleepy-bye` would lose both audiences (the kid within a year, the adult
  instantly).
- **Moy is wordless**, so the mascot can never talk down to anyone — the exact
  reason the Playdate eye and the classic Mac face age well while chatty
  assistants (Clippy) became the canonical cautionary tale.
- **The honest-tool tier** — raw tracebacks inside the code editor, real line
  numbers, the "small print" lowercase metadata — is the enthusiast's proof that
  the machine is real, and the ceiling a curious kid grows into. Never simplify
  it away; it costs the kid nothing (the shell's framing sits above it) and buys
  the enthusiast everything.
- **The fiction** (cartridges, the Library, the construction field, MOY64) is
  the shared layer: kids inhabit it, enthusiasts collect it. Whimsy-in-names is
  enthusiast catnip *and* kid-readable.

The working test — **the stream test** (law 11): every string must survive being
read aloud twice — by an eight-year-old alone (comprehension), and by a
thirty-five-year-old streaming the console to their audience (dignity). A string
that fails the first is jargon; a string that fails the second is baby-talk;
the voice is whatever passes both.

## 3. The four registers

The audit of the shipped shell found the voice is *accidental*: ALL-CAPS dominates
because of the bitmap font, warmth appears in three lucky pockets (`OOPS`, the
achievement names, `YOU LEVELED UP TO CODE!`), and the same concept flips casing
between files. The fix is not "one tone everywhere" — it is four deliberate
registers, each with a job:

### 3.1 ENGRAVED — the machine's controls

ALL-CAPS, **three words or fewer**, no terminal punctuation. This is text stamped
on the machine: verbs, labels, settings rows, tab names, status pills.

```text
PLAY   CHANGE   MAKE   SAVED   HOLD TO EXIT   TAP AGAIN TO DELETE   WIFI
```

ENGRAVED text never forms a sentence. The moment a string needs a verb *and* an
object *and* a reason, it has outgrown this register — move it to Spoken.

### 3.2 Spoken — the machine explains

Sentence case, real punctuation, one short sentence (two at most: the fact, then
the next move). Used for anything that explains: errors, empty-state hints,
captions, confirmations that carry a consequence.

```text
That didn't run yet.
Start a fresh project.
Save drawings here and they show up in your games.
```

Spoken text says **you/your** for the kid's things and never says "I" or "we" —
the machine is a place, not a person. Contractions are normal ("didn't",
"can't"). "Please" is not used (the two-tap confirm asks by requiring a second
tap, not by pleading); "sorry" is reserved — see §5.

### 3.3 Named — titles of things

Title Case for every named thing: cartridges (`Brick Siege`), achievements
(`Lift Off!`, `Secret Coder`), files, themes. The achievement names are already
the best copy in the OS — whimsical, concrete, two words. That is the model for
all naming: **whimsy goes in names, not in system prose.** (Exception: the
wordmark is lowercase `moybyte`, everywhere, including the About panel.)

### 3.4 Moy — the wordless register

Moy never has a speech bubble. Like the classic Mac's face and Playdate's waking
eye, Moy communicates by *expression and animation*: present and calm at boot,
delighted at a graduation, puzzled (not sad, never crying) beside a stopped cart,
asleep on an idle charge screen. Everything emotional the OS wants to say goes
through Moy's face, which keeps the text plain, the personality unmistakable, and
the whole register translation-free. If a string is doing emotional work
("OOPS!"), that work moves to Moy and the string returns to stating the fact.

## 4. The laws

Mechanical rules any string can be checked against:

1. **Register check first.** Is it a control (ENGRAVED), an explanation (Spoken),
   a name (Named), or a feeling (Moy)? Mixed-register strings
   (`CODE LOCKED -- can't blockify`) get split or rewritten, not joined with `--`.
2. **Say the fix, not the fault.** `COULD NOT CONNECT` → `That password didn't
   work.` + `TRY AGAIN`. Every failure surface has exactly one primary next
   action, and it's a verb.
3. **The banned words** in kid-project contexts: *error, failed, invalid, wrong,
   illegal, fatal, warning*. A kid's code is never "failed"; it *stopped*, or it
   *didn't run yet*. (Raw compiler/tracebacks may still appear *inside the code
   editor*, where they are honest tool output — PICO-8 doctrine — but the shell's
   own framing never uses these words.)
4. **The "yet" doctrine.** Empty states and first failures point forward:
   `NOTHING HERE YET` (already shipped, already right), `NO GAMES YET`,
   `That didn't run yet.` The word "yet" is our house style for potential.
5. **Exclamation budget: milestones only.** `UPDATED!`, `YOU LEVELED UP TO
   CODE!`, achievement banners. Never on errors (`OOPS! IT CRASHED` spends
   excitement on a bad moment), never on ordinary confirmations (`SAVED`, calm).
6. **`...` means in progress** (`SCANNING...`, `FLASHING...`) and nothing else.
7. **The refusal idiom is `CAN'T <verb> <here/that>`** — the shipped `CAN'T SAVE
   HERE` family is honest, short, and kid-readable; keep it as the standard
   ENGRAVED refusal. If the reason matters, a Spoken line explains beneath it.
8. **No engagement language, ever**: streaks, daily rewards, trending, likes,
   "come back tomorrow". (Already law in visual_identity §9; restated because
   voice is where it would leak in.)
9. **Two-tier screens are fine**: loud ENGRAVED headers over quiet lowercase
   metadata (`channel: stable`, `rebooting...`) — the OTA screen already does
   this well; the lowercase tier reads as the machine's small print and is the
   *only* sanctioned lowercase.
10. **Read it in a kid's voice out loud.** If it sounds like a school report
    ("assessment", "incorrect"), a casino ("reward unlocked"), or a lawyer
    ("are you sure you want to permanently"), rewrite it.
11. **The stream test** (§2.1): every string must survive being read aloud by an
    eight-year-old alone (comprehension) *and* by a thirty-five-year-old
    streaming the console (dignity). Fails the first → jargon. Fails the
    second → baby-talk. The voice is what passes both.

## 5. Failure doctrine — the crash is a door

Three kinds of failure, three treatments:

**The kid's cart stops (crash-to-code).** No apology — it isn't ours to make —
and no alarm. The panel states the fact in Spoken register, Moy looks puzzled,
and the one action is the door:

```text
current:  OOPS! THIS CART CRASHED          proposed:  Your game stopped.
          TAP CODE TO FIX IT                          [Moy, puzzled]
                                                      TAP CODE TO SEE WHY
```

"SEE WHY" beats "FIX IT": it frames the code tab as the place where the answer
lives (intrigue-first, per shell_ux) rather than presuming the kid broke
something. Inside the editor, the error popup shows the honest one-line reason
and the caret is already on the line — the machinery is shipped; the voice just
stops shouting over it.

**The machine fails (SD, WiFi, OTA, save).** This is where "sorry"-adjacent
warmth belongs — Playdate doctrine — but our machine keeps it short and states
the retry:

```text
SAVE FAILED            →  CAN'T SAVE — TAP TO TRY AGAIN
COULD NOT CONNECT      →  That password didn't work.  TRY AGAIN
UPDATE FAILED          →  Update didn't finish. Nothing changed.  TRY AGAIN
```

(`Nothing changed.` is load-bearing: the rollback design means a failed update
truly is harmless, and saying so is the machine being trustworthy.)

**Failure we design away (Scratch doctrine).** Before wordsmithing any frequent
error, ask whether the interaction should exist: the console already prefers
failsoft (degrade to built-in carts on SD failure, two-tap instead of confirm
dialogs, no save button at all). A recurring error message is a design bug filed
against the surface, not a copywriting task.

## 6. Celebration doctrine

Celebration is real but scarce, and it marks *capability*, not compliance:

- **Graduation** (`YOU LEVELED UP TO CODE!`) is the ceiling — full banner, Moy
  delighted, exclamation point. It celebrates becoming *more* of a maker.
- **Achievements** keep their Title-Case whimsy (`Lift Off!`) and the one-line
  `ACHIEVEMENT UNLOCKED!` banner. Never for showing up — only for doing.
- Ordinary success is calm: `SAVED`, not `SAVED!` — a workshop where every glue
  joint gets applause is exhausting. Confetti-tier moments per Playdate: rare,
  physical, earned.

## 7. Naming — one object, four contexts, on purpose

The audit found cart/project/game/app used interchangeably. The fix is not one
word — it's a deliberate mapping that mirrors the product journey:

| Word | When | Example surfaces |
|---|---|---|
| **cartridge / cart** | The object on the shelf — the thing you play and own. The universal noun; "everything is a cartridge" is the product | Library, `CART INFO`, `RESTART CART`, crash panel |
| **project** | The *same* cartridge while open in the Studio — yours, in progress | Picker (`PICK A PROJECT`), `YOUR PROJECTS`, MAKE captions |
| **game** | Only cartridges that are actually games (attach-to-game flows, `NO GAMES YET`) | Files app verbs, sheets attach |
| **app** | System tools (Files, Paint, Writer, Settings) | Docs, taskbar |

A cartridge *becomes* a project when you open it and goes back to being a
cartridge on the shelf — that's the See→Play→Open→Change story told through
nouns. What's forbidden is crossing the map (a picker that says "DELETE CART"
next to "PICK A PROJECT").

Auto-names stay boring on purpose (`New Cart`, `drawings_2`) — boring defaults
invite renaming; a cute auto-name colonizes the kid's naming moment.

## 8. First boot — the missing hello

The audit's sharpest absence: the console has no first-boot moment at all. The
whole comparator pool has one (Mac's handwritten "hello", Playdate's waking eye,
Wii's channel bloom). Ours should be **text-light and Moy-led**: Moy wakes on the
construction field, the lowercase wordmark draws itself, one Spoken line at most
— then the Library, because the fastest hello is a shelf full of things to play.
No name prompt, no tour, no settings gauntlet before play. (Scoped as a future
issue; noted here so the voice owns the moment when it's built.)

## 9. Rewrite table — the audit's strings under the laws

All rows below are SHIPPED (2026-07-23) unless noted:

| Where | Was | Voiced (as shipped) |
|---|---|---|
| Crash panel | `OOPS! THIS CART CRASHED` / `TAP CODE TO FIX IT` | `Your game stopped.` + `TAP CODE TO SEE WHY` (Moy puzzled art still open, §11.2) |
| Code-editor crash popup | `OOPS! IT CRASHED` / `TAP TO CLOSE` | `Stopped on this line.` / `TAP TO CLOSE` |
| Calc ÷0 | `OOPS` | kept — this one is perfect |
| Save failure | `SAVE FAILED` (+ variants) | `CAN'T SAVE` (retry is automatic — the debounce autosave fires again, so no false `TAP TO TRY AGAIN` promise) |
| Sprite share / send | `GET FAILED` / `PUT FAILED` / `FILE FAILED` | `CAN'T GET` / `CAN'T PUT` / `CAN'T SEND` |
| WiFi bad password | `COULD NOT CONNECT` | `That password didn't work.` when a password was typed; `Couldn't connect.` for open/saved networks (an open network's failure isn't a password's fault) |
| OTA failure | `UPDATE FAILED` | `Update didn't finish.` / `Nothing changed.` + `B = BACK` (back *is* the retry path — the UPDATE row re-arms the check; no separate retry action exists to promise) |
| Block editor mixed casing | `MOVED` vs `can't move there` | one register: the whole lowercase status set unified into ENGRAVED (`CAN'T MOVE THERE`, `COPY A BLOCK FIRST`, `TAP A + SPOT`, `BLOCK DELETED`, …); `CODE LOCKED -- can't blockify` → `CODE LOCKED` |
| Undo split | `NOTHING TO UNDO` vs `nothing to undo` | `NOTHING TO UNDO` everywhere |
| Delete confirm | `DELETE? TAP AGAIN` | kept — the two-tap *is* our "are you sure" |
| Empty grid | `NOTHING HERE YET` | kept — the "yet" doctrine's model string (block menus' `(nothing here)` joined it) |
| About panel | `MOYBYTE CONSOLE` | `moybyte` (lowercase wordmark law) |
| Launcher captions | `Open or create a project` | kept — already correct Spoken register |

## 10. Non-goals

- A chatty OS narrator or an assistant persona. The machine is a place.
- Moy dialogue, speech bubbles, or a tutorial character double-act (Mario Maker's
  Yamamura/Nina is great and is not us — our teaching surface is the carts and
  the docs).
- Humor that requires reading fast or getting a reference; irony of any kind.
- Localization-hostile wordplay in system strings (names may be playful;
  plumbing must translate).
- Rewriting `docs/moy_cart_api.md` — the manual's warm second-person maker voice
  (PICO-8-manual register) is already right and is the voice this document wants
  the OS to grow toward.

## 11. Open questions

1. Does `Your game stopped.` beat `That didn't run yet.` for the mid-play crash?
   ("Not yet" is strongest on the *first* run; a game that ran for two minutes
   did run.) Test both with kids alongside the visual-identity comprehension tasks.
2. Moy's puzzled expression on the crash panel needs art and a restraint check on
   the 320×240 tier — does the face fit without stealing the line number?
3. Whether the Spoken register survives Petme128 at 320×240 in practice — the
   sentence-case strings must be tested on the T-Deck panel before the register
   is applied broadly (same gate as visual_identity §11.5).
4. `TAP CODE TO SEE WHY` vs `TAP CODE TO FIX IT` — intrigue vs agency; another
   cheap comprehension test.
