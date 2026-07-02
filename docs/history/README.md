# Archived docs (history)

Superseded design docs and completed-work dev notes, kept for reference only.
The current, authoritative plan is `moybyte_Console_Plan_v0_5.md` at the repo
root (and `CLAUDE.md` for working orientation).

## Product / design history

| doc | status |
|---|---|
| `moybyte_Console_Plan_v0_4.md` | superseded by v0.5 — the v0.4 "fantasy workstation / everything is a cartridge" direction |
| `moybyte_Console_Design_Doc_v0_3.md` | superseded by v0.4 |
| `moybyte_Console_MicroPython_First_Design_Doc_v0_1.md` | early MicroPython-first exploration |
| `moybyte_Software_Architecture_Codex_Handoff_v0_1.md` | v0.1 implementation handoff (done) |
| `moybyte_Codex_First_Sprint_Prompt_v0_1.md` | v0.1 first-sprint prompt (done) |

## Native-core (compositor) dev history — done, shipped in the firmware

The MicroPython spike + native `moy_gfx`/`moy_compositor` work (Stages 1–3), which is
**built and in use**. Live firmware build/flash docs stay in
`firmware/lilygo_t_deck_plus_micropython/README.md`.

| doc | what it is |
|---|---|
| `SPIKE_RESULTS.md` / `SPIKE_FINAL.md` | MicroPython-on-T-Deck spike notes + Stage-2 gate result |
| `NATIVE_CORE_PLAN.md` | the native-core plan (Stages 1–3, native takeover) |
| `STAGE3_PLAN.md` | the production dirty-rect compositor plan |

## `.moyproj` SDK reference (legacy format + API)

The original `.moyproj` SDK docs, superseded by the v0.4 `.moy` console — the current
cart API is [`../moy_cart_api.md`](../moy_cart_api.md). The SDK itself is still
maintained (it seeds the icons→blocks→code ladder), so these are reference, not dead.

| doc | what it is |
|---|---|
| `moybyte_api.md` | the `.moyproj` `from moybyte import *` API (`run`/`sprite`/`text`/`button`/…) |
| `project_format.md` | the `.moyproj` project folder format |
| `firmware_runtime_contract.md` | the `.moyproj` device runtime contract (128×128 canvas) |
