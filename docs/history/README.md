# Archived docs (history)

Superseded design docs and completed-work dev notes, kept for reference only.
The current, authoritative plan is
[`KidCode_Console_Plan_v0_4.md`](../../KidCode_Console_Plan_v0_4.md) at the repo
root (and `CLAUDE.md` for working orientation).

## Product / design history

| doc | status |
|---|---|
| `KidCode_Console_Plan_v0_4.md` (repo root) | **current** — the v0.4 "fantasy workstation / everything is a cartridge" direction |
| `KidCode_Console_Design_Doc_v0_3.md` | superseded by v0.4 |
| `KidCode_Console_MicroPython_First_Design_Doc_v0_1.md` | early MicroPython-first exploration |
| `KidCode_Software_Architecture_Codex_Handoff_v0_1.md` | v0.1 implementation handoff (done) |
| `KidCode_Codex_First_Sprint_Prompt_v0_1.md` | v0.1 first-sprint prompt (done) |

## Native-core (compositor) dev history — done, shipped in the firmware

The MicroPython spike + native `kc_gfx`/`kc_compositor` work (Stages 1–3), which is
**built and in use**. Live firmware build/flash docs stay in
`firmware/lilygo_t_deck_plus_micropython/README.md`.

| doc | what it is |
|---|---|
| `SPIKE_RESULTS.md` / `SPIKE_FINAL.md` | MicroPython-on-T-Deck spike notes + Stage-2 gate result |
| `NATIVE_CORE_PLAN.md` | the native-core plan (Stages 1–3, native takeover) |
| `STAGE3_PLAN.md` | the production dirty-rect compositor plan |
