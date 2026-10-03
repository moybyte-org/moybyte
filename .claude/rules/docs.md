---
paths:
  - "**/*.md"
  - ".claude/**"
---

<!-- What a document in this tree may say, and how it stays true. -->

- **Decisions, not status.** A decision keeps its value forever — "the indexed
  canvas was A/B'd on P4 glass and RGB565 won (2026-08-05)", "the double game
  canvas was built, measured and REVERTED (`26e1f9f`)" — and it is historical by
  nature: keep its date and commit hash, because it is what stops the next
  session redoing dead work. A status claim ("still needs on-hardware
  verification", "pending next flash") is true for a week and then lies, and a
  doc that lies is worse than a silent one because the reader acts on it. Status
  goes in a GitHub issue, which has state. On 2026-08-15 CLAUDE.md said OTA
  "Still NEEDS ON-HARDWARE VERIFICATION" directly above the paragraph recording
  the chain passing on glass on both boards, and an agent told the owner their
  T-Deck might need a migration flash it had taken two weeks earlier.

- **A document is a MAP to numbers, never a copy of them.** A measurement
  pasted into prose goes stale: one did inside the sentence forbidding it, and
  on 2026-08-28 a sweep found the T-Deck's image headroom recorded as `186KB`
  when the build reported eight times that. State the decision and point at the
  number's home (CLAUDE.md's table). Numbers that are CONFIGURATION — a chunk
  size, a timeout, a headroom floor, a gesture's hold time — may stay, because
  they are the design, not a measurement of it.

- **Every doc in the tree is held to this, and the rules here hardest**,
  because they load exactly when somebody is about to act on them. On
  2026-09-11 `.claude/rules/testing.md` still said "Merely OPENING the P4's
  CH343 reboots it", nine days after `1725d40` fixed that and updated two other
  copies; the one left was the one that loads when you write a test, an agent
  repeated it to the owner as fact, and a session was planned around a boot that
  no longer happened.

- **A change that alters behaviour goes and FINDS the claims it falsified.**
  Nothing else will: a doc has no test, and `tools/check_docs.py` resolves paths
  and pins duplication without reading for truth. Grep the vocabulary the change
  moved — the verb, the file name, the words of the rule it replaced — read every
  hit, and correct the claim itself. Do not append a correction: a paragraph
  that states the old thing and walks it back two clauses later leaves both
  readings on the page (both shapes turned up in one afternoon on 2026-09-11,
  one in a cell the same commit had just edited).

- **Present tense for how the system works; historical voice only for a
  decision.** "The Code tab is one of the cart's scripts", not "since #89 the
  Code tab stopped…" — changelog voice rots a second time when the next change
  lands on top of it. Keep the issue number as a reference and drop the
  transition.

- **Dated, not numbered.** Design docs, plans and shell generations are named by
  the month they describe (`moybyte_console_plan_2026-07.md`, "the 2026-07
  shell"), because a `v0.5` beside a firmware `0.6` reads as an older release.
  The two format generations keep their names: the `.moy` format and the
  indexed-canvas contract (`v04` in `tests/test_v04_userland.py`,
  `docs/audio_design_v04.md` and code comments).

- **One copy, and a pointer.** When a doc explains what a linked doc already
  explains, the two drift and the reader cannot tell which is current. Prefer a
  sentence of orientation plus the pointer. `tools/check_docs.py` pins shared
  ten-word runs per pair of documents so the count only comes down, and fails a
  backticked path that no longer resolves (a leading `+` marks a file a doc
  proposes to create). Run it after every doc edit; `make test` runs it too.

- **Where it goes.** CLAUDE.md is the router every session loads: a line there
  costs every session, so it holds only what every session needs. A rule under
  `.claude/rules/` is the thing that bites when you touch its paths. A skill
  under `.claude/skills/` is how to do a procedure. How a subsystem works lives
  in that module's header or its board dir's README.
